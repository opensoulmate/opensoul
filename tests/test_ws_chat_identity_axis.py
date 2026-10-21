"""ws_chat 判口换 bind_identity（DEC-⑭-④：DEC-⑭-③ 边界③留档件收口）。

修前（控制组实证非推断）：_user_for_token=decode_token 单一判定——
① UUID 标准 token 不查 users 行：用户停用/删除后凭据照常有效（握手照常
  connected、连接期看门狗不关闭）=失效凭据留对话通道（DEC-⑭-③ 边界③
  「用户停用/删除不触发关闭——需换 bind_identity 判口=行为变更另拍」）。
② 企业 token 分支 DB 瞬断被吞成 None：故障被当失效凭据（看门狗误杀连接），
  与判口自身声明「故障≠失效凭据」相悖（DEC-⑭-③ 边界③「留档另拍」）。

收口：_identity_for_token=bind_identity 单一派生口 + authed 必真（F3b-② 家族，
terminal_ws._identity_for_token 同款判法）——users 行存在且 is_active 才算有效
凭据，停用/删除/消失→None（握手拒 / 看门狗 close 1008）；判定口故障（非
HTTPException）原样上抛=故障≠失效凭据（握手 fail-closed 拒、看门狗记日志继续）。

全部 in-process 真 WS 往返（TestClient），不依赖 live :8090。
"""

import inspect
import json
import queue
import sqlite3
import threading
import time as _time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt
from starlette.websockets import WebSocketDisconnect

import src.api.ws_chat as ws_chat_api
import src.services.tenant_binding as tenant_binding_mod
from src.services import auth as auth_mod
from src.services.auth import create_access_token


@pytest.fixture()
def identity_env(monkeypatch, tmp_path):
    """身份真源全隔离（USERS_DB/ENTERPRISE_DB 归 tmp，绝不碰生产库）+
    短重校验节拍 + 播种/改行口。"""
    os_db = tmp_path / "opensoul.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(os_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    monkeypatch.setattr(ws_chat_api, "TOKEN_REVALIDATE_INTERVAL_S", 0.2, raising=False)
    with sqlite3.connect(os_db) as conn:
        conn.execute(
            "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT,"
            " is_active INTEGER NOT NULL DEFAULT 1,"
            " tenant_id TEXT NOT NULL DEFAULT 'default')"
        )
    with sqlite3.connect(ent_db) as conn:
        conn.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT,"
            " is_active INTEGER NOT NULL DEFAULT 1,"
            " tenant_id TEXT NOT NULL DEFAULT 'default')"
        )

    def seed_user(uid, active=1, tenant="default"):
        with sqlite3.connect(os_db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users (id, username, is_active, tenant_id)"
                " VALUES (?,?,?,?)",
                (str(uid), f"user-{uid}", 1 if active else 0, tenant),
            )
        return str(uid)

    def seed_enterprise(uid, active=1, username="ent-user", tenant="default"):
        with sqlite3.connect(ent_db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO users (id, username, is_active, tenant_id)"
                " VALUES (?,?,?,?)",
                (int(uid), username, 1 if active else 0, tenant),
            )
        return int(uid)

    def set_user(uid, active=None, delete=False):
        with sqlite3.connect(os_db) as conn:
            if delete:
                conn.execute("DELETE FROM users WHERE id = ?", (str(uid),))
            else:
                conn.execute(
                    "UPDATE users SET is_active = ? WHERE id = ?",
                    (1 if active else 0, str(uid)),
                )

    def enterprise_token(uid, username="ent-user", exp_seconds=3600):
        payload = {
            "sub": str(int(uid)),
            "username": username,
            "scope": "enterprise",
            "exp": datetime.now(UTC) + timedelta(seconds=exp_seconds),
            "iat": datetime.now(UTC),
        }
        return jose_jwt.encode(
            payload, auth_mod.settings.jwt_secret, algorithm=auth_mod.settings.jwt_algorithm
        )

    app = FastAPI()
    app.include_router(ws_chat_api.router, prefix="/api/ws")
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            seed_user=seed_user,
            seed_enterprise=seed_enterprise,
            set_user=set_user,
            enterprise_token=enterprise_token,
            ent_db=str(ent_db),
        )


class _Conn:
    """一条 WS 连接 + 后台排空线程（test_ws_chat_token_revalidation 同款）。"""

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


def _open(client, token):
    ctx = client.websocket_connect(f"/api/ws/chat?token={token}")
    ws = ctx.__enter__()
    return _Conn(ctx, ws)


def _handshake_frame(client, token):
    """握手单次往返：返回服务端首帧（error 契约帧或 connected）。"""
    ctx = client.websocket_connect(f"/api/ws/chat?token={token}")
    ws = ctx.__enter__()
    try:
        return ws.receive_json()
    finally:
        ctx.__exit__(None, None, None)


class TestHandshakeRequiresActiveIdentity:
    """握手：真实活跃身份行才给通道（修前 decode_token 不查行照常 connected）。"""

    def test_valid_user_connected(self, identity_env):
        uid = uuid4()
        identity_env.seed_user(uid)
        frame = _handshake_frame(identity_env.client, create_access_token(uid))
        assert frame["type"] == "connected"

    def test_unknown_user_row_rejected(self, identity_env):
        """token 合法签名但 users 行不存在 → Invalid token（修前 connected）。"""
        frame = _handshake_frame(identity_env.client, create_access_token(uuid4()))
        assert frame == {"type": "error", "message": "Invalid token"}

    def test_disabled_user_rejected(self, identity_env):
        """用户停用（is_active=0）→ Invalid token（修前 connected）。"""
        uid = uuid4()
        identity_env.seed_user(uid, active=0)
        frame = _handshake_frame(identity_env.client, create_access_token(uid))
        assert frame == {"type": "error", "message": "Invalid token"}


class TestRevocationClosesConnection:
    """连接期撤销（停用/删除）→ close 1008，失效凭据不留对话通道。"""

    def test_deleted_user_watchdog_closes(self, identity_env):
        uid = uuid4()
        identity_env.seed_user(uid)
        a = _open(identity_env.client, create_access_token(uid))
        try:
            assert a.frame()["type"] == "connected"
            a.ping_ok()
            identity_env.set_user(uid, delete=True)
            verdict = a.expect_close(timeout=8.0)
            assert verdict.code == 1008
        finally:
            a.close()

    def test_disabled_user_watchdog_closes(self, identity_env):
        uid = uuid4()
        identity_env.seed_user(uid)
        a = _open(identity_env.client, create_access_token(uid))
        try:
            assert a.frame()["type"] == "connected"
            a.ping_ok()
            identity_env.set_user(uid, active=False)
            verdict = a.expect_close(timeout=8.0)
            assert verdict.code == 1008
            assert verdict.reason == "invalid token"
        finally:
            a.close()

    def test_control_connection_not_killed(self, identity_env):
        """同窗控制连接（真活跃身份）不被误杀——撤销只作用于失效凭据。"""
        uid_a, uid_b = uuid4(), uuid4()
        identity_env.seed_user(uid_a)
        identity_env.seed_user(uid_b)
        a = _open(identity_env.client, create_access_token(uid_a))
        b = _open(identity_env.client, create_access_token(uid_b))
        try:
            assert a.frame()["type"] == "connected"
            assert b.frame()["type"] == "connected"
            identity_env.set_user(uid_a, delete=True)
            verdict = a.expect_close(timeout=8.0)
            assert verdict.code == 1008
            b.ping_ok()  # 控制连接照常存活
        finally:
            a.close()
            b.close()


class TestEnterpriseFace:
    """企业 token 面同判法（bind_identity scope=enterprise 分支）。"""

    def test_enterprise_connected_derived_id(self, identity_env):
        """企业 token 载荷=bind_identity 派生 user_id（str(enterprise 数字 id)，
        F3b-② 语义）——判口单一派生口锁死（旧 decode_token 按 username 解析
        opensoul.db UUID 的双源语义随判口收口）。"""
        ent_id = 700001
        identity_env.seed_enterprise(ent_id)
        frame = _handshake_frame(identity_env.client, identity_env.enterprise_token(ent_id))
        assert frame == {"type": "connected", "user_id": str(ent_id)}

    def test_enterprise_disabled_watchdog_closes(self, identity_env):
        ent_id = 700002
        identity_env.seed_enterprise(ent_id)
        a = _open(identity_env.client, identity_env.enterprise_token(ent_id))
        try:
            assert a.frame()["type"] == "connected"
            a.ping_ok()
            with sqlite3.connect(identity_env.ent_db) as conn:
                conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (ent_id,))
            verdict = a.expect_close(timeout=8.0)
            assert verdict.code == 1008
        finally:
            a.close()

    def test_enterprise_unknown_row_rejected(self, identity_env):
        frame = _handshake_frame(identity_env.client, identity_env.enterprise_token(700003))
        assert frame == {"type": "error", "message": "Invalid token"}


class TestFaultIsNotRevocation:
    """判定口故障（DB 瞬断）≠失效凭据：禁误杀 + 握手 fail-closed 照旧拒。"""

    def test_probe_fault_does_not_kill_connection(self, identity_env, monkeypatch):
        uid = uuid4()
        identity_env.seed_user(uid)
        orig = tenant_binding_mod._lookup_user
        armed = {"on": False}

        def flaky(db_path, user_pk):
            if armed["on"]:
                raise sqlite3.OperationalError("database is locked (transient)")
            return orig(db_path, user_pk)

        monkeypatch.setattr(tenant_binding_mod, "_lookup_user", flaky)
        a = _open(identity_env.client, create_access_token(uid))
        try:
            assert a.frame()["type"] == "connected"
            a.ping_ok()
            armed["on"] = True  # 重校验拍连续瞬断
            _time.sleep(0.7)  # > 2 拍
            a.ping_ok()  # 瞬断拍后连接照常存活（禁误杀）
            armed["on"] = False
            a.ping_ok()
        finally:
            a.close()

    def test_handshake_fault_fail_closed(self, identity_env, monkeypatch):
        """握手面判定口故障=无证可验=拒（fail-closed，「Invalid token」同契约帧）。"""

        def boom(db_path, user_pk):
            raise sqlite3.OperationalError("database is locked (transient)")

        monkeypatch.setattr(tenant_binding_mod, "_lookup_user", boom)
        frame = _handshake_frame(identity_env.client, create_access_token(uuid4()))
        assert frame == {"type": "error", "message": "Invalid token"}


class TestConnectedPayloadSingleSource:
    """connected 载荷=bind_identity 派生 user_id 单一源（DEC-⑰ 纯 id 契约延续）。"""

    def test_payload_equals_users_row_id(self, identity_env):
        uid = uuid4()
        identity_env.seed_user(uid, tenant="tenant-x")
        frame = _handshake_frame(identity_env.client, create_access_token(uid))
        assert frame == {"type": "connected", "user_id": str(uid)}
        assert "{" not in frame["user_id"]  # 禁 dict repr 怪形
        assert "UUID(" not in frame["user_id"]


class TestSingleVerdictCensus:
    """单一判定口静态普查：bind_identity 唯一消费点=判口内，禁第二套判法回退。"""

    def test_verdict_single_source(self):
        src = inspect.getsource(ws_chat_api)
        # decode_token 零调用（禁回退第二套判法；docstring 历史引用不计）
        assert src.count("decode_token(") == 0
        # bind_identity 唯一消费点=判口内
        assert src.count("bind_identity(") == 1
        # _identity_for_token：1 定义 + 2 消费点（握手 + 看门狗）
        assert src.count("def _identity_for_token") == 1
        assert src.count("_identity_for_token(") == 3

    def test_send_point_uses_payload_helper(self):
        src = inspect.getsource(ws_chat_api)
        assert '"user_id": str(' not in src  # 禁复活 str(dict) 载荷
        assert '"user_id": _connected_user_id(identity)' in src
        assert src.count("def _connected_user_id") == 1
