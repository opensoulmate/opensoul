"""F3b-③b-3 AI 群组组面（ai_groups/ai_group_agents/agent_scores/agent_capabilities）
数据面隔离接线 + delete_group 级联按租户收口 测试。

覆盖五层（全部 tmp 独立库 + NEST_DB/USERS_DB/ENTERPRISE_DB 环境隔离，绝不写生产
data/*.db）：
①四表 tenant_id 列迁移（老库补列+存量行回填 'default'）与索引
②租户键落行（组/成员=身份派生或组行继承；评分行=父任务行继承；能力行=组行继承）
③查询过滤（list_groups/get_group 子查询按租户，跨租户行互不可见）
④IsolationEngine.check_access 真执行（组闸门：跨租户 GroupAccessDenied
  +引擎审计留痕 ns-tenant-b:group_id 硬断言——数据面组轴真消费）
⑤delete_group 级联按行租户键收口（自己行全清、他租户行一律不动）
+API 层派生租户全链（token → bind_identity → 行归属落库 → 闸门 403，真实 router）
"""

import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb

# 生产组面四表 F3b-③b-3 迁移前形状（无 tenant_id → 证明自愈补列）
OLD_GROUP_DDL = """CREATE TABLE ai_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL)"""

OLD_AGA_DDL = """CREATE TABLE ai_group_agents (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    model TEXT DEFAULT '',
    status TEXT DEFAULT 'online')"""

OLD_CAPS_DDL = """CREATE TABLE agent_capabilities (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    avg_score REAL DEFAULT 0,
    task_count INTEGER DEFAULT 0,
    trend TEXT DEFAULT 'stable',
    updated_at TEXT NOT NULL,
    UNIQUE(group_id, agent_id, capability))"""

OLD_SCORES_DDL = """CREATE TABLE agent_scores (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    scored_agent_id TEXT NOT NULL,
    scorer_agent_id TEXT NOT NULL,
    score INTEGER NOT NULL,
    reason TEXT DEFAULT '',
    capability TEXT DEFAULT '',
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


def _sql(sql, params=()):
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur
    finally:
        conn.close()


def _rows(table, where="", params=()):
    import src.api.ai_groups as ai_groups

    conn = ai_groups.get_db()
    try:
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _add_group(gid="g-grp", tenant="default"):
    _sql(
        "INSERT OR REPLACE INTO ai_groups (id,name,description,status,created_at,tenant_id) VALUES (?,?,?,?,?,?)",
        (gid, "grp-test", "", "active", NOW_TS, tenant),
    )


def _add_task(tid, gid="g-grp", tenant="default", status="pending"):
    _sql(
        "INSERT INTO ai_group_tasks (id, group_id, goal, status, tenant_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (tid, gid, "测试任务", status, tenant, NOW_TS, NOW_TS),
    )


def _add_agent_row(aid, gid="g-grp", tenant="default", agent_id="mimo"):
    _sql(
        "INSERT INTO ai_group_agents (id, group_id, agent_id, name, role, model, status, tenant_id) VALUES (?,?,?,?,?,?,?,?)",
        (aid, gid, agent_id, "测试成员", "executor", "", "online", tenant),
    )


def _add_msg(mid, gid, tid, tenant):
    _sql(
        "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, metadata, created_at, tenant_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (mid, gid, tid, "mimo", "", "comment", "hi", "{}", NOW_TS, tenant),
    )


def _add_evt(gid, tid, tenant):
    _sql(
        "INSERT INTO task_execution_events (group_id, task_id, agent_id, event_type, content, created_at, tenant_id) VALUES (?,?,?,?,?,?,?)",
        (gid, tid, "mimo", "output", "x", NOW_TS, tenant),
    )


def _add_score(sid, gid, tid, tenant):
    _sql(
        "INSERT INTO agent_scores (id, group_id, task_id, scored_agent_id, scorer_agent_id, score, reason, capability, created_at, tenant_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sid, gid, tid, "mimo", "auto-scorer", 8, "", "cap", NOW_TS, tenant),
    )


def _add_cap(cid, gid, tenant, agent_id="mimo"):
    _sql(
        "INSERT INTO agent_capabilities (id, group_id, agent_id, capability, avg_score, task_count, trend, updated_at, tenant_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (cid, gid, agent_id, "cap", 8.0, 1, "stable", NOW_TS, tenant),
    )


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
    def test_group_tables_tenant_column_added_and_backfilled(self, tmp_path, monkeypatch):
        import src.api.ai_groups as ai_groups

        db = tmp_path / "old_groups.db"
        conn = sqlite3.connect(db)
        for ddl in (OLD_GROUP_DDL, OLD_AGA_DDL, OLD_CAPS_DDL, OLD_SCORES_DDL):
            conn.execute(ddl)
        conn.execute(
            "INSERT INTO ai_groups (id,name,description,status,created_at) VALUES ('g1','n','','active','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO ai_group_agents (id,group_id,agent_id,name,role,model,status) VALUES ('a1','g1','m','n','executor','','online')"
        )
        conn.execute(
            "INSERT INTO agent_capabilities (id,group_id,agent_id,capability,avg_score,task_count,trend,updated_at) VALUES ('c1','g1','m','cap',8,1,'stable','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO agent_scores (id,group_id,task_id,scored_agent_id,scorer_agent_id,score,reason,capability,created_at) VALUES ('s1','g1','t1','m','auto',8,'','','2026-01-01')"
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(ai_groups, "DB_PATH", db)
        ai_groups.init_db()

        conn = sqlite3.connect(db)
        for tbl in ("ai_groups", "ai_group_agents", "agent_scores", "agent_capabilities"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            assert "tenant_id" in cols, f"{tbl} 缺 tenant_id 列"
            vals = {r[0] for r in conn.execute(f"SELECT DISTINCT tenant_id FROM {tbl}").fetchall()}
            assert vals == {"default"}, f"{tbl} 存量行应回填 default 实得 {vals}"
        idx = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert {
            "idx_groups_tenant",
            "idx_aga_tenant",
            "idx_scores_tenant",
            "idx_caps_tenant",
        } <= idx
        conn.close()

    def test_row_roundtrip(self, env):
        _add_group("g-rt", "tenant-a")
        _add_agent_row("a-rt", "g-rt", "tenant-a")
        _add_score("s-rt", "g-rt", "t-rt", "tenant-a")
        _add_cap("c-rt", "g-rt", "tenant-a")
        assert _rows("ai_groups", "id=?", ("g-rt",))[0]["tenant_id"] == "tenant-a"
        assert _rows("ai_group_agents", "id=?", ("a-rt",))[0]["tenant_id"] == "tenant-a"
        assert _rows("agent_scores", "id=?", ("s-rt",))[0]["tenant_id"] == "tenant-a"
        assert _rows("agent_capabilities", "id=?", ("c-rt",))[0]["tenant_id"] == "tenant-a"


# ── ② 租户键落行 ──────────────────────────────────────────────────────


class TestRowKeys:
    def test_create_group_derives_tenant_and_members_inherit(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        r = api.post(
            "/api/ai-groups",
            json={
                "name": "g1",
                "agents": [{"agent_id": "mimo", "name": "m", "role": "executor"}],
            },
            headers=tok_a,
        )
        assert r.status_code == 200
        gid = r.json()["id"]
        assert _rows("ai_groups", "id=?", (gid,))[0]["tenant_id"] == "tenant-a"
        members = _rows("ai_group_agents", "group_id=?", (gid,))
        assert members and all(m["tenant_id"] == "tenant-a" for m in members)

    def test_add_agent_inherits_group_tenant(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        r = api.post(
            "/api/ai-groups",
            json={"name": "g1", "agents": []},
            headers=tok_a,
        )
        gid = r.json()["id"]
        r = api.post(
            f"/api/ai-groups/{gid}/agents",
            json={"agent_id": "hermes", "name": "h", "role": "verifier"},
            headers=tok_a,
        )
        assert r.status_code == 200
        rows = _rows("ai_group_agents", "group_id=? AND agent_id=?", (gid, "hermes"))
        assert rows and rows[0]["tenant_id"] == "tenant-a"

    def test_score_rows_inherit_task_tenant(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group("g-sc", "tenant-a")
        _add_task("t-sc", "g-sc", "tenant-a")
        r = api.post(
            "/api/ai-groups/g-sc/tasks/t-sc/score",
            json={"scorer_agent_id": "mimo", "score": 8},
            headers=tok_a,
        )
        assert r.status_code == 200
        scores = _rows("agent_scores", "task_id=?", ("t-sc",))
        assert scores and all(s["tenant_id"] == "tenant-a" for s in scores)

    def test_capability_row_inherits_group_tenant(self, env):
        import src.api.ai_groups as ai_groups

        _add_group("g-cap", "tenant-b")
        ai_groups.update_agent_capability("g-cap", "mimo", "cap", 8)
        rows = _rows("agent_capabilities", "group_id=?", ("g-cap",))
        assert rows and rows[0]["tenant_id"] == "tenant-b"


# ── ③ 查询过滤 ────────────────────────────────────────────────────────


class TestQueryFilter:
    def test_list_groups_scoped_per_tenant(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        r = api.post("/api/ai-groups", json={"name": "ga"}, headers=tok_a)
        gid_a = r.json()["id"]
        r = api.post("/api/ai-groups", json={"name": "gb"}, headers=tok_b)
        gid_b = r.json()["id"]
        _add_group("g-default", "default")

        ids_a = {g["id"] for g in api.get("/api/ai-groups", headers=tok_a).json()}
        ids_b = {g["id"] for g in api.get("/api/ai-groups", headers=tok_b).json()}
        ids_anon = {g["id"] for g in api.get("/api/ai-groups").json()}
        assert gid_a in ids_a and gid_b not in ids_a
        assert gid_b in ids_b and gid_a not in ids_b
        assert ids_anon == {"g-default"}

    def test_get_group_subqueries_scoped(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        _add_group("g-f", "tenant-a")
        _add_task("t-own", "g-f", "tenant-a")
        _add_task("t-foreign", "g-f", "tenant-b")
        _add_agent_row("a-own", "g-f", "tenant-a")
        _add_agent_row("a-foreign", "g-f", "tenant-b")
        g = api.get("/api/ai-groups/g-f", headers=tok_a).json()
        assert [t["id"] for t in g["tasks"]] == ["t-own"]
        assert [a["id"] for a in g["agents"]] == ["a-own"]


# ── ④ IsolationEngine 组闸门 ─────────────────────────────────────────


class TestGroupGate:
    def test_cross_tenant_group_gate_denied_with_audit(self, env):
        import src.api.ai_groups as ai_groups

        with pytest.raises(ai_groups.GroupAccessDenied) as e:
            ai_groups._gate_group("tenant-a", "tenant-b", "gid-x", "read")
        assert "tenant" in e.value.reason

        # 引擎真被执行：审计留痕 + 计数器（组轴真消费）
        iso = ai_groups._iso()
        stats = iso.stats()
        assert stats["total_access_checks"] >= 1
        assert stats["blocked_attempts"] >= 1
        blocked = iso.get_access_log(allowed=False)
        hit = [
            x for x in blocked if x["resource_id"] == "ns-tenant-b:gid-x" and x["action"] == "read"
        ]
        assert hit, f"blocked audit entry missing: {blocked}"
        assert "namespace mismatch" in hit[0]["reason"]
        assert hit[0]["resource_type"] == "ai_group"

    def test_same_tenant_group_gate_allowed_and_logged(self, env):
        import src.api.ai_groups as ai_groups

        ai_groups._gate_group("tenant-a", "tenant-a", "gid-y", "write")
        allowed = ai_groups._iso().get_access_log(allowed=True)
        assert any(
            x["resource_id"] == "ns-tenant-a:gid-y" and x["action"] == "write" for x in allowed
        )

    def test_api_group_endpoints_cross_tenant_battery(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        gid = api.post(
            "/api/ai-groups",
            json={"name": "ga", "agents": [{"agent_id": "mimo", "name": "m", "role": "executor"}]},
            headers=tok_a,
        ).json()["id"]

        # A 正对照：全通（闸门不误伤同租户）
        assert api.get(f"/api/ai-groups/{gid}", headers=tok_a).status_code == 200
        assert (
            api.patch(f"/api/ai-groups/{gid}", json={"name": "ga2"}, headers=tok_a).status_code
            == 200
        )
        assert (
            api.post(
                f"/api/ai-groups/{gid}/agents",
                json={"agent_id": "hermes", "name": "h", "role": "verifier"},
                headers=tok_a,
            ).status_code
            == 200
        )
        assert (
            api.get(f"/api/ai-groups/{gid}/agents/mimo/capabilities", headers=tok_a).status_code
            == 200
        )
        assert (
            api.post(
                f"/api/ai-groups/{gid}/smart-assign", json={"goal": "x"}, headers=tok_a
            ).status_code
            == 200
        )

        # B 冒读/冒写 A 的组：8 端点全 403（真拒绝，不是 404 假 not_found、更不是静默 200）
        denied = [
            ("GET", f"/api/ai-groups/{gid}", None),
            ("PATCH", f"/api/ai-groups/{gid}", {"name": "hijack"}),
            ("DELETE", f"/api/ai-groups/{gid}", None),
            (
                "POST",
                f"/api/ai-groups/{gid}/agents",
                {"agent_id": "x", "name": "x", "role": "executor"},
            ),
            ("DELETE", f"/api/ai-groups/{gid}/agents/mimo", None),
            ("PATCH", f"/api/ai-groups/{gid}/agents/mimo", {"model": "hijack"}),
            ("POST", f"/api/ai-groups/{gid}/smart-assign", {"goal": "x"}),
            ("GET", f"/api/ai-groups/{gid}/agents/mimo/capabilities", None),
        ]
        for method, url, body in denied:
            r = api.request(method, url, json=body, headers=tok_b)
            assert r.status_code == 403, f"{method} {url} 应 403 实得 {r.status_code}"
            assert "access denied" in r.json()["detail"]
            assert "namespace" in r.json()["detail"]
        # 匿名同拒（匿名='default' 非组归属租户）
        assert api.get(f"/api/ai-groups/{gid}").status_code == 403

    def test_list_bulk_audited(self, api, env):
        import src.api.ai_groups as ai_groups

        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        assert api.get("/api/ai-groups", headers=tok_a).status_code == 200
        log = ai_groups._iso().get_access_log(tenant_id="ns-tenant-a")
        hits = [x for x in log if x["action"] == "list" and x["resource_type"] == "ai_group"]
        assert hits, f"bulk 审计缺 list 留痕: {log}"


# ── ⑤ delete_group 级联按租户收口 ─────────────────────────────────────


class TestDeleteCascade:
    def _seed_mixed_group(self, gid="g-del"):
        _add_group(gid, "tenant-a")
        _add_task("t-a", gid, "tenant-a")
        _add_task("t-b", gid, "tenant-b")
        _add_agent_row("a-a", gid, "tenant-a")
        _add_agent_row("a-b", gid, "tenant-b")
        _add_msg("m-a", gid, "t-a", "tenant-a")
        _add_msg("m-b", gid, "t-b", "tenant-b")
        _add_evt(gid, "t-a", "tenant-a")
        _add_evt(gid, "t-b", "tenant-b")
        _add_score("s-a", gid, "t-a", "tenant-a")
        _add_score("s-b", gid, "t-b", "tenant-b")
        _add_cap("c-a", gid, "tenant-a")
        # 能力表 UNIQUE(group_id, agent_id, capability) 不含租户键——外来行换 agent_id
        _add_cap("c-b", gid, "tenant-b", agent_id="hermes")

    def test_delete_scoped_own_rows_cleared_foreign_survive(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        self._seed_mixed_group()
        r = api.delete("/api/ai-groups/g-del", headers=tok_a)
        assert r.status_code == 200
        assert r.json()["status"] == "deleted"

        # 自己（组归属租户）的行全清
        for tbl, where in (
            ("ai_groups", "id='g-del'"),
            ("ai_group_tasks", "id='t-a'"),
            ("ai_group_agents", "id='a-a'"),
            ("discussion_messages", "id='m-a'"),
            ("task_execution_events", "task_id='t-a'"),
            ("agent_scores", "id='s-a'"),
            ("agent_capabilities", "id='c-a'"),
        ):
            assert _rows(tbl, where) == [], f"{tbl} 自己行未清: {_rows(tbl, where)}"

        # 他租户行一律不动（禁跨租户破坏优先于不留孤儿）
        for tbl, where in (
            ("ai_group_tasks", "id='t-b'"),
            ("ai_group_agents", "id='a-b'"),
            ("discussion_messages", "id='m-b'"),
            ("task_execution_events", "task_id='t-b'"),
            ("agent_scores", "id='s-b'"),
            ("agent_capabilities", "id='c-b'"),
        ):
            assert len(_rows(tbl, where)) == 1, f"{tbl} 他租户行被误删"

    def test_cross_tenant_delete_denied_nothing_deleted(self, api, env):
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        self._seed_mixed_group()
        r = api.delete("/api/ai-groups/g-del", headers=tok_b)
        assert r.status_code == 403
        assert "namespace" in r.json()["detail"]
        # 组与全部子行原样
        assert len(_rows("ai_groups", "id='g-del'")) == 1
        assert len(_rows("ai_group_tasks")) == 2
        assert len(_rows("ai_group_agents")) == 2
        assert len(_rows("discussion_messages")) == 2
        assert len(_rows("task_execution_events")) == 2
        assert len(_rows("agent_scores")) == 2
        assert len(_rows("agent_capabilities")) == 2

    def test_anonymous_contract_default_rows(self, api, env):
        # 存量 'default' 行（回填语义）匿名可见可删（契约不变）
        _add_group("g-anon", "default")
        _add_task("t-anon", "g-anon", "default")
        ids = {g["id"] for g in api.get("/api/ai-groups").json()}
        assert "g-anon" in ids
        assert api.get("/api/ai-groups/g-anon").status_code == 200
        assert api.delete("/api/ai-groups/g-anon").status_code == 200
        assert _rows("ai_groups", "id='g-anon'") == []
        assert _rows("ai_group_tasks", "id='t-anon'") == []
