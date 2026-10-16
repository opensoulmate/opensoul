"""G1-⑤c-③ agent_reports 表租户加轴测试——行租户键+行归属单一权威。

修前（G1-⑤c-② 诚实边界留档「agent_reports 无租户列（另拍）」）：agent_reports
零租户列，/report 落行无行归属键——非 default 租户 agent 的上报行无法按租户
归属（未来读面一旦加租户过滤即全部落 'default' 桶，⑨ 审计同型）。本文件验证：

①迁移自愈：agent_reports.tenant_id 缺列 ALTER 幂等补+存量行回填 'default'
  +租户索引（补列之后建，③c-2① 坑纪律；重连幂等）
②行归属：/report 行租户=agents 行归属租户单一权威（_node_row_tenant 同源，
  心跳/注销桥键禁手抄）——租户 A/B agent 上报互不串、'default' 租户契约
③兜底：行租户键空白→'default'（键派生故障绝不吃掉落库，③b-2 纪律）
④claim 持证零回归：矛盾 body agent_id 403 零落库（跨租户写路）
⑤响应契约：status/agent_id 逐键不变（加性列不改 API 形状）

全部 tmp 独立库（USERS_DB/ENTERPRISE_DB/NEST_DB env 归 tmp + tmp SQLitePool），
测试行绝不写生产 data/*.db。
"""

import sqlite3
from contextlib import asynccontextmanager
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

AGENTS_DDL = """CREATE TABLE agents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
    type TEXT DEFAULT 'generic', status TEXT DEFAULT 'offline',
    capabilities TEXT DEFAULT '[]', config TEXT DEFAULT '{}',
    logo_url TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    agent_type TEXT DEFAULT '', metadata TEXT DEFAULT '{}',
    token TEXT DEFAULT NULL, last_heartbeat TEXT DEFAULT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default')"""

# 老库形态（无 tenant_id）——迁移自愈测试本体：connect 时补列+回填+索引。
AGENT_REPORTS_DDL_LEGACY = """CREATE TABLE agent_reports (
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


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离数据面：tmp opensoul.db（agents/agent_reports 真源）+ 两个租户
    admin 身份（tenant-a/tenant-b）+ 一条存量 report 行（回填面）。"""
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setenv("NEST_DB", str(tmp_path / "nest.db"))

    db = tmp_path / "opensoul.db"
    conn = sqlite3.connect(str(db))
    conn.execute(USERS_DDL)
    conn.execute(AGENTS_DDL)
    conn.execute(AGENT_REPORTS_DDL_LEGACY)
    uid_a, uid_b, uid_d = str(uuid4()), str(uuid4()), str(uuid4())
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
    # 'default' 租户用户（匿名='default' 契约的可达形态——register admin-only）
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, tenant_id) "
        "VALUES (?, 'admin-d', 'x', 'admin', 'default')",
        (uid_d,),
    )
    legacy_report_agent = str(uuid4())
    conn.execute(
        "INSERT INTO agent_reports (agent_id, report_type, data) VALUES (?, 'legacy-report', '{}')",
        (legacy_report_agent,),
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

    app = FastAPI(lifespan=_lifespan)
    app.include_router(agent_api.router, prefix="/api/agent")
    with TestClient(app) as c:
        yield {
            "client": c,
            "db": db,
            "uid_a": uid_a,
            "uid_b": uid_b,
            "uid_d": uid_d,
            "legacy_report_agent": legacy_report_agent,
        }


def _register(client, uid, name, agent_type="generic"):
    r = client.post(
        "/api/agent/register",
        json={"name": name, "agent_type": agent_type},
        headers=_headers(uid),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _report(client, token, agent_id, report_type="metrics", data=None):
    return client.post(
        "/api/agent/report",
        json={
            "agent_id": agent_id,
            "report_type": report_type,
            "data": data if data is not None else {"k": "v"},
        },
        headers={"X-Agent-Token": token},
    )


# ── ① 迁移自愈：列/回填/索引 ──────────────────────────────────────────


class TestSchemaHeal:
    def test_tenant_column_backfill_and_index(self, env):
        db = env["db"]
        cols = {r["name"] for r in _rows(db, "PRAGMA table_info(agent_reports)")}
        assert "tenant_id" in cols
        # 存量行回填 'default'（与 F3 实测分布一致）
        rows = _rows(
            db,
            "SELECT tenant_id FROM agent_reports WHERE agent_id = ?",
            (env["legacy_report_agent"],),
        )
        assert rows and rows[0]["tenant_id"] == "default"
        # 租户索引真建（补列之后，③c-2① 坑纪律）
        idx = _rows(
            db,
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_agent_reports_tenant'",
        )
        assert idx

    def test_reconnect_idempotent(self, env, monkeypatch):
        """重复 connect 幂等：列不重复补、索引不重建炸、数据不动。"""
        import asyncio

        from src.database.postgres import SQLitePool

        pool2 = SQLitePool()
        asyncio.run(pool2.connect())
        asyncio.run(pool2.disconnect())
        db = env["db"]
        cols = [r["name"] for r in _rows(db, "PRAGMA table_info(agent_reports)")]
        assert cols.count("tenant_id") == 1
        rows = _rows(db, "SELECT COUNT(*) AS n FROM agent_reports")
        assert rows[0]["n"] == 1  # 幂等重连零新增行


# ── ② 行归属：agents 行归属租户单一权威 ────────────────────────────────


class TestRowAttribution:
    def test_report_row_tenant_follows_agent_row(self, env):
        client, db = env["client"], env["db"]
        a = _register(client, env["uid_a"], name="a-node")
        b = _register(client, env["uid_b"], name="b-node")
        ra = _report(client, a["token"], a["agent_id"], data={"src": "a"})
        assert ra.status_code == 200, ra.text
        rb = _report(client, b["token"], b["agent_id"], data={"src": "b"})
        assert rb.status_code == 200, rb.text
        rows = _rows(
            db,
            "SELECT agent_id, tenant_id FROM agent_reports WHERE report_type = 'metrics' "
            "ORDER BY agent_id",
        )
        by_agent = {r["agent_id"]: r["tenant_id"] for r in rows}
        assert by_agent[a["agent_id"]] == "tenant-a"  # 修前无列=无法归属
        assert by_agent[b["agent_id"]] == "tenant-b"  # 双租户互不串

    def test_default_tenant_contract(self, env):
        """'default' 租户 agent 上报行 'default' 契约不变（修前无列=隐式 default）。"""
        client, db = env["client"], env["db"]
        r = client.post(
            "/api/agent/register",
            json={"name": "default-node", "agent_type": "generic"},
            headers=_headers(env["uid_d"]),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["tenant_id"] == "default"
        assert _report(client, data["token"], data["agent_id"]).status_code == 200
        rows = _rows(
            db,
            "SELECT tenant_id FROM agent_reports WHERE agent_id = ?",
            (data["agent_id"],),
        )
        assert rows and rows[0]["tenant_id"] == "default"

    def test_response_shape_unchanged(self, env):
        client = env["client"]
        a = _register(client, env["uid_a"], name="a-node")
        r = _report(client, a["token"], a["agent_id"])
        body = r.json()
        assert set(body.keys()) == {"status", "agent_id"}
        assert body["status"] == "received"
        assert body["agent_id"] == a["agent_id"]


# ── ③ 兜底：键派生故障绝不吃掉落库 ────────────────────────────────────


class TestFallback:
    def test_blank_row_tenant_falls_back_default(self, env):
        client, db = env["client"], env["db"]
        a = _register(client, env["uid_a"], name="a-node")
        # 行租户键空白（键派生故障形态）→'default' 兜底，写入不丢
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE agents SET tenant_id = '' WHERE id = ?", (a["agent_id"],))
        conn.commit()
        conn.close()
        r = _report(client, a["token"], a["agent_id"], report_type="blank-fallback")
        assert r.status_code == 200, r.text
        rows = _rows(
            db,
            "SELECT tenant_id FROM agent_reports WHERE report_type = 'blank-fallback'",
        )
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"


# ── ④ claim 持证零回归 ─────────────────────────────────────────────────


class TestClaim:
    def test_claim_contradiction_403_zero_rows(self, env):
        client, db = env["client"], env["db"]
        a = _register(client, env["uid_a"], name="a-node")
        b = _register(client, env["uid_b"], name="b-node")
        before = _rows(db, "SELECT COUNT(*) AS n FROM agent_reports")[0]["n"]
        r = _report(client, a["token"], b["agent_id"], report_type="forged")
        assert r.status_code == 403, r.text
        after = _rows(db, "SELECT COUNT(*) AS n FROM agent_reports")[0]["n"]
        assert after == before  # 零落库

    def test_missing_token_401_zero_rows(self, env):
        client, db = env["client"], env["db"]
        a = _register(client, env["uid_a"], name="a-node")
        before = _rows(db, "SELECT COUNT(*) AS n FROM agent_reports")[0]["n"]
        r = client.post(
            "/api/agent/report",
            json={"agent_id": a["agent_id"], "report_type": "metrics", "data": {}},
        )
        assert r.status_code == 401
        after = _rows(db, "SELECT COUNT(*) AS n FROM agent_reports")[0]["n"]
        assert after == before
