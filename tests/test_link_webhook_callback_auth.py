"""DEC-⑲ link.py receive_webhook token 化——OpenLink connector webhook 入口凭据门。

修前（DEC-① 1a 契约+live 实证）：/api/link/webhook/{connector_id} 零凭据门
（connector_id 即凭证、secret HMAC 可选=不配即全开）——任意人可伪造 webhook
注入 timeline 事件/改 trigger_count/发通知（DEC-⑬ heartbeat/incoming 同型
伪造面）。

收口（DEC-⑲ 自决取 B，DEC-⑬ 同轴同哲学）：create_connector 恒签发
callback_token（明文只回注册响应一次，get/list 不回吐）+ receive_webhook
必持证——X-Callback-Token=注册签发凭据（F3b-④/⑤ 同族），或既有 X-Signature
HMAC=connector.secret 等效证明；两证全无/全错=401 零副作用（record_event
不落、trigger_count 不动、零通知）；fail-closed 无证可验=拒。

覆盖：签发 2 / 不回吐 1 / 放行 1 / 缺证·错证 401 零副作用 2 / HMAC 等效
证明+OR 语义 3 / Authorization 通道锁死 1 / fail-closed 空行 token 1 /
404·503 契约先于凭据 2 / 静态接线 1。
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.link.connector import IntegrationManager
from src.nerve import event_bridge


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


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """link router + fresh IntegrationManager + tmp TimelineStore（绝不碰生产）。"""
    import src.api.link as link_api
    from src.timeline.store import TimelineStore

    monkeypatch.setattr(link_api, "manager", IntegrationManager())

    tl_db = str(tmp_path / "timeline.db")
    store = TimelineStore(tl_db)
    monkeypatch.setattr(event_bridge.push_event, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    app = FastAPI()
    app.include_router(link_api.router, prefix="/api/link")
    client = TestClient(app)
    yield {"client": client, "mgr": link_api.manager, "tl_db": tl_db}
    from src.api import event_stream

    event_stream._event_buffer.clear()


def _register(env, name="wh1", secret=""):
    r = env["client"].post(
        "/api/link/connectors",
        json={"name": name, "type": "webhook_in", "secret": secret},
    )
    assert r.status_code == 200
    return r.json()


def _post(env, cid, headers=None, body=None):
    return env["client"].post(
        f"/api/link/webhook/{cid}",
        json=body if body is not None else {"hello": "world"},
        headers=headers or {},
    )


# ── 签发（register 签发，恒签发=生产不可达"无证"行） ─────────────────────


def test_register_issues_callback_token(env):
    data = _register(env)
    tok = data["callback_token"]
    assert tok and len(tok) == 32
    row = env["mgr"].get_connector(data["connector_id"])
    assert row is not None and row.callback_token == tok
    # 两个 connector 各持各证（泄漏半径单行，F3b-④/⑤ 同款）
    d2 = _register(env, name="wh2")
    assert d2["callback_token"] != tok


def test_token_passes_and_lands_event(env):
    data = _register(env)
    r = _post(env, data["connector_id"], headers={"X-Callback-Token": data["callback_token"]})
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "webhook_received")
    assert len(rows) == 1
    assert env["mgr"].get_connector(data["connector_id"]).trigger_count == 1


def test_list_get_do_not_leak_token(env):
    data = _register(env)
    cid = data["connector_id"]
    got = env["client"].get(f"/api/link/connectors/{cid}").json()
    assert got["has_callback_token"] is True
    assert "callback_token" not in got
    assert data["callback_token"] not in str(got)
    listed = env["client"].get("/api/link/connectors").json()["connectors"]
    assert listed[0]["has_callback_token"] is True
    assert "callback_token" not in listed[0]
    assert data["callback_token"] not in str(listed)


# ── 缺/错证=401 零副作用 ─────────────────────────────────────────────────


def test_missing_credential_401_zero_side_effect(env):
    data = _register(env)
    r = _post(env, data["connector_id"])
    assert r.status_code == 401
    assert "callback credential missing" in r.json()["detail"]
    # 零副作用硬证：事件不落、计数一字不动
    assert _rows(env["tl_db"], "webhook_received") == []
    assert env["mgr"].get_connector(data["connector_id"]).trigger_count == 0


def test_wrong_token_401_zero_side_effect(env):
    data = _register(env)
    r = _post(env, data["connector_id"], headers={"X-Callback-Token": "wrong"})
    assert r.status_code == 401
    assert "invalid callback credential" in r.json()["detail"]
    assert _rows(env["tl_db"], "webhook_received") == []
    assert env["mgr"].get_connector(data["connector_id"]).trigger_count == 0


# ── HMAC 等效证明（发方持 secret 即持证）+ OR 语义 ────────────────────────


def test_hmac_signature_equivalent_proof(env):
    data = _register(env, secret="s3cret")
    body = b'{"hello": "world"}'
    r = env["client"].post(
        f"/api/link/webhook/{data['connector_id']}",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": _sign("s3cret", body)},
    )
    assert r.status_code == 200
    assert len(_rows(env["tl_db"], "webhook_received")) == 1


def test_or_semantics_bad_signature_valid_token_ok(env):
    """两证取或：坏签名+真 token 放行（token 是独立等效证明）。"""
    data = _register(env, secret="s3cret")
    r = _post(
        env,
        data["connector_id"],
        headers={"X-Callback-Token": data["callback_token"], "X-Signature": "sha256=bad"},
    )
    assert r.status_code == 200


def test_bad_signature_without_token_401(env):
    data = _register(env, secret="s3cret")
    body = b'{"hello": "world"}'
    r = env["client"].post(
        f"/api/link/webhook/{data['connector_id']}",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": "sha256=bad"},
    )
    assert r.status_code == 401
    assert _rows(env["tl_db"], "webhook_received") == []


# ── 通道锁死 / fail-closed / 契约序 ──────────────────────────────────────


def test_garbage_authorization_never_401_with_credential(env):
    """DEC-① 1a 通道锁死保留：垃圾 Authorization 绝不是 401 来源。"""
    data = _register(env)
    r = _post(
        env,
        data["connector_id"],
        headers={
            "Authorization": "Bearer some-third-party-secret",
            "X-Callback-Token": data["callback_token"],
        },
    )
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "webhook_received")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"  # 持证匿名发方=系统事件照旧


def test_fail_closed_empty_row_token(env):
    """fail-closed：行上无证可验=拒（create 恒签发=生产不可达态，单元面锁死）。"""
    data = _register(env)
    assert env["mgr"].update_connector(data["connector_id"], callback_token="")
    for headers in (
        {"X-Callback-Token": "anything"},
        {"X-Callback-Token": ""},
        {},
    ):
        r = _post(env, data["connector_id"], headers=headers)
        assert r.status_code == 401
    assert _rows(env["tl_db"], "webhook_received") == []


def test_404_contract_before_credential(env):
    """DEC-⑬ 边界⑥ 同序：404/503 契约先于凭据检查（既有契约不变优先）。"""
    r = _post(env, "conn-nonexist")
    assert r.status_code == 404


def test_503_contract_before_credential(env):
    data = _register(env)
    assert env["mgr"].update_connector(data["connector_id"], status="paused")
    r = _post(env, data["connector_id"])
    assert r.status_code == 503


# ── 静态接线普查 ─────────────────────────────────────────────────────────


def test_static_wiring_single_source():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    conn_src = (root / "src" / "link" / "connector.py").read_text()
    api_src = (root / "src" / "api" / "link.py").read_text()
    svc_src = (root / "src" / "services" / "callback_token.py").read_text()
    lg_src = (root / "src" / "api" / "link_gateway.py").read_text()
    # register 恒签发唯一产生点
    assert conn_src.count("callback_token=uuid.uuid4().hex") == 1
    # 判定语义单一源：两消费面共用 services.callback_token（禁两套判法漂移）
    assert svc_src.count("def callback_token_ok") == 1
    assert "callback_token_ok" in api_src
    assert "from src.services.callback_token import callback_token_ok" in lg_src
    assert "return callback_token_ok(row_token, presented)" in lg_src
