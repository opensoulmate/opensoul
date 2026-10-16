"""DEC-⑭-① /api/terminal/ws/terminal 身份轴收口测试。

修前（live 修前探针 8 项 3 FAIL 硬证，scripts/e2e_terminal_ws_auth.py）：
/ws/terminal 零鉴权——任意网络可达方真 spawn bash（无 token SHELL_OK=RCE 面）、
坏 token / 停用用户 token 同给 shell。

覆盖（tmp 全隔离 USERS_DB/ENTERPRISE_DB，绝不写生产 data/opensoul.db）：
①握手凭据必持（fail-closed，DEC-⑬ 同款）：缺 token / 坏 token / 未知用户 /
  停用用户 → 握手拒绝（WebSocketDisconnect），且拒绝路径零 spawn（forkpty 哨兵
  硬反证=不给 shell 不拉进程）
②真凭据放行：query ?token= 主口（浏览器 WS 设不了头，OpenMate 面板现状即带
  token）+ Authorization: Bearer 头兜底（F3b-④/1c-⑤ 双源同款）
③真 shell 语义保持：持证连接仍真 spawn bash 算术展开（D1 契约零回归）
④bind_identity 单一派生口真接线（Gate2：spy 捕获派生调用）
"""

import json
import sqlite3
import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.api.terminal_ws as terminal_ws_api

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)

MARKER = "term-ok-42"


def _seed_user(db_path, active=1):
    uid = str(uuid.uuid4())
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', ?, 'default')",
            (uid, f"u-{uid[:8]}", active),
        )
        c.commit()
    return uid


def _token(uid):
    from src.services.auth import create_access_token

    return create_access_token(user_id=uuid.UUID(uid), role="user")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + 真 router（真 bind_identity，数据面全隔离）。"""
    users_db = tmp_path / "users.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    app = FastAPI()
    app.include_router(terminal_ws_api.router, prefix="/api/terminal")
    client = TestClient(app)
    return {
        "client": client,
        "uid_on": _seed_user(users_db, 1),
        "uid_off": _seed_user(users_db, 0),
    }


def _assert_rejected(client, url, monkeypatch=None):
    """握手拒绝 + 零 spawn 硬反证（forkpty 哨兵：拒绝路径绝不能拉起 shell）。"""
    if monkeypatch is not None:

        def _boom():
            raise AssertionError("rejected handshake must not spawn a shell")

        monkeypatch.setattr(terminal_ws_api.os, "forkpty", _boom)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url):
            pass  # pragma: no cover — 进入即拒


class TestTerminalWsAuthGate:
    """① fail-closed 握手闸。"""

    def test_missing_token_rejected_zero_spawn(self, env, monkeypatch):
        _assert_rejected(env["client"], "/api/terminal/ws/terminal", monkeypatch)

    def test_garbage_token_rejected_zero_spawn(self, env, monkeypatch):
        _assert_rejected(
            env["client"], "/api/terminal/ws/terminal?token=garbage-not-a-jwt", monkeypatch
        )

    def test_unknown_user_token_rejected(self, env):
        # 签名合法但无 users 行（bind_identity 401：identity not found）
        uid = str(uuid.uuid4())
        _assert_rejected(
            env["client"], f"/api/terminal/ws/terminal?token={_token(uid)}", monkeypatch=None
        )

    def test_inactive_user_token_rejected_zero_spawn(self, env, monkeypatch):
        tok = _token(env["uid_off"])
        _assert_rejected(env["client"], f"/api/terminal/ws/terminal?token={tok}", monkeypatch)

    def test_empty_token_query_rejected(self, env):
        _assert_rejected(env["client"], "/api/terminal/ws/terminal?token=", monkeypatch=None)

    def test_non_bearer_authorization_rejected(self, env):
        client = env["client"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/terminal/ws/terminal", headers={"Authorization": "Basic abc"}
            ):
                pass  # pragma: no cover — 进入即拒


def _shell_roundtrip(client, url, headers, marker_cmd, marker):
    """真 shell 往返（线程+有界 join，原 test_terminal_ws 同款防挂死）：
    输入行含 $((...)) 不含 marker 值，marker 只可能来自 bash 算术展开输出。"""
    collected, error = [], []

    def _run():
        try:
            with client.websocket_connect(url, headers=headers or {}) as ws:
                ws.send_text(json.dumps({"type": "input", "data": marker_cmd}))
                buf = ""
                for _ in range(60):
                    buf += ws.receive_text()
                    if marker in buf:
                        break
                collected.append(buf)
        except Exception as exc:  # noqa: BLE001 —— 捕获后如实回传给断言
            error.append(repr(exc))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=20)
    assert not t.is_alive(), "terminal WS hung —— 读泵死锁或没有 shell 被拉起"
    assert not error, f"roundtrip error: {error}"
    buf = collected[0] if collected else ""
    assert marker in buf, f"bash 未真执行命令（无算术展开）；实际收到: {buf!r}"


class TestTerminalWsTokenSources:
    """② 双源取参 + ③ 真 shell 保持 + ④ bind_identity 真接线。"""

    def test_query_token_grants_real_shell(self, env):
        tok = _token(env["uid_on"])
        _shell_roundtrip(
            env["client"],
            f"/api/terminal/ws/terminal?token={tok}",
            None,
            "echo term-ok-$((6*7))\n",
            MARKER,
        )

    def test_header_token_grants_channel(self, env):
        tok = _token(env["uid_on"])
        _shell_roundtrip(
            env["client"],
            "/api/terminal/ws/terminal",
            {"Authorization": f"Bearer {tok}"},
            "echo term-hdr-$((10*4))\n",
            "term-hdr-40",
        )

    def test_bind_identity_single_derivation_called(self, env, monkeypatch):
        """Gate2 接线硬证：放行路径真走 bind_identity 单一派生口（禁第二套判法）。"""
        calls = []
        real = terminal_ws_api.bind_identity

        def _spy(auth, *a, **kw):
            calls.append(auth)
            return real(auth, *a, **kw)

        monkeypatch.setattr(terminal_ws_api, "bind_identity", _spy)
        tok = _token(env["uid_on"])
        _shell_roundtrip(
            env["client"],
            f"/api/terminal/ws/terminal?token={tok}",
            None,
            "echo term-spy-$((2*21))\n",
            "term-spy-42",
        )
        assert calls == [f"Bearer {tok}"]

    def test_rejected_path_never_calls_bind_identity_without_token(self, env, monkeypatch):
        """缺 token=闸前置拒绝，派生口零调用（无证可验不进派生）。"""
        calls = []
        monkeypatch.setattr(
            terminal_ws_api, "bind_identity", lambda *a, **kw: calls.append(a) or None
        )
        _assert_rejected(env["client"], "/api/terminal/ws/terminal", monkeypatch)
        assert calls == []
