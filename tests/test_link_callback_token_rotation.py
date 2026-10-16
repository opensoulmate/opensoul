"""DEC-⑲-② link.py callback_token 轮换·撤销端点。

DEC-⑲-① 留档候选「token 轮换·撤销端点（⑲-②，可另拍）」：凭据丢失恢复路径
（⑲-① 边界①「凭据丢失=重注册新 connector 或 ⑲-② 轮换端点」）+凭据生命周期
收口（修前 create 签发后无任何轮换/撤销面，泄漏凭据只能整行重注册）。

语义（收口锁死）：
- 轮换（POST /connectors/{id}/callback-token/rotate）=签发新凭据并即刻作废
  旧凭据（行 token 被替换，旧明文立即 401）；新明文只回响应一次（register
  同语义，get/list 面不回吐）。
- 撤销（DELETE /connectors/{id}/callback-token）=置空行 token，X-Callback-Token
  证明 fail-closed 拒；只清 token 证明面，X-Signature HMAC=secret 等效证明
  独立轴不动（双证取或 DEC-⑲ 既定，secret 兼出站签名）。
- 撤销后 rotate 可重新签发（免重注册）。
- 写面 bind_identity：坏 token 401 先于读写零副作用。

覆盖：轮换签发+旧证作废 1 / 明文不回吐 1 / 撤销 fail-closed 1 / HMAC 独立
轴 1 / 撤销后再签发 1 / 缺行 404×2 / 坏 token 401 零副作用 1 / 事件留痕 2 /
轮换泄漏半径 1 / manager 单元 1。
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


# ── 轮换：签发新证+旧证即刻作废 ─────────────────────────────────────────


def test_rotate_issues_new_token_and_invalidates_old(env):
    data = _register(env)
    cid, old = data["connector_id"], data["callback_token"]
    r = env["client"].post(f"/api/link/connectors/{cid}/callback-token/rotate")
    assert r.status_code == 200
    body = r.json()
    assert body["rotated"] is True and body["connector_id"] == cid
    new = body["callback_token"]
    assert new and len(new) == 32 and new != old
    assert env["mgr"].get_connector(cid).callback_token == new
    # 旧明文即刻失效：401 零副作用（事件不落/trigger_count 不动）
    r_old = _post(env, cid, headers={"X-Callback-Token": old})
    assert r_old.status_code == 401
    assert env["mgr"].get_connector(cid).trigger_count == 0
    assert _rows(env["tl_db"], "webhook_received") == []
    # 新明文放行
    r_new = _post(env, cid, headers={"X-Callback-Token": new})
    assert r_new.status_code == 200
    assert env["mgr"].get_connector(cid).trigger_count == 1


def test_rotate_plaintext_only_in_response(env):
    data = _register(env)
    cid = data["connector_id"]
    new = (
        env["client"]
        .post(f"/api/link/connectors/{cid}/callback-token/rotate")
        .json()["callback_token"]
    )
    got = env["client"].get(f"/api/link/connectors/{cid}").json()
    assert got["has_callback_token"] is True
    assert "callback_token" not in got
    assert new not in str(got)
    listed = env["client"].get("/api/link/connectors").json()["connectors"]
    assert listed[0]["has_callback_token"] is True
    assert "callback_token" not in listed[0]
    assert new not in str(listed)


# ── 撤销：fail-closed + 独立轴不动 + 可再签发 ────────────────────────────


def test_revoke_fails_closed(env):
    data = _register(env)
    cid, tok = data["connector_id"], data["callback_token"]
    r = env["client"].delete(f"/api/link/connectors/{cid}/callback-token")
    assert r.status_code == 200
    assert r.json() == {"connector_id": cid, "revoked": True, "has_callback_token": False}
    row = env["mgr"].get_connector(cid)
    assert row.callback_token == ""
    got = env["client"].get(f"/api/link/connectors/{cid}").json()
    assert got["has_callback_token"] is False
    r2 = _post(env, cid, headers={"X-Callback-Token": tok})
    assert r2.status_code == 401  # fail-closed：空行 token 无证可验=拒
    assert row.trigger_count == 0
    assert _rows(env["tl_db"], "webhook_received") == []


def test_revoke_keeps_hmac_axis(env):
    data = _register(env, secret="s3cret")
    cid = data["connector_id"]
    assert env["client"].delete(f"/api/link/connectors/{cid}/callback-token").status_code == 200
    body = b'{"hello": "world"}'
    r = env["client"].post(
        f"/api/link/webhook/{cid}",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": _sign("s3cret", body)},
    )
    assert r.status_code == 200  # secret HMAC 等效证明独立轴不动（DEC-⑲ 双证取或）


def test_rotate_after_revoke_reissues(env):
    data = _register(env)
    cid = data["connector_id"]
    assert env["client"].delete(f"/api/link/connectors/{cid}/callback-token").status_code == 200
    r = env["client"].post(f"/api/link/connectors/{cid}/callback-token/rotate")
    assert r.status_code == 200
    new = r.json()["callback_token"]
    assert new and len(new) == 32
    assert env["client"].get(f"/api/link/connectors/{cid}").json()["has_callback_token"] is True
    assert _post(env, cid, headers={"X-Callback-Token": new}).status_code == 200


# ── 契约面：缺行 404 / 坏 token 401 先于写 ──────────────────────────────


def test_missing_connector_404(env):
    assert (
        env["client"].post("/api/link/connectors/conn-none/callback-token/rotate").status_code
        == 404
    )
    assert env["client"].delete("/api/link/connectors/conn-none/callback-token").status_code == 404


def test_bad_token_401_before_write_zero_side_effect(env):
    data = _register(env)
    cid, old = data["connector_id"], data["callback_token"]
    bad = {"Authorization": "Bearer garbage-token"}
    assert (
        env["client"]
        .post(f"/api/link/connectors/{cid}/callback-token/rotate", headers=bad)
        .status_code
        == 401
    )
    assert (
        env["client"].delete(f"/api/link/connectors/{cid}/callback-token", headers=bad).status_code
        == 401
    )
    # 零副作用：行 token 一字不动
    assert env["mgr"].get_connector(cid).callback_token == old


# ── 事件留痕 + 泄漏半径 + 单元 ──────────────────────────────────────────


def test_rotate_revoke_emit_events(env):
    data = _register(env)
    cid = data["connector_id"]
    env["client"].post(f"/api/link/connectors/{cid}/callback-token/rotate")
    env["client"].delete(f"/api/link/connectors/{cid}/callback-token")
    rotated = _rows(env["tl_db"], "connector_token_rotated")
    revoked = _rows(env["tl_db"], "connector_token_revoked")
    assert len(rotated) == 1 and cid in str(rotated[0]["detail"])
    assert len(revoked) == 1 and cid in str(revoked[0]["detail"])


def test_rotation_leak_radius_per_connector(env):
    a = _register(env, name="wh-a")
    b = _register(env, name="wh-b")
    b_tok = b["callback_token"]
    new_a = (
        env["client"]
        .post(f"/api/link/connectors/{a['connector_id']}/callback-token/rotate")
        .json()["callback_token"]
    )
    assert new_a != b_tok
    assert env["mgr"].get_connector(b["connector_id"]).callback_token == b_tok
    assert _post(env, b["connector_id"], headers={"X-Callback-Token": b_tok}).status_code == 200


def test_manager_units(env):
    mgr = env["mgr"]
    assert mgr.rotate_callback_token("conn-none") is None
    assert mgr.revoke_callback_token("conn-none") is False
    data = _register(env)
    cid = data["connector_id"]
    assert mgr.rotate_callback_token(cid)
    assert mgr.revoke_callback_token(cid) is True
    assert mgr.get_connector(cid).callback_token == ""
