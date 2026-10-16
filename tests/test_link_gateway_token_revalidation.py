"""F3b-⑨b-③-1c-⑤ link_gateway token 连接期重校验。

修前（1c-② 诚实边界留档件「token 连接期不重校验」）：WS /ws 与 SSE /stream
长连接只在握手/开流时校验凭据——JWT 过期、用户停用或消失后连接照常收发，
持失效凭据继续以陈旧派生租户键路由/收帧。

收口：TOKEN_REVALIDATE_INTERVAL_S 周期重校验（单一派生口 bind_identity）：
- 凭据失效 → WS close 1008（与握手拒绝同语义）/SSE 结束流，禁凭失效凭据
  继续收发；
- 派生租户变化 → re-key（WS=me.tenant_id，消息循环每条消息取最新键；
  SSE=scope 跟随新派生），禁持陈旧绑定继续服务；
- 匿名连接无凭据可失效，不重校验，行为字节不变（契约锁死）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp TimelineStore/死端口 _BASE/
link_gateway 模块态 fresh），绝不写生产库、不打 live :8090。
"""

import asyncio
import json
import queue
import sqlite3
import threading
import time as _time
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


def _ent_token(uid, username="u", lifetime_s=600.0):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(seconds=lifetime_s),
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


def _set_user(env, uid, **fields):
    """改用户行（is_active / tenant_id）——模拟停用/改绑。"""
    conn = sqlite3.connect(env["ent_db"])
    try:
        for k, v in fields.items():
            conn.execute(f"UPDATE users SET {k} = ? WHERE id = ?", (v, uid))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """link_gateway router + tmp 身份库/TimelineStore/模块态 fresh + 短重校验节拍。"""
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
    # raising=False：旧代码（控制组）无此常量时容缺，让控制组失败落在行为断言
    monkeypatch.setattr(lg, "TOKEN_REVALIDATE_INTERVAL_S", 0.2, raising=False)

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
        try:
            self.ctx.__exit__(None, None, None)
        except Exception:
            pass


def _open(env, path):
    ctx = env["client"].websocket_connect(path)
    ws = ctx.__enter__()
    return _Conn(ctx, ws)


def _mk_user(env, username, tenant):
    return _insert_ent_user(env["ent_db"], username, tenant)


def _path(token):
    return "/api/link/ws?token=" + token if token else "/api/link/ws"


def _wait_until(fn, budget=6.0, step=0.05):
    deadline = _time.time() + budget
    while _time.time() < deadline:
        if fn():
            return True
        _time.sleep(step)
    return fn()


def _frames(text_lines):
    out = []
    for line in text_lines:
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: ") :]))
    return out


# ── WS：凭据失效 → 关闭（修前：照常收发）────────────────────────────


def test_ws_close_when_identity_deactivated(env):
    """用户停用后连接必须被关（1008），禁凭失效凭据继续收发+清理 _ws_clients。"""
    lg = env["lg"]
    uid = _mk_user(env, "u-a", "tenant-a")
    ukeep = _mk_user(env, "u-keep", "tenant-a")
    tok = _ent_token(uid, "u-a")
    a = _open(env, _path(tok))
    b = _open(env, _path(_ent_token(ukeep, "u-keep")))  # 有效凭据对照=不误杀
    try:
        a.ws.send_text("alive")
        assert b.frame()["data"] == {"raw": "alive"}  # 连接真活
        _set_user(env, uid, is_active=0)  # 停用=bind_identity 401
        with pytest.raises(WebSocketDisconnect) as ei:
            a.frame(timeout=8.0)
        assert ei.value.code == 1008
        # 失效连接清出 _ws_clients（b 仍在=恰一条被清；有效对照不误杀由
        # test_ws_valid_token_survives_intervals 锁死）
        assert _wait_until(lambda: len(lg._ws_clients) == 1, budget=6.0)
    finally:
        a.close()
        b.close()


def test_ws_close_when_token_expires(env):
    """JWT 过期（真 exp）后连接必须被关（1008）——jwt 过期面。"""
    lg = env["lg"]
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a", lifetime_s=2.0)
    a = _open(env, _path(tok))
    try:
        with pytest.raises(WebSocketDisconnect) as ei:
            a.frame(timeout=12.0)  # 无帧=等关闭帧（exp 2s + 重校验节拍）
        assert ei.value.code == 1008
        assert _wait_until(lambda: len(lg._ws_clients) == 0, budget=6.0)
    finally:
        a.close()


def test_ws_valid_token_survives_intervals(env):
    """有效凭据多拍存活——禁误杀（重校验不得把好连接关掉）。"""
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    a = _open(env, _path(tok))
    b = _open(env, _path(tok))
    try:
        _time.sleep(1.2)  # ≥5 个重校验拍
        a.ws.send_text("still-alive")
        assert b.frame()["data"] == {"raw": "still-alive"}
    finally:
        a.close()
        b.close()


def test_ws_anonymous_connection_untouched(env):
    """匿名连接无凭据可失效——不重校验、不关闭（契约字节不变）。"""
    lg = env["lg"]
    a = _open(env, _path(None))
    b = _open(env, _path(None))
    try:
        _time.sleep(1.2)  # ≥5 个重校验拍（匿名不看门狗，必存活）
        a.ws.send_text("anon")
        assert b.frame()["data"] == {"raw": "anon"}
        assert len(lg._ws_clients) == 2
    finally:
        a.close()
        b.close()


# ── WS：派生租户变化 → re-key（禁陈旧绑定继续服务）──────────────────


def test_ws_tenant_rebind_rekeys_routing(env):
    """改绑后 me.tenant_id re-key：消息路由/事件归属跟随新派生键。"""
    lg = env["lg"]
    ua = _mk_user(env, "u-a1", "tenant-a")
    mate = _mk_user(env, "u-a2", "tenant-a")  # 同租户对照（不改绑）
    ub = _mk_user(env, "u-b", "tenant-b")
    a = _open(env, _path(_ent_token(ua, "u-a1")))
    m = _open(env, _path(_ent_token(mate, "u-a2")))
    b = _open(env, _path(_ent_token(ub, "u-b")))
    try:
        a.ws.send_text("pre")
        assert m.frame()["data"] == {"raw": "pre"}  # 改绑前=旧租户路由
        b.expect_silent()

        _set_user(env, ua, tenant_id="tenant-b")  # 改绑 u-a1
        # 等 re-key：tenant-a 订阅者 2→1、tenant-b 1→2（恰一个迁移）
        assert _wait_until(
            lambda: (
                sum(1 for s in lg._ws_clients if s.tenant_id == "tenant-a") == 1
                and sum(1 for s in lg._ws_clients if s.tenant_id == "tenant-b") == 2
            ),
            budget=6.0,
        )

        a.ws.send_text("post")
        assert b.frame()["data"] == {"raw": "post"}  # 新租户路由真生效
        m.expect_silent()  # 旧租户对照零投递（re-key 后不再算同租户）
        # 事件归属同样跟随 re-key（消息循环每条消息取最新键）
        rows = [e for e in lg._events if e.event_type == "ws_message"]
        assert rows[-1].tenant_id == "tenant-b"
    finally:
        a.close()
        m.close()
        b.close()


# ── SSE：凭据失效 → 结束流 / re-key（修前：陈旧绑定继续收帧）────────


def test_sse_stream_ends_when_identity_deactivated(env):
    lg = env["lg"]
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    lg._record_event(source="src-a", direction="inbound", event_type="probe", tenant_id="tenant-a")
    gen = lg._sse_event_lines("tenant-a", "", "", token=tok)

    async def run():
        first = await asyncio.wait_for(gen.__anext__(), timeout=5)
        assert _frames([first])[0]["source"] == "src-a"
        _set_user(env, uid, is_active=0)  # 停用=重校验 401
        try:
            while True:
                await asyncio.wait_for(gen.__anext__(), timeout=8)
            return "never"
        except StopAsyncIteration:
            return "ended"
        except TimeoutError:
            return "hung"

    try:
        assert asyncio.run(run()) == "ended"  # 流必须真结束（修前：永续）
    finally:
        asyncio.run(gen.aclose())


def test_sse_stream_tenant_rebind_rekeys_filter(env):
    lg = env["lg"]
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    lg._record_event(source="src-a1", direction="inbound", event_type="probe", tenant_id="tenant-a")
    gen = lg._sse_event_lines("tenant-a", "", "", token=tok)

    async def run():
        first = await asyncio.wait_for(gen.__anext__(), timeout=5)
        assert _frames([first])[0]["source"] == "src-a1"
        _set_user(env, uid, tenant_id="tenant-b")  # 改绑
        await asyncio.sleep(1.5)  # ≥1 个生成器迭代=重校验 re-key 已发生
        lg._record_event(
            source="src-a2", direction="inbound", event_type="probe", tenant_id="tenant-a"
        )
        lg._record_event(
            source="src-b1", direction="inbound", event_type="probe", tenant_id="tenant-b"
        )
        out = []
        try:
            while len(out) < 3:
                line = await asyncio.wait_for(gen.__anext__(), timeout=5)
                out.extend(_frames([line]))
        except (TimeoutError, StopAsyncIteration):
            pass
        return out

    try:
        got = asyncio.run(run())
        srcs = [f["source"] for f in got]
        assert "src-b1" in srcs  # 新派生租户面真收帧
        assert "src-a2" not in srcs  # 旧租户面零帧（禁陈旧绑定继续服务）
    finally:
        asyncio.run(gen.aclose())


def test_sse_stream_valid_token_keeps_streaming(env):
    """有效凭据多拍存活+继续出帧——禁误杀。"""
    lg = env["lg"]
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    lg._record_event(source="src-a1", direction="inbound", event_type="probe", tenant_id="tenant-a")
    gen = lg._sse_event_lines("tenant-a", "", "", token=tok)

    async def run():
        first = await asyncio.wait_for(gen.__anext__(), timeout=5)
        assert _frames([first])[0]["source"] == "src-a1"
        await asyncio.sleep(1.5)  # ≥5 个重校验拍
        lg._record_event(
            source="src-a2", direction="inbound", event_type="probe", tenant_id="tenant-a"
        )
        second = await asyncio.wait_for(gen.__anext__(), timeout=5)
        return _frames([second])[0]["source"]

    try:
        assert asyncio.run(run()) == "src-a2"
    finally:
        asyncio.run(gen.aclose())


def test_sse_anonymous_no_revalidation(env):
    """匿名流（token=None，既有 3 参调用面）不重校验、跨拍存活——契约字节不变。"""
    lg = env["lg"]
    lg._record_event(source="src-d1", direction="inbound", event_type="probe", tenant_id="default")
    gen = lg._sse_event_lines("default", "", "")

    async def run():
        first = await asyncio.wait_for(gen.__anext__(), timeout=5)
        assert _frames([first])[0]["source"] == "src-d1"
        await asyncio.sleep(1.5)  # 匿名若被误看门狗此间必炸/关流
        lg._record_event(
            source="src-d2", direction="inbound", event_type="probe", tenant_id="default"
        )
        second = await asyncio.wait_for(gen.__anext__(), timeout=5)
        return _frames([second])[0]["source"]

    try:
        assert asyncio.run(run()) == "src-d2"
    finally:
        asyncio.run(gen.aclose())


# ── /stream 真端点接线（token 真流进生成器=写了≠接线了）──────────────


@pytest.mark.asyncio
async def test_stream_endpoint_token_flows_into_revalidation(env):
    """端点真把凭据交进生成器：停用后经端点拿到的流真结束。"""
    lg = env["lg"]
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    lg._record_event(source="src-a", direction="inbound", event_type="probe", tenant_id="tenant-a")
    resp = await lg.sse_event_stream(_mk_request_obj(query="token=" + tok), "", "")
    line = await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=5)
    assert _frames([line.decode() if isinstance(line, bytes) else line])[0]["source"] == "src-a"
    _set_user(env, uid, is_active=0)
    ended = False
    try:
        while True:
            await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=8)
    except StopAsyncIteration:
        ended = True
    except TimeoutError:
        ended = False
    assert ended


def _mk_request_obj(query="", headers=None):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/link/stream",
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


def test_static_wiring_census():
    """接线普查：重校验真接进两长连接面（写了≠接线了）。"""
    from pathlib import Path

    src = (
        Path(__file__)
        .resolve()
        .parent.parent.joinpath("src", "api", "link_gateway.py")
        .read_text(encoding="utf-8")
    )
    pipe = chr(124)
    assert "str " + pipe + " None" in src  # 竖线完好探针
    assert "str *** None" not in src
    # 看门狗真启动（WS）+ 生成器真消费 token（SSE）+ /stream 真传 token
    assert src.count("asyncio.create_task(_ws_token_watchdog(") == 1
    # DEC-⑱-③ 契约随更新：SSE 端点拒绝审计缝把取参提为 presented 变量（语义不变）
    assert src.count("token=presented") == 1
    assert src.count("if token and time.time() >= next_check:") == 1
    # 既有身份派生点字节不动（1c-③④ 静态普查计数回归钉；SSE 面字节形随 DEC-⑱-③
    # 拒绝审计缝变，单一派生点语义不变）
    assert src.count("bind_identity(_ws_presented_token(ws)") == 1
    assert src.count("presented = _http_presented_token(request)") == 1
    assert src.count("bind_identity(presented or None)") == 1
