"""F3b-④b /ws/agent/{agent_id} token 连接期重校验。

修前（1c-⑤ 诚实边界③留档件「其余长连接面不在本拍」）：F3b-④ 只在握手时校验
register 签发 token——token 轮换（重注册换 token）、agent 行删除、连接后才注册
（顶号重开面）发生后，已建立的长连接照常收发/顶号，claim 注册身份无需持续持证。

收口（link_gateway 1c-⑤ 同族语义）：TOKEN_REVALIDATE_INTERVAL_S 周期重跑
_agent_token_verdict（单一判定口，与握手/HTTP /message 同源）：
- verdict False（claim 注册身份但凭据缺失/不匹配）→ close 1008（与握手拒绝
  同语义），看门狗只摘自己的 _ws_connections 注册（禁误删同名后连接）；
- verdict True / None（未注册语义 ②d 契约）→ 继续服务；行删除=降级未注册
  语义，重注册后持旧凭据连接随 False 关闭；
- 判定口瞬断（DB 故障）→ 记录可见后继续，故障≠失效凭据禁误杀。

数据面全 tmp（AGENT_COLLAB_DB 环境隔离），绝不写生产 data/agent_collaboration.db。
"""

import json
import queue
import sqlite3
import threading
import time as _time

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


@pytest.fixture()
def collab_env(tmp_path, monkeypatch):
    """agent_collaboration 隔离数据面 + 短重校验节拍（1c-⑤ fixture 同款）。"""
    monkeypatch.setenv("AGENT_COLLAB_DB", str(tmp_path / "agent_collaboration.db"))
    import src.api.agent_collaboration as collab

    monkeypatch.setattr(collab, "DB_PATH", tmp_path / "agent_collaboration.db")
    collab._init_db()
    # raising=False：旧代码（控制组）无此常量时容缺，让控制组失败落在行为断言
    monkeypatch.setattr(collab, "TOKEN_REVALIDATE_INTERVAL_S", 0.2, raising=False)
    monkeypatch.setattr(collab, "_ws_connections", {})
    return collab


@pytest.fixture()
def client(collab_env):
    app = FastAPI()
    app.include_router(collab_env.router, prefix="/api/collab")
    with TestClient(app) as c:
        yield c


def _register(client, agent_id):
    r = client.post("/api/collab/register", json={"agent_id": agent_id, "name": agent_id})
    assert r.status_code == 200
    return r.json()["token"]


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


def _wait_until(fn, budget=6.0, step=0.05):
    deadline = _time.time() + budget
    while _time.time() < deadline:
        if fn():
            return True
        _time.sleep(step)
    return fn()


def _path(agent_id, token):
    return (
        f"/api/collab/ws/agent/{agent_id}?token={token}"
        if token
        else f"/api/collab/ws/agent/{agent_id}"
    )


class TestRevalidationClose:
    """claim 注册身份的凭据失效 → close 1008（修前：照常收发/顶号）。"""

    def test_ws_close_when_token_rotated(self, collab_env, client):
        """token 轮换（重注册换 token）后连接必须被关（1008）+清理自己的注册；
        有效凭据对照连接不误杀。"""
        tok1 = _register(client, "a1")
        keep_tok = _register(client, "a-keep")
        a = _open(client, _path("a1", tok1))
        keep = _open(client, _path("a-keep", keep_tok))
        try:
            a.ping_ok()
            keep.ping_ok()
            _register(client, "a1")  # 同 agent 重注册=token 轮换（②d 既有语义）
            verdict = a.expect_close()
            assert verdict.code == 1008
            # 看门狗/finally 只摘自己的注册
            assert _wait_until(lambda: "a1" not in collab_env._ws_connections, budget=6.0)
            keep.ping_ok()  # 有效凭据对照存活=禁误杀
            assert "a-keep" in collab_env._ws_connections
        finally:
            a.close()
            keep.close()

    def test_ws_close_when_registered_after_connect(self, collab_env, client):
        """连接后才注册（顶号重开面收口）：未注册放行的连接在该名成为注册身份后
        不持签发 token=verdict False→close 1008，修前照常顶号收发。"""
        a = _open(client, _path("late-reg", ""))
        try:
            a.ping_ok()  # 未注册先连接=②d 契约放行
            _register(client, "late-reg")  # 该名成为注册身份，签发 token
            verdict = a.expect_close()
            assert verdict.code == 1008
            assert _wait_until(lambda: "late-reg" not in collab_env._ws_connections, budget=6.0)
        finally:
            a.close()

    def test_row_deleted_degrades_then_reregistered_closes(self, collab_env, client):
        """行删除=降级未注册语义（②d 同轴，连接继续）；重注册（新 token）后
        持旧凭据连接随 False 关闭。"""
        tok1 = _register(client, "d1")
        db = collab_env.DB_PATH
        a = _open(client, _path("d1", tok1))
        try:
            a.ping_ok()
            _exec(db, "DELETE FROM agents WHERE agent_id = 'd1'")
            _time.sleep(0.8)  # > 3 拍：行删除不杀连接（未注册语义）
            a.ping_ok()
            _register(client, "d1")  # 重注册=新 token，旧凭据失效
            verdict = a.expect_close()
            assert verdict.code == 1008
        finally:
            a.close()


class TestNoFalseKill:
    """有效凭据/未注册/判定口瞬断 → 连接照常存活（禁误杀）。"""

    def test_valid_credential_survives_intervals(self, collab_env, client):
        tok = _register(client, "v1")
        a = _open(client, _path("v1", tok))
        try:
            a.ping_ok()
            _time.sleep(0.9)  # > 4 拍
            a.ping_ok()
            assert "v1" in collab_env._ws_connections
        finally:
            a.close()

    def test_unregistered_stays_open_without_registration(self, collab_env, client):
        """未注册 agent_id 既有语义零变化（②d 契约锁死）：无注册身份可顶=不看
        门狗收尾，连接持续服务。"""
        a = _open(client, _path("ghost-f4b", ""))
        try:
            a.ping_ok()
            _time.sleep(0.9)  # > 4 拍
            a.ping_ok()
        finally:
            a.close()

    def test_probe_failure_does_not_kill_connection(self, collab_env, client, monkeypatch):
        """判定口瞬断（DB 故障）≠失效凭据：记录可见后继续，禁误杀。"""
        tok = _register(client, "f1")
        orig = collab_env._agent_token_verdict
        calls = {"n": 0}

        def flaky(conn, agent_id, provided):
            calls["n"] += 1
            if calls["n"] in (2, 3):  # #1=握手，#2/#3=重校验拍（瞬断）
                raise RuntimeError("transient db probe failure")
            return orig(conn, agent_id, provided)

        monkeypatch.setattr(collab_env, "_agent_token_verdict", flaky)
        a = _open(client, _path("f1", tok))
        try:
            a.ping_ok()
            _time.sleep(0.9)  # > 4 拍：两拍瞬断后恢复
            a.ping_ok()
            assert calls["n"] >= 4  # 重校验拍真在跑（瞬断真发生）
        finally:
            a.close()


class TestCleanupAndWiring:
    """注册清理只摘自己 + 接线面。"""

    def test_first_connection_exit_keeps_second_registration(self, collab_env, client):
        """同名双连接：先连接离场（看门狗 close+finally）禁误删后连接的投递注册
        （_ws_connections 是投递注册表，误删=后连接收不到消息）。"""
        tok1 = _register(client, "s1")
        a = _open(client, _path("s1", tok1))
        try:
            a.ping_ok()
            tok2 = _register(client, "s1")  # 轮换：a 的凭据失效、b 持新凭据
            b = _open(client, _path("s1", tok2))
            try:
                b.ping_ok()
                verdict = a.expect_close()  # a 被看门狗关闭
                assert verdict.code == 1008
                _time.sleep(0.3)  # 让 a 的 finally 走完
                assert "s1" in collab_env._ws_connections  # b 的注册仍在
                b.ping_ok()
            finally:
                b.close()
        finally:
            a.close()

    def test_normal_disconnect_cleans_own_registration(self, collab_env, client):
        tok = _register(client, "n1")
        a = _open(client, _path("n1", tok))
        a.ping_ok()
        a.close()  # 正常断开
        assert _wait_until(lambda: "n1" not in collab_env._ws_connections, budget=6.0)

    def test_static_wiring(self, collab_env):
        """接线面静态普查：看门狗真被 agent_ws 起、finally 真收、判定走单一判定口。"""
        import inspect

        src = inspect.getsource(collab_env)
        assert "asyncio.create_task(" in src
        assert "_agent_ws_token_watchdog(" in src
        # G1-⑤ 契约随更新：看门狗增 tenant_id 形参（节拍兼协调心跳保活），
        # 调用点真传行归属租户+presented token（断言意图零丢失）
        assert "_ws_presented_token(websocket), row_tenant" in src
        assert "watchdog.cancel()" in src
        wd_src = inspect.getsource(collab_env._agent_ws_token_watchdog)
        assert "_agent_token_verdict(conn, agent_id, token)" in wd_src  # 单一判定口禁第二套判法
        assert 'websocket.close(code=1008, reason="invalid agent token")' in wd_src
