"""mirror sandbox 模板存储面行租户键加轴测试（DEC-⑦-㉒b）。

修前（HEAD 源码+live 探针 15 FAIL 实证）：src/mirror/templates.py 零租户轴——
sandbox_templates 表无 tenant_id 列、create 写点丢身份（api create 有
bind_identity 但 create() 不收租户）、GET /templates 与 GET/DELETE
/templates/{id} 与 /instantiate 零鉴权零租户过滤（任意调用方可见/可实例化/可删
全部租户沙箱模板，builtin 种子行可被任意人真删）、stats 聚合全局泄露、
template stats 进 /stats 与 /health 全局计数。

收口=DEC-⑦ 家族同款（⑦-㉑ echo 模板面直接先例）：
① template_row_tenant 行租户键单一判法（自带键缺失/空白→'default' 兜底，写读
   同源禁消费点另发明判法 F4b-㉗ 纪律）
② sandbox_templates.tenant_id 行键（DDL+ALTER 双轨幂等回填 'default'、
   idx_sboxtpl_tenant 补列之后建——③c-2① 坑延续）+ create 写点归属
   （bind_identity 派生，匿名 'default' 契约不变）
③ template_visible_to 读面可见性单一判法（DEC-⑩-① builtin starter 目录跨租户
   共享语义：builtin=系统共享只读目录对全租户可见，其余行恒按行归属；写面不适用
   ——delete 恒按行归属+builtin 保护，共享≠可写）
④ by-ID 归属闸（get/instantiate 源读走 get 单点闸）：跨租户与 not-found 同契约
   不差分泄露（③c-1 纪律）；instantiate 闸拒不记 usage（零副作用）；delete
   builtin 恒拒（共享≠可写）
⑤ stats 聚合恒按可见集聚合（禁泄露全局分布，⑦-⑭⑰ 同款）；/health 保持系统视图
⑥ API 模板面 bind_identity 派生流入（坏 token 401 先于读/写零副作用）+读面输出
   加性键 tenant_id/builtin（行归属可观测）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/tmp TemplateEngine/tmp SandboxManager），
绝不写生产 data/mirror/templates.db、不打 live :8090。
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


def _seed_user(db_path, uid, username, tenant="default"):
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
        )
        c.commit()
    return uid


def _token(uid):
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": "Bearer " + tok}


BAD_TOKEN = {"Authorization": "Bearer not-a-real-token"}


def _engine(db):
    from src.mirror.templates import SandboxTemplateEngine

    return SandboxTemplateEngine(db_path=str(db))


def _count_rows(db):
    with sqlite3.connect(db) as c:
        return c.execute("SELECT count(*) FROM sandbox_templates").fetchone()[0]


def _row(db, tid):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT * FROM sandbox_templates WHERE template_id=?", (tid,)).fetchone()
        return dict(r) if r else None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + tmp 模板库/沙箱库 + 真 router 真 bind_identity。"""
    users_db = tmp_path / "users.db"
    tpl_db = tmp_path / "templates.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.mirror as mirror_api
    from src.mirror.sandbox import SandboxManager

    engine = _engine(tpl_db)
    monkeypatch.setattr(mirror_api, "template_engine", engine)
    monkeypatch.setattr(mirror_api, "manager", SandboxManager(tmp_path / "sandboxes"))

    app = FastAPI()
    app.include_router(mirror_api.router, prefix="/api/mirror")
    client = TestClient(app)

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_d = _seed_user(users_db, str(uuid.uuid4()), "carol-d", "default")

    yield {
        "client": client,
        "engine": engine,
        "tpl_db": tpl_db,
        "user_a": user_a,
        "user_b": user_b,
        "user_d": user_d,
    }


def _create(c, headers, name="tpl-x"):
    return c.post("/api/mirror/templates", json={"name": name}, headers=headers)


# ── ① 行租户键/builtin 单一判法 ──────────────────────────────────


class TestJudgmentSingleSource:
    def test_row_tenant_shapes(self):
        from src.mirror.templates import SandboxTemplate, template_row_tenant

        tpl = SandboxTemplate(
            template_id="t1",
            name="n",
            description="",
            icon="",
            config={},
            variables={},
            tags=[],
            category="custom",
            created_at=1.0,
        )
        assert template_row_tenant(tpl) == "default"  # 数据类缺省行键兜底
        tpl2 = SandboxTemplate(
            template_id="t2",
            name="n",
            description="",
            icon="",
            config={},
            variables={},
            tags=[],
            category="custom",
            created_at=1.0,
            tenant_id="  ",
        )
        assert template_row_tenant(tpl2) == "default"  # 空白兜底
        tpl3 = SandboxTemplate(
            template_id="t3",
            name="n",
            description="",
            icon="",
            config={},
            variables={},
            tags=[],
            category="custom",
            created_at=1.0,
            tenant_id=" tenant-x ",
        )
        assert template_row_tenant(tpl3) == "tenant-x"  # 归一
        assert template_row_tenant(None) == "default"
        assert template_row_tenant("tenant-y") == "tenant-y"

    def test_is_builtin_derived_single_source(self):
        from src.mirror.templates import (
            BUILTIN_TEMPLATE_IDS,
            DEFAULT_TEMPLATES,
            is_builtin,
        )

        assert BUILTIN_TEMPLATE_IDS == frozenset(t["template_id"] for t in DEFAULT_TEMPLATES)
        assert is_builtin("stpl-workflow-test") is True
        assert is_builtin("stpl-api-stress") is True
        assert is_builtin("stpl-user-made") is False

    def test_builtin_shared_all_tenants(self):
        from src.mirror.templates import SandboxTemplate, template_visible_to

        tpl = SandboxTemplate(
            template_id="stpl-workflow-test",
            name="n",
            description="",
            icon="",
            config={},
            variables={},
            tags=[],
            category="workflow",
            created_at=1.0,
            tenant_id="default",
        )
        for tenant in ("tenant-a", "tenant-b", "default"):
            assert template_visible_to(tpl, tenant) is True

    def test_row_ownership_visibility(self):
        from src.mirror.templates import SandboxTemplate, template_visible_to

        tpl = SandboxTemplate(
            template_id="stpl-mine",
            name="n",
            description="",
            icon="",
            config={},
            variables={},
            tags=[],
            category="custom",
            created_at=1.0,
            tenant_id="tenant-a",
        )
        assert template_visible_to(tpl, "tenant-a") is True
        assert template_visible_to(tpl, "tenant-b") is False
        assert template_visible_to(tpl, "default") is False


# ── ② 迁移/行键 ──────────────────────────────────────────────────


class TestMigrationAndRowKey:
    def test_new_db_column_and_index(self, tmp_path):
        engine = _engine(tmp_path / "fresh.db")
        cols = {r[1] for r in engine._db.execute("PRAGMA table_info(sandbox_templates)")}
        assert "tenant_id" in cols
        idx = engine._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_sboxtpl_tenant'"
        ).fetchone()
        assert idx is not None

    def test_old_db_altered_and_backfilled(self, tmp_path):
        db = tmp_path / "old.db"
        with sqlite3.connect(db) as c:
            c.execute(
                "CREATE TABLE sandbox_templates ("
                "template_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                "description TEXT DEFAULT '', icon TEXT DEFAULT 'x', "
                "config TEXT DEFAULT '{}', variables TEXT DEFAULT '{}', "
                "tags TEXT DEFAULT '[]', category TEXT DEFAULT 'custom', "
                "usage_count INTEGER DEFAULT 0, created_at REAL NOT NULL)"
            )
            c.execute(
                "INSERT INTO sandbox_templates (template_id, name, created_at) "
                "VALUES ('stpl-old-1', 'old', 1.0)"
            )
            c.commit()
        engine = _engine(db)  # 老库形态构造=幂等自愈真跑
        cols = {r[1] for r in engine._db.execute("PRAGMA table_info(sandbox_templates)")}
        assert "tenant_id" in cols
        assert _row(db, "stpl-old-1")["tenant_id"] == "default"
        idx = engine._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_sboxtpl_tenant'"
        ).fetchone()
        assert idx is not None
        # 幂等：再构造不炸不重复
        engine2 = _engine(db)
        assert engine2.get("stpl-old-1") is not None

    def test_create_writes_derived_tenant(self, tmp_path):
        engine = _engine(tmp_path / "t.db")
        tpl = engine.create(name="mine", tenant_id="tenant-a")
        assert tpl.tenant_id == "tenant-a"
        assert _row(tmp_path / "t.db", tpl.template_id)["tenant_id"] == "tenant-a"

    def test_create_missing_or_blank_falls_default(self, tmp_path):
        engine = _engine(tmp_path / "t.db")
        t1 = engine.create(name="no-key")
        t2 = engine.create(name="blank-key", tenant_id="   ")
        assert _row(tmp_path / "t.db", t1.template_id)["tenant_id"] == "default"
        assert _row(tmp_path / "t.db", t2.template_id)["tenant_id"] == "default"


# ── ③ 读闸 ───────────────────────────────────────────────────────


class TestReadGate:
    def test_get_system_view_none_sees_all(self, env):
        e = env["engine"]
        own = e.create(name="mine", tenant_id="tenant-a")
        assert e.get(own.template_id) is not None  # None=系统视图字节不变
        assert e.get("stpl-workflow-test") is not None

    def test_get_gate_cross_hidden_builtin_visible(self, env):
        e = env["engine"]
        own = e.create(name="mine", tenant_id="tenant-a")
        assert e.get(own.template_id, tenant_id="tenant-a") is not None
        assert e.get(own.template_id, tenant_id="tenant-b") is None
        assert e.get("stpl-agent-debug", tenant_id="tenant-b") is not None

    def test_get_gate_zero_side_effect_for_missing(self, env):
        e = env["engine"]
        assert e.get("stpl-nope", tenant_id="tenant-a") is None

    def test_list_three_faces_disjoint_plus_builtin(self, env):
        e = env["engine"]
        ra = e.create(name="a-row", tenant_id="tenant-a")
        rb = e.create(name="b-row", tenant_id="tenant-b")
        rd = e.create(name="d-row", tenant_id="default")
        la = {t["template_id"] for t in e.list_templates(tenant_id="tenant-a")}
        lb = {t["template_id"] for t in e.list_templates(tenant_id="tenant-b")}
        ld = {t["template_id"] for t in e.list_templates(tenant_id="default")}
        builtin = {
            "stpl-workflow-test",
            "stpl-agent-debug",
            "stpl-connector-test",
            "stpl-knowledge-import",
            "stpl-prompt-experiment",
            "stpl-api-stress",
        }
        assert la - builtin == {ra.template_id}
        assert lb - builtin == {rb.template_id}
        assert ld - builtin == {rd.template_id}
        assert not ((la - builtin) & (lb - builtin))
        assert not ((la - builtin) & (ld - builtin))
        assert not ((lb - builtin) & (ld - builtin))
        assert all(t in la for t in builtin)  # builtin 共享三面全见

    def test_list_additive_keys_and_category(self, env):
        e = env["engine"]
        own = e.create(name="mine", tenant_id="tenant-a", category="custom")
        rows = e.list_templates(tenant_id="tenant-a")
        row = next(r for r in rows if r["template_id"] == own.template_id)
        assert row["tenant_id"] == "tenant-a"
        assert row["builtin"] is False
        bi = next(r for r in rows if r["template_id"] == "stpl-workflow-test")
        assert bi["builtin"] is True
        assert bi["tenant_id"] == "default"
        # category 过滤语义不变（叠加可见集）
        cats = e.list_templates(category="custom", tenant_id="tenant-a")
        assert all(r["category"] == "custom" for r in cats)
        assert own.template_id in {r["template_id"] for r in cats}
        wf = e.list_templates(category="workflow", tenant_id="tenant-b")
        assert all(r["category"] == "workflow" for r in wf)


# ── ④ 写闸 ───────────────────────────────────────────────────────


class TestWriteGate:
    def test_delete_builtin_rejected_alive(self, env):
        e, db = env["engine"], env["tpl_db"]
        assert e.delete("stpl-api-stress", tenant_id="tenant-a") is False
        assert _row(db, "stpl-api-stress") is not None

    def test_delete_cross_false_row_alive(self, env):
        e, db = env["engine"], env["tpl_db"]
        own = e.create(name="mine", tenant_id="tenant-a")
        assert e.delete(own.template_id, tenant_id="tenant-b") is False
        assert _row(db, own.template_id) is not None

    def test_delete_own_true_gone(self, env):
        e, db = env["engine"], env["tpl_db"]
        own = e.create(name="mine", tenant_id="tenant-a")
        assert e.delete(own.template_id, tenant_id="tenant-a") is True
        assert _row(db, own.template_id) is None

    def test_delete_system_view_none_internal_face(self, env):
        e, db = env["engine"], env["tpl_db"]
        own = e.create(name="mine", tenant_id="tenant-a")
        assert e.delete(own.template_id) is True  # None=系统视图字节不变
        assert _row(db, own.template_id) is None


# ── ⑤ instantiate 闸 ────────────────────────────────────────────


class TestInstantiateGate:
    def test_cross_returns_none_zero_usage_side_effect(self, env):
        e, db = env["engine"], env["tpl_db"]
        own = e.create(name="mine", tenant_id="tenant-a")
        assert e.instantiate(own.template_id, tenant_id="tenant-b") is None
        assert _row(db, own.template_id)["usage_count"] == 0  # 闸拒不记账

    def test_own_bumps_usage_and_shape(self, env):
        e, db = env["engine"], env["tpl_db"]
        own = e.create(
            name="mine",
            tenant_id="tenant-a",
            config={"ttl_seconds": 100},
            variables={"v": "1"},
        )
        inst = e.instantiate(own.template_id, tenant_id="tenant-a")
        assert inst is not None
        assert set(inst.keys()) == {
            "name",
            "description",
            "config",
            "variables",
            "tags",
            "ttl_seconds",
        }
        assert inst["ttl_seconds"] == 100
        assert _row(db, own.template_id)["usage_count"] == 1

    def test_builtin_any_tenant_shares_usage_count(self, env):
        e, db = env["engine"], env["tpl_db"]
        before = _row(db, "stpl-prompt-experiment")["usage_count"]
        assert e.instantiate("stpl-prompt-experiment", tenant_id="tenant-b") is not None
        assert _row(db, "stpl-prompt-experiment")["usage_count"] == before + 1


# ── ⑥ stats 作用域 ───────────────────────────────────────────────


class TestStatsScope:
    def test_system_view_global(self, env):
        e = env["engine"]
        e.create(name="a-row", tenant_id="tenant-a")
        e.create(name="b-row", tenant_id="tenant-b")
        total = e.stats()["total_templates"]
        assert total == 8  # 6 builtin + 2

    def test_scoped_to_visible_set(self, env):
        e = env["engine"]
        own = e.create(name="a-row", tenant_id="tenant-a")
        st = e.stats(tenant_id="tenant-a")
        assert st["total_templates"] == 7  # 6 builtin + own
        assert st["total_templates"] == len(e.list_templates(tenant_id="tenant-a"))
        stb = e.stats(tenant_id="tenant-b")
        assert stb["total_templates"] == 6  # 只 builtin
        assert own.template_id not in str(stb)

    def test_by_category_scoped(self, env):
        e = env["engine"]
        e.create(name="a-row", tenant_id="tenant-a", category="custom")
        st = e.stats(tenant_id="tenant-b")
        assert "custom" not in st["by_category"]  # 他租户 custom 行不泄露分布
        st2 = e.stats(tenant_id="tenant-a")
        assert st2["by_category"].get("custom") == 1


# ── ⑦ API 全链 ───────────────────────────────────────────────────


class TestApiFullChain:
    def test_bad_token_401_battery_zero_side_effect(self, env):
        c, db = env["client"], env["tpl_db"]
        own = _create(c, _token(env["user_a"]), name="keep").json()["template_id"]
        before = _count_rows(db)
        codes = [
            c.get("/api/mirror/templates", headers=BAD_TOKEN).status_code,
            c.get(f"/api/mirror/templates/{own}", headers=BAD_TOKEN).status_code,
            c.post("/api/mirror/templates", json={"name": "x"}, headers=BAD_TOKEN).status_code,
            c.delete(f"/api/mirror/templates/{own}", headers=BAD_TOKEN).status_code,
            c.post(
                f"/api/mirror/templates/{own}/instantiate",
                json={"template_id": own},
                headers=BAD_TOKEN,
            ).status_code,
            c.get("/api/mirror/stats", headers=BAD_TOKEN).status_code,
        ]
        assert codes == [401, 401, 401, 401, 401, 401]
        assert _count_rows(db) == before  # 零写入零删除
        assert _row(db, own)["usage_count"] == 0  # 闸前零副作用

    def test_api_three_face_isolation_and_contract(self, env):
        c = env["client"]
        ta, tb = _token(env["user_a"]), _token(env["user_b"])
        ia = _create(c, ta, name="row-a").json()["template_id"]
        ib = _create(c, tb, name="row-b").json()["template_id"]
        idd = _create(c, None, name="row-d").json()["template_id"]
        la = {
            x["template_id"] for x in c.get("/api/mirror/templates", headers=ta).json()["templates"]
        }
        lb = {
            x["template_id"] for x in c.get("/api/mirror/templates", headers=tb).json()["templates"]
        }
        ld = {x["template_id"] for x in c.get("/api/mirror/templates").json()["templates"]}
        assert ia in la and ia not in lb and ia not in ld
        assert ib in lb and ib not in la
        assert idd in ld and idd not in la and idd not in lb
        assert (
            "stpl-workflow-test" in la and "stpl-workflow-test" in lb and "stpl-workflow-test" in ld
        )
        # 跨租户与 not-found 同契约（同 status 同 body 不差分泄露）
        r_cross = c.get(f"/api/mirror/templates/{ia}", headers=tb)
        r_miss = c.get("/api/mirror/templates/stpl-nope-nope", headers=tb)
        assert r_cross.status_code == r_miss.status_code == 404
        assert r_cross.json() == r_miss.json()

    def test_api_get_additive_keys(self, env):
        c, ta = env["client"], _token(env["user_a"])
        tid = _create(c, ta, name="row-a").json()["template_id"]
        r = c.get(f"/api/mirror/templates/{tid}", headers=ta)
        assert r.status_code == 200
        data = r.json()
        assert data["tenant_id"] == "tenant-a"
        assert data["builtin"] is False
        bi = c.get("/api/mirror/templates/stpl-agent-debug", headers=ta).json()
        assert bi["builtin"] is True

    def test_api_anonymous_default_contract(self, env):
        c = env["client"]
        r = _create(c, None, name="row-d")
        assert r.status_code == 200
        assert sorted(r.json().keys()) == ["name", "template_id"]  # 响应形状不变
        rows = c.get("/api/mirror/templates").json()["templates"]
        row = next(x for x in rows if x["template_id"] == r.json()["template_id"])
        assert row["tenant_id"] == "default"

    def test_api_delete_gate(self, env):
        c, db = env["client"], env["tpl_db"]
        ta, tb = _token(env["user_a"]), _token(env["user_b"])
        ia = _create(c, ta, name="row-a").json()["template_id"]
        assert c.delete(f"/api/mirror/templates/{ia}", headers=tb).status_code == 404
        assert _row(db, ia) is not None  # 跨租户删零破坏
        assert c.delete("/api/mirror/templates/stpl-connector-test", headers=ta).status_code == 404
        assert _row(db, "stpl-connector-test") is not None  # builtin 恒拒行存活
        assert c.delete(f"/api/mirror/templates/{ia}", headers=ta).status_code == 200
        assert _row(db, ia) is None

    def test_api_instantiate_gate(self, env):
        c, db = env["client"], env["tpl_db"]
        ta, tb = _token(env["user_a"]), _token(env["user_b"])
        ia = _create(c, ta, name="row-a").json()["template_id"]
        r_cross = c.post(
            f"/api/mirror/templates/{ia}/instantiate",
            json={"template_id": ia, "name": "sb-cross"},
            headers=tb,
        )
        assert r_cross.status_code == 404
        assert _row(db, ia)["usage_count"] == 0  # 闸拒不记账零副作用
        r_own = c.post(
            f"/api/mirror/templates/{ia}/instantiate",
            json={"template_id": ia, "name": "sb-own"},
            headers=ta,
        )
        assert r_own.status_code == 200
        assert sorted(r_own.json().keys()) == [
            "name",
            "sandbox_id",
            "status",
            "template_id",
            "variables",
        ]
        assert _row(db, ia)["usage_count"] == 1

    def test_api_stats_scoped_health_system_view(self, env):
        c, ta, tb = env["client"], _token(env["user_a"]), _token(env["user_b"])
        _create(c, ta, name="row-a")
        _create(c, tb, name="row-b")
        sa = c.get("/api/mirror/stats", headers=ta).json()["templates"]["total_templates"]
        la = len(c.get("/api/mirror/templates", headers=ta).json()["templates"])
        sb = c.get("/api/mirror/stats", headers=tb).json()["templates"]["total_templates"]
        assert sa == la == 7  # 6 builtin + own（他租户行不入聚合）
        assert sb == 7  # 6 builtin + own（对称）
        h = c.get("/api/mirror/health")
        assert h.status_code == 200
        assert h.json()["templates"]["total_templates"] == 8  # 系统视图全量字节不变
        assert sa < h.json()["templates"]["total_templates"]  # 租户面收窄于全局
        assert "status" in h.json() and "component" in h.json()


# ── ⑧ 测试隔离护栏（MIRROR_TEMPLATES_DB）────────────────────────


class TestTestIsolationGuard:
    def test_construction_follows_env_not_production(self, tmp_path, monkeypatch):
        """护栏：src.api.mirror 模块级 template_engine 单例 import 即构造——
        MIRROR_TEMPLATES_DB env 真消费，测试进程绝不写生产 data/mirror/templates.db。
        PRAGMA database_list 硬证真源路径（禁只信构造参数）。"""
        import src.mirror.templates as tpl_mod

        target = str(tmp_path / "guard.db")
        monkeypatch.setenv("MIRROR_TEMPLATES_DB", target)
        engine = tpl_mod.SandboxTemplateEngine()  # 与 import 即构造同一缺省解析路径
        real = str(engine._db.execute("PRAGMA database_list").fetchone()[2])
        assert real == target

    def test_env_isolated_autouse(self):
        """conftest autouse 真接线：默认 fixture 期 env 已指向 tmp，非生产路径。"""
        val = os.environ.get("MIRROR_TEMPLATES_DB", "")
        prod = os.path.expanduser("~/opensoul/data/mirror/templates.db")
        assert val
        assert os.path.abspath(val) != os.path.abspath(prod)
