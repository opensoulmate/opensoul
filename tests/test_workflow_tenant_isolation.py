"""F3b-⑦ will 工作流定义面（will_workflows.json）租户归属收口测试。

覆盖五层（全部 tmp 独立数据面 + NEST_DB 环境隔离（conftest autouse），绝不写生产
data/will_workflows.json / data/nest.db）：
①定义面行归属（create=身份派生、旧 JSON 缺省 'default'、粘性不洗键、持久化往返）
②查询过滤（list_workflows 按租户，跨租户行互不可见；None=系统视图）
③IsolationEngine.check_access 真执行（by-ID 闸门：跨租户 WorkflowAccessDenied
  +引擎审计留痕，total_access_checks>0）
④执行闸门（执行=使用定义面资源恒过闸，跨租户零副作用）
⑤API 层派生租户全链（auth token → bind_identity → 行归属落行 → 闸门 403，真实 will router）
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb
from src.will.engine import WorkflowAccessDenied, WorkflowEngine
from src.will.models import NodeType, Workflow

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _run(coro):
    return asyncio.run(coro)


def _ent_token(uid, username="u"):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _insert_ent_user(db, username, tenant, is_active=1):
    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, is_active, tenant_id) VALUES (?, ?, ?, ?)",
            (username, "hashed", is_active, tenant),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(WorkflowEngine, "_PERSIST_PATH", tmp_path / "wf.json")
    return WorkflowEngine(checkpoint_db=tmp_path / "ckpt.db")


def _mk_dag(engine, wf):
    n0 = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
    n1 = engine.add_node(
        wf.id, NodeType.ACTION, label="a", config={"type": "script", "command": "echo hi"}
    )
    n2 = engine.add_node(wf.id, NodeType.END, label="end")
    engine.add_edge(wf.id, n0.id, n1.id)
    engine.add_edge(wf.id, n1.id, n2.id)


# ── ① 行归属/持久化/粘性 ──────────────────────────────────────────────


class TestOwnershipAndPersistence:
    def test_old_json_row_defaults_tenant(self, tmp_path, monkeypatch):
        # 修前持久化形状（无 tenant_id 键）→ 回载归一 'default'（与生产分布一致）
        persist = tmp_path / "wf.json"
        persist.write_text(
            json.dumps(
                {
                    "workflows": [
                        {"id": "wf_old", "name": "old"},
                        {"id": "wf_blank", "name": "b", "tenant_id": "  "},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(WorkflowEngine, "_PERSIST_PATH", persist)
        eng = WorkflowEngine(checkpoint_db=tmp_path / "ckpt.db")
        assert eng.get_workflow("wf_old").tenant_id == "default"
        assert eng.get_workflow("wf_blank").tenant_id == "default"

    def test_create_tenant_roundtrip_persisted(self, engine, tmp_path):
        wf = engine.create_workflow(name="t", tenant_id="tenant-a")
        assert wf.tenant_id == "tenant-a"
        raw = json.loads((tmp_path / "wf.json").read_text(encoding="utf-8"))
        assert raw["workflows"][0]["tenant_id"] == "tenant-a"  # 真落盘非内存态
        # 重启（同盘新实例）回载粘性
        eng2 = WorkflowEngine(checkpoint_db=tmp_path / "ckpt2.db")
        assert eng2.get_workflow(wf.id).tenant_id == "tenant-a"

    def test_create_blank_tenant_falls_default(self, engine):
        assert engine.create_workflow(name="t", tenant_id="").tenant_id == "default"
        assert engine.create_workflow(name="t", tenant_id="   ").tenant_id == "default"
        assert engine.create_workflow(name="t").tenant_id == "default"

    def test_update_sticky_no_wash(self, engine):
        wf = engine.create_workflow(name="t", tenant_id="tenant-a")
        engine.update_workflow(wf.id, tenant_id="tenant-a", name="renamed")
        assert engine.get_workflow(wf.id).tenant_id == "tenant-a"  # 归属不被 update 洗改
        assert engine.get_workflow(wf.id).name == "renamed"


# ── ② 查询过滤 ────────────────────────────────────────────────────────


class TestQueryFilter:
    def test_list_scoped_per_tenant(self, engine):
        engine.create_workflow(name="a1", tenant_id="tenant-a")
        engine.create_workflow(name="a2", tenant_id="tenant-a")
        engine.create_workflow(name="b1", tenant_id="tenant-b")
        la = {w.name for w in engine.list_workflows(tenant_id="tenant-a")}
        lb = {w.name for w in engine.list_workflows(tenant_id="tenant-b")}
        ld = {w.name for w in engine.list_workflows(tenant_id="default")}
        allw = {w.name for w in engine.list_workflows()}  # None=系统视图
        assert la == {"a1", "a2"}
        assert lb == {"b1"}
        assert ld == set()  # 匿名 default 租户看不到他租户定义
        assert allw == {"a1", "a2", "b1"}

    def test_list_status_filter_combined(self, engine):
        from src.will.models import WorkflowStatus

        wf = engine.create_workflow(name="act", tenant_id="tenant-a")
        engine.create_workflow(name="draft", tenant_id="tenant-a")
        engine.update_workflow(wf.id, tenant_id="tenant-a", status=WorkflowStatus.ACTIVE)
        got = [
            w.name
            for w in engine.list_workflows(status=WorkflowStatus.ACTIVE, tenant_id="tenant-a")
        ]
        assert got == ["act"]


# ── ③ IsolationEngine.check_access 真执行 ────────────────────────────


class TestIsolationGate:
    def test_cross_tenant_read_denied_with_audit(self, engine):
        wf = engine.create_workflow(name="x", tenant_id="tenant-b")
        with pytest.raises(WorkflowAccessDenied) as e:
            engine.get_workflow(wf.id, tenant_id="tenant-a")
        assert "namespace" in e.value.reason

        stats = engine.isolation.stats()
        assert stats["total_access_checks"] >= 1
        assert stats["blocked_attempts"] >= 1
        blocked = engine.isolation.get_access_log(allowed=False)
        hit = [
            x
            for x in blocked
            if x["resource_id"] == f"ns-tenant-b:{wf.id}" and x["action"] == "read"
        ]
        assert hit, f"blocked audit entry missing: {blocked}"
        assert "namespace mismatch" in hit[0]["reason"]
        assert hit[0]["resource_type"] == "workflow"

    def test_same_tenant_allowed_and_logged(self, engine):
        wf = engine.create_workflow(name="x", tenant_id="tenant-a")
        assert engine.get_workflow(wf.id, tenant_id="tenant-a") is not None
        allowed = engine.isolation.get_access_log(allowed=True)
        assert any(
            x["resource_id"] == f"ns-tenant-a:{wf.id}" and x["action"] == "read" for x in allowed
        )

    def test_cross_tenant_mutations_denied_row_survives(self, engine):
        wf = engine.create_workflow(name="x", tenant_id="tenant-a")
        n0 = engine.add_node(wf.id, NodeType.TRIGGER, label="start", tenant_id="tenant-a")
        with pytest.raises(WorkflowAccessDenied):
            engine.update_workflow(wf.id, tenant_id="tenant-b", name="hacked")
        with pytest.raises(WorkflowAccessDenied):
            engine.add_node(wf.id, NodeType.END, label="e", tenant_id="tenant-b")
        with pytest.raises(WorkflowAccessDenied):
            engine.remove_node(wf.id, n0.id, tenant_id="tenant-b")
        with pytest.raises(WorkflowAccessDenied):
            engine.add_edge(wf.id, n0.id, n0.id, tenant_id="tenant-b")
        with pytest.raises(WorkflowAccessDenied):
            engine.remove_edge(wf.id, "edge_x", tenant_id="tenant-b")
        with pytest.raises(WorkflowAccessDenied):
            engine.delete_workflow(wf.id, tenant_id="tenant-b")
        # 行零破坏：仍在、名字未被洗改
        got = engine.get_workflow(wf.id, tenant_id="tenant-a")
        assert got is not None and got.name == "x"
        # 同租户过闸
        assert engine.update_workflow(wf.id, tenant_id="tenant-a", name="mine").name == "mine"
        assert engine.delete_workflow(wf.id, tenant_id="tenant-a") is True

    def test_missing_row_contract_unchanged(self, engine):
        assert engine.get_workflow("wf_missing", tenant_id="tenant-a") is None  # 缺行=404 契约
        assert engine.delete_workflow("wf_missing", tenant_id="tenant-a") is False
        assert engine.update_workflow("wf_missing", tenant_id="tenant-a", name="x") is None

    def test_bulk_calls_audited(self, engine):
        engine.create_workflow(name="x", tenant_id="tenant-a")
        engine.list_workflows(tenant_id="tenant-a")
        log = engine.isolation.get_access_log(tenant_id="ns-tenant-a")
        assert any(x["action"] == "list" for x in log)
        assert engine.isolation.stats()["total_access_checks"] >= 1

    def test_system_paths_no_gate(self, engine):
        # None=引擎内部读（seeding/维护）：不过滤不闸门，且不产生审计噪音
        wf = engine.create_workflow(name="x", tenant_id="tenant-b")
        assert engine.get_workflow(wf.id) is not None
        assert engine.delete_workflow(wf.id) is True
        assert engine.isolation.stats()["total_access_checks"] == 0


# ── ④ 执行闸门（执行=使用定义面资源恒过闸）───────────────────────────


class TestExecuteGate:
    def test_execute_cross_tenant_denied_zero_side_effect(self, engine):
        wf = engine.create_workflow(name="x", tenant_id="tenant-a")
        _mk_dag(engine, wf)
        with pytest.raises(WorkflowAccessDenied):
            _run(engine.execute_async(wf.id, tenant_id="tenant-b"))
        # 零副作用：不建执行、不落 checkpoint
        assert engine.list_executions() == []
        assert engine.list_checkpoints() == []

    def test_execute_same_tenant_ok(self, engine):
        wf = engine.create_workflow(name="x", tenant_id="tenant-a")
        _mk_dag(engine, wf)
        exec = _run(engine.execute_async(wf.id, tenant_id="tenant-a"))
        assert exec is not None and exec.tenant_id == "tenant-a"

    def test_execute_empty_tenant_normalized_default_gated(self, engine):
        # 空 tenant 归一 'default' 恒过闸（无系统旁路）：default 归属放行、他租户拒
        wf_d = engine.create_workflow(name="d")
        _mk_dag(engine, wf_d)
        exec = _run(engine.execute_async(wf_d.id))
        assert exec is not None and exec.tenant_id == "default"

        wf_a = engine.create_workflow(name="a", tenant_id="tenant-a")
        _mk_dag(engine, wf_a)
        with pytest.raises(WorkflowAccessDenied):
            _run(engine.execute_async(wf_a.id))


# ── ⑤ API 层：认证派生租户 → 行归属 → 闸门 403 全链 ───────────────────


class TestAPI:
    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        ent_db = tmp_path / "enterprise.db"
        monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
        monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
        conn = sqlite3.connect(ent_db)
        conn.execute(ENTERPRISE_DDL)
        conn.commit()
        conn.close()
        return ent_db

    @pytest.fixture()
    def api(self, will_client, tmp_path, monkeypatch):
        import src.api.will as will_api

        monkeypatch.setattr(WorkflowEngine, "_PERSIST_PATH", tmp_path / "wf.json")
        monkeypatch.setattr(will_api, "engine", WorkflowEngine(checkpoint_db=tmp_path / "ckpt.db"))
        return will_client

    def test_api_tenant_lands_and_cross_denied_battery(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}

        # A 建档：行归属从认证身份派生落行（加性 tenant_id 键可观测）
        resp = api.post("/api/will/workflows", json={"name": "api-wf"}, headers=tok_a)
        assert resp.status_code == 200
        wf_id = resp.json()["id"]
        assert resp.json()["tenant_id"] == "tenant-a"

        # B 冒读/写/删/执行全 403 电池（detail 带真因，禁静默 not_found）
        battery = [
            ("get", api.get(f"/api/will/workflows/{wf_id}", headers=tok_b)),
            ("put", api.put(f"/api/will/workflows/{wf_id}", json={"name": "h"}, headers=tok_b)),
            ("delete", api.delete(f"/api/will/workflows/{wf_id}", headers=tok_b)),
            (
                "nodes",
                api.post(
                    f"/api/will/workflows/{wf_id}/nodes", json={"node_type": "end"}, headers=tok_b
                ),
            ),
            (
                "remove_node",
                api.delete(f"/api/will/workflows/{wf_id}/nodes/node_x", headers=tok_b),
            ),
            (
                "edges",
                api.post(
                    f"/api/will/workflows/{wf_id}/edges",
                    json={"source_node_id": "a", "target_node_id": "b"},
                    headers=tok_b,
                ),
            ),
            (
                "remove_edge",
                api.delete(f"/api/will/workflows/{wf_id}/edges/edge_x", headers=tok_b),
            ),
            ("validate", api.get(f"/api/will/workflows/{wf_id}/validate", headers=tok_b)),
            ("execute", api.post(f"/api/will/workflows/{wf_id}/execute", json={}, headers=tok_b)),
        ]
        for name, r in battery:
            assert r.status_code == 403, f"{name} expected 403, got {r.status_code}"
            assert "workflow access denied" in r.json()["detail"], name

        # A 正对照全通
        assert api.get(f"/api/will/workflows/{wf_id}", headers=tok_a).status_code == 200
        assert api.get(f"/api/will/workflows/{wf_id}/validate", headers=tok_a).status_code == 200
        assert (
            api.post(f"/api/will/workflows/{wf_id}/execute", json={}, headers=tok_a).status_code
            == 200
        )

        # 列表三面隔离：A 见自己的、B/匿名见不到
        assert [
            w["id"] for w in api.get("/api/will/workflows", headers=tok_a).json()["workflows"]
        ] == [wf_id]
        assert api.get("/api/will/workflows", headers=tok_b).json()["workflows"] == []
        assert api.get("/api/will/workflows").json()["workflows"] == []

        # B 403 后行仍存活（禁跨租户破坏）
        assert api.get(f"/api/will/workflows/{wf_id}", headers=tok_a).status_code == 200

    def test_api_anonymous_default_contract(self, api, env):
        resp = api.post("/api/will/workflows", json={"name": "anon-wf"})
        assert resp.status_code == 200
        wf_id = resp.json()["id"]
        assert resp.json()["tenant_id"] == "default"
        assert api.get(f"/api/will/workflows/{wf_id}").status_code == 200
        assert api.post(f"/api/will/workflows/{wf_id}/execute", json={}).status_code == 200
        assert api.delete(f"/api/will/workflows/{wf_id}").status_code == 200

    def test_api_missing_404_contract(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        assert api.get("/api/will/workflows/wf_missing").status_code == 404
        assert api.get("/api/will/workflows/wf_missing", headers=tok_a).status_code == 404
        assert api.delete("/api/will/workflows/wf_missing", headers=tok_a).status_code == 404

    def test_api_no_self_report(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        # body 带 tenant_id 自报=被丢弃，行归属恒派生（禁自报）
        resp = api.post(
            "/api/will/workflows",
            json={"name": "evil", "tenant_id": "attacker-tenant"},
            headers=tok_a,
        )
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "tenant-a"
