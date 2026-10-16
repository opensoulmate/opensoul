"""G1-⑤c-④ 心跳桥工作集声明测试。

修前（G1-⑤c 留档「心跳桥不带工作集」）：管理面 /api/agent/heartbeat 桥只透传
status，current_task/current_files 无从声明——管理面 agent 的工作集进不了
agent_registry 协调真源，check_file_conflict 冲突视图看不见它们（G1-⑤b 的
声明面只在 collab WS 协议事件上）。本文件验证管理面声明语义（G1-⑤b 同款
单一语义）：

①声明透传：显式 current_task/current_files → 协调行更新，/api/brain/agents
  协作视图真消费（工作集可见）+ check_file_conflict 冲突视图真消费
②COALESCE 保留：缺省 None=保留现值（keep-alive 心跳不洗已声明工作集）；
  显式空值=清空；逐列独立
③offline 清空：status='offline' 且未显式声明=清空工作集（WS 断开收尾
  heartbeat_sync ''/'[]' 同语义）；显式声明恒优先（禁静默吞声明）
④非法载荷：pydantic 422 先拒零副作用（禁静默吞声明），心跳主路径不写
⑤既有纪律零回归：UPDATE-only 禁 fabricate、claim 持证、旁路纪律、响应契约。

全部 tmp 独立库（settings.database_url 换 tmp SQLitePool + USERS_DB 归 tmp），
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
        return []  # 协调表未建=协调面无行（如实空态）


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
        yield {"client": c, "db": db, "uid": uid, "pool": pool}


def _register(client, uid, name="ws-node", agent_type="collector"):
    r = client.post(
        "/api/agent/register",
        json={"name": name, "agent_type": agent_type, "capabilities": ["file"]},
        headers=_admin_headers(uid),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _beat(client, data, payload):
    return client.post(
        "/api/agent/heartbeat",
        json={"agent_id": data["agent_id"], **payload},
        headers={"X-Agent-Token": data["token"]},
    )


# ── ① 声明透传 + 协作视图/冲突视图真消费 ──────────────────────────────


class TestDeclarationThrough:
    def test_declaration_lands_coord_row_and_brain_agents(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="decl-node")
        agent_id = data["agent_id"]
        r = _beat(
            client,
            data,
            {
                "status": "active",
                "current_task": "refactor module X",
                "current_files": ["src/a.py", "src/b.py"],
            },
        )
        assert r.status_code == 200, r.text
        rows = _coord_rows(db, agent_id)
        assert rows and rows[0]["current_task"] == "refactor module X"
        assert rows[0]["current_files"] == '["src/a.py", "src/b.py"]'  # json.dumps 落行
        # /api/brain/agents 真消费闭环（管理面声明进协作视图）
        rr = client.get("/api/brain/agents")
        agents = rr.json()["agents"]
        assert [a["agent_id"] for a in agents] == [agent_id]
        assert agents[0]["current_task"] == "refactor module X"
        assert agents[0]["current_files"] == '["src/a.py", "src/b.py"]'

    def test_declared_set_feeds_conflict_view(self, mgmt_env):
        """冲突视图真消费：管理面声明的工作集进 check_file_conflict。"""
        client, pool = mgmt_env["client"], mgmt_env["pool"]
        a = _register(client, mgmt_env["uid"], name="worker-a")
        b = _register(client, mgmt_env["uid"], name="worker-b")
        assert (
            _beat(
                client,
                a,
                {"current_task": "task-a", "current_files": ["src/shared.py", "src/only_a.py"]},
            ).status_code
            == 200
        )
        assert (
            _beat(
                client,
                b,
                {"current_task": "task-b", "current_files": ["src/shared.py", "src/only_b.py"]},
            ).status_code
            == 200
        )
        import anyio

        from src.cortex.multi_agent_coord import MultiAgentCoordinator

        coord = MultiAgentCoordinator(pool, "default")
        conflicts = anyio.run(coord.check_file_conflict, a["agent_id"], ["src/shared.py"])
        assert [c["agent_id"] for c in conflicts] == [b["agent_id"]]
        assert conflicts[0]["conflicting_files"] == ["src/shared.py"]
        assert conflicts[0]["their_task"] == "task-b"
        # 无重叠零冲突（非恒真视图）
        assert anyio.run(coord.check_file_conflict, a["agent_id"], ["src/only_a.py"]) == []


# ── ② COALESCE 保留语义 ──────────────────────────────────────────────


class TestKeepSemantics:
    def test_keepalive_without_declaration_preserves(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="keep-node")
        agent_id = data["agent_id"]
        assert (
            _beat(client, data, {"current_task": "t1", "current_files": ["f1"]}).status_code == 200
        )
        # keep-alive 心跳（缺省声明）不洗工作集——G1-⑤b「keep-alive 节拍不再洗声明」
        assert _beat(client, data, {"status": "active"}).status_code == 200
        rows = _coord_rows(db, agent_id)
        assert rows[0]["current_task"] == "t1"
        assert rows[0]["current_files"] == '["f1"]'

    def test_explicit_empty_clears(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="clear-node")
        agent_id = data["agent_id"]
        assert (
            _beat(client, data, {"current_task": "t1", "current_files": ["f1"]}).status_code == 200
        )
        assert (
            _beat(client, data, {"current_task": "", "current_files": []}).status_code == 200
        )  # 显式空=清空
        rows = _coord_rows(db, agent_id)
        assert rows[0]["current_task"] == ""
        assert rows[0]["current_files"] == "[]"

    def test_partial_declaration_per_column(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="partial-node")
        agent_id = data["agent_id"]
        assert (
            _beat(client, data, {"current_task": "t1", "current_files": ["f1"]}).status_code == 200
        )
        assert _beat(client, data, {"current_files": ["f2"]}).status_code == 200
        rows = _coord_rows(db, agent_id)
        assert rows[0]["current_task"] == "t1"  # 未声明列保留
        assert rows[0]["current_files"] == '["f2"]'


# ── ③ offline 清空 ──────────────────────────────────────────────────


class TestOfflineClear:
    def test_offline_without_declaration_clears(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="off-node")
        agent_id = data["agent_id"]
        assert (
            _beat(client, data, {"current_task": "t1", "current_files": ["f1"]}).status_code == 200
        )
        assert _beat(client, data, {"status": "offline"}).status_code == 200
        rows = _coord_rows(db, agent_id)
        assert rows[0]["current_task"] == ""
        assert rows[0]["current_files"] == "[]"  # WS 断开收尾 heartbeat_sync 同语义

    def test_explicit_declaration_wins_over_offline(self, mgmt_env):
        """显式声明恒优先：禁静默吞声明（offline+显式=按显式更新）。"""
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="off-decl-node")
        agent_id = data["agent_id"]
        assert (
            _beat(
                client,
                data,
                {"status": "offline", "current_task": "final note", "current_files": ["f9"]},
            ).status_code
            == 200
        )
        rows = _coord_rows(db, agent_id)
        assert rows[0]["current_task"] == "final note"
        assert rows[0]["current_files"] == '["f9"]'


# ── ④ 非法载荷 422 零副作用 ─────────────────────────────────────────


class TestInvalidPayload:
    @pytest.mark.parametrize(
        "payload",
        [
            {"current_task": 123},
            {"current_task": ["not-a-string"]},
            {"current_files": "src/a.py"},
            {"current_files": [1, 2]},
            {"current_files": ["ok", 3]},
        ],
    )
    def test_invalid_declaration_422_zero_side_effect(self, mgmt_env, payload):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="bad-decl")
        agent_id = data["agent_id"]
        assert (
            _beat(
                client, data, {"current_task": "keep-me", "current_files": ["keep.py"]}
            ).status_code
            == 200
        )
        r = _beat(client, data, payload)
        assert r.status_code == 422  # 如实拒，禁静默吞声明
        rows = _coord_rows(db, agent_id)
        assert rows[0]["current_task"] == "keep-me"  # 零副作用：声明一字不动
        assert rows[0]["current_files"] == '["keep.py"]'


# ── ⑤ 既有纪律零回归 ────────────────────────────────────────────────


class TestNoRegression:
    def test_update_only_no_fabrication_with_declaration(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="nofab-node")
        agent_id = data["agent_id"]
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM agent_registry WHERE agent_id = ?", (agent_id,))
        conn.commit()
        conn.close()
        r = _beat(client, data, {"current_task": "t", "current_files": ["f"]})
        assert r.status_code == 200  # 主路径照常
        assert _coord_rows(db, agent_id) == []  # 绝不 fabricate 协调行

    def test_claim_mismatch_403_no_declaration_written(self, mgmt_env):
        client, db = mgmt_env["client"], mgmt_env["db"]
        victim = _register(client, mgmt_env["uid"], name="victim")
        attacker = _register(client, mgmt_env["uid"], name="attacker")
        r = client.post(
            "/api/agent/heartbeat",
            json={
                "agent_id": victim["agent_id"],
                "current_task": "forged",
                "current_files": ["forged.py"],
            },
            headers={"X-Agent-Token": attacker["token"]},
        )
        assert r.status_code == 403  # claim 持证零回归
        rows = _coord_rows(db, victim["agent_id"])
        assert rows[0]["current_task"] != "forged"  # 零副作用
        assert rows[0]["current_files"] != '["forged.py"]'

    def test_bridge_failure_bypass_with_declaration(self, mgmt_env, monkeypatch):
        client, db = mgmt_env["client"], mgmt_env["db"]
        data = _register(client, mgmt_env["uid"], name="broken-bridge")
        agent_id = data["agent_id"]

        from src.cortex.multi_agent_coord import MultiAgentCoordinator

        async def _boom(self, *args, **kwargs):
            raise RuntimeError("coord down (injected)")

        monkeypatch.setattr(MultiAgentCoordinator, "heartbeat", _boom)
        r = _beat(client, data, {"current_task": "t", "current_files": ["f"]})
        assert r.status_code == 200  # 桥炸不吃心跳主路径
        mrows = _rows(db, "SELECT last_heartbeat FROM agents WHERE id = ?", (agent_id,))
        assert mrows and mrows[0]["last_heartbeat"] is not None  # 主路径真写

    def test_response_contract_keys_unchanged(self, mgmt_env):
        client = mgmt_env["client"]
        data = _register(client, mgmt_env["uid"], name="contract-node")
        r = _beat(client, data, {"current_task": "t", "current_files": ["f"]})
        assert r.status_code == 200
        assert set(r.json().keys()) == {"agent_id", "status", "timestamp"}

    def test_bridge_passes_declaration_positional_contract(self, mgmt_env, monkeypatch):
        """桥真透传（单一语义）：声明值原样到 heartbeat 方法，None=保留哨兵不被洗。"""
        client = mgmt_env["client"]
        data = _register(client, mgmt_env["uid"], name="spy-node")
        seen = {}

        from src.cortex.multi_agent_coord import MultiAgentCoordinator

        async def _spy(self, agent_id, status="active", current_task=None, current_files=None):
            seen["agent_id"] = agent_id
            seen["status"] = status
            seen["current_task"] = current_task
            seen["current_files"] = current_files

        monkeypatch.setattr(MultiAgentCoordinator, "heartbeat", _spy)
        assert _beat(client, data, {"current_task": "t", "current_files": ["f"]}).status_code == 200
        assert seen["current_task"] == "t"
        assert seen["current_files"] == ["f"]
        assert _beat(client, data, {"status": "idle"}).status_code == 200
        assert seen["current_task"] is None  # 保留哨兵真透传（非空串顶替）
        assert seen["current_files"] is None
        assert _beat(client, data, {"status": "offline"}).status_code == 200
        assert seen["current_task"] == ""  # offline 清空语义真到桥
        assert seen["current_files"] == []
