"""H1-②b-⑪ 自驱循环驱动收口（GoalRunner.record_loop 首个生产消费面）测试。

覆盖：
- 循环步骤分类（NONE 不计成功证据 / FAILED 计入 fail-stop——禁 fabricated success）
- run_loop_iteration：真执行留痕 + record_loop 计次（序=先事件后计次）
- max_loops 停机判定生产可达（record_loop 真消费）
- fail-stop（连续失败→PAUSED）随循环事件可达
- 循环 chatter 不可过 goal_report 事件验证（反 fabricated completed）
- 非 ACTIVE 如实 skipped 不触达执行器
- default_step_executor：无配置如实 error + 真 HTTP 桩成功/失败双路径
- run_active_goal_loops：批量推进 + 单 goal 失败不掀翻其余
- API 全链（真实 router 挂进程内 app）：200 迭代 / 404 / 跨租户 403 / skipped
- proactive 调度接线（register_default_watchers + run_watcher 真跑）
- 静态接线普查（record_loop 生产消费点）

隔离纪律（conftest _isolate_goal_loop_faces autouse）：will_goals.json 归 tmp、
goal_runner 单例每用例复位、goal_loop settings llm_api_key 置空（心跳 tick 零网络）。
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.will.goal_loop as gl_mod
import src.will.goal_runner as gr_mod
import src.will.proactive_behaviors as pb_mod
from src.config import Settings
from src.will.goal_loop import (
    default_step_executor,
    run_active_goal_loops,
    run_loop_iteration,
)
from src.will.goal_runner import (
    LOOP_STEP_TOOL,
    GoalCreate,
    GoalOutcome,
    GoalReport,
    GoalRunner,
    GoalState,
)


@pytest.fixture()
def runner(tmp_path):
    r = GoalRunner.__new__(GoalRunner)
    r._goals = {}
    r._PERSIST_PATH = tmp_path / "will_goals.json"
    return r


def ok_exec(text="下一步：整理文档"):
    return lambda goal: {"output": text}


def err_exec(msg="LLM boom"):
    return lambda goal: {"error": msg}


class TestLoopStepClassification:
    """循环步骤判法（classify_tool_outcome 单一源）"""

    def test_loop_step_success_is_none_not_success(self, runner):
        assert runner.classify_tool_outcome(LOOP_STEP_TOOL, {"output": "x"}) == GoalOutcome.NONE

    def test_loop_step_error_is_failed(self, runner):
        assert runner.classify_tool_outcome(LOOP_STEP_TOOL, {"error": "boom"}) == GoalOutcome.FAILED

    def test_loop_step_explicit_false_is_failed(self, runner):
        assert (
            runner.classify_tool_outcome(LOOP_STEP_TOOL, {"success": False}) == GoalOutcome.FAILED
        )

    def test_bash_unchanged(self, runner):
        assert runner.classify_tool_outcome("bash", {"exit_code": 0}) == GoalOutcome.SUCCESS
        assert runner.classify_tool_outcome("bash", {"exit_code": 1}) == GoalOutcome.FAILED


class TestLoopIteration:
    def test_iteration_success_no_success_inflation(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="整理文档"))
        r = run_loop_iteration(goal.goal_id, runner=runner, executor=ok_exec())
        assert r["performed"] is True and r["skipped"] is None
        assert r["loop_count"] == 1
        assert r["event_outcome"] == "none"
        g = runner.get_goal(goal.goal_id)
        assert g.success_count == 0 and g.failure_count == 0  # 禁 fabricated success
        assert g.events[-1].tool_name == LOOP_STEP_TOOL
        assert r["step"] == "下一步：整理文档"  # detail=执行器产出

    def test_iteration_failure_counts_failed(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="x"))
        r = run_loop_iteration(goal.goal_id, runner=runner, executor=err_exec())
        assert r["event_outcome"] == "failed"
        g = runner.get_goal(goal.goal_id)
        assert g.failure_count == 1 and g.success_count == 0

    def test_max_loops_stop_reachable(self, runner):
        """核心：record_loop 真消费 → max_loops 停机判定生产可达。"""
        goal = runner.create_goal(GoalCreate(session_id="s1", description="x", max_loops=2))
        gid = goal.goal_id
        r1 = run_loop_iteration(gid, runner=runner, executor=ok_exec())
        assert r1["state"] == "active"
        r2 = run_loop_iteration(gid, runner=runner, executor=ok_exec())
        assert r2["state"] == "failed"
        g = runner.get_goal(gid)
        assert g.loop_count == 2
        assert g.state == GoalState.FAILED
        assert "Max loops (2) exceeded" in g.state_history[-1]["reason"]
        # 序=先事件后计次：第 max_loops 次工作事件必须留痕
        assert len(g.events) == 2

    def test_fail_stop_via_loop_events(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="x", max_loops=50))
        gid = goal.goal_id
        for _ in range(3):
            r = run_loop_iteration(gid, runner=runner, executor=err_exec())
        assert r["state"] == "paused"
        g = runner.get_goal(gid)
        assert g.state == GoalState.PAUSED
        assert "Fail-stop" in g.state_history[-1]["reason"]

    def test_non_active_skips_executor(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="x"))
        gid = goal.goal_id
        runner.pause_goal(gid)
        calls = []

        def spy(goal):
            calls.append(1)
            return {"output": "never"}

        r = run_loop_iteration(gid, runner=runner, executor=spy)
        assert r["performed"] is False and r["skipped"] == "not_active:paused"
        assert calls == []

    def test_missing_goal_returns_none(self, runner):
        assert run_loop_iteration("nope", runner=runner, executor=ok_exec()) is None
        assert runner.record_loop("nope") is None


class TestChatterCannotComplete:
    def test_loop_chatter_cannot_pass_report_verification(self, runner):
        """循环 chatter（全 NONE）不得让 goal_report 'complete' 过事件验证。"""
        goal = runner.create_goal(GoalCreate(session_id="s1", description="x", max_loops=50))
        gid = goal.goal_id
        for _ in range(5):
            run_loop_iteration(gid, runner=runner, executor=ok_exec())
        g = runner.goal_report(GoalReport(goal_id=gid, status="complete", reason="我说完了"))
        assert g.state == GoalState.PAUSED
        assert "no success evidence" in g.state_history[-1]["reason"]

    def test_real_tool_evidence_still_completes(self, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="x", max_loops=50))
        gid = goal.goal_id
        run_loop_iteration(gid, runner=runner, executor=ok_exec())
        runner.record_event(gid, "bash", {"exit_code": 0})
        g = runner.goal_report(GoalReport(goal_id=gid, status="complete", reason="done"))
        assert g.state == GoalState.COMPLETED


class TestDefaultStepExecutor:
    def test_no_llm_config_honest_error(self, monkeypatch):
        monkeypatch.setattr(gl_mod, "settings", Settings(llm_api_key=""))
        GoalRunner.__new__(GoalRunner)
        from src.will.goal_runner import Goal

        g = Goal(description="x")
        out = default_step_executor(g)
        assert "error" in out and "无LLM配置" in out["error"]

    def test_whitespace_key_treated_as_no_config(self, monkeypatch):
        monkeypatch.setattr(gl_mod, "settings", Settings(llm_api_key="   "))
        from src.will.goal_runner import Goal

        out = default_step_executor(Goal(description="x"))
        assert "error" in out

    def test_real_http_stub_success(self, monkeypatch):
        """真 socket 桩：请求体回读 goal 逐字 + 正常 content 解析。"""
        captured = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers["Content-Length"])
                captured["body"] = json.loads(self.rfile.read(n))
                captured["auth"] = self.headers.get("Authorization")
                resp = json.dumps(
                    {"choices": [{"message": {"content": "下一步：写测试"}}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            monkeypatch.setattr(
                gl_mod,
                "settings",
                Settings(
                    llm_api_key="k-test",
                    llm_base_url=f"http://127.0.0.1:{port}/v1",
                    llm_model="m-test",
                ),
            )
            from src.will.goal_runner import Goal

            out = default_step_executor(Goal(description="整理知识库目录"))
            assert out == {"output": "下一步：写测试"}
            assert "整理知识库目录" in captured["body"]["messages"][0]["content"]
            assert captured["body"]["model"] == "m-test"
            assert captured["auth"] == "Bearer k-test"
        finally:
            srv.shutdown()

    def test_upstream_error_honest(self, monkeypatch):
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            monkeypatch.setattr(
                gl_mod,
                "settings",
                Settings(
                    llm_api_key="k", llm_base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1"
                ),
            )
            from src.will.goal_runner import Goal

            out = default_step_executor(Goal(description="x"))
            assert "error" in out and "HTTP 500" in out["error"]
        finally:
            srv.shutdown()


class TestBatchAndProactive:
    def test_run_active_goal_loops_ticks_all_active(self, runner):
        g1 = runner.create_goal(GoalCreate(session_id="s1", description="a"))
        g2 = runner.create_goal(GoalCreate(session_id="s2", description="b"))
        g3 = runner.create_goal(GoalCreate(session_id="s3", description="c"))
        runner.pause_goal(g3.goal_id)
        res = run_active_goal_loops(runner=runner, executor=ok_exec())
        assert res["active_goals"] == 2
        assert res["performed"] == 2 and res["skipped"] == 0
        assert runner.get_goal(g1.goal_id).loop_count == 1
        assert runner.get_goal(g2.goal_id).loop_count == 1
        assert runner.get_goal(g3.goal_id).loop_count == 0

    def test_single_goal_failure_does_not_flip_rest(self, runner):
        g1 = runner.create_goal(GoalCreate(session_id="s1", description="a"))
        g2 = runner.create_goal(GoalCreate(session_id="s2", description="b"))

        def selective(goal):
            if goal.goal_id == g1.goal_id:
                raise RuntimeError("executor boom")
            return {"output": "ok"}

        res = run_active_goal_loops(runner=runner, executor=selective)
        assert len(res["errors"]) == 1 and "executor boom" in res["errors"][0]
        assert res["performed"] == 1
        assert runner.get_goal(g2.goal_id).loop_count == 1

    def test_goal_loop_wired_into_default_watchers(self):
        from functools import partial

        from src.will.proactive import ProactiveEngine

        eng = ProactiveEngine(db_path="")
        names = pb_mod.register_default_watchers(eng)
        assert "goal_loop" in names, names
        w = eng._watchers["goal_loop"]
        assert w["interval_seconds"] == pb_mod.DEFAULT_INTERVALS["goal_loop"]
        assert isinstance(w["callback"], partial) and w["callback"].func is pb_mod.goal_loop

    def test_proactive_run_watcher_real_tick(self, monkeypatch, tmp_path):
        """run_watcher('goal_loop') 真跑（tmp 单例 + 确定性执行器）。"""
        from src.will.proactive import ProactiveEngine

        r = GoalRunner.__new__(GoalRunner)
        r._goals = {}
        r._PERSIST_PATH = tmp_path / "will_goals.json"
        monkeypatch.setattr(gr_mod, "_runner", r)
        g = r.create_goal(GoalCreate(session_id="s1", description="tick"))
        monkeypatch.setattr(gl_mod, "default_step_executor", ok_exec())
        eng = ProactiveEngine(db_path="")
        pb_mod.register_default_watchers(eng)
        res = asyncio.run(eng.run_watcher("goal_loop"))
        assert res["ok"] is True, res
        assert res["result"]["performed"] == 1
        assert r.get_goal(g.goal_id).loop_count == 1


class TestApiFace:
    @pytest.fixture()
    def client(self, runner, monkeypatch):
        import src.api.will as will_api

        monkeypatch.setattr(gr_mod, "_runner", runner)
        app = FastAPI()
        app.include_router(will_api.router, prefix="/api/will")
        return TestClient(app)

    def test_endpoint_iteration_and_loop_count(self, client, runner, monkeypatch):
        monkeypatch.setattr(gl_mod, "default_step_executor", ok_exec("下一步：验证"))
        goal = runner.create_goal(GoalCreate(session_id="s1", description="x", max_loops=50))
        r = client.post(f"/api/will/goals/{goal.goal_id}/loop")
        assert r.status_code == 200
        body = r.json()
        assert body["performed"] is True
        assert body["loop_count"] == 1
        assert body["event_outcome"] == "none"
        assert body["state"] == "active"

    def test_endpoint_honest_error_without_llm(self, client, runner):
        """无 LLM 配置（conftest 隔离态）：200 + event_outcome=failed + error 真因。"""
        goal = runner.create_goal(GoalCreate(session_id="s1", description="x"))
        r = client.post(f"/api/will/goals/{goal.goal_id}/loop")
        assert r.status_code == 200
        body = r.json()
        assert body["event_outcome"] == "failed"
        assert "无LLM配置" in body["step"]

    def test_endpoint_404_missing(self, client):
        r = client.post("/api/will/goals/no-such-goal/loop")
        assert r.status_code == 404

    def test_endpoint_cross_tenant_403(self, client, runner):
        goal = runner.create_goal(
            GoalCreate(session_id="s1", description="x"), tenant_id="tenant-a"
        )
        r = client.post(f"/api/will/goals/{goal.goal_id}/loop")
        assert r.status_code == 403
        assert "access denied" in r.json()["detail"]
        # 行零改写：跨租户 403 后 loop_count 不动
        assert runner.get_goal(goal.goal_id, tenant_id=None).loop_count == 0

    def test_endpoint_skips_non_active(self, client, runner):
        goal = runner.create_goal(GoalCreate(session_id="s1", description="x"))
        runner.pause_goal(goal.goal_id)
        r = client.post(f"/api/will/goals/{goal.goal_id}/loop")
        assert r.status_code == 200
        assert r.json()["performed"] is False
        assert r.json()["skipped"] == "not_active:paused"


class TestStaticWiring:
    def test_record_loop_first_production_consumer(self):
        """静态接线普查：record_loop 生产消费点=goal_loop 驱动（修前仅测试消费）。"""
        src = open("src/will/goal_loop.py", encoding="utf-8").read()
        assert "runner.record_loop(" in src
        api_src = open("src/api/will.py", encoding="utf-8").read()
        assert "run_loop_iteration" in api_src
        pb_src = open("src/will/proactive_behaviors.py", encoding="utf-8").read()
        assert "run_active_goal_loops" in pb_src
        # 单一源：LOOP_STEP_TOOL 定义在 goal_runner，消费方 import 不手抄
        assert (
            'LOOP_STEP_TOOL = "goal_loop_step"'
            in open("src/will/goal_runner.py", encoding="utf-8").read()
        )
        assert "LOOP_STEP_TOOL" in src
