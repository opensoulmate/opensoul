"""F3b-③c-1 will_checkpoints 表数据面隔离接线测试。

覆盖五层（全部 tmp 独立库 + NEST_DB 环境隔离（conftest autouse），绝不写生产
data/*.db）：
①will_checkpoints.tenant_id 列迁移（老库补列+存量行回填 'default'）与往返/粘性
②查询过滤（list/prune/stats 按租户，跨租户行互不可见/不可清）
③IsolationEngine.check_access 真执行（by-ID 闸门：跨租户 CheckpointAccessDenied
  +引擎审计留痕，total_access_checks>0）
④引擎层（内存行与 checkpoint 行同一资源同一闸门；execute 写点落派生租户）
⑤API 层派生租户全链（auth token → bind_identity → 行归属落库 → 闸门 403，真实 will router）
"""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb
from src.will.checkpoint import CheckpointAccessDenied, CheckpointStore
from src.will.engine import WorkflowEngine
from src.will.models import ExecutionStatus, NodeType, StepExecution, WorkflowExecution

# 生产 will_checkpoints 表 F3b-③c-1 迁移前形状（无 tenant_id → 证明自愈补列）
OLD_CKPT_DDL = """CREATE TABLE will_checkpoints (
    execution_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    workflow_name TEXT DEFAULT '',
    status TEXT NOT NULL,
    started_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT '',
    completed_at TEXT,
    variables TEXT DEFAULT '{}',
    steps TEXT DEFAULT '[]',
    error TEXT,
    trigger_type TEXT DEFAULT 'manual',
    resume_count INTEGER DEFAULT 0)"""

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


def _exec(exec_id="exec_1", workflow_id="wf_1", status=ExecutionStatus.SUCCESS, tenant="default"):
    return WorkflowExecution(
        id=exec_id,
        workflow_id=workflow_id,
        workflow_name="test-wf",
        status=status,
        steps=[
            StepExecution(
                node_id="n1",
                status=ExecutionStatus.SUCCESS,
                output_data={"action": "script", "output": "hello"},
            )
        ],
        variables={"k": "v"},
        trigger_type="manual",
        tenant_id=tenant,
    )


@pytest.fixture()
def store(tmp_path):
    return CheckpointStore(tmp_path / "ckpt.db")


# ── ① 迁移 + 落行/粘性 ─────────────────────────────────────────────────


class TestMigrationAndRoundtrip:
    def test_tenant_column_added_and_backfilled(self, tmp_path):
        db = tmp_path / "ckpt.db"
        conn = sqlite3.connect(db)
        conn.execute(OLD_CKPT_DDL)
        conn.execute(
            "INSERT INTO will_checkpoints (execution_id, workflow_id, status, started_at) "
            "VALUES ('exec_old', 'wf_1', 'success', '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        conn.close()

        s = CheckpointStore(db)

        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(will_checkpoints)").fetchall()]
        row = conn.execute(
            "SELECT tenant_id FROM will_checkpoints WHERE execution_id = 'exec_old'"
        ).fetchone()
        idx = [r[1] for r in conn.execute("PRAGMA index_info(idx_ckpt_tenant)").fetchall()]
        conn.close()
        assert "tenant_id" in cols
        assert row[0] == "default"  # 存量行回填 default（与生产数据分布一致）
        assert idx  # idx_ckpt_tenant 真建
        got = s.load("exec_old", tenant_id="default")
        assert got is not None and got.tenant_id == "default"

    def test_save_load_roundtrip_tenant(self, store, tmp_path):
        store.save(_exec(tenant="tenant-a"))
        loaded = store.load("exec_1", tenant_id="tenant-a")
        assert loaded is not None and loaded.tenant_id == "tenant-a"
        conn = sqlite3.connect(tmp_path / "ckpt.db")
        row = conn.execute(
            "SELECT tenant_id FROM will_checkpoints WHERE execution_id = 'exec_1'"
        ).fetchone()
        conn.close()
        assert row[0] == "tenant-a"
        items = store.list_checkpoints(tenant_id="tenant-a")
        assert items[0]["tenant_id"] == "tenant-a"

    def test_save_sticky_on_empty(self, store, tmp_path):
        # COALESCE 粘性：字段置空=未声明，重存不把既有归属洗回 'default'
        store.save(_exec(tenant="tenant-a"))
        store.save(_exec(tenant=""))
        conn = sqlite3.connect(tmp_path / "ckpt.db")
        row = conn.execute(
            "SELECT tenant_id FROM will_checkpoints WHERE execution_id = 'exec_1'"
        ).fetchone()
        conn.close()
        assert row[0] == "tenant-a"

    def test_save_empty_falls_default_on_new_row(self, store, tmp_path):
        store.save(_exec(tenant=""))
        conn = sqlite3.connect(tmp_path / "ckpt.db")
        row = conn.execute(
            "SELECT tenant_id FROM will_checkpoints WHERE execution_id = 'exec_1'"
        ).fetchone()
        conn.close()
        assert row[0] == "default"  # 新行空值落 'default'


# ── ② 查询过滤 ────────────────────────────────────────────────────────


class TestQueryFilter:
    def test_list_scoped_per_tenant(self, store):
        store.save(_exec("exec_a1", tenant="tenant-a"))
        store.save(_exec("exec_a2", tenant="tenant-a"))
        store.save(_exec("exec_b1", tenant="tenant-b"))
        la = {x["execution_id"] for x in store.list_checkpoints(tenant_id="tenant-a")}
        lb = {x["execution_id"] for x in store.list_checkpoints(tenant_id="tenant-b")}
        ld = {x["execution_id"] for x in store.list_checkpoints(tenant_id="default")}
        allx = {x["execution_id"] for x in store.list_checkpoints()}  # None=系统扫描
        assert la == {"exec_a1", "exec_a2"}
        assert lb == {"exec_b1"}
        assert ld == set()  # 匿名 default 租户看不到他租户行
        assert allx == {"exec_a1", "exec_a2", "exec_b1"}

    def test_list_workflow_filter_combined(self, store):
        store.save(_exec("exec_a1", workflow_id="wf_1", tenant="tenant-a"))
        store.save(_exec("exec_b1", workflow_id="wf_1", tenant="tenant-b"))
        got = store.list_checkpoints(workflow_id="wf_1", tenant_id="tenant-a")
        assert [x["execution_id"] for x in got] == ["exec_a1"]

    def test_prune_scoped_per_tenant(self, store):
        store.save(_exec("exec_a1", tenant="tenant-a"))
        store.save(_exec("exec_a2", tenant="tenant-a"))
        store.save(_exec("exec_b1", tenant="tenant-b"))
        # keep=0：本租户终态行全清，他租户行一律不动
        assert store.prune(keep=0, tenant_id="tenant-a") == 2
        remaining = {x["execution_id"] for x in store.list_checkpoints()}
        assert remaining == {"exec_b1"}

    def test_prune_exempt_states_not_deleted(self, store):
        store.save(_exec("exec_w1", status=ExecutionStatus.WAITING, tenant="tenant-a"))
        assert store.prune(keep=0, tenant_id="tenant-a") == 0  # WAITING 可续跑豁免
        assert store.load("exec_w1", tenant_id="tenant-a") is not None

    def test_stats_scoped_per_tenant(self, store):
        store.save(_exec("exec_a1", tenant="tenant-a"))
        store.save(_exec("exec_b1", status=ExecutionStatus.WAITING, tenant="tenant-b"))
        sa = store.stats(tenant_id="tenant-a")
        sb = store.stats(tenant_id="tenant-b")
        assert sa == {"total": 1, "by_status": {"success": 1}, "resumable": 0}
        assert sb["total"] == 1 and sb["resumable"] == 1
        assert store.stats()["total"] == 2  # None=系统全局（health）


# ── ③ IsolationEngine.check_access 真执行 ────────────────────────────


class TestIsolationGate:
    def test_cross_tenant_load_denied_with_audit(self, store):
        store.save(_exec("exec_x", tenant="tenant-b"))
        with pytest.raises(CheckpointAccessDenied) as e:
            store.load("exec_x", tenant_id="tenant-a")
        assert "namespace" in e.value.reason

        stats = store.isolation.stats()
        assert stats["total_access_checks"] >= 1
        assert stats["blocked_attempts"] >= 1
        blocked = store.isolation.get_access_log(allowed=False)
        hit = [
            x for x in blocked if x["resource_id"] == "ns-tenant-b:exec_x" and x["action"] == "read"
        ]
        assert hit, f"blocked audit entry missing: {blocked}"
        assert "namespace mismatch" in hit[0]["reason"]
        assert hit[0]["resource_type"] == "checkpoint"

    def test_same_tenant_load_allowed_and_logged(self, store):
        store.save(_exec("exec_x", tenant="tenant-a"))
        assert store.load("exec_x", tenant_id="tenant-a") is not None
        allowed = store.isolation.get_access_log(allowed=True)
        assert any(
            x["resource_id"] == "ns-tenant-a:exec_x" and x["action"] == "read" for x in allowed
        )

    def test_cross_tenant_delete_denied_row_survives(self, store):
        store.save(_exec("exec_x", tenant="tenant-a"))
        with pytest.raises(CheckpointAccessDenied):
            store.delete("exec_x", tenant_id="tenant-b")
        assert store.load("exec_x", tenant_id="tenant-a") is not None  # 行存活
        assert store.delete("exec_x", tenant_id="tenant-a") is True  # 本租户照常删
        assert store.load("exec_x") is None

    def test_delete_missing_row_contract_unchanged(self, store):
        assert store.delete("exec_missing", tenant_id="tenant-a") is False  # 缺行=404 契约

    def test_bulk_calls_audited(self, store):
        store.save(_exec("exec_x", tenant="tenant-a"))
        store.list_checkpoints(tenant_id="tenant-a")
        store.prune(keep=0, tenant_id="tenant-a")
        store.stats(tenant_id="tenant-a")
        log = store.isolation.get_access_log(tenant_id="ns-tenant-a")
        actions = {x["action"] for x in log}
        assert {"list", "prune", "stats"} <= actions
        assert store.isolation.stats()["total_access_checks"] >= 3

    def test_system_paths_no_gate(self, store):
        # None=系统内部读（启动恢复/维护）：不过滤不闸门，且不产生审计噪音
        store.save(_exec("exec_x", tenant="tenant-b"))
        assert store.load("exec_x") is not None
        assert store.delete("exec_x") is True
        assert store.isolation.stats()["total_access_checks"] == 0


# ── ④ 引擎层（内存行与 checkpoint 行同一资源同一闸门）─────────────────


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(WorkflowEngine, "_PERSIST_PATH", tmp_path / "wf.json")
    return WorkflowEngine(checkpoint_db=tmp_path / "ckpt.db")


class TestEngineLayer:
    def test_execute_async_tenant_lands_in_store(self, engine, tmp_path):
        wf = engine.create_workflow(name="t")
        n0 = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n1 = engine.add_node(
            wf.id, NodeType.ACTION, label="a", config={"type": "script", "command": "echo hi"}
        )
        n2 = engine.add_node(wf.id, NodeType.END, label="end")
        engine.add_edge(wf.id, n0.id, n1.id)
        engine.add_edge(wf.id, n1.id, n2.id)

        exec = _run(engine.execute_async(wf.id, tenant_id="tenant-a"))
        assert exec is not None and exec.tenant_id == "tenant-a"
        conn = sqlite3.connect(tmp_path / "ckpt.db")
        row = conn.execute(
            "SELECT tenant_id FROM will_checkpoints WHERE execution_id = ?", (exec.id,)
        ).fetchone()
        conn.close()
        assert row[0] == "tenant-a"  # 写点真落派生租户

    def test_get_execution_gated_memory_row(self, engine):
        # 内存行（未经 store.load）同样走闸门——禁旁路
        exec = _exec("exec_mem", tenant="tenant-a")
        engine._executions["exec_mem"] = exec
        assert engine.get_execution("exec_mem", tenant_id="tenant-a") is exec
        with pytest.raises(CheckpointAccessDenied):
            engine.get_execution("exec_mem", tenant_id="tenant-b")

    def test_get_execution_gated_checkpoint_row(self, engine):
        engine._checkpoints.save(_exec("exec_db", tenant="tenant-a"))
        with pytest.raises(CheckpointAccessDenied):
            engine.get_execution("exec_db", tenant_id="tenant-b")
        assert engine.get_execution("exec_db", tenant_id="tenant-a") is not None

    def test_resume_and_cancel_gated(self, engine):
        exec = _exec(
            "exec_r",
            status=ExecutionStatus.SUCCESS,
            tenant="tenant-a",
        )
        engine._executions["exec_r"] = exec
        with pytest.raises(CheckpointAccessDenied):
            _run(engine.resume_execution("exec_r", tenant_id="tenant-b"))
        with pytest.raises(CheckpointAccessDenied):
            engine.cancel_execution("exec_r", tenant_id="tenant-b")
        # 同租户过闸（SUCCESS 不可续跑/不可取消=409/400 语义在闸门之后，证明闸门放行）
        assert engine.cancel_execution("exec_r", tenant_id="tenant-a") is False
        got, reason = _run(engine.resume_execution("exec_r", tenant_id="tenant-a"))
        assert got is None and reason.startswith("not_resumable")

    def test_list_executions_filtered(self, engine):
        engine._executions["e1"] = _exec("e1", tenant="tenant-a")
        engine._executions["e2"] = _exec("e2", tenant="tenant-b")
        assert [e.id for e in engine.list_executions(tenant_id="tenant-a")] == ["e1"]
        assert [e.id for e in engine.list_executions(tenant_id="tenant-b")] == ["e2"]
        assert len(engine.list_executions()) == 2  # None=系统视图

    def test_delete_checkpoint_cross_tenant_denied_row_survives(self, engine):
        engine._checkpoints.save(_exec("exec_d", tenant="tenant-a"))
        with pytest.raises(CheckpointAccessDenied):
            engine.delete_checkpoint("exec_d", tenant_id="tenant-b")
        assert engine.get_execution("exec_d", tenant_id="tenant-a") is not None
        assert engine.delete_checkpoint("exec_d", tenant_id="tenant-a") is True


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
        # will_client 的模块级 engine 指向生产 checkpoint 库——换 tmp 引擎
        import src.api.will as will_api

        monkeypatch.setattr(WorkflowEngine, "_PERSIST_PATH", tmp_path / "wf.json")
        monkeypatch.setattr(will_api, "engine", WorkflowEngine(checkpoint_db=tmp_path / "ckpt.db"))
        return will_client

    def _mk_wf(self, api):
        import src.api.will as will_api

        eng = will_api.engine
        wf = eng.create_workflow(name="api-t")
        n0 = eng.add_node(wf.id, NodeType.TRIGGER, label="start")
        n1 = eng.add_node(
            wf.id, NodeType.ACTION, label="a", config={"type": "script", "command": "echo api"}
        )
        n2 = eng.add_node(wf.id, NodeType.END, label="end")
        eng.add_edge(wf.id, n0.id, n1.id)
        eng.add_edge(wf.id, n1.id, n2.id)
        return wf.id

    def test_auth_to_gate_full_chain(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        wf_id = self._mk_wf(api)

        # A 执行：行归属从认证身份派生落库
        resp = api.post(f"/api/will/workflows/{wf_id}/execute", json={}, headers=tok_a)
        assert resp.status_code == 200
        exec_id = resp.json()["id"]
        assert resp.json()["tenant_id"] == "tenant-a"

        # A 读自己的：200
        got = api.get(f"/api/will/executions/{exec_id}", headers=tok_a)
        assert got.status_code == 200 and got.json()["tenant_id"] == "tenant-a"

        # B 冒读 A 的执行：403（闸门真拒绝，不是 404 假 not_found）
        denied = api.get(f"/api/will/executions/{exec_id}", headers=tok_b)
        assert denied.status_code == 403
        assert "access denied" in denied.json()["detail"]

        # B cancel/continue/delete 同样 403
        assert api.post(f"/api/will/executions/{exec_id}/cancel", headers=tok_b).status_code == 403
        assert (
            api.post(f"/api/will/executions/{exec_id}/continue", headers=tok_b).status_code == 403
        )
        assert api.delete(f"/api/will/checkpoints/{exec_id}", headers=tok_b).status_code == 403

        # 列表按派生租户隔离：B/匿名看不到 A 的执行/checkpoint
        assert api.get("/api/will/executions", headers=tok_b).json()["executions"] == []
        assert api.get("/api/will/executions").json()["executions"] == []
        assert api.get("/api/will/checkpoints", headers=tok_b).json()["checkpoints"] == []
        assert api.get("/api/will/checkpoints").json()["checkpoints"] == []

        # B 403 后行仍存活（禁跨租户破坏）
        assert api.get(f"/api/will/executions/{exec_id}", headers=tok_a).status_code == 200

    def test_anonymous_default_contract_unchanged(self, api, env):
        wf_id = self._mk_wf(api)
        resp = api.post(f"/api/will/workflows/{wf_id}/execute", json={})
        assert resp.status_code == 200
        exec_id = resp.json()["id"]
        assert resp.json()["tenant_id"] == "default"
        got = api.get(f"/api/will/executions/{exec_id}")
        assert got.status_code == 200
        listed = api.get("/api/will/checkpoints").json()["checkpoints"]
        assert [x["execution_id"] for x in listed] == [exec_id]
        # 匿名清理通道照常
        assert api.delete(f"/api/will/checkpoints/{exec_id}").status_code == 200

    def test_delete_scoped_at_api(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        wf_id = self._mk_wf(api)
        ea = api.post(f"/api/will/workflows/{wf_id}/execute", json={}, headers=tok_a).json()["id"]
        eb = api.post(f"/api/will/workflows/{wf_id}/execute", json={}, headers=tok_b).json()["id"]

        # A 清理自己的 checkpoint：200；B 的行存活
        resp = api.delete(f"/api/will/checkpoints/{ea}", headers=tok_a)
        assert resp.status_code == 200
        assert api.get("/api/will/checkpoints", headers=tok_b).json()["count"] == 1
        assert (
            api.get(f"/api/will/executions/{eb}", headers=tok_b).status_code == 200
        )  # B 自己的照常

    def test_continue_denied_cross_tenant(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        wf_id = self._mk_wf(api)
        ea = api.post(f"/api/will/workflows/{wf_id}/execute", json={}, headers=tok_a).json()["id"]

        assert api.post(f"/api/will/executions/{ea}/continue", headers=tok_b).status_code == 403
        # A 过闸（终态不可续跑=409，闸门放行证明）
        resp = api.post(f"/api/will/executions/{ea}/continue", headers=tok_a)
        assert resp.status_code == 409
