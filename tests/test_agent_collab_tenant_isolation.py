"""F3b-③c-2②d agent_collaboration 面（agent_collaboration.db agents、agent_capabilities、
agent_messages 三表）数据面隔离接线测试。

覆盖五层（全部 tmp 独立库 + NEST_DB、USERS_DB、ENTERPRISE_DB 环境隔离，测试行绝不写
生产 data/agent_collaboration.db）：
①三表 tenant_id 列迁移（DDL+ALTER 双轨幂等、存量行回填 'default'、租户索引补列之后建）
  与粘性（重跑 _init_db 不洗租户键）
②行归属（agent 行=bind_identity 身份派生、能力行=agent 行继承、消息行=身份派生、
  WS 发方 agent 行）与跨租户劫持闸门（同 agent_id 他租户重注册 403 行不改写）
③查询过滤（/status 恒按请求者租户，修前全量列表=跨租户身份泄露）
④IsolationEngine.check_access 真执行（by-ID 闸门跨租户 403 带真因、bulk 审计留痕、
  审计行 ns 对账、total_access_checks/blocked 硬证）
⑤API 与 WS 契约（匿名='default' 不变、响应形状加性 tenant_id 键、坏 token 401、
  未注册 agent_id 语义不变、WS 跨租户投递拒绝+error 回执）

注：src.api.agent_collaboration 模块级 DB_PATH 在 import 期计算——fixture 内延迟
import + monkeypatch DB_PATH + 幂等 _init_db()，无论模块此前是否已 import 都落 tmp。
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings

# 生产 agent_collaboration.db F3b-③c-2②d 迁移前形状（三表均无 tenant_id）
OLD_AGENTS_DDL = """CREATE TABLE agents (
            agent_id   TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            model      TEXT DEFAULT '',
            role       TEXT DEFAULT '',
            endpoint   TEXT DEFAULT '',
            token      TEXT NOT NULL,
            status     TEXT DEFAULT 'online',
            last_seen  TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""

OLD_CAPS_DDL = """CREATE TABLE agent_capabilities (
            agent_id      TEXT NOT NULL,
            capability    TEXT NOT NULL,
            PRIMARY KEY (agent_id, capability))"""

OLD_MSGS_DDL = """CREATE TABLE agent_messages (
            id         TEXT PRIMARY KEY,
            from_agent TEXT NOT NULL,
            to_agent   TEXT NOT NULL,
            msg_type   TEXT NOT NULL,
            payload    TEXT DEFAULT '{}',
            created_at TEXT NOT NULL)"""

ENT_USERS_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _tok(uid):
    payload = {
        "sub": str(uid),
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _auth(uid):
    return {"Authorization": "Bearer " + _tok(uid)}


def _rows(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _exec(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _wait_rows(db, sql, params=(), expect=1, timeout=5.0):
    """WS 写路径是门户线程异步处理：短轮询等行落库（超时如实返回，断言必炸不假绿）。"""
    deadline = time.time() + timeout
    rows = _rows(db, sql, params)
    while len(rows) < expect and time.time() < deadline:
        time.sleep(0.05)
        rows = _rows(db, sql, params)
    return rows


@pytest.fixture()
def collab_env(tmp_path, monkeypatch):
    """agent_collaboration 隔离数据面：tmp agent_collaboration.db、enterprise.db、
    opensoul.db（NEST_DB 由 conftest autouse 隔离）。import 延迟+路径双保险，绝不碰
    生产库。uid 1=tenant-a、uid 2=tenant-b、uid 3=default。"""
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    conn = sqlite3.connect(str(tmp_path / "enterprise.db"))
    conn.execute(ENT_USERS_DDL)
    conn.execute("INSERT INTO users (username, password, tenant_id) VALUES ('ua', 'h', 'tenant-a')")
    conn.execute("INSERT INTO users (username, password, tenant_id) VALUES ('ub', 'h', 'tenant-b')")
    conn.execute("INSERT INTO users (username, password, tenant_id) VALUES ('ud', 'h', 'default')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("AGENT_COLLAB_DB", str(tmp_path / "agent_collaboration.db"))
    import src.api.agent_collaboration as collab

    monkeypatch.setattr(collab, "DB_PATH", tmp_path / "agent_collaboration.db")
    collab._init_db()  # 幂等：tmp 库建表+迁移
    return collab


@pytest.fixture()
def client(collab_env):
    app = FastAPI()
    app.include_router(collab_env.router, prefix="/api/collab")
    with TestClient(app) as c:
        yield c


# ── ① 迁移 + 粘性 ─────────────────────────────────────────────────────


class TestMigrationAndSticky:
    def test_old_db_columns_backfilled_and_indexed(self, collab_env, tmp_path, monkeypatch):
        db = tmp_path / "old_shape.db"
        conn = sqlite3.connect(str(db))
        conn.execute(OLD_AGENTS_DDL)
        conn.execute(OLD_CAPS_DDL)
        conn.execute(OLD_MSGS_DDL)
        conn.execute(
            "INSERT INTO agents (agent_id, name, token, last_seen, created_at) "
            "VALUES ('legacy-a', 'Legacy A', 'tok1', 't', 't')"
        )
        conn.execute(
            "INSERT INTO agents (agent_id, name, token, last_seen, created_at) "
            "VALUES ('legacy-b', 'Legacy B', 'tok2', 't', 't')"
        )
        conn.execute(
            "INSERT INTO agent_capabilities (agent_id, capability) VALUES ('legacy-a', 'read')"
        )
        conn.execute(
            "INSERT INTO agent_messages (id, from_agent, to_agent, msg_type, created_at) "
            "VALUES ('m1', 'legacy-a', 'legacy-b', 'task', 't')"
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(collab_env, "DB_PATH", db)
        collab_env._init_db()

        expected_idx = {
            "agents": "idx_collab_agents_tenant",
            "agent_capabilities": "idx_collab_caps_tenant",
            "agent_messages": "idx_collab_msgs_tenant",
        }
        for table in ("agents", "agent_capabilities", "agent_messages"):
            cols = [r["name"] for r in _rows(db, f"PRAGMA table_info({table})")]
            assert "tenant_id" in cols, table
            keys = [r["tenant_id"] for r in _rows(db, f"SELECT tenant_id FROM {table}")]
            assert keys and all(k == "default" for k in keys), (table, keys)
            idx = [r["name"] for r in _rows(db, f"PRAGMA index_list({table})")]
            assert expected_idx[table] in idx, (table, idx)

    def test_rerun_idempotent_and_sticky(self, collab_env):
        db = collab_env.DB_PATH
        _exec(
            db,
            "INSERT INTO agents (agent_id, name, token, last_seen, created_at, tenant_id) "
            "VALUES ('sticky-a', 'S', 'tok', 't', 't', 'tenant-x')",
        )
        _exec(
            db,
            "INSERT INTO agent_capabilities (agent_id, capability, tenant_id) VALUES ('sticky-a', 'read', 'tenant-x')",
        )
        _exec(
            db,
            "INSERT INTO agent_messages (id, from_agent, to_agent, msg_type, created_at, tenant_id) "
            "VALUES ('m-sticky', 'sticky-a', 'ghost', 'task', 't', 'tenant-x')",
        )
        collab_env._init_db()  # 重跑幂等：不洗租户键
        assert [r["tenant_id"] for r in _rows(db, "SELECT tenant_id FROM agents")] == ["tenant-x"]
        assert [r["tenant_id"] for r in _rows(db, "SELECT tenant_id FROM agent_capabilities")] == [
            "tenant-x"
        ]
        assert [r["tenant_id"] for r in _rows(db, "SELECT tenant_id FROM agent_messages")] == [
            "tenant-x"
        ]


# ── ② 行归属 + 跨租户劫持闸门 ─────────────────────────────────────────


class TestRowAttributionAndHijackGate:
    def test_register_rows_and_caps_inherit(self, collab_env, client):
        r = client.post(
            "/api/collab/register",
            json={"agent_id": "agent-a1", "name": "A1", "capabilities": ["read", "write"]},
            headers=_auth(1),
        )
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "tenant-a"
        db = collab_env.DB_PATH
        assert (
            _rows(db, "SELECT tenant_id FROM agents WHERE agent_id = 'agent-a1'")[0]["tenant_id"]
            == "tenant-a"
        )
        caps = _rows(db, "SELECT tenant_id FROM agent_capabilities WHERE agent_id = 'agent-a1'")
        assert len(caps) == 2 and all(c["tenant_id"] == "tenant-a" for c in caps)

    def test_register_cross_tenant_hijack_denied(self, collab_env, client):
        r = client.post(
            "/api/collab/register",
            json={"agent_id": "agent-x", "name": "A agent", "capabilities": ["read"]},
            headers=_auth(1),
        )
        assert r.status_code == 200
        before = _rows(
            collab_env.DB_PATH,
            "SELECT name, token, tenant_id FROM agents WHERE agent_id = 'agent-x'",
        )[0]

        r = client.post(
            "/api/collab/register",
            json={"agent_id": "agent-x", "name": "B hijack"},
            headers=_auth(2),
        )
        assert r.status_code == 403
        assert "cross-tenant" in r.json()["detail"]
        # 行零改写（名字/token/行键全不动）
        after = _rows(
            collab_env.DB_PATH,
            "SELECT name, token, tenant_id FROM agents WHERE agent_id = 'agent-x'",
        )[0]
        assert after == before
        # 引擎审计硬对账：deny 行 resource_id=行归属方 ns、访问者 ns 为 tenant-b
        iso = collab_env._iso()
        denials = [e for e in iso.get_access_log(limit=200) if not e["allowed"]]
        assert any(e["resource_id"] == "ns-tenant-a:agent-x" for e in denials)
        assert any(e["tenant_id"] == "ns-tenant-b" for e in denials)
        st = iso.stats()
        assert st["total_access_checks"] > 0 and st["blocked_attempts"] > 0

    def test_register_same_tenant_reregister_ok(self, collab_env, client):
        r1 = client.post(
            "/api/collab/register", json={"agent_id": "agent-a2", "name": "A2"}, headers=_auth(1)
        )
        r2 = client.post(
            "/api/collab/register", json={"agent_id": "agent-a2", "name": "A2 v2"}, headers=_auth(1)
        )
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["token"] != r2.json()["token"]  # token 轮换语义不变
        row = _rows(
            collab_env.DB_PATH, "SELECT name, tenant_id FROM agents WHERE agent_id = 'agent-a2'"
        )[0]
        assert row == {"name": "A2 v2", "tenant_id": "tenant-a"}


# ── ③ 消息隔离 + 查询过滤 ─────────────────────────────────────────────


class TestMessageIsolation:
    def test_message_row_tenant_and_unknown_parties(self, collab_env, client):
        r = client.post(
            "/api/collab/message",
            json={
                "from_agent": "ghost-1",
                "to_agent": "ghost-2",
                "type": "task",
                "payload": {"k": 1},
            },
            headers=_auth(2),
        )
        assert r.status_code == 200
        rows = _rows(collab_env.DB_PATH, "SELECT tenant_id FROM agent_messages")
        assert rows and rows[0]["tenant_id"] == "tenant-b"  # 未注册 agent_id 语义不变

    def test_message_cross_tenant_parties_denied(self, collab_env, client):
        client.post(
            "/api/collab/register", json={"agent_id": "agent-a1", "name": "A1"}, headers=_auth(1)
        )
        db = collab_env.DB_PATH
        before = len(_rows(db, "SELECT id FROM agent_messages"))
        # 冒名发方（from=他租户 agent）与跨租户收方（to=他租户 agent）都 403
        r1 = client.post(
            "/api/collab/message",
            json={"from_agent": "agent-a1", "to_agent": "ghost", "type": "task"},
            headers=_auth(2),
        )
        r2 = client.post(
            "/api/collab/message",
            json={"from_agent": "ghost", "to_agent": "agent-a1", "type": "task"},
            headers=_auth(2),
        )
        assert r1.status_code == 403 and r2.status_code == 403
        assert len(_rows(db, "SELECT id FROM agent_messages")) == before  # 拒绝=零落库
        # 同租户正对照
        r3 = client.post(
            "/api/collab/message",
            json={"from_agent": "agent-a1", "to_agent": "ghost", "type": "task"},
            headers=_auth(1),
        )
        assert r3.status_code == 200


class TestStatusFiltering:
    def test_status_three_way_isolation(self, collab_env, client):
        client.post(
            "/api/collab/register", json={"agent_id": "agent-a1", "name": "A1"}, headers=_auth(1)
        )
        client.post(
            "/api/collab/register", json={"agent_id": "agent-b1", "name": "B1"}, headers=_auth(2)
        )
        client.post("/api/collab/register", json={"agent_id": "agent-d1", "name": "D1"})

        a = client.get("/api/collab/status", headers=_auth(1)).json()
        b = client.get("/api/collab/status", headers=_auth(2)).json()
        d = client.get("/api/collab/status").json()
        assert {x["agent_id"] for x in a} == {"agent-a1"}
        assert {x["agent_id"] for x in b} == {"agent-b1"}
        assert {x["agent_id"] for x in d} == {"agent-d1"}
        assert a[0]["tenant_id"] == "tenant-a"
        assert b[0]["tenant_id"] == "tenant-b"
        assert d[0]["tenant_id"] == "default"

    def test_status_item_shape(self, collab_env, client):
        client.post(
            "/api/collab/register", json={"agent_id": "shape-1", "name": "S"}, headers=_auth(1)
        )
        item = client.get("/api/collab/status", headers=_auth(1)).json()[0]
        assert set(item.keys()) == {
            "agent_id",
            "name",
            "model",
            "role",
            "capabilities",
            "status",
            "last_seen",
            "tenant_id",
        }


# ── ④ 引擎审计 ────────────────────────────────────────────────────────


class TestEngineAudit:
    def test_bulk_audit_and_stats(self, collab_env, client):
        client.get("/api/collab/status", headers=_auth(1))
        client.post(
            "/api/collab/message",
            json={"from_agent": "ghost-1", "to_agent": "ghost-2", "type": "task"},
            headers=_auth(1),
        )
        iso = collab_env._iso()
        log = iso.get_access_log(limit=200)
        assert any(
            e["resource_type"] == "agent" and e["resource_id"] == "ns-tenant-a:*" and e["allowed"]
            for e in log
        )
        assert any(
            e["resource_type"] == "agent_message"
            and e["resource_id"] == "ns-tenant-a:*"
            and e["allowed"]
            for e in log
        )
        st = iso.stats()
        assert st["total_access_checks"] > 0


# ── ⑤ API/WS 契约 ────────────────────────────────────────────────────


class TestApiContract:
    def test_anonymous_default_contract(self, collab_env, client):
        r = client.post("/api/collab/register", json={"agent_id": "anon-1", "name": "Anon"})
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "default"
        data = client.get("/api/collab/status").json()
        assert any(x["agent_id"] == "anon-1" and x["tenant_id"] == "default" for x in data)

    def test_bad_token_401(self, collab_env, client):
        bad = {"Authorization": "Bearer not-a-jwt"}
        assert (
            client.post(
                "/api/collab/register", json={"agent_id": "x", "name": "x"}, headers=bad
            ).status_code
            == 401
        )
        assert client.get("/api/collab/status", headers=bad).status_code == 401
        assert (
            client.post(
                "/api/collab/message",
                json={"from_agent": "a", "to_agent": "b", "type": "task"},
                headers=bad,
            ).status_code
            == 401
        )


class TestWsFace:
    def test_ws_row_attribution_and_cross_tenant_delivery(self, collab_env, client):
        client.post(
            "/api/collab/register", json={"agent_id": "ws-a", "name": "WA"}, headers=_auth(1)
        )
        client.post(
            "/api/collab/register", json={"agent_id": "ws-b", "name": "WB"}, headers=_auth(2)
        )
        db = collab_env.DB_PATH

        with client.websocket_connect("/api/collab/ws/agent/ws-a") as ws:
            # 同租户/未注册目标：正常落库（发方 agent 行租户=tenant-a）
            ws.send_json(
                {"event": "message", "to_agent": "ghost-t", "type": "query", "payload": {}}
            )
            # 跨租户投递：目标 ws-b 属 tenant-b → error 回执且不落库
            ws.send_json({"event": "message", "to_agent": "ws-b", "type": "query", "payload": {}})
            resp = ws.receive_json()
            assert resp["event"] == "error" and "cross-tenant" in resp["detail"]

        rows = _wait_rows(db, "SELECT tenant_id FROM agent_messages WHERE to_agent = 'ghost-t'")
        assert rows and rows[0]["tenant_id"] == "tenant-a"
        assert _rows(db, "SELECT id FROM agent_messages WHERE to_agent = 'ws-b'") == []

    def test_ws_unregistered_agent_default(self, collab_env, client):
        db = collab_env.DB_PATH
        with client.websocket_connect("/api/collab/ws/agent/ghost-ws") as ws:
            ws.send_json(
                {"event": "message", "to_agent": "ghost-t2", "type": "query", "payload": {}}
            )
        rows = _wait_rows(db, "SELECT tenant_id FROM agent_messages WHERE to_agent = 'ghost-t2'")
        assert rows and rows[0]["tenant_id"] == "default"  # 未注册发方→'default' 兜底
