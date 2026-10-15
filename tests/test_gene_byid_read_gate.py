"""gene 管理面 by-ID 读面归属闸测试（DEC-⑦-⑫）。

修前（控制组实证）四类泄露：
①GET /api/gene/templates/{id} 零鉴权零闸——持有 template_id 的跨租户方可读
  他人模板 config/variables 全文（by-ID 读面，⑦-③ 列表/搜索已滤、by-ID 未收）；
②POST /templates/{id}/instantiate 拿他人模板内容跑实例化（源读零闸，且
  usage_count 写他人行）；
③GET /templates/{id}/export 与 ① 同内容泄露面（同一 by-ID 行读）；
④POST /templates/{id}/clone 源读零闸=拿他人模板内容跑克隆（⑦-⑪ fork 源读
  同型；新行归属已由 ⑦-③ 收，源可跨租户读=⑦-③ 边界⑤ 留档件）。

收口=TemplateEngine.get_template 单点归属闸（显式租户=跨租户与 not-found 同
契约不差分泄露、None=系统视图字节不变，⑦-⑩ file_store get_meta 单点闸同款）
覆盖 get/instantiate/export/clone 源读四面 + API 三面（get/instantiate/export）
bind_identity 派生流入（clone 既有 identity 双职=源读可见性+新行归属，⑦-⑪
fork 同款）。

测试面=conftest gene_client 隔离面（真实 gene router + tmp TemplateEngine/
skills.db，test_gene_test_isolation 护栏口径）+ tmp 身份库（USERS_DB/
ENTERPRISE_DB env，绝不碰生产）；seed 走 ⑦-③ 既有 create_template(tenant_id=)
写点签名（控制组旧代码可达行为断言而非 fixture TypeError）。
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


def _token(uid):
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def env(gene_client, tmp_path, monkeypatch):
    """gene_client 隔离面 + tmp 身份库（真 bind_identity）+ 三租户种子用户。"""
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
        "ua": _seed_user(users_db, str(uuid.uuid4()), "alice-gate", "tenant-a"),
        "ub": _seed_user(users_db, str(uuid.uuid4()), "bob-gate", "tenant-b"),
        "ud": _seed_user(users_db, str(uuid.uuid4()), "carol-gate", "default"),
    }


def _seed(env, tid, tenant):
    """写点归属 seed（⑦-③ 既有签名，控制组可达）：行带 config/variables 内容，
    泄露断言才可判（空行 200 也无内容可泄）。"""
    return env["engine"].create_template(
        {
            "template_id": tid,
            "name": "probe " + tid,
            "category": "skill",
            "description": "probe body " + tid,
            "config": {"k": "v-" + tid},
            "variables": [{"name": "x", "default": "dx"}],
        },
        tenant_id=tenant,
    )


# ── store 闸（TemplateEngine 单点归属闸）────────────────────────────


def test_get_template_gate_three_way(env):
    """get 三向：own 可见 / 跨租户 None 与缺行同契约 / None 系统视图不变。"""
    eng = env["engine"]
    _seed(env, "gate-get", "tenant-a")
    assert eng.get_template("gate-get", tenant_id="tenant-a") is not None
    assert eng.get_template("gate-get", tenant_id="tenant-b") is None
    assert eng.get_template("gate-get", tenant_id="default") is None
    assert eng.get_template("gate-get") is not None  # 系统视图字节不变
    assert eng.get_template("no-such", tenant_id="tenant-b") is None  # 缺行同契约
    # builtin 严格过滤延续（⑦-③ 边界② 裁决：非 default 租户不见 starter 目录）
    assert eng.get_template("agent-rag-assistant", tenant_id="default") is not None
    assert eng.get_template("agent-rag-assistant", tenant_id="tenant-a") is None


def test_instantiate_gate_zero_side_effect(env):
    """instantiate 源读闸：跨租户与 not-found 同契约 + usage_count 零写他人行。"""
    eng = env["engine"]
    _seed(env, "gate-inst", "tenant-a")
    ok = eng.instantiate("gate-inst", {"x": "1"}, tenant_id="tenant-a")
    assert ok["success"] and ok["config"]["k"] == "v-gate-inst"
    before = eng.get_template("gate-inst").usage_count
    bad = eng.instantiate("gate-inst", {"x": "1"}, tenant_id="tenant-b")
    missing = eng.instantiate("no-such", {}, tenant_id="tenant-b")
    assert bad == {"success": False, "error": "Template 'gate-inst' not found"}
    assert bad["error"].replace("gate-inst", "ID") == missing["error"].replace("no-such", "ID")
    assert eng.get_template("gate-inst").usage_count == before  # 零副作用硬证
    assert eng.instantiate("gate-inst", {"x": "1"})["success"]  # 系统视图不变


def test_export_gate_three_way(env):
    """export 源读闸（get_template 单点闸覆盖）：跨租户 None 与缺行同契约。"""
    eng = env["engine"]
    _seed(env, "gate-exp", "tenant-a")
    own = eng.export_template("gate-exp", tenant_id="tenant-a")
    assert own is not None and own["config"]["k"] == "v-gate-exp"
    assert eng.export_template("gate-exp", tenant_id="tenant-b") is None
    assert eng.export_template("no-such", tenant_id="tenant-b") is None  # 缺行同契约
    assert eng.export_template("gate-exp") is not None  # 系统视图不变


def test_clone_source_gate_zero_side_effect(env):
    """clone 源读闸（tenant_id 双职）：跨租户=与缺源逐字同契约 + 零克隆副作用。"""
    from src.gene.templates import template_row_tenant

    eng = env["engine"]
    _seed(env, "gate-clone", "tenant-a")
    bad, msg = eng.clone_template("gate-clone", new_id="gate-clone-b", tenant_id="tenant-b")
    missing, mmsg = eng.clone_template("no-such", new_id="x2", tenant_id="tenant-b")
    assert bad is None and missing is None
    assert msg == "Source template 'gate-clone' not found"  # 与缺源同一文案模板
    assert msg.replace("gate-clone", "ID") == mmsg.replace("no-such", "ID")
    # 零克隆副作用：源行存活、无新行、磁盘无新文件
    assert template_row_tenant(eng.get_template("gate-clone")) == "tenant-a"
    assert eng.get_template("gate-clone-b") is None
    assert not (eng.storage_dir / "gate-clone-b.json").exists()
    # 同租户克隆：新行归属克隆者
    own, _ = eng.clone_template("gate-clone", new_id="gate-clone-a2", tenant_id="tenant-a")
    assert own is not None and template_row_tenant(own) == "tenant-a"
    # 系统视图克隆（None 既有语义）：源读不变 + 新行 'default' 不继承源行键
    sysc, _ = eng.clone_template("gate-clone", new_id="gate-clone-sys")
    assert sysc is not None and template_row_tenant(sysc) == "default"
    assert template_row_tenant(eng.get_template("gate-clone")) == "tenant-a"  # 源行键不动


# ── API 面（真 router 真 bind_identity）───────────────────────────


def test_api_get_three_way_and_shape(env):
    api = env["api"]
    _seed(env, "api-get", "tenant-a")
    ok = api.get("/api/gene/templates/api-get", headers=_token(env["ua"]))
    assert ok.status_code == 200
    body = ok.json()
    # 响应形状逐键不变（修前 11 键契约）
    assert set(body) == {
        "template_id",
        "name",
        "category",
        "description",
        "version",
        "author",
        "tags",
        "config",
        "variables",
        "usage_count",
        "builtin",
    }
    assert body["config"]["k"] == "v-api-get"
    bad = api.get("/api/gene/templates/api-get", headers=_token(env["ub"]))
    missing = api.get("/api/gene/templates/no-such", headers=_token(env["ub"]))
    assert bad.status_code == 404 and missing.status_code == 404
    assert bad.json() == missing.json() == {"detail": "Template not found"}  # 不差分泄露
    anon = api.get("/api/gene/templates/api-get")
    assert anon.status_code == 404 and anon.json() == missing.json()  # 匿名='default'


def test_api_instantiate_gate(env):
    api = env["api"]
    _seed(env, "api-inst", "tenant-a")
    ok = api.post(
        "/api/gene/templates/api-inst/instantiate",
        json={"variables": {}},
        headers=_token(env["ua"]),
    )
    assert ok.status_code == 200 and ok.json()["success"]
    bad = api.post(
        "/api/gene/templates/api-inst/instantiate",
        json={"variables": {}},
        headers=_token(env["ub"]),
    )
    missing = api.post(
        "/api/gene/templates/no-such/instantiate",
        json={"variables": {}},
        headers=_token(env["ub"]),
    )
    assert bad.status_code == 404 and missing.status_code == 404
    assert bad.json()["detail"] == "Template 'api-inst' not found"  # 缺行同文案
    assert bad.json()["detail"].replace("api-inst", "ID") == missing.json()["detail"].replace(
        "no-such", "ID"
    )


def test_api_export_gate(env):
    api = env["api"]
    _seed(env, "api-exp", "tenant-a")
    ok = api.get("/api/gene/templates/api-exp/export", headers=_token(env["ua"]))
    assert ok.status_code == 200 and ok.json()["template"]["config"]["k"] == "v-api-exp"
    bad = api.get("/api/gene/templates/api-exp/export", headers=_token(env["ub"]))
    missing = api.get("/api/gene/templates/no-such/export", headers=_token(env["ub"]))
    assert bad.status_code == 404 and missing.status_code == 404
    assert bad.json() == missing.json() == {"detail": "Template not found"}


def test_api_clone_source_gate(env):
    from src.gene.templates import template_row_tenant

    api, eng = env["api"], env["engine"]
    _seed(env, "api-clone", "tenant-a")
    bad = api.post(
        "/api/gene/templates/api-clone/clone",
        json={"new_id": "api-clone-b"},
        headers=_token(env["ub"]),
    )
    missing = api.post(
        "/api/gene/templates/no-such/clone",
        json={"new_id": "nm"},
        headers=_token(env["ub"]),
    )
    assert bad.status_code == 400 and missing.status_code == 400
    assert bad.json()["detail"] == "Source template 'api-clone' not found"  # 缺源同文案
    assert eng.get_template("api-clone-b") is None  # 零克隆副作用
    assert not (eng.storage_dir / "api-clone-b.json").exists()
    assert template_row_tenant(eng.get_template("api-clone")) == "tenant-a"  # 源行零改写
    ok = api.post(
        "/api/gene/templates/api-clone/clone",
        json={"new_id": "api-clone-a2"},
        headers=_token(env["ua"]),
    )
    assert ok.status_code == 200
    assert template_row_tenant(eng.get_template("api-clone-a2")) == "tenant-a"
    # 匿名克隆 builtin（'default' 行）契约不变（不变面）
    anon = api.post(
        "/api/gene/templates/agent-rag-assistant/clone", json={"new_id": "builtin-copy"}
    )
    assert anon.status_code == 200
    assert template_row_tenant(eng.get_template("builtin-copy")) == "default"


def test_default_user_contract(env):
    """default 租户用户（ud）=匿名视角：default 行（含 builtin）可见、tenant-a 不可见。"""
    api = env["api"]
    _seed(env, "api-def", "tenant-a")
    _seed(env, "api-def-td", "default")
    h = _token(env["ud"])
    assert api.get("/api/gene/templates/api-def-td", headers=h).status_code == 200
    assert api.get("/api/gene/templates/agent-rag-assistant", headers=h).status_code == 200
    assert api.get("/api/gene/templates/api-def", headers=h).status_code == 404


def test_bad_token_401_three_faces(env):
    """坏 token 401 先于任何读取（F3b-⑤ 纪律）——三个新鉴权面。"""
    api = env["api"]
    _seed(env, "api-bad", "tenant-a")
    h = {"Authorization": "Bearer not-a-token"}
    assert api.get("/api/gene/templates/api-bad", headers=h).status_code == 401
    assert (
        api.post(
            "/api/gene/templates/api-bad/instantiate", json={"variables": {}}, headers=h
        ).status_code
        == 401
    )
    assert api.get("/api/gene/templates/api-bad/export", headers=h).status_code == 401
