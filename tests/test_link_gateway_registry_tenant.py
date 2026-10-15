"""F3b-⑨b-③-1c-④ link_gateway 注册表面（_webhooks/_systems）行租户键加轴。

修前（⑨b-③-1c-② 留档候选「注册表·SSE·/events 全局面未收」拆件——1c-③ 已收
事件面，本拍收注册表面）：_webhooks/_systems 行零租户键、写点（register）
虽有 bind_identity 但派生租户不落行、读面（/webhooks /systems list）零过滤
=任意身份全量可见（含他人注册行）、/push by-ID 零闸=跨租户借用他人 webhook
出站（数据外泄+计数改写）、/broadcast 选靶 _systems.values() 全量=跨租户
系统真收到广播（跨租户数据外泄）。

覆盖：行归属（身份派生/缺失空白 'default' 兜底）+ 判法单一源（legacy/blank）
+ list 恒过滤（三面互斥/加性 tenant_id 键/坏 token 401）+ /push 归属闸（跨租户
与 not-found 同契约同文案/零副作用/同租户放行）+ /broadcast 选靶恒过滤（全量
广播只投自己/显式 id 跨租户与未知同契约跳过/全外 400）+ 外部回调入口边界锁死
（heartbeat/incoming 字节不动=1a 同类）+ /stats 系统视图字节不变。
"""

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


def _mk_user(env, username, tenant):
    return _insert_ent_user(env["ent_db"], username, tenant)


def _seed_webhook(lg, webhook_id, tenant):
    wh = lg.WebhookEndpoint(
        webhook_id=webhook_id,
        name=webhook_id,
        direction=lg.WebhookDirection.OUT,
        url="http://sink/" + webhook_id,
    )
    # 属性直赋而非构造 kwarg：判法 getattr 容缺收（缺失/空白→'default'），
    # 控制组（旧代码无行键）亦可播种=不变面真可对照（新增 kwarg 固有 TypeError 除外）。
    wh.tenant_id = tenant
    lg._webhooks[webhook_id] = wh
    return wh


def _seed_system(lg, system_id, tenant, url="http://sink/" + "sys"):
    s = lg.ExternalSystem(system_id=system_id, name=system_id, type="custom", url=url)
    s.tenant_id = tenant
    lg._systems[system_id] = s
    return s


# ── 行归属（写点落行）────────────────────────────────────────


def test_register_webhook_row_tenant_identity(env):
    """身份派生租户键真落 WebhookEndpoint 行（写点归属）。"""
    lg = env["lg"]
    uid = _mk_user(env, "u-a", "tenant-a")
    r = env["client"].post(
        "/api/link/webhooks",
        json={"name": "wh-a", "direction": "in"},
        headers=_auth(_ent_token(uid, "u-a")),
    )
    assert r.status_code == 200
    wid = r.json()["webhook_id"]
    assert lg._webhooks[wid].tenant_id == "tenant-a"


def test_register_system_row_tenant_identity(env):
    """身份派生租户键真落 ExternalSystem 行（写点归属）。"""
    lg = env["lg"]
    uid = _mk_user(env, "u-a", "tenant-a")
    r = env["client"].post(
        "/api/link/systems",
        json={"name": "sys-a", "type": "custom"},
        headers=_auth(_ent_token(uid, "u-a")),
    )
    assert r.status_code == 200
    sid = r.json()["system_id"]
    assert lg._systems[sid].tenant_id == "tenant-a"


def test_register_anonymous_default(env):
    lg = env["lg"]
    rw = env["client"].post("/api/link/webhooks", json={"name": "wh-x", "direction": "in"})
    rs = env["client"].post("/api/link/systems", json={"name": "sys-x", "type": "custom"})
    assert rw.status_code == rs.status_code == 200
    assert lg._webhooks[rw.json()["webhook_id"]].tenant_id == "default"
    assert lg._systems[rs.json()["system_id"]].tenant_id == "default"


def test_row_tenant_judgment_legacy_blank(env):
    """判法单一源：缺键/空白→'default'，显式键原样（两面同款）。"""
    lg = env["lg"]
    legacy_w = lg.WebhookEndpoint(
        webhook_id="wh-legacy", name="l", direction=lg.WebhookDirection.IN
    )
    assert lg._webhook_row_tenant(legacy_w) == "default"
    blank_w = lg.WebhookEndpoint(
        webhook_id="wh-blank", name="b", direction=lg.WebhookDirection.IN, tenant_id="   "
    )
    assert lg._webhook_row_tenant(blank_w) == "default"
    named_w = lg.WebhookEndpoint(
        webhook_id="wh-n", name="n", direction=lg.WebhookDirection.IN, tenant_id="tenant-a"
    )
    assert lg._webhook_row_tenant(named_w) == "tenant-a"
    legacy_s = lg.ExternalSystem(system_id="sys-legacy", name="l", type="custom")
    assert lg._system_row_tenant(legacy_s) == "default"
    blank_s = lg.ExternalSystem(system_id="sys-blank", name="b", type="custom", tenant_id="")
    assert lg._system_row_tenant(blank_s) == "default"
    named_s = lg.ExternalSystem(system_id="sys-n", name="n", type="custom", tenant_id="tenant-a")
    assert lg._system_row_tenant(named_s) == "tenant-a"


# ── list 恒过滤（读面）────────────────────────────────────────


def test_list_webhooks_scoped_cross_tenant(env):
    """三面互斥：A 只见 A 行、B 只见 B 行、匿名只见 default 行 + 加性 tenant_id 键。"""
    lg = env["lg"]
    _seed_webhook(lg, "wh-a1", "tenant-a")
    _seed_webhook(lg, "wh-a2", "tenant-a")
    _seed_webhook(lg, "wh-b1", "tenant-b")
    _seed_webhook(lg, "wh-d1", "default")
    ua = _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    ra = env["client"].get("/api/link/webhooks", headers=_auth(_ent_token(ua, "u-a")))
    rb = env["client"].get("/api/link/webhooks", headers=_auth(_ent_token(ub, "u-b")))
    rd = env["client"].get("/api/link/webhooks")
    assert ra.status_code == rb.status_code == rd.status_code == 200
    ids_a = {w["webhook_id"] for w in ra.json()["webhooks"]}
    ids_b = {w["webhook_id"] for w in rb.json()["webhooks"]}
    ids_d = {w["webhook_id"] for w in rd.json()["webhooks"]}
    assert ids_a == {"wh-a1", "wh-a2"}  # 修前：全量可见
    assert ids_b == {"wh-b1"}
    assert ids_d == {"wh-d1"}  # 匿名=default 面
    assert {w["tenant_id"] for w in ra.json()["webhooks"]} == {"tenant-a"}  # 加性键


def test_list_systems_scoped_cross_tenant(env):
    lg = env["lg"]
    _seed_system(lg, "sys-a1", "tenant-a")
    _seed_system(lg, "sys-b1", "tenant-b")
    _seed_system(lg, "sys-d1", "default")
    ua = _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    ra = env["client"].get("/api/link/systems", headers=_auth(_ent_token(ua, "u-a")))
    rb = env["client"].get("/api/link/systems", headers=_auth(_ent_token(ub, "u-b")))
    rd = env["client"].get("/api/link/systems")
    assert ra.status_code == rb.status_code == rd.status_code == 200
    assert {s["system_id"] for s in ra.json()["systems"]} == {"sys-a1"}
    assert {s["system_id"] for s in rb.json()["systems"]} == {"sys-b1"}
    assert {s["system_id"] for s in rd.json()["systems"]} == {"sys-d1"}
    assert {s["tenant_id"] for s in ra.json()["systems"]} == {"tenant-a"}


def test_list_bad_token_401(env):
    """坏 token 401 先于读（身份证明先于一切），注册面读不给坏身份任何行。"""
    r1 = env["client"].get("/api/link/webhooks", headers=_auth("not-a-jwt"))
    r2 = env["client"].get("/api/link/systems", headers=_auth("not-a-jwt"))
    assert r1.status_code == r2.status_code == 401


# ── /push by-ID 归属闸 ────────────────────────────────────────


def test_push_cross_tenant_gate_404_same_contract(env):
    """跨租户 /push 与缺行同契约同文案（禁差分泄露存在性）+ 零副作用。"""
    lg = env["lg"]
    wh = _seed_webhook(lg, "wh-a1", "tenant-a")
    ua = _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    r_cross = env["client"].post(
        "/api/link/push",
        json={"webhook_id": "wh-a1", "payload": {"x": 1}},
        headers=_auth(_ent_token(ub, "u-b")),
    )
    r_missing = env["client"].post(
        "/api/link/push",
        json={"webhook_id": "wh-gone", "payload": {"x": 1}},
        headers=_auth(_ent_token(ua, "u-a")),
    )
    assert r_cross.status_code == r_missing.status_code == 404
    assert r_cross.json() == r_missing.json()  # 同契约同文案
    assert env["sent"] == []  # 零出站
    assert wh.trigger_count == 0  # 行零改写
    assert wh.error_count == 0


def test_push_own_tenant_passes(env):
    lg = env["lg"]
    wh = _seed_webhook(lg, "wh-a1", "tenant-a")
    ua = _mk_user(env, "u-a", "tenant-a")
    r = env["client"].post(
        "/api/link/push",
        json={"webhook_id": "wh-a1", "payload": {"x": 1}},
        headers=_auth(_ent_token(ua, "u-a")),
    )
    assert r.status_code == 200
    assert len(env["sent"]) == 1  # 同租户真出站
    assert wh.trigger_count == 1


def test_push_anonymous_default_scope(env):
    """匿名='default' 契约：default 行放行、命名租户行与缺行同契约。"""
    lg = env["lg"]
    _seed_webhook(lg, "wh-d1", "default")
    _seed_webhook(lg, "wh-a1", "tenant-a")
    r_ok = env["client"].post("/api/link/push", json={"webhook_id": "wh-d1", "payload": {"x": 1}})
    r_no = env["client"].post("/api/link/push", json={"webhook_id": "wh-a1", "payload": {"x": 1}})
    assert r_ok.status_code == 200
    assert r_no.status_code == 404
    assert len(env["sent"]) == 1


# ── /broadcast 选靶恒过滤 ─────────────────────────────────────


def test_broadcast_all_scoped_own_only(env):
    """无 id 全量广播只投自己租户系统（修前：_systems.values() 全量=跨租户外泄）。"""
    lg = env["lg"]
    _seed_system(lg, "sys-a1", "tenant-a")
    _seed_system(lg, "sys-b1", "tenant-b")
    ua = _mk_user(env, "u-a", "tenant-a")
    r = env["client"].post(
        "/api/link/broadcast",
        json={"payload": {"x": 1}},
        headers=_auth(_ent_token(ua, "u-a")),
    )
    assert r.status_code == 200
    assert r.json()["targets"] == 1
    assert [s["source"] for s in env["sent"]] == ["sys-a1"]  # B 系统零投递


def test_broadcast_explicit_foreign_id_skipped_like_unknown(env):
    """显式 id 跨租户与未知 id 同契约（同款跳过，禁差分泄露存在性），自己的照投。"""
    lg = env["lg"]
    _seed_system(lg, "sys-a1", "tenant-a")
    _seed_system(lg, "sys-b1", "tenant-b")
    ua = _mk_user(env, "u-a", "tenant-a")
    r = env["client"].post(
        "/api/link/broadcast",
        json={"payload": {"x": 1}, "target_system_ids": ["sys-b1", "sys-gone", "sys-a1"]},
        headers=_auth(_ent_token(ua, "u-a")),
    )
    assert r.status_code == 200
    assert r.json()["targets"] == 1
    assert [s["source"] for s in env["sent"]] == ["sys-a1"]  # 跨租户/未知零出站
    assert [x["system_id"] for x in r.json()["results"]] == ["sys-a1"]  # 结果不含他租户


def test_broadcast_only_foreign_targets_400(env):
    lg = env["lg"]
    _seed_system(lg, "sys-b1", "tenant-b")
    ua = _mk_user(env, "u-a", "tenant-a")
    r = env["client"].post(
        "/api/link/broadcast",
        json={"payload": {"x": 1}, "target_system_ids": ["sys-b1"]},
        headers=_auth(_ent_token(ua, "u-a")),
    )
    assert r.status_code == 400
    assert env["sent"] == []  # 零出站


# ── 外部回调入口边界锁死（1a 同类，行为字节不动）────────────────


def test_heartbeat_external_entry_untouched(env):
    """heartbeat=外部系统回调入口：跨租户心跳照收=既有语义（回调入口非用户
    JWT 轴）字节不动；鉴权化已按 DEC-⑬ 与 incoming 统一拍板收口（行 token
    必持，缺/错 401 见 test_link_gateway_callback_auth）。"""
    lg = env["lg"]
    sys_row = _seed_system(lg, "sys-a1", "tenant-a")
    sys_row.callback_token = "tok-a1"
    _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    r = env["client"].post(
        "/api/link/systems/sys-a1/heartbeat",
        headers={**_auth(_ent_token(ub, "u-b")), "X-Callback-Token": "tok-a1"},
    )
    assert r.status_code == 200
    assert sys_row.heartbeat_count == 1
    assert sys_row.status == "online"


def test_incoming_external_entry_untouched(env):
    """incoming=外部 webhook 回调入口：匿名照收（事件 'default' 兜底=1a 语义）
    行为不变；鉴权化已按 DEC-⑬ 统一拍板收口（X-Callback-Token 必持）。"""
    lg = env["lg"]
    wh = _seed_webhook(lg, "wh-a1", "tenant-a")
    wh.callback_token = "tok-a1"
    r = env["client"].post(
        "/api/link/webhooks/wh-a1/incoming",
        json={"data": "probe"},
        headers={"X-Callback-Token": "tok-a1"},
    )
    assert r.status_code == 200
    assert r.json()["received"] is True


def test_stats_system_view_unchanged(env):
    """/stats 恒系统视图字节不变（⑨b-③-1c-③ 边界③：全局计数，另拍）。"""
    lg = env["lg"]
    _seed_webhook(lg, "wh-a1", "tenant-a")
    _seed_webhook(lg, "wh-b1", "tenant-b")
    r = env["client"].get("/api/link/gateway/stats")
    assert r.status_code == 200
    assert r.json()["webhooks"] == 2  # 全局计数（系统视图）
    assert r.json()["systems"] == 0
