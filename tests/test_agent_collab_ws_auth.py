"""F3b-④ /ws/agent WS agent 身份轴收口测试。

修前（控制组实证）：/ws/agent/{agent_id} 零鉴权——任意人可顶号注册 agent（改该行
status/last_seen、以其名义落消息、劫持 _ws_connections 投递），而 register 签发的
agents.token（"receive a token for subsequent communication"）全仓零消费=死凭证。

覆盖（全部 tmp 独立库 + AGENT_COLLAB_DB 环境隔离，绝不写生产 data/agent_collaboration.db）：
①注册身份开会话必须持 register 签发 token（token 首个真消费点：register 响应 token
  == agents.token 行 == WS 真放行凭证）
②顶号面收口：错误 token / 缺 token / 他人 token → 握手拒绝（WebSocketDisconnect），
  且零副作用（该行 status/last_seen 一字不动、零消息冒名落库、不进 _ws_connections）
③未注册 agent_id 既有语义零变化（允许连接、行归属 'default' 兜底——与 HTTP /message
  未注册语义同轴，②d 契约锁死）
④token 取参双源（?token= query / Authorization: Bearer 头）与重注册轮换（旧 token 失效）

注：src.api.agent_collaboration 模块级 DB_PATH 在 import 期计算——fixture 内延迟
import + monkeypatch DB_PATH + 幂等 _init_db()，无论模块此前是否已 import 都落 tmp。
"""

import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


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
    """agent_collaboration 隔离数据面：tmp agent_collaboration.db（NEST_DB 由 conftest
    autouse 隔离）。import 延迟+路径双保险，绝不碰生产库。"""
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


def _register(client, agent_id, name=None):
    r = client.post("/api/collab/register", json={"agent_id": agent_id, "name": name or agent_id})
    assert r.status_code == 200
    return r.json()["token"]


def _sentinel_row(db, agent_id):
    """把行打成哨兵值：任何顶号写（status/last_seen）都会留下可见差异（禁时序比对）。"""
    _exec(
        db,
        "UPDATE agents SET status = 'offline', last_seen = 'sentinel-ts' WHERE agent_id = ?",
        (agent_id,),
    )


def _assert_row_untouched(db, agent_id):
    rows = _rows(db, "SELECT status, last_seen FROM agents WHERE agent_id = ?", (agent_id,))
    assert rows and rows[0]["status"] == "offline" and rows[0]["last_seen"] == "sentinel-ts", rows


class TestWsAgentAuth:
    """① token 首个真消费点 + ② 顶号面收口。"""

    def test_registered_token_grants_ws_and_marks_online(self, collab_env, client):
        tok = _register(client, "a1")
        db = collab_env.DB_PATH
        # register 响应 token == agents.token 行（WS 凭证单一真源，Gate2 硬证）
        rows = _rows(db, "SELECT token FROM agents WHERE agent_id = 'a1'")
        assert rows and rows[0]["token"] == tok
        _sentinel_row(db, "a1")

        with client.websocket_connect(f"/api/collab/ws/agent/a1?token={tok}") as ws:
            ws.send_json({"event": "ping"})
            assert ws.receive_json() == {"event": "pong"}
            rows = _wait_rows(
                db,
                "SELECT status, last_seen FROM agents WHERE agent_id = 'a1' AND last_seen != 'sentinel-ts'",
            )
            assert rows and rows[0]["status"] == "online"  # 真连接=行被合法更新

    def test_wrong_token_rejected_row_untouched(self, collab_env, client):
        _register(client, "a2")
        db = collab_env.DB_PATH
        _sentinel_row(db, "a2")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/collab/ws/agent/a2?token=wrong-token"):
                pass  # pragma: no cover — 进入即拒
        _assert_row_untouched(db, "a2")
        assert "a2" not in collab_env._ws_connections

    def test_missing_token_rejected_row_untouched(self, collab_env, client):
        _register(client, "a3")
        db = collab_env.DB_PATH
        _sentinel_row(db, "a3")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/collab/ws/agent/a3"):
                pass  # pragma: no cover — 进入即拒
        _assert_row_untouched(db, "a3")
        assert "a3" not in collab_env._ws_connections

    def test_impersonation_with_other_agents_token_zero_side_effects(self, collab_env, client):
        _register(client, "victim")
        tok_attacker = _register(client, "attacker")
        db = collab_env.DB_PATH
        _sentinel_row(db, "victim")
        # 顶号攻击：持 attacker 自己的真 token 冒 victim（token 真实但身份不符）
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/collab/ws/agent/victim?token={tok_attacker}"):
                pass  # pragma: no cover — 进入即拒
        # 零副作用：行不动、零冒名消息、不占投递注册表（顶号三害全闭）
        _assert_row_untouched(db, "victim")
        assert _rows(db, "SELECT id FROM agent_messages WHERE from_agent = 'victim'") == []
        assert "victim" not in collab_env._ws_connections

    def test_token_rotation_invalidates_old_token(self, collab_env, client):
        tok1 = _register(client, "r1")
        tok2 = _register(client, "r1")  # 同租户重注册=token 轮换（②d 既有语义）
        assert tok1 != tok2
        db = collab_env.DB_PATH
        _sentinel_row(db, "r1")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/collab/ws/agent/r1?token={tok1}"):
                pass  # pragma: no cover — 旧 token 失效
        _assert_row_untouched(db, "r1")
        with client.websocket_connect(f"/api/collab/ws/agent/r1?token={tok2}") as ws:
            ws.send_json({"event": "ping"})
            assert ws.receive_json() == {"event": "pong"}


class TestWsTokenDualSource:
    """④ token 取参双源（?token= query / Authorization: Bearer 头）。"""

    def test_header_source_grants_ws(self, collab_env, client):
        tok = _register(client, "h1")
        with client.websocket_connect(
            "/api/collab/ws/agent/h1", headers={"Authorization": f"Bearer {tok}"}
        ) as ws:
            ws.send_json({"event": "ping"})
            assert ws.receive_json() == {"event": "pong"}

    def test_header_source_wrong_token_rejected(self, collab_env, client):
        _register(client, "h2")
        db = collab_env.DB_PATH
        _sentinel_row(db, "h2")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/collab/ws/agent/h2", headers={"Authorization": "Bearer wrong"}
            ):
                pass  # pragma: no cover — 进入即拒
        _assert_row_untouched(db, "h2")


class TestUnregisteredContract:
    """③ 未注册 agent_id 既有语义零变化（②d '未注册发方 default 兜底' 契约锁死）。"""

    def test_unregistered_agent_connects_without_token(self, collab_env, client):
        db = collab_env.DB_PATH
        with client.websocket_connect("/api/collab/ws/agent/ghost-ws-auth") as ws:
            ws.send_json({"event": "ping"})
            assert ws.receive_json() == {"event": "pong"}
            ws.send_json(
                {"event": "message", "to_agent": "ghost-t-auth", "type": "query", "payload": {}}
            )
        rows = _wait_rows(
            db, "SELECT tenant_id FROM agent_messages WHERE to_agent = 'ghost-t-auth'"
        )
        assert rows and rows[0]["tenant_id"] == "default"  # 未注册发方→'default' 兜底不变

    def test_unregistered_rowless_claim_leaves_no_row(self, collab_env, client):
        # 未注册身份无行可顶：连接不 fabricate 行（修前修后一致）
        db = collab_env.DB_PATH
        with client.websocket_connect("/api/collab/ws/agent/ghost-rowless") as ws:
            ws.send_json({"event": "ping"})
            assert ws.receive_json() == {"event": "pong"}
        assert _rows(db, "SELECT agent_id FROM agents WHERE agent_id = 'ghost-rowless'") == []
