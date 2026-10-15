"""F3b-⑨b-③-1c 边界② 收口：link_gateway WS relay 广播消息路由隔离。

修前（⑨b-③-1c 诚实边界② 留档）：WS relay 广播是全局内存面跨租户互通——
_ws_clients 扁平列表，任一租户 WS 客户端发出的消息被广播给**全部**其余连接
客户端（跨租户消息泄露），事件归属已收（1c）但消息路由未隔离。

收口：WsSubscriber(ws, tenant_id)——tenant_id=握手 bind_identity 派生租户键
（单一归属源）；广播只走发方租户键，跨租户零投递；发方自身照旧不收自己的
广播；匿名（'default'）面行为不变（本地单机契约）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp TimelineStore/死端口 _BASE/
link_gateway 模块态 fresh），绝不写生产库、不打 live :8090。
传输级证据：同租户正对照真收到帧，再断言跨租户队列超时零帧（广播循环同一
迭代内投递，正对照到达即证明循环已跑过——非纯时序猜测）。
"""

import json
import queue
import sqlite3
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt
from starlette.websockets import WebSocketDisconnect

from src.config import settings
from src.nerve import event_bridge

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _ent_token(uid, username="u"):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _insert_ent_user(db, username, tenant, is_active=1):
    from src.services import tenant_binding as tb

    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, is_active, tenant_id) VALUES (?, ?, ?, ?)",
            (username, "hashed", is_active, tenant),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """link_gateway router + tmp 身份库/TimelineStore/模块态 fresh。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.link_gateway as lg
    from src.timeline.store import TimelineStore

    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    ent_db = tmp_path / "enterprise.db"
    conn = sqlite3.connect(ent_db)
    conn.execute(ENTERPRISE_DDL)
    conn.commit()
    conn.close()

    tl_db = str(tmp_path / "timeline.db")
    store = TimelineStore(tl_db)
    monkeypatch.setattr(event_bridge.push_event, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    monkeypatch.setattr(lg, "_webhooks", {})
    monkeypatch.setattr(lg, "_systems", {})
    monkeypatch.setattr(lg, "_events", deque(maxlen=lg.MAX_EVENTS))
    monkeypatch.setattr(lg, "_ws_clients", [])

    from src.api import event_stream

    event_stream._event_buffer.clear()

    app = FastAPI()
    app.include_router(lg.router, prefix="/api/link")
    with TestClient(app) as c:
        yield {
            "client": c,
            "tl_db": tl_db,
            "ent_db": ent_db,
            "lg": lg,
        }
    event_stream._event_buffer.clear()


class _Conn:
    """一条 WS 连接 + 后台排空线程（收帧进 queue，负断言=queue 超时零帧）。"""

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
        assert msg["type"] == "websocket.send", msg  # ASGI app→client 帧形
        return json.loads(msg["text"])

    def expect_silent(self, timeout=1.0):
        with pytest.raises(queue.Empty):
            self.q.get(timeout=timeout)

    def close(self):
        self.ctx.__exit__(None, None, None)


def _open(env, path):
    ctx = env["client"].websocket_connect(path)
    ws = ctx.__enter__()
    return _Conn(ctx, ws)


def _mk_user(env, username, tenant):
    return _insert_ent_user(env["ent_db"], username, tenant)


def _path(token):
    return "/api/link/ws?token=" + token if token else "/api/link/ws"


# ── 同租户路由（正对照=广播循环真跑）────────────────────────────────


def test_relay_same_tenant_delivered(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    a = _open(env, _path(tok))
    b = _open(env, _path(tok))
    try:
        a.ws.send_text("m1")
        frame = b.frame()
        assert frame["from"] == "relay"
        assert frame["data"] == {"raw": "m1"}
    finally:
        a.close()
        b.close()


def test_relay_multi_recipient_same_tenant(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    a = _open(env, _path(tok))
    b = _open(env, _path(tok))
    c = _open(env, _path(tok))
    try:
        a.ws.send_text("fanout")
        assert b.frame()["data"] == {"raw": "fanout"}
        assert c.frame()["data"] == {"raw": "fanout"}
    finally:
        a.close()
        b.close()
        c.close()


def test_relay_sender_not_echoed(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    a = _open(env, _path(tok))
    b = _open(env, _path(tok))
    try:
        a.ws.send_text("m1")
        assert b.frame()["data"] == {"raw": "m1"}
        b.ws.send_text("m2")
        # a 只收到 b 的 m2，不收自己发出的 m1（发方自排除既有语义）
        assert a.frame()["data"] == {"raw": "m2"}
        a.expect_silent()
    finally:
        a.close()
        b.close()


# ── 跨租户零投递（修前：全局互通=泄露）────────────────────────────


def test_relay_cross_tenant_zero_delivery(env):
    ua = _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    a = _open(env, _path(_ent_token(ua, "u-a")))
    c = _open(env, _path(_ent_token(ua, "u-a")))  # 同租户正对照
    b = _open(env, _path(_ent_token(ub, "u-b")))
    try:
        a.ws.send_text("leak-probe")
        # 正对照先到=广播循环同一迭代已跑过；b 队列必须零帧（修前：b 真收到）
        assert c.frame()["data"] == {"raw": "leak-probe"}
        b.expect_silent()
    finally:
        a.close()
        b.close()
        c.close()


def test_relay_cross_tenant_bidirectional_zero(env):
    ua = _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    a = _open(env, _path(_ent_token(ua, "u-a")))
    b = _open(env, _path(_ent_token(ub, "u-b")))
    b2 = _open(env, _path(_ent_token(ub, "u-b")))  # b 同租户正对照
    try:
        b.ws.send_text("b-to-a")
        assert b2.frame()["data"] == {"raw": "b-to-a"}  # 正对照=循环真跑
        a.expect_silent()  # 反向同样零投递
    finally:
        a.close()
        b.close()
        b2.close()


def test_relay_anonymous_vs_named_tenant_zero(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    anon = _open(env, _path(None))
    named = _open(env, _path(_ent_token(uid, "u-a")))
    anon2 = _open(env, _path(None))
    try:
        anon.ws.send_text("anon-msg")
        assert anon2.frame()["data"] == {"raw": "anon-msg"}  # 匿名面内部照旧
        named.expect_silent()  # 匿名 'default' 与 tenant-a 互不投递（双向）
    finally:
        anon.close()
        anon2.close()
        named.close()


def test_relay_named_to_anonymous_zero(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    named = _open(env, _path(_ent_token(uid, "u-a")))
    named2 = _open(env, _path(_ent_token(uid, "u-a")))
    anon = _open(env, _path(None))
    try:
        named.ws.send_text("named-msg")
        assert named2.frame()["data"] == {"raw": "named-msg"}
        anon.expect_silent()
    finally:
        named.close()
        named2.close()
        anon.close()


# ── 匿名面契约不变（本地单机）────────────────────────────────────


def test_relay_anonymous_pair_contract_unchanged(env):
    a = _open(env, _path(None))
    b = _open(env, _path(None))
    try:
        a.ws.send_text("hello")
        assert b.frame()["data"] == {"raw": "hello"}
    finally:
        a.close()
        b.close()


# ── 帧形/结构不变面（测试锁死）──────────────────────────────────


def test_relay_frame_shape_unchanged(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    a = _open(env, _path(tok))
    b = _open(env, _path(tok))
    try:
        a.ws.send_text("shape")
        frame = b.frame()
        assert set(frame.keys()) == {"from", "data", "timestamp"}
        assert frame["from"] == "relay"
        assert isinstance(frame["timestamp"], float)
    finally:
        a.close()
        b.close()


def test_relay_json_payload_passthrough(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    a = _open(env, _path(tok))
    b = _open(env, _path(tok))
    try:
        a.ws.send_text(json.dumps({"k": "v", "n": 3}))
        assert b.frame()["data"] == {"k": "v", "n": 3}
    finally:
        a.close()
        b.close()


def test_ws_disconnected_client_pruned(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    a = _open(env, _path(tok))
    b = _open(env, _path(tok))
    try:
        assert len(env["lg"]._ws_clients) == 2
        b.close()
        deadline = time.time() + 3.0
        while time.time() < deadline and len(env["lg"]._ws_clients) > 1:
            time.sleep(0.02)
        assert len(env["lg"]._ws_clients) == 1  # 断开者被清出（既有语义）
        a.ws.send_text("after-prune")  # 广播遍历不因残留条目崩溃
    finally:
        a.close()


def test_ws_connected_event_total_clients_contract(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    a = _open(env, _path(tok))
    b = _open(env, _path(tok))
    try:
        deadline = time.time() + 3.0
        evs = []
        while time.time() < deadline:
            evs = [e for e in env["lg"]._events if e.event_type == "ws_connected"]
            if len(evs) >= 2:
                break
            time.sleep(0.05)
        # total_clients 语义不变（总数，系统视图；真源=GatewayEvent.payload）
        assert [e.payload.get("total_clients") for e in evs] == [1, 2]
        # 行归属不变面（1c 既有语义零变化）
        con = sqlite3.connect(env["tl_db"])
        con.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM events WHERE event_type = 'ws_connected' ORDER BY timestamp, event_id"
            )
        ]
        con.close()
        assert len(rows) == 2
        assert [r["tenant_id"] for r in rows] == ["tenant-a", "tenant-a"]
    finally:
        a.close()
        b.close()


def test_stats_ws_clients_contract(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    a = _open(env, _path(tok))
    b = _open(env, _path(tok))
    try:
        r = env["client"].get("/api/link/gateway/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["ws_clients"] == 2  # 键集与语义不变（全局计数）
        assert set(data.keys()) == {
            "status",
            "component",
            "webhooks",
            "systems",
            "events",
            "ws_clients",
            "systems_online",
            "systems_offline",
        }
    finally:
        a.close()
        b.close()


def test_ws_bad_token_still_rejected(env):
    """不变面：1c 坏 token 握手拒绝语义零变化，且不占通道。"""
    with pytest.raises(WebSocketDisconnect):
        ctx = env["client"].websocket_connect(_path("not-a-jwt"))
        ctx.__enter__()
    assert env["lg"]._ws_clients == []
