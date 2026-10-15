"""F3b-⑨b-③-1c link_gateway 3 点（helper 汇聚点 + HTTP + WS）身份轴接线——
事件带 bind_identity 派生租户键（DEC-① 1c「零语义争议可先行」拆件）。

修前：link_gateway 全部调用点不传 tenant_id→事件 dict 无键→record() 'default'
兜底（timeline/store.py record tenant_val），非 default 租户身份用户的 link_gateway
动作事件落 'default'→被自己的租户过滤查询挡掉=自动作自我不可见（⑨ 审计同型、
②b-1~②b-8 同型）。

范围（DEC-① 1c）：HTTP 用户面 4 handler（register_webhook/push_to_external/
register_system/broadcast_to_systems）+ WS（/ws 凭据双源 F3b-④ 先例）+
helper 形参带入（1 改覆盖 9 fan-out）。receive_incoming_webhook（外部 webhook
入口）保持 'default' 兜底=1a 待拍面行为字节不变（边界锁死）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp TimelineStore/死端口 _BASE/
link_gateway 模块态 fresh/真 socket 出站桩），绝不写生产库、不打 live :8090。
"""

import json
import sqlite3
import threading
from collections import deque
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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


def _rows(db_path, event_type=None):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    sql = "SELECT * FROM events"
    args = ()
    if event_type:
        sql += " WHERE event_type = ?"
        args = (event_type,)
    sql += " ORDER BY timestamp, event_id"
    out = [dict(r) for r in con.execute(sql, args)]
    con.close()
    return out


def _auth(token):
    return {"Authorization": "Bearer " + token} if token else {}


class _Recorder(BaseHTTPRequestHandler):
    """出站桩——真 socket 监听，记录请求体（坏 token 零请求可硬证）。"""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.requests.append({"path": self.path, "body": body})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


class _StubHTTPServer(ThreadingHTTPServer):
    requests: list


class _Stub:
    """出站桩句柄（URL + 请求记录器）。"""

    def __init__(self, url, requests):
        self.url = url
        self.requests = requests


@pytest.fixture()
def stub_server():
    server = _StubHTTPServer(("127.0.0.1", 0), _Recorder)
    server.requests = []
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield _Stub(f"http://127.0.0.1:{server.server_address[1]}/hook", server.requests)
    server.shutdown()
    server.server_close()


@pytest.fixture()
def env(tmp_path, monkeypatch, stub_server):
    """link_gateway router + tmp 身份库/TimelineStore/模块态 fresh/出站桩。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.link_gateway as lg
    from src.timeline.store import TimelineStore

    # 身份库隔离（bind_identity 真源绝不碰生产）
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    ent_db = tmp_path / "enterprise.db"
    conn = sqlite3.connect(ent_db)
    conn.execute(ENTERPRISE_DDL)
    conn.commit()
    conn.close()

    # timeline 行归属真源=tmp（push_event._timeline 注入，绝不写生产 timeline.db）
    tl_db = str(tmp_path / "timeline.db")
    store = TimelineStore(tl_db)
    monkeypatch.setattr(event_bridge.push_event, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    # 死端口：nerve publish 快速连接失败（被 push_event 自身吞掉），绝不打 live
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    # link_gateway 模块态每用例 fresh
    monkeypatch.setattr(lg, "_webhooks", {})
    monkeypatch.setattr(lg, "_systems", {})
    monkeypatch.setattr(lg, "_events", deque(maxlen=lg.MAX_EVENTS))
    monkeypatch.setattr(lg, "_ws_clients", [])

    from src.api import event_stream

    event_stream._event_buffer.clear()

    app = FastAPI()
    app.include_router(lg.router, prefix="/api/link")  # 镜像 src/main.py 挂载面
    with TestClient(app) as c:
        yield {
            "client": c,
            "tl_db": tl_db,
            "ent_db": ent_db,
            "buffer": event_stream._event_buffer,
            "stub": stub_server,
            "lg": lg,
        }
    event_stream._event_buffer.clear()


def _mk_user(env, username, tenant):
    return _insert_ent_user(env["ent_db"], username, tenant)


def _register_webhook(env, headers=None, name="wh1", url=""):
    return env["client"].post(
        "/api/link/webhooks",
        json={"name": name, "direction": "out", "url": url},
        headers=headers or {},
    )


# ── HTTP 用户面 4 handler ────────────────────────────────────────────


def test_register_webhook_row_tenant_and_buffer(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    r = _register_webhook(env, _auth(_ent_token(uid, "u-a")))
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "webhook_registered")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "tenant-a"
    # 缓冲 dict 自带键（⑨b-① 三处注入真流经调用点）
    buffered = [
        e for e in env["buffer"] if isinstance(e, dict) and e.get("type") == "webhook_registered"
    ]
    assert buffered and buffered[0].get("tenant_id") == "tenant-a"


def test_register_webhook_anonymous_default(env):
    r = _register_webhook(env)
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "webhook_registered")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"


def test_register_webhook_bad_token_401_zero_side_effect(env):
    r = _register_webhook(env, _auth("not-a-jwt"))
    assert r.status_code == 401
    assert _rows(env["tl_db"]) == []
    # 零副作用：webhook 未被注册（身份证明先于一切）
    assert env["lg"]._webhooks == {}


def test_register_webhook_dual_tenant(env):
    ua = _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    assert _register_webhook(env, _auth(_ent_token(ua, "u-a")), name="wa").status_code == 200
    assert _register_webhook(env, _auth(_ent_token(ub, "u-b")), name="wb").status_code == 200
    rows = _rows(env["tl_db"], "webhook_registered")
    assert sorted(r["tenant_id"] for r in rows) == ["tenant-a", "tenant-b"]


def test_push_to_external_row_tenant(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    headers = _auth(_ent_token(uid, "u-a"))
    assert _register_webhook(env, headers, url=env["stub"].url).status_code == 200
    wh_id = next(iter(env["lg"]._webhooks))
    r = env["client"].post(
        "/api/link/push",
        json={"webhook_id": wh_id, "payload": {"k": "v"}},
        headers=headers,
    )
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "webhook_sent")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "tenant-a"
    assert len(env["stub"].requests) == 1  # 真出站已发生


def test_push_to_external_failure_row_tenant(env, monkeypatch):
    """_send_with_retry 失败路径两调用点（webhook_retry/webhook_failed）同带派生键。"""
    monkeypatch.setattr(env["lg"], "RETRY_DELAYS", [0.0, 0.0])
    uid = _mk_user(env, "u-a", "tenant-a")
    headers = _auth(_ent_token(uid, "u-a"))
    assert _register_webhook(env, headers, url="http://127.0.0.1:9/dead").status_code == 200
    wh_id = next(iter(env["lg"]._webhooks))
    r = env["client"].post(
        "/api/link/push",
        json={"webhook_id": wh_id, "payload": {"k": "v"}},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "error"
    retries = _rows(env["tl_db"], "webhook_retry")
    failed = _rows(env["tl_db"], "webhook_failed")
    assert len(retries) == 3 and len(failed) == 1  # 每轮（含末轮）各 1 retry + 收尾 failed
    assert all(rw["tenant_id"] == "tenant-a" for rw in retries + failed)


def test_push_to_external_bad_token_401_zero_request(env):
    assert _register_webhook(env, url=env["stub"].url).status_code == 200
    wh_id = next(iter(env["lg"]._webhooks))
    r = env["client"].post(
        "/api/link/push",
        json={"webhook_id": wh_id, "payload": {"k": "v"}},
        headers=_auth("not-a-jwt"),
    )
    assert r.status_code == 401
    assert env["stub"].requests == []  # 零出站（身份证明先于一切）
    # 零事件（setup 的 webhook_registered 除外）：身份证明先于一切
    rows = _rows(env["tl_db"])
    assert {rw["event_type"] for rw in rows} <= {"webhook_registered"}


def test_register_system_row_tenant(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    r = env["client"].post(
        "/api/link/systems",
        json={"name": "s1", "type": "custom", "url": env["stub"].url},
        headers=_auth(_ent_token(uid, "u-a")),
    )
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "system_registered")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "tenant-a"


def test_broadcast_row_tenant(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    headers = _auth(_ent_token(uid, "u-a"))
    r = env["client"].post(
        "/api/link/systems",
        json={"name": "s1", "type": "custom", "url": env["stub"].url},
        headers=headers,
    )
    assert r.status_code == 200
    r = env["client"].post(
        "/api/link/broadcast",
        json={"payload": {"msg": "hi"}, "event_type": "note"},
        headers=headers,
    )
    assert r.status_code == 200
    sent = _rows(env["tl_db"], "webhook_sent")
    bcast = _rows(env["tl_db"], "broadcast")
    assert len(sent) == 1 and len(bcast) == 1
    assert sent[0]["tenant_id"] == "tenant-a"
    assert bcast[0]["tenant_id"] == "tenant-a"


def test_broadcast_bad_token_401_zero_request(env):
    r = env["client"].post(
        "/api/link/systems",
        json={"name": "s1", "type": "custom", "url": env["stub"].url},
    )
    assert r.status_code == 200
    r = env["client"].post(
        "/api/link/broadcast",
        json={"payload": {"msg": "hi"}},
        headers=_auth("not-a-jwt"),
    )
    assert r.status_code == 401
    assert env["stub"].requests == []
    assert _rows(env["tl_db"], "system_registered")[0]["tenant_id"] == "default"


# ── WS 面（凭据双源，F3b-④ 先例）────────────────────────────────────


def _ws_send(client, url, text="hello"):
    with client.websocket_connect(url) as ws:
        ws.send_text(text)


def _ws_msg_rows(rows):
    """WS 消息双记录面按 summary 逐点拆分（_record_event 汇聚点 vs :703 转发点）。"""
    helper = [r for r in rows if r["summary"].endswith("ws_message: ws-client")]
    forward = [r for r in rows if r["summary"] == "🔌 WebSocket message received"]
    return helper, forward


def test_ws_query_token_row_tenant(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    _ws_send(env["client"], f"/api/link/ws?token={tok}", "ping-q")
    conn = _rows(env["tl_db"], "ws_connected")
    helper, forward = _ws_msg_rows(_rows(env["tl_db"], "ws_message"))
    # :678 ws_connected / :694 _record_event / :703 转发 三点各自行归属恰 1
    assert len(conn) == 1 and conn[0]["tenant_id"] == "tenant-a"
    assert len(helper) == 1 and helper[0]["tenant_id"] == "tenant-a"
    assert len(forward) == 1 and forward[0]["tenant_id"] == "tenant-a"
    # 缓冲 dict 自带键（⑨b-① 桥注入口真流经）
    buffered = [e for e in env["buffer"] if isinstance(e, dict) and e.get("type") == "ws_message"]
    assert buffered and all(e.get("tenant_id") == "tenant-a" for e in buffered)


def test_ws_header_token_row_tenant(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    tok = _ent_token(uid, "u-a")
    with env["client"].websocket_connect(
        "/api/link/ws", headers={"Authorization": "Bearer " + tok}
    ) as ws:
        ws.send_text("ping-h")
    helper, forward = _ws_msg_rows(_rows(env["tl_db"], "ws_message"))
    assert len(helper) == 1 and helper[0]["tenant_id"] == "tenant-a"
    assert len(forward) == 1 and forward[0]["tenant_id"] == "tenant-a"


def test_ws_bad_token_rejected_zero_side_effect(env):
    with pytest.raises(WebSocketDisconnect):
        with env["client"].websocket_connect("/api/link/ws?token=not-a-jwt"):
            pass
    assert _rows(env["tl_db"]) == []
    assert env["lg"]._ws_clients == []  # 不占通道


def test_ws_anonymous_default(env):
    _ws_send(env["client"], "/api/link/ws", "ping-anon")
    helper, forward = _ws_msg_rows(_rows(env["tl_db"], "ws_message"))
    assert len(helper) == 1 and helper[0]["tenant_id"] == "default"
    assert len(forward) == 1 and forward[0]["tenant_id"] == "default"


def test_ws_dual_tenant(env):
    ua = _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    _ws_send(env["client"], f"/api/link/ws?token={_ent_token(ua, 'u-a')}", "m-a")
    _ws_send(env["client"], f"/api/link/ws?token={_ent_token(ub, 'u-b')}", "m-b")
    helper, forward = _ws_msg_rows(_rows(env["tl_db"], "ws_message"))
    # 每面各 2 行、各自归属发方租户（互不串）
    assert sorted(m["tenant_id"] for m in helper) == ["tenant-a", "tenant-b"]
    assert sorted(m["tenant_id"] for m in forward) == ["tenant-a", "tenant-b"]


# ── 1a 边界锁死（DEC-① 1a 待拍面，行为字节不变）────────────────────


def test_incoming_webhook_boundary_default(env):
    """receive_incoming_webhook=外部 webhook 入口：垃圾 Authorization 不得被
    bind_identity 语义 401 打断（外部 webhook 真回归面），事件照旧 'default' 兜底。"""
    r = _register_webhook(env, name="wh-in")
    assert r.status_code == 200
    wh_id = r.json()["webhook_id"]
    r = env["client"].post(
        f"/api/link/webhooks/{wh_id}/incoming",
        json={"data": "third-party"},
        headers={"Authorization": "Bearer garbage-not-a-jwt"},
    )
    assert r.status_code == 200  # 非 401（边界锁死）
    rows = _rows(env["tl_db"])
    types = {rw["event_type"] for rw in rows}
    assert "webhook_received" in types and "webhook_ingress" in types
    assert all(rw["tenant_id"] == "default" for rw in rows)


# ── 静态接线普查（写了≠接线了）──────────────────────────────────────


def test_static_wiring_census():
    src = (
        Path(__file__)
        .resolve()
        .parent.parent.joinpath("src", "api", "link_gateway.py")
        .read_text(encoding="utf-8")
    )
    pipe = chr(124)
    assert "str " + pipe + " None" in src  # 竖线完好探针
    assert "str *** None" not in src
    # 7 HTTP handler 各恰 1 身份派生（4 声明/写面 + /events 读面 F3b-⑨b-③-1c-③
    # + /webhooks /systems list 读面 F3b-⑨b-③-1c-④）
    # + WS 1 + SSE 1（_http_presented_token 双源取参）
    assert src.count("identity = bind_identity(authorization)") == 7
    assert src.count("bind_identity(_ws_presented_token(ws)") == 1
    assert src.count("bind_identity(_http_presented_token(request)") == 1
    # 注入点：identity 派生 7（4 handler 事件/出站 + broadcast 双点 + 注册行落键 2
    # F3b-⑨b-③-1c-④）+ helper 形参 3 + WS 3
    assert src.count("tenant_id=identity.tenant_id") == 7
    assert src.count("tenant_id=tenant_id") == 3
    assert src.count("tenant_id=tenant,") == 3
    # 行归属落行 2（GatewayEvent 行键 + push_event 同键，F3b-⑨b-③-1c-③ 单一判法）
    assert src.count("tenant_id=row_tenant") == 2
    # _record_event/_send_with_retry 形参收口
    assert src.count("tenant_id: str " + pipe + " None = None") == 2
    # 注册面恒过滤/归属闸真消费（F3b-⑨b-③-1c-④：写了≠接线了）
    assert src.count("_scoped_webhooks(identity.tenant_id)") == 1
    assert src.count("_scoped_systems(identity.tenant_id)") == 2
    assert src.count("wh = _gate_webhook(req.webhook_id, identity.tenant_id)") == 1
