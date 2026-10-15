"""limb 模板存储面行租户键加轴测试（DEC-⑦-㉒）。

修前（HEAD 源码实证）：src/limb/tasks.py + executor.py 零租户轴——TaskTemplate 无
tenant_id、create 写点零身份轴（行归属无从谈起）、GET /templates 与
GET/DELETE /templates/{id} 与 /instantiate 零鉴权零租户过滤（任意调用方可见/可删
全部租户自定义模板、可用他人模板内容跑实例化）、stats templates 计数全局泄露、
builtin 种子可被 create_template 显式 id 覆盖（共享≠可写失守）。

收口=DEC-⑦ 家族同款（① _event_row_tenant~㉑ echo template_row_tenant 先例）：
① template_row_tenant 行租户键单一判法（自带键缺失/空白→'default' 兜底，写读
   同源禁消费点另发明判法 F4b-㉗ 纪律）
② TaskTemplate.tenant_id 行键 + create 写点归属（bind_identity 派生，匿名
   'default' 契约不变）+ 创建覆盖劫持闸（行归属不可被跨租户改写，⑦-③ 同款）
③ template_visible_to 读面可见性单一判法（DEC-⑩-① builtin 共享语义：builtin=
   系统共享只读目录对全租户可见，其余行恒按行归属；写面不适用——delete/创建
   覆盖恒按行归属+builtin 保护，共享≠可写）
④ by-ID 归属闸（get/instantiate 源读）：跨租户与 not-found 同契约不差分泄露
   （③c-1 纪律）；builtin delete 恒拒、builtin 创建覆盖恒拒
⑤ stats 聚合恒按可见集聚合（禁泄露全局分布，⑦-⑭⑰ 同款）；/health 保持系统视图
⑥ API 模板面 5 端点+stats bind_identity 派生流入（坏 token 401 先于读/写零
   副作用）+ 读面输出加性键 tenant_id/builtin（行归属可观测）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/fresh RPAExecutor），纯内存面无生产
库可写、不打 live :8090。
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
    """tmp 身份库 + fresh RPAExecutor + 真 router 真 bind_identity。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.limb as limb_api
    from src.limb.executor import RPAExecutor

    fresh = RPAExecutor()
    monkeypatch.setattr(limb_api, "executor", fresh)

    app = FastAPI()
    app.include_router(limb_api.router, prefix="/api/limb")
    client = TestClient(app)

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")

    yield {
        "client": client,
        "eng": fresh,
        "user_a": user_a,
        "user_b": user_b,
        "user_default": user_default,
    }


def _create(c, headers, name="tpl-x"):
    return c.post(
        "/api/limb/templates",
        json={"name": name, "category": "custom", "actions": []},
        headers=headers,
    )


# ── ① 行租户键/builtin 单一判法 ──────────────────────────────────


class TestJudgmentSingleSource:
    def test_row_tenant_shapes(self):
        from src.limb.tasks import TaskTemplate, template_row_tenant

        assert (
            template_row_tenant(
                TaskTemplate(template_id="t", name="n", description="", category="c", actions=[])
            )
            == "default"
        )
        assert template_row_tenant(None) == "default"
        assert template_row_tenant("") == "default"
        assert template_row_tenant("   ") == "default"
        assert template_row_tenant("tenant-a") == "tenant-a"

    def test_builtin_ids_derived_single_source(self):
        from src.limb.tasks import BUILTIN_TEMPLATE_IDS, BUILTIN_TEMPLATES, is_builtin

        # 禁第二套名单手抄：id 集必须=BUILTIN_TEMPLATES 派生（F4b-㉗ 纪律）
        assert BUILTIN_TEMPLATE_IDS == frozenset(t.template_id for t in BUILTIN_TEMPLATES)
        assert is_builtin("tpl-form-fill") and not is_builtin("tpl-user-x")

    def test_single_judgment_consumed(self, env, monkeypatch):
        """monkeypatch 翻转判法→读面/闸真跟走=消费单一源非行内私抄。"""
        import src.limb.executor as ex

        calls = []

        def spy(template, tenant_id):
            calls.append((template.template_id, tenant_id))
            return False  # 翻转：什么都不可见

        monkeypatch.setattr(ex, "template_visible_to", spy)
        _create(env["client"], _token(env["user_a"]))
        calls.clear()
        out = env["eng"].list_templates(tenant_id="tenant-a")
        assert out == [] and calls  # 真消费硬证
        assert env["eng"].get_template("tpl-form-fill", tenant_id="tenant-a") is None
        assert env["eng"].get_template("tpl-form-fill") is not None  # 系统视图不走判法


# ── ② builtin 共享只读目录（DEC-⑩-① 语义） ──────────────────────


class TestBuiltinSharedCatalog:
    def test_builtin_shared_read(self, env):
        from src.limb.tasks import template_row_tenant, template_visible_to

        eng = env["eng"]
        tpl = eng.get_template("tpl-form-fill")  # 系统视图取行
        assert template_row_tenant(tpl) == "default"  # 行键 default 但共享可见
        for tenant in ("tenant-a", "tenant-b", "default"):
            assert template_visible_to(tpl, tenant)
        ids = {t["template_id"] for t in eng.list_templates(tenant_id="tenant-b")}
        assert {"tpl-form-fill", "tpl-data-extract", "tpl-login", "tpl-screenshot"} <= ids

    def test_builtin_output_additive_keys(self, env):
        data = (
            env["client"]
            .get("/api/limb/templates/tpl-form-fill", headers=_token(env["user_b"]))
            .json()
        )
        assert data["builtin"] is True
        assert data["tenant_id"] == "default"

    def test_builtin_delete_denied_and_survives(self, env):
        c = env["client"]
        r = c.delete("/api/limb/templates/tpl-form-fill", headers=_token(env["user_a"]))
        assert r.status_code == 400  # 既有契约：builtin 恒拒
        assert env["eng"].get_template("tpl-form-fill") is not None


# ── ③ create 写点行归属 ─────────────────────────────────────────


class TestWriteAttribution:
    def test_create_row_owned_by_identity(self, env):
        c = env["client"]
        r = _create(c, _token(env["user_a"]))
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "tenant-a"  # 加性键归一值
        assert r.json()["builtin"] is False
        from src.limb.tasks import template_row_tenant

        assert template_row_tenant(env["eng"].get_template(r.json()["template_id"])) == "tenant-a"

    def test_create_anonymous_default_contract(self, env):
        r = _create(env["client"], None)
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "default"

    def test_create_self_report_ignored(self, env):
        """请求体自报 tenant_id 不进落行（派生轴纯，⑦-⑰ 同款）。"""
        c = env["client"]
        r = c.post(
            "/api/limb/templates",
            json={"name": "self", "category": "custom", "tenant_id": "tenant-b"},
            headers=_token(env["user_a"]),
        )
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "tenant-a"

    def test_blank_key_fallback(self, env):
        """键派生故障（空白键）绝不吃掉落库→'default' 兜底。"""
        tpl = env["eng"].create_template(
            {"name": "blank", "template_id": "tpl-blank"}, tenant_id="  "
        )
        from src.limb.tasks import template_row_tenant

        assert template_row_tenant(tpl) == "default"
        assert env["eng"].get_template("tpl-blank", tenant_id="default") is not None


# ── ④ by-ID 归属闸（get/instantiate 源读） ────────────────────────


class TestReadGate:
    def test_get_three_way(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"])).json()["template_id"]
        own = c.get(f"/api/limb/templates/{tid}", headers=_token(env["user_a"]))
        cross = c.get(f"/api/limb/templates/{tid}", headers=_token(env["user_b"]))
        missing = c.get("/api/limb/templates/tpl-no-such", headers=_token(env["user_b"]))
        assert own.status_code == 200
        assert cross.status_code == 404
        assert cross.json() == missing.json()  # 与 not-found 同契约不差分泄露（逐字）
        builtin = c.get("/api/limb/templates/tpl-login", headers=_token(env["user_b"]))
        assert builtin.status_code == 200  # builtin 共享可见

    def test_instantiate_source_gate(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"])).json()["template_id"]
        before = env["eng"].stats()["total_tasks"]
        cross = c.post(
            f"/api/limb/templates/{tid}/instantiate", json={}, headers=_token(env["user_b"])
        )
        missing = c.post(
            "/api/limb/templates/tpl-no-such/instantiate", json={}, headers=_token(env["user_b"])
        )
        assert cross.status_code == 404
        assert cross.json() == missing.json()
        assert env["eng"].stats()["total_tasks"] == before  # 零副作用硬证
        own = c.post(
            f"/api/limb/templates/{tid}/instantiate", json={}, headers=_token(env["user_a"])
        )
        assert own.status_code == 200
        assert env["eng"].stats()["total_tasks"] == before + 1

    def test_instantiate_builtin_any_tenant(self, env):
        r = env["client"].post(
            "/api/limb/templates/tpl-screenshot/instantiate",
            json={"variables": {"url": "http://x"}},
            headers=_token(env["user_b"]),
        )
        assert r.status_code == 200


# ── ⑤ list 过滤三面互斥 ─────────────────────────────────────────


class TestListFilter:
    def test_three_way_mutual_exclusion(self, env):
        c = env["client"]
        _create(c, _token(env["user_a"]), name="a-row")
        _create(c, _token(env["user_b"]), name="b-row")
        _create(c, None, name="default-row")
        a_ids = {
            t["name"]
            for t in c.get("/api/limb/templates", headers=_token(env["user_a"])).json()["templates"]
        }
        b_ids = {
            t["name"]
            for t in c.get("/api/limb/templates", headers=_token(env["user_b"])).json()["templates"]
        }
        anon_ids = {t["name"] for t in c.get("/api/limb/templates").json()["templates"]}
        assert "a-row" in a_ids and "b-row" not in a_ids and "default-row" not in a_ids
        assert "b-row" in b_ids and "a-row" not in b_ids
        assert "default-row" in anon_ids and "a-row" not in anon_ids and "b-row" not in anon_ids

    def test_default_user_face_equals_anonymous(self, env):
        c = env["client"]
        _create(c, None, name="default-row")
        anon = c.get("/api/limb/templates").json()["templates"]
        tok = c.get("/api/limb/templates", headers=_token(env["user_default"])).json()["templates"]
        assert anon == tok  # default 用户面==匿名面（⑦-⑬ 同款）

    def test_category_filter_stack(self, env):
        c = env["client"]
        c.post(
            "/api/limb/templates",
            json={"name": "cat-x", "category": "data_extract"},
            headers=_token(env["user_a"]),
        )
        out = c.get(
            "/api/limb/templates?category=data_extract", headers=_token(env["user_a"])
        ).json()["templates"]
        assert {t["name"] for t in out} == {"数据提取", "cat-x"}


# ── ⑥ delete 归属闸（写面恒按行归属+builtin 保护） ────────────────


class TestDeleteGate:
    def test_cross_tenant_same_contract_as_missing(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"])).json()["template_id"]
        cross = c.delete(f"/api/limb/templates/{tid}", headers=_token(env["user_b"]))
        missing = c.delete("/api/limb/templates/tpl-no-such", headers=_token(env["user_b"]))
        assert cross.status_code == missing.status_code  # 同契约不差分泄露
        assert cross.json() == missing.json()
        assert env["eng"].get_template(tid) is not None  # 行存活零破坏

    def test_own_delete(self, env):
        c = env["client"]
        tid = _create(c, _token(env["user_a"])).json()["template_id"]
        r = c.delete(f"/api/limb/templates/{tid}", headers=_token(env["user_a"]))
        assert r.status_code == 200
        assert env["eng"].get_template(tid) is None


# ── ⑦ create 覆盖写面（劫持闸+builtin 保护，进程内语义） ─────────


class TestCreateWriteFace:
    def test_hijack_denied_row_untouched(self, env):
        from src.limb.tasks import TemplateTenantDenied, template_row_tenant

        eng = env["eng"]
        tid = _create(env["client"], _token(env["user_a"])).json()["template_id"]
        with pytest.raises(TemplateTenantDenied):
            eng.create_template({"name": "hijack", "template_id": tid}, tenant_id="tenant-b")
        row = eng.get_template(tid)
        assert row.name != "hijack" and template_row_tenant(row) == "tenant-a"  # 行零改写

    def test_builtin_overwrite_denied(self, env):
        from src.limb.tasks import TemplateTenantDenied

        with pytest.raises(TemplateTenantDenied):
            env["eng"].create_template(
                {"name": "evil", "template_id": "tpl-form-fill"}, tenant_id="tenant-a"
            )
        assert env["eng"].get_template("tpl-form-fill").name == "表单填写"  # 种子完好

    def test_same_tenant_overwrite_ok(self, env):
        eng = env["eng"]
        tid = _create(env["client"], _token(env["user_a"])).json()["template_id"]
        tpl = eng.create_template({"name": "mine-v2", "template_id": tid}, tenant_id="tenant-a")
        assert tpl.name == "mine-v2"

    def test_system_view_overwrite_unchanged(self, env):
        """None=系统视图字节不变：非 builtin 行覆盖既有语义不触碰。"""
        eng = env["eng"]
        eng.create_template({"name": "sys", "template_id": "tpl-sys"}, tenant_id="tenant-a")
        tpl = eng.create_template({"name": "sys-v2", "template_id": "tpl-sys"})
        assert tpl.name == "sys-v2" and tpl.tenant_id == "default"


# ── ⑧ stats 聚合作用域 ──────────────────────────────────────────


class TestStatsScope:
    def test_stats_scoped_health_system_view(self, env):
        c = env["client"]
        _create(c, _token(env["user_a"]), name="a-row")
        _create(c, _token(env["user_b"]), name="b-row")
        sa = c.get("/api/limb/stats", headers=_token(env["user_a"])).json()
        sb = c.get("/api/limb/stats", headers=_token(env["user_b"])).json()
        anon = c.get("/api/limb/stats").json()
        health = c.get("/api/limb/health").json()
        assert sa["templates"] == 5  # 4 builtin + own
        assert sb["templates"] == 5
        assert anon["templates"] == 4  # 仅 builtin（default 无自定义行）
        assert health["templates"] == 6  # /health 系统视图=全量（4 builtin + 2）
        assert sa["total_tasks"] == health["total_tasks"]  # 任务面键不在本拍不随租户


# ── ⑨ 鉴权（坏 token 401 先于读/写零副作用） ─────────────────────


class TestAuth:
    def test_bad_token_battery(self, env):
        c = env["client"]
        before = env["eng"].stats()["templates"]
        assert c.get("/api/limb/templates", headers=BAD_TOKEN).status_code == 401
        assert c.get("/api/limb/templates/tpl-x", headers=BAD_TOKEN).status_code == 401
        assert (
            c.post("/api/limb/templates", json={"name": "x"}, headers=BAD_TOKEN).status_code == 401
        )
        assert c.delete("/api/limb/templates/tpl-x", headers=BAD_TOKEN).status_code == 401
        assert (
            c.post("/api/limb/templates/tpl-x/instantiate", json={}, headers=BAD_TOKEN).status_code
            == 401
        )
        assert c.get("/api/limb/stats", headers=BAD_TOKEN).status_code == 401
        assert env["eng"].stats()["templates"] == before  # 零写入
