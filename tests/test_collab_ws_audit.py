"""DEC-⑱-④ WS 连接审计面——collab agent WS (/ws/agent/{agent_id}) 连接生命周期
事件落 timeline（immune 🛡 安全归口）。

闭 DEC-⑱-③ 留档件「collab 面 ⑱-④」：agent 协作通道谁被拒（顶号/冒名）/谁连上/
会话何时以何因结束必须可审计回放（此前只打 logger 不可审计）。

事件面（organ=immune，与 terminal_ws_* / ws_chat_* / link_* / intrusion_detected /
permission_denied 同流）：
- collab_ws_rejected   握手拒绝（detail: face/agent_id/client/has_token/cause，
                       token 只字不进事件）
- collab_ws_connected  会话建立（detail: face/agent_id/client，租户键=agent 行租户）
- collab_ws_closed     会话结束（detail: reason∈{credential_revoked,error,
                       client_disconnect} + duration_s）

数据面全 tmp（TIMELINE_DB conftest 隔离链 + AGENT_COLLAB_DB/DB_PATH 双保险 +
event_bridge._BASE 死端口），绝不写生产 timeline.db / agent_collaboration.db。
"""

import json
import os
import queue
import sqlite3
import threading
import time as _time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.nerve import event_bridge
from src.timeline.store import TimelineStore


def _exec(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def collab_env(tmp_path, monkeypatch):
    """collab 隔离数据面（test_agent_collab_ws_auth 同款双保险）+ tmp timeline。"""
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9", raising=False)
    monkeypatch.setenv("AGENT_COLLAB_DB", str(tmp_path / "agent_collaboration.db"))
    import src.api.agent_collaboration as collab

    monkeypatch.setattr(collab, "DB_PATH", tmp_path / "agent_collaboration.db")
    collab._init_db()
    store = TimelineStore(os.environ["TIMELINE_DB"])
    return {"collab": collab, "db": tmp_path / "agent_collaboration.db", "store": store}


@pytest.fixture()
def client(collab_env):
    app = FastAPI()
    app.include_router(collab_env["collab"].router, prefix="/api/collab")
    with TestClient(app) as c:
        yield c


def _register(client, agent_id):
    r = client.post("/api/collab/register", json={"agent_id": agent_id, "name": agent_id})
    assert r.status_code == 200
    return r.json()["token"]


def _rows(store, event_type=None):
    out = []
    for r in store.query(organ="immune", limit=300):
        if r.event_type.startswith("collab_ws"):
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

    def ping_ok(self, timeout=5.0):
        self.ws.send_json({"event": "ping"})
        assert self.frame(timeout=timeout) == {"event": "pong"}

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


def _path(agent_id, token):
    return (
        f"/api/collab/ws/agent/{agent_id}?token={token}"
        if token
        else f"/api/collab/ws/agent/{agent_id}"
    )


# ── 握手拒绝面 ─────────────────────────────────────────────────────────────


def test_rejected_missing_token_recorded(collab_env, client):
    _register(client, "a-miss")
    store = collab_env["store"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(_path("a-miss", "")):
            pass  # pragma: no cover — 进入即拒
    rows = _wait_rows(store, "collab_ws_rejected", 1)
    assert len(rows) == 1
    d = rows[0].detail_dict
    assert d["has_token"] is False
    assert d["face"] == "collab_ws"
    assert d["cause"] == "missing_token"
    assert d["agent_id"] == "a-miss"
    assert "client" in d
    # 被拒尝试无派生身份→'default' 兜底（③b-2 旁路纪律）
    assert rows[0].tenant_id == "default"
    # 零会话面：无 connected/closed 行
    assert _rows(store, "collab_ws_connected") == []
    assert _rows(store, "collab_ws_closed") == []


def test_rejected_bad_token_recorded_secret_free(collab_env, client):
    _register(client, "a-bad")
    store = collab_env["store"]
    bad = "not-a-real-agent-token-secret-material"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(_path("a-bad", bad)):
            pass  # pragma: no cover — 进入即拒
    rows = _wait_rows(store, "collab_ws_rejected", 1)
    d = rows[0].detail_dict
    assert d["has_token"] is True
    assert d["cause"] == "invalid_token"
    assert d["agent_id"] == "a-bad"
    # 凭据材料只字不进事件（硬反证：token 串不出现在任何字段，键名也不含 token）
    blob = json.dumps({"summary": rows[0].summary, "detail": d}, ensure_ascii=False)
    assert bad not in blob
    assert "token" not in d
    assert _rows(store, "collab_ws_connected") == []


def test_rejected_impersonation_target_audited(collab_env, client):
    """顶号攻击（持他人真 token 冒 victim）→ 拒绝行带被 claim 的目标身份。"""
    _register(client, "victim-a")
    tok_attacker = _register(client, "attacker-a")
    store = collab_env["store"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(_path("victim-a", tok_attacker)):
            pass  # pragma: no cover — 进入即拒
    rows = _wait_rows(store, "collab_ws_rejected", 1)
    d = rows[0].detail_dict
    assert d["agent_id"] == "victim-a"  # 被 claim 的目标
    assert d["cause"] == "invalid_token"
    blob = json.dumps({"summary": rows[0].summary, "detail": d}, ensure_ascii=False)
    assert tok_attacker not in blob  # 攻击者真 token 同样只字不进


# ── 会话面：connected + closed 成对 ───────────────────────────────────────


def test_session_records_connected_and_closed(collab_env, client):
    tok = _register(client, "s1")
    db = collab_env["db"]
    store = collab_env["store"]
    # 租户键真源=agent 行租户（测试播种行键，断言行归属真流入审计行）
    _exec(db, "UPDATE agents SET tenant_id = 'tenant-collab-x' WHERE agent_id = 's1'")
    a = _open(client, _path("s1", tok))
    try:
        a.ping_ok()  # 通道真可用
    finally:
        a.close()  # 客户端断开→closed
    conn_rows = _wait_rows(store, "collab_ws_connected", 1)
    closed_rows = _wait_rows(store, "collab_ws_closed", 1)
    c = conn_rows[0].detail_dict
    assert c["agent_id"] == "s1"
    assert c["face"] == "collab_ws"
    assert "client" in c
    # 租户键=agent 行租户单一源真流入行归属
    assert conn_rows[0].tenant_id == "tenant-collab-x"
    d = closed_rows[0].detail_dict
    assert d["agent_id"] == "s1"
    assert d["reason"] in ("client_disconnect", "error")
    assert isinstance(d["duration_s"], (int, float)) and d["duration_s"] >= 0
    assert closed_rows[0].tenant_id == "tenant-collab-x"
    # token 只字不进事件（会话面同样）
    blob = json.dumps(
        {"s": conn_rows[0].summary + closed_rows[0].summary, "d": [c, d]}, ensure_ascii=False
    )
    assert tok not in blob


def test_unregistered_agent_default_tenant_audited(collab_env, client):
    """未注册 agent_id 既有语义零变化（②d 契约）：可连接，审计行租户='default' 兜底。"""
    store = collab_env["store"]
    a = _open(client, _path("ghost-collab-audit", ""))
    try:
        a.ping_ok()
    finally:
        a.close()
    conn_rows = _wait_rows(store, "collab_ws_connected", 1)
    closed_rows = _wait_rows(store, "collab_ws_closed", 1)
    assert conn_rows[0].detail_dict["agent_id"] == "ghost-collab-audit"
    assert conn_rows[0].tenant_id == "default"
    assert closed_rows[0].tenant_id == "default"


def test_closed_reason_error(collab_env, client):
    """非断开型异常收口（畸形帧→data.get AttributeError）→ reason=error（如实分流）。"""
    tok = _register(client, "err1")
    store = collab_env["store"]
    a = _open(client, _path("err1", tok))
    try:
        a.ping_ok()
        a.ws.send_text("[1, 2]")  # JSON 数组→receive_json 回 list→.get AttributeError
    finally:
        a.close()
    rows = _wait_rows(store, "collab_ws_closed", 1)
    assert rows[0].detail_dict["reason"] == "error"


def test_closed_reason_credential_revoked(collab_env, client, monkeypatch):
    monkeypatch.setattr(collab_env["collab"], "TOKEN_REVALIDATE_INTERVAL_S", 0.2, raising=False)
    tok = _register(client, "rv1")
    db = collab_env["db"]
    store = collab_env["store"]
    a = _open(client, _path("rv1", tok))
    try:
        a.ping_ok()
        # 撤销=token 轮换（重注册换 token）→ 看门狗判定 False → close 1008
        _exec(db, "UPDATE agents SET token = 'rotated-by-test' WHERE agent_id = 'rv1'")
        verdict = a.expect_close()
        assert verdict.code == 1008
    finally:
        a.close()
    rows = _wait_rows(store, "collab_ws_closed", 1)
    assert rows[0].detail_dict["reason"] == "credential_revoked"
    assert _rows(store, "collab_ws_connected") != []


# ── 旁路纪律 + 静态接线 ───────────────────────────────────────────────────


def test_audit_failure_does_not_break_session(collab_env, client, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("audit backend down")

    monkeypatch.setattr(collab_env["collab"], "push_event", boom, raising=False)
    tok = _register(client, "by1")
    a = _open(client, _path("by1", tok))
    try:
        a.ping_ok()  # 审计故障≠会话故障
    finally:
        a.close()


def test_static_wiring():
    import inspect

    import src.api.agent_collaboration as collab

    src = inspect.getsource(collab)
    # _audit 单一审计口：恰 1 定义 + 3 调用点（rejected/connected/closed），同步口
    assert src.count("def _audit(") == 1
    assert src.count("_audit(") == 4
    assert "await _audit(" not in src  # 同步口禁回退 await 型（取消会吞审计，实测）
    # 安全归口单一源：organ=immune 恰一处（_audit 内）
    assert src.count('"immune"') == 1
    # 事件名恰各在其调用点
    assert src.count('"collab_ws_rejected"') == 1
    assert src.count('"collab_ws_connected"') == 1
    assert src.count('"collab_ws_closed"') == 1
    # 凭据材料禁入事件：detail 构造无 token 键（has_token 只记在场与否）
    assert '"has_token"' in src
    assert '"token":' not in src
