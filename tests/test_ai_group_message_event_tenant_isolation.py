"""F3b-③b-2 AI 群组消息/事件面（discussion_messages / task_execution_events）
数据面隔离接线测试。

覆盖五层（全部 tmp 独立库 + NEST_DB/USERS_DB/ENTERPRISE_DB 环境隔离，绝不写生产
data/*.db）：
①两表 tenant_id 列迁移（老库补列+存量行回填 'default'）与索引
②租户键落行（写入继承=父任务行：respond 消息/_emit_exec_event/_insert_govern_event
  +伪任务兜底 'default' +外部共享写方（acp-proxy 列清单）落 'default' 且聊天完整）
③事件查询过滤（get_task_events/list_tasks last_events/_finished_duration_history
  恒带租户键，跨租户行互不可见/耗时不混）
④IsolationEngine.check_access 真执行（消息/事件面 bulk 审计留痕进 NestStore）
⑤API 层派生租户全链（token → 行归属落库 → 跨租户 403 + 匿名契约不变）
"""

import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb

# 生产 discussion_messages / task_execution_events 表 F3b-③b-2 迁移前形状
# （无 tenant_id → 证明自愈补列）
OLD_MSG_DDL = """CREATE TABLE discussion_messages (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    agent_name TEXT DEFAULT '',
    intent TEXT DEFAULT 'comment',
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    round_num INTEGER DEFAULT 1,
    created_at TEXT NOT NULL)"""

OLD_EVT_DDL = """CREATE TABLE task_execution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    agent_id TEXT DEFAULT '',
    event_type TEXT NOT NULL,
    content TEXT DEFAULT '',
    created_at TEXT NOT NULL)"""

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _ent_token(uid, username="u"):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.utcnow() + timedelta(minutes=10),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _insert_ent_user(db, username, tenant, is_active=1):
    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, is_active, tenant_id) VALUES (?, ?, ?, ?)",
            (username, "hashed", is_active, tenant),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


NOW_TS = datetime.now().isoformat()


def _add_group(gid="g-msg"):
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
            (gid, "msg-iso-test", "", "active", NOW_TS),
        )
        conn.commit()
    finally:
        conn.close()


def _add_task(tid, gid="g-msg", status="pending", tenant="default", created=None, updated=None):
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        ts = created or NOW_TS
        conn.execute(
            "INSERT INTO ai_group_tasks (id, group_id, goal, status, tenant_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (tid, gid, "测试任务", status, tenant, ts, updated or ts),
        )
        conn.commit()
    finally:
        conn.close()


def _raw_message(tid, gid="g-msg", tenant="default", content="raw", created=None):
    """外部共享写方形态（acp-proxy ws_group._persist_message 列清单，不带 tenant_id
    的写法落 'default'；带 tenant 的直插用于构造对照行）。"""
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, created_at, tenant_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "m-" + content[:12],
                gid,
                tid,
                "ext",
                "Ext",
                "comment",
                content,
                created or NOW_TS,
                tenant,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _raw_message_external(tid, gid="g-msg", content="ext-msg"):
    """完全模拟 acp-proxy：列清单不含 tenant_id → 落 'default'。"""
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, metadata, round_num, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "ext-" + content[:12],
                gid,
                tid,
                "ext-agent",
                "Ext",
                "agent_reply",
                content,
                "{}",
                1,
                NOW_TS,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _raw_event(tid, gid="g-msg", tenant="default", etype="output", content="evt", created=None):
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        conn.execute(
            "INSERT INTO task_execution_events (group_id, task_id, agent_id, event_type, content, created_at, tenant_id) VALUES (?,?,?,?,?,?,?)",
            (gid, tid, "probe", etype, content, created or NOW_TS, tenant),
        )
        conn.commit()
    finally:
        conn.close()


def _msg_rows(task_id):
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM discussion_messages WHERE task_id=? ORDER BY created_at, id",
                (task_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


def _evt_rows(task_id):
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM task_execution_events WHERE task_id=? ORDER BY id", (task_id,)
            ).fetchall()
        ]
    finally:
        conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 数据面全隔离：ai_groups.db + enterprise/users 身份库（NEST_DB 由
    conftest autouse 全测试隔离）。绝不写生产 data/*.db。"""
    import src.api.ai_groups as ai_groups

    monkeypatch.setattr(ai_groups, "DB_PATH", tmp_path / "ai_groups.db")
    ai_groups.init_db()
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    conn = sqlite3.connect(ent_db)
    conn.execute(ENTERPRISE_DDL)
    conn.commit()
    conn.close()
    return ent_db


@pytest.fixture()
def api(env, monkeypatch):
    """真实 router 进程内测试面（不拉起真 agent 执行）。"""
    import src.api.ai_groups as ai_groups

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(ai_groups, "_run_task_background", _noop)
    monkeypatch.setattr(ai_groups, "_run_task_chain", _noop)
    app = FastAPI()
    app.include_router(ai_groups.router)
    return TestClient(app)


# ── ① 迁移 + 落行 ─────────────────────────────────────────────────────


class TestMigration:
    def test_migration_adds_columns_backfill_and_indexes(self, tmp_path, monkeypatch):
        import src.api.ai_groups as ai_groups

        db = tmp_path / "old_msg_evt.db"
        conn = sqlite3.connect(db)
        conn.execute(OLD_MSG_DDL)
        conn.execute(OLD_EVT_DDL)
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, content, created_at) "
            "VALUES ('m_old', 'g1', 't1', 'a', 'x', ?)",
            (NOW_TS,),
        )
        conn.execute(
            "INSERT INTO task_execution_events (group_id, task_id, event_type, created_at) "
            "VALUES ('g1', 't1', 'output', ?)",
            (NOW_TS,),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(ai_groups, "DB_PATH", db)
        ai_groups.init_db()

        conn = sqlite3.connect(db)
        msg_cols = [r[1] for r in conn.execute("PRAGMA table_info(discussion_messages)").fetchall()]
        evt_cols = [
            r[1] for r in conn.execute("PRAGMA table_info(task_execution_events)").fetchall()
        ]
        msg_row = conn.execute(
            "SELECT tenant_id FROM discussion_messages WHERE id='m_old'"
        ).fetchone()
        evt_row = conn.execute(
            "SELECT tenant_id FROM task_execution_events WHERE task_id='t1'"
        ).fetchone()
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name IN ('idx_dm_tenant','idx_tee_tenant')"
            ).fetchall()
        }
        conn.close()
        assert "tenant_id" in msg_cols and "tenant_id" in evt_cols
        assert msg_row[0] == "default" and evt_row[0] == "default"  # 存量行回填 default
        assert idx == {"idx_dm_tenant", "idx_tee_tenant"}

    def test_row_roundtrip(self, env):
        _add_task("t_rt", tenant="tenant-a")
        _raw_message("t_rt", tenant="tenant-a", content="rt-msg")
        _raw_event("t_rt", tenant="tenant-a", content="rt-evt")
        assert _msg_rows("t_rt")[0]["tenant_id"] == "tenant-a"
        assert _evt_rows("t_rt")[0]["tenant_id"] == "tenant-a"


# ── ② 租户键落行（写入继承=父任务行）─────────────────────────────────


class TestWriteInheritance:
    def test_respond_message_inherits_task_tenant(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group()
        _add_task("t-disc", status="discussing", tenant="tenant-a")

        resp = api.post(
            "/api/ai-groups/g-msg/discuss/t-disc/respond",
            json={"agent_id": "a1", "agent_name": "A1", "intent": "comment", "content": "讨论发言"},
            headers=tok_a,
        )
        assert resp.status_code == 200
        rows = _msg_rows("t-disc")
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-a"  # 消息租户=父任务行继承

    def test_emit_exec_event_inherits_and_pseudo_falls_default(self, env):
        import src.api.ai_groups as ai_groups

        _add_task("t-evt", tenant="tenant-a")
        ai_groups._emit_exec_event("g-msg", "t-evt", "a1", "output", "hello")
        ai_groups._emit_exec_event("g-msg", "", "a1", "agent_reply", "pseudo")  # 空 task_id
        ai_groups._emit_exec_event("g-msg", "no-such-task", "a1", "output", "orphan")

        assert _evt_rows("t-evt")[0]["tenant_id"] == "tenant-a"
        conn = ai_groups.get_db()
        try:
            by_content = {
                r["content"]: r["tenant_id"]
                for r in conn.execute(
                    "SELECT content, tenant_id FROM task_execution_events"
                ).fetchall()
            }
        finally:
            conn.close()
        assert by_content["pseudo"] == "default"  # 伪任务兜底
        assert by_content["orphan"] == "default"  # 任务行缺失兜底

    def test_govern_event_inherits(self, env):
        import src.api.ai_groups as ai_groups

        _add_task("t-gov", tenant="tenant-a")
        conn = ai_groups.get_db()
        try:
            ai_groups._insert_govern_event(conn, "g-msg", "t-gov", "governed: x")
            conn.commit()
        finally:
            conn.close()
        rows = _evt_rows("t-gov")
        assert rows and rows[0]["tenant_id"] == "tenant-a"

    def test_external_writer_falls_default_and_stays_visible(self, api, env):
        """外部共享写方（acp-proxy _persist_message 列清单不含 tenant_id）落 'default'，
        但对父任务租户读者仍可见——消息读取权威=父任务闸门，行键过滤会吞掉
        agent 回帖=真回归（聊天完整性回归锁）。"""
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group()
        _add_task("t-chat", status="discussing", tenant="tenant-a")
        api.post(
            "/api/ai-groups/g-msg/discuss/t-chat/respond",
            json={"agent_id": "a1", "agent_name": "A1", "intent": "comment", "content": "本地写"},
            headers=tok_a,
        )
        _raw_message_external("t-chat", content="acp-reply")

        rows = _msg_rows("t-chat")
        by_content = {r["content"]: r["tenant_id"] for r in rows}
        assert by_content["acp-reply"] == "default"  # 外部写方兜底
        assert by_content["本地写"] == "tenant-a"  # 本地写点继承

        resp = api.get("/api/ai-groups/g-msg/discuss/t-chat/messages", headers=tok_a)
        assert resp.status_code == 200
        got = {m["content"] for m in resp.json()}
        assert "acp-reply" in got and "本地写" in got  # 两条都在=聊天完整


# ── ③ 事件查询过滤（键可靠面恒带租户键）────────────────────────────


class TestEventQueryFiltering:
    def test_get_task_events_own_rows_visible_foreign_hidden(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group()
        _add_task("t-vis", tenant="tenant-a")
        import src.api.ai_groups as ai_groups

        ai_groups._emit_exec_event("g-msg", "t-vis", "a1", "started", "s1")
        ai_groups._emit_exec_event("g-msg", "t-vis", "a1", "finished", "f1")
        # 键错行（他租户键落本任务）= 不可见（事件键可靠面恒过滤）
        _raw_event("t-vis", tenant="tenant-b", content="foreign", created=NOW_TS)

        resp = api.get("/api/ai-groups/g-msg/tasks/t-vis/events", headers=tok_a)
        assert resp.status_code == 200
        contents = [e["content"] for e in resp.json()["events"]]
        assert contents == ["s1", "f1"]  # 自租户行全可见（无丢失）+外来键隐藏

    def test_finished_duration_history_tenant_scoped(self, env):
        import src.api.ai_groups as ai_groups

        t0 = datetime.now() - timedelta(minutes=10)
        _add_task(
            "t-hist-a",
            tenant="tenant-a",
            created=t0.isoformat(),
            updated=(t0 + timedelta(seconds=5)).isoformat(),
        )
        _add_task(
            "t-hist-b",
            tenant="tenant-b",
            created=t0.isoformat(),
            updated=(t0 + timedelta(seconds=5)).isoformat(),
        )
        _raw_event(
            "t-hist-a",
            tenant="tenant-a",
            etype="finished",
            created=(t0 + timedelta(seconds=30)).isoformat(),
        )
        _raw_event(
            "t-hist-b",
            tenant="tenant-b",
            etype="finished",
            created=(t0 + timedelta(seconds=90)).isoformat(),
        )

        conn = ai_groups.get_db()
        try:
            hist_a = ai_groups._finished_duration_history(conn, "probe", tenant_id="tenant-a")
            hist_b = ai_groups._finished_duration_history(conn, "probe", tenant_id="tenant-b")
        finally:
            conn.close()
        assert 30.0 in hist_a and 90.0 not in hist_a  # 他租户耗时不进本租户 ETA
        assert 90.0 in hist_b and 30.0 not in hist_b

    def test_last_events_aggregate_tenant_scoped(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group()
        _add_task("t-last", tenant="tenant-a")
        t1 = (datetime.now() - timedelta(minutes=5)).isoformat()
        t2 = (datetime.now() - timedelta(minutes=1)).isoformat()
        _raw_event("t-last", tenant="tenant-a", content="own", created=t1)
        _raw_event(
            "t-last", tenant="tenant-b", content="foreign-later", created=t2
        )  # 同任务外来键更晚

        resp = api.get("/api/ai-groups/g-msg/tasks", headers=tok_a)
        assert resp.status_code == 200
        task = [t for t in resp.json() if t["id"] == "t-last"][0]
        assert task["last_event_at"] == t1  # 聚合按租户键过滤，外来键不混入


# ── ④ 引擎 bulk 审计留痕 ────────────────────────────────────────────


class TestEngineFaceAudit:
    def test_message_face_bulk_audit(self, api, env):
        import src.api.ai_groups as ai_groups

        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group()
        _add_task("t-audit-m", status="discussing", tenant="tenant-a")

        resp = api.get("/api/ai-groups/g-msg/discuss/t-audit-m/messages", headers=tok_a)
        assert resp.status_code == 200

        iso = ai_groups._iso()
        log = iso.get_access_log(limit=200)
        assert any(e["resource_type"] == "ai_group_message" for e in log)
        assert iso.stats()["total_access_checks"] > 0

    def test_event_face_bulk_audit(self, api, env):
        import src.api.ai_groups as ai_groups

        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group()
        _add_task("t-audit-e", tenant="tenant-a")

        resp = api.get("/api/ai-groups/g-msg/tasks/t-audit-e/events", headers=tok_a)
        assert resp.status_code == 200

        iso = ai_groups._iso()
        log = iso.get_access_log(limit=200)
        assert any(e["resource_type"] == "ai_group_event" for e in log)


# ── ⑤ API 全链（派生租户 + 跨租户 403 + 匿名契约）──────────────────


class TestApiChain:
    def test_cross_tenant_403_messages_and_events(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        _add_group()
        _add_task("t-sec", status="discussing", tenant="tenant-a")

        r_m = api.get("/api/ai-groups/g-msg/discuss/t-sec/messages", headers=tok_b)
        r_e = api.get("/api/ai-groups/g-msg/tasks/t-sec/events", headers=tok_b)
        assert r_m.status_code == 403 and r_e.status_code == 403
        assert "task access denied" in r_m.json()["detail"]
        assert "task access denied" in r_e.json()["detail"]
        # 正对照：本租户全通
        assert (
            api.get("/api/ai-groups/g-msg/discuss/t-sec/messages", headers=tok_a).status_code == 200
        )
        assert api.get("/api/ai-groups/g-msg/tasks/t-sec/events", headers=tok_a).status_code == 200

    def test_anonymous_default_contract(self, api, env):
        """匿名='default' 契约不变：默认租户任务的消息/事件照常读写。"""
        _add_group()
        _add_task("t-anon", status="discussing", tenant="default")
        r = api.post(
            "/api/ai-groups/g-msg/discuss/t-anon/respond",
            json={"agent_id": "a1", "agent_name": "A1", "intent": "comment", "content": "匿名发言"},
        )
        assert r.status_code == 200
        rows = _msg_rows("t-anon")
        assert rows[0]["tenant_id"] == "default"

        import src.api.ai_groups as ai_groups

        ai_groups._emit_exec_event("g-msg", "t-anon", "a1", "output", "anon-evt")
        r_m = api.get("/api/ai-groups/g-msg/discuss/t-anon/messages")
        r_e = api.get("/api/ai-groups/g-msg/tasks/t-anon/events")
        assert r_m.status_code == 200 and r_e.status_code == 200
        assert {m["content"] for m in r_m.json()} >= {"匿名发言"}
        assert [e["content"] for e in r_e.json()["events"]] == ["anon-evt"]
