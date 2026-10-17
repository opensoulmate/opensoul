"""H1-②b-⑧ dag 步进轴收口测试——get_ready_steps/update_step 首个真消费。

覆盖（全部 tmp 独立数据面 + NEST_DB autouse 隔离，绝不写生产 data/nest.db）：
①引擎步进语义（就绪判法原样出口：PENDING 且 depends_on 全 COMPLETED）
②终态只定一次（接线前定形）：同一终态步幂等重放不虚增 completed_plans/failed_plans
③API 就绪查询面（GET /dag/{plan_id}/ready）：形状锁死 + 依赖门 + 随推进变化
④API 步状态推进面（POST /dag/{plan_id}/steps/{step_id}）：全周期推进/幂等重放/
  result·error 往返/缺计划·缺步 404 零副作用/非法 status 422
⑤租户闸门全链（bind_identity 派生 → by-ID 读写闸门 403 行零改写）
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb
from src.will.dag_planner import DAGPlanner, StepStatus

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""

LLM_JSON_DEP = (
    '{"steps": [{"id": "s1", "description": "first"}, '
    '{"id": "s2", "description": "second", "depends_on": ["s1"]}]}'
)
LLM_JSON_ONE = '{"steps": [{"id": "s1", "description": "only"}]}'
LLM_JSON_TWO = (
    '{"steps": [{"id": "s1", "description": "first"}, {"id": "s2", "description": "second"}]}'
)

READY_STEP_KEYS = {
    "step_id",
    "description",
    "tool_name",
    "arguments",
    "status",
    "depends_on",
    "estimated_duration",
}
ADVANCE_RESPONSE_KEYS = {
    "plan_id",
    "step_id",
    "status",
    "result",
    "error",
    "progress",
    "is_complete",
    "plan_status",
    "tenant_id",
}


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


# ── ① 引擎步进语义 + ② 终态只定一次 ─────────────────────────────────


class TestEngineStepping:
    @pytest.fixture()
    def planner(self):
        return DAGPlanner()

    def test_ready_dep_gate_progression(self, planner):
        spec = [
            {"id": "s1", "description": "a"},
            {"id": "s2", "description": "b", "depends_on": ["s1"]},
            {"id": "s3", "description": "c", "depends_on": ["s2"]},
        ]
        plan = planner.create_plan("g", spec)
        assert [s.step_id for s in plan.get_ready_steps()] == ["s1"]
        planner.update_step(plan.plan_id, "s1", StepStatus.COMPLETED)
        assert [s.step_id for s in plan.get_ready_steps()] == ["s2"]
        planner.update_step(plan.plan_id, "s2", StepStatus.COMPLETED)
        assert [s.step_id for s in plan.get_ready_steps()] == ["s3"]
        # 未完成的依赖不放行（硬反证：s3 依赖未满足时不在就绪集）
        plan2 = planner.create_plan("g2", spec)
        planner.update_step(plan2.plan_id, "s1", StepStatus.FAILED)
        assert plan2.get_ready_steps() == []

    def test_ready_skips_non_pending(self, planner):
        plan = planner.create_plan("g", [{"id": "s1", "description": "a"}])
        planner.update_step(plan.plan_id, "s1", StepStatus.RUNNING)
        # RUNNING 态不进就绪集（引擎判法原样：只认 PENDING）
        assert plan.get_ready_steps() == []

    def test_finalize_once_no_counter_inflation(self, planner):
        plan = planner.create_plan("g", [{"id": "s1", "description": "a"}])
        planner.update_step(plan.plan_id, "s1", StepStatus.COMPLETED)
        # 幂等重放（网络重试/步进循环重复推进同一终态步）
        planner.update_step(plan.plan_id, "s1", StepStatus.COMPLETED)
        planner.update_step(plan.plan_id, "s1", StepStatus.COMPLETED)
        stats = planner.get_stats()
        assert stats["total_plans"] == 1
        assert stats["completed_plans"] == 1  # 修前=3（每次 update 全终态都累加）
        assert stats["failed_plans"] == 0
        assert plan.status == "completed"

    def test_finalize_failed_once(self, planner):
        plan = planner.create_plan(
            "g", [{"id": "s1", "description": "a"}, {"id": "s2", "description": "b"}]
        )
        planner.update_step(plan.plan_id, "s1", StepStatus.FAILED, error="boom")
        planner.update_step(plan.plan_id, "s2", StepStatus.COMPLETED)
        planner.update_step(plan.plan_id, "s2", StepStatus.COMPLETED)  # 幂等重放
        stats = planner.get_stats()
        assert plan.status == "failed"
        assert stats["failed_plans"] == 1  # 修前=2
        assert stats["completed_plans"] == 0

    def test_completed_at_set_on_finalize(self, planner):
        plan = planner.create_plan("g", [{"id": "s1", "description": "a"}])
        assert plan.completed_at == 0.0
        planner.update_step(plan.plan_id, "s1", StepStatus.COMPLETED)
        assert plan.completed_at > 0.0


# ── ③④ API 全链 ─────────────────────────────────────────────────────


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

    def test_ready_face_shape_and_dep_gate(self, api):
        resp = api.post("/api/will/dag/plan", json={"goal": "g", "llm_response": LLM_JSON_DEP})
        assert resp.status_code == 200
        pid = resp.json()["plan_id"]

        r = api.get(f"/api/will/dag/{pid}/ready")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"plan_id", "count", "ready_steps", "tenant_id"}
        assert body["count"] == 1
        assert body["ready_steps"][0]["step_id"] == "s1"
        assert set(body["ready_steps"][0]) == READY_STEP_KEYS
        assert body["ready_steps"][0]["status"] == "pending"  # 引擎判法原样：PENDING 且依赖满足

        # 推进 s1 后 s2 依赖满足进就绪集（步进面随推进真变化）
        assert (
            api.post(f"/api/will/dag/{pid}/steps/s1", json={"status": "completed"}).status_code
            == 200
        )
        r2 = api.get(f"/api/will/dag/{pid}/ready")
        assert r2.json()["count"] == 1
        assert r2.json()["ready_steps"][0]["step_id"] == "s2"
        assert r2.json()["ready_steps"][0]["depends_on"] == ["s1"]

    def test_advance_full_cycle(self, api):
        resp = api.post("/api/will/dag/plan", json={"goal": "g", "llm_response": LLM_JSON_DEP})
        pid = resp.json()["plan_id"]

        r1 = api.post(
            f"/api/will/dag/{pid}/steps/s1",
            json={"status": "completed", "result": {"out": 1}},
        )
        assert r1.status_code == 200
        body1 = r1.json()
        assert set(body1) == ADVANCE_RESPONSE_KEYS
        assert body1["status"] == "completed"
        assert body1["result"] == {"out": 1}
        assert body1["progress"] == 0.5
        assert body1["is_complete"] is False
        assert body1["plan_status"] == "pending"  # 引擎既有语义：非终态不置 running

        r2 = api.post(f"/api/will/dag/{pid}/steps/s2", json={"status": "failed", "error": "boom"})
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["error"] == "boom"
        assert body2["is_complete"] is True
        assert body2["plan_status"] == "failed"
        assert body2["progress"] == 0.5  # completed 计 1/2（FAILED 不计进度，引擎既有）

    def test_idempotent_replay_no_stats_inflation(self, api):
        resp = api.post("/api/will/dag/plan", json={"goal": "g", "llm_response": LLM_JSON_ONE})
        pid = resp.json()["plan_id"]
        for _ in range(3):
            r = api.post(f"/api/will/dag/{pid}/steps/s1", json={"status": "completed"})
            assert r.status_code == 200
            assert r.json()["plan_status"] == "completed"
        stats = api.get("/api/will/dag/stats").json()
        assert stats["total_plans"] == 1
        assert stats["completed_plans"] == 1  # 修前=3 虚增

    def test_missing_404_zero_side_effect(self, api):
        # 缺计划：两口 404
        assert api.get("/api/will/dag/plan_missing/ready").status_code == 404
        assert (
            api.post(
                "/api/will/dag/plan_missing/steps/s1", json={"status": "completed"}
            ).status_code
            == 404
        )
        # 缺步：404 且行零改写（禁静默假成功——引擎缺步 no-op 是系统路径语义）
        resp = api.post("/api/will/dag/plan", json={"goal": "g", "llm_response": LLM_JSON_ONE})
        pid = resp.json()["plan_id"]
        r = api.post(f"/api/will/dag/{pid}/steps/nope", json={"status": "completed"})
        assert r.status_code == 404
        assert "nope" in r.json()["detail"]
        status = api.get(f"/api/will/dag/{pid}/status").json()
        assert status["steps"][0]["status"] == "pending"
        assert status["is_complete"] is False
        assert api.get("/api/will/dag/stats").json()["completed_plans"] == 0

    def test_invalid_status_422(self, api):
        resp = api.post("/api/will/dag/plan", json={"goal": "g", "llm_response": LLM_JSON_ONE})
        pid = resp.json()["plan_id"]
        assert (
            api.post(f"/api/will/dag/{pid}/steps/s1", json={"status": "bogus"}).status_code == 422
        )
        assert api.post(f"/api/will/dag/{pid}/steps/s1", json={}).status_code == 422
        # 422 零副作用
        assert api.get(f"/api/will/dag/{pid}/status").json()["steps"][0]["status"] == "pending"

    def test_tenant_gate_all_chain(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}

        resp = api.post(
            "/api/will/dag/plan",
            json={"goal": "g", "llm_response": LLM_JSON_DEP},
            headers=tok_a,
        )
        assert resp.status_code == 200
        pid = resp.json()["plan_id"]
        assert resp.json()["tenant_id"] == "tenant-a"

        # B 冒读/冒写 403 带真因（禁静默 not_found/假成功）
        r_b1 = api.get(f"/api/will/dag/{pid}/ready", headers=tok_b)
        assert r_b1.status_code == 403
        assert "dag plan access denied" in r_b1.json()["detail"]
        r_b2 = api.post(
            f"/api/will/dag/{pid}/steps/s1", json={"status": "completed"}, headers=tok_b
        )
        assert r_b2.status_code == 403
        # 匿名（default）同拒
        assert api.get(f"/api/will/dag/{pid}/ready").status_code == 403
        assert (
            api.post(f"/api/will/dag/{pid}/steps/s1", json={"status": "completed"}).status_code
            == 403
        )

        # 行零改写（B 的写被拒后 s1 仍 pending）
        r_a = api.get(f"/api/will/dag/{pid}/status", headers=tok_a)
        assert r_a.status_code == 200
        assert r_a.json()["steps"][0]["status"] == "pending"

        # A 正对照全链
        assert api.get(f"/api/will/dag/{pid}/ready", headers=tok_a).json()["count"] == 1
        assert (
            api.post(
                f"/api/will/dag/{pid}/steps/s1", json={"status": "completed"}, headers=tok_a
            ).status_code
            == 200
        )
        assert (
            api.get(f"/api/will/dag/{pid}/ready", headers=tok_a).json()["ready_steps"][0]["step_id"]
            == "s2"
        )
