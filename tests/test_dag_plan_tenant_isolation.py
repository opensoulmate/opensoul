"""F3b-⑧b dag_planner 面（DAG 计划面）租户归属收口测试。

覆盖四层（全部 tmp 独立数据面 + NEST_DB 环境隔离（conftest autouse），绝不写生产
data/nest.db）：
①行归属（create=身份派生、缺失/空白→'default'、JSON/文本两解析路径透传）
②IsolationEngine.check_access 真执行（by-ID 读/写闸门：跨租户 DAGPlanAccessDenied
  +引擎审计留痕 ns-tenant-b:{plan_id} 硬断言，total_access_checks>0）
③系统视图/缺行/stats 契约不变面（None=引擎内部路径零闸零审计噪音）
④API 层派生租户全链（auth token → bind_identity → 行归属落行 → 闸门 403，真实 will router）
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb
from src.will.dag_planner import (
    DAGPlanAccessDenied,
    DAGPlanner,
    StepStatus,
)

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


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


STEPS_SPEC = [{"id": "s1", "description": "do thing", "tool": "bash"}]
LLM_JSON = '{"steps": [{"id": "s1", "description": "do thing"}]}'
LLM_TEXT = "Step 0: first thing\nStep 1: second thing\n  depends on step_0"


@pytest.fixture()
def planner():
    return DAGPlanner()


# ── ① 行归属 ─────────────────────────────────────────────────────────


class TestOwnership:
    def test_create_plan_tenant_lands(self, planner):
        plan = planner.create_plan("goal-x", STEPS_SPEC, tenant_id="tenant-a")
        assert plan.tenant_id == "tenant-a"
        assert planner.get_plan(plan.plan_id).tenant_id == "tenant-a"

    def test_missing_blank_defaults_tenant(self, planner):
        p1 = planner.create_plan("g", STEPS_SPEC)
        p2 = planner.create_plan("g", STEPS_SPEC, tenant_id="   ")
        assert p1.tenant_id == "default"
        assert p2.tenant_id == "default"

    def test_create_from_llm_both_parse_paths_pass_tenant(self, planner):
        p_json = planner.create_from_llm_response("g", LLM_JSON, tenant_id="tenant-a")
        p_text = planner.create_from_llm_response("g", LLM_TEXT, tenant_id="tenant-b")
        assert p_json is not None and p_json.tenant_id == "tenant-a"
        assert p_text is not None and p_text.tenant_id == "tenant-b"
        assert len(p_text.steps) == 2  # 文本解析路径行为零变化


# ── ② IsolationEngine.check_access 真执行 ────────────────────────────


class TestIsolationGate:
    def test_cross_tenant_read_denied_with_audit(self, planner):
        plan = planner.create_plan("g", STEPS_SPEC, tenant_id="tenant-b")
        with pytest.raises(DAGPlanAccessDenied) as e:
            planner.get_plan(plan.plan_id, tenant_id="tenant-a")
        assert "namespace" in e.value.reason

        stats = planner.isolation.stats()
        assert stats["total_access_checks"] >= 1
        assert stats["blocked_attempts"] >= 1
        blocked = planner.isolation.get_access_log(allowed=False)
        hit = [
            x
            for x in blocked
            if x["resource_id"] == f"ns-tenant-b:{plan.plan_id}" and x["action"] == "read"
        ]
        assert hit, f"blocked audit entry missing: {blocked}"
        assert "namespace mismatch" in hit[0]["reason"]
        assert hit[0]["resource_type"] == "dag_plan"

    def test_same_tenant_allowed_and_logged(self, planner):
        plan = planner.create_plan("g", STEPS_SPEC, tenant_id="tenant-a")
        assert planner.get_plan(plan.plan_id, tenant_id="tenant-a") is not None
        allowed = planner.isolation.get_access_log(allowed=True)
        assert any(
            x["resource_id"] == f"ns-tenant-a:{plan.plan_id}" and x["action"] == "read"
            for x in allowed
        )

    def test_cross_tenant_write_denied_row_zero_mutation(self, planner):
        plan = planner.create_plan("g", STEPS_SPEC, tenant_id="tenant-a")
        pid = plan.plan_id
        with pytest.raises(DAGPlanAccessDenied):
            planner.update_step(pid, "s1", StepStatus.COMPLETED, tenant_id="tenant-b")
        # 行零改写：step 仍 pending
        got = planner.get_plan(pid, tenant_id="tenant-a")
        assert got.steps[0].status == StepStatus.PENDING
        # 同租户过闸
        planner.update_step(pid, "s1", StepStatus.COMPLETED, tenant_id="tenant-a")
        assert planner.get_plan(pid, tenant_id="tenant-a").steps[0].status == StepStatus.COMPLETED

    def test_missing_plan_contract_unchanged(self, planner):
        # 系统视图缺行契约（新旧代码皆过=不变面）
        assert planner.get_plan("plan_missing") is None
        assert planner.update_step("plan_missing", "s1", StepStatus.COMPLETED) is None

    def test_missing_plan_tenant_path_contract(self, planner):
        # 显式租户缺行契约（同 404 语义，不因闸门变味）
        assert planner.get_plan("plan_missing", tenant_id="tenant-a") is None
        assert (
            planner.update_step("plan_missing", "s1", StepStatus.COMPLETED, tenant_id="tenant-a")
            is None
        )

    def test_system_view_no_gate_no_audit_noise(self, planner):
        # None=引擎内部读（to_mermaid/维护路径）：不过闸、零审计噪音
        plan = planner.create_plan("g", STEPS_SPEC, tenant_id="tenant-b")
        assert planner.get_plan(plan.plan_id) is not None
        planner.update_step(plan.plan_id, "s1", StepStatus.COMPLETED)
        assert planner.isolation.stats()["total_access_checks"] == 0


# ── ③ stats 契约不变面 ──────────────────────────────────────────────


class TestStatsBoundary:
    def test_stats_global_shape_unchanged(self, planner):
        planner.create_plan("g", STEPS_SPEC)  # 无租户参数=既有契约（新旧代码皆过）
        stats = planner.get_stats()
        assert set(stats) == {
            "total_plans",
            "completed_plans",
            "failed_plans",
            "total_steps",
            "active_plans",
        }
        assert stats["total_plans"] == 1
        assert stats["total_steps"] == 1


# ── ④ API 层：认证派生租户 → 行归属 → 闸门 403 全链 ───────────────────


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
    def api(self, will_client, monkeypatch):
        import src.api.will as will_api

        monkeypatch.setattr(will_api, "dag_planner", DAGPlanner())  # 每测全新计划面
        return will_client

    def test_api_tenant_lands_and_cross_denied(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}

        # A 建计划：行归属从认证身份派生（加性 tenant_id 键可观测）
        resp = api.post(
            "/api/will/dag/plan", json={"goal": "g", "llm_response": LLM_JSON}, headers=tok_a
        )
        assert resp.status_code == 200
        pid = resp.json()["plan_id"]
        assert resp.json()["tenant_id"] == "tenant-a"

        # B 冒读 403 带真因（禁静默 not_found）
        r_b = api.get(f"/api/will/dag/{pid}/status", headers=tok_b)
        assert r_b.status_code == 403
        assert "dag plan access denied" in r_b.json()["detail"]
        # 匿名（default）同样读不到 A 的计划
        assert api.get(f"/api/will/dag/{pid}/status").status_code == 403

        # A 正对照 + 行归属回显
        r_a = api.get(f"/api/will/dag/{pid}/status", headers=tok_a)
        assert r_a.status_code == 200
        assert r_a.json()["tenant_id"] == "tenant-a"
        assert r_a.json()["steps"][0]["step_id"] == "s1"

        # B 403 后行仍存活未被洗改（禁跨租户破坏）
        assert api.get(f"/api/will/dag/{pid}/status", headers=tok_a).status_code == 200

    def test_api_anonymous_default_contract(self, api, env):
        resp = api.post("/api/will/dag/plan", json={"goal": "g", "llm_response": LLM_JSON})
        assert resp.status_code == 200
        pid = resp.json()["plan_id"]
        assert resp.json()["tenant_id"] == "default"
        r = api.get(f"/api/will/dag/{pid}/status")
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "default"
        assert "mermaid" in resp.json()

    def test_api_missing_404_contract(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        assert api.get("/api/will/dag/plan_missing/status").status_code == 404
        assert api.get("/api/will/dag/plan_missing/status", headers=tok_a).status_code == 404

    def test_api_no_self_report(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        # body 带 tenant_id 自报=被丢弃（DAGPlanRequest 无该字段），行归属恒派生
        resp = api.post(
            "/api/will/dag/plan",
            json={"goal": "g", "llm_response": LLM_JSON, "tenant_id": "attacker-tenant"},
            headers=tok_a,
        )
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "tenant-a"

    def test_api_dag_stats_global_boundary(self, api, env):
        # /dag/stats 聚合计数器全局不闸（⑧ goals-stats 全局边界同款），匿名可用
        assert api.get("/api/will/dag/stats").status_code == 200
