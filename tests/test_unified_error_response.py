"""C2统一错误响应（不谎报ok）审计收口测试。

覆盖：
① 6个stats端点异常时必须status="error"+error原因，禁假ok假0（对照组：假ok冒充"没数据"）
② /api/knowledge/stats 的 PG语法根因修复（原EXTRACT(EPOCH...)在SQLite必炸被假ok吞掉，
   live实测306条谎报0）——修复后recent_24h真值
③ /register 的 SQLite绑定修复（capabilities/metadata json.dumps、UUID str()）——
   原list/dict直接绑参写路径必炸，且RETURNING键名与响应模型错位
④ /nodes 的 TEXT last_heartbeat算术修复——有心跳行时原实现TypeError 500
⑤ /report 的 dict绑定修复
⑥ 静默零值（system_overview/sessions_api）失败带error字段
测试面=真实router代码路径+tmp隔离库（test_schema_drift_generalization同款纪律）。
"""

import asyncio
import json
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


USERS_DDL = """
CREATE TABLE users (
    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
    email TEXT DEFAULT '', password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')))
"""

AGENTS_DDL = """
CREATE TABLE agents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',
    type TEXT DEFAULT 'generic', status TEXT DEFAULT 'offline',
    capabilities TEXT DEFAULT '[]', config TEXT DEFAULT '{}',
    logo_url TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    agent_type TEXT DEFAULT '', metadata TEXT DEFAULT '{}',
    token TEXT DEFAULT NULL, last_heartbeat TEXT DEFAULT NULL)
"""

KNOWLEDGE_DDL = """
CREATE TABLE knowledge (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    title TEXT NOT NULL, content TEXT DEFAULT '',
    tags TEXT DEFAULT '[]', source TEXT DEFAULT '',
    content_type TEXT DEFAULT '', metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')))
"""

AGENT_REPORTS_DDL = """
CREATE TABLE agent_reports (
    id TEXT PRIMARY KEY, agent_id TEXT NOT NULL,
    report_type TEXT DEFAULT '', content TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')))
"""

# 生产正典形状（migrations/001_init.sql）：type列，不是entity_type
ENTITIES_DDL = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
    type TEXT DEFAULT '', description TEXT DEFAULT '',
    properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')))
"""

RELATIONS_DDL = """
CREATE TABLE relations (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
    type TEXT NOT NULL, properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')))
"""


class _BrokenDB:
    """DB故障注入：任何查询都炸，验证降级回包不谎报ok。"""

    async def fetch(self, query, *args):
        raise RuntimeError("db down (injected)")

    async def fetchrow(self, query, *args):
        raise RuntimeError("db down (injected)")

    async def fetchval(self, query, *args):
        raise RuntimeError("db down (injected)")

    async def execute(self, query, *args):
        raise RuntimeError("db down (injected)")


def _build_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as c:
        c.execute(USERS_DDL)
        c.execute(AGENTS_DDL)
        c.execute(KNOWLEDGE_DDL)
        c.execute(AGENT_REPORTS_DDL)
        c.execute(ENTITIES_DDL)
        c.execute(RELATIONS_DDL)
        c.commit()


def _seed_user(db_path: Path) -> str:
    uid = str(uuid4())
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, 'admin')",
            (uid, f"admin-{uid[:8]}", "x"),
        )
        c.commit()
    return uid


def _admin_headers(user_id: str) -> dict:
    from src.services.auth import create_access_token

    token = create_access_token(user_id=UUID(user_id), role="admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    """真实agent/knowledge/entity/graph/workflow router + tmp隔离库（真SQLitePool）。"""
    import src.api.agent as agent_api
    import src.api.entity as entity_api
    import src.api.graph as graph_api
    import src.api.knowledge as knowledge_api
    import src.api.workflow as workflow_api
    import src.middleware.auth as mw_auth
    from src.config import settings
    from src.database.postgres import SQLitePool

    db = tmp_path / "opensoul.db"
    _build_db(db)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db}")
    pool = SQLitePool()

    for mod in (agent_api, entity_api, graph_api, knowledge_api, workflow_api, mw_auth):
        monkeypatch.setattr(mod, "db_pool", pool)

    app = FastAPI()
    app.include_router(agent_api.router, prefix="/api/agent")
    app.include_router(knowledge_api.router, prefix="/api/knowledge")
    app.include_router(entity_api.router, prefix="/api/entity")
    app.include_router(graph_api.router, prefix="/api/graph")
    app.include_router(workflow_api.router, prefix="/api/workflow")

    @asynccontextmanager
    async def _lifespan(_app):
        await pool.connect()
        yield
        await pool.disconnect()

    app.router.lifespan_context = _lifespan
    app.state.db_path = str(db)
    with TestClient(app) as c:
        yield c


# ── ① stats假ok对照：DB炸了必须说error，禁status:ok假0 ───────────────

STATS_CASES = [
    ("/api/agent/stats", "AgentSystem", "total_agents"),
    ("/api/knowledge/stats", "OpenKnowledge", "total_entries"),
    ("/api/entity/stats", "EntitySystem", "total_entities"),
    ("/api/graph/stats", "OpenGraph", "total_entities"),
    ("/api/workflow/stats", "OpenWorkflow", "total_tasks"),
]


@pytest.mark.parametrize("path,component,zero_key", STATS_CASES)
def test_stats_error_not_fake_ok(api_client, monkeypatch, path, component, zero_key):
    """DB故障时status必须是error且带原因——假ok=谎报'没数据'（修复前形态）。"""
    import src.api.agent as agent_api
    import src.api.entity as entity_api
    import src.api.graph as graph_api
    import src.api.knowledge as knowledge_api
    import src.api.workflow as workflow_api

    broken = _BrokenDB()
    for mod in (agent_api, entity_api, graph_api, knowledge_api, workflow_api):
        monkeypatch.setattr(mod, "db_pool", broken)

    resp = api_client.get(path)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error", f"{path} 故障时谎报ok（假{zero_key}=0冒充没数据）"
    assert data["component"] == component
    assert "db down (injected)" in data["error"]
    assert zero_key in data  # 兼容字段保留，前端取数不炸


def test_search_stats_error_not_fake_ok(monkeypatch):
    """search的stats在函数内import db_pool，需patch源模块。"""
    import src.api.search as search_api
    import src.database.postgres as pg

    monkeypatch.setattr(pg, "db_pool", _BrokenDB())

    @asynccontextmanager
    async def _noop(_app):
        yield

    app = FastAPI()
    app.include_router(search_api.router, prefix="/api/search")
    app.router.lifespan_context = _noop
    with TestClient(app) as c:
        resp = c.get("/api/search/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"
    assert "db down (injected)" in data["error"]


def test_stats_happy_path_still_ok(api_client):
    """回归：真库正常时照旧status=ok+真值（降级改造不破坏健康路径）。"""
    db = api_client.app.state.db_path
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO knowledge (id, user_id, title) VALUES ('k1', 'u1', 't1')")
        c.commit()
    resp = api_client.get("/api/knowledge/stats")
    data = resp.json()
    assert data["status"] == "ok"
    assert data["total_entries"] == 1
    assert "error" not in data


def test_entity_graph_stats_by_type_real_value(api_client):
    """entities正典列是type（migrations/001）：原SQL误写entity_type必炸被假ok吞掉。"""
    db = api_client.app.state.db_path
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO entities (id, user_id, name, type) VALUES ('e1','u1','n1','person')")
        c.execute("INSERT INTO entities (id, user_id, name, type) VALUES ('e2','u1','n2','person')")
        c.execute("INSERT INTO entities (id, user_id, name, type) VALUES ('e3','u1','n3','place')")
        c.execute(
            "INSERT INTO relations (id, source_id, target_id, type) VALUES ('r1','e1','e2','knows')"
        )
        c.commit()
    for path in ("/api/entity/stats", "/api/graph/stats"):
        resp = api_client.get(path)
        data = resp.json()
        assert data["status"] == "ok", f"{path} {data}"
        assert data["by_type"] == {"person": 2, "place": 1}, f"{path} by_type真值"
    assert api_client.get("/api/graph/stats").json()["total_relations"] == 1


# ── ② knowledge stats根因：PG语法→SQLite兼容，recent_24h真值 ──────────


def test_knowledge_recent_24h_real_value(api_client):
    """原EXTRACT(EPOCH FROM ...)在SQLite是语法错误，整查询炸了被假ok吞成0。"""
    db = api_client.app.state.db_path
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    old = "2020-01-01 00:00:00"
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO knowledge (id, user_id, title, created_at) VALUES ('k-new', 'u1', 'n', ?)",
            (now,),
        )
        c.execute(
            "INSERT INTO knowledge (id, user_id, title, created_at) VALUES ('k-old', 'u1', 'o', ?)",
            (old,),
        )
        c.commit()
    resp = api_client.get("/api/knowledge/stats")
    data = resp.json()
    assert data["status"] == "ok"
    assert data["total_entries"] == 2
    assert data["recent_24h"] == 1, "24h窗口必须真过滤（修复前PG语法炸了谎报0）"


# ── ③ /register SQLite绑定修复：list/dict必须json.dumps ────────────────


def test_register_json_bind_roundtrip(api_client):
    """capabilities/metadata真写真读回（原list/dict直接绑参写路径必炸）。"""
    db = api_client.app.state.db_path
    uid = _seed_user(db)
    resp = api_client.post(
        "/api/agent/register",
        json={
            "name": "bind-test",
            "agent_type": "collector",
            "capabilities": ["file", "network"],
            "metadata": {"region": "cn-east", "nested": {"k": 1}},
        },
        headers=_admin_headers(uid),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "bind-test"
    assert data["agent_id"] and data["token"]  # RETURNING键名对齐响应模型
    with sqlite3.connect(db) as c:
        row = c.execute(
            "SELECT capabilities, metadata, id FROM agents WHERE name = 'bind-test'"
        ).fetchone()
    assert json.loads(row[0]) == ["file", "network"]
    assert json.loads(row[1]) == {"region": "cn-east", "nested": {"k": 1}}
    assert row[2] == data["agent_id"]


def test_report_json_bind_roundtrip(api_client):
    """report的dict数据必须json.dumps落库（原dict直接绑参写路径必炸）。"""
    db = api_client.app.state.db_path
    _seed_user(db)
    agent_id = str(uuid4())
    token = "tok-report"
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO agents (id, name, agent_type, token) VALUES (?, 'rep', 'generic', ?)",
            (agent_id, token),
        )
        c.commit()
    resp = api_client.post(
        "/api/agent/report",
        json={"agent_id": agent_id, "report_type": "metrics", "data": {"cpu": 0.5}},
        headers={"X-Agent-Token": token},
    )
    assert resp.status_code == 200, resp.text
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(agent_reports)")}
        assert "data" in cols, "缺列自愈未生效（agent_reports.data）"
        row = c.execute("SELECT data FROM agent_reports WHERE agent_id = ?", (agent_id,)).fetchone()
    assert json.loads(row[0]) == {"cpu": 0.5}


# ── ④ /nodes TEXT last_heartbeat算术修复 ───────────────────────────────


def test_nodes_text_last_heartbeat_no_500(api_client):
    """有心跳行时原实现对TEXT做datetime算术→TypeError 500。"""
    db = api_client.app.state.db_path
    uid = _seed_user(db)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO agents (id, name, agent_type, last_heartbeat) "
            "VALUES ('a-live', 'live', 'generic', ?)",
            (now,),
        )
        c.execute(
            "INSERT INTO agents (id, name, agent_type, last_heartbeat) "
            "VALUES ('a-stale', 'stale', 'generic', '2020-01-01 00:00:00')",
        )
        c.execute(
            "INSERT INTO agents (id, name, agent_type, last_heartbeat) "
            "VALUES ('a-null', 'null', 'generic', NULL)",
        )
        c.commit()
    resp = api_client.get("/api/agent/nodes", headers=_admin_headers(uid))
    assert resp.status_code == 200, resp.text
    by_name = {r["name"]: r for r in resp.json()}
    assert by_name["live"]["online"] is True
    assert by_name["stale"]["online"] is False
    assert by_name["null"]["online"] is False


# ── ⑤ 静默零值失败必须带error字段 ─────────────────────────────────────


def test_silent_zero_fallbacks_carry_error():
    """system_overview两个探测的降级回包禁静默0，必须带error。"""
    import src.api.system_overview as so

    async def _run(fn):
        return await fn()

    import unittest.mock as mock

    with mock.patch.dict(sys.modules, {"src.plugin_loader": None}):
        res = asyncio.run(_run(so._get_plugin_stats))
    # import失败路径：必须带error而非静默0
    assert res.get("total_plugins") == 0
    assert "error" in res, "插件探测失败静默回0=谎报'没插件'"
