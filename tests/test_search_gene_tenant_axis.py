"""unified gene 子搜索存储层加轴测试（DEC-⑦-③：gene 面最小件）。

修前（HEAD 源码实证）：_search_gene_templates 读模板注册表零租户过滤=任意租户
可见全部模板（与 timeline ③c-2① / events DEC-⑦-① / echo DEC-⑦-② 已收的查询
过滤不对称=跨租户泄露）；且 Template 行无租户键、写点（create/import/clone）
零身份轴=行归属无从谈起。收口=template_row_tenant 单一判法（模板自带键缺失/
空白→'default' 兜底，DEC-⑦-① _event_row_tenant / ② message_row_tenant 同款；
写读同一判法）+Template.tenant_id 行键落盘（_save_template JSON 往返）+写点
行归属（create/import/clone=bind_identity 派生租户、缺省→'default' 系统写方）
+跨租户劫持闸（同名 create/import 覆盖=行归属不可被跨租户改写，F3b-③c-2②d
同款）+delete 归属闸（跨租户删除=零副作用与 not-found 同契约）+list_templates
(tenant_id=) 恒按行租户键过滤（None=系统视图既有调用面字节不变；过滤先于
截断=limit 只计可见命中）+_search_gene_templates 恒按派生租户过滤+读真源=
src.api.gene 模块单例 registry。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/tmp TemplateEngine 绝不碰生产
~/.opensoul/templates）。
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
    """tmp 身份库 + tmp TemplateEngine（monkeypatch src.api.gene.engine 模块单例）
    + 真 search/gene router 真 bind_identity。搜索与写面读同一真源实例。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    # HOME 对齐：旧代码 _search_gene_templates 函数内 new TemplateEngine() 读
    # ~/.opensoul/templates（expanduser 随 HOME）——把默认存储也归 tmp，控制组
    # 旧代码才读得到 seed 行（泄露暴露在行为断言而非空集假象）。
    monkeypatch.setenv("HOME", str(tmp_path))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.gene as gene_api
    from src.gene.templates import TemplateEngine

    fresh = TemplateEngine(storage_dir=tmp_path / ".opensoul" / "templates")
    monkeypatch.setattr(gene_api, "engine", fresh)

    import src.api.search as search_api

    app = FastAPI()
    app.include_router(search_api.router, prefix="/api/search")
    app.include_router(gene_api.router, prefix="/api/gene")
    client = TestClient(app)

    user_tenant_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_tenant_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")

    yield {
        "client": client,
        "engine": fresh,
        "user_tenant_a": user_tenant_a,
        "user_tenant_b": user_tenant_b,
        "user_default": user_default,
    }


def _titles(client, headers=None, params=None):
    p = {"q": "probe", "sources": "gene", "limit": 10}
    p.update(params or {})
    r = client.get("/api/search/unified", params=p, headers=headers or {})
    assert r.status_code == 200
    return [m["title"] for m in r.json()["by_source"]["gene"]]


def _seed_rows(eng, tenant_a: int = 2, tenant_default: int = 2):
    """直写存储层行（行键 setattr 直写，写点归属另有专测）：tenant-a 行
    probe-ta-N、default 行 probe-td-N。行同时落盘（_save_template）——控制组旧
    代码的 fresh TemplateEngine 读盘才看得见（否则空集假象盖住泄露反证）。
    控制组纪律：seed 不依赖新 ctor 签名，让旧代码失败暴露在行为断言而非
    fixture TypeError。"""
    from src.gene.templates import Template

    def _row(tid: str, name: str, tenant: str):
        t = Template(template_id=tid, name=name, category="skill", description="probe body")
        setattr(t, "tenant_id", tenant)
        eng._templates[tid] = t
        eng._save_template(t)

    # 顺序=default 先、tenant-a 后（created_at 降序=ta 更新排前）：旧代码零过滤时
    # limit 名额被跨租户行占掉——「过滤先于截断」断言才可判。
    for i in range(tenant_default):
        _row(f"seed-td-{i}", f"probe-td-{i}", "default")
    for i in range(tenant_a):
        _row(f"seed-ta-{i}", f"probe-ta-{i}", "tenant-a")


# ── 纯函数：行租户键单一判法 ─────────────────────────────────────────


def test_template_row_tenant_fallback_forms():
    from src.gene.templates import Template, template_row_tenant

    assert (
        template_row_tenant(Template(template_id="x", name="", category="", description=""))
        == "default"
    )
    none_key = Template(template_id="x", name="", category="", description="")
    setattr(none_key, "tenant_id", None)  # 防御形（历史行 None 键）走同一兜底
    assert template_row_tenant(none_key) == "default"
    assert (
        template_row_tenant(
            Template(template_id="x", name="", category="", description="", tenant_id="   ")
        )
        == "default"
    )
    assert (
        template_row_tenant(
            Template(template_id="x", name="", category="", description="", tenant_id="tenant-a")
        )
        == "tenant-a"
    )


def test_template_row_tenant_single_source():
    """判法单一源锁死：list_templates 过滤、delete 归属闸、create/import 劫持闸
    同一判法（禁消费点另发明）。"""
    from src.gene.templates import TemplateEngine

    for method in (
        TemplateEngine.list_templates,
        TemplateEngine.delete_template,
        TemplateEngine.create_template,
    ):
        src = inspect.getsource(method)
        assert "template_row_tenant(" in src
        assert ".tenant_id ==" not in src


# ── 写点行归属（真 gene API 真 bind_identity）─────────────────────────


def test_create_api_row_attribution_derived(env):
    """POST /api/gene/templates：token 派生租户落行（修前恒 'default'）、匿名 'default'。"""
    from src.gene.templates import template_row_tenant

    client, eng = env["client"], env["engine"]
    body = {"template_id": "probe-attr-a", "name": "probe", "category": "skill"}
    r = client.post("/api/gene/templates", json=body, headers=_token(env["user_tenant_a"]))
    assert r.status_code == 200
    body2 = {"template_id": "probe-attr-anon", "name": "probe", "category": "skill"}
    r2 = client.post("/api/gene/templates", json=body2)
    assert r2.status_code == 200
    assert template_row_tenant(eng.get_template("probe-attr-a")) == "tenant-a"
    assert template_row_tenant(eng.get_template("probe-attr-anon")) == "default"


def test_write_direct_blank_fallback(env):
    """系统写方直调缺省/空白→'default' 兜底（键派生故障绝不吃掉落库）。"""
    from src.gene.templates import template_row_tenant

    eng = env["engine"]
    eng.create_template({"template_id": "probe-sys", "name": "probe", "category": "skill"})
    eng.create_template(
        {"template_id": "probe-sys-blank", "name": "probe", "category": "skill"}, tenant_id="   "
    )
    assert template_row_tenant(eng.get_template("probe-sys")) == "default"
    assert template_row_tenant(eng.get_template("probe-sys-blank")) == "default"


def test_create_bad_token_401_zero_row(env):
    """坏 token 401 先于任何写入（F3b-⑤ 纪律）。"""
    client, eng = env["client"], env["engine"]
    r = client.post(
        "/api/gene/templates",
        json={"template_id": "probe-bad", "name": "probe", "category": "skill"},
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert r.status_code == 401
    assert eng.get_template("probe-bad") is None


def test_import_and_clone_attribution(env):
    """POST /api/gene/import 与 /clone：行归属=身份派生租户；clone 新行归属
    克隆者不继承源行键（DEC-⑦-⑫ 契约随更新：clone 源读归属闸后跨租户源克隆
    拒收=与缺源同契约，原「B 克隆 A」例改跨租户拒收+同租户克隆+系统视图不继承
    三向断言——「克隆归克隆者不继承源行键」断言意图零丢失，收口的是跨租户源
    可读面=⑦-③ 边界⑤ 留档件）。"""
    from src.gene.templates import template_row_tenant

    client, eng = env["client"], env["engine"]
    r = client.post(
        "/api/gene/import",
        json={"templates": [{"template_id": "probe-imp", "name": "probe", "category": "skill"}]},
        headers=_token(env["user_tenant_a"]),
    )
    assert r.status_code == 200
    assert r.json()["imported"] == 1
    assert template_row_tenant(eng.get_template("probe-imp")) == "tenant-a"

    # 跨租户源克隆=拒收（DEC-⑦-⑫ 源读归属闸，与缺源同契约）+零副作用
    bad = client.post(
        "/api/gene/templates/probe-imp/clone",
        json={"new_id": "probe-imp-copy"},
        headers=_token(env["user_tenant_b"]),
    )
    assert bad.status_code == 400
    assert bad.json()["detail"] == "Source template 'probe-imp' not found"
    assert eng.get_template("probe-imp-copy") is None  # 零克隆副作用
    assert template_row_tenant(eng.get_template("probe-imp")) == "tenant-a"  # 源行键不动

    # 同租户克隆：新行归属克隆者
    r2 = client.post(
        "/api/gene/templates/probe-imp/clone",
        json={"new_id": "probe-imp-copy"},
        headers=_token(env["user_tenant_a"]),
    )
    assert r2.status_code == 200
    assert template_row_tenant(eng.get_template("probe-imp-copy")) == "tenant-a"

    # 系统视图克隆（tenant_id=None 既有语义）：新行 'default' 不继承源行键
    sysc, _ = eng.clone_template("probe-imp", new_id="probe-imp-sys")
    assert sysc is not None
    assert template_row_tenant(eng.get_template("probe-imp-sys")) == "default"
    assert template_row_tenant(eng.get_template("probe-imp")) == "tenant-a"  # 源行键不动


# ── 劫持闸门（行归属不可被跨租户改写）────────────────────────────────


def test_cross_tenant_create_hijack_refused(env):
    client, eng = env["client"], env["engine"]
    body = {"template_id": "probe-hijack", "name": "name-a", "category": "skill"}
    assert (
        client.post(
            "/api/gene/templates", json=body, headers=_token(env["user_tenant_a"])
        ).status_code
        == 200
    )
    bad = client.post(
        "/api/gene/templates",
        json={"template_id": "probe-hijack", "name": "name-b", "category": "skill"},
        headers=_token(env["user_tenant_b"]),
    )
    assert bad.status_code == 403
    from src.gene.templates import template_row_tenant

    row = eng.get_template("probe-hijack")
    assert row.name == "name-a"  # 行零改写
    assert template_row_tenant(row) == "tenant-a"


def test_cross_tenant_delete_refused_row_survives(env):
    client, eng = env["client"], env["engine"]
    assert (
        client.post(
            "/api/gene/templates",
            json={"template_id": "probe-del", "name": "probe", "category": "skill"},
            headers=_token(env["user_tenant_a"]),
        ).status_code
        == 200
    )
    bad = client.delete("/api/gene/templates/probe-del", headers=_token(env["user_tenant_b"]))
    assert bad.status_code == 400  # 与 not-found 同契约（不差分泄露）
    assert eng.get_template("probe-del") is not None  # 行存活
    ok = client.delete("/api/gene/templates/probe-del", headers=_token(env["user_tenant_a"]))
    assert ok.status_code == 200
    assert eng.get_template("probe-del") is None


# ── 租户过滤行为（真 unified 真 bind_identity）────────────────────────


def test_anonymous_sees_default_and_unkeyed_only(env):
    client, eng = env["client"], env["engine"]
    _seed_rows(eng)
    eng.create_template(
        {"template_id": "probe-unkeyed", "name": "probe-unkeyed", "category": "skill"}
    )
    titles = _titles(client)
    assert sorted(titles) == ["probe-td-0", "probe-td-1", "probe-unkeyed"]


def test_tenant_a_sees_only_own_rows(env):
    client, eng = env["client"], env["engine"]
    _seed_rows(eng)
    titles = _titles(client, headers=_token(env["user_tenant_a"]))
    assert sorted(titles) == ["probe-ta-0", "probe-ta-1"]


def test_default_tenant_user_same_as_anonymous(env):
    client, eng = env["client"], env["engine"]
    _seed_rows(eng)
    anon = sorted(_titles(client))
    default_user = sorted(_titles(client, headers=_token(env["user_default"])))
    assert anon == default_user == ["probe-td-0", "probe-td-1"]


def test_tenant_axis_derived_not_self_reported(env):
    """tenant 从认证派生禁自报：token=tenant-a + user_id 自报 'default'（未声明
    →取派生），过滤仍按派生租户 tenant-a。"""
    client, eng = env["client"], env["engine"]
    _seed_rows(eng)
    titles = _titles(client, headers=_token(env["user_tenant_a"]), params={"user_id": "default"})
    assert sorted(titles) == ["probe-ta-0", "probe-ta-1"]


def test_limit_counts_only_visible_rows(env):
    """过滤先于截断=limit 只计可见命中（跨租户行不占名额，DEC-⑦-① 同款）。"""
    client, eng = env["client"], env["engine"]
    _seed_rows(eng, tenant_a=3, tenant_default=2)
    titles = _titles(client, params={"limit": 2})
    assert len(titles) == 2
    assert all(t.startswith("probe-td-") for t in titles)


# ── 语义/契约 ────────────────────────────────────────────────────────


def test_read_source_module_singleton(env):
    """读真源收口硬证：只存在于模块单例内存注册表（未落盘）的行能被 unified gene
    子搜索搜到（修前函数内 new TemplateEngine() 重扫盘=读不到内存行，旧代码此例
    必空）。"""
    from src.gene.templates import Template

    client, eng = env["client"], env["engine"]
    t = Template(template_id="probe-mem", name="probe-mem", category="skill", description="x")
    eng._templates["probe-mem"] = t  # 不经 _save_template=磁盘无行
    titles = _titles(client)
    assert "probe-mem" in titles


def test_result_shape_and_match_semantics_unchanged(env):
    client, eng = env["client"], env["engine"]
    _seed_rows(eng, tenant_a=0, tenant_default=1)
    p = {"q": "probe body", "sources": "gene", "limit": 10}
    r = client.get("/api/search/unified", params=p)
    assert r.status_code == 200
    entries = r.json()["by_source"]["gene"]
    assert entries, "description 匹配语义不变（修前即按 name/description/category 匹配）"
    assert set(entries[0].keys()) == {
        "source",
        "icon",
        "title",
        "snippet",
        "template_id",
        "category",
    }


def test_system_view_none_and_single_tenant_contract(env):
    """list_templates(tenant_id=None)=系统视图全量（既有调用面字节不变）；单租户全
    'default' 时匿名全见=本地单机契约零变化。DEC-⑩-① 契约随更新：非 default 租户
    见共享 builtin 目录（修前 0 行）但 default 用户行恒不可见。"""
    from src.gene.templates import template_row_tenant

    client, eng = env["client"], env["engine"]
    _seed_rows(eng, tenant_a=0, tenant_default=3)
    all_rows = eng.list_templates()
    assert len(eng.list_templates(tenant_id=None)) == len(all_rows) > 3
    assert len(eng.list_templates(tenant_id="default")) == len(all_rows)  # 单租户全 default
    rows_a = eng.list_templates(tenant_id="tenant-a")
    assert all(r["builtin"] for r in rows_a)  # 只见共享目录，default 用户行恒不可见
    assert {r["template_id"] for r in rows_a} == {
        r["template_id"] for r in all_rows if r["builtin"]
    }
    assert all(
        template_row_tenant(eng.get_template(r["template_id"])) == "default" for r in all_rows
    )
    assert len(_titles(client)) == 3  # 匿名搜索 3 条 seed 行全见（builtin 无 probe 不命中）
