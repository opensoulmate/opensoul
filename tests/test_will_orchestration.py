"""工具链编排·节点级错误恢复 + 失败补偿回滚（saga）测试。

对齐 docs/roadmap.md Phase 2 #3「工具链编排 | 多工具串联、错误恢复、回滚 | will扩展」：
- ① 节点级重试：config.retry 次内重试（默认 0=原语义不变），attempts 落 step 可观测
- ② 失败补偿回滚：已成功节点逆序执行 config.compensate 动作（saga），补偿失败只记录
  不中断其余补偿，汇总追加 execution.error
- ③ 断点续跑：被成功补偿的节点效果已撤销，resume 必须重放（补偿失败=效果仍在不重放）

单元测试无外网（HTTP 用本地真 httpx 栈断连/正常双形态；script 用 tmp_path 真 shell）；
live 测试经 conftest client 打 :8090（见 Gate3）。
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from src.will.engine import WorkflowEngine
from src.will.models import ExecutionStatus, NodeType

# ── Helpers ─────────────────────────────────────────────────────


@pytest.fixture
def make_engine(tmp_path, monkeypatch):
    def _make(name: str = "a") -> WorkflowEngine:
        monkeypatch.setattr(WorkflowEngine, "_PERSIST_PATH", tmp_path / f"wf_{name}.json")
        return WorkflowEngine(checkpoint_db=tmp_path / f"ckpt_{name}.db")

    return _make


def _wire(engine: WorkflowEngine, wf_id: str, nodes: list) -> None:
    for src, dst in zip(nodes, nodes[1:]):
        engine.add_edge(wf_id, src.id, dst.id)


def _fix_command(engine: WorkflowEngine, wf_id: str, node_id: str, command: str) -> None:
    wf = engine.get_workflow(wf_id)
    assert wf is not None
    for node in wf.nodes:
        if node.id == node_id:
            node.config["command"] = command


class _FlakyHandler(BaseHTTPRequestHandler):
    """前 fail_first 次直接断连（不回响应）→ httpx 协议错误→ ValueError→ 重试；之后 200。"""

    attempts = 0
    fail_first = 2

    def do_GET(self):  # noqa: N802
        cls = type(self)
        cls.attempts += 1
        if cls.attempts <= cls.fail_first:
            self.connection.close()
            return
        body = b'{"ok": true, "attempt": %d}' % cls.attempts
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默测试服务日志
        pass


@pytest.fixture
def flaky_server():
    """真 HTTP 服务：前 N 次断连、之后 200（真 httpx 栈，非 mock）。"""
    handler = type("Handler", (_FlakyHandler,), {"attempts": 0, "fail_first": 2})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/flaky", handler
    server.shutdown()
    server.server_close()


# ── ① 节点级重试（错误恢复）─────────────────────────────────────


class TestNodeRetry:
    def test_retry_recovers_after_transient_failures(self, make_engine, flaky_server):
        """真栈：HTTP 前 2 次断连→重试→第 3 次 200，执行成功且 attempts=3 可观测。"""
        url, handler = flaky_server
        engine = make_engine()
        wf = engine.create_workflow(name="retry-ok")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_act = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="flaky-call",
            config={"type": "http", "url": url, "retry": 3, "retry_delay": 0},
        )
        n_end = engine.add_node(wf.id, NodeType.END, label="done")
        _wire(engine, wf.id, [n_trigger, n_act, n_end])

        execution = asyncio.run(engine.execute_async(wf.id))

        assert execution is not None
        assert execution.status == ExecutionStatus.SUCCESS, execution.error
        assert handler.attempts == 3
        act_step = next(s for s in execution.steps if s.node_id == n_act.id)
        assert act_step.status == ExecutionStatus.SUCCESS
        assert act_step.attempts == 3
        assert act_step.output_data.get("success") is True

    def test_retry_exhausted_records_attempts_and_error(self, make_engine):
        """重试耗尽：attempts=retry+1，error 带 (after N attempts) 不谎报。"""
        engine = make_engine()
        wf = engine.create_workflow(name="retry-fail")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_act = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="always-fail",
            # notify 缺 content 必抛 ValueError（真实动作层校验，非 mock）
            config={"type": "notify", "title": "x", "retry": 2, "retry_delay": 0},
        )
        _wire(engine, wf.id, [n_trigger, n_act])

        execution = asyncio.run(engine.execute_async(wf.id))

        assert execution is not None
        assert execution.status == ExecutionStatus.FAILED
        act_step = next(s for s in execution.steps if s.node_id == n_act.id)
        assert act_step.status == ExecutionStatus.FAILED
        assert act_step.attempts == 3
        assert "(after 3 attempts)" in (act_step.error or "")
        assert "always-fail" in (execution.error or "")

    def test_soft_failure_triggers_retry_then_recovers(self, make_engine, tmp_path):
        """动作层显式 success:False（脚本非零退出）也走重试——真 shell 自愈旗标：
        首次 exit 1 并落旗标，重试第二次命中旗标成功。"""
        flag = tmp_path / "healed.flag"
        engine = make_engine()
        wf = engine.create_workflow(name="soft-retry")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_act = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="soft-fail-then-heal",
            config={
                "type": "script",
                "command": f"if [ -f {flag} ]; then echo healed; else touch {flag}; exit 1; fi",
                "retry": 2,
                "retry_delay": 0,
            },
        )
        _wire(engine, wf.id, [n_trigger, n_act])

        execution = asyncio.run(engine.execute_async(wf.id))

        assert execution is not None
        assert execution.status == ExecutionStatus.SUCCESS, execution.error
        act_step = next(s for s in execution.steps if s.node_id == n_act.id)
        assert act_step.attempts == 2
        assert act_step.output_data.get("success") is True

    def test_default_retry_off_is_unchanged_semantics(self, make_engine, flaky_server):
        """控制组：retry 缺省 0=原语义——失败即停，只执行 1 次不重试。"""
        url, handler = flaky_server
        handler.fail_first = 10  # 恒断连
        engine = make_engine()
        wf = engine.create_workflow(name="retry-off")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_act = engine.add_node(
            wf.id, NodeType.ACTION, label="dead-call", config={"type": "http", "url": url}
        )
        _wire(engine, wf.id, [n_trigger, n_act])

        execution = asyncio.run(engine.execute_async(wf.id))

        assert execution is not None
        assert execution.status == ExecutionStatus.FAILED
        act_step = next(s for s in execution.steps if s.node_id == n_act.id)
        assert act_step.attempts == 1
        assert handler.attempts == 1
        assert "(after" not in (act_step.error or "")


# ── ② 失败补偿回滚（saga）───────────────────────────────────────


class TestCompensation:
    def test_rollback_reverse_order_removes_effects(self, make_engine, tmp_path):
        """真 shell：A/B 各落副作用文件+compensate 删文件，C 必炸→A/B 逆序回滚，文件全消。"""
        f1, f2 = tmp_path / "a.txt", tmp_path / "b.txt"
        engine = make_engine()
        wf = engine.create_workflow(name="saga")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_a = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="step-a",
            config={
                "type": "script",
                "command": f"echo a > {f1}",
                "compensate": {"type": "script", "command": f"rm -f {f1}"},
            },
        )
        n_b = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="step-b",
            config={
                "type": "script",
                "command": f"echo b > {f2}",
                "compensate": {"type": "script", "command": f"rm -f {f2}"},
            },
        )
        n_c = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="step-c",
            config={"type": "script", "command": "mkfs"},  # 阻断命令必抛→链失败
        )
        _wire(engine, wf.id, [n_trigger, n_a, n_b, n_c])

        execution = asyncio.run(engine.execute_async(wf.id))

        assert execution is not None
        assert execution.status == ExecutionStatus.FAILED
        assert not f1.exists() and not f2.exists(), "compensate 必须真撤销副作用"
        comp_steps = [s for s in execution.steps if s.node_id.startswith("compensate:")]
        assert [s.node_id for s in comp_steps] == [f"compensate:{n_b.id}", f"compensate:{n_a.id}"]
        assert all(s.status == ExecutionStatus.SUCCESS for s in comp_steps)
        assert "compensation: 2 rolled back" in (execution.error or "")

    def test_compensation_failure_recorded_not_silent(self, make_engine, tmp_path):
        """补偿自身失败：只记录不中断其余补偿（best-effort 全跑完，禁静默）。"""
        f1, f2 = tmp_path / "a.txt", tmp_path / "b.txt"
        engine = make_engine()
        wf = engine.create_workflow(name="saga-partial")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_a = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="step-a",
            config={
                "type": "script",
                "command": f"echo a > {f1}",
                "compensate": {"type": "script", "command": "mkfs"},  # 补偿必炸
            },
        )
        n_b = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="step-b",
            config={
                "type": "script",
                "command": f"echo b > {f2}",
                "compensate": {"type": "script", "command": f"rm -f {f2}"},
            },
        )
        n_c = engine.add_node(
            wf.id, NodeType.ACTION, label="step-c", config={"type": "script", "command": "mkfs"}
        )
        _wire(engine, wf.id, [n_trigger, n_a, n_b, n_c])

        execution = asyncio.run(engine.execute_async(wf.id))

        assert execution is not None
        assert execution.status == ExecutionStatus.FAILED
        assert f1.exists(), "补偿失败=效果仍在，如实留档不假装已撤销"
        assert not f2.exists(), "B 的补偿不受 A 补偿失败影响，仍执行"
        comp_by_node = {
            s.node_id: s for s in execution.steps if s.node_id.startswith("compensate:")
        }
        assert comp_by_node[f"compensate:{n_a.id}"].status == ExecutionStatus.FAILED
        assert comp_by_node[f"compensate:{n_a.id}"].error
        assert comp_by_node[f"compensate:{n_b.id}"].status == ExecutionStatus.SUCCESS
        assert "1 failed" in (execution.error or "")
        assert "1 rolled back" in (execution.error or "")

    def test_no_compensate_spec_no_fake_rollback(self, make_engine, tmp_path):
        """未声明 compensate 的节点不硬造回滚（诚实边界：不假装已撤销）。"""
        f1 = tmp_path / "a.txt"
        engine = make_engine()
        wf = engine.create_workflow(name="no-saga")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_a = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="step-a",
            config={"type": "script", "command": f"echo a > {f1}"},
        )
        n_c = engine.add_node(
            wf.id, NodeType.ACTION, label="step-c", config={"type": "script", "command": "mkfs"}
        )
        _wire(engine, wf.id, [n_trigger, n_a, n_c])

        execution = asyncio.run(engine.execute_async(wf.id))

        assert execution is not None
        assert execution.status == ExecutionStatus.FAILED
        assert f1.exists(), "无 compensate 声明=不回滚，副作用如实留档"
        assert not any(s.node_id.startswith("compensate:") for s in execution.steps)
        assert "compensation" not in (execution.error or "")

    def test_soft_failure_triggers_compensation(self, make_engine, tmp_path):
        """动作层显式 success:False（C 脚本 exit 1，非异常）同样触发补偿回滚。"""
        f1 = tmp_path / "a.txt"
        engine = make_engine()
        wf = engine.create_workflow(name="saga-soft")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_a = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="step-a",
            config={
                "type": "script",
                "command": f"echo a > {f1}",
                "compensate": {"type": "script", "command": f"rm -f {f1}"},
            },
        )
        n_c = engine.add_node(
            wf.id, NodeType.ACTION, label="step-c", config={"type": "script", "command": "exit 1"}
        )
        _wire(engine, wf.id, [n_trigger, n_a, n_c])

        execution = asyncio.run(engine.execute_async(wf.id))

        assert execution is not None
        assert execution.status == ExecutionStatus.FAILED
        assert "action reported failure" in (execution.error or "")
        assert not f1.exists(), "软失败同样触发补偿，副作用真撤销"
        assert "compensation: 1 rolled back" in (execution.error or "")

    def test_compensation_soft_failure_recorded_not_rolled_back(self, make_engine, tmp_path):
        """补偿脚本非零退出（success:False）=补偿失败如实记 FAILED，不谎报已回滚。"""
        f1 = tmp_path / "a.txt"
        engine = make_engine()
        wf = engine.create_workflow(name="saga-soft-comp")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_a = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="step-a",
            config={
                "type": "script",
                "command": f"echo a > {f1}",
                "compensate": {"type": "script", "command": "exit 1"},
            },
        )
        n_c = engine.add_node(
            wf.id, NodeType.ACTION, label="step-c", config={"type": "script", "command": "exit 1"}
        )
        _wire(engine, wf.id, [n_trigger, n_a, n_c])

        execution = asyncio.run(engine.execute_async(wf.id))

        assert execution is not None
        assert execution.status == ExecutionStatus.FAILED
        assert f1.exists(), "补偿失败=效果仍在，不假装已撤销"
        comp_step = next(s for s in execution.steps if s.node_id.startswith("compensate:"))
        assert comp_step.status == ExecutionStatus.FAILED
        assert "compensation action reported failure" in (comp_step.error or "")
        assert "1 failed" in (execution.error or "")

    def test_compensation_survives_checkpoint_roundtrip(self, make_engine, tmp_path):
        """补偿步骤随 steps checkpoint 持久化（重启后回读仍在）。"""
        f1 = tmp_path / "a.txt"
        engine = make_engine()
        wf = engine.create_workflow(name="saga-ckpt")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_a = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="step-a",
            config={
                "type": "script",
                "command": f"echo a > {f1}",
                "compensate": {"type": "script", "command": f"rm -f {f1}"},
            },
        )
        n_c = engine.add_node(
            wf.id, NodeType.ACTION, label="step-c", config={"type": "script", "command": "mkfs"}
        )
        _wire(engine, wf.id, [n_trigger, n_a, n_c])

        execution = asyncio.run(engine.execute_async(wf.id))
        assert execution is not None
        loaded = engine._checkpoints.load(execution.id)
        assert loaded is not None
        comp_steps = [s for s in loaded.steps if s.node_id.startswith("compensate:")]
        assert len(comp_steps) == 1 and comp_steps[0].status == ExecutionStatus.SUCCESS


# ── ③ 断点续跑：被补偿节点必须重放 ──────────────────────────────


class TestResumeAfterCompensation:
    def test_resume_replays_compensated_nodes(self, make_engine, tmp_path):
        """A 建文件+compensate 删文件、C 必炸→回滚后 A 效果已撤销；
        修复 C 后 resume：A 必须重放（文件重建），而非被当"已完成"跳过。"""
        f1 = tmp_path / "a.txt"
        engine = make_engine()
        wf = engine.create_workflow(name="resume-saga")
        n_trigger = engine.add_node(wf.id, NodeType.TRIGGER, label="start")
        n_a = engine.add_node(
            wf.id,
            NodeType.ACTION,
            label="step-a",
            config={
                "type": "script",
                "command": f"echo a > {f1}",
                "compensate": {"type": "script", "command": f"rm -f {f1}"},
            },
        )
        n_c = engine.add_node(
            wf.id, NodeType.ACTION, label="step-c", config={"type": "script", "command": "mkfs"}
        )
        n_end = engine.add_node(wf.id, NodeType.END, label="done")
        _wire(engine, wf.id, [n_trigger, n_a, n_c, n_end])

        execution = asyncio.run(engine.execute_async(wf.id))
        assert execution is not None
        assert execution.status == ExecutionStatus.FAILED
        assert not f1.exists(), "回滚后 A 的效果已撤销"

        _fix_command(engine, wf.id, n_c.id, "echo fixed")
        resumed, reason = asyncio.run(engine.resume_execution(execution.id))

        assert resumed is not None, reason
        assert resumed.status == ExecutionStatus.SUCCESS, resumed.error
        assert f1.exists(), "被补偿回滚的节点必须重放重建效果（不许当已完成跳过）"
        a_steps = [s for s in resumed.steps if s.node_id == n_a.id]
        assert len(a_steps) == 1 and a_steps[0].status == ExecutionStatus.SUCCESS
        assert not any(s.node_id.startswith("compensate:") for s in resumed.steps)
