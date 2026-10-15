"""builtin starter 目录跨租户共享语义测试（DEC-⑩-①，pending-decisions ⑩ 建议默认 B）。

修前（⑦-③ 边界②「暂与其他面同款严格过滤宁窄勿滥」）：builtin starter 目录
（author='system'、builtin=True、引擎启动注册）行键='default'，非 default 租户
的 gene 搜索/列表/categories/tags/stats/export/get/instantiate 全部不见 starter
目录=多租户下新租户目录空、starter 语义不成立（严格过滤是租户轴机械化的副作用）。

收口=template_visible_to 读面可见性单一判法（builtin 共享只读目录全租户可见，
其余行恒按行归属 template_row_tenant 单一源）+7 读面真消费（list/get/categories/
tags/search/export_all/stats）+目录列表面 GET /api/gene/templates 漏网点同拍收口
（修前零鉴权恒系统视图=任意调用方可见全部租户行）。写面零变化（测试锁死）：
delete builtin 恒拒、创建劫持闸/import 覆盖仍按行归属（共享≠可写）。

测试面=conftest gene_client 隔离面 + tmp 身份库（USERS_DB/ENTERPRISE_DB env，
绝不碰生产）。
"""

import inspect
import sqlite3
import uuid

import pytest

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


def _auth(uid):
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def env(gene_client, tmp_path, monkeypatch):
    """gene_client 隔离面 + tmp 身份库（真 bind_identity）+ 三种子用户。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.gene as gene_api

    yield {
        "api": gene_client,
        "engine": gene_api.engine,
        "ua": _seed_user(users_db, str(uuid.uuid4()), "alice-shared", "tenant-a"),
        "ub": _seed_user(users_db, str(uuid.uuid4()), "bob-shared", "tenant-b"),
        "ud": _seed_user(users_db, str(uuid.uuid4()), "carol-shared", "default"),
    }


def _seed(env, tid, tenant, category="skill", tags=None, name=None, config=None):
    return env["engine"].create_template(
        {
            "template_id": tid,
            "name": name or ("probe " + tid),
            "category": category,
            "description": "probe body " + tid,
            "tags": list(tags or []),
            "config": config if config is not None else {"k": "v-" + tid},
        },
        tenant_id=tenant,
    )


def _ids(rows):
    return {r["template_id"] for r in rows}


# ── 判法单一源（写读两个单一源，禁消费点另发明）────────────────────


def test_visible_predicate_semantics():
    """builtin 全租户可见 / own 行可见 / 他租户行不可见 / 空白键='default'。"""
    from src.gene.templates import Template, template_visible_to

    builtin = Template(template_id="b1", name="", category="", description="", builtin=True)
    own = Template(template_id="u1", name="", category="", description="", tenant_id="tenant-a")
    blank = Template(template_id="u2", name="", category="", description="", tenant_id="  ")
    for tenant in ("tenant-a", "tenant-b", "default", "tenant-zzz"):
        assert template_visible_to(builtin, tenant) is True  # 共享目录
    assert template_visible_to(own, "tenant-a") is True
    assert template_visible_to(own, "tenant-b") is False
    assert template_visible_to(blank, "default") is True
    assert template_visible_to(blank, "tenant-a") is False


def test_visible_delegates_and_single_source():
    """单一源锁死：可见性判法委托 template_row_tenant（禁手抄归属判法）；
    读面走 template_visible_to、写面（delete/create/import）仍走行归属判法，
    且两边互不越界（可见≠可写）。"""
    from src.gene.templates import TemplateEngine, template_row_tenant, template_visible_to

    assert "template_row_tenant(" in inspect.getsource(template_visible_to)
    assert ".tenant_id ==" not in inspect.getsource(template_visible_to)

    read_methods = (
        TemplateEngine.list_templates,
        TemplateEngine.get_template,
        TemplateEngine.categories,
        TemplateEngine.tags,
        TemplateEngine.search,
        TemplateEngine.export_all,
        TemplateEngine.stats,
    )
    for method in read_methods:
        src = inspect.getsource(method)
        assert "template_visible_to(" in src, method.__name__
        assert ".tenant_id ==" not in src, method.__name__

    for method in (
        TemplateEngine.delete_template,
        TemplateEngine.create_template,
        TemplateEngine.import_template,
    ):
        src = inspect.getsource(method)
        assert "template_row_tenant(" in src, method.__name__
        assert "template_visible_to(" not in src, method.__name__  # 写面禁用可见性判法


# ── 共享目录读面（engine 层七面）─────────────────────────────────


def test_list_shared_catalog_plus_own(env):
    """list：A 见 builtin 目录+己行，B 行恒不可见（用户行零泄露）。"""
    eng = env["engine"]
    base = _ids(eng.list_templates(tenant_id="default"))  # builtin(+default 行)
    _seed(env, "sh-list-a", "tenant-a")
    _seed(env, "sh-list-b", "tenant-b")
    ids_a = _ids(eng.list_templates(tenant_id="tenant-a"))
    assert "sh-list-a" in ids_a and "sh-list-b" not in ids_a
    assert base <= ids_a  # 共享目录全量在场（修前严格过滤=builtin 全不见）
    assert "agent-rag-assistant" in ids_a  # starter 目录真可见


def test_get_byid_builtin_shared(env):
    """by-ID：builtin 对 A 可见（修前 None）、own 可见、他租户行仍 None 同契约。"""
    eng = env["engine"]
    _seed(env, "sh-get-a", "tenant-a")
    assert eng.get_template("agent-rag-assistant", tenant_id="tenant-a") is not None
    assert eng.get_template("agent-rag-assistant", tenant_id="tenant-b") is not None
    assert eng.get_template("sh-get-a", tenant_id="tenant-a") is not None
    assert eng.get_template("sh-get-a", tenant_id="tenant-b") is None  # 他租户仍不可见
    assert eng.get_template("no-such", tenant_id="tenant-a") is None  # 缺行同契约


def test_search_categories_tags_shared(env):
    """search/categories/tags：builtin 贡献对各租户一致，B 行贡献恒不混入。"""
    eng = env["engine"]
    base_cats = {i["category"]: i["count"] for i in eng.categories(tenant_id="default")}
    base_tags = {i["tag"]: i["count"] for i in eng.tags(tenant_id="default")}
    _seed(env, "sh-agg-a", "tenant-a", category="skill", tags=["alpha"])
    _seed(env, "sh-agg-b", "tenant-b", category="workflow", tags=["beta"])

    cats_a = {i["category"]: i["count"] for i in eng.categories(tenant_id="tenant-a")}
    assert cats_a.get("skill", 0) == base_cats.get("skill", 0) + 1  # +己行
    assert cats_a.get("workflow", 0) == base_cats.get("workflow", 0)  # B 行不混

    tags_a = {i["tag"]: i["count"] for i in eng.tags(tenant_id="tenant-a")}
    assert tags_a.get("alpha", 0) == 1
    assert tags_a.get("beta", 0) == base_tags.get("beta", 0)  # B 行不混

    # starter 搜索词对 A 可见（修前严格过滤=恒空）
    hits = eng.search("RAG", tenant_id="tenant-a")
    assert "agent-rag-assistant" in _ids(hits)
    # B 的 needle 行对 A 恒不可见（既有 ⑦-⑯ 意图零丢失）
    _seed(env, "sh-needle-b", "tenant-b", name="needle beta")
    assert _ids(eng.search("needle", tenant_id="tenant-a")) == set()


def test_stats_and_export_shared(env):
    """stats/export_all：builtin 计数/行进 A 面（修前 builtin_count=0/不含 builtin），
    B 行恒不进；include_builtin=False 精确排除共享目录。"""
    eng = env["engine"]
    base = eng.stats(tenant_id="default")
    _seed(env, "sh-st-a", "tenant-a")
    _seed(env, "sh-st-b", "tenant-b", category="workflow")

    st_a = eng.stats(tenant_id="tenant-a")
    assert st_a["builtin_count"] == base["builtin_count"]  # 共享目录全量（修前 0）
    assert st_a["builtin_count"] > 0
    assert st_a["user_count"] == 1  # B 行恒不计
    assert st_a["total_templates"] == base["builtin_count"] + 1

    rows = eng.export_all(tenant_id="tenant-a")
    ids = _ids(rows)
    assert "agent-rag-assistant" in ids and "sh-st-a" in ids and "sh-st-b" not in ids
    only_own = _ids(eng.export_all(include_builtin=False, tenant_id="tenant-a"))
    assert only_own == {"sh-st-a"}  # 共享目录可精确排除


def test_instantiate_and_clone_builtin(env):
    """instantiate/clone 共享目录：A 可用（修前与 not-found 同契约）；clone 新行
    归克隆者（⑦-⑫ 语义零变化），源 builtin 行完好（共享行不被改写身份）。"""
    from src.gene.templates import template_row_tenant

    eng = env["engine"]
    ok = eng.instantiate("agent-rag-assistant", {}, tenant_id="tenant-a")
    assert ok["success"] and ok["config"]  # 真实例化出 config
    cl, msg = eng.clone_template(
        "agent-rag-assistant", new_id="sh-builtin-copy", tenant_id="tenant-a"
    )
    assert cl is not None
    assert template_row_tenant(cl) == "tenant-a"  # clone 归克隆者
    assert cl.builtin is False  # 克隆件=用户行
    src_row = eng.get_template("agent-rag-assistant")
    assert src_row.builtin and template_row_tenant(src_row) == "default"  # 源行身份不变


# ── 写面零变化（共享≠可写）──────────────────────────────────────


def test_write_faces_unchanged(env):
    """delete builtin 恒拒（含 default）/创建劫持闸/import 覆盖拒——共享目录不可写。"""
    from src.gene.templates import TemplateTenantDenied

    eng = env["engine"]
    # delete builtin：全租户恒拒，行存活
    assert eng.delete_template("agent-rag-assistant", tenant_id="tenant-a") is False
    assert eng.delete_template("agent-rag-assistant", tenant_id="default") is False
    assert eng.get_template("agent-rag-assistant") is not None

    # 创建劫持闸：同 builtin id 覆盖=拒（注册表全局 id 命名空间）
    with pytest.raises(TemplateTenantDenied):
        eng.create_template(
            {"template_id": "agent-rag-assistant", "name": "x", "category": "skill"},
            tenant_id="tenant-a",
        )
    assert eng.get_template("agent-rag-assistant").builtin is True  # 行零改写

    # import 覆盖 builtin：A 恒拒（builtin 行归属='default'）
    tpl, msg = eng.import_template(
        {"template_id": "agent-rag-assistant", "name": "x", "category": "skill"},
        overwrite=True,
        tenant_id="tenant-a",
    )
    assert tpl is None
    assert eng.get_template("agent-rag-assistant").builtin is True  # 行零改写


# ── API 全链（目录列表面漏网点收口）──────────────────────────────


def test_api_list_catalog_scope(env):
    """GET /api/gene/templates：A 见共享目录+己行不见 B 行（修前零鉴权全量泄露）、
    匿名=default 面、加性键 tenant_id、响应形状零破坏。"""
    api = env["api"]
    _seed(env, "sh-api-a", "tenant-a")
    _seed(env, "sh-api-b", "tenant-b")

    r = api.get("/api/gene/templates", headers=_auth(env["ua"]))
    assert r.status_code == 200
    body = r.json()
    assert "templates" in body and body["tenant_id"] == "tenant-a"
    ids = _ids(body["templates"])
    assert "sh-api-a" in ids and "agent-rag-assistant" in ids
    assert "sh-api-b" not in ids  # 修前=全量泄露，硬反证点

    anon = api.get("/api/gene/templates").json()
    assert anon["tenant_id"] == "default"
    anon_ids = _ids(anon["templates"])
    assert (
        "agent-rag-assistant" in anon_ids
        and "sh-api-a" not in anon_ids
        and "sh-api-b" not in anon_ids
    )


def test_api_list_bad_token_401(env):
    """坏 token 401 先于读（修前零鉴权 200 系统视图）。"""
    api = env["api"]
    r = api.get("/api/gene/templates", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_api_catalog_zero_user_row_leak(env):
    """目录面用户行零泄露（漏网点收口本体）：B 行在 A/匿名/UD 三面恒不可见。"""
    api = env["api"]
    _seed(env, "sh-leak-b", "tenant-b", name="secret b", config={"k": "v-secret"})
    for headers in (_auth(env["ua"]), _auth(env["ud"]), {}):
        body = api.get("/api/gene/templates", headers=headers).json()
        ids = _ids(body["templates"])
        assert "sh-leak-b" not in ids
        rows = body["templates"]
        assert all(r.get("config") != {"k": "v-secret"} for r in rows)
