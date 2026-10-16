"""G1-⑤c agent.py 管理面注册接线测试。

修前（G1-⑤ 诚实边界④留档）：agent.py 管理面注册（admin agent 节点，
opensoul.db agents 表）与 MultiAgentCoordinator 协调注册表零接线——admin
注册的节点不进 /api/brain/agents 协作视图、不参与冲突检查/协调收件箱。
本文件验证管理面生命周期→协调面接线：

①注册桥：/api/agent/register 落协调注册表（agent_type 直通、+active 同步
  两注册表状态一致），/api/brain/agents 真消费闭环
②心跳桥：/api/agent/heartbeat 保活协调活跃窗（UPDATE-only 禁 fabricate）
③注销桥：DELETE /api/agent/nodes/{id} 派生协调行随真源删除（禁幽灵行）
④旁路纪律：桥写失败绝不吃掉管理面主路径（注册/心跳/删除照常成功）
⑤不变面：注册/心跳/删除响应契约逐键不变、404 契约不变。

全部 tmp 独立库（settings.database_url 换 tmp SQLitePool + USERS_DB 归 tmp），
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
    created_at TEXT DEFAULT (datetime('now')))"""

AGENTS_DDL = """CREATE TABLE agents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
    type TEXT DEFAULT 'generic', status TEXT DEFAULT 'offline',
    capabilities TEXT DEFAULT '[]', config TEXT DEFAULT '{}',
    logo_url TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    agent_type TEXT DEFAULT '', metadata TEXT DEFAULT '{}',
    token TEXT DEFAULT NULL, last_heartbeat TEXT DEFAULT NULL)"""


def _admin_headers(user_id: str) -> dict:
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
        return _rows(
            db,
            "SELECT tenant_id, agent_id, agent_type, status, current_task, current_files, last_heartbeat "
            "FROM agent_registry WHERE agent_id = ?",
            (agent_id,),
        )
    except sqlite3.OperationalError:
        return []  # 协调表未建=协调面无行（桥失败用例的如实空态）


@pytest.fixture()
def mgmt_env(tmp_path, monkeypatch):
    """隔离数据面：tmp opensoul.db（agents 真源 + agent_registry 协调真源）。
    模块级 db_pool 三处同 patch（api.agent 主路径/middleware.auth 鉴权/
    src.database.postgres 给 brain /agents 的 call-time import）。"""
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))

    db = tmp_path / "opensoul.db"
    conn = sqlite3.connect(str(db))
    conn.execute(USERS_DDL)
    conn.execute(AGENTS_DDL)
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (?, 'admin-1', 'x', 'admin')",
        (str(uuid4()),),
    )
    conn.commit()
    uid = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
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
        yield {"client": c, "db": db, "uid": uid}


def _register(client, uid, name="mgmt-node", agent_type="collector"):
    r = client.post(
        "/api/agent/register",
        json={"name": name, "agent_type": agent_type, "capabilities": ["file"]},
        headers=_admin_headers(uid),
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── ① 注册桥 + /api/brain/agents 真消费闭环 ────────────────────────────


class TestRegisterBridge:
    def test_register_lands_coord_row_and_brain_agents(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], agent_type="collector")
        agent_id = data["agent_id"]
        rows = _coord_rows(db, agent_id)
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"  # 管理面无租户轴→'default'（记档边界）
        assert rows[0]["agent_type"] == "collector"  # agent_type 直通
        assert rows[0]["status"] == "active"  # +active 同步两注册表状态一致
        # /api/brain/agents 真消费闭环（管理面节点进协作视图）
        r = client.get("/api/brain/agents")
        assert r.status_code == 200
        agents = r.json()["agents"]
        assert [a["agent_id"] for a in agents] == [agent_id]
        assert agents[0]["agent_type"] == "collector"

    def test_register_contract_keys_unchanged(self, mgmt_env):
        client = mgmt_env["client"]
        data = _register(client, mgmt_env["uid"], name="contract-node")
        # 响应契约逐键不变（无加性键）
        assert set(data.keys()) == {"agent_id", "name", "token", "registered_at"}
        # /nodes 不变面：列表行含 online 判定键
        r = client.get("/api/agent/nodes", headers=_admin_headers(mgmt_env["uid"]))
        assert r.status_code == 200
        by_name = {row["name"]: row for row in r.json()}
        assert "contract-node" in by_name
        assert "online" in by_name["contract-node"]

    def test_reregister_new_node_new_coord_row(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        a = _register(client, mgmt_env["uid"], name="n1")
        b = _register(client, mgmt_env["uid"], name="n2")
        assert a["agent_id"] != b["agent_id"]
        assert len(_coord_rows(db, a["agent_id"])) == 1
        assert len(_coord_rows(db, b["agent_id"])) == 1


# ── ② 心跳桥 ──────────────────────────────────────────────────────────


class TestHeartbeatBridge:
    def test_heartbeat_refreshes_coord_row(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="hb-node")
        agent_id = data["agent_id"]
        # 把协调行心跳拨回远古，证明是心跳桥真刷新（非注册时残留）
        conn = sqlite3.connect(str(db))
        conn.execute(
            "UPDATE agent_registry SET last_heartbeat = 1.0 WHERE agent_id = ?", (agent_id,)
        )
        conn.commit()
        conn.close()
        r = client.post(
            "/api/agent/heartbeat",
            json={"agent_id": agent_id, "status": "idle"},
            headers={"X-Agent-Token": data["token"]},
        )
        assert r.status_code == 200, r.text
        rows = _coord_rows(db, agent_id)
        assert rows and rows[0]["last_heartbeat"] > 1000.0  # 真前移
        assert rows[0]["status"] == "idle"  # 状态真透传
        # 不变面：管理面行照常更新
        mrows = _rows(db, "SELECT status FROM agents WHERE id = ?", (agent_id,))
        assert mrows and mrows[0]["status"] == "idle"

    def test_heartbeat_update_only_no_fabrication(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="hb-a")
        ghost_id = str(uuid4())
        r = client.post(
            "/api/agent/heartbeat",
            json={"agent_id": ghost_id, "status": "active"},
            headers={"X-Agent-Token": data["token"]},  # A 的 token 打 B 的 id
        )
        assert r.status_code == 404  # 不变面：UPDATE 0 → 404
        assert _coord_rows(db, ghost_id) == []  # 绝不 fabricate 协调行

    def test_heartbeat_bridge_failure_bypass(self, mgmt_env, monkeypatch):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="hb-broken")
        agent_id = data["agent_id"]

        from src.cortex.multi_agent_coord import MultiAgentCoordinator

        async def _boom(self, *args, **kwargs):
            raise RuntimeError("coord down (injected)")

        monkeypatch.setattr(MultiAgentCoordinator, "heartbeat", _boom)
        r = client.post(
            "/api/agent/heartbeat",
            json={"agent_id": agent_id, "status": "active"},
            headers={"X-Agent-Token": data["token"]},
        )
        assert r.status_code == 200, r.text  # 桥炸不吃心跳主路径
        mrows = _rows(db, "SELECT last_heartbeat FROM agents WHERE id = ?", (agent_id,))
        assert mrows and mrows[0]["last_heartbeat"] is not None  # 主路径真写


# ── ③ 注销桥 ──────────────────────────────────────────────────────────


class TestDeleteBridge:
    def test_delete_unregisters_coord_row(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="del-node")
        agent_id = data["agent_id"]
        assert len(_coord_rows(db, agent_id)) == 1
        r = client.delete(f"/api/agent/nodes/{agent_id}", headers=_admin_headers(mgmt_env["uid"]))
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "deleted", "agent_id": agent_id}  # 契约逐键
        # 真源行删除 + 派生协调行随删（禁幽灵行）
        assert _rows(db, "SELECT id FROM agents WHERE id = ?", (agent_id,)) == []
        assert _coord_rows(db, agent_id) == []
        r = client.get("/api/brain/agents")
        assert r.json()["agents"] == []

    def test_delete_bridge_failure_bypass(self, mgmt_env, monkeypatch):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="del-broken")
        agent_id = data["agent_id"]

        from src.cortex.multi_agent_coord import MultiAgentCoordinator

        async def _boom(self, *args, **kwargs):
            raise RuntimeError("coord down (injected)")

        monkeypatch.setattr(MultiAgentCoordinator, "unregister_agent", _boom)
        r = client.delete(f"/api/agent/nodes/{agent_id}", headers=_admin_headers(mgmt_env["uid"]))
        assert r.status_code == 200, r.text  # 桥炸不吃删除主路径
        assert _rows(db, "SELECT id FROM agents WHERE id = ?", (agent_id,)) == []

    def test_delete_nonexistent_404_contract(self, mgmt_env):
        client = mgmt_env["client"]
        r = client.delete(f"/api/agent/nodes/{uuid4()}", headers=_admin_headers(mgmt_env["uid"]))
        assert r.status_code == 404  # 不变面


# ── ④ 静态接线普查 ─────────────────────────────────────────────────────


class TestStaticWiring:
    def test_bridge_call_sites_wired(self):
        """写了≠接线了：三桥调用点必须真在管理面端点路径上（grep 同款普查）。"""
        agent_src = (Path(__file__).parent.parent / "src" / "api" / "agent.py").read_text()
        coord_src = (
            Path(__file__).parent.parent / "src" / "cortex" / "multi_agent_coord.py"
        ).read_text()
        # 注册桥（register 端点）+ 心跳桥（register/heartbeat 双调用点）
        assert "_coord_register(str(agent_id), req.agent_type)" in agent_src
        assert '_coord_heartbeat(str(agent_id), status="active")' in agent_src
        assert "_coord_heartbeat(str(req.agent_id), status=req.status)" in agent_src
        # 注销桥（delete 端点）
        assert "_coord_unregister(str(node_id))" in agent_src
        # unregister_agent 方法真定义（注销桥消费面）
        assert "async def unregister_agent(self, agent_id: str)" in coord_src
