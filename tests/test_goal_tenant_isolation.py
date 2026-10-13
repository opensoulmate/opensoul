"""F3b-⑧ will goals 面（will_goals.json）租户归属收口测试。

覆盖五层（全部 tmp 独立数据面 + NEST_DB 环境隔离（conftest autouse），绝不写生产
data/will_goals.json / data/nest.db）：
①行归属/持久化/粘性（create=身份派生、旧 JSON 缺省 'default'、状态流转不洗键、往返）
②查询过滤（list_goals 按租户，跨租户行互不可见；None=系统视图）
③IsolationEngine.check_access 真执行（by-ID 闸门：跨租户 GoalAccessDenied
  +引擎审计留痕 ns-tenant-b:{goal_id} 硬断言，total_access_checks>0）
④准入租户隔离（F3b-⑧ 语义收口：session 键按租户隔离，同名 session 跨租户不误拦）
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
from src.will.goal_runner import (
    GoalAccessDenied,
    GoalCreate,
    GoalReport,
    GoalRunner,
    GoalState,
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


@pytest.fixture()
def runner(tmp_path, monkeypatch):
    monkeypatch.setattr(GoalRunner, "_PERSIST_PATH", tmp_path / "goals.json")
    return GoalRunner()


# ── ① 行归属/持久化/粘性 ──────────────────────────────────────────────


class TestOwnershipAndPersistence:
    def test_old_json_row_defaults_tenant(self, tmp_path, monkeypatch):
        # 修前持久化形状（无 tenant_id 键）→ 回载归一 'default'（与生产分布一致）
        persist = tmp_path / "goals.json"
        persist.write_text(
            json.dumps(
                {
                    "goals": [
                        {"goal_id": "g_old", "session_id": "s", "description": "old"},
                        {"goal_id": "g_blank", "session_id": "s", "tenant_id": "  "},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(GoalRunner, "_PERSIST_PATH", persist)
        r = GoalRunner()
        assert r.get_goal("g_old").tenant_id == "default"
        assert r.get_goal("g_blank").tenant_id == "default"

    def test_create_tenant_roundtrip_persisted(self, runner, tmp_path):
        goal = runner.create_goal(
            GoalCreate(session_id="s1", description="t"), tenant_id="tenant-a"
        )
        assert goal.tenant_id == "tenant-a"
        raw = json.loads((tmp_path / "goals.json").read_text(encoding="utf-8"))
        assert raw["goals"][0]["tenant_id"] == "tenant-a"  # 真落盘非内存态
        # 重启（同盘新实例）回载粘性
        r2 = GoalRunner()
        assert r2.get_goal(goal.goal_id).tenant_id == "tenant-a"

    def test_create_blank_tenant_falls_default(self, runner):
        g1 = runner.create_goal(GoalCreate(session_id="a", description="t"), tenant_id="")
        g2 = runner.create_goal(GoalCreate(session_id="b", description="t"), tenant_id="   ")
        g3 = runner.create_goal(GoalCreate(session_id="c", description="t"))
        assert g1 is not None and g1.tenant_id == "default"
        assert g2 is not None and g2.tenant_id == "default"
        assert g3 is not None and g3.tenant_id == "default"

    def test_state_transitions_sticky_no_wash(self, runner):
        goal = runner.create_goal(
            GoalCreate(session_id="s1", description="t"), tenant_id="tenant-a"
        )
        gid = goal.goal_id
        runner.pause_goal(gid)  # 无 tenant 参数的系统路径流转
        runner.resume_goal(gid)
        runner.record_event(gid, "bash", {"exit_code": 0})
        got = runner.get_goal(gid)
        assert got.tenant_id == "tenant-a"  # 归属不被状态流转洗改
        assert got.state == GoalState.ACTIVE


# ── ② 查询过滤 ────────────────────────────────────────────────────────


class TestQueryFilter:
    def test_list_scoped_per_tenant(self, runner):
        runner.create_goal(GoalCreate(session_id="a1", description="a1"), tenant_id="tenant-a")
        runner.create_goal(GoalCreate(session_id="a2", description="a2"), tenant_id="tenant-a")
        runner.create_goal(GoalCreate(session_id="b1", description="b1"), tenant_id="tenant-b")
        la = {g.description for g in runner.list_goals(tenant_id="tenant-a")}
        lb = {g.description for g in runner.list_goals(tenant_id="tenant-b")}
        ld = {g.description for g in runner.list_goals(tenant_id="default")}
        allg = {g.description for g in runner.list_goals()}  # None=系统视图
        assert la == {"a1", "a2"}
        assert lb == {"b1"}
        assert ld == set()  # 匿名 default 租户看不到他租户 goal
        assert allg == {"a1", "a2", "b1"}

    def test_list_session_state_filter_combined(self, runner):
        runner.create_goal(GoalCreate(session_id="sx", description="act"), tenant_id="tenant-a")
        gid = runner.list_goals(tenant_id="tenant-a")[0].goal_id
        runner.pause_goal(gid)
        got = [
            g.description
            for g in runner.list_goals(
                session_id="sx", state=GoalState.PAUSED, tenant_id="tenant-a"
            )
        ]
        assert got == ["act"]


# ── ③ IsolationEngine.check_access 真执行 ────────────────────────────


class TestIsolationGate:
    def test_cross_tenant_read_denied_with_audit(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s", description="x"), tenant_id="tenant-b")
        with pytest.raises(GoalAccessDenied) as e:
            runner.get_goal(goal.goal_id, tenant_id="tenant-a")
        assert "namespace" in e.value.reason

        stats = runner.isolation.stats()
        assert stats["total_access_checks"] >= 1
        assert stats["blocked_attempts"] >= 1
        blocked = runner.isolation.get_access_log(allowed=False)
        hit = [
            x
            for x in blocked
            if x["resource_id"] == f"ns-tenant-b:{goal.goal_id}" and x["action"] == "read"
        ]
        assert hit, f"blocked audit entry missing: {blocked}"
        assert "namespace mismatch" in hit[0]["reason"]
        assert hit[0]["resource_type"] == "goal"

    def test_same_tenant_allowed_and_logged(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s", description="x"), tenant_id="tenant-a")
        assert runner.get_goal(goal.goal_id, tenant_id="tenant-a") is not None
        allowed = runner.isolation.get_access_log(allowed=True)
        assert any(
            x["resource_id"] == f"ns-tenant-a:{goal.goal_id}" and x["action"] == "read"
            for x in allowed
        )

    def test_cross_tenant_mutations_denied_row_survives(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s", description="x"), tenant_id="tenant-a")
        gid = goal.goal_id
        with pytest.raises(GoalAccessDenied):
            runner.goal_report(GoalReport(goal_id=gid, status="blocked"), tenant_id="tenant-b")
        with pytest.raises(GoalAccessDenied):
            runner.record_event(gid, "bash", {"exit_code": 0}, tenant_id="tenant-b")
        with pytest.raises(GoalAccessDenied):
            runner.pause_goal(gid, "hacked", tenant_id="tenant-b")
        with pytest.raises(GoalAccessDenied):
            runner.resume_goal(gid, "hacked", tenant_id="tenant-b")
        with pytest.raises(GoalAccessDenied):
            runner.cancel_goal(gid, "hacked", tenant_id="tenant-b")
        with pytest.raises(GoalAccessDenied):
            runner.delete_goal(gid, tenant_id="tenant-b")
        # 行零破坏：仍在、状态未被洗改、事件零混入
        got = runner.get_goal(gid, tenant_id="tenant-a")
        assert got is not None and got.state == GoalState.ACTIVE and len(got.events) == 0
        # 同租户过闸
        assert runner.pause_goal(gid, "mine", tenant_id="tenant-a").state == GoalState.PAUSED
        assert runner.delete_goal(gid, tenant_id="tenant-a") is True

    def test_gate_before_terminal_state_check(self, runner):
        # 闸门前置：跨租户恒拒，禁用 404/终态差分泄露他租户 goal 状态
        goal = runner.create_goal(GoalCreate(session_id="s", description="x"), tenant_id="tenant-a")
        gid = goal.goal_id
        runner.cancel_goal(gid, tenant_id="tenant-a")  # → FAILED 终态
        with pytest.raises(GoalAccessDenied):
            runner.record_event(gid, "bash", {"exit_code": 0}, tenant_id="tenant-b")

    def test_missing_row_contract_unchanged(self, runner):
        assert runner.get_goal("g_missing", tenant_id="tenant-a") is None  # 缺行=404 契约
        assert runner.delete_goal("g_missing", tenant_id="tenant-a") is False
        assert runner.record_event("g_missing", "bash", {}, tenant_id="tenant-a") is None
        assert runner.cancel_goal("g_missing", tenant_id="tenant-a") is None

    def test_bulk_calls_audited(self, runner):
        runner.create_goal(GoalCreate(session_id="s", description="x"), tenant_id="tenant-a")
        runner.list_goals(tenant_id="tenant-a")
        log = runner.isolation.get_access_log(tenant_id="ns-tenant-a")
        assert any(x["action"] == "list" for x in log)
        assert runner.isolation.stats()["total_access_checks"] >= 1

    def test_system_paths_no_gate(self, runner):
        # None=引擎内部读（维护/系统路径）：不过滤不闸门，且不产生审计噪音
        goal = runner.create_goal(GoalCreate(session_id="s", description="x"), tenant_id="tenant-b")
        assert runner.get_goal(goal.goal_id) is not None
        assert runner.delete_goal(goal.goal_id) is True
        assert runner.isolation.stats()["total_access_checks"] == 0


# ── ④ 准入租户隔离（F3b-⑧ 语义收口）─────────────────────────────────


class TestAdmissionTenantScope:
    def test_same_session_cross_tenant_not_blocked(self, runner):
        # 语义收口：session 键按租户隔离——A 的 active goal 不拦同名 session 的 B
        ga = runner.create_goal(
            GoalCreate(session_id="shared", description="a"), tenant_id="tenant-a"
        )
        gb = runner.create_goal(
            GoalCreate(session_id="shared", description="b"), tenant_id="tenant-b"
        )
        assert ga is not None and gb is not None

    def test_same_session_same_tenant_still_blocked(self, runner):
        # 既有准入语义不变：同租户同 session 仍只跑一个
        g1 = runner.create_goal(GoalCreate(session_id="s", description="1"), tenant_id="tenant-a")
        g2 = runner.create_goal(GoalCreate(session_id="s", description="2"), tenant_id="tenant-a")
        assert g1 is not None and g2 is None

    def test_admit_no_tenant_global_contract_unchanged(self, runner):
        runner.create_goal(GoalCreate(session_id="s", description="1"), tenant_id="tenant-a")
        ok, _ = runner.admit("s")  # None=既有全局语义（跨租户也拦）不变
        assert ok is False


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
        import src.will.goal_runner as gr

        monkeypatch.setattr(gr.GoalRunner, "_PERSIST_PATH", tmp_path / "goals.json")
        monkeypatch.setattr(gr, "_runner", None)  # 单例重建指向 tmp 持久化面
        return will_client

    def test_api_tenant_lands_and_cross_denied_battery(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}

        # A 建档：行归属从认证身份派生落行（加性 tenant_id 键可观测）
        resp = api.post("/api/will/goals", json={"description": "api-goal"}, headers=tok_a)
        assert resp.status_code == 200
        gid = resp.json()["goal_id"]
        assert resp.json()["tenant_id"] == "tenant-a"

        # B 冒读/写/删 7 端点 403 电池（detail 带真因，禁静默 not_found）
        battery = [
            ("get", api.get(f"/api/will/goals/{gid}", headers=tok_b)),
            ("delete", api.delete(f"/api/will/goals/{gid}", headers=tok_b)),
            (
                "report",
                api.post(
                    f"/api/will/goals/{gid}/report",
                    json={"status": "blocked", "reason": "x"},
                    headers=tok_b,
                ),
            ),
            (
                "events",
                api.post(
                    f"/api/will/goals/{gid}/events",
                    json={"tool_name": "bash", "result": {"exit_code": 0}},
                    headers=tok_b,
                ),
            ),
            ("pause", api.post(f"/api/will/goals/{gid}/pause", headers=tok_b)),
            ("resume", api.post(f"/api/will/goals/{gid}/resume", headers=tok_b)),
            ("cancel", api.post(f"/api/will/goals/{gid}/cancel", headers=tok_b)),
        ]
        for name, r in battery:
            assert r.status_code == 403, f"{name} expected 403, got {r.status_code}"
            assert "goal access denied" in r.json()["detail"], name

        # A 正对照全通
        assert api.get(f"/api/will/goals/{gid}", headers=tok_a).status_code == 200
        assert (
            api.post(
                f"/api/will/goals/{gid}/events",
                json={"tool_name": "bash", "result": {"exit_code": 0}},
                headers=tok_a,
            ).status_code
            == 200
        )
        assert api.post(f"/api/will/goals/{gid}/pause", headers=tok_a).status_code == 200

        # 列表三面隔离：A 见自己的、B/匿名见不到
        assert [
            g["goal_id"] for g in api.get("/api/will/goals", headers=tok_a).json()["goals"]
        ] == [gid]
        assert api.get("/api/will/goals", headers=tok_b).json()["goals"] == []
        assert api.get("/api/will/goals").json()["goals"] == []

        # B 403 后行仍存活且状态未被洗改（禁跨租户破坏）
        got = api.get(f"/api/will/goals/{gid}", headers=tok_a).json()
        assert got["state"] == "paused"
        assert got["tenant_id"] == "tenant-a"

    def test_api_anonymous_default_contract(self, api, env):
        resp = api.post("/api/will/goals", json={"description": "anon-goal"})
        assert resp.status_code == 200
        gid = resp.json()["goal_id"]
        assert resp.json()["tenant_id"] == "default"
        assert api.get(f"/api/will/goals/{gid}").status_code == 200
        assert api.post(f"/api/will/goals/{gid}/pause").status_code == 200
        assert api.post(f"/api/will/goals/{gid}/cancel").status_code == 200
        assert api.delete(f"/api/will/goals/{gid}").status_code == 200

    def test_api_missing_404_contract(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        assert api.get("/api/will/goals/g_missing").status_code == 404
        assert api.get("/api/will/goals/g_missing", headers=tok_a).status_code == 404
        assert api.delete("/api/will/goals/g_missing", headers=tok_a).status_code == 404
        # resume 缺行既有 400 契约不变
        assert api.post("/api/will/goals/g_missing/resume", headers=tok_a).status_code == 400

    def test_api_no_self_report(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        # body 带 tenant_id 自报=被丢弃，行归属恒派生（禁自报）
        resp = api.post(
            "/api/will/goals",
            json={"description": "evil", "tenant_id": "attacker-tenant"},
            headers=tok_a,
        )
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "tenant-a"

    def test_api_goals_stats_global_boundary(self, api, env):
        # /goals-stats 全局统计不闸（③c-1 体检边界同款），匿名可用
        assert api.get("/api/will/goals-stats").status_code == 200
