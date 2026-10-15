"""gene 管理面 bulk 读加轴测试（DEC-⑦-⑯）。

修前（⑦-⑫ 边界② 留档定性）：export_all / stats / categories / tags / search
五个 bulk 读面恒系统视图——任意调用方可见全部租户模板的分类分布、标签分布、
搜索命中与整库导出（config 全文）+stats 聚合泄露全局分布；与 list_templates
（⑦-③ 已滤）不对称。

收口=TemplateEngine 五方法增 tenant_id 可选过滤（显式租户=恒按 template_row_tenant
单一判法过滤/聚合，None=系统视图字节不变）+API 五端点 bind_identity 派生流入
（匿名='default' 面，⑦-⑬⑭ 同款；tenant 从认证派生禁自报）+加性键 tenant_id
（作用域可观测，⑦-⑭ 同款）。

测试面=conftest gene_client 隔离面（真实 gene router + tmp TemplateEngine，
test_gene_test_isolation 护栏口径）+ tmp 身份库（USERS_DB/ENTERPRISE_DB env，
绝不碰生产）。
"""

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
        "ua": _seed_user(users_db, str(uuid.uuid4()), "alice-bulk", "tenant-a"),
        "ub": _seed_user(users_db, str(uuid.uuid4()), "bob-bulk", "tenant-b"),
        "ud": _seed_user(users_db, str(uuid.uuid4()), "carol-bulk", "default"),
    }


def _seed(env, tid, tenant, category="skill", tags=None, name=None):
    """写点归属 seed（⑦-③ 既有 create_template(tenant_id=) 签名）。"""
    return env["engine"].create_template(
        {
            "template_id": tid,
            "name": name or ("probe " + tid),
            "category": category,
            "description": "probe body " + tid,
            "tags": list(tags or []),
            "config": {"k": "v-" + tid},
        },
        tenant_id=tenant,
    )


def _by_cat(items):
    return {i["category"]: i["count"] for i in items}


def _by_tag(items):
    return {i["tag"]: i["count"] for i in items}


def _plus(base, extra):
    """DEC-⑩-① 契约随更新：租户面=共享 builtin 目录（base=default 面基线）+己行。"""
    out = dict(base)
    for k, v in extra.items():
        out[k] = out.get(k, 0) + v
    return out


# ── store 层五方法（TemplateEngine bulk 读）────────────────────────


def test_categories_scoped_three_way(env):
    """categories 三向（DEC-⑩-① 契约随更新）：A=共享 builtin 目录+己行 / B 同 /
    default 面不受 A·B 影响 / 系统视图全量——B 行恒不可见（用户行零泄露意图零丢失）。"""
    eng = env["engine"]
    base_d = _by_cat(eng.categories(tenant_id="default"))
    _seed(env, "bulk-cat-a1", "tenant-a", category="skill")
    _seed(env, "bulk-cat-a2", "tenant-a", category="workflow")
    _seed(env, "bulk-cat-b1", "tenant-b", category="skill")
    cats_a = _by_cat(eng.categories(tenant_id="tenant-a"))
    cats_b = _by_cat(eng.categories(tenant_id="tenant-b"))
    assert cats_a == _plus(base_d, {"skill": 1, "workflow": 1})  # 不见 B 的 skill 行
    assert cats_b == _plus(base_d, {"skill": 1})
    assert _by_cat(eng.categories(tenant_id="default")) == base_d  # default 面零变化
    sys_counts = _by_cat(eng.categories())
    assert sys_counts["skill"] == base_d.get("skill", 0) + 2  # A 1 + B 1
    assert sys_counts["workflow"] == base_d.get("workflow", 0) + 1  # A 1


def test_tags_scoped_three_way(env):
    """tags 三向（DEC-⑩-① 契约随更新）：同名 tag 跨租户计数互不混、聚合禁泄露全局
    分布——租户面=共享 builtin 目录+己行，B 行贡献恒不混入。"""
    eng = env["engine"]
    base_d = _by_tag(eng.tags(tenant_id="default"))
    _seed(env, "bulk-tag-a1", "tenant-a", tags=["alpha", "shared"])
    _seed(env, "bulk-tag-b1", "tenant-b", tags=["beta", "shared"])
    assert _by_tag(eng.tags(tenant_id="tenant-a")) == _plus(base_d, {"alpha": 1, "shared": 1})
    assert _by_tag(eng.tags(tenant_id="tenant-b")) == _plus(base_d, {"beta": 1, "shared": 1})
    assert _by_tag(eng.tags(tenant_id="default")) == base_d
    assert _by_tag(eng.tags())["shared"] == 2  # 系统视图才见全局 2


def test_search_scoped_and_additive_key(env):
    """search：跨租户不可见 + 行加性键 tenant_id（与 list_templates 逐键对齐）+ 系统视图不变。"""
    eng = env["engine"]
    _seed(env, "bulk-srch-a", "tenant-a", name="needle alpha")
    _seed(env, "bulk-srch-b", "tenant-b", name="needle beta")
    rows_a = eng.search("needle", tenant_id="tenant-a")
    assert [r["template_id"] for r in rows_a] == ["bulk-srch-a"]
    assert rows_a[0]["tenant_id"] == "tenant-a"
    # 形状与 list_templates 逐键对齐（⑦-③ 加性契约同款）
    listed = eng.list_templates(tenant_id="tenant-a")
    same = [r for r in listed if r["template_id"] == "bulk-srch-a"][0]
    assert set(rows_a[0].keys()) == set(same.keys())
    rows_b = eng.search("needle", tenant_id="tenant-b")
    assert [r["template_id"] for r in rows_b] == ["bulk-srch-b"]
    sys_rows = eng.search("needle")
    assert {r["template_id"] for r in sys_rows} == {"bulk-srch-a", "bulk-srch-b"}


def test_export_all_scoped_and_filter_compose(env):
    """export_all（DEC-⑩-① 契约随更新）：跨租户 config 全文不泄（B 行恒不进）
    + category/include_builtin 组合过滤 + 系统视图不变——租户面=共享 builtin 目录+己行。"""
    eng = env["engine"]
    _seed(env, "bulk-exp-a1", "tenant-a", category="skill")
    _seed(env, "bulk-exp-a2", "tenant-a", category="workflow")
    _seed(env, "bulk-exp-b1", "tenant-b", category="skill", tags=["expb"])
    builtin_ids = {r["template_id"] for r in eng.export_all() if r["builtin"]}
    rows_a = eng.export_all(tenant_id="tenant-a")
    ids_a = {r["template_id"] for r in rows_a}
    assert ids_a == builtin_ids | {"bulk-exp-a1", "bulk-exp-a2"}  # 不含 B（含共享目录）
    assert all(r["config"] == {"k": "v-" + r["template_id"]} for r in rows_a if not r["builtin"])
    assert all(r["config"].get("k") != "v-bulk-exp-b1" for r in rows_a)  # B config 不泄
    # 组合过滤：category 叠加租户过滤（共享目录中的 skill 类同进）
    skill_a = {r["template_id"] for r in eng.export_all(category="skill", tenant_id="tenant-a")}
    assert skill_a == {i for i in builtin_ids} & {
        r["template_id"] for r in eng.export_all(category="skill")
    } | {"bulk-exp-a1"}
    # include_builtin=False 精确排除共享目录
    assert {
        r["template_id"] for r in eng.export_all(include_builtin=False, tenant_id="tenant-a")
    } == {
        "bulk-exp-a1",
        "bulk-exp-a2",
    }
    sys_ids = {r["template_id"] for r in eng.export_all()}
    assert {"bulk-exp-a1", "bulk-exp-a2", "bulk-exp-b1"} <= sys_ids
    # 导出 dict=import 回灌 payload 形状纯净（不注入行键，⑦-⑫ export_template 同款）
    assert "tenant_id" not in rows_a[0]


def test_stats_scoped_aggregate(env):
    """stats（DEC-⑩-① 契约随更新）：逐键只算可见行（total/builtin/user/by_category）
    禁泄露全局分布——租户面=共享 builtin 目录+己行，B 行恒不计。"""
    eng = env["engine"]
    base_d = eng.stats(tenant_id="default")
    _seed(env, "bulk-st-a1", "tenant-a", category="skill")
    _seed(env, "bulk-st-a2", "tenant-a", category="workflow")
    _seed(env, "bulk-st-b1", "tenant-b", category="skill")
    st_a = eng.stats(tenant_id="tenant-a")
    st_b = eng.stats(tenant_id="tenant-b")
    assert st_a["total_templates"] == base_d["total_templates"] + 2
    assert st_a["user_count"] == 2
    assert st_a["builtin_count"] == base_d["builtin_count"]  # 共享目录（修前恒 0）
    assert st_a["by_category"] == _plus(base_d["by_category"], {"skill": 1, "workflow": 1})
    assert st_b["total_templates"] == base_d["total_templates"] + 1
    assert eng.stats(tenant_id="default") == base_d  # default 面零变化
    st_sys = eng.stats()
    assert st_sys["total_templates"] == base_d["total_templates"] + 3


# ── API 面（bind_identity 派生流入）────────────────────────────────


def test_search_api_scoped(env):
    """/templates/search：派生租户过滤 + 顶层加性键 + 匿名 default 面不见租户行。"""
    api = env["api"]
    _seed(env, "bulk-sapi-a", "tenant-a", name="probe-needle a")
    _seed(env, "bulk-sapi-b", "tenant-b", name="probe-needle b")
    ra = api.get("/api/gene/templates/search/probe-needle", headers=_auth(env["ua"]))
    assert ra.status_code == 200
    body_a = ra.json()
    assert [t["template_id"] for t in body_a["templates"]] == ["bulk-sapi-a"]
    assert body_a["tenant_id"] == "tenant-a"  # 加性键=作用域真值
    rb = api.get("/api/gene/templates/search/probe-needle", headers=_auth(env["ub"]))
    assert [t["template_id"] for t in rb.json()["templates"]] == ["bulk-sapi-b"]
    ran = api.get("/api/gene/templates/search/probe-needle")
    assert [t["template_id"] for t in ran.json()["templates"]] == []  # 匿名 default 面
    assert ran.json()["tenant_id"] == "default"


def test_categories_tags_api_scoped(env):
    """/categories 与 /tags：聚合按派生租户 + 匿名 default 面零变化 + 顶层加性键。"""
    api = env["api"]
    an_before = api.get("/api/gene/categories").json()  # builtin 面基线
    tags_before = api.get("/api/gene/tags").json()
    _seed(env, "bulk-capi-a", "tenant-a", category="workflow", tags=["taga"])
    _seed(env, "bulk-capi-b", "tenant-b", category="skill", tags=["tagb"])
    ca = api.get("/api/gene/categories", headers=_auth(env["ua"])).json()
    cb = api.get("/api/gene/categories", headers=_auth(env["ub"])).json()
    # DEC-⑩-① 契约随更新：租户面=共享 builtin 目录（an_before 基线）+己行，B 行恒不混
    base_cats = _by_cat(an_before["categories"])
    base_tags = _by_tag(tags_before["tags"])
    assert _by_cat(ca["categories"]) == _plus(base_cats, {"workflow": 1})
    assert _by_cat(cb["categories"]) == _plus(base_cats, {"skill": 1})
    assert ca["tenant_id"] == "tenant-a"
    ta = api.get("/api/gene/tags", headers=_auth(env["ua"])).json()
    assert _by_tag(ta["tags"]) == _plus(base_tags, {"taga": 1})
    an = api.get("/api/gene/categories").json()
    assert an == an_before  # 匿名 default 面不受 A/B 租户行影响（builtin 基线零变化）
    assert tags_before == api.get("/api/gene/tags").json()
    assert an["tenant_id"] == "default"


def test_export_api_scoped(env):
    """/export：bundle 只含可见行（config 不泄跨租户）+ 顶层加性键 + 组合过滤。"""
    api = env["api"]
    _seed(env, "bulk-xapi-a", "tenant-a", category="skill")
    _seed(env, "bulk-xapi-b", "tenant-b", category="skill")
    ra = api.get("/api/gene/export", headers=_auth(env["ua"])).json()
    ids_a = {t["template_id"] for t in ra["templates"]}
    # DEC-⑩-① 契约随更新：bundle=共享 builtin 目录+己行，B 行恒不进（config 不泄意图零丢失）
    assert "bulk-xapi-a" in ids_a and "bulk-xapi-b" not in ids_a
    assert any(t["builtin"] for t in ra["templates"])  # 共享目录真进 bundle
    assert ra["count"] == len(ids_a)
    assert ra["tenant_id"] == "tenant-a"
    assert all(t["config"].get("k") != "v-bulk-xapi-b" for t in ra["templates"])
    rf = api.get(
        "/api/gene/export", params={"include_builtin": "false"}, headers=_auth(env["ua"])
    ).json()
    assert {t["template_id"] for t in rf["templates"]} == {"bulk-xapi-a"}
    an = api.get("/api/gene/export").json()
    assert "bulk-xapi-a" not in {t["template_id"] for t in an["templates"]}
    assert an["tenant_id"] == "default"


def test_stats_api_scoped(env):
    """/stats：聚合+派生字段（by_author/total_usage/most_used）全部按派生租户。"""
    api = env["api"]
    _seed(env, "bulk-tapi-a1", "tenant-a", category="skill")
    _seed(env, "bulk-tapi-a2", "tenant-a", category="workflow")
    _seed(env, "bulk-tapi-b1", "tenant-b", category="skill")
    ra = api.get("/api/gene/stats", headers=_auth(env["ua"])).json()
    rb = api.get("/api/gene/stats", headers=_auth(env["ub"])).json()
    builtin_count = env["engine"].stats(tenant_id="default")["builtin_count"]
    # DEC-⑩-① 契约随更新：聚合=共享 builtin 目录+己行（B 行恒不计），by_author 同作用域
    assert ra["total_templates"] == builtin_count + 2
    assert ra["by_category"]["skill"] == 1 + sum(
        1
        for t in env["engine"].list_templates(tenant_id="tenant-a")
        if t["builtin"] and t["category"] == "skill"
    )
    assert sum(ra["by_author"].values()) == ra["total_templates"]  # by_author 同作用域
    assert ra["tenant_id"] == "tenant-a"
    assert all(t["tenant_id"] == "tenant-a" or t["builtin"] for t in ra["most_used"])
    assert rb["total_templates"] == builtin_count + 1
    an = api.get("/api/gene/stats").json()
    # 匿名=default 面=builtin 行（A/B 租户行不进匿名聚合）
    assert an["total_templates"] == env["engine"].stats(tenant_id="default")["total_templates"]
    assert an["tenant_id"] == "default"
    assert "bulk-tapi-b1" not in {t["template_id"] for t in an["most_used"]}


def test_default_user_face_matches_anonymous(env):
    """default 租户用户面==匿名面（⑦-⑬⑭ 同口径）：三面逐键相等。"""
    api = env["api"]
    _seed(env, "bulk-def-a", "tenant-a", category="workflow", tags=["defface"])
    h_d, h_an = _auth(env["ud"]), {}
    c_d = api.get("/api/gene/categories", headers=h_d).json()
    c_an = api.get("/api/gene/categories", headers=h_an).json()
    assert c_d == c_an
    e_d = api.get("/api/gene/export", headers=h_d).json()
    e_an = api.get("/api/gene/export", headers=h_an).json()
    assert e_d["count"] == e_an["count"]
    assert {t["template_id"] for t in e_d["templates"]} == {
        t["template_id"] for t in e_an["templates"]
    }
    s_d = api.get("/api/gene/stats", headers=h_d).json()
    s_an = api.get("/api/gene/stats", headers=h_an).json()
    assert s_d["total_templates"] == s_an["total_templates"]


def test_bad_token_401_before_read(env):
    """坏 token 401 先于读取（F3b-⑤）：五端点零读取面。"""
    api = env["api"]
    bad = _auth("00000000-0000-0000-0000-000000000000")
    bad["Authorization"] = "Bearer not-a-token"
    codes = [
        api.get("/api/gene/templates/search/needle", headers=bad).status_code,
        api.get("/api/gene/categories", headers=bad).status_code,
        api.get("/api/gene/tags", headers=bad).status_code,
        api.get("/api/gene/export", headers=bad).status_code,
        api.get("/api/gene/stats", headers=bad).status_code,
    ]
    assert codes == [401, 401, 401, 401, 401]


def test_health_system_view_unchanged(env):
    """/health 监控面保持系统视图（⑦-⑬⑭ 边界锁死）：engine.stats() 全量口径。
    DEC-⑩-① 契约随更新：租户面=共享目录+己行，须有他租户行才严格小于系统面。"""
    api = env["api"]
    eng = env["engine"]
    _seed(env, "bulk-hlt-a", "tenant-a", category="workflow")
    _seed(env, "bulk-hlt-b", "tenant-b", category="workflow")
    h = api.get("/api/gene/health")
    assert h.status_code == 200
    assert h.json()["total_templates"] == eng.stats()["total_templates"]  # 全量
    assert h.json()["total_templates"] > eng.stats(tenant_id="tenant-a")["total_templates"]


def test_single_verdict_source_all_five_faces(env, monkeypatch):
    """单一判法真消费（DEC-⑩-① 契约随更新）：monkeypatch template_visible_to
    （读面可见性单一判法）桩翻转，五面全跟着走——非行内私抄。"""
    import src.gene.templates as gt

    eng = env["engine"]
    _seed(env, "bulk-spy-1", "tenant-a", category="skill", tags=["spy"])
    monkeypatch.setattr(gt, "template_visible_to", lambda t, tid: tid == "tenant-spy")
    # 桩把所有行判给 tenant-spy 可见：tenant-spy 视图==系统视图全量
    assert eng.categories(tenant_id="tenant-spy") == eng.categories()
    assert eng.tags(tenant_id="tenant-spy") == eng.tags()
    assert eng.search("probe", tenant_id="tenant-spy") == eng.search("probe")
    assert eng.export_all(tenant_id="tenant-spy") == eng.export_all()
    assert eng.stats(tenant_id="tenant-spy") == eng.stats()
    # tenant-a 视图清空（无行被判可见）——五面全走判法而非行内私抄
    assert eng.categories(tenant_id="tenant-a") == []
    assert eng.tags(tenant_id="tenant-a") == []
    assert eng.search("probe", tenant_id="tenant-a") == []
    assert eng.export_all(tenant_id="tenant-a") == []
    assert eng.stats(tenant_id="tenant-a")["total_templates"] == 0
