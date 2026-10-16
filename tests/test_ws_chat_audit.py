"""DEC-⑱-② WS 连接审计面——ws_chat 连接生命周期事件落 timeline（immune 🛡）。

闭 DEC-⑱ 诚实边界②留档件「ws_chat/link/collab 长连接面审计留待拍（⑱-②）」：
对话通道谁被拒/谁连上/会话何时以何因结束必须可审计回放。

事件面（organ=immune，安全归口与 terminal_ws_* / intrusion_detected /
permission_denied 同流）：
- ws_chat_rejected   握手拒绝（detail: face/client/has_token/cause，token 只字不进事件）
- ws_chat_connected  会话建立（detail: face/user_id/client，租户键=派生）
- ws_chat_closed     会话结束（detail: reason∈{credential_revoked,error,
                     client_disconnect} + duration_s）

数据面全 tmp（TIMELINE_DB/USERS_DB/ENTERPRISE_DB 环境隔离 + event_bridge._BASE
死端口），绝不写生产 timeline.db / 身份库 / 活动流。
"""

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

import src.api.ws_chat as ws_chat_api
from src.nerve import event_bridge
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
    """tmp 身份库 + tmp timeline（conftest TIMELINE_DB 隔离链）+ 真 router。"""
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9", raising=False)
    users_db = tmp_path / "users.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()
    app = FastAPI()
    app.include_router(ws_chat_api.router, prefix="/api/ws")
    client = TestClient(app)
    store = TimelineStore(os.environ["TIMELINE_DB"])
    return {
        "client": client,
        "users_db": users_db,
        "store": store,
        "uid": _seed_user(users_db, 1, "tenant-audit-y"),
    }


def _rows(store, event_type=None):
    out = []
    for r in store.query(organ="immune", limit=200):
        if r.event_type.startswith("ws_chat"):
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
    """一条 WS 连接 + 后台排空线程（test_ws_chat_token_revalidation 同款 harness）。"""

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

    def ping_ok(self, timeout=5.0):
        self.ws.send_json({"type": "ping"})
        assert self.frame(timeout=timeout) == {"type": "pong"}

    def expect_close(self, timeout=8.0):
        with pytest.raises(WebSocketDisconnect) as ei:
            self.frame(timeout=timeout)
        return ei.value

    def close(self):
        try:
            self.ctx.__exit__(None, None, None)
        except Exception:
            pass


def _open(client, path):
    ctx = client.websocket_connect(path)
    ws = ctx.__enter__()
    return _Conn(ctx, ws)


def _path(token):
    return f"/api/ws/chat?token={token}"


# ── 握手拒绝面 ─────────────────────────────────────────────────────────────


def test_rejected_missing_token_recorded(env):
    a = _open(env["client"], "/api/ws/chat")
    try:
        assert a.frame()["message"] == "Missing token"
        a.expect_close()
    finally:
        a.close()
    rows = _wait_rows(env["store"], "ws_chat_rejected", 1)
    assert len(rows) == 1
    d = rows[0].detail_dict
    assert d["has_token"] is False
    assert d["face"] == "ws_chat"
    assert d["cause"] == "missing_token"
    assert "client" in d
    # 被拒尝试无派生身份→'default' 兜底（③b-2 旁路纪律）
    assert rows[0].tenant_id == "default"
    # 零会话面：无 connected/closed 行
    assert _rows(env["store"], "ws_chat_connected") == []
    assert _rows(env["store"], "ws_chat_closed") == []


def test_rejected_bad_token_recorded_secret_free(env):
    bad = "not-a-jwt-secret-material"
    a = _open(env["client"], _path(bad))
    try:
        assert a.frame()["message"] == "Invalid token"
        a.expect_close()
    finally:
        a.close()
    rows = _wait_rows(env["store"], "ws_chat_rejected", 1)
    d = rows[0].detail_dict
    assert d["has_token"] is True
    assert d["cause"] == "invalid_token"
    # 凭据材料只字不进事件（硬反证：token 串不出现在任何字段，键名也不含 token）
    blob = json.dumps({"summary": rows[0].summary, "detail": d}, ensure_ascii=False)
    assert bad not in blob
    assert "token" not in d
    assert _rows(env["store"], "ws_chat_connected") == []


# ── 会话面：connected + closed 成对 ───────────────────────────────────────


def test_session_records_connected_and_closed(env):
    token = _token(env["uid"])
    a = _open(env["client"], _path(token))
    try:
        assert a.frame()["type"] == "connected"
        a.ping_ok()  # 通道真可用
    finally:
        a.close()  # 客户端断开→closed
    conn_rows = _wait_rows(env["store"], "ws_chat_connected", 1)
    closed_rows = _wait_rows(env["store"], "ws_chat_closed", 1)
    c = conn_rows[0].detail_dict
    # user_id 载荷=DEC-⑰ 单一判法（纯用户 id，非 dict repr）
    assert c["user_id"] == env["uid"]
    assert c["face"] == "ws_chat"
    # 租户键=bind_identity 派生单一源（users 行 tenant_id）真流入行归属
    assert conn_rows[0].tenant_id == "tenant-audit-y"
    d = closed_rows[0].detail_dict
    assert d["user_id"] == env["uid"]
    assert d["reason"] in ("client_disconnect", "error")
    assert isinstance(d["duration_s"], (int, float)) and d["duration_s"] >= 0
    assert closed_rows[0].tenant_id == "tenant-audit-y"
    # token 只字不进事件（会话面同样）
    blob = json.dumps(
        {"s": conn_rows[0].summary + closed_rows[0].summary, "d": [c, d]}, ensure_ascii=False
    )
    assert token not in blob


def test_closed_reason_error(env):
    """非断开型异常收口（畸形帧）→ reason=error（与 client_disconnect 分流如实）。"""
    a = _open(env["client"], _path(_token(env["uid"])))
    try:
        assert a.frame()["type"] == "connected"
        a.ws.send_text("[1, 2]")  # JSON 数组=receive_json 回 list→.get AttributeError
        assert a.frame()["type"] == "error"  # 异常如实回帧后收口
        # 收口不依赖服务端关帧形态（错误路径 handler 返回不显式 close），
        # 以审计行归因为准（test_closed_reason_shell_exit 同纪律）。
        rows = _wait_rows(env["store"], "ws_chat_closed", 1)
    finally:
        a.close()
    assert rows[0].detail_dict["reason"] == "error"


def test_closed_reason_credential_revoked(env, monkeypatch):
    # 短重校验节拍（raising=False：控制组旧代码无此常量容缺，失败落行为断言）
    monkeypatch.setattr(ws_chat_api, "TOKEN_REVALIDATE_INTERVAL_S", 0.2, raising=False)
    a = _open(env["client"], _path(_token(env["uid"])))
    try:
        assert a.frame()["type"] == "connected"
        a.ping_ok()
        _set_active(env["users_db"], env["uid"], 0)  # 撤销：停用用户→看门狗判定失效
        verdict = a.expect_close()
        assert verdict.code == 1008
    finally:
        a.close()
    rows = _wait_rows(env["store"], "ws_chat_closed", 1)
    assert rows[0].detail_dict["reason"] == "credential_revoked"
    assert _rows(env["store"], "ws_chat_connected") != []


# ── 旁路纪律 + 静态接线 ───────────────────────────────────────────────────


def test_audit_failure_does_not_break_session(env, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("audit backend down")

    monkeypatch.setattr(ws_chat_api, "push_event", boom, raising=False)
    a = _open(env["client"], _path(_token(env["uid"])))
    try:
        assert a.frame()["type"] == "connected"
        a.ping_ok()  # 审计故障≠会话故障
    finally:
        a.close()


def test_static_wiring():
    import inspect

    src = inspect.getsource(ws_chat_api)
    # _audit 单一审计口：恰 1 定义 + 4 调用点（rejected×2/connected/closed），同步口
    assert src.count("def _audit(") == 1
    assert src.count("_audit(") == 5
    assert "await _audit(" not in src  # 同步口禁回退 await 型（取消会吞审计，实测）
    # 安全归口单一源：organ=immune 恰一处（_audit 内）
    assert src.count('"immune"') == 1
    # 事件名恰各在其调用点（rejected 双拒绝路径各一）
    assert src.count('"ws_chat_rejected"') == 2
    assert src.count('"ws_chat_connected"') == 1
    assert src.count('"ws_chat_closed"') == 1
    # 凭据材料禁入事件：detail 构造无 token 键（has_token 只记在场与否）
    assert '"has_token"' in src
    assert '"token":' not in src
