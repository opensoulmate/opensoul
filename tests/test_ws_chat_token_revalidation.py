"""ws_chat /chat 连接期凭据周期重校验（DEC-⑭-① 诚实边界⑥留档件「已有 decode_token
握手轴但无连接期重校验」收口；F3b-④b/1c-⑤/DEC-⑭-② 同族）。

修前：握手只在建连时 decode_token 校验凭据，JWT 过期/签名失效后已建立的连接
延续至断开=失效凭据仍可继续对话。

收口：TOKEN_REVALIDATE_INTERVAL_S 周期重跑 _user_for_token（单一判定口，与
握手同源禁第二套判法）：
- 凭据失效 → close 1008（与握手拒绝同语义），失效凭据不留对话通道；
- 判定口瞬断 → 记录可见后继续，故障≠失效凭据禁误杀（DEC-⑭-② 同款）。

全部 in-process 真 WS 往返（TestClient），不依赖 live :8090。
"""

import json
import queue
import threading
import time as _time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt
from starlette.websockets import WebSocketDisconnect

import src.api.ws_chat as ws_chat_api
from src.services import auth as auth_mod
from src.services.auth import create_access_token


@pytest.fixture()
def ws_env(monkeypatch):
    """ws_chat 隔离面 + 短重校验节拍（agent/terminal revalidation fixture 同款）。"""
    # raising=False：旧代码（控制组）无此常量时容缺，让控制组失败落在行为断言
    monkeypatch.setattr(ws_chat_api, "TOKEN_REVALIDATE_INTERVAL_S", 0.2, raising=False)
    app = FastAPI()
    app.include_router(ws_chat_api.router, prefix="/api/ws")
    with TestClient(app) as c:
        yield c


def _token(exp_seconds=None):
    """真 JWT：缺省=有效长 token；exp_seconds=短时效 token（真过期语义）。"""
    if exp_seconds is None:
        return create_access_token(uuid4())
    payload = {
        "sub": str(uuid4()),
        "role": "user",
        "exp": datetime.now(UTC) + timedelta(seconds=exp_seconds),
        "iat": datetime.now(UTC),
    }
    return jose_jwt.encode(
        payload, auth_mod.settings.jwt_secret, algorithm=auth_mod.settings.jwt_algorithm
    )


class _Conn:
    """一条 WS 连接 + 后台排空线程（收帧进 queue，服务端 close=WebSocketDisconnect）。"""

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


def _open(client, token):
    ctx = client.websocket_connect(f"/api/ws/chat?token={token}")
    ws = ctx.__enter__()
    return _Conn(ctx, ws)


def _wait_until(fn, budget=6.0, step=0.05):
    deadline = _time.time() + budget
    while _time.time() < deadline:
        if fn():
            return True
        _time.sleep(step)
    return fn()


class TestRevalidationClose:
    """凭据失效 → close 1008（修前：连接延续至断开，失效凭据继续对话）。"""

    def test_expired_token_connection_closes(self, ws_env):
        """真过期语义：短时效 JWT 建连后过期 → 服务端主动 close 1008。"""
        tok = _token(exp_seconds=2.0)
        a = _open(ws_env, tok)
        try:
            assert a.frame()["type"] == "connected"
            a.ping_ok()  # 过期前通道可用
            verdict = a.expect_close(timeout=8.0)
            assert verdict.code == 1008
        finally:
            a.close()

    def test_signature_rotation_connection_closes(self, ws_env, monkeypatch):
        """签名失效（jwt_secret 轮换=旧凭据全失效）→ close 1008 带 reason。"""
        tok = _token()
        a = _open(ws_env, tok)
        try:
            assert a.frame()["type"] == "connected"
            a.ping_ok()

            class _Rotated:
                jwt_secret = "rotated-secret-e2e"
                jwt_algorithm = auth_mod.settings.jwt_algorithm

            monkeypatch.setattr(auth_mod, "settings", _Rotated)
            verdict = a.expect_close(timeout=8.0)
            assert verdict.code == 1008
            assert verdict.reason == "invalid token"
        finally:
            a.close()


class TestNoFalseKill:
    """有效凭据/判定口瞬断 → 连接照常存活（禁误杀）。"""

    def test_valid_credential_survives_and_watchdog_runs(self, ws_env, monkeypatch):
        """有效凭据 >4 拍存活 + 看门狗真在跑（判口 spy 计数硬证）。"""
        orig = ws_chat_api._user_for_token
        calls = {"n": 0}

        def spy(token):
            calls["n"] += 1
            return orig(token)

        monkeypatch.setattr(ws_chat_api, "_user_for_token", spy)
        tok = _token()
        a = _open(ws_env, tok)
        try:
            assert a.frame()["type"] == "connected"
            a.ping_ok()
            _time.sleep(0.9)  # > 4 拍
            a.ping_ok()
            assert calls["n"] >= 4  # 握手 1 + 重校验拍真在跑（否则无看门狗）
        finally:
            a.close()

    def test_probe_failure_does_not_kill_connection(self, ws_env, monkeypatch):
        """判定口瞬断（故障）≠失效凭据：记录可见后继续，禁误杀。"""
        orig = ws_chat_api._user_for_token
        calls = {"n": 0}

        def flaky(token):
            calls["n"] += 1
            if calls["n"] in (2, 3):  # #1=握手，#2/#3=重校验拍（瞬断）
                raise RuntimeError("transient probe failure")
            return orig(token)

        monkeypatch.setattr(ws_chat_api, "_user_for_token", flaky)
        tok = _token()
        a = _open(ws_env, tok)
        try:
            assert a.frame()["type"] == "connected"
            a.ping_ok()
            _time.sleep(0.9)  # > 4 拍：两拍瞬断后恢复
            a.ping_ok()
            assert calls["n"] >= 3  # 瞬断拍真发生过
        finally:
            a.close()


class TestHandshakeContractUnchanged:
    """握手契约零变化（测试锁死）：重校验只加连接期轴。"""

    def test_missing_and_invalid_token_handshake_unchanged(self, ws_env):
        ctx = ws_env.websocket_connect("/api/ws/chat")
        ws = ctx.__enter__()
        try:
            assert ws.receive_json() == {"type": "error", "message": "Missing token"}
        finally:
            ctx.__exit__(None, None, None)
        ctx = ws_env.websocket_connect("/api/ws/chat?token=not-a-jwt")
        ws = ctx.__enter__()
        try:
            assert ws.receive_json() == {"type": "error", "message": "Invalid token"}
        finally:
            ctx.__exit__(None, None, None)

    def test_connected_frame_shape_pure_user_id(self, ws_env):
        """connected 帧 user_id 载荷=纯用户 id（DEC-⑰ 契约随更新：修前为
        str(decode_token(token)) dict repr 怪形，本拍收口为纯 id——生态参照形
        acp-proxy ws_chat.py 同款；dict repr 硬反证锁死禁回退）。"""
        tok = _token()
        payload = auth_mod.decode_token(tok)
        assert payload is not None
        expected = str(payload["user_id"])
        a = _open(ws_env, tok)
        try:
            frame = a.frame()
            assert frame == {"type": "connected", "user_id": expected}
            # 硬反证：载荷禁 dict repr 怪形（修前 str(dict) 含 "user_id"/"UUID(" 字样）
            assert "user_id" not in frame["user_id"]
            assert "UUID(" not in frame["user_id"]
            a.ping_ok()
        finally:
            a.close()


class TestSingleVerdict:
    """单一判定口静态普查：握手与看门狗共用 _user_for_token，禁第二套判法。"""

    def test_single_verdict_census(self):
        import inspect

        src = inspect.getsource(ws_chat_api)
        # decode_token 唯一消费点=判口内（握手不再直调=无第二套判法）
        assert src.count("decode_token(") == 1
        # _user_for_token：1 定义 + 2 消费点（握手 + 看门狗）
        assert src.count("_user_for_token(") == 3
