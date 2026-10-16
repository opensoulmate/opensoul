"""G1-⑤ MultiAgentCoordinator 注册接线测试。

修前（G0 审计⑤）：MultiAgentCoordinator 协调面有 API 零注册方——agent_registry
恒空、/api/brain/agents 恒 []，register_agent/heartbeat 两方法全仓零生产调用点
（wired-but-not-functional）。本文件验证真实注册面→协调面接线：

①注册桥：/api/collab/register 落协调注册表（行归属租户=身份派生单一源、
  agent_type 映射），/api/brain/agents 从恒空变真活清单（真消费闭环）
②WS 生命周期桥：连接=活跃、协议心跳事件真消费、断开标 offline
③看门狗节拍保活（连接=活跃代理进 5min 活跃窗）
④旁路纪律：桥写失败绝不吃掉注册/连接主路径（可见 logger.warning 禁静默）
⑤不变面：注册响应契约逐键不变、未注册 agent WS 绝不 fabricate 协调行
  （heartbeat UPDATE-only）、匿名='default' 契约不变。

全部 tmp 独立库（AGENT_COLLAB_DB/USERS_DB/ENTERPRISE_DB/settings.database_url
换 tmp SQLitePool），测试行绝不写生产 data/*.db。
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings

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


def _coord_rows(db, agent_id):
    try:
        return _rows(
            db,
            "SELECT tenant_id, agent_id, agent_type, status, current_task, current_files "
            "FROM agent_registry WHERE agent_id = ?",
            (agent_id,),
        )
    except sqlite3.OperationalError:
        return []  # 协调表未建=协调面无行（桥失败用例的如实空态）


def _wait_coord(db, agent_id, pred, timeout=5.0):
    """WS 写路径是门户线程异步处理：短轮询等协调行到位（超时如实返回断言必炸）。"""
    deadline = time.time() + timeout
    rows = _coord_rows(db, agent_id)
    while time.time() < deadline:
        if rows and pred(rows[0]):
            return rows[0]
        time.sleep(0.05)
        rows = _coord_rows(db, agent_id)
    return rows[0] if rows else None


@pytest.fixture()
def coord_env(tmp_path, monkeypatch):
    """隔离数据面：tmp agent_collaboration.db / enterprise.db / opensoul.db（协调
    注册表真源）+ tmp SQLitePool 换 src.database.postgres.db_pool（_coord call-time
    import 真消费）。uid 1=tenant-a、uid 2=tenant-b、uid 3=default。"""
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'opensoul.db'}")

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
    collab._init_db()

    import src.database.postgres as pg_mod
    from src.database.postgres import SQLitePool

    tmp_pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", tmp_pool)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()  # tmp opensoul.db startup 时连接（真实 SQLitePool）
        yield
        await tmp_pool.disconnect()

    from src.api.brain import router as brain_router

    app = FastAPI(lifespan=_lifespan)
    app.include_router(collab.router, prefix="/api/collab")
    app.include_router(brain_router, prefix="/api/brain")
    with TestClient(app) as c:
        yield {
            "client": c,
            "collab": collab,
            "coord_db": tmp_path / "opensoul.db",
            "collab_db": tmp_path / "agent_collaboration.db",
        }


# ── ① 注册桥 + /api/brain/agents 真消费闭环 ────────────────────────────


class TestRegisterBridge:
    def test_register_lands_coord_row_and_brain_agents(self, coord_env):
        client = coord_env["client"]
        r = client.post(
            "/api/collab/register",
            json={"agent_id": "g15-a", "name": "A", "role": "researcher", "capabilities": ["read"]},
        )
        assert r.status_code == 200
        rows = _coord_rows(coord_env["coord_db"], "g15-a")
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"  # 匿名='default' 契约不变
        assert rows[0]["agent_type"] == "researcher"  # role 优先映射
        assert rows[0]["status"] == "idle"  # register_agent 语义原样
        # /api/brain/agents 从恒空变真活清单（register_agent 方法首个生产消费点）
        r = client.get("/api/brain/agents")
        assert r.status_code == 200
        agents = r.json()["agents"]
        assert [a["agent_id"] for a in agents] == ["g15-a"]
        assert agents[0]["agent_type"] == "researcher"

    def test_register_tenant_derived_not_self_reported(self, coord_env):
        client = coord_env["client"]
        r = client.post(
            "/api/collab/register",
            json={"agent_id": "g15-t", "name": "T", "role": "worker"},
            headers=_auth(1),
        )
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "tenant-a"
        rows = _coord_rows(coord_env["coord_db"], "g15-t")
        assert rows and rows[0]["tenant_id"] == "tenant-a"  # 协调行键=身份派生
        # /api/brain/agents 租户视图互不可见
        a_view = client.get("/api/brain/agents", headers=_auth(1)).json()["agents"]
        b_view = client.get("/api/brain/agents", headers=_auth(2)).json()["agents"]
        assert [a["agent_id"] for a in a_view] == ["g15-t"]
        assert b_view == []

    def test_register_contract_keys_unchanged(self, coord_env):
        client = coord_env["client"]
        r = client.post(
            "/api/collab/register", json={"agent_id": "g15-k", "name": "K", "model": "m1"}
        )
        assert r.status_code == 200
        # 响应契约逐键不变（无加性键）
        assert set(r.json().keys()) == {"agent_id", "token", "status", "tenant_id"}
        # agent_type 映射回落链：role 空→model，全空→openmate
        rows = _coord_rows(coord_env["coord_db"], "g15-k")
        assert rows and rows[0]["agent_type"] == "m1"
        r = client.post("/api/collab/register", json={"agent_id": "g15-k2", "name": "K2"})
        assert r.status_code == 200
        rows = _coord_rows(coord_env["coord_db"], "g15-k2")
        assert rows and rows[0]["agent_type"] == "openmate"

    def test_reregister_updates_coord_row_not_duplicates(self, coord_env):
        client = coord_env["client"]
        for role in ("one", "two"):
            r = client.post(
                "/api/collab/register", json={"agent_id": "g15-r", "name": "R", "role": role}
            )
            assert r.status_code == 200
        rows = _coord_rows(coord_env["coord_db"], "g15-r")
        assert len(rows) == 1  # ON CONFLICT upsert 不重复
        assert rows[0]["agent_type"] == "two"


# ── ② WS 生命周期桥 ───────────────────────────────────────────────────


class TestWsLifecycleBridge:
    def test_ws_connect_heartbeat_event_and_disconnect(self, coord_env):
        client = coord_env["client"]
        reg = client.post(
            "/api/collab/register", json={"agent_id": "g15-w", "name": "W", "role": "runner"}
        ).json()
        token = reg["token"]
        with client.websocket_connect(f"/api/collab/ws/agent/g15-w?token={token}") as ws:
            # 连接=活跃代理（心跳桥落行）
            row = _wait_coord(coord_env["coord_db"], "g15-w", lambda r: r["status"] == "active")
            assert row is not None and row["status"] == "active"
            assert row["tenant_id"] == "default"
            # 协议心跳事件真消费：last_heartbeat 前移（time.time() 浮点真值对比）
            before = _rows(
                coord_env["coord_db"],
                "SELECT last_heartbeat FROM agent_registry WHERE agent_id = 'g15-w'",
            )[0]["last_heartbeat"]
            time.sleep(0.02)
            ws.send_json({"event": "heartbeat"})
            deadline = time.time() + 5.0
            after = before
            while after <= before and time.time() < deadline:
                time.sleep(0.05)
                after = _rows(
                    coord_env["coord_db"],
                    "SELECT last_heartbeat FROM agent_registry WHERE agent_id = 'g15-w'",
                )[0]["last_heartbeat"]
            assert after > before, "协议心跳事件未刷协调面 last_heartbeat"
        # 断开标 offline（如实反映最近见过但已离线）
        row = _wait_coord(coord_env["coord_db"], "g15-w", lambda r: r["status"] == "offline")
        assert row is not None and row["status"] == "offline"

    def test_watchdog_tick_keeps_alive(self, coord_env, monkeypatch):
        client = coord_env["client"]
        collab = coord_env["collab"]
        reg = client.post(
            "/api/collab/register", json={"agent_id": "g15-hb", "name": "HB", "role": "runner"}
        ).json()
        calls = []
        real = collab._coord_heartbeat

        async def spy(tenant_id, agent_id, status="active"):
            calls.append((tenant_id, agent_id, status))
            await real(tenant_id, agent_id, status=status)

        monkeypatch.setattr(collab, "_coord_heartbeat", spy)
        monkeypatch.setattr(collab, "TOKEN_REVALIDATE_INTERVAL_S", 0.05)
        with client.websocket_connect(f"/api/collab/ws/agent/g15-hb?token={reg['token']}") as ws:
            ws.send_json({"event": "ping"})
            assert ws.receive_json() == {"event": "pong"}
            deadline = time.time() + 5.0
            while time.time() < deadline:
                ticks = [c for c in calls if c[1] == "g15-hb" and c[2] == "active"]
                if len(ticks) >= 2:  # 连接落行 + 至少 1 个看门狗节拍
                    break
                time.sleep(0.05)
        ticks = [c for c in calls if c[1] == "g15-hb" and c[2] == "active"]
        assert len(ticks) >= 2, calls
        assert all(c[0] == "default" for c in ticks)  # 租户键=行归属单一源

    def test_unregistered_ws_never_fabricates_coord_row(self, coord_env):
        client = coord_env["client"]
        with client.websocket_connect("/api/collab/ws/agent/g15-ghost") as ws:
            ws.send_json({"event": "heartbeat"})
            ws.send_json({"event": "ping"})
            assert ws.receive_json() == {"event": "pong"}
            time.sleep(0.1)
        time.sleep(0.1)
        # heartbeat UPDATE-only：未注册 agent_id 绝不 fabricate 协调行
        assert _coord_rows(coord_env["coord_db"], "g15-ghost") == []


# ── ④ 旁路纪律：桥写失败不吃掉主路径 ──────────────────────────────────


class TestBridgeFaultIsolation:
    def test_register_survives_coord_failure(self, coord_env, monkeypatch):
        import src.database.postgres as pg_mod

        class BrokenPool:
            async def execute(self, *a, **k):
                raise RuntimeError("coord bridge probe failure")

            async def fetch(self, *a, **k):
                raise RuntimeError("coord bridge probe failure")

        monkeypatch.setattr(pg_mod, "db_pool", BrokenPool())
        client = coord_env["client"]
        r = client.post(
            "/api/collab/register", json={"agent_id": "g15-f", "name": "F", "role": "x"}
        )
        # 注册主路径照常成功（旁路纪律：协调面是派生观测面）
        assert r.status_code == 200
        assert r.json()["status"] == "online"
        conn = sqlite3.connect(str(coord_env["collab_db"]))
        try:
            n = conn.execute("SELECT COUNT(*) FROM agents WHERE agent_id = 'g15-f'").fetchone()[0]
        finally:
            conn.close()
        assert n == 1  # collab 真源行照落
        assert _coord_rows(coord_env["coord_db"], "g15-f") == []  # 桥失败无假行

    def test_ws_connect_survives_coord_failure(self, coord_env, monkeypatch):
        import src.database.postgres as pg_mod

        class BrokenPool:
            async def execute(self, *a, **k):
                raise RuntimeError("coord bridge probe failure")

        monkeypatch.setattr(pg_mod, "db_pool", BrokenPool())
        client = coord_env["client"]
        reg = client.post(
            "/api/collab/register", json={"agent_id": "g15-fw", "name": "FW", "role": "x"}
        ).json()
        with client.websocket_connect(f"/api/collab/ws/agent/g15-fw?token={reg['token']}") as ws:
            ws.send_json({"event": "ping"})
            assert ws.receive_json() == {"event": "pong"}  # 连接主路径不受桥故障影响


# ── ⑤ 静态接线普查（写了≠接线了）──────────────────────────────────────


class TestStaticWiring:
    def test_bridge_call_points(self, coord_env):
        src = open("src/api/agent_collaboration.py").read()
        # 注册桥 1 个调用点（register handler）
        assert src.count("await _coord_register(") == 1
        # 异步心跳桥 3 个调用点：WS 连接 / 协议心跳事件 / 看门狗节拍
        assert src.count("await _coord_heartbeat(") == 3
        # 断开 offline=同步收尾 1 个调用点（DEC-⑱ 取消免疫，teardown 禁 await）
        assert src.count("heartbeat_sync(") == 1
        # 真实方法消费点（修前 register_agent/heartbeat 零生产调用）
        coord_src = open("src/cortex/multi_agent_coord.py").read()
        assert "async def register_agent(" in coord_src
        assert "async def heartbeat(" in coord_src
        assert "register_agent(agent_id, agent_type)" in src  # 真方法调用非旁路写库
        assert "heartbeat(agent_id, status=status)" in src
        # SQL 单一源（async 与同步收尾共用禁手抄漂移）
        assert coord_src.count("UPDATE agent_registry") == 1
