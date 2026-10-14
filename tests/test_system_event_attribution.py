"""DEC-①① 1a/1b/1d 非 HTTP 面系统事件归属契约锁死（F3b-⑨b-③-1abd）。

DEC-① 裁决＝建议默认 A（2026-10-14 自决例外，pending-decisions.md ① 留档，
异议回「①a改B/①b改B/①d改B」即改道）：link receive_webhook（1a）/
soma_connector 4 点（1b）/ intrusion 中间件（1d）的事件是**系统事件**，
刻意不带 tenant_id → record() 'default' 兜底——即使请求携带真实租户用户
JWT 也落 'default'（外部 webhook 调用方 / 组件 / 安全拦截史均非用户身份轴）。

本文件锁死该语义两个方向（防未来无拍板反向改动）：
- 归属方向：真实租户身份不改变行归属（防"顺手接身份轴"改归属——1b 若拍 B
  需先定 components 归属模型，1d 若按请求身份派生=攻击者拦截记录挂受害者名下）
- 通道方向：外部 webhook 绝不因 Authorization 被 401 打断（防"补鉴权"回归）。

数据面全 tmp（TIMELINE_DB autouse + push_event._timeline 注入 + 身份库 tmp），
绝不写生产 timeline.db / 生产身份库。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import settings
from src.immune.intrusion import AttackType, ThreatEvent, ThreatLevel
from src.middleware.intrusion_middleware import IntrusionDetectionMiddleware
from src.nerve import event_bridge

ROOT = Path(__file__).resolve().parent.parent

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
    args: tuple = ()
    if event_type:
        sql += " WHERE event_type = ?"
        args = (event_type,)
    sql += " ORDER BY timestamp, event_id"
    out = [dict(r) for r in con.execute(sql, args)]
    con.close()
    return out


def _auth(token):
    return {"Authorization": "Bearer " + token} if token else {}


class _StubDetector:
    """IntrusionDetector 桩——middleware dispatch→push_event 接线是被测物，
    探测本体（真实 pattern 匹配）不进测试面；inspect_request 真被调用可证。"""

    def __init__(self, threats):
        self._threats = threats
        self.calls = []

    def inspect_request(self, **kw):
        self.calls.append(kw)
        return list(self._threats)


def _critical_threat():
    return ThreatEvent(
        threat_id="t-stub",
        attack_type=AttackType.SQL_INJECTION,
        threat_level=ThreatLevel.CRITICAL,
        source_ip="testclient",
        path="/probe",
        method="GET",
        detail="stub critical threat",
    )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """link + soma 两 router + 中间件 app + tmp 身份库/TimelineStore（绝不碰生产）。"""
    import src.api.link as link_api
    import src.api.soma_connector as soma_api
    from src.api import event_stream
    from src.api.soma_connector import ComponentRegistry
    from src.link.connector import IntegrationManager
    from src.timeline.store import TimelineStore

    # fresh 单例（修前=生产服务进程内存单例，测试绝不污染生产）
    monkeypatch.setattr(link_api, "manager", IntegrationManager())
    monkeypatch.setattr(soma_api, "registry", ComponentRegistry())

    # 身份库隔离（bind_identity 真源绝不碰生产）
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    ent_db = tmp_path / "enterprise.db"
    conn = sqlite3.connect(ent_db)
    conn.execute(ENTERPRISE_DDL)
    conn.commit()
    conn.close()

    # timeline 行归属真源=tmp（绝不写生产 timeline.db）
    tl_db = str(tmp_path / "timeline.db")
    store = TimelineStore(tl_db)
    monkeypatch.setattr(event_bridge.push_event, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    # 死端口：nerve publish 快速连接失败（被 push_event 自身吞掉），绝不打 live
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    event_stream._event_buffer.clear()

    app = FastAPI()
    app.include_router(link_api.router, prefix="/api/link")
    app.include_router(soma_api.router, prefix="/api/soma")

    # 中间件面：独立 app + 桩探测器（critical 威胁→403+push_event 真接线）
    mw_app = FastAPI()
    stub = _StubDetector([_critical_threat()])

    @mw_app.get("/probe")
    async def _probe():
        return {"ok": True}

    mw_app.add_middleware(IntrusionDetectionMiddleware, detector=stub)

    with TestClient(app) as c, TestClient(mw_app) as mwc:
        yield {
            "client": c,
            "mw_client": mwc,
            "mw_stub": stub,
            "tl_db": tl_db,
            "ent_db": ent_db,
            "link_api": link_api,
            "soma_api": soma_api,
        }
    event_stream._event_buffer.clear()


def _mk_user(env, username, tenant):
    return _insert_ent_user(env["ent_db"], username, tenant)


def _create_connector(env, name="wh1"):
    r = env["client"].post("/api/link/connectors", json={"name": name, "type": "webhook_in"})
    assert r.status_code == 200
    return env["link_api"].manager.list_connectors()[0]["connector_id"]


# ── 1a link receive_webhook（DEC-① 1a：外部 webhook＝系统事件） ─────────


def test_webhook_tenant_user_token_event_default(env):
    """真实租户用户 JWT 在场，webhook_received 仍落 'default'（归属方向锁死）。"""
    uid = _mk_user(env, "u-a", "tenant-a")
    cid = _create_connector(env)
    r = env["client"].post(
        f"/api/link/webhook/{cid}",
        json={"hello": "world"},
        headers=_auth(_ent_token(uid, "u-a")),
    )
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "webhook_received")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"


def test_webhook_no_token_event_default(env):
    """匿名第三方 webhook 照旧 200 + 'default'（回归面零变化）。"""
    cid = _create_connector(env)
    r = env["client"].post(f"/api/link/webhook/{cid}", json={"hello": "world"})
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "webhook_received")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"


def test_webhook_garbage_auth_not_401_default(env):
    """通道方向锁死：垃圾 Authorization 绝不 401（接身份轴=打断外部 webhook）。"""
    cid = _create_connector(env)
    r = env["client"].post(
        f"/api/link/webhook/{cid}",
        json={"hello": "world"},
        headers={"Authorization": "Bearer some-third-party-secret"},
    )
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "webhook_received")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"


# ── 1b soma_connector 4 点（DEC-① 1b：组件＝系统资源） ─────────────────


def _register(env, comp_id, headers=None):
    return env["client"].post(
        "/api/soma/register",
        json={
            "component_id": comp_id,
            "name": comp_id,
            "component_type": "collector",
            "version": "0.1.0",
        },
        headers=headers or {},
    )


def test_soma_register_tenant_token_event_default(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    r = _register(env, "comp-a", _auth(_ent_token(uid, "u-a")))
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "component_registered")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"


def test_soma_push_tenant_token_event_default(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    token = _ent_token(uid, "u-a")
    reg = _register(env, "comp-push", _auth(token))
    assert reg.status_code == 200
    secret = reg.json()["secret_token"]
    r = env["client"].post(
        "/api/soma/push",
        json={"data_type": "event", "payload": {"k": "v"}},
        headers={
            "X-Component-Id": "comp-push",
            "X-Component-Token": secret,
            **_auth(token),
        },
    )
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "data_pushed")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"


def test_soma_unregister_and_error_event_default(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    token = _ent_token(uid, "u-a")
    assert _register(env, "comp-u", _auth(token)).status_code == 200
    r = env["client"].delete("/api/soma/components/comp-u", headers=_auth(token))
    assert r.status_code == 200
    r = env["client"].post("/api/soma/error", params={"component_id": "comp-u", "error": "boom"})
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "component_unregistered") + _rows(env["tl_db"], "component_error")
    assert len(rows) == 2
    assert all(row["tenant_id"] == "default" for row in rows)


def test_soma_dual_tenant_both_default(env):
    ua = _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    assert _register(env, "comp-a", _auth(_ent_token(ua, "u-a"))).status_code == 200
    assert _register(env, "comp-b", _auth(_ent_token(ub, "u-b"))).status_code == 200
    rows = _rows(env["tl_db"], "component_registered")
    assert len(rows) == 2
    assert {row["tenant_id"] for row in rows} == {"default"}


# ── 1d intrusion 中间件（DEC-① 1d：安全拦截史归系统） ──────────────────


def test_intrusion_block_tenant_token_event_default(env):
    """携带真实租户用户 JWT 的被拦截请求，request_blocked 仍落 'default'
    （不按请求身份派生——攻击者拦截记录挂受害者名下反而失真）。"""
    uid = _mk_user(env, "u-a", "tenant-a")
    r = env["mw_client"].get("/probe", headers=_auth(_ent_token(uid, "u-a")))
    assert r.status_code == 403
    rows = _rows(env["tl_db"], "request_blocked")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"


def test_intrusion_block_no_token_event_default(env):
    r = env["mw_client"].get("/probe")
    assert r.status_code == 403
    rows = _rows(env["tl_db"], "request_blocked")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"


def test_intrusion_pass_no_event(env):
    """非威胁请求零事件（只有拦截才 emit——既有语义回归锁死）。"""
    env["mw_stub"]._threats = []
    r = env["mw_client"].get("/probe", headers=_auth("any"))
    assert r.status_code == 200
    assert _rows(env["tl_db"]) == []
    assert env["mw_stub"].calls  # 探测器真被调用（非静默短路）
