"""unified events 子搜索存储层加轴测试（DEC-⑦-①：events 面最小件）。

修前（HEAD 源码实证）：_search_events 读 event_stream._event_buffer 全局内存
缓冲零租户过滤=任意租户可见全缓冲（与 timeline 面 ③c-2① 已收的查询过滤不对称
=跨租户泄露）。收口=_event_row_tenant 单一判法（事件自带键、缺失/空白→'default'
兜底，timeline record() 同款语义）+ _search_events 恒按派生租户过滤（limit 只计
可见命中）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB），_event_buffer 保存/恢复，绝不写生产库。
"""

import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)

_MISSING = object()


def _seed_user(db_path: Path, uid: str, username: str, tenant: str = "default") -> str:
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
        )
        c.commit()
    return uid


def _token(uid: str) -> dict:
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


def _ev(title: str, tenant=_MISSING, organ: str = "soul", etype: str = "note") -> dict:
    ev = {
        "id": f"evt-{uuid.uuid4().hex[:8]}",
        "summary": title,
        "organ": organ,
        "type": etype,
        "emoji": "⚡",
        "collected_at": 1000.0,
    }
    if tenant is not _MISSING:
        ev["tenant_id"] = tenant
    return ev


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + 真 _event_buffer（保存/恢复）+ 真 router 真 bind_identity。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    from src.api.event_stream import _event_buffer

    saved = list(_event_buffer)
    _event_buffer.clear()

    import src.api.search as search_api

    app = FastAPI()
    app.include_router(search_api.router, prefix="/api/search")
    client = TestClient(app)

    user_tenant_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "bob-default", "default")

    yield {
        "client": client,
        "buffer": _event_buffer,
        "user_tenant_a": user_tenant_a,
        "user_default": user_default,
    }

    _event_buffer.clear()
    _event_buffer.extend(saved)


def _titles(client, headers=None, params=None):
    p = {"q": "probe", "sources": "events", "limit": 10}
    p.update(params or {})
    r = client.get("/api/search/unified", params=p, headers=headers or {})
    assert r.status_code == 200
    return [m["title"] for m in r.json()["by_source"]["events"]]


# ── 纯函数：行租户键单一判法 ─────────────────────────────────────────


def test_event_row_tenant_fallback_forms():
    from src.api.search import _event_row_tenant

    assert _event_row_tenant({}) == "default"
    assert _event_row_tenant({"tenant_id": None}) == "default"
    assert _event_row_tenant({"tenant_id": ""}) == "default"
    assert _event_row_tenant({"tenant_id": "   "}) == "default"
    assert _event_row_tenant({"tenant_id": "tenant-a"}) == "tenant-a"


def test_event_row_tenant_single_source():
    """判法单一源锁死：search 过滤与本函数同一判法（禁消费点另发明）。"""
    import inspect

    from src.api import search as search_api

    src = inspect.getsource(search_api._search_events)
    assert "_event_row_tenant(ev)" in src
    assert 'ev.get("tenant_id")' not in src


# ── 租户过滤行为 ───────────────────────────────────────────────────


def test_anonymous_sees_default_and_unkeyed_only(env):
    """匿名（派生 'default'）只见 unkeyed+default+空白键行，跨租户行不可见。

    修前（零过滤）同一缓冲五条全可见=跨租户泄露硬反证（控制组同测试必炸）。
    """
    buf = env["buffer"]
    buf.append(_ev("probe-unkeyed"))
    buf.append(_ev("probe-default-keyed", "default"))
    buf.append(_ev("probe-blank-keyed", "   "))
    buf.append(_ev("probe-tenant-a-secret", "tenant-a"))
    buf.append(_ev("probe-tenant-b-secret", "tenant-b"))

    got = _titles(env["client"])
    assert got == ["probe-blank-keyed", "probe-default-keyed", "probe-unkeyed"]
    assert "probe-tenant-a-secret" not in got
    assert "probe-tenant-b-secret" not in got


def test_tenant_a_sees_only_own_rows(env):
    """tenant-a 派生身份只见自己租户行——unkeyed/default 行同样不可见。"""
    buf = env["buffer"]
    buf.append(_ev("probe-unkeyed"))
    buf.append(_ev("probe-default-keyed", "default"))
    buf.append(_ev("probe-tenant-a-secret", "tenant-a"))
    buf.append(_ev("probe-tenant-b-secret", "tenant-b"))

    got = _titles(env["client"], headers=_token(env["user_tenant_a"]))
    assert got == ["probe-tenant-a-secret"]


def test_default_tenant_user_same_as_anonymous(env):
    """default 租户认证用户与匿名同一可见集（'default' 契约延续）。"""
    buf = env["buffer"]
    buf.append(_ev("probe-unkeyed"))
    buf.append(_ev("probe-default-keyed", "default"))
    buf.append(_ev("probe-tenant-a-secret", "tenant-a"))

    anon = _titles(env["client"])
    authed = _titles(env["client"], headers=_token(env["user_default"]))
    assert authed == anon == ["probe-default-keyed", "probe-unkeyed"]


def test_tenant_axis_derived_not_self_reported(env):
    """tenant 轴来自 token 派生——user_id 自报不影响事件可见域。"""
    buf = env["buffer"]
    buf.append(_ev("probe-default-keyed", "default"))
    buf.append(_ev("probe-tenant-a-secret", "tenant-a"))

    got = _titles(
        env["client"],
        headers=_token(env["user_tenant_a"]),
        params={"user_id": "default"},
    )
    assert got == ["probe-tenant-a-secret"]


def test_limit_counts_only_visible_rows(env):
    """limit 只计可见命中——跨租户行不占名额（修前新旧行混排会吃掉名额）。"""
    buf = env["buffer"]
    # append 顺序=旧→新，reversed() 遍历=新→旧：跨租户 2 条最新（遍历首两位）
    buf.append(_ev("probe-default-3", "default"))
    buf.append(_ev("probe-default-2", "default"))
    buf.append(_ev("probe-default-1", "default"))
    buf.append(_ev("probe-tenant-a-2", "tenant-a"))
    buf.append(_ev("probe-tenant-a-1", "tenant-a"))

    got = _titles(env["client"], params={"limit": 2})
    assert got == ["probe-default-1", "probe-default-2"]


def test_match_semantics_unchanged(env):
    """查询匹配语义零变化（summary/organ/type 三面）且只在本租户内匹配。"""
    buf = env["buffer"]
    buf.append(_ev("alpha summary hit", "default", organ="soul"))
    buf.append(_ev("no hit here", "default", organ="vein", etype="beta"))
    buf.append(_ev("gamma summary", "default", organ="delta-organ", etype="note"))
    buf.append(_ev("alpha secret", "tenant-a"))

    assert _titles(env["client"], params={"q": "alpha"}) == ["alpha summary hit"]
    assert _titles(env["client"], params={"q": "beta"}) == ["no hit here"]
    assert _titles(env["client"], params={"q": "delta"}) == ["gamma summary"]


def test_result_shape_keys_unchanged(env):
    """返回形状逐键不变（source/icon/title/snippet/timestamp/organ）。"""
    buf = env["buffer"]
    buf.append(_ev("probe-shape", "default"))

    r = env["client"].get("/api/search/unified", params={"q": "probe", "sources": "events"})
    (row,) = r.json()["by_source"]["events"]
    assert set(row.keys()) == {"source", "icon", "title", "snippet", "timestamp", "organ"}
    assert row["source"] == "events"


def test_unified_sources_events_group_filtered(env):
    """unified sources=events 分组真消费过滤（接线证明非直接调内部函数）。"""
    buf = env["buffer"]
    buf.append(_ev("probe-unkeyed"))
    buf.append(_ev("probe-tenant-a-secret", "tenant-a"))

    r = env["client"].get("/api/search/unified", params={"q": "probe", "sources": "events"})
    data = r.json()
    assert [m["title"] for m in data["by_source"]["events"]] == ["probe-unkeyed"]
    assert data["query"] == "probe"


def test_single_tenant_local_contract_unchanged(env):
    """单租户本地契约：缓冲全 unkeyed 时匿名全见（既有行为零变化）。"""
    buf = env["buffer"]
    buf.append(_ev("probe-oldest"))
    buf.append(_ev("probe-newest"))

    assert _titles(env["client"]) == ["probe-newest", "probe-oldest"]
