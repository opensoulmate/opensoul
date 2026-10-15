"""DEC-⑬ link_gateway 外部回调入口鉴权化统一拍板（heartbeat+incoming）。

修前（1c-④ 边界锁死测试自注「鉴权化需与 incoming 入口统一拍板」=候选
「外部回调入口鉴权化统一拍板（heartbeat+incoming）」）：两个外部回调入口
零鉴权——/webhooks/{id}/incoming（webhook_id 即凭证，secret HMAC 可选）、
/systems/{id}/heartbeat（system_id 即凭证）——任意人可伪造心跳把 offline
顶成 online/抬计数、伪造 webhook 注入事件/改统计。

收口（DEC-⑬ 自决取 B，F3b-④/⑤ 同族「claim 注册身份必须持 register 签发
token」）：register 签发 callback_token（明文只回注册响应一次，list 面不
回吐），incoming/heartbeat 必须持 X-Callback-Token（或 incoming 的 HMAC
签名等效证明）；缺/错=401 零副作用；fail-closed 无证可验=拒。

覆盖：签发（两面+list 不漏）/ incoming 四态（缺/错/对/签名等效+替代证明+
fail-closed）/ heartbeat 四态（缺/错/对/缺行 404 契约）/ 既有契约零变化
（404/503/413/跨租户照收=回调入口非用户 JWT 轴）/ 零副作用硬证（计数/
事件/状态一字不动）/ 静态接线普查。
"""

import hashlib
import hmac
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

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
    """link_gateway router + tmp 身份库/TimelineStore/模块态 fresh + 出站记录器。"""
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
    monkeypatch.setattr(lg, "_events", __import__("collections").deque(maxlen=lg.MAX_EVENTS))
    monkeypatch.setattr(lg, "_ws_clients", [])

    sent = []

    async def _fake_send(url, payload, headers=None, source="", tenant_id=None):
        sent.append({"url": url, "payload": payload, "source": source, "tenant_id": tenant_id})
        return lg.GatewayEvent(
            event_id="gw-fake",
            timestamp=1.0,
            source=source,
            direction="outbound",
            event_type="webhook_sent",
            status="ok",
            tenant_id=tenant_id or "default",
        )

    monkeypatch.setattr(lg, "_send_with_retry", _fake_send)

    app = FastAPI()
    app.include_router(lg.router, prefix="/api/link")
    with TestClient(app) as c:
        yield {"client": c, "ent_db": ent_db, "lg": lg, "sent": sent}


def _register_webhook(env, name="wh-in", **body):
    body.setdefault("name", name)
    body.setdefault("direction", "in")
    return env["client"].post("/api/link/webhooks", json=body)


def _register_system(env, name="sys-x", **body):
    body.setdefault("name", name)
    body.setdefault("type", "custom")
    return env["client"].post("/api/link/systems", json=body)


def _seed_webhook(lg, webhook_id, tenant="default", token="", secret=""):
    wh = lg.WebhookEndpoint(
        webhook_id=webhook_id,
        name=webhook_id,
        direction=lg.WebhookDirection.IN,
    )
    # 属性直赋而非构造 kwarg：控制组（旧代码无 callback_token 字段）亦可播种，
    # 失败落在行为断言上而非 TypeError（1c-④ 同款纪律）。
    wh.tenant_id = tenant
    wh.callback_token = token
    wh.secret = secret
    lg._webhooks[webhook_id] = wh
    return wh


def _seed_system(lg, system_id, tenant="default", token=""):
    s = lg.ExternalSystem(system_id=system_id, name=system_id, type="custom")
    s.tenant_id = tenant
    s.callback_token = token
    lg._systems[system_id] = s
    return s


def _wh_side_effects(env, wh_id):
    wh = env["lg"]._webhooks[wh_id]
    evts = [e for e in env["lg"]._events if e.event_type == "webhook_received"]
    return wh.trigger_count, wh.last_triggered, len(evts)


# ── 签发（register 签发 token，明文只回一次）────────────────────────


def test_register_webhook_issues_callback_token(env):
    r = _register_webhook(env)
    assert r.status_code == 200
    tok = r.json()["callback_token"]
    assert tok and isinstance(tok, str)
    wh_id = r.json()["webhook_id"]
    assert env["lg"]._webhooks[wh_id].callback_token == tok  # 行真落


def test_register_system_issues_callback_token(env):
    r = _register_system(env)
    assert r.status_code == 200
    tok = r.json()["callback_token"]
    assert tok and isinstance(tok, str)
    sys_id = r.json()["system_id"]
    assert env["lg"]._systems[sys_id].callback_token == tok  # 行真落


def test_list_surfaces_do_not_leak_tokens(env):
    tok_w = _register_webhook(env).json()["callback_token"]
    tok_s = _register_system(env).json()["callback_token"]
    lw = env["client"].get("/api/link/webhooks").json()
    ls = env["client"].get("/api/link/systems").json()
    dumped = json.dumps(lw) + json.dumps(ls)
    assert tok_w not in dumped and tok_s not in dumped  # 明文只回注册响应一次


# ── incoming 四态+零副作用────────────────────────────────────────


def test_incoming_missing_credential_401_zero_side_effects(env):
    _seed_webhook(env["lg"], "wh-a1", token="tok-a1")
    before = _wh_side_effects(env, "wh-a1")
    r = env["client"].post("/api/link/webhooks/wh-a1/incoming", json={"data": "probe"})
    assert r.status_code == 401
    assert "X-Callback-Token" in r.json()["detail"]  # 真因可见
    assert _wh_side_effects(env, "wh-a1") == before  # 计数/事件一字不动


def test_incoming_wrong_credential_401_zero_side_effects(env):
    _seed_webhook(env["lg"], "wh-a1", token="tok-a1")
    before = _wh_side_effects(env, "wh-a1")
    r = env["client"].post(
        "/api/link/webhooks/wh-a1/incoming",
        json={"data": "probe"},
        headers={"X-Callback-Token": "tok-attacker"},
    )
    assert r.status_code == 401
    assert "invalid" in r.json()["detail"]
    assert _wh_side_effects(env, "wh-a1") == before


def test_incoming_valid_token_200(env):
    _seed_webhook(env["lg"], "wh-a1", token="tok-a1")
    r = env["client"].post(
        "/api/link/webhooks/wh-a1/incoming",
        json={"data": "probe"},
        headers={"X-Callback-Token": "tok-a1"},
    )
    assert r.status_code == 200
    assert r.json()["received"] is True
    count, triggered, evts = _wh_side_effects(env, "wh-a1")
    assert count == 1 and triggered > 0 and evts == 1


def test_incoming_hmac_signature_equivalent_proof(env):
    """wh.secret HMAC 签名=等效证明（发方持 secret 即持证，签名面契约零变化）。"""
    secret = "s3cret"
    _seed_webhook(env["lg"], "wh-a1", token="tok-a1", secret=secret)
    body = b'{"data": "signed"}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = env["client"].post(
        "/api/link/webhooks/wh-a1/incoming",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": sig},
    )
    assert r.status_code == 200
    assert _wh_side_effects(env, "wh-a1")[0] == 1


def test_incoming_token_alternative_proof_with_bad_signature(env):
    """secret 已配但 token 正确=替代证明放行（两证任一即可，非双因子要求）。"""
    _seed_webhook(env["lg"], "wh-a1", token="tok-a1", secret="s3cret")
    body = b'{"data": "x"}'
    r = env["client"].post(
        "/api/link/webhooks/wh-a1/incoming",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": "sha256=deadbeef",
            "X-Callback-Token": "tok-a1",
        },
    )
    assert r.status_code == 200


def test_incoming_secret_configured_all_proofs_bad_401(env):
    _seed_webhook(env["lg"], "wh-a1", token="tok-a1", secret="s3cret")
    before = _wh_side_effects(env, "wh-a1")
    body = b'{"data": "x"}'
    r = env["client"].post(
        "/api/link/webhooks/wh-a1/incoming",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": "sha256=deadbeef"},
    )
    assert r.status_code == 401
    assert _wh_side_effects(env, "wh-a1") == before


def test_incoming_no_credential_configured_fail_closed(env):
    """无证可验=拒（fail-closed）：缺行凭据的行不因"没配凭据"静默放行。"""
    _seed_webhook(env["lg"], "wh-a1", token="", secret="")
    before = _wh_side_effects(env, "wh-a1")
    r = env["client"].post("/api/link/webhooks/wh-a1/incoming", json={"data": "x"})
    assert r.status_code == 401
    assert _wh_side_effects(env, "wh-a1") == before


def test_incoming_unknown_id_404_unchanged(env):
    r = env["client"].post(
        "/api/link/webhooks/nope/incoming", json={"data": "x"}, headers={"X-Callback-Token": "t"}
    )
    assert r.status_code == 404  # 缺行 404 契约不变


def test_incoming_disabled_503_unchanged(env):
    wh = _seed_webhook(env["lg"], "wh-a1", token="tok-a1")
    wh.enabled = False
    r = env["client"].post(
        "/api/link/webhooks/wh-a1/incoming",
        json={"data": "x"},
        headers={"X-Callback-Token": "tok-a1"},
    )
    assert r.status_code == 503  # disabled 契约不变


# ── heartbeat 四态+零副作用────────────────────────────────────────


def test_heartbeat_missing_credential_401_zero_side_effects(env):
    s = _seed_system(env["lg"], "sys-a1", token="tok-s1")
    s.status = "offline"
    r = env["client"].post("/api/link/systems/sys-a1/heartbeat")
    assert r.status_code == 401
    assert "X-Callback-Token" in r.json()["detail"]
    assert s.heartbeat_count == 0 and s.status == "offline"  # 状态一字不动


def test_heartbeat_wrong_credential_401_zero_side_effects(env):
    s = _seed_system(env["lg"], "sys-a1", token="tok-s1")
    s.status = "offline"
    r = env["client"].post(
        "/api/link/systems/sys-a1/heartbeat", headers={"X-Callback-Token": "tok-attacker"}
    )
    assert r.status_code == 401
    assert s.heartbeat_count == 0 and s.status == "offline"


def test_heartbeat_valid_token_200(env):
    s = _seed_system(env["lg"], "sys-a1", token="tok-s1")
    s.status = "offline"
    r = env["client"].post(
        "/api/link/systems/sys-a1/heartbeat", headers={"X-Callback-Token": "tok-s1"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "online"
    assert s.heartbeat_count == 1 and s.status == "online" and s.last_heartbeat > 0


def test_heartbeat_unknown_id_404_unchanged(env):
    r = env["client"].post("/api/link/systems/nope/heartbeat", headers={"X-Callback-Token": "t"})
    assert r.status_code == 404  # 缺行 404 契约不变


def test_heartbeat_cross_tenant_with_row_token_ok(env):
    """回调入口非用户 JWT 轴（1a 语义延续）：跨租户用户 JWT + 行 token=照收。"""
    lg = env["lg"]
    s = _seed_system(lg, "sys-a1", tenant="tenant-a", token="tok-s1")
    uid = _insert_ent_user(env["ent_db"], "u-b", "tenant-b")
    r = env["client"].post(
        "/api/link/systems/sys-a1/heartbeat",
        headers={**_auth(_ent_token(uid, "u-b")), "X-Callback-Token": "tok-s1"},
    )
    assert r.status_code == 200
    assert s.heartbeat_count == 1


# ── 静态接线普查（写了≠接线了）──────────────────────────────────


def test_static_wiring_census():
    from pathlib import Path

    src = (
        Path(__file__)
        .resolve()
        .parent.parent.joinpath("src", "api", "link_gateway.py")
        .read_text(encoding="utf-8")
    )
    # 单一判定口：两入口真消费、禁第二套判法
    assert src.count("def _callback_token_ok") == 1
    assert src.count("_callback_token_ok(") == 3  # 1 定义 + 2 消费
    # 签发两写点真落行
    assert src.count("callback_token = uuid.uuid4().hex") == 2
    assert src.count("callback_token=callback_token,") == 2
