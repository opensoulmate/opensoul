"""F3b-③b-1 AI 群组任务面（ai_group_tasks 表）数据面隔离接线测试。

覆盖五层（全部 tmp 独立库 + NEST_DB/USERS_DB/ENTERPRISE_DB 环境隔离，绝不写生产 data/*.db）：
①ai_group_tasks.tenant_id 列迁移（老库补列+存量行回填 'default'）与索引
②租户键落行（API 身份派生写入 + 内部创建点继承：子任务/复盘讨论任务/decide 派生子任务）
③查询过滤（list_tasks/govern 按租户，跨租户行互不可见/治理互不改写）
④IsolationEngine.check_access 真执行（by-ID 闸门：跨租户 TaskAccessDenied
  +引擎审计留痕 ns-tenant-b:task_id 硬断言——数据面第二条真消费轴）
⑤API 层派生租户全链（token → bind_identity → 行归属落库 → 闸门 403，真实 router）
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb

# 生产 ai_group_tasks 表 F3b-③b-1 迁移前形状（无 tenant_id → 证明自愈补列）
OLD_TASKS_DDL = """CREATE TABLE ai_group_tasks (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    parent_task_id TEXT,
    goal TEXT NOT NULL,
    constraints TEXT DEFAULT '[]',
    completion_criteria TEXT DEFAULT '[]',
    status TEXT DEFAULT 'planning',
    assigned_agent_id TEXT,
    result TEXT DEFAULT '',
    quality_score REAL DEFAULT 0,
    iteration INTEGER DEFAULT 0,
    max_iterations INTEGER DEFAULT 2,
    source_task_id TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL)"""

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


OLD_TS = (datetime.now() - timedelta(days=4)).isoformat()
NOW_TS = datetime.now().isoformat()


def _add_group(gid="g-iso"):
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at) VALUES (?,?,?,?,?)",
            (gid, "iso-test", "", "active", NOW_TS),
        )
        conn.commit()
    finally:
        conn.close()


def _add_task(tid, gid="g-iso", parent=None, status="pending", tenant="default", created=None):
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        ts = created or NOW_TS
        conn.execute(
            "INSERT INTO ai_group_tasks (id, group_id, parent_task_id, goal, status, tenant_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (tid, gid, parent, "测试任务", status, tenant, ts, ts),
        )
        conn.commit()
    finally:
        conn.close()


def _task(tid):
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        r = conn.execute("SELECT * FROM ai_group_tasks WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None
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
    """真实 router 进程内测试面。执行/链后台任务换 no-op（闸门在 spawn 之前，
    本文件只测隔离面不拉起真 agent 执行）。"""
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
    def test_tenant_column_added_and_backfilled(self, tmp_path, monkeypatch):
        import src.api.ai_groups as ai_groups

        db = tmp_path / "old_tasks.db"
        conn = sqlite3.connect(db)
        conn.execute(OLD_TASKS_DDL)
        conn.execute(
            "INSERT INTO ai_group_tasks (id, group_id, goal, status, created_at, updated_at) "
            "VALUES ('t_old', 'g1', 'x', 'completed', ?, ?)",
            (NOW_TS, NOW_TS),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(ai_groups, "DB_PATH", db)
        ai_groups.init_db()

        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ai_group_tasks)").fetchall()]
        row = conn.execute("SELECT tenant_id FROM ai_group_tasks WHERE id='t_old'").fetchone()
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tasks_tenant'"
        ).fetchone()
        conn.close()
        assert "tenant_id" in cols
        assert row[0] == "default"  # 存量行回填 default（与 F3 实测数据分布一致）
        assert idx is not None

    def test_row_roundtrip(self, env):
        _add_task("t_rt", tenant="tenant-a")
        assert _task("t_rt")["tenant_id"] == "tenant-a"


# ── ② 租户键落行（身份派生 + 内部继承）────────────────────────────────


class TestTenantFlow:
    def test_submit_derives_tenant_and_subtasks_inherit(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group()

        resp = api.post(
            "/api/ai-groups/g-iso/tasks", json={"goal": "分析设计实现测试"}, headers=tok_a
        )
        assert resp.status_code == 200
        tid = resp.json()["task_id"]
        assert _task(tid)["tenant_id"] == "tenant-a"
        # 子任务同源继承（auto_decompose 产出的全部子任务）
        conn_tasks = [r for r in api.get("/api/ai-groups/g-iso/tasks", headers=tok_a).json()]
        root = [t for t in conn_tasks if t["id"] == tid][0]
        assert root["subtasks"], "auto_decompose 应产出子任务"
        for st in root["subtasks"]:
            assert st["tenant_id"] == "tenant-a"

        # 匿名提交：'default' 契约不变
        resp2 = api.post("/api/ai-groups/g-iso/tasks", json={"goal": "匿名任务"})
        tid2 = resp2.json()["task_id"]
        assert _task(tid2)["tenant_id"] == "default"

    def test_start_discussion_tenant(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group()
        resp = api.post(
            "/api/ai-groups/g-iso/discuss", json={"goal": "讨论一下方案"}, headers=tok_a
        )
        assert resp.status_code == 200
        assert _task(resp.json()["task_id"])["tenant_id"] == "tenant-a"

    def test_decide_subtasks_inherit(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group()
        tid = api.post(
            "/api/ai-groups/g-iso/discuss", json={"goal": "讨论题"}, headers=tok_a
        ).json()["task_id"]
        resp = api.post(
            f"/api/ai-groups/g-iso/discuss/{tid}/decide",
            json={"assignments": [{"subgoal": "子任务A", "agent_id": "mimo"}]},
            headers=tok_a,
        )
        assert resp.status_code == 200
        # decide 派生子任务继承讨论任务租户
        import src.api.ai_groups as ai_groups

        c = ai_groups.get_db()
        try:
            subs = c.execute(
                "SELECT tenant_id FROM ai_group_tasks WHERE parent_task_id=?", (tid,)
            ).fetchall()
        finally:
            c.close()
        assert subs and all(s["tenant_id"] == "tenant-a" for s in subs)

    def test_post_task_discussion_inherits_source_tenant(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group()
        _add_task("t_src", status="scored", tenant="tenant-a")
        resp = api.post("/api/ai-groups/g-iso/tasks/t_src/discuss-after", headers=tok_a)
        assert resp.status_code == 200
        did = resp.json()["discussion_task_id"]
        d = _task(did)
        assert d["tenant_id"] == "tenant-a"  # 讨论任务从源任务行继承
        assert d["source_task_id"] == "t_src"


# ── ③ 查询过滤 ────────────────────────────────────────────────────────


class TestQueryFilter:
    def test_list_scoped_per_tenant(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        _add_group()
        _add_task("a1", tenant="tenant-a")
        _add_task("a2", parent="a1", tenant="tenant-a")
        _add_task("b1", tenant="tenant-b")

        listed_a = {t["id"] for t in api.get("/api/ai-groups/g-iso/tasks", headers=tok_a).json()}
        listed_b = {t["id"] for t in api.get("/api/ai-groups/g-iso/tasks", headers=tok_b).json()}
        listed_anon = {t["id"] for t in api.get("/api/ai-groups/g-iso/tasks").json()}
        assert listed_a == {"a1"}
        assert listed_b == {"b1"}
        assert listed_anon == set()  # 匿名 default 租户看不到他租户任务
        # 子任务随父过滤：A 的子任务只在 A 面出现
        root_a = api.get("/api/ai-groups/g-iso/tasks", headers=tok_a).json()[0]
        assert [s["id"] for s in root_a["subtasks"]] == ["a2"]

    def test_govern_scoped_per_tenant(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        _add_group()
        # 双租户各有超龄未启动任务（R2 目标）
        _add_task("stale_a", tenant="tenant-a", created=OLD_TS)
        _add_task("stale_b", tenant="tenant-b", created=OLD_TS)

        stats_b = api.post("/api/ai-groups/g-iso/tasks/govern", headers=tok_b).json()
        assert stats_b["skipped_stale"] == 1  # 只治理自己租户名下
        assert _task("stale_b")["status"] == "skipped"
        assert _task("stale_a")["status"] == "pending"  # 他租户行绝不动

        stats_a = api.post("/api/ai-groups/g-iso/tasks/govern", headers=tok_a).json()
        assert stats_a["skipped_stale"] == 1
        assert _task("stale_a")["status"] == "skipped"


# ── ④ IsolationEngine.check_access 真执行 ────────────────────────────


class TestIsolationGate:
    def test_cross_tenant_gate_denied_with_audit(self, env):
        import src.api.ai_groups as ai_groups

        with pytest.raises(ai_groups.TaskAccessDenied) as e:
            ai_groups._gate_task("tenant-a", "tenant-b", "tid-x", "read")
        assert "tenant" in e.value.reason

        # 引擎真被执行：审计留痕 + 计数器（F3 审计"数据面从未咨询隔离引擎"续闭）
        iso = ai_groups._iso()
        stats = iso.stats()
        assert stats["total_access_checks"] >= 1
        assert stats["blocked_attempts"] >= 1
        blocked = iso.get_access_log(allowed=False)
        hit = [
            x for x in blocked if x["resource_id"] == "ns-tenant-b:tid-x" and x["action"] == "read"
        ]
        assert hit, f"blocked audit entry missing: {blocked}"
        assert "namespace mismatch" in hit[0]["reason"]
        assert hit[0]["resource_type"] == "ai_group_task"

    def test_same_tenant_gate_allowed_and_logged(self, env):
        import src.api.ai_groups as ai_groups

        ai_groups._gate_task("tenant-a", "tenant-a", "tid-y", "write")
        allowed = ai_groups._iso().get_access_log(allowed=True)
        assert any(
            x["resource_id"] == "ns-tenant-a:tid-y" and x["action"] == "write" for x in allowed
        )

    def test_bulk_calls_audited(self, env):
        import src.api.ai_groups as ai_groups

        ai_groups._gate_task_bulk("tenant-a", "list")
        ai_groups._gate_task_bulk("tenant-a", "write")
        log = ai_groups._iso().get_access_log(tenant_id="ns-tenant-a")
        actions = {x["action"] for x in log}
        assert {"list", "write"} <= actions
        assert ai_groups._iso().stats()["total_access_checks"] >= 2


# ── ⑤ API 层：认证派生租户 → 行归属 → 闸门 403 全链 ───────────────────


class TestAPI:
    def test_auth_to_gate_full_chain(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        _add_group()

        # A 提交：行归属从认证身份派生落库
        resp = api.post("/api/ai-groups/g-iso/tasks", json={"goal": "实现一个功能"}, headers=tok_a)
        assert resp.status_code == 200
        tid = resp.json()["task_id"]
        assert _task(tid)["tenant_id"] == "tenant-a"

        # A 读写自己的：全通
        assert (
            api.get(f"/api/ai-groups/g-iso/tasks/{tid}/exec-status", headers=tok_a).status_code
            == 200
        )
        assert api.get(f"/api/ai-groups/g-iso/tasks/{tid}/events", headers=tok_a).status_code == 200
        assert (
            api.post(
                f"/api/ai-groups/g-iso/tasks/{tid}/score",
                json={"scorer_agent_id": "mimo", "score": 8},
                headers=tok_a,
            ).status_code
            == 200
        )

        # B 冒读/冒写 A 的任务：403（闸门真拒绝，不是 404 假 not_found、更不是静默 200）
        denied_urls = [
            ("GET", f"/api/ai-groups/g-iso/tasks/{tid}/exec-status"),
            ("GET", f"/api/ai-groups/g-iso/tasks/{tid}/events"),
            ("GET", f"/api/ai-groups/g-iso/tasks/{tid}/discuss-x"),  # 不存在的子路由→404/405 不算
        ]
        for method, url in denied_urls[:2]:
            r = api.get(url, headers=tok_b)
            assert r.status_code == 403, f"{url} 应 403 实得 {r.status_code}"
            assert "access denied" in r.json()["detail"]
            assert "namespace" in r.json()["detail"]

        denied_posts = [
            (f"/api/ai-groups/g-iso/tasks/{tid}/execute", {"agent_id": "mimo"}),
            (f"/api/ai-groups/g-iso/tasks/{tid}/retry", {}),
            (f"/api/ai-groups/g-iso/tasks/{tid}/assign", {"agent_id": "mimo"}),
            (f"/api/ai-groups/g-iso/tasks/{tid}/complete", {"result": "x", "quality_score": 5}),
            (f"/api/ai-groups/g-iso/tasks/{tid}/verify", {"passed": True}),
            (f"/api/ai-groups/g-iso/tasks/{tid}/review", {"result": "x"}),
            (f"/api/ai-groups/g-iso/tasks/{tid}/score", {"scorer_agent_id": "mimo", "score": 8}),
            (f"/api/ai-groups/g-iso/tasks/{tid}/auto-evaluate", {"success": True}),
            (f"/api/ai-groups/g-iso/tasks/{tid}/discuss-after", {}),
            (f"/api/ai-groups/g-iso/tasks/{tid}/exec-cancel", {"force": False}),
        ]
        for url, body in denied_posts:
            r = api.post(url, json=body, headers=tok_b)
            assert r.status_code == 403, f"{url} 应 403 实得 {r.status_code}"
            assert "access denied" in r.json()["detail"]

        # 匿名同样 403（匿名钉 default ≠ tenant-a）
        assert api.get(f"/api/ai-groups/g-iso/tasks/{tid}/events").status_code == 403

        # B 的列表/治理面看不到也动不了 A 的任务
        assert api.get("/api/ai-groups/g-iso/tasks", headers=tok_b).json() == []
        gov = api.post("/api/ai-groups/g-iso/tasks/govern", headers=tok_b).json()
        assert gov["skipped_stale"] + gov["skipped_parent_finished"] + gov["parent_finalized"] == 0
        assert _task(tid)["status"] != "skipped"

    def test_anonymous_default_contract_unchanged(self, api, env):
        _add_group()
        resp = api.post("/api/ai-groups/g-iso/tasks", json={"goal": "匿名任务"})
        assert resp.status_code == 200
        tid = resp.json()["task_id"]
        assert _task(tid)["tenant_id"] == "default"
        # 匿名读写照常（既有本地调用方契约不变）
        assert api.get(f"/api/ai-groups/g-iso/tasks/{tid}/exec-status").status_code == 200
        assert (
            api.post(f"/api/ai-groups/g-iso/tasks/{tid}/review", json={"result": "ok"}).status_code
            == 200
        )
        listed = {t["id"] for t in api.get("/api/ai-groups/g-iso/tasks").json()}
        assert tid in listed

    def test_discussion_message_face_gated(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        _add_group()
        tid = api.post(
            "/api/ai-groups/g-iso/discuss", json={"goal": "讨论题"}, headers=tok_a
        ).json()["task_id"]
        # A 能读自己的讨论消息面
        assert (
            api.get(f"/api/ai-groups/g-iso/discuss/{tid}/messages", headers=tok_a).status_code
            == 200
        )
        # B 冒读/插话/decide 全 403
        assert (
            api.get(f"/api/ai-groups/g-iso/discuss/{tid}/messages", headers=tok_b).status_code
            == 403
        )
        r = api.post(
            f"/api/ai-groups/g-iso/discuss/{tid}/respond",
            json={"agent_id": "mimo", "content": "插话"},
            headers=tok_b,
        )
        assert r.status_code == 403
        r = api.post(
            f"/api/ai-groups/g-iso/discuss/{tid}/decide",
            json={"assignments": []},
            headers=tok_b,
        )
        assert r.status_code == 403

    def test_missing_task_still_404(self, api, env):
        _add_group()
        # 行加载型端点照旧 404（闸门不把 not_found 洗成 403）
        assert (
            api.post("/api/ai-groups/g-iso/tasks/nope/verify", json={"passed": True}).status_code
            == 404
        )
        assert api.get("/api/ai-groups/g-iso/discuss/nope/messages").status_code == 404
        # 容忍缺行端点契约不变（events 缺行仍 200 空列表）
        r = api.get("/api/ai-groups/g-iso/tasks/nope/events")
        assert r.status_code == 200 and r.json()["events"] == []
