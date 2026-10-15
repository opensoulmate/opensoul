"""echo /history 读面归属闸测试（DEC-⑦-⑬）。

修前（HEAD 源码实证）：GET /api/echo/history 零鉴权零租户过滤——dispatcher.history
不传 tenant_id=系统视图全量，任意调用方可见全部租户消息（title/content 前 200 字
跨租户泄露），与 timeline ③c-2① / events ⑦-① / echo 搜索面 ⑦-② 已收的查询过滤
不对称；且 ⑦-② 边界② 明记「echo /history 与 /stats 端点读面未按租户过滤（echo
模块自身 API 面不在 DEC-⑦ unified 范围，可另拍）」=本拍即那件。

收口=tenant 从认证身份派生（bind_identity）禁自报（/history 无 tenant_id 自报
参数），恒按行租户键过滤（key=message_row_tenant 单一源；匿名='default' 面=
⑦-①② 匿名面同款）+坏 token 401 先于读取（F3b-⑤）+输出加性键 tenant_id（⑦-③④⑤
「行归属可观测」同款）；history(tenant_id=None) 系统视图仍为内部调用面保留
（⑦-② 边界③ 字节不变）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB 归 tmp），dispatcher 每用例 fresh
monkeypatch（内存面零生产持久化），绝不写生产库。
"""

import inspect
import sqlite3
import time
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
    + 真 router 真 bind_identity。"""
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

    app = FastAPI()
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


def _seed_rows(disp, tenant_a: int = 2, tenant_default: int = 2, newest: str = "tenant-a"):
    """直写存储层行（行键 setattr 直写，写点归属另有专测）：tenant-a 行
    probe-ta-N、default 行 probe-td-N。控制组纪律：seed 不依赖新 API 签名，
    让旧代码失败暴露在行为断言而非 fixture TypeError。

    newest='tenant-a' → 租户行时间戳最新（旧代码系统视图 top-N 名额被跨租户行
    占满=过滤先于截断可断言）；'default' → 反向。
    """
    from src.echo.dispatcher import Channel, Message

    base = time.time()

    def _row(title: str, body: str, tenant: str, ts: float):
        msg = Message(
            msg_id=f"seed-{uuid.uuid4().hex[:8]}",
            channel=Channel.CONSOLE,
            title=title,
            content=body,
            timestamp=ts,
        )
        setattr(msg, "tenant_id", tenant)
        disp._history.append(msg)

    if newest == "tenant-a":
        ta_ts = [base + 10 + i for i in range(tenant_a)]
        td_ts = [base + i for i in range(tenant_default)]
    else:
        ta_ts = [base + i for i in range(tenant_a)]
        td_ts = [base + 10 + i for i in range(tenant_default)]
    for i in range(tenant_a):
        _row(f"probe-ta-{i}", "ta body", "tenant-a", ta_ts[i])
    for i in range(tenant_default):
        _row(f"probe-td-{i}", "td body", "default", td_ts[i])


def _titles(client, headers=None, params=None):
    p = {"limit": 50}
    p.update(params or {})
    r = client.get("/api/echo/history", params=p, headers=headers or {})
    assert r.status_code == 200
    return [m["title"] for m in r.json()["messages"]]


# ── 读面归属闸：派生租户恒过滤 ─────────────────────────────────────


def test_history_anon_sees_only_default_rows(env):
    """匿名='default' 面（⑦-①② 同款）：tenant-a 行不可见。修前系统视图全量
    =跨租户泄露反证。"""
    _seed_rows(env["disp"])
    titles = _titles(env["client"])
    assert sorted(titles) == ["probe-td-0", "probe-td-1"]


def test_history_tenant_a_sees_only_own_rows(env):
    _seed_rows(env["disp"])
    titles = _titles(env["client"], headers=_token(env["user_tenant_a"]))
    assert sorted(titles) == ["probe-ta-0", "probe-ta-1"]


def test_history_default_user_face_equals_anon(env):
    """认证 default 租户用户面==匿名面（同一 'default' 键）。"""
    _seed_rows(env["disp"])
    anon = sorted(_titles(env["client"]))
    default_user = sorted(_titles(env["client"], headers=_token(env["user_default"])))
    assert anon == default_user == ["probe-td-0", "probe-td-1"]


def test_history_limit_filters_before_truncation(env):
    """过滤先于截断（⑦-① 同款）：跨租户行不占 limit 名额。修前 top-2 全被
    时间戳最新的 tenant-a 行占满。"""
    _seed_rows(env["disp"], tenant_a=2, tenant_default=2, newest="tenant-a")
    titles = _titles(env["client"], params={"limit": 2})
    assert sorted(titles) == ["probe-td-0", "probe-td-1"]


def test_history_bad_token_401_before_read(env):
    """坏 token 401 先于读取（F3b-⑤ 身份证明先于一切）；修前零鉴权恒 200。"""
    _seed_rows(env["disp"])
    r = env["client"].get("/api/echo/history", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_history_channel_filter_stacks_with_tenant(env):
    """channel 过滤叠加租户过滤（既有参数语义零变化）。"""
    import time as _t

    from src.echo.dispatcher import Channel, Message

    disp = env["disp"]
    msg = Message(
        msg_id="seed-mail",
        channel=Channel.EMAIL,
        title="probe-mail-ta",
        content="mail body",
        timestamp=_t.time(),
    )
    setattr(msg, "tenant_id", "tenant-a")
    disp._history.append(msg)
    _seed_rows(env["disp"])

    titles = _titles(
        env["client"], headers=_token(env["user_tenant_a"]), params={"channel": "email"}
    )
    assert titles == ["probe-mail-ta"]
    # 反向：console 面在 tenant-a 下只见 console 行
    titles = _titles(
        env["client"], headers=_token(env["user_tenant_a"]), params={"channel": "console"}
    )
    assert sorted(titles) == ["probe-ta-0", "probe-ta-1"]


def test_history_invalid_channel_400_unchanged(env):
    r = env["client"].get("/api/echo/history", params={"channel": "nope"})
    assert r.status_code == 400


# ── 契约面：加性键 + 系统视图保留 ─────────────────────────────────


def test_history_shape_additive_tenant_key(env):
    """输出加性键 tenant_id（⑦-③④⑤「行归属可观测」同款），归一值
    （message_row_tenant 判法：缺失/空白→'default'）。"""
    _seed_rows(env["disp"], tenant_a=1, tenant_default=0)
    r = env["client"].get("/api/echo/history", headers=_token(env["user_tenant_a"]))
    assert r.status_code == 200
    entries = r.json()["messages"]
    assert len(entries) == 1
    assert set(entries[0].keys()) == {
        "msg_id",
        "channel",
        "title",
        "content",
        "status",
        "error",
        "timestamp",
        "priority",
        "tenant_id",
    }
    assert entries[0]["tenant_id"] == "tenant-a"

    r = env["client"].get("/api/echo/history")
    anon_entries = r.json()["messages"]
    assert anon_entries == []  # tenant-a 行匿名不可见（泄露收口）
    # 空白键行（旧数据形状）归一 'default' 可见
    _seed_rows(env["disp"], tenant_a=0, tenant_default=1)
    import src.api.echo as echo_api

    echo_api.dispatcher._history[-1].tenant_id = ""
    anon_entries = env["client"].get("/api/echo/history").json()["messages"]
    assert anon_entries and anon_entries[0]["tenant_id"] == "default"


def test_history_system_view_none_unchanged(env):
    """history(tenant_id=None)=系统视图全量（⑦-② 边界③ 既有内部调用面字节
    不变，消费方自查身份）。"""
    _seed_rows(env["disp"])
    disp = env["disp"]
    assert len(disp.history(limit=50, tenant_id=None)) == 4
    assert len(disp.history(limit=50, tenant_id="default")) == 2
    assert len(disp.history(limit=50, tenant_id="tenant-a")) == 2


def test_history_filter_single_source_message_row_tenant():
    """判法单一源锁死：端点读面过滤与 send 落行同一判法（禁消费点另发明，
    F4b-㉗ 纪律）。"""
    import src.api.echo as echo_api
    from src.echo.dispatcher import MessageDispatcher

    src_text = inspect.getsource(MessageDispatcher.history)
    assert "message_row_tenant" in src_text
    ep_src = inspect.getsource(echo_api.message_history)
    assert "bind_identity" in ep_src
    assert "identity.tenant_id" in ep_src
