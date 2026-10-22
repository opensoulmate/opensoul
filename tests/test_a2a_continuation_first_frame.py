"""F4b-⑯e a2a 续接首帧对称化——入史先于订阅（拆 F4b-⑧ 入史权）。

修前不对称（F4b-⑯c 留档候选）：SendStreamingMessage 续接路径订阅先于入史
（subscribe_stream → process_task 才 append），首帧快照缺刚发出的新消息；
而新任务首帧 create_task 已入史（history=[user]）=两路首帧不对称。

收口：入史权从 process_task 拆出（record_message 恰一次入史），流式续接路由缝
record_message（入史）→ subscribe_stream（首帧含新消息）→ run_processing（处理），
非流式续接 process_task=record_message+run_processing 契约不变。

真栈非 mock：直驱真 ASGI FastAPI 路由层 + 双向门闩（test_a2a_stream_frame_order
同款 harness）。
"""

import asyncio
import json

import pytest
from fastapi import FastAPI

from src.a2a.api import router as a2a_router
from src.a2a.models import A2AError, ErrorCode, Message, TaskStatus
from src.a2a.task_manager import task_manager

STUB = {"llm_mode": "stub"}
_END = object()


def _reopen(task_id):
    """§6.3 多轮正典：终态任务不再接受消息（F4b-⑯d）——续接前置回 input-required。"""
    task_manager.tasks[task_id].status = TaskStatus(state="input-required")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(a2a_router)
    return app


def _msg(text="hello cf", **extra):
    m = {"role": "ROLE_USER", "parts": [{"text": text}]}
    m.update(extra)
    return m


def _rpc(method, params, rid="cf-rpc"):
    return {"jsonrpc": "2.0", "method": method, "params": params, "id": rid}


class _SseClient:
    """直驱 ASGI 的单请求客户端：SSE 逐帧解码（增量进 queue + 全量进 frame_list）。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.queue: asyncio.Queue = asyncio.Queue()
        self.frame_list: list = []
        self.body = b""
        self.status = None
        self.headers = {}
        self.done = asyncio.Event()
        self.task: asyncio.Task | None = None

    async def _run(self, app):
        body = json.dumps(self.payload).encode()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "path": "/a2a",
            "raw_path": b"/a2a",
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "headers": [
                (b"host", b"testserver"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 50003),
            "server": ("testserver", 80),
        }
        state = {"sent": False}

        async def receive():
            if not state["sent"]:
                state["sent"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        buf = b""

        async def send(message):
            nonlocal buf
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self.headers = {k.decode().lower(): v.decode() for k, v in message["headers"]}
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                self.body += chunk
                if self.headers.get("content-type", "").startswith("text/event-stream"):
                    buf += chunk
                    while b"\n\n" in buf:
                        raw, buf = buf.split(b"\n\n", 1)
                        for line in raw.decode("utf-8").splitlines():
                            if line.startswith("data: "):
                                env = json.loads(line[6:])
                                self.frame_list.append(env)
                                await self.queue.put(env)

        try:
            await app(scope, receive, send)
        finally:
            self.done.set()
            await self.queue.put(_END)

    def start(self, app):
        self.task = asyncio.create_task(self._run(app))
        return self

    async def next_frame(self, timeout=8.0):
        return await asyncio.wait_for(self.queue.get(), timeout=timeout)

    async def collect(self, app, timeout=15.0):
        self.start(app)
        await asyncio.wait_for(self.done.wait(), timeout=timeout)
        return self

    def json_body(self):
        return json.loads(self.body.decode("utf-8"))


class _Gate:
    """双向门闩：处理入口挂住（entered），测试放行（release）后才返回应答。"""

    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = False

    def install(self, monkeypatch):
        gate = self

        async def _blocked_process(text, task):
            gate.entered.set()
            while not gate.release.is_set():
                await asyncio.sleep(0.01)
            gate.finished = True
            return "[gated-llm] done"

        monkeypatch.setattr(task_manager, "_process_message", _blocked_process)


@pytest.fixture(autouse=True)
def clean_tasks():
    """全局 TaskManager 隔离：任务/推送配置/投递留痕/流订阅表清空，事后逐字还原。"""
    subs = getattr(task_manager, "_stream_subs", None)
    saved_tasks = dict(task_manager.tasks)
    saved_cfgs = {k: dict(v) for k, v in task_manager.push_configs.items()}
    saved_log = list(task_manager.push_delivery_log)
    saved_subs = {k: list(v) for k, v in subs.items()} if subs is not None else {}
    task_manager.tasks.clear()
    task_manager.push_configs.clear()
    task_manager.push_delivery_log.clear()
    if subs is not None:
        subs.clear()
    try:
        yield task_manager
    finally:
        task_manager.tasks.clear()
        task_manager.tasks.update(saved_tasks)
        task_manager.push_configs.clear()
        task_manager.push_configs.update(saved_cfgs)
        task_manager.push_delivery_log.clear()
        task_manager.push_delivery_log.extend(saved_log)
        if subs is not None:
            subs.clear()
            subs.update(saved_subs)


def _first_task(frames, rid):
    env = frames[0]
    assert env["jsonrpc"] == "2.0" and env["id"] == rid
    result = env["result"]
    assert set(result) == {"task"}, f"首帧必须是 Task 对象：{result}"
    return result["task"]


async def _turn1_then_reopen(app, text="turn-1"):
    """跑完首轮（新任务）→ 回 input-required（§6.3），返回 task_id。"""
    c1 = await _SseClient(
        _rpc("SendStreamingMessage", {"message": _msg(text), "metadata": dict(STUB)}, rid="cf-1")
    ).collect(app)
    task_id = c1.frame_list[0]["result"]["task"]["id"]
    _reopen(task_id)
    return task_id


class TestContinuationFirstFrameSymmetry:
    """续接首帧与新任务首帧对称（本拍本体，API 全链）。"""

    @pytest.mark.asyncio
    async def test_continuation_first_frame_includes_new_message(self):
        """续接首帧快照含刚发出的新消息（history [u,a,u]，尾条=turn-2）——修前
        订阅先于入史首帧恰 2 条缺新消息（不对称）。state 仍订阅时点真值。"""
        app = _make_app()
        task_id = await _turn1_then_reopen(app)
        c2 = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("turn-2", taskId=task_id), "metadata": dict(STUB)},
                rid="cf-2",
            )
        ).collect(app)
        frames = c2.frame_list
        first = _first_task(frames, "cf-2")
        assert first["id"] == task_id  # 续接同任务
        assert first["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"  # 处理未启动
        assert [m["role"] for m in first["history"]] == [
            "ROLE_USER",
            "ROLE_AGENT",
            "ROLE_USER",
        ]
        assert first["history"][-1]["parts"] == [{"text": "turn-2"}]
        # 终稿 4 条 [u,a,u,a]（F4b-⑧ 契约零变化）
        final = frames[-1]["result"]["task"]
        assert [m["role"] for m in final["history"]] == [
            "ROLE_USER",
            "ROLE_AGENT",
            "ROLE_USER",
            "ROLE_AGENT",
        ]

    @pytest.mark.asyncio
    async def test_first_frame_tail_is_the_just_sent_message(self):
        """对称性明示：新任务首帧与续接首帧尾条=各自刚发出的消息（两路同语义）。"""
        app = _make_app()
        # 新任务路：首帧尾条=sent-a
        c_new = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("sent-a"), "metadata": dict(STUB)},
                rid="cf-3a",
            )
        ).collect(app)
        new_first = _first_task(c_new.frame_list, "cf-3a")
        assert new_first["history"][-1]["parts"] == [{"text": "sent-a"}]
        assert [m["role"] for m in new_first["history"]] == ["ROLE_USER"]

        # 续接路：首帧尾条=sent-b（与新任务路同语义=对称）
        _reopen(new_first["id"])
        c_cont = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("sent-b", taskId=new_first["id"]), "metadata": dict(STUB)},
                rid="cf-3b",
            )
        ).collect(app)
        cont_first = _first_task(c_cont.frame_list, "cf-3b")
        assert cont_first["history"][-1]["parts"] == [{"text": "sent-b"}]

    @pytest.mark.asyncio
    async def test_first_frame_message_before_processing_completes(self, monkeypatch):
        """入史先于订阅（非收尾搭车）：处理被门闩挂住时首帧已含新消息，且处理
        真在跑（GetTask 真源 history 与首帧一致=快照非 fabricate）。"""
        gate = _Gate()
        app = _make_app()
        task_id = await _turn1_then_reopen(app)  # 首轮不受门闩（门闩只挂续接轮）
        gate.install(monkeypatch)
        c = _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("gated-turn", taskId=task_id), "metadata": dict(STUB)},
                rid="cf-4",
            )
        ).start(app)
        first_task = _first_task([await c.next_frame(timeout=6)], "cf-4")
        assert first_task["history"][-1]["parts"] == [{"text": "gated-turn"}]
        assert first_task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert not gate.finished  # 处理未完成 = 首帧确系订阅时点交付

        await asyncio.wait_for(gate.entered.wait(), timeout=6)  # 处理真在跑
        got = await _SseClient(_rpc("GetTask", {"id": task_id}, rid="cf-4g")).collect(app)
        truth = got.json_body()["result"]
        assert [m["messageId"] for m in truth["history"]] == [
            m["messageId"] for m in first_task["history"]
        ]  # 真源与首帧逐条一致（禁 fabricate）
        assert truth["status"]["state"] == "TASK_STATE_WORKING"

        gate.release.set()
        while True:
            item = await c.next_frame(timeout=8)
            if item is _END:
                break

    @pytest.mark.asyncio
    async def test_new_message_exactly_once_in_history(self):
        """恰一次反证（F4b-⑧ 守恒，拆入史权最大风险=双 append 复发）：新消息在
        首帧/终稿/真源各恰一次，终稿恰 [u,a,u,a]。"""
        app = _make_app()
        task_id = await _turn1_then_reopen(app)
        c2 = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("once-turn", taskId=task_id), "metadata": dict(STUB)},
                rid="cf-5",
            )
        ).collect(app)
        first = _first_task(c2.frame_list, "cf-5")
        new_id = first["history"][-1]["messageId"]
        assert [m["messageId"] for m in first["history"]].count(new_id) == 1

        final = c2.frame_list[-1]["result"]["task"]
        ids = [m["messageId"] for m in final["history"]]
        assert ids.count(new_id) == 1, "拆入史权后双 append 复发"
        assert len(ids) == 4  # [u,a,u,a] 恰 4 条
        got = await _SseClient(_rpc("GetTask", {"id": task_id}, rid="cf-5g")).collect(app)
        assert [m["messageId"] for m in got.json_body()["result"]["history"]].count(new_id) == 1

    @pytest.mark.asyncio
    async def test_non_streaming_continuation_unchanged(self):
        """非流式续接（process_task 路）契约零变化：终态 Task history [u,a,u,a]，
        新消息恰一次——拆权只动流式路由缝。"""
        app = _make_app()
        c1 = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("ns-1"), "metadata": dict(STUB)},
                rid="cf-6a",
            )
        ).collect(app)
        task_id = c1.frame_list[0]["result"]["task"]["id"]
        _reopen(task_id)

        c2 = await _SseClient(
            _rpc(
                "SendMessage",
                {"message": _msg("ns-2", taskId=task_id), "metadata": dict(STUB)},
                rid="cf-6b",
            )
        ).collect(app)
        task = c2.json_body()["result"]["task"]
        assert [m["role"] for m in task["history"]] == [
            "ROLE_USER",
            "ROLE_AGENT",
            "ROLE_USER",
            "ROLE_AGENT",
        ]
        texts = [p["text"] for m in task["history"] for p in m["parts"]]
        assert texts.count("ns-2") == 1  # 恰一次
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"


class TestRecordMessageAuthority:
    """record_message 入史权单元语义（TaskManager 层）。"""

    def test_record_message_appends_exactly_once(self):
        task = asyncio.run(
            task_manager.create_task(Message(role="user", parts=[{"type": "text", "text": "h"}]))
        )
        msg = Message(role="user", parts=[{"type": "text", "text": "m"}])
        got = task_manager.record_message(task.id, msg, metadata={"k": "v"})
        assert got is task
        assert [m.messageId for m in task.history].count(msg.messageId) == 1
        assert task.metadata["k"] == "v"  # metadata 更新同 process_task 契约

    def test_record_message_unknown_task_raises_32001(self):
        """不存在任务=A2AError(-32001)（process_task 旧 raise 点同码同文案，契约不变）。"""
        with pytest.raises(A2AError) as ei:
            task_manager.record_message(
                "no-such-task", Message(role="user", parts=[{"type": "text", "text": "x"}])
            )
        assert ei.value.code == ErrorCode.TASK_NOT_FOUND
        assert "no-such-task" in str(ei.value)

    @pytest.mark.asyncio
    async def test_process_task_delegation_contract_unchanged(self):
        """process_task=record_message+run_processing（委托后契约零变化）：首轮
        create+run_processing 后续接，入史恰一次 + agent 回复入史 + 终态 completed。"""
        msg0 = Message(role="user", parts=[{"type": "text", "text": "h"}])
        # 首轮建任务即带 STUB 元数据（本文件 llm_mode=stub 约定）：修前首轮不带 STUB=
        # 走真实 LLM 路径，上游不可达时预算耗尽→failed（环境耦合假失败，RV-①c 逼出的
        # 测试敏感性收口；断言意图零丢失=仍验委托后契约）。
        task = await task_manager.create_task(msg0, metadata=dict(STUB))
        await task_manager.run_processing(task, msg0)
        msg = Message(role="user", parts=[{"type": "text", "text": "m"}])
        out = await task_manager.process_task(task.id, msg, metadata=dict(STUB))
        assert out.status.state == "completed"
        assert [m.role for m in task.history] == ["user", "agent", "user", "agent"]
        assert [m.messageId for m in task.history].count(msg.messageId) == 1
