"""F3b-⑨b-③-1c-③ link_gateway 事件面（GatewayEvent+_events）行租户键加轴。

修前（⑨b-③-1c 诚实边界留档件「注册表·SSE·/events 全局面未收」拆件首件=事件
面）：_record_event 虽已带 bind_identity 派生租户键，但只透传 push_event、
不落 GatewayEvent 行——事件读面（/events 与 /stream SSE）零租户过滤=任意
身份可见全量事件（/stream 为生产活面匿名即全量可见）。

覆盖：行归属（身份派生/缺失空白 'default' 兜底不掉落库）+ /events 恒过滤
（跨租户隔离/total 作用域/既有过滤组合/形状不变/坏 token 401）+ SSE 生成器
（同租户投递/跨租户零帧/匿名 default 面/过滤语义）+ /stream 真端点（双源
取参/坏 token 401/真流式帧）。
"""

import asyncio
import json
import sqlite3
from collections import deque
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt
from starlette.requests import Request

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


def _auth(token):
    return {"Authorization": "Bearer " + token} if token else {}


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


def _mk_user(env, username, tenant):
    return _insert_ent_user(env["ent_db"], username, tenant)


def _tl_rows(db_path, event_type=None):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    sql = "SELECT * FROM events"
    args = ()
    if event_type:
        sql += " WHERE event_type = ?"
        args = (event_type,)
    out = [dict(r) for r in con.execute(sql, args)]
    con.close()
    return out


def _frames(text_lines):
    out = []
    for line in text_lines:
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: ") :]))
    return out


def _collect_sync(gen, max_frames=5, budget=1.5):
    """同步收集 SSE 生成器帧（负断言=budget 内零帧）。"""

    async def _run():
        out = []
        try:
            while len(out) < max_frames:
                out.append(await asyncio.wait_for(gen.__anext__(), timeout=budget))
        except (TimeoutError, StopAsyncIteration):
            pass
        finally:
            await gen.aclose()
        return out

    return asyncio.run(_run())


def _mk_request(query="", headers=None):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/link/stream",
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


# ── 行归属（写点落行）────────────────────────────────────────


def test_record_event_row_tenant_identity(env):
    """身份派生租户键真落 GatewayEvent 行 + timeline 同键（单一判法双面）。"""
    lg = env["lg"]
    uid = _mk_user(env, "u-a", "tenant-a")
    r = env["client"].post(
        "/api/link/webhooks",
        json={"name": "wh-a", "direction": "in"},
        headers=_auth(_ent_token(uid, "u-a")),
    )
    assert r.status_code == 200
    rows = [e for e in lg._events if e.event_type == "webhook_registered"]
    assert rows and rows[-1].tenant_id == "tenant-a"
    tl = _tl_rows(env["tl_db"], "webhook_registered")
    assert tl and tl[-1]["tenant_id"] == "tenant-a"


def test_record_event_row_tenant_anonymous_default(env):
    lg = env["lg"]
    r = env["client"].post("/api/link/webhooks", json={"name": "wh-x", "direction": "in"})
    assert r.status_code == 200
    rows = [e for e in lg._events if e.event_type == "webhook_registered"]
    assert rows and rows[-1].tenant_id == "default"


def test_record_event_row_tenant_fallback_not_dropped(env):
    """缺失/空白租户键→'default' 兜底，行照落（键派生故障绝不吃掉落库）。"""
    lg = env["lg"]
    for i, val in enumerate((None, "", "   ")):
        evt = lg._record_event(
            source=f"s{i}", direction="inbound", event_type="probe", tenant_id=val
        )
        assert evt.tenant_id == "default"
        assert lg._event_row_tenant(evt) == "default"
    assert len([e for e in lg._events if e.event_type == "probe"]) == 3


def test_event_row_tenant_judgment_legacy_blank(env):
    """判法单一源：缺键/空白→'default'，显式键原样。"""
    lg = env["lg"]
    legacy = lg.GatewayEvent(
        event_id="gw-legacy", timestamp=1.0, source="s", direction="inbound", event_type="t"
    )
    assert legacy.tenant_id == "default"
    assert lg._event_row_tenant(legacy) == "default"
    blank = lg.GatewayEvent(
        event_id="gw-blank",
        timestamp=1.0,
        source="s",
        direction="inbound",
        event_type="t",
        tenant_id="  ",
    )
    assert lg._event_row_tenant(blank) == "default"
    named = lg.GatewayEvent(
        event_id="gw-n",
        timestamp=1.0,
        source="s",
        direction="inbound",
        event_type="t",
        tenant_id="tenant-a",
    )
    assert lg._event_row_tenant(named) == "tenant-a"


# ── /events 恒过滤（读面）────────────────────────────────────


def _seed(env):
    lg = env["lg"]
    lg._record_event(source="src-a", direction="inbound", event_type="probe", tenant_id="tenant-a")
    lg._record_event(
        source="src-a2", direction="outbound", event_type="probe", tenant_id="tenant-a"
    )
    lg._record_event(source="src-b", direction="inbound", event_type="probe", tenant_id="tenant-b")
    lg._record_event(source="src-d", direction="inbound", event_type="probe")


def test_events_cross_tenant_isolated(env):
    _seed(env)
    ua = _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    ra = env["client"].get("/api/link/events", headers=_auth(_ent_token(ua, "u-a")))
    rb = env["client"].get("/api/link/events", headers=_auth(_ent_token(ub, "u-b")))
    rd = env["client"].get("/api/link/events")
    assert ra.status_code == rb.status_code == rd.status_code == 200
    sa = {e["source"] for e in ra.json()["events"]}
    sb = {e["source"] for e in rb.json()["events"]}
    sd = {e["source"] for e in rd.json()["events"]}
    assert sa == {"src-a", "src-a2"}  # A 只见自己（修前：全量可见）
    assert sb == {"src-b"}
    assert sd == {"src-d"}  # 匿名=default 面


def test_events_total_scoped(env):
    _seed(env)
    ua = _mk_user(env, "u-a", "tenant-a")
    ra = env["client"].get("/api/link/events", headers=_auth(_ent_token(ua, "u-a")))
    assert ra.json()["total"] == 2  # 作用域计数，非全局 4
    rd = env["client"].get("/api/link/events")
    assert rd.json()["total"] == 1


def test_events_filters_compose_with_tenant(env):
    _seed(env)
    ua = _mk_user(env, "u-a", "tenant-a")
    r = env["client"].get(
        "/api/link/events",
        params={"source": "src-a2", "direction": "outbound"},
        headers=_auth(_ent_token(ua, "u-a")),
    )
    assert r.status_code == 200
    evts = r.json()["events"]
    assert [e["source"] for e in evts] == ["src-a2"]
    # 异租户同 source 名零泄露（过滤先组合租户）
    ub = _mk_user(env, "u-b", "tenant-b")
    rb = env["client"].get(
        "/api/link/events", params={"source": "src-a2"}, headers=_auth(_ent_token(ub, "u-b"))
    )
    assert rb.json()["events"] == []


def test_events_shape_unchanged(env):
    _seed(env)
    r = env["client"].get("/api/link/events")
    assert set(r.json().keys()) == {"events", "total"}
    assert set(r.json()["events"][0].keys()) == {
        "event_id",
        "timestamp",
        "source",
        "direction",
        "event_type",
        "target_url",
        "status",
        "error",
        "attempt",
    }


def test_events_bad_token_401(env):
    _seed(env)
    r = env["client"].get("/api/link/events", headers=_auth("not-a-jwt"))
    assert r.status_code == 401


# ── SSE 生成器（跨租户零帧）─────────────────────────────────


def test_sse_same_tenant_yields_own_frames(env):
    _seed(env)
    lg = env["lg"]
    raw = _collect_sync(lg._sse_event_lines("tenant-a", "", ""), max_frames=2, budget=1.5)
    got = _frames(raw)
    assert {f["source"] for f in got} == {"src-a", "src-a2"}
    assert set(got[0].keys()) == {
        "event_id",
        "timestamp",
        "source",
        "direction",
        "event_type",
        "status",
    }


def test_sse_cross_tenant_zero_frames(env):
    """修前：任意订阅者全量可见=跨租户泄露；修后 B 流对 A 行恒零帧。"""
    _seed(env)
    lg = env["lg"]
    raw = _collect_sync(lg._sse_event_lines("tenant-b", "", ""), max_frames=5, budget=0.4)
    got = _frames(raw)
    assert {f["source"] for f in got} == {"src-b"}  # 一条 A 行都不许出现
    raw_a = _collect_sync(lg._sse_event_lines("tenant-a", "", ""), max_frames=5, budget=0.4)
    assert all(f["source"] != "src-b" for f in _frames(raw_a))


def test_sse_anonymous_default_only(env):
    _seed(env)
    lg = env["lg"]
    raw = _collect_sync(lg._sse_event_lines("default", "", ""), max_frames=5, budget=0.4)
    got = _frames(raw)
    assert {f["source"] for f in got} == {"src-d"}


def test_sse_source_type_filters(env):
    _seed(env)
    lg = env["lg"]
    raw = _collect_sync(lg._sse_event_lines("tenant-a", "src-a2", ""), max_frames=5, budget=0.4)
    got = _frames(raw)
    assert [f["source"] for f in got] == ["src-a2"]
    raw2 = _collect_sync(lg._sse_event_lines("tenant-a", "", "probe"), max_frames=5, budget=0.4)
    assert len(_frames(raw2)) == 2


# ── /stream 真端点（身份轴+真流式）──────────────────────────


@pytest.mark.asyncio
async def test_stream_endpoint_query_token_scoped(env):
    _seed(env)
    lg = env["lg"]
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    resp = await lg.sse_event_stream(_mk_request(query="token=" + tok), "", "")
    assert resp.media_type == "text/event-stream"
    line = await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=1.5)
    frame = _frames([line])[0]
    assert frame["source"] in {"src-a", "src-a2"}  # 真派生租户=tenant-a 面


@pytest.mark.asyncio
async def test_stream_endpoint_header_token_scoped(env):
    _seed(env)
    lg = env["lg"]
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    resp = await lg.sse_event_stream(
        _mk_request(headers={"Authorization": "Bearer " + tok}), "", ""
    )
    line = await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=1.5)
    frame = _frames([line])[0]
    assert frame["source"] in {"src-a", "src-a2"}


@pytest.mark.asyncio
async def test_stream_endpoint_cross_tenant_zero_frames(env):
    """只种 A 行——B 流恒零帧（修前：全量可见=泄露）。"""
    lg = env["lg"]
    lg._record_event(source="src-a", direction="inbound", event_type="probe", tenant_id="tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    tok = _ent_token(ub, "u-b")
    resp = await lg.sse_event_stream(_mk_request(query="token=" + tok), "", "")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=0.4)


@pytest.mark.asyncio
async def test_stream_endpoint_anonymous_default_only(env):
    _seed(env)
    lg = env["lg"]
    resp = await lg.sse_event_stream(_mk_request(), "", "")
    line = await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=1.5)
    frame = _frames([line])[0]
    assert frame["source"] == "src-d"


def test_stream_bad_token_401_zero_frames(env):
    _seed(env)
    r = env["client"].get("/api/link/stream", params={"token": "not-a-jwt"})
    assert r.status_code == 401
