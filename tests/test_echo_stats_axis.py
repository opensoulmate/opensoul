"""echo /stats 聚合面加轴测试（DEC-⑦-⑭）。

修前（HEAD 源码实证）：GET /api/echo/stats 零鉴权零租户过滤——dispatcher.stats()
对整个 _history 计数 total/sent/failed，任意调用方可见全部租户消息量（聚合泄露，
DEC-⑦-⑬ 诚实边界①留档件「/stats 聚合面（total/sent/failed 全局计数）未按租户
过滤（聚合泄露另拍）」=本拍即那件）。

收口=tenant 从认证身份派生（bind_identity）禁自报（/stats 无 tenant_id 自报参数），
total/sent/failed 恒按行租户键聚合（key=message_row_tenant 单一源；匿名='default'
面=⑦-⑬ 同款）+坏 token 401 先于读取（F3b-⑤）+输出加性键 tenant_id（聚合作用域
可观测）；dispatcher.stats(tenant_id=None) 系统视图仍为 /health 监控面与内部调用
面保留（字节不变）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB 归 tmp），dispatcher 每用例 fresh
monkeypatch（内存面零生产持久化），TemplateEngine tmp db，绝不写生产库。
"""

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
    """tmp 身份库 + fresh dispatcher + tmp 模板库 + 真 router 真 bind_identity。"""
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

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "bob-default", "default")

    yield {
        "client": client,
        "disp": fresh,
        "user_a": user_a,
        "user_default": user_default,
    }


def _seed_rows(disp, rows):
    """直写存储层行（行键 setattr 直写，写点归属另有专测）。

    rows=[(title, tenant, status)]，时间戳递增。控制组纪律：seed 不依赖新 API
    签名，让旧代码失败暴露在行为断言而非 fixture TypeError。
    """
    from src.echo.dispatcher import Channel, Message

    base = time.time()
    for i, (title, tenant, status) in enumerate(rows):
        msg = Message(
            msg_id=f"seed-{uuid.uuid4().hex[:8]}",
            channel=Channel.CONSOLE,
            title=title,
            content="body",
            timestamp=base + i,
            status=status,
        )
        setattr(msg, "tenant_id", tenant)
        disp._history.append(msg)


class TestStatsAggregateAxis:
    def test_aggregate_filtered_three_faces(self, env):
        """租户只见自己的聚合：A 2 行（1 sent 1 failed）、default 2 行、B 1 行。"""
        _seed_rows(
            env["disp"],
            [
                ("ta-0", "tenant-a", "sent"),
                ("ta-1", "tenant-a", "failed"),
                ("td-0", "default", "sent"),
                ("td-1", "default", "sent"),
                ("tb-0", "tenant-b", "failed"),
            ],
        )
        r = env["client"].get("/api/echo/stats", headers=_token(env["user_a"]))
        assert r.status_code == 200
        data = r.json()
        assert data["total_messages"] == 2
        assert data["sent"] == 1
        assert data["failed"] == 1
        # 匿名='default' 面（⑦-⑬ 同款）
        anon = env["client"].get("/api/echo/stats").json()
        assert anon["total_messages"] == 2
        assert anon["sent"] == 2
        assert anon["failed"] == 0
        # default 身份用户==匿名面
        du = env["client"].get("/api/echo/stats", headers=_token(env["user_default"])).json()
        assert (du["total_messages"], du["sent"], du["failed"]) == (2, 2, 0)

    def test_system_view_none_contract(self, env):
        """stats(tenant_id=None)=系统视图全量（既有内部调用面字节不变契约锁死）。"""
        _seed_rows(
            env["disp"],
            [
                ("ta-0", "tenant-a", "sent"),
                ("td-0", "default", "failed"),
                ("tb-0", "tenant-b", "sent"),
            ],
        )
        sysview = env["disp"].stats()
        assert sysview["total_messages"] == 3
        assert sysview["sent"] == 2
        assert sysview["failed"] == 1

    def test_channels_counts_global_not_filtered(self, env):
        """channels_* 是全局系统配置计数非租户消息行，恒全局如实（非本轴数据面）。"""
        _seed_rows(env["disp"], [("ta-0", "tenant-a", "sent")] * 3)
        data = env["client"].get("/api/echo/stats", headers=_token(env["user_a"])).json()
        sysview = env["disp"].stats()
        assert data["channels_configured"] == sysview["channels_configured"]
        assert data["channels_enabled"] == sysview["channels_enabled"]

    def test_response_shape_keys(self, env):
        """响应形状逐键：加性 tenant_id=聚合作用域（派生值），其余键零丢失。"""
        _seed_rows(env["disp"], [("ta-0", "tenant-a", "sent")])
        data = env["client"].get("/api/echo/stats", headers=_token(env["user_a"])).json()
        assert set(data.keys()) == {
            "status",
            "component",
            "tenant_id",
            "total_messages",
            "sent",
            "failed",
            "channels_configured",
            "channels_enabled",
            "templates",
        }
        assert data["tenant_id"] == "tenant-a"
        assert data["status"] == "ok"
        assert data["component"] == "OpenEcho"
        assert isinstance(data["templates"], dict) and "total_templates" in data["templates"]

    def test_bad_token_401_before_read(self, env):
        """坏 token 401 先于读取（F3b-⑤ 身份证明先于一切）。"""
        r = env["client"].get("/api/echo/stats", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401

    def test_blank_key_rows_count_under_default(self, env):
        """行键空白/缺失→'default' 兜底=聚合判法与 message_row_tenant 单一源同轴。"""
        _seed_rows(
            env["disp"],
            [
                ("blank-0", "", "sent"),
                ("missing-0", "  ", "failed"),
                ("ta-0", "tenant-a", "sent"),
            ],
        )
        anon = env["client"].get("/api/echo/stats").json()
        assert anon["total_messages"] == 2
        assert anon["sent"] == 1
        assert anon["failed"] == 1
        a = env["client"].get("/api/echo/stats", headers=_token(env["user_a"])).json()
        assert a["total_messages"] == 1

    def test_pending_counted_in_total_only(self, env):
        """pending 行只进 total 不进 sent/failed（聚合语义过滤前后一致）。"""
        _seed_rows(
            env["disp"],
            [
                ("ta-0", "tenant-a", "pending"),
                ("ta-1", "tenant-a", "sent"),
            ],
        )
        data = env["client"].get("/api/echo/stats", headers=_token(env["user_a"])).json()
        assert data["total_messages"] == 2
        assert data["sent"] == 1
        assert data["failed"] == 0

    def test_health_system_view_unchanged(self, env):
        """/health 监控面=系统视图（stats(tenant_id=None) 保留，既有契约字节不变）。"""
        _seed_rows(
            env["disp"],
            [
                ("ta-0", "tenant-a", "sent"),
                ("td-0", "default", "failed"),
            ],
        )
        data = env["client"].get("/api/echo/health").json()
        assert data["total_messages"] == 2
        assert data["sent"] == 1
        assert data["failed"] == 1

    def test_stats_tenant_axis_single_source(self, env):
        """单一源锁死：stats 过滤判法=message_row_tenant（同一行两口径自洽）。"""
        from src.echo.dispatcher import message_row_tenant

        _seed_rows(env["disp"], [("ta-0", " tenant-a ", "sent")])
        # 归一值（带空白）行按归一键聚合
        data = env["client"].get("/api/echo/stats", headers=_token(env["user_a"])).json()
        assert data["total_messages"] == 1
        assert message_row_tenant(" tenant-a ") == "tenant-a"
        assert message_row_tenant("") == "default"

    def test_filter_not_selfreport(self, env):
        """派生轴非自报：/stats 无 tenant_id 自报参数——带 ?tenant_id= 查询参数
        不改聚合作用域（仍是派生租户），冒读面不可达。"""
        _seed_rows(
            env["disp"],
            [
                ("ta-0", "tenant-a", "sent"),
                ("ta-1", "tenant-a", "sent"),
                ("td-0", "default", "sent"),
            ],
        )
        data = (
            env["client"]
            .get(
                "/api/echo/stats",
                params={"tenant_id": "tenant-a"},
                headers=_token(env["user_default"]),
            )
            .json()
        )
        assert data["tenant_id"] == "default"
        assert data["total_messages"] == 1
