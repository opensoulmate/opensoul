"""unified echo 子搜索存储层加轴测试（DEC-⑦-②：echo 面最小件）。

修前（HEAD 源码实证）：①_search_echo_messages 读消息历史零租户过滤=任意租户
可见全部消息（与 timeline 面 ③c-2① / events 面 DEC-⑦-① 已收的查询过滤不对称
=跨租户泄露）②其读真源=函数内 new MessageDispatcher() 空历史=echo 子搜索
恒空集（wired-but-not-functional，加轴前必须先能读到真历史否则过滤不可观测）。
收口=message_row_tenant 单一判法（消息自带键、缺失/空白→'default' 兜底，
DEC-⑦-① _event_row_tenant 同款语义；写读同一判法）+history(tenant_id=) 恒按
行租户键过滤（None=系统视图既有调用面字节不变；过滤先于截断=limit 只计可见
命中）+_search_echo_messages 恒按派生租户过滤+读真源=src.api.echo 模块单例
dispatcher 共享历史。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/tmp TemplateEngine），dispatcher
每用例 fresh monkeypatch（内存面零生产持久化），绝不写生产库。
"""

import inspect
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


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + fresh dispatcher（monkeypatch src.api.echo.dispatcher）
    + 真 router 真 bind_identity。search 与 echo 写面读同一真源实例。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.echo as echo_api
    from src.echo.dispatcher import MessageDispatcher
    from src.echo.templates import TemplateEngine

    fresh = MessageDispatcher()
    monkeypatch.setattr(echo_api, "dispatcher", fresh)
    monkeypatch.setattr(echo_api, "template_engine", TemplateEngine(db_path=str(tmp_path / "t.db")))

    import src.api.search as search_api

    app = FastAPI()
    app.include_router(search_api.router, prefix="/api/search")
    app.include_router(echo_api.router, prefix="/api/echo")
    client = TestClient(app)

    user_tenant_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "bob-default", "default")

    yield {
        "client": client,
        "disp": fresh,
        "user_tenant_a": user_tenant_a,
        "user_default": user_default,
    }


def _titles(client, headers=None, params=None):
    p = {"q": "probe", "sources": "echo", "limit": 10}
    p.update(params or {})
    r = client.get("/api/search/unified", params=p, headers=headers or {})
    assert r.status_code == 200
    return [m["title"] for m in r.json()["by_source"]["echo"]]


def _seed_rows(disp, tenant_a: int = 2, tenant_default: int = 2):
    """直写存储层行（行键 setattr 直写，写点归属另有专测）：tenant-a 行
    probe-ta-N、default 行 probe-td-N。控制组纪律：seed 不依赖新 API 签名，
    让旧代码失败暴露在行为断言而非 fixture TypeError。"""
    import time

    from src.echo.dispatcher import Channel, Message

    def _row(title: str, body: str, tenant: str):
        msg = Message(
            msg_id=f"seed-{uuid.uuid4().hex[:8]}",
            channel=Channel.CONSOLE,
            title=title,
            content=body,
            timestamp=time.time(),
        )
        setattr(msg, "tenant_id", tenant)
        disp._history.append(msg)

    for i in range(tenant_a):
        _row(f"probe-ta-{i}", "ta body", "tenant-a")
    for i in range(tenant_default):
        _row(f"probe-td-{i}", "td body", "default")


# ── 纯函数：行租户键单一判法 ─────────────────────────────────────────


def test_message_row_tenant_fallback_forms():
    from src.echo.dispatcher import Channel, Message, message_row_tenant

    assert message_row_tenant(None) == "default"
    assert message_row_tenant("") == "default"
    assert message_row_tenant("   ") == "default"
    assert message_row_tenant("tenant-a") == "tenant-a"
    blank_msg = Message(
        msg_id="m", channel=Channel.CONSOLE, title="", content="", timestamp=0.0, tenant_id=""
    )
    assert message_row_tenant(blank_msg) == "default"


def test_message_row_tenant_single_source():
    """判法单一源锁死：history 过滤与 send 落行同一判法（禁消费点另发明）。"""
    from src.echo.dispatcher import MessageDispatcher

    hist_src = inspect.getsource(MessageDispatcher.history)
    assert "message_row_tenant(e)" in hist_src
    assert "e.tenant_id ==" not in hist_src
    send_src = inspect.getsource(MessageDispatcher.send)
    assert "message_row_tenant(tenant_id)" in send_src


# ── 写点行归属（真 echo API 真 bind_identity）─────────────────────────


def test_send_api_row_attribution_derived(env):
    """POST /api/echo/send：token 派生租户落行（修前恒 'default'）、匿名 'default'。"""
    client, disp = env["client"], env["disp"]
    r = client.post(
        "/api/echo/send",
        json={"channel": "console", "title": "probe-api-a", "content": "b"},
        headers=_token(env["user_tenant_a"]),
    )
    assert r.status_code == 200
    r2 = client.post(
        "/api/echo/send",
        json={"channel": "console", "title": "probe-api-anon", "content": "b"},
    )
    assert r2.status_code == 200
    keys = {m.title: m.tenant_id for m in disp._history}
    assert keys["probe-api-a"] == "tenant-a"
    assert keys["probe-api-anon"] == "default"


def test_broadcast_api_row_attribution_and_auth(env):
    """POST /api/echo/broadcast：修前零身份轴（广播恒落 'default'）→派生落行；
    坏 token 401 先于任何发送（F3b-⑤ 纪律）。"""
    client, disp = env["client"], env["disp"]
    r = client.post(
        "/api/echo/broadcast",
        json={"title": "probe-bc", "content": "b"},
        headers=_token(env["user_tenant_a"]),
    )
    assert r.status_code == 200
    rows = [m for m in disp._history if m.title == "probe-bc"]
    assert rows and all(m.tenant_id == "tenant-a" for m in rows)

    before = len(disp._history)
    bad = client.post(
        "/api/echo/broadcast",
        json={"title": "probe-bc-bad", "content": "b"},
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert bad.status_code == 401
    assert len(disp._history) == before  # 零消息落库


def test_send_direct_blank_fallback(env):
    """系统发送方直调（notifications/healer 同款）缺省/空白→'default' 兜底
    （DEC-① 系统事件语义，键派生故障绝不吃掉落库）。"""
    from src.echo.dispatcher import Channel

    disp = env["disp"]
    disp.send(Channel.CONSOLE, "probe-sys", "b")
    disp.send(Channel.CONSOLE, "probe-sys-blank", "b", tenant_id="   ")
    keys = {m.title: m.tenant_id for m in disp._history}
    assert keys["probe-sys"] == "default"
    assert keys["probe-sys-blank"] == "default"


# ── 租户过滤行为（真 unified 真 bind_identity）────────────────────────


def test_anonymous_sees_default_and_unkeyed_only(env):
    from src.echo.dispatcher import Channel

    client, disp = env["client"], env["disp"]
    _seed_rows(disp)
    disp.send(Channel.CONSOLE, "probe-unkeyed", "b")
    titles = _titles(client)
    assert sorted(titles) == ["probe-td-0", "probe-td-1", "probe-unkeyed"]


def test_tenant_a_sees_only_own_rows(env):
    client, disp = env["client"], env["disp"]
    _seed_rows(disp)
    titles = _titles(client, headers=_token(env["user_tenant_a"]))
    assert sorted(titles) == ["probe-ta-0", "probe-ta-1"]


def test_default_tenant_user_same_as_anonymous(env):
    client, disp = env["client"], env["disp"]
    _seed_rows(disp)
    anon = sorted(_titles(client))
    default_user = sorted(_titles(client, headers=_token(env["user_default"])))
    assert anon == default_user == ["probe-td-0", "probe-td-1"]


def test_tenant_axis_derived_not_self_reported(env):
    """tenant 从认证派生禁自报：token=tenant-a + user_id 自报 'default'（未声明
    →取派生），过滤仍按派生租户 tenant-a。"""
    client, disp = env["client"], env["disp"]
    _seed_rows(disp)
    titles = _titles(client, headers=_token(env["user_tenant_a"]), params={"user_id": "default"})
    assert sorted(titles) == ["probe-ta-0", "probe-ta-1"]


def test_limit_counts_only_visible_rows(env):
    """过滤先于截断=limit 只计可见命中（跨租户行不占名额，DEC-⑦-① 同款）。"""
    client, disp = env["client"], env["disp"]
    _seed_rows(disp, tenant_a=3, tenant_default=2)
    titles = _titles(client, params={"limit": 2})
    assert len(titles) == 2
    assert all(t.startswith("probe-td-") for t in titles)


# ── 语义/契约 ────────────────────────────────────────────────────────


def test_read_source_shared_dispatcher_history(env):
    """读真源收口硬证：真发送方（src.api.echo.dispatcher 单例）落的消息能被
    unified echo 子搜索搜到（修前函数内 new MessageDispatcher() 空历史恒空集
    =wired-but-not-functional，旧代码此例必空）。"""
    client, _disp = env["client"], env["disp"]
    r = client.post(
        "/api/echo/send",
        json={"channel": "console", "title": "probe-real-src", "content": "b"},
    )
    assert r.status_code == 200
    titles = _titles(client)
    assert "probe-real-src" in titles


def test_result_shape_and_match_semantics_unchanged(env):
    client, disp = env["client"], env["disp"]
    _seed_rows(disp, tenant_a=0, tenant_default=1)
    p = {"q": "td body", "sources": "echo", "limit": 10}
    r = client.get("/api/search/unified", params=p)
    assert r.status_code == 200
    entries = r.json()["by_source"]["echo"]
    assert entries, "content 匹配语义不变（修前即按 title/content/channel 匹配）"
    assert set(entries[0].keys()) == {"source", "icon", "title", "snippet", "msg_id", "channel"}


def test_system_view_none_and_single_tenant_contract(env):
    """history(tenant_id=None)=系统视图全量（既有 /history 调用面字节不变）；
    单租户全 'default' 时匿名全见=本地单机契约零变化。"""
    client, disp = env["client"], env["disp"]
    _seed_rows(disp, tenant_a=0, tenant_default=3)
    assert len(disp.history(limit=50, tenant_id=None)) == 3
    assert len(disp.history(limit=50, tenant_id="default")) == 3
    assert len(disp.history(limit=50, tenant_id="tenant-a")) == 0
    assert len(_titles(client)) == 3
