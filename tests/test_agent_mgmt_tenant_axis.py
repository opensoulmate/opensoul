"""G1-⑤c-② 管理面租户轴收口测试——agents 表行租户键+身份派生+协调键随行归属。

修前（G1-⑤c 诚实边界留档）：管理面无租户轴（agents 表无 tenant 列、/nodes 全局
列表、/stats 全局计数、删除全局可见），协调行钉 'default'。本文件验证：

①迁移自愈：agents.tenant_id 缺列 ALTER 幂等补+存量行回填 'default'+租户索引
  （补列之后建，③c-2① 坑纪律）
②行归属：register 落行 tenant=bind_identity 派生租户（禁自报）、响应加性键
③协调键随行归属：注册/心跳/注销桥键=行归属租户（修前钉 'default' 反证面）
④读面过滤：/nodes /stats 恒按派生租户（三面互不可见、匿名 'default' 契约不变）
⑤删除闸：by-ID 闸门跨租户 403 带真因零破坏（行/协调行存活）+行租户双键删除
⑥claim 持证：heartbeat/report 的 body agent_id 必须与 X-Agent-Token 行一致，
  矛盾 403 零副作用（跨租户写路收口——否则租户轴收口即谎报）

全部 tmp 独立库（USERS_DB/ENTERPRISE_DB/NEST_DB env 归 tmp + tmp SQLitePool），
测试行绝不写生产 data/*.db。
"""

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

USERS_DDL = """CREATE TABLE users (
    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
    email TEXT DEFAULT '', password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now')))"""

# 老库形态（无 tenant_id）——迁移自愈测试本体：connect 时补列+回填+索引。
AGENTS_DDL_LEGACY = """CREATE TABLE agents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
    type TEXT DEFAULT 'generic', status TEXT DEFAULT 'offline',
    capabilities TEXT DEFAULT '[]', config TEXT DEFAULT '{}',
    logo_url TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    agent_type TEXT DEFAULT '', metadata TEXT DEFAULT '{}',
    token TEXT DEFAULT NULL, last_heartbeat TEXT DEFAULT NULL)"""

AGENT_REPORTS_DDL = """CREATE TABLE agent_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, report_type TEXT,
    content TEXT DEFAULT '', data TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')))"""


def _headers(user_id: str) -> dict:
    from src.services.auth import create_access_token

    token = create_access_token(user_id=UUID(user_id), role="admin")
    return {"Authorization": f"Bearer {token}"}


def _rows(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _coord_rows(db, agent_id):
    try:
        return _rows(db, "SELECT * FROM agent_registry WHERE agent_id = ?", (agent_id,))
    except sqlite3.OperationalError:
        return []


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离数据面：tmp opensoul.db（agents/agent_registry/agent_reports 真源）
    + 两个租户 admin 身份（tenant-a/tenant-b）+ 一条存量 legacy 行（回填面）。"""
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setenv("NEST_DB", str(tmp_path / "nest.db"))

    db = tmp_path / "opensoul.db"
    conn = sqlite3.connect(str(db))
    conn.execute(USERS_DDL)
    conn.execute(AGENTS_DDL_LEGACY)
    conn.execute(AGENT_REPORTS_DDL)
    uid_a, uid_b = str(uuid4()), str(uuid4())
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, tenant_id) "
        "VALUES (?, 'admin-a', 'x', 'admin', 'tenant-a')",
        (uid_a,),
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, tenant_id) "
        "VALUES (?, 'admin-b', 'x', 'admin', 'tenant-b')",
        (uid_b,),
    )
    legacy_id = str(uuid4())
    conn.execute(
        "INSERT INTO agents (id, name, agent_type, status) VALUES (?, 'legacy-node', 'legacy', 'offline')",
        (legacy_id,),
    )
    conn.commit()
    conn.close()

    import src.api.agent as agent_api
    import src.database.postgres as pg_mod
    import src.middleware.auth as mw_auth
    from src.config import settings
    from src.database.postgres import SQLitePool

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db}")
    pool = SQLitePool()
    for mod in (agent_api, mw_auth, pg_mod):
        monkeypatch.setattr(mod, "db_pool", pool)

    @asynccontextmanager
    async def _lifespan(_app):
        await pool.connect()
        yield
        await pool.disconnect()

    from src.api.brain import router as brain_router

    app = FastAPI(lifespan=_lifespan)
    app.include_router(agent_api.router, prefix="/api/agent")
    app.include_router(brain_router, prefix="/api/brain")
    with TestClient(app) as c:
        yield {
            "client": c,
            "db": db,
            "uid_a": uid_a,
            "uid_b": uid_b,
            "legacy_id": legacy_id,
        }


def _register(client, uid, name, agent_type="generic"):
    r = client.post(
        "/api/agent/register",
        json={"name": name, "agent_type": agent_type},
        headers=_headers(uid),
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── ① 迁移自愈：列/回填/索引 ──────────────────────────────────────────


class TestSchemaHeal:
    def test_tenant_column_backfill_and_index(self, env):
        db = env["db"]
        cols = {r["name"] for r in _rows(db, "PRAGMA table_info(agents)")}
        assert "tenant_id" in cols
        # 存量行回填 'default'（与 F3 实测分布一致）
        rows = _rows(db, "SELECT tenant_id FROM agents WHERE id = ?", (env["legacy_id"],))
        assert rows and rows[0]["tenant_id"] == "default"
        # 租户索引真建（补列之后，③c-2① 坑纪律）
        idx = _rows(
            db, "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_agents_tenant'"
        )
        assert idx


# ── ② 行归属 + ③ 协调键随行归属 ──────────────────────────────────────


class TestRowAttribution:
    def test_register_row_tenant_and_coord_key(self, env):
        client, db = env["client"], env["db"]
        data = _register(client, env["uid_a"], name="a-node", agent_type="collector")
        assert data["tenant_id"] == "tenant-a"  # 响应加性键=身份派生
        rows = _rows(db, "SELECT tenant_id FROM agents WHERE id = ?", (data["agent_id"],))
        assert rows and rows[0]["tenant_id"] == "tenant-a"
        # 协调键随行归属（修前钉 'default' 反证面）
        crows = _coord_rows(db, data["agent_id"])
        assert len(crows) == 1
        assert crows[0]["tenant_id"] == "tenant-a"
        # /api/brain/agents 协作视图按租户可见（身份派生）
        r = client.get("/api/brain/agents", headers=_headers(env["uid_a"]))
        assert r.status_code == 200
        assert [a["agent_id"] for a in r.json()["agents"]] == [data["agent_id"]]

    def test_heartbeat_coord_key_follows_row_tenant(self, env):
        client, db = env["client"], env["db"]
        data = _register(client, env["uid_b"], name="b-node")
        r = client.post(
            "/api/agent/heartbeat",
            json={"agent_id": data["agent_id"], "status": "working"},
            headers={"X-Agent-Token": data["token"]},
        )
        assert r.status_code == 200, r.text
        crows = _coord_rows(db, data["agent_id"])
        assert crows and crows[0]["tenant_id"] == "tenant-b"  # 修前恒 'default'
        assert crows[0]["status"] == "working"


# ── ④ 读面过滤 ────────────────────────────────────────────────────────


class TestReadFilter:
    def test_nodes_and_stats_tenant_filtered(self, env):
        client, _db = env["client"], env["db"]
        a = _register(client, env["uid_a"], name="a-node")
        _register(client, env["uid_b"], name="b-node")

        ra = client.get("/api/agent/nodes", headers=_headers(env["uid_a"]))
        assert ra.status_code == 200
        rows_a = ra.json()
        assert [r["name"] for r in rows_a] == ["a-node"]  # 三面互不可见
        assert rows_a[0]["tenant_id"] == "tenant-a"  # 加性归属键

        rb = client.get("/api/agent/nodes", headers=_headers(env["uid_b"]))
        assert [r["name"] for r in rb.json()] == ["b-node"]

        sa = client.get("/api/agent/stats", headers=_headers(env["uid_a"])).json()
        assert sa["total_agents"] == 1  # 修前全局计数=2+legacy=3
        sb = client.get("/api/agent/stats", headers=_headers(env["uid_b"])).json()
        assert sb["total_agents"] == 1
        # status 过滤叠加租户过滤
        rf = client.get(
            "/api/agent/nodes",
            params={"status": "active"},
            headers=_headers(env["uid_a"]),
        )
        assert [r["id"] for r in rf.json()] == [a["agent_id"]]

    def test_anonymous_default_contract(self, env):
        client = env["client"]
        s = client.get("/api/agent/stats")
        assert s.status_code == 200
        assert s.json()["total_agents"] == 1  # 匿名='default' 视图（legacy 行）
        # /nodes 无鉴权轴既有契约不变
        assert client.get("/api/agent/nodes").status_code == 401


# ── ⑤ 删除闸 + 双键 ──────────────────────────────────────────────────


class TestDeleteGate:
    def test_cross_tenant_delete_gated_zero_damage(self, env):
        client, db = env["client"], env["db"]
        a = _register(client, env["uid_a"], name="a-node")
        r = client.delete(f"/api/agent/nodes/{a['agent_id']}", headers=_headers(env["uid_b"]))
        assert r.status_code == 403
        assert "access denied" in r.json()["detail"]  # 带真因禁静默 not_found
        assert _rows(db, "SELECT id FROM agents WHERE id = ?", (a["agent_id"],))  # 行存活
        assert _coord_rows(db, a["agent_id"])  # 协调行存活
        # 同租户正对照
        r = client.delete(f"/api/agent/nodes/{a['agent_id']}", headers=_headers(env["uid_a"]))
        assert r.status_code == 200
        assert _rows(db, "SELECT id FROM agents WHERE id = ?", (a["agent_id"],)) == []
        assert _coord_rows(db, a["agent_id"]) == []  # 注销桥键=行归属租户

    def test_delete_nonexistent_404_unchanged(self, env):
        r = env["client"].delete(f"/api/agent/nodes/{uuid4()}", headers=_headers(env["uid_a"]))
        assert r.status_code == 404


# ── ⑥ claim 持证 ─────────────────────────────────────────────────────


class TestClaimAxis:
    def test_heartbeat_claim_mismatch_403_zero_effect(self, env):
        client, db = env["client"], env["db"]
        a = _register(client, env["uid_a"], name="a-node")
        b = _register(client, env["uid_b"], name="b-node")
        # A 的 token 冒写 B 的行（跨租户写路收口本体）
        r = client.post(
            "/api/agent/heartbeat",
            json={"agent_id": b["agent_id"], "status": "hijacked"},
            headers={"X-Agent-Token": a["token"]},
        )
        assert r.status_code == 403
        assert "claim mismatch" in r.json()["detail"]
        brows = _rows(db, "SELECT status FROM agents WHERE id = ?", (b["agent_id"],))
        assert brows and brows[0]["status"] == "active"  # 行零改写
        bcrows = _coord_rows(db, b["agent_id"])
        assert bcrows and bcrows[0]["status"] != "hijacked"  # 协调行零改写

    def test_report_claim_mismatch_403_zero_row(self, env):
        client, db = env["client"], env["db"]
        a = _register(client, env["uid_a"], name="a-node")
        b = _register(client, env["uid_b"], name="b-node")
        r = client.post(
            "/api/agent/report",
            json={"agent_id": b["agent_id"], "report_type": "t", "data": {"x": 1}},
            headers={"X-Agent-Token": a["token"]},
        )
        assert r.status_code == 403
        assert _rows(db, "SELECT id FROM agent_reports") == []  # 零落库
        # claim 一致正对照
        r = client.post(
            "/api/agent/report",
            json={"agent_id": a["agent_id"], "report_type": "t", "data": {"x": 1}},
            headers={"X-Agent-Token": a["token"]},
        )
        assert r.status_code == 200
        rows = _rows(db, "SELECT agent_id FROM agent_reports")
        assert [x["agent_id"] for x in rows] == [a["agent_id"]]


# ── ⑦ 静态接线普查 ───────────────────────────────────────────────────


class TestStaticWiring:
    def test_tenant_axis_wired(self):
        """写了≠接线了：行归属/过滤/闸门/桥键调用点真在端点路径上（grep 同款普查）。"""
        src = (Path(__file__).parent.parent / "src" / "api" / "agent.py").read_text()
        pg = (Path(__file__).parent.parent / "src" / "database" / "postgres.py").read_text()
        # 落行链：identity.tenant_id → INSERT tenant_id
        assert "VALUES ($1, $2, $3, $4, $5, $6, 'active', $7)" in src
        assert "identity.tenant_id," in src
        # 读面过滤（nodes 双分支 + stats 双查询）
        assert src.count("WHERE tenant_id = $1") >= 2
        assert "WHERE status = $1 AND tenant_id = $2" in src
        # 闸门调用点
        assert '_gate(identity.tenant_id, row_tenant, str(node_id), "delete")' in src
        assert src.count('_gate_bulk(identity.tenant_id, "read")') == 2
        # claim 持证两写点
        assert src.count("_require_agent_claim(agent, req.agent_id)") == 2
        # 协调桥键=行归属（修前 _MGMT_COORD_TENANT 常量钉死收口——注释提及不计）
        assert "_MGMT_COORD_TENANT =" not in src
        # 迁移自愈登记
        assert "ALTER TABLE agents ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'" in pg
        # 租户索引真建（G1-⑤c-③ 起索引收口为 (表,列,索引名) 单一源循环——契约
        # 随更新，断言意图零丢失：agents/agent_reports 两表索引名逐字在场）
        assert '("agents", "tenant_id", "idx_agents_tenant")' in pg
        assert '("agent_reports", "tenant_id", "idx_agent_reports_tenant")' in pg
