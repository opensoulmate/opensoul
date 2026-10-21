"""DEC-⑱-③ WS/SSE 连接审计面——link_gateway 长连接面（WS /ws + SSE /stream）
连接生命周期事件落 timeline（immune 🛡）。

闭 DEC-⑱-② 诚实边界②留档件「link_gateway/collab 长连接面审计留待拍（⑱-③/⑱-④）」：
中继通道谁被拒/谁连上/连接何时以何因结束必须可审计回放（⑱-③=link 面，
WS+SSE 两连接面同拍=1c-⑤「link_gateway token 连接期重校验」同款一面口径）。

事件面（organ=immune，安全归口与 terminal_ws_* / ws_chat_* / intrusion_detected /
permission_denied 同流）：
- link_ws_rejected / link_sse_rejected   拒绝（detail: face/client/has_token/
  cause=invalid_token；匿名缺凭据=契约放行非拒绝面；token 只字不进事件）
- link_ws_connected / link_sse_connected 连接建立（detail: face/user_id/authed/
  client·has_token，租户键=bind_identity 派生）
- link_ws_closed / link_sse_closed       连接结束（detail: reason∈{credential_revoked,
  error,client_disconnect} + duration_s）

数据面全 tmp（TIMELINE_DB/USERS_DB/ENTERPRISE_DB 环境隔离 + event_bridge._BASE
死端口），绝不写生产 timeline.db / 身份库 / 活动流。
"""

import asyncio
import json
import os
import queue
import sqlite3
import threading
import time as _time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.api.link_gateway as link_gateway_api
from src.nerve import event_bridge
from src.services.tenant_binding import DEFAULT_USER
from src.timeline.store import TimelineStore

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)


def _seed_user(db_path, active=1, tenant_id="default"):
    uid = str(uuid.uuid4())
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', ?, ?)",
            (uid, f"u-{uid[:8]}", active, tenant_id),
        )
        c.commit()
    return uid


def _token(uid):
    from src.services.auth import create_access_token

    return create_access_token(user_id=uuid.UUID(uid), role="user")


def _set_active(db, uid, active):
    with sqlite3.connect(db) as c:
        c.execute("UPDATE users SET is_active = ? WHERE id = ?", (active, uid))
        c.commit()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + tmp timeline（conftest TIMELINE_DB 隔离链）+ 真 router。

    模块态每用例 fresh（test_link_gateway_ws_relay_tenant 同款）：_events 泄漏会
    让 SSE 流读到前用例残留帧、_ws_clients 泄漏会串广播面；TestClient 用作
    上下文管理器=单 portal 多会话（两 WS 会话同 loop 才能互相收广播）。
    """
    from collections import deque

    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9", raising=False)
    users_db = tmp_path / "users.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()
    monkeypatch.setattr(link_gateway_api, "_events", deque(maxlen=link_gateway_api.MAX_EVENTS))
    monkeypatch.setattr(link_gateway_api, "_ws_clients", [])
    app = FastAPI()
    app.include_router(link_gateway_api.router, prefix="/api/link")
    with TestClient(app) as client:
        store = TimelineStore(os.environ["TIMELINE_DB"])
        yield {
            "client": client,
            "users_db": users_db,
            "store": store,
            "uid": _seed_user(users_db, 1, "tenant-audit-x"),
        }


def _rows(store, event_type=None):
    out = []
    for r in store.query(organ="immune", limit=200):
        if r.event_type.startswith("link_ws_") or r.event_type.startswith("link_sse_"):
            if event_type is None or r.event_type == event_type:
                out.append(r)
    return out


def _wait_rows(store, event_type, n, timeout=8.0):
    """有界轮询等待审计行（handler finally 里 _audit 与测试线程并发，禁裸 sleep 碰运气）。"""
    deadline = _time.time() + timeout
    rows = []
    while _time.time() < deadline:
        rows = _rows(store, event_type)
        if len(rows) >= n:
            return rows
        _time.sleep(0.05)
    raise AssertionError(
        f"audit rows {event_type} >= {n} not found in {timeout}s (got {len(rows)})"
    )


class _Conn:
    """一条 WS 连接 + 后台排空线程（test_ws_chat_audit 同款 harness）。"""

    def __init__(self, ctx, ws):
        self.ctx = ctx
        self.ws = ws
        self.q = queue.Queue()
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self):
        try:
            while True:
                self.q.put(self.ws.receive())
        except Exception:
            pass

    def frame(self, timeout=5.0):
        msg = self.q.get(timeout=timeout)
        if msg["type"] == "websocket.close":
            raise WebSocketDisconnect(code=msg.get("code", 1000), reason=msg.get("reason", ""))
        assert msg["type"] == "websocket.send", msg
        return json.loads(msg["text"])

    def expect_close(self, timeout=8.0):
        # 等到 close 帧为止（中间文本帧=残留 prompt 等噪声不判失败）：修前首帧判定
        # 形在 bash prompt 残帧先行入队时误判 DID NOT RAISE（并发 flake 实测复现，
        # 2026-10-21 flake 根因收口拍 family 同型收口）。
        deadline = _time.time() + timeout
        while True:
            remaining = deadline - _time.time()
            if remaining <= 0:
                break
            try:
                self.frame(timeout=remaining)
            except WebSocketDisconnect as ei:
                return ei
            except queue.Empty:
                break
        raise AssertionError(f"close frame not seen within {timeout}s")

    def close(self):
        try:
            self.ctx.__exit__(None, None, None)
        except Exception:
            pass


def _open(client, path):
    ctx = client.websocket_connect(path)
    ws = ctx.__enter__()
    return _Conn(ctx, ws)


def _open_expect_reject(client, path):
    """握手前拒绝（accept 前 close 1008）：TestClient 在 __enter__ 即抛断连。"""
    with pytest.raises(WebSocketDisconnect) as ei:
        client.websocket_connect(path).__enter__()
    return ei.value


def _ws_path(token=""):
    return f"/api/link/ws?token={token}" if token else "/api/link/ws"


# ── WS 握手拒绝面 ─────────────────────────────────────────────────────────


def test_ws_rejected_bad_token_recorded_secret_free(env):
    bad = "not-a-jwt-secret-material"
    verdict = _open_expect_reject(env["client"], _ws_path(bad))
    assert verdict.code == 1008
    rows = _wait_rows(env["store"], "link_ws_rejected", 1)
    assert len(rows) == 1
    d = rows[0].detail_dict
    assert d["has_token"] is True
    assert d["face"] == "link_ws"
    assert d["cause"] == "invalid_token"
    assert "client" in d
    # 被拒尝试无派生身份→'default' 兜底（③b-2 旁路纪律）
    assert rows[0].tenant_id == "default"
    # 凭据材料只字不进事件（硬反证：token 串不出现在任何字段，键名也不含 token）
    blob = json.dumps({"summary": rows[0].summary, "detail": d}, ensure_ascii=False)
    assert bad not in blob
    assert "token" not in d
    # 零会话面：无 connected/closed 行
    assert _rows(env["store"], "link_ws_connected") == []
    assert _rows(env["store"], "link_ws_closed") == []


# ── WS 会话面：connected + closed 成对 ────────────────────────────────────


def test_ws_anonymous_session_pair(env):
    a = _open(env["client"], _ws_path())
    try:
        a.ws.send_text("hello")  # 通道真可用（匿名契约不变）
    finally:
        a.close()  # 客户端断开→closed
    conn_rows = _wait_rows(env["store"], "link_ws_connected", 1)
    closed_rows = _wait_rows(env["store"], "link_ws_closed", 1)
    c = conn_rows[0].detail_dict
    assert c["face"] == "link_ws"
    assert c["authed"] is False
    assert c["user_id"] == DEFAULT_USER  # 匿名缺省身份如实（非捏造）
    assert conn_rows[0].tenant_id == "default"
    d = closed_rows[0].detail_dict
    assert d["reason"] in ("client_disconnect", "error")
    assert isinstance(d["duration_s"], (int, float)) and d["duration_s"] >= 0
    assert closed_rows[0].tenant_id == "default"


def test_ws_token_session_pair(env):
    token = _token(env["uid"])
    a = _open(env["client"], _ws_path(token))
    try:
        a.ws.send_text("hello")
    finally:
        a.close()
    conn_rows = _wait_rows(env["store"], "link_ws_connected", 1)
    closed_rows = _wait_rows(env["store"], "link_ws_closed", 1)
    c = conn_rows[0].detail_dict
    # user/tenant=bind_identity 派生单一源真流入行归属
    assert c["user_id"] == env["uid"]
    assert c["authed"] is True
    assert conn_rows[0].tenant_id == "tenant-audit-x"
    d = closed_rows[0].detail_dict
    assert d["user_id"] == env["uid"]
    assert d["reason"] in ("client_disconnect", "error")
    assert closed_rows[0].tenant_id == "tenant-audit-x"
    # token 只字不进事件（会话面同样）
    blob = json.dumps(
        {"s": conn_rows[0].summary + closed_rows[0].summary, "d": [c, d]}, ensure_ascii=False
    )
    assert token not in blob


def test_ws_closed_reason_error(env, monkeypatch):
    """非断开型异常收口（记录面故障）→ reason=error（与 client_disconnect 分流如实）。"""
    real = link_gateway_api._record_event

    def boom(*a, **kw):
        if kw.get("event_type") == "ws_message":
            raise RuntimeError("record down")
        return real(*a, **kw)

    monkeypatch.setattr(link_gateway_api, "_record_event", boom)
    a = _open(env["client"], _ws_path(_token(env["uid"])))
    try:
        a.ws.send_text("hello")  # ws_message 记录面故障→except Exception→error
        rows = _wait_rows(env["store"], "link_ws_closed", 1)
    finally:
        a.close()
    assert rows[0].detail_dict["reason"] == "error"


def test_ws_closed_reason_credential_revoked(env, monkeypatch):
    # 短重校验节拍（raising=False：控制组旧代码无审计面容缺，失败落行为断言）
    monkeypatch.setattr(link_gateway_api, "TOKEN_REVALIDATE_INTERVAL_S", 0.2, raising=False)
    a = _open(env["client"], _ws_path(_token(env["uid"])))
    try:
        a.ws.send_text("hello")
        _set_active(env["users_db"], env["uid"], 0)  # 撤销：停用用户→看门狗判定失效
        verdict = a.expect_close()
        assert verdict.code == 1008
    finally:
        a.close()
    rows = _wait_rows(env["store"], "link_ws_closed", 1)
    assert rows[0].detail_dict["reason"] == "credential_revoked"
    assert _rows(env["store"], "link_ws_connected") != []


# ── SSE 面 ────────────────────────────────────────────────────────────────


def test_sse_rejected_bad_token_recorded(env):
    bad = "sse-bad-token-secret-material"
    r = env["client"].get(f"/api/link/stream?token={bad}")
    assert r.status_code == 401  # 零行为变化：审计后原样上抛→既有 401
    rows = _wait_rows(env["store"], "link_sse_rejected", 1)
    d = rows[0].detail_dict
    assert d["face"] == "link_sse"
    assert d["cause"] == "invalid_token"
    assert d["has_token"] is True
    assert rows[0].tenant_id == "default"
    blob = json.dumps({"summary": rows[0].summary, "detail": d}, ensure_ascii=False)
    assert bad not in blob
    assert "token" not in d
    assert _rows(env["store"], "link_sse_connected") == []


def test_sse_session_pair_direct(env):
    """直驱 _sse_event_lines（1c-③ 提取的模块级可测面）：connected+closed 成对。"""

    async def run():
        # 预置一条本租户 GatewayEvent：首次 __anext__ 即产帧（生成器启动=connected）
        link_gateway_api._record_event(
            source="probe", direction="inbound", event_type="probe_evt", tenant_id="tenant-sse"
        )
        agen = link_gateway_api._sse_event_lines(
            "tenant-sse", "", "", token=None, user_id="u-sse", authed=False
        )
        frame = await asyncio.wait_for(agen.__anext__(), 5)
        assert "probe_evt" in frame
        await agen.aclose()  # 流结束（GeneratorExit→finally）→closed

    asyncio.run(run())
    conn_rows = _wait_rows(env["store"], "link_sse_connected", 1)
    closed_rows = _wait_rows(env["store"], "link_sse_closed", 1)
    c = conn_rows[0].detail_dict
    assert c["face"] == "link_sse"
    assert c["user_id"] == "u-sse"
    assert c["has_token"] is False
    assert conn_rows[0].tenant_id == "tenant-sse"
    d = closed_rows[0].detail_dict
    assert d["reason"] in ("client_disconnect", "error")
    assert isinstance(d["duration_s"], (int, float)) and d["duration_s"] >= 0
    assert closed_rows[0].tenant_id == "tenant-sse"


def test_sse_closed_reason_credential_revoked(env, monkeypatch):
    monkeypatch.setattr(link_gateway_api, "TOKEN_REVALIDATE_INTERVAL_S", 0.2, raising=False)
    token = _token(env["uid"])

    async def run():
        link_gateway_api._record_event(
            source="probe", direction="inbound", event_type="probe_evt", tenant_id="default"
        )
        agen = link_gateway_api._sse_event_lines(
            "default", "", "", token=token, user_id=env["uid"], authed=True
        )
        await asyncio.wait_for(agen.__anext__(), 5)  # connected + 首帧
        _set_active(env["users_db"], env["uid"], 0)  # 撤销→重校验失效→流结束
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(agen.__anext__(), 5)

    asyncio.run(run())
    rows = _wait_rows(env["store"], "link_sse_closed", 1)
    assert rows[0].detail_dict["reason"] == "credential_revoked"
    assert _rows(env["store"], "link_sse_connected") != []


def test_sse_closed_reason_error(env, monkeypatch):
    """非断开型异常收口（事件读面故障）→ reason=error 如实归因。"""

    def boom(scope):
        raise RuntimeError("scoped events down")

    monkeypatch.setattr(link_gateway_api, "_scoped_events", boom)

    async def run():
        agen = link_gateway_api._sse_event_lines(
            "default", "", "", token=None, user_id="u", authed=False
        )
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(agen.__anext__(), 5)

    asyncio.run(run())
    rows = _wait_rows(env["store"], "link_sse_closed", 1)
    assert rows[0].detail_dict["reason"] == "error"


# ── 旁路纪律 + 静态接线 ───────────────────────────────────────────────────


def test_audit_failure_does_not_break_session(env, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("audit backend down")

    monkeypatch.setattr(link_gateway_api, "push_event", boom, raising=False)
    a = _open(env["client"], _ws_path())
    b = _open(env["client"], _ws_path())
    try:
        a.ws.send_text("relay-me")  # 审计故障≠会话故障：中继照常
        got = b.frame(timeout=5.0)
        assert got["from"] == "relay"
        assert got["data"] == {"raw": "relay-me"}
    finally:
        a.close()
        b.close()


def test_static_wiring():
    import inspect

    src = inspect.getsource(link_gateway_api)
    # _audit 单一审计口：恰 1 定义 + 6 调用点（两面各 rejected/connected/closed），同步口
    assert src.count("def _audit(") == 1
    assert src.count("_audit(") == 7
    assert "await _audit(" not in src  # 同步口禁回退 await 型（取消会吞审计，实测）
    # 安全归口单一源：organ=immune 恰一处（_audit 内）
    assert src.count('"immune"') == 1
    # 事件名恰各在其调用点
    for name in (
        '"link_ws_rejected"',
        '"link_ws_connected"',
        '"link_ws_closed"',
        '"link_sse_rejected"',
        '"link_sse_connected"',
        '"link_sse_closed"',
    ):
        assert src.count(name) == 1, name
    # 凭据材料禁入事件：detail 构造无 token 键（has_token 只记在场与否，恰 4 处）
    assert src.count('"has_token"') == 4
    assert '"token":' not in src
