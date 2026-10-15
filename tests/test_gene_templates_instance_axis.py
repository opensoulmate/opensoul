"""gene_templates 实例面行租户键加轴测试（DEC-⑦-⑰）。

修前（⑦-⑯ 边界④ 留档定性）：gene_templates.py 实例面无行租户键——
_instances 行不落键、instantiate 写点零身份轴、/instances 恒系统视图、
delete_instance 按 id 跨租户改删零闸、/dashboard 实例聚合全局可见、
/capabilities（实例派生能力行）恒全局。

收口=instance_row_tenant 单一判法（自带键缺失/空白→'default' 兜底，⑦-③
template_row_tenant 同款）+实例行键（instantiate 落行 bind_identity 派生，
匿名='default' 契约不变、禁自报）+能力行=实例行继承落键+/instances 恒过滤
（count=可见数+行内加性键）+delete 归属闸（跨租户与 not-found 同契约）+
/capabilities 读面过滤+adapter_types 聚合同作用域+/dashboard 实例·能力派生
聚合恒按派生租户（模板计数保持全局=边界）。

测试面=conftest gene_client 隔离面（真实 router + tmp 持久化，绝不写生产
~/.openmate/gene_templates.json）+ tmp 身份库（USERS_DB/ENTERPRISE_DB env，
真 bind_identity）。
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

    import src.api.gene_templates as gt

    # 注册表桩：adapter_type="rpa" 短路不打外部 execute，但 registry.get 必须命中
    monkeypatch.setattr(gt, "_get_adapter_registry", lambda: {"stub-adapter": object()})
    gt._templates["inst-probe"] = {
        "template_id": "inst-probe",
        "name": "Instance Probe",
        "description": "probe template for instance axis",
        "adapter_type": "rpa",
        "adapter_class": "stub-adapter",
        "builtin": True,
        "params": [],
        "capabilities": ["can probe things"],
        "capability_actions": [],
        "tags": ["probe"],
    }

    yield {
        "api": gene_client,
        "gt": gt,
        "ua": _seed_user(users_db, str(uuid.uuid4()), "alice-inst", "tenant-a"),
        "ub": _seed_user(users_db, str(uuid.uuid4()), "bob-inst", "tenant-b"),
        "ud": _seed_user(users_db, str(uuid.uuid4()), "carol-inst", "default"),
    }


def _put_inst(gt, iid, tenant=None, name=None, adapter_type="rpa", status="active"):
    """直接落实例行（读面测试 seed）；tenant=None=旧持久化行形态（无键）。"""
    row = {
        "instance_id": iid,
        "template_id": "inst-probe",
        "name": name or iid,
        "adapter_type": adapter_type,
        "status": status,
        "config": {"k": "v-" + iid},
        "capabilities": [],
        "created_at": 1.0,
        "last_activity": 1.0,
        "activity_count": 0,
    }
    if tenant is not None:
        row["tenant_id"] = tenant
    gt._instances[iid] = row
    return row


def _put_cap(gt, cid, iid, tenant=None, desc=None, adapter_type="rpa"):
    row = {
        "capability_id": cid,
        "adapter_instance_id": iid,
        "adapter_name": "probe",
        "adapter_type": adapter_type,
        "description": desc or ("cap " + cid),
        "actions": [],
        "tags": [],
        "registered_at": 1.0,
    }
    if tenant is not None:
        row["tenant_id"] = tenant
    gt._capabilities[cid] = row
    return row


def _ids(insts):
    return sorted(i["instance_id"] for i in insts)


# ── 行租户键单一判法 ─────────────────────────────────────────────────────


def test_row_judge_fallback_forms(env):
    gt = env["gt"]
    j = gt.instance_row_tenant
    assert j({}) == "default"  # 缺失
    assert j({"tenant_id": None}) == "default"
    assert j({"tenant_id": ""}) == "default"  # 空白
    assert j({"tenant_id": "   "}) == "default"
    assert j({"tenant_id": 5}) == "default"  # 非串如实兜底
    assert j({"tenant_id": "tenant-x"}) == "tenant-x"
    assert j({"tenant_id": " tenant-x "}) == "tenant-x"  # strip 归一


def test_single_judge_all_faces_follow(env, monkeypatch):
    """单一判法硬证：翻转判法 → list/capabilities/dashboard/delete 四面全跟走。"""
    gt = env["gt"]
    _put_inst(gt, "flip-i1", "tenant-a", name="flip-one")
    _put_cap(gt, "flip-c1", "flip-i1", "tenant-a", desc="flip cap")
    orig = gt.instance_row_tenant

    def flipped(row):
        v = orig(row)
        return {"tenant-a": "tenant-b", "tenant-b": "tenant-a"}.get(v, v)

    monkeypatch.setattr(gt, "instance_row_tenant", flipped)
    ha, hb = _auth(env["ua"]), _auth(env["ub"])
    api = env["api"]

    # list：翻转后 A 视角行归属变 B → A 恒空、B 可见
    assert _ids(api.get("/api/gene/instances", headers=ha).json()["instances"]) == []
    assert _ids(api.get("/api/gene/instances", headers=hb).json()["instances"]) == ["flip-i1"]
    # capabilities 同跟走
    assert api.get("/api/gene/capabilities", headers=ha).json()["count"] == 0
    assert api.get("/api/gene/capabilities", headers=hb).json()["count"] == 1
    # dashboard 聚合同跟走
    assert api.get("/api/gene/dashboard", headers=ha).json()["summary"]["total_instances"] == 0
    assert api.get("/api/gene/dashboard", headers=hb).json()["summary"]["total_instances"] == 1
    # delete 闸同跟走（翻转后 B 可删、A 拒）
    assert api.delete("/api/gene/instances/flip-i1", headers=ha).status_code == 404
    assert api.delete("/api/gene/instances/flip-i1", headers=hb).status_code == 200


# ── 写点归属（instantiate）────────────────────────────────────────────────


def test_instantiate_row_attribution_derived(env):
    gt = env["gt"]
    r = env["api"].post(
        "/api/gene/adapter-templates/inst-probe/instantiate",
        json={"name": "i-a", "params": {}, "auto_configure": True},
        headers=_auth(env["ua"]),
    )
    assert r.status_code == 200, r.text
    iid = r.json()["instance_id"]
    assert r.json()["tenant_id"] == "tenant-a"  # 响应加性键
    assert gt._instances[iid]["tenant_id"] == "tenant-a"  # 行归属真落库
    cap_ids = gt._instances[iid]["capabilities"]
    assert cap_ids and all(
        gt._capabilities[c]["tenant_id"] == "tenant-a" for c in cap_ids
    )  # 能力行=实例行继承


def test_instantiate_anonymous_default_contract(env):
    gt = env["gt"]
    r = env["api"].post(
        "/api/gene/adapter-templates/inst-probe/instantiate",
        json={"name": "i-anon", "params": {}},
    )
    assert r.status_code == 200, r.text
    iid = r.json()["instance_id"]
    assert gt._instances[iid]["tenant_id"] == "default"  # 匿名='default' 契约不变


def test_instantiate_bad_token_401_zero_instance(env):
    gt = env["gt"]
    before = len(gt._instances)
    r = env["api"].post(
        "/api/gene/adapter-templates/inst-probe/instantiate",
        json={"name": "i-bad", "params": {}},
        headers={"Authorization": "Bearer garbage-token"},
    )
    assert r.status_code == 401
    assert len(gt._instances) == before  # 401 先于写入，零行


def test_instantiate_self_report_ignored(env):
    """派生轴非自报：请求体带 tenant_id 不进落行（pydantic 忽略 extra）。"""
    gt = env["gt"]
    r = env["api"].post(
        "/api/gene/adapter-templates/inst-probe/instantiate",
        json={
            "name": "i-spoof",
            "params": {},
            "tenant_id": "attacker-tenant",
        },
        headers=_auth(env["ua"]),
    )
    assert r.status_code == 200, r.text
    iid = r.json()["instance_id"]
    assert gt._instances[iid]["tenant_id"] == "tenant-a"
    assert r.json()["tenant_id"] == "tenant-a"


# ── 读面过滤（/instances）────────────────────────────────────────────────


def test_list_instances_three_way(env):
    gt = env["gt"]
    _put_inst(gt, "li-a", "tenant-a", name="A one")
    _put_inst(gt, "li-b", "tenant-b", name="B one")
    _put_inst(gt, "li-d", "default", name="D one")
    _put_inst(gt, "li-legacy", None, name="legacy no key")  # 旧持久化行形态
    api, ha, hb = env["api"], _auth(env["ua"]), _auth(env["ub"])

    ra = api.get("/api/gene/instances", headers=ha).json()
    assert _ids(ra["instances"]) == ["li-a"]  # 不见 B/default/legacy
    assert ra["count"] == 1
    assert ra["tenant_id"] == "tenant-a"
    assert ra["instances"][0]["tenant_id"] == "tenant-a"  # 行内加性键归一值

    rb = api.get("/api/gene/instances", headers=hb).json()
    assert _ids(rb["instances"]) == ["li-b"]

    rn = api.get("/api/gene/instances").json()
    assert _ids(rn["instances"]) == ["li-d", "li-legacy"]  # 匿名=default 面含 legacy 兜底行
    assert rn["count"] == 2


def test_default_user_face_equals_anonymous(env):
    """default 用户面==匿名面（⑦-⑯ 同款不变面）。"""
    gt = env["gt"]
    _put_inst(gt, "eq-d", "default")
    _put_inst(gt, "eq-a", "tenant-a")
    api = env["api"]
    anon = _ids(api.get("/api/gene/instances").json()["instances"])
    tok = _ids(api.get("/api/gene/instances", headers=_auth(env["ud"])).json()["instances"])
    assert anon == tok == ["eq-d"]


def test_bad_token_401_before_read(env):
    for path in ("/api/gene/instances", "/api/gene/capabilities", "/api/gene/dashboard"):
        r = env["api"].get(path, headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401, path


# ── delete 归属闸 ────────────────────────────────────────────────────────


def test_delete_cross_tenant_404_same_contract_and_row_survives(env):
    gt = env["gt"]
    _put_inst(gt, "del-a", "tenant-a")
    _put_cap(gt, "del-a-c1", "del-a", "tenant-a")
    api, hb = env["api"], _auth(env["ub"])

    cross = api.delete("/api/gene/instances/del-a", headers=hb)
    missing = api.delete("/api/gene/instances/no-such-id", headers=hb)
    assert cross.status_code == 404
    # 与 not-found 同契约：同 404 同文案形态（id 回显=客户端自己给的输入非泄露）
    assert cross.json()["detail"] == "Instance 'del-a' not found"
    assert missing.json()["detail"] == "Instance 'no-such-id' not found"
    assert "del-a" in gt._instances  # 行零破坏
    assert "del-a-c1" in gt._capabilities  # 能力零破坏


def test_delete_own_removes_instance_and_caps(env):
    gt = env["gt"]
    _put_inst(gt, "del-b", "tenant-a")
    _put_cap(gt, "del-b-c1", "del-b", "tenant-a")
    _put_cap(gt, "del-b-c2", "del-b", "tenant-a")
    r = env["api"].delete("/api/gene/instances/del-b", headers=_auth(env["ua"]))
    assert r.status_code == 200, r.text
    assert r.json()["capabilities_removed"] == 2
    assert "del-b" not in gt._instances
    assert "del-b-c1" not in gt._capabilities and "del-b-c2" not in gt._capabilities


# ── 能力读面（父行归属）─────────────────────────────────────────────────


def test_capabilities_scoped_by_row_key(env):
    gt = env["gt"]
    _put_inst(gt, "cap-ia", "tenant-a")
    _put_inst(gt, "cap-ib", "tenant-b")
    _put_cap(gt, "cap-a1", "cap-ia", "tenant-a", desc="alpha skill")
    _put_cap(gt, "cap-b1", "cap-ib", "tenant-b", desc="beta skill")
    _put_cap(gt, "cap-d1", "cap-ia", "default", desc="default skill")
    _put_cap(gt, "cap-orphan", "no-such-parent", None, desc="orphan skill")  # 孤儿行兜底
    api, ha, hb = env["api"], _auth(env["ua"]), _auth(env["ub"])

    ra = api.get("/api/gene/capabilities", headers=ha).json()
    assert sorted(c["capability_id"] for c in ra["capabilities"]) == ["cap-a1"]
    assert ra["tenant_id"] == "tenant-a"

    rb = api.get("/api/gene/capabilities", headers=hb).json()
    assert sorted(c["capability_id"] for c in rb["capabilities"]) == ["cap-b1"]

    rn = api.get("/api/gene/capabilities").json()
    assert sorted(c["capability_id"] for c in rn["capabilities"]) == [
        "cap-d1",
        "cap-orphan",
    ]  # 匿名=default 面含孤儿兜底行


# ── /dashboard 聚合作用域 ────────────────────────────────────────────────


def test_dashboard_aggregates_scoped(env):
    gt = env["gt"]
    _put_inst(gt, "db-a1", "tenant-a", adapter_type="rpa")
    _put_inst(gt, "db-a2", "tenant-a", adapter_type="rest")
    _put_inst(gt, "db-b1", "tenant-b", adapter_type="rpa")
    _put_cap(gt, "db-a-c1", "db-a1", "tenant-a")
    _put_cap(gt, "db-b-c1", "db-b1", "tenant-b")
    api = env["api"]

    ra = api.get("/api/gene/dashboard", headers=_auth(env["ua"])).json()
    assert ra["summary"]["total_instances"] == 2  # 修前=3 全局泄露
    assert ra["summary"]["total_capabilities"] == 1  # 修前=2
    assert ra["instances_by_type"] == {"rpa": 1, "rest": 1}
    assert ra["instances_by_status"] == {"active": 2}
    assert _ids(ra["recent_instances"]) == ["db-a1", "db-a2"]  # recent 不含 B 行
    assert ra["tenant_id"] == "tenant-a"

    rb = api.get("/api/gene/dashboard", headers=_auth(env["ub"])).json()
    assert rb["summary"]["total_instances"] == 1
    assert _ids(rb["recent_instances"]) == ["db-b1"]


def test_dashboard_template_counts_global_and_health_system_view(env):
    """边界锁死：模板计数保持全局（共享 catalog）、/health·/adapter-health 系统视图。"""
    gt = env["gt"]
    _put_inst(gt, "hv-a", "tenant-a")
    _put_inst(gt, "hv-b", "tenant-b")
    api = env["api"]

    ha = api.get("/api/gene/dashboard", headers=_auth(env["ua"])).json()
    hn = api.get("/api/gene/dashboard").json()
    assert ha["summary"]["total_templates"] == hn["summary"]["total_templates"]
    assert ha["summary"]["total_templates"] >= 1
    assert ha["available_adapter_types"] == hn["available_adapter_types"]

    # 系统视图（⑦-⑬⑭ 同款）：health 全量计数不受租户过滤影响
    h = api.get("/api/gene/adapter-health").json()
    assert h["instances"] == len(gt._instances)
    assert h["capabilities"] == len(gt._capabilities)
    assert api.get("/api/gene/health").status_code == 200


# ── 持久化往返 ───────────────────────────────────────────────────────────


def test_tenant_key_persistence_round_trip(env):
    gt = env["gt"]
    _put_inst(gt, "rt-1", "tenant-a")
    _put_cap(gt, "rt-c1", "rt-1", "tenant-a")
    gt._save_persisted()
    data = gt._load_persisted()
    assert data["instances"]["rt-1"]["tenant_id"] == "tenant-a"  # 键随 JSON 往返
    assert data["capabilities"]["rt-c1"]["tenant_id"] == "tenant-a"

    # 旧持久化行（无键）加载=兜底 'default' 不掉落库
    legacy = {"instance_id": "rt-old", "name": "old"}
    assert gt.instance_row_tenant(legacy) == "default"
