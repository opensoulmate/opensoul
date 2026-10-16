"""C1 — captures/schema 漂移自愈推广到其余表（2026-10-11 全库漂移审计驱动）。

三类漂移的治理与实证：
① agents（真缺列漂移）：生产是logo_url形状（type列非agent_type、无token），
   而 api/agent.py 与 middleware/auth.py:46（require_agent鉴权）引用
   agent_type/metadata/token/last_heartbeat——缺列时注册/心跳/X-Agent-Token
   鉴权全 "no such column"（live :8090 同病）。统一缺列自愈
   （SQLitePool._ensure_schema 的 _SCHEMA_FIXES）在 connect() 时补列，
   真实鉴权/心跳路径恢复。
② trajectory_events（同名不同形表冲突）：trajectory/store.py 形状（TEXT uuid
   主键/metadata_json/created_at）vs api/trajectory_api.py 形状（自增id/
   metadata/timestamp）共享一张表，NOT NULL无默认列ALTER补不了。根因拆表：
   v2 事件落 trajectory_v2_events，store 的 trajectory_events 零改动。
③ agent_messages（同名不同形表冲突）：消息树形状（acp-proxy/ws_chat同库）vs
   cortex/multi_agent_coord 协作收件箱形状。根因拆表：协作落
   agent_coord_messages，消息树零改动（防假行被 experience_collector
   按消息树语义采集）。

测试面=真实代码路径（require_agent/heartbeat端点/trajectory-v2端点/
MultiAgentCoordinator真实方法），数据面=tmp隔离（绝不写生产 data/opensoul.db）。
开局=生产实测漂移DDL原样复刻（2026-10-11 sqlite3 data/opensoul.db .schema）。
"""

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

# ── 生产实测漂移schema原样复刻（2026-10-11 .schema实测DDL）────────────

_PROD_AGENTS_DDL = (
    "CREATE TABLE agents ("
    "id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '', "
    "type TEXT DEFAULT 'generic', status TEXT DEFAULT 'offline', "
    "capabilities TEXT DEFAULT '[]', config TEXT DEFAULT '{}', "
    "logo_url TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')), "
    "updated_at TEXT DEFAULT (datetime('now')))"
)

_PROD_AGENT_MESSAGES_DDL = (
    "CREATE TABLE agent_messages ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
    "role TEXT NOT NULL, content TEXT NOT NULL, timestamp REAL NOT NULL, "
    "attachments TEXT, parent_message_id INTEGER, metadata TEXT)"
)

_PROD_TRAJECTORY_EVENTS_DDL = (
    "CREATE TABLE trajectory_events ("
    "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, parent_event_id TEXT, "
    "event_type TEXT NOT NULL, agent_id TEXT NOT NULL DEFAULT '', "
    "content TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}', "
    "token_usage INTEGER NOT NULL DEFAULT 0, duration_ms REAL NOT NULL DEFAULT 0, "
    "status TEXT NOT NULL DEFAULT 'ok', created_at TEXT NOT NULL)"
)

_PROD_KNOWLEDGE_CHUNKS_DDL = (
    "CREATE TABLE knowledge_chunks ("
    "id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL, content TEXT NOT NULL, "
    "chunk_index INTEGER DEFAULT 0)"
)

_SEED_TREE_ROW = ("sess-tree-1", "user", "树消息", 1728600000.0)
_SEED_TRAJ_ROW = ("traj-uuid-1", "sess-traj-1", "step", "store事件", "2026-10-11 00:00:00")


def _build_drift_db(db_path) -> None:
    """tmp库开局=生产漂移形状（agents缺列 + 两张同名不同形共享表带种子行）。"""
    with sqlite3.connect(db_path) as c:
        c.execute(_PROD_AGENTS_DDL)
        c.execute(_PROD_AGENT_MESSAGES_DDL)
        c.execute(_PROD_TRAJECTORY_EVENTS_DDL)
        c.execute(_PROD_KNOWLEDGE_CHUNKS_DDL)
        c.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            _SEED_TREE_ROW,
        )
        c.execute(
            "INSERT INTO trajectory_events "
            "(id, session_id, event_type, content, created_at) VALUES (?, ?, ?, ?, ?)",
            _SEED_TRAJ_ROW,
        )
        c.commit()


def _columns(db_path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as c:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _connected_pool(db_path, monkeypatch):
    from src.config import settings
    from src.database.postgres import SQLitePool

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    return SQLitePool()


# ── ① 审计钉子：修复前，真实调用点查询在漂移形状上确实全挂 ────────────


def test_control_drifted_shapes_break_real_queries(tmp_path):
    db = tmp_path / "opensoul.db"
    _build_drift_db(db)
    con = sqlite3.connect(db)
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        # middleware/auth.py:46 require_agent 鉴权查询（真实SQL）
        con.execute(
            "SELECT id, name, agent_type, status FROM agents WHERE token = ?",
            ("tok",),
        ).fetchall()
    with pytest.raises(sqlite3.OperationalError, match="no (such column|column named)"):
        # api/trajectory_api.py:78 v2事件写入（真实SQL，拆表前共享trajectory_events）
        con.execute(
            "INSERT INTO trajectory_events "
            "(session_id, agent_id, event_type, content, metadata, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("s", "a", "step", "c", "{}", "ts"),
        )
    with pytest.raises(sqlite3.OperationalError, match="no (such column|column named)"):
        # cortex/multi_agent_coord.py:148 协作消息写入（真实SQL，拆表前共享agent_messages）
        con.execute(
            "INSERT INTO agent_messages "
            "(tenant_id, from_agent, to_agent, message_type, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("t", "a", "b", "task", "c", 1.0),
        )
    con.close()


# ── ① agents：缺列自愈 + 真实鉴权/心跳/统计路径恢复 ─────────────────────


@pytest.fixture()
def agent_client(tmp_path, monkeypatch):
    """真实 agent router + middleware.auth.require_agent 探针 + tmp漂移库。"""
    import src.api.agent as agent_api
    import src.middleware.auth as mw_auth
    from src.middleware.auth import require_agent

    db = tmp_path / "opensoul.db"
    _build_drift_db(db)
    tmp_pool = _connected_pool(db, monkeypatch)
    monkeypatch.setattr(agent_api, "db_pool", tmp_pool)
    monkeypatch.setattr(mw_auth, "db_pool", tmp_pool)

    app = FastAPI()

    @app.get("/probe")
    async def _probe(agent: dict = Depends(require_agent)):
        return {"agent": agent}

    app.include_router(agent_api.router, prefix="/api/agent")

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app.router.lifespan_context = _lifespan
    app.state.drift_db = str(db)
    with TestClient(app) as c:
        yield c


def test_agents_columns_healed_on_connect(agent_client):
    db = agent_client.app.state.drift_db
    cols = _columns(db, "agents")
    # 自愈补齐真实调用点引用的4列（api/agent.py + middleware/auth.py）
    assert {"agent_type", "metadata", "token", "last_heartbeat"} <= cols
    # 生产既有列零改动（拆表/改名都不做，只加列）
    assert {"id", "name", "type", "capabilities", "config", "logo_url"} <= cols


def test_agents_real_auth_heartbeat_stats_paths(agent_client):
    db = agent_client.app.state.drift_db
    agent_id = str(uuid4())
    token = uuid4().hex
    # /register 的意图写入形态（capabilities/metadata按SQLite铁律json.dumps）
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO agents (id, name, agent_type, capabilities, metadata, token, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active')",
            (
                agent_id,
                "probe-agent",
                "probe-type",
                json.dumps(["bash"]),
                json.dumps({"k": "v"}),
                token,
            ),
        )
        c.commit()

    # 真实 require_agent 鉴权（middleware/auth.py:45-48 真SQL）
    assert agent_client.get("/probe", headers={"X-Agent-Token": token}).status_code == 200
    assert agent_client.get("/probe", headers={"X-Agent-Token": "wrong"}).status_code == 401
    assert agent_client.get("/probe").status_code == 401

    # 真实心跳端点（api/agent.py:102 UPDATE last_heartbeat = NOW()）
    resp = agent_client.post(
        "/api/agent/heartbeat",
        headers={"X-Agent-Token": token},
        json={"agent_id": agent_id, "status": "active"},
    )
    assert resp.status_code == 200, resp.text

    # 真实统计端点（api/agent.py:28 GROUP BY agent_type）——修复前异常被吞成
    # total_agents=0的假ok，现在如实反映
    stats = agent_client.get("/api/agent/stats").json()
    assert stats["total_agents"] == 1
    assert stats["by_type"] == {"probe-type": 1}

    with sqlite3.connect(db) as c:
        hb = c.execute("SELECT last_heartbeat FROM agents WHERE id = ?", (agent_id,)).fetchone()[0]
    assert hb, "心跳应写入last_heartbeat"


def test_agents_heal_idempotent_reconnect(tmp_path, monkeypatch):
    db = tmp_path / "opensoul.db"
    _build_drift_db(db)
    before = _columns(db, "agents")
    mid = set()

    async def run():
        nonlocal mid
        pool = _connected_pool(db, monkeypatch)
        await pool.connect()
        mid = _columns(db, "agents")
        await pool.disconnect()
        pool2 = _connected_pool(db, monkeypatch)
        await pool2.connect()
        await pool2.disconnect()

    asyncio.run(run())
    after = _columns(db, "agents")
    assert after == mid  # 二次connect幂等：不重复补列不报错
    # G1-⑤c-② 增补 tenant_id（agents.tenant_id 行租户键，管理面租户轴收口）
    assert after - before == {
        "agent_type",
        "metadata",
        "token",
        "last_heartbeat",
        "tenant_id",
    }


def test_knowledge_chunks_token_count_regression(tmp_path, monkeypatch):
    """既有自愈项回归：knowledge_chunks.token_count 仍然补。"""
    db = tmp_path / "opensoul.db"
    _build_drift_db(db)

    async def run():
        pool = _connected_pool(db, monkeypatch)
        await pool.connect()
        await pool.disconnect()

    asyncio.run(run())
    assert "token_count" in _columns(db, "knowledge_chunks")


# ── ② trajectory-v2：拆表后自己的表全路径可用，store表零改动 ────────────


@pytest.fixture()
def traj_client(tmp_path, monkeypatch):
    import src.api.trajectory_api as traj_api

    db = tmp_path / "opensoul.db"
    _build_drift_db(db)
    tmp_pool = _connected_pool(db, monkeypatch)
    monkeypatch.setattr(traj_api, "db_pool", tmp_pool)

    app = FastAPI()
    app.include_router(traj_api.router, prefix="/api/trajectory-v2")

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app.router.lifespan_context = _lifespan
    app.state.drift_db = str(db)
    with TestClient(app) as c:
        yield c


def test_trajectory_v2_endpoints_on_own_table(traj_client):
    resp = traj_client.post(
        "/api/trajectory-v2/events",
        json={
            "session_id": "probe-sess",
            "agent_id": "probe-agent",
            "event_type": "step",
            "content": "探针事件",
            "metadata": {"k": "v"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["metadata"] == {"k": "v"}
    assert body["timestamp"]

    sessions = traj_client.get("/api/trajectory-v2/sessions").json()
    assert sessions["total"] == 1
    assert sessions["sessions"][0]["session_id"] == "probe-sess"
    assert sessions["sessions"][0]["event_count"] == 1

    detail = traj_client.get("/api/trajectory-v2/sessions/probe-sess").json()
    assert detail["count"] == 1
    assert detail["events"][0]["metadata"] == {"k": "v"}

    replay = traj_client.get("/api/trajectory-v2/sessions/probe-sess/replay").json()
    assert replay["total_steps"] == 1
    assert replay["steps"][0]["content"] == "探针事件"


def test_trajectory_store_table_untouched(traj_client):
    """共享表零改动：store形状的trajectory_events不加列、种子行原样。"""
    db = traj_client.app.state.drift_db
    assert _columns(db, "trajectory_events") == {
        "id",
        "session_id",
        "parent_event_id",
        "event_type",
        "agent_id",
        "content",
        "metadata_json",
        "token_usage",
        "duration_ms",
        "status",
        "created_at",
    }
    with sqlite3.connect(db) as c:
        rows = c.execute(
            "SELECT id, session_id, event_type, content, created_at FROM trajectory_events"
        ).fetchall()
    assert rows == [_SEED_TRAJ_ROW]


# ── ③ 协作收件箱：拆表后真实方法可用，消息树零改动 ───────────────────────


def test_multi_agent_coord_own_table_and_tree_untouched(tmp_path, monkeypatch):
    from src.cortex.multi_agent_coord import MultiAgentCoordinator

    db = tmp_path / "opensoul.db"
    _build_drift_db(db)

    async def run():
        pool = _connected_pool(db, monkeypatch)
        await pool.connect()
        coord = MultiAgentCoordinator(pool, tenant_id="t1")
        await coord.register_agent("a1", "openmate")
        await coord.send_message("a1", "b1", "task", "协作消息")
        inbox = await coord.get_messages("b1")
        stats = await coord.get_stats()
        active = await coord.get_active_agents()
        await pool.disconnect()
        return inbox, stats, active

    inbox, stats, active = asyncio.run(run())
    assert len(inbox) == 1
    assert inbox[0]["content"] == "协作消息"
    assert inbox[0]["message_type"] == "task"
    assert stats["agents"] == 1
    assert [a["agent_id"] for a in active] == ["a1"]

    # 共享消息树零改动：不加协作列、种子树消息原样
    assert _columns(db, "agent_messages") == {
        "id",
        "session_id",
        "role",
        "content",
        "timestamp",
        "attachments",
        "parent_message_id",
        "metadata",
    }
    with sqlite3.connect(db) as c:
        rows = c.execute(
            "SELECT session_id, role, content, timestamp FROM agent_messages"
        ).fetchall()
    assert rows == [_SEED_TREE_ROW]
