"""echo 模板存储面行租户键加轴测试（DEC-⑦-㉑）。

修前（HEAD 源码实证）：src/echo/templates.py 零租户轴——templates 表无 tenant_id
列、create 写点丢身份（api/echo.py create_template 有 bind_identity 但 create()
不收租户）、GET /templates 与 GET/PATCH/DELETE /templates/{id} 与 /preview 零鉴权
零租户过滤（任意调用方可见/可改/可删全部租户消息模板，DEC-⑦-⑭ 诚实边界留档
「templates 聚合无行租户键（echo 模板存储层无轴）保持全局如实」）、send/preview
render 零闸=拿他人模板内容渲染发送、template stats 聚合全局泄露。

收口=DEC-⑦ 家族同款（① _event_row_tenant~⑳ v2_event_row_tenant 先例）：
① template_row_tenant 行租户键单一判法（自带键缺失/空白→'default' 兜底，写读
   同源禁消费点另发明判法 F4b-㉗ 纪律）
② templates.tenant_id 行键（DDL+ALTER 双轨幂等回填 'default'、idx_templates_tenant
   补列之后建——③c-2① 坑延续）+ create 写点归属（bind_identity 派生，匿名
   'default' 契约不变）+ UPDATE 粘性不洗键
③ template_visible_to 读面可见性单一判法（DEC-⑩-① builtin starter 目录跨租户
   共享语义：builtin=系统共享只读目录对全租户可见，其余行恒按行归属；写面不适用
   ——update/delete 恒按行归属+builtin 保护，共享≠可写）
④ by-ID 归属闸（get/update/delete/render）：跨租户与 not-found 同契约不差分泄露
   （③c-1 纪律）；builtin update/delete 恒拒（gene delete builtin 恒拒同款）
⑤ stats 聚合恒按可见集聚合（禁泄露全局分布，⑦-⑭ 同款）；/health 保持系统视图
⑥ API 模板面 11 handler bind_identity 派生流入（坏 token 401 先于读/写零副作用）
   + 读面输出加性键 tenant_id/builtin（行归属可观测）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/tmp TemplateEngine/fresh dispatcher），
绝不写生产 data/echo/templates.db、不打 live :8090。
"""

import os
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


BAD_TOKEN = {"Authorization": "Bearer not-a-real-token"}


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
    engine = TemplateEngine(db_path=str(tmp_path / "t.db"))
    monkeypatch.setattr(echo_api, "template_engine", engine)

    app = FastAPI()
    app.include_router(echo_api.router, prefix="/api/echo")
    client = TestClient(app)

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")

    yield {
        "client": client,
        "engine": engine,
        "disp": fresh,
        "user_a": user_a,
        "user_b": user_b,
        "user_default": user_default,
    }


def _create(c, headers, name="tpl-x"):
    return c.post(
        "/api/echo/templates",
        json={"name": name, "title_template": "T {{v}}", "content_template": "C"},
        headers=headers,
    )


# ── ① 行租户键/builtin 单一判法 ──────────────────────────────────


class TestJudgmentSingleSource:
    def test_row_tenant_shapes(self):
        from src.echo.templates import MessageTemplate, template_row_tenant

        assert (
            template_row_tenant(
                MessageTemplate("t", "n", "", "any", "T", "C", [], "custom", "x", 0.0)
            )
            == "default"
        )
        tpl = MessageTemplate(
            "t", "n", "", "any", "T", "C", [], "custom", "x", 0.0, tenant_id="tenant-a"
        )
        assert template_row_tenant(tpl) == "tenant-a"
        tpl.tenant_id = "   "
        assert template_row_tenant(tpl) == "default"
        assert template_row_tenant("tenant-z") == "tenant-z"
        assert template_row_tenant("") == "default"
        assert template_row_tenant(None) == "default"

    def test_builtin_ids_derived_from_seed_single_source(self):
        from src.echo.templates import (
            BUILTIN_TEMPLATE_IDS,
            DEFAULT_TEMPLATES,
            is_builtin,
        )

        assert BUILTIN_TEMPLATE_IDS == frozenset(t["template_id"] for t in DEFAULT_TEMPLATES)
        assert is_builtin("tpl-task-complete") is True
        assert is_builtin("tpl-9999-nope") is False


# ── ② 写点归属 + 粘性 ────────────────────────────────────────────


class TestWriteAttribution:
    def test_create_row_attribution_api(self, env):
        c = env["client"]
        ra = _create(c, _token(env["user_a"]), name="mine-a")
        assert ra.status_code == 200
        tid_a = ra.json()["template_id"]
        tpl = env["engine"]._raw_get(tid_a)
        assert tpl.tenant_id == "tenant-a"  # 写点=bind_identity 派生禁自报

        r0 = _create(c, {}, name="anon-1")
        assert r0.status_code == 200
        assert env["engine"]._raw_get(r0.json()["template_id"]).tenant_id == "default"

    def test_update_sticky_not_washed(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="sticky-a").json()["template_id"]
        r = c.patch(
            f"/api/echo/templates/{tid}",
            json={"name": "renamed-a"},
            headers=_token(env["user_a"]),
        )
        assert r.status_code == 200
        assert env["engine"]._raw_get(tid).tenant_id == "tenant-a"


# ── ③ list 读面三向 + 加性键 ─────────────────────────────────────


class TestListVisibility:
    def test_list_three_faces_disjoint(self, env):
        c = env["client"]
        ta = _create(c, _token(env["user_a"]), name="row-a").json()["template_id"]
        tb = _create(c, _token(env["user_b"]), name="row-b").json()["template_id"]
        td = _create(c, {}, name="row-d").json()["template_id"]

        rows_a = {
            r["template_id"]
            for r in c.get("/api/echo/templates", headers=_token(env["user_a"])).json()["templates"]
        }
        rows_b = {
            r["template_id"]
            for r in c.get("/api/echo/templates", headers=_token(env["user_b"])).json()["templates"]
        }
        rows_0 = {r["template_id"] for r in c.get("/api/echo/templates").json()["templates"]}

        from src.echo.templates import BUILTIN_TEMPLATE_IDS

        assert BUILTIN_TEMPLATE_IDS <= rows_a  # builtin 共享目录对全租户可见
        assert BUILTIN_TEMPLATE_IDS <= rows_b
        assert BUILTIN_TEMPLATE_IDS <= rows_0
        assert ta in rows_a and tb not in rows_a and td not in rows_a
        assert tb in rows_b and ta not in rows_b and td not in rows_b
        assert td in rows_0 and ta not in rows_0 and tb not in rows_0

    def test_additive_keys(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="keyed-a").json()["template_id"]
        rows = c.get("/api/echo/templates", headers=_token(env["user_a"])).json()["templates"]
        by_id = {r["template_id"]: r for r in rows}
        assert by_id[tid]["tenant_id"] == "tenant-a"
        assert by_id[tid]["builtin"] is False
        assert by_id["tpl-task-complete"]["tenant_id"] == "default"
        assert by_id["tpl-task-complete"]["builtin"] is True

    def test_category_filter_stackable(self, env):
        c = env["client"]
        _create(c, _token(env["user_a"]), name="cat-a")
        rows = c.get("/api/echo/templates?category=custom", headers=_token(env["user_b"])).json()[
            "templates"
        ]
        assert rows == []  # B 看不到 A 的 custom 行（过滤叠加租户轴）


# ── ④ by-ID 读闸 ─────────────────────────────────────────────────


class TestGetGate:
    def test_get_own_and_cross_same_contract_as_missing(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="get-a").json()["template_id"]
        own = c.get(f"/api/echo/templates/{tid}", headers=_token(env["user_a"]))
        assert own.status_code == 200
        assert own.json()["tenant_id"] == "tenant-a"
        cross = c.get(f"/api/echo/templates/{tid}", headers=_token(env["user_b"]))
        missing = c.get("/api/echo/templates/tpl-nope-nope", headers=_token(env["user_b"]))
        assert cross.status_code == 404 and missing.status_code == 404
        assert cross.json() == missing.json()  # 不差分泄露行存在性（③c-1）

    def test_get_builtin_any_tenant(self, env):
        c = env["client"]
        for headers in (_token(env["user_a"]), _token(env["user_b"]), {}):
            r = c.get("/api/echo/templates/tpl-task-complete", headers=headers)
            assert r.status_code == 200
            assert r.json()["builtin"] is True


# ── ⑤ 写闸（update/delete）────────────────────────────────────────


class TestWriteGate:
    def test_update_cross_404_row_unchanged(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="upd-a").json()["template_id"]
        r = c.patch(
            f"/api/echo/templates/{tid}",
            json={"name": "hacked"},
            headers=_token(env["user_b"]),
        )
        assert r.status_code == 404
        assert env["engine"]._raw_get(tid).name == "upd-a"  # 行零改写硬反证

    def test_update_builtin_rejected_row_unchanged(self, env):
        c = env["client"]
        r = c.patch(
            "/api/echo/templates/tpl-task-complete",
            json={"name": "hacked"},
            headers=_token(env["user_a"]),
        )
        assert r.status_code == 404
        assert env["engine"]._raw_get("tpl-task-complete").name == "任务完成通知"

    def test_update_own(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="own-upd").json()["template_id"]
        r = c.patch(
            f"/api/echo/templates/{tid}",
            json={"name": "own-upd-2"},
            headers=_token(env["user_a"]),
        )
        assert r.status_code == 200
        assert env["engine"]._raw_get(tid).name == "own-upd-2"

    def test_delete_cross_404_row_alive(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="del-a").json()["template_id"]
        r = c.delete(f"/api/echo/templates/{tid}", headers=_token(env["user_b"]))
        assert r.status_code == 404
        assert env["engine"]._raw_get(tid) is not None  # 行存活

    def test_delete_builtin_rejected_alive(self, env):
        c = env["client"]
        r = c.delete("/api/echo/templates/tpl-daily-digest", headers=_token(env["user_a"]))
        assert r.status_code == 404
        assert env["engine"]._raw_get("tpl-daily-digest") is not None  # 共享≠可写

    def test_delete_own_200_gone(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="del-own").json()["template_id"]
        r = c.delete(f"/api/echo/templates/{tid}", headers=_token(env["user_a"]))
        assert r.status_code == 200
        assert env["engine"]._raw_get(tid) is None


# ── ⑥ render 闸（send/preview）────────────────────────────────────


class TestRenderGate:
    def test_send_cross_404_zero_messages(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="snd-a").json()["template_id"]
        r = c.post(
            f"/api/echo/templates/{tid}/send",
            json={"variables": {"v": "1"}},
            headers=_token(env["user_b"]),
        )
        assert r.status_code == 404
        assert env["disp"].history() == []  # 零投递零副作用硬反证

    def test_preview_cross_404(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="pv-a").json()["template_id"]
        r = c.post(
            f"/api/echo/templates/{tid}/preview",
            json={"variables": {}},
            headers=_token(env["user_b"]),
        )
        assert r.status_code == 404
        own = c.post(
            f"/api/echo/templates/{tid}/preview",
            json={"variables": {}},
            headers=_token(env["user_a"]),
        )
        assert own.status_code == 200

    def test_send_own_and_builtin(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="snd-own").json()["template_id"]
        r1 = c.post(
            f"/api/echo/templates/{tid}/send",
            json={"variables": {"v": "1"}},
            headers=_token(env["user_a"]),
        )
        assert r1.status_code == 200
        r2 = c.post(
            "/api/echo/templates/tpl-task-complete/send",
            json={"variables": {"task_name": "x", "duration": "1s", "result": "ok", "agent": "a"}},
            headers=_token(env["user_b"]),
        )
        assert r2.status_code == 200  # builtin 共享目录可发送（可见即可用）
        entries = env["disp"].history()
        assert sorted(e["tenant_id"] for e in entries) == ["tenant-a", "tenant-b"]


# ── ⑦ stats 聚合作用域 ───────────────────────────────────────────


class TestStatsScope:
    def test_stats_scoped_not_global(self, env):
        c = env["client"]
        _create(c, _token(env["user_a"]), name="s-a1")
        _create(c, _token(env["user_a"]), name="s-a2")
        _create(c, _token(env["user_b"]), name="s-b1")

        from src.echo.templates import BUILTIN_TEMPLATE_IDS

        n_builtin = len(BUILTIN_TEMPLATE_IDS)
        data_a = c.get("/api/echo/stats", headers=_token(env["user_a"])).json()
        data_b = c.get("/api/echo/stats", headers=_token(env["user_b"])).json()
        data_0 = c.get("/api/echo/stats").json()
        assert data_a["templates"]["total_templates"] == n_builtin + 2  # 不含 B 行
        assert data_b["templates"]["total_templates"] == n_builtin + 1
        assert data_0["templates"]["total_templates"] == n_builtin  # 匿名 default 面

    def test_health_system_view_unchanged(self, env):
        c = env["client"]
        _create(c, _token(env["user_a"]), name="h-a")
        _create(c, _token(env["user_b"]), name="h-b")
        data = c.get("/api/echo/health").json()
        from src.echo.templates import BUILTIN_TEMPLATE_IDS

        assert data["templates"]["total_templates"] == len(BUILTIN_TEMPLATE_IDS) + 2

    def test_stats_shape_keys(self, env):
        c = env["client"]
        data = c.get("/api/echo/stats", headers=_token(env["user_a"])).json()["templates"]
        assert set(data.keys()) == {"total_templates", "by_category", "total_usage"}


# ── ⑧ 鉴权：坏 token 401 先于读/写 ────────────────────────────────


class TestAuthAxis:
    def test_bad_token_401_battery_zero_side_effects(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="auth-a").json()["template_id"]
        assert c.get("/api/echo/templates", headers=BAD_TOKEN).status_code == 401
        assert c.get(f"/api/echo/templates/{tid}", headers=BAD_TOKEN).status_code == 401
        assert (
            c.patch(f"/api/echo/templates/{tid}", json={"name": "x"}, headers=BAD_TOKEN).status_code
            == 401
        )
        assert c.delete(f"/api/echo/templates/{tid}", headers=BAD_TOKEN).status_code == 401
        assert (
            c.post(
                f"/api/echo/templates/{tid}/preview", json={"variables": {}}, headers=BAD_TOKEN
            ).status_code
            == 401
        )
        assert env["engine"]._raw_get(tid).name == "auth-a"  # 写闸零副作用


# ── ⑨ 老库迁移自愈 + 粘性 ────────────────────────────────────────


class TestLegacyMigration:
    def test_legacy_db_backfill_and_index(self, tmp_path):
        from src.echo.templates import TemplateEngine

        db = tmp_path / "legacy.db"
        with sqlite3.connect(db) as c:
            c.execute(
                "CREATE TABLE templates (template_id TEXT PRIMARY KEY, name TEXT NOT NULL,"
                " description TEXT DEFAULT '', channel TEXT DEFAULT 'any',"
                " title_template TEXT NOT NULL, content_template TEXT NOT NULL,"
                " variables TEXT DEFAULT '[]', category TEXT DEFAULT 'custom',"
                " icon TEXT DEFAULT 'x', created_at REAL NOT NULL,"
                " usage_count INTEGER DEFAULT 0, last_used REAL DEFAULT 0)"
            )
            c.execute(
                "INSERT INTO templates (template_id, name, title_template,"
                " content_template, created_at) VALUES ('tpl-legacy-1', 'legacy', 'T', 'C', 1.0)"
            )
            c.commit()

        engine = TemplateEngine(db_path=str(db))
        tpl = engine._raw_get("tpl-legacy-1")
        assert tpl is not None
        assert tpl.tenant_id == "default"  # 存量行回填 'default'（家族语义）

        with sqlite3.connect(db) as c:
            idx = c.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_templates_tenant'"
            ).fetchone()
            cols = {r[1] for r in c.execute("PRAGMA table_info(templates)")}
        assert idx is not None  # 索引真建（补列之后建）
        assert "tenant_id" in cols

        assert engine.update("tpl-legacy-1", tenant_id="default", name="legacy-2") is True
        tpl2 = engine._raw_get("tpl-legacy-1")
        assert tpl2 is not None
        assert tpl2.tenant_id == "default"  # 粘性不洗键

    def test_idempotent_rerun(self, tmp_path):
        from src.echo.templates import TemplateEngine

        db = tmp_path / "idem.db"
        for _ in range(2):
            engine = TemplateEngine(db_path=str(db))
            assert engine._raw_get("tpl-task-complete") is not None


# ── ⑩ 单一判法真消费（monkeypatch 翻转全跟走）────────────────────


class TestSingleSourceConsumed:
    def test_visible_to_flipped_all_read_faces_follow(self, env, monkeypatch):
        import src.echo.templates as tpl_mod

        c = env["client"]
        tid = _create(c, _token(env["user_a"]), name="flip-a").json()["template_id"]

        monkeypatch.setattr(tpl_mod, "template_visible_to", lambda t, k: False)
        assert c.get("/api/echo/templates", headers=_token(env["user_a"])).json()["templates"] == []
        assert c.get(f"/api/echo/templates/{tid}", headers=_token(env["user_a"])).status_code == 404
        assert (
            c.get("/api/echo/stats", headers=_token(env["user_a"])).json()["templates"][
                "total_templates"
            ]
            == 0
        )

        monkeypatch.setattr(tpl_mod, "template_visible_to", lambda t, k: True)
        rows = c.get("/api/echo/templates", headers=_token(env["user_a"])).json()["templates"]
        assert any(r["template_id"] == tid for r in rows)  # 判法被真消费非摆设

    def test_row_tenant_flipped_write_read_follow(self, env, monkeypatch):
        import src.echo.templates as tpl_mod

        c = env["client"]
        monkeypatch.setattr(tpl_mod, "template_row_tenant", lambda raw: "tenant-x")
        tid = _create(c, _token(env["user_a"]), name="flip-w").json()["template_id"]
        assert env["engine"]._raw_get(tid).tenant_id == "tenant-x"  # 写侧真消费
        rows = env["engine"].list_templates()  # 系统视图不过滤，只验输出归一真消费
        by_id = {r["template_id"]: r for r in rows}
        assert by_id[tid]["tenant_id"] == "tenant-x"  # 输出归一真消费
        # 可见性闸同一判法真消费：翻转后 'tenant-x' 可见、'tenant-a' 不可见
        assert env["engine"].get(tid, tenant_id="tenant-x") is not None
        assert env["engine"].get(tid, tenant_id="tenant-a") is None


# ── ⑪ 测试隔离护栏（ECHO_TEMPLATES_DB）────────────────────────────


class TestTestIsolationGuard:
    def test_construction_follows_env_not_production(self, tmp_path, monkeypatch):
        """护栏：src.api.echo 模块级 template_engine 单例 import 即构造——
        ECHO_TEMPLATES_DB env 真消费，测试进程绝不写生产 data/echo/templates.db
        （DEC-⑦-㉑ Gate3 逼出缺口：全量 pytest 的 import 曾把迁移 SQL 提前触发进
        生产库）。PRAGMA database_list 硬证真源路径（禁只信构造参数）。"""
        import src.echo.templates as tpl_mod

        target = str(tmp_path / "guard.db")
        monkeypatch.setenv("ECHO_TEMPLATES_DB", target)
        engine = tpl_mod.TemplateEngine()  # 与 import 即构造同一缺省解析路径
        real = str(engine._db.execute("PRAGMA database_list").fetchone()[2])
        assert real == target

    def test_env_isolated_autouse(self):
        """conftest autouse 真接线：默认 fixture 期 env 已指向 tmp，非生产路径。"""

        val = os.environ.get("ECHO_TEMPLATES_DB", "")
        prod = os.path.expanduser("~/opensoul/data/echo/templates.db")
        assert val  # env 已设置
        assert os.path.abspath(val) != os.path.abspath(prod)  # 非生产路径
