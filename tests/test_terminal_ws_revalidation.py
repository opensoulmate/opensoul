"""DEC-⑭-② terminal WS 连接期凭据周期重校验（F3b-④b/1c-⑤ 同族）。

DEC-⑭-① 诚实边界①留档件：握手只在建连时校验凭据，token 过期/用户停用后
已建立的 shell 会话延续至断开——失效凭据仍持活 shell（RCE 面连接期残留）。

收口（link_gateway 1c-⑤ / agent F3b-④b 同族语义）：TOKEN_REVALIDATE_INTERVAL_S
周期重跑 _identity_for_token（单一判定口，与握手同源禁第二套判法）：
- 凭据失效（JWT 过期/签名失效/用户停用或消失）→ close 1008（与握手拒绝同
  语义），read_ws 结束 → handler finally SIGHUP+reap 杀 shell——失效凭据不留
  shell；
- 判定口瞬断（DB 故障）→ 记录可见后继续，故障≠失效凭据禁误杀（F3b-④b 同款）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB 环境隔离），绝不写生产 data/opensoul.db。
"""

import json
import queue
import signal
import sqlite3
import threading
import time as _time
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


def _expiring_token(uid, ttl_s):
    """签名合法但即将过期的 JWT（exp 分支覆盖：握手过、连接期过期）。"""
    from datetime import UTC, datetime, timedelta

    from jose import jwt

    from src.config import settings

    now = datetime.now(UTC)
    payload = {
        "sub": uid,
        "role": "user",
        "iat": now,
        "exp": now + timedelta(seconds=ttl_s),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + 真 router（真 bind_identity）+ 短重校验节拍（1c-⑤ fixture 同款）。"""
    users_db = tmp_path / "users.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()
    # raising=False：旧代码（控制组）无此常量时容缺，让控制组失败落在行为断言
    monkeypatch.setattr(terminal_ws_api, "TOKEN_REVALIDATE_INTERVAL_S", 0.2, raising=False)

    app = FastAPI()
    app.include_router(terminal_ws_api.router, prefix="/api/terminal")
    client = TestClient(app)
    return {
        "client": client,
        "users_db": users_db,
        "uid_on": _seed_user(users_db, 1),
        "uid_on2": _seed_user(users_db, 1),
    }


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
        return msg["text"]

    def shell_marker(self, cmd, marker, timeout=10.0):
        """真 shell 往返：marker 只可能来自 bash 求值（输入行不含 marker 值）。"""
        self.ws.send_text(json.dumps({"type": "input", "data": cmd}))
        buf = ""
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            buf += self.frame(timeout=max(0.2, deadline - _time.time()))
            if marker in buf:
                return buf
        raise AssertionError(
            f"marker {marker!r} not seen (shell dead or pump stopped); buf={buf!r}"
        )

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


def _open(client, path, headers=None):
    ctx = client.websocket_connect(path, headers=headers or {})
    ws = ctx.__enter__()
    return _Conn(ctx, ws)


def _path(token):
    return f"/api/terminal/ws/terminal?token={token}"


def _wait_until(fn, budget=6.0, step=0.05):
    deadline = _time.time() + budget
    while _time.time() < deadline:
        if fn():
            return True
        _time.sleep(step)
    return fn()


def _set_active(db, uid, active):
    with sqlite3.connect(db) as c:
        c.execute("UPDATE users SET is_active = ? WHERE id = ?", (active, uid))
        c.commit()


class TestRevalidationClose:
    """凭据失效（停用/消失/过期）→ close 1008（修前：shell 会话延续至断开）。"""

    def test_user_disabled_mid_session_closes_1008(self, env):
        """连接期停用用户：已建立的 shell 会话必须被关（1008）；有效凭据对照连接
        不误杀（F3b-④b 同款禁误杀对照）。"""
        a = _open(env["client"], _path(_token(env["uid_on"])))
        keep = _open(env["client"], _path(_token(env["uid_on2"])))
        try:
            a.shell_marker("echo term-a-$((6*7))\n", "term-a-42")
            keep.shell_marker("echo term-k-$((2*21))\n", "term-k-42")
            _set_active(env["users_db"], env["uid_on"], 0)
            verdict = a.expect_close()
            assert verdict.code == 1008
            keep.shell_marker("echo term-k2-$((3*14))\n", "term-k2-42")  # 禁误杀
        finally:
            a.close()
            keep.close()

    def test_user_deleted_mid_session_closes_1008(self, env):
        """用户行消失（bind_identity 401 identity not found）同为凭据失效。"""
        a = _open(env["client"], _path(_token(env["uid_on"])))
        try:
            a.shell_marker("echo term-d-$((10*4))\n", "term-d-40")
            with sqlite3.connect(env["users_db"]) as c:
                c.execute("DELETE FROM users WHERE id = ?", (env["uid_on"],))
                c.commit()
            verdict = a.expect_close()
            assert verdict.code == 1008
        finally:
            a.close()

    def test_expired_token_closes_1008(self, env):
        """JWT exp 过期（签名合法）：握手过、连接期重校验拒——修前会话延续至断开。"""
        uid = _seed_user(env["users_db"], 1)
        tok = _expiring_token(uid, ttl_s=3.0)
        a = _open(env["client"], _path(tok))
        try:
            a.shell_marker("echo term-e-$((5*8))\n", "term-e-40")
            verdict = a.expect_close(timeout=10.0)
            assert verdict.code == 1008
        finally:
            a.close()

    def test_header_token_connection_also_revalidated(self, env):
        """凭据取参双源（query/Authorization 头）连接同受看门狗（双源同款）。"""
        uid = _seed_user(env["users_db"], 1)
        tok = _token(uid)
        a = _open(
            env["client"], "/api/terminal/ws/terminal", headers={"Authorization": f"Bearer {tok}"}
        )
        try:
            a.shell_marker("echo term-h-$((7*6))\n", "term-h-42")
            _set_active(env["users_db"], uid, 0)
            verdict = a.expect_close()
            assert verdict.code == 1008
        finally:
            a.close()


class TestNoFalseKill:
    """有效凭据/判定口瞬断 → 连接照常存活（禁误杀）。"""

    def test_valid_credential_survives_intervals(self, env):
        a = _open(env["client"], _path(_token(env["uid_on"])))
        try:
            a.shell_marker("echo term-v-$((1*1))\n", "term-v-1")
            _time.sleep(0.9)  # > 4 拍
            a.shell_marker("echo term-v2-$((2*2))\n", "term-v2-4")  # 真 shell 仍活
        finally:
            a.close()

    def test_probe_failure_does_not_kill_connection(self, env, monkeypatch):
        """判定口瞬断（DB 故障）≠失效凭据：记录可见后继续，禁误杀。"""
        calls = {"n": 0}
        real = terminal_ws_api.bind_identity

        def flaky(auth, *a, **kw):
            calls["n"] += 1
            if calls["n"] in (2, 3):  # #1=握手，#2/#3=重校验拍（瞬断）
                raise RuntimeError("transient db probe failure")
            return real(auth, *a, **kw)

        monkeypatch.setattr(terminal_ws_api, "bind_identity", flaky)
        a = _open(env["client"], _path(_token(env["uid_on"])))
        try:
            a.shell_marker("echo term-f-$((9*3))\n", "term-f-27")
            _time.sleep(0.9)  # > 4 拍：两拍瞬断后恢复
            a.shell_marker("echo term-f2-$((4*4))\n", "term-f2-16")
            assert calls["n"] >= 4  # 重校验拍真在跑（瞬断真发生）
        finally:
            a.close()


class TestShellCleanup:
    """失效凭据不留 shell：重校验 close → handler finally 真 SIGHUP 杀 shell。"""

    def test_revalidation_close_sighups_shell(self, env, monkeypatch):
        import os as _os

        pids, kills = [], []
        real_forkpty, real_kill = _os.forkpty, _os.kill

        def _spy_forkpty():
            pid, fd = real_forkpty()
            if pid:
                pids.append(pid)
            return pid, fd

        def _spy_kill(pid, sig):
            kills.append((pid, sig))
            real_kill(pid, sig)

        monkeypatch.setattr(terminal_ws_api.os, "forkpty", _spy_forkpty)
        monkeypatch.setattr(terminal_ws_api.os, "kill", _spy_kill)

        uid = _seed_user(env["users_db"], 1)
        a = _open(env["client"], _path(_token(uid)))
        try:
            a.shell_marker("echo term-s-$((6*7))\n", "term-s-42")
            assert pids, "forkpty 哨兵未捕获 child pid（shell 未真 spawn？）"
            _set_active(env["users_db"], uid, 0)
            verdict = a.expect_close()
            assert verdict.code == 1008
            # finally 收尾真跑：SIGHUP 送达自己 spawn 的 shell（挂断=会话撤销）
            assert _wait_until(lambda: (pids[0], signal.SIGHUP) in kills, budget=6.0), (
                f"shell 未被 SIGHUP（失效凭据留 shell）；kills={kills!r}"
            )
        finally:
            a.close()


class TestStaticWiring:
    """接线面静态普查：看门狗真被 handler 起、finally 真收、判定走单一判定口。"""

    def test_static_wiring(self):
        import inspect

        src = inspect.getsource(terminal_ws_api)
        assert "asyncio.create_task(" in src
        assert "_terminal_token_watchdog(websocket, _ws_presented_token(websocket)" in src
        assert "{t_pty, t_ws, watchdog}" in src  # 看门狗进 wait 集：撤销即拆会话
        assert "t_pty, t_ws, watchdog" in src  # finally 真收看门狗
        assert "task.cancel()" in src
        wd_src = inspect.getsource(terminal_ws_api._terminal_token_watchdog)
        assert "_identity_for_token(token)" in wd_src  # 单一判定口禁第二套判法
        assert 'websocket.close(code=1008, reason="invalid token")' in wd_src
