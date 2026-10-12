"""F4b-⑱ a2a input-required 产生面 + 中断态收流语义（§6.3/§11.7）。

修前缺口（F4b-⑰ 留档候选"input-required 产生面"）：
① 全仓无任何代码把任务置为 input-required（grep 实证：仅测试 _reopen 手工置位）
   ——§6.3 message.taskId 多轮正典生产不可达（终态拒收 F4b-⑯d 后，续接只剩
   contextId 新任务一路）。
② 中断态收流语义缺失：subscribe_stream 首帧收流判定只认终态——订阅已
   input-required（中断态）任务首帧后流悬挂；§11.7 明文「until the task reaches
   a terminal or interrupted state, at which point the stream closes」。
③ 消息无可提取文本（空 parts/仅图片/仅文件/仅 data part）时拿空 prompt 硬跑
   LLM（llm_mode=stub 则回显空消息）→ fabricated completed。

收口：TaskInputRequired 产生信号 + TaskManager.require_input 唯一产生口（问句以
agent 消息入史恰一次+状态置中断态）+ run_processing 归口（真实触发=无可提取文本）
+ _STREAM_END_STATES（终态+中断态）收流判定。续接零改动（ensure_accepts_message
非终态接受=§6.3 既有语义），input-required → 用户回答 → 同任务 completed。

真栈非 mock：in-process TestClient（src.main app 全路由）+ 直驱真 ASGI SSE
（test_a2a_stream_frame_order 同款 harness）。
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.a2a.api import router as a2a_router
from src.a2a.models import TaskPushNotificationConfig
from src.a2a.task_manager import task_manager
from src.main import app

STUB = {"llm_mode": "stub"}
_END = object()
_QUESTION_MARK = "补充文字说明"


def _msg(text="hello input-required", **extra):
    m = {"role": "ROLE_USER", "parts": [{"text": text}], "messageId": f"ir-{text}"}
    m.update(extra)
    return m


def _rpc(client, method, params, rid="input-required"):
    resp = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    )
    assert resp.status_code == 200
    return resp.json()


def _send(client, text="turn-1", rid="ir-new", **msg_extra):
    data = _rpc(
        client,
        "SendMessage",
        {"message": _msg(text, **msg_extra), "metadata": dict(STUB)},
        rid=rid,
    )
    assert "result" in data, data
    return data["result"]["task"]


def _wire_rpc(method, params, rid):
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


def _make_app() -> FastAPI:
    a = FastAPI()
    a.include_router(a2a_router)
    return a


def _kind(env: dict) -> str:
    return next(iter(env["result"]))


class _SseClient:
    """直驱 ASGI 的单请求客户端：SSE 逐帧解码（test_a2a_stream_frame_order 同款）。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.queue: asyncio.Queue = asyncio.Queue()
        self.frame_list: list = []
        self.body = b""
        self.status = None
        self.headers = {}
        self.done = asyncio.Event()
        self.task: asyncio.Task | None = None

    async def _run(self, app_):
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
            await app_(scope, receive, send)
        finally:
            self.done.set()
            await self.queue.put(_END)

    def start(self, app_):
        self.task = asyncio.create_task(self._run(app_))
        return self

    async def next_frame(self, timeout=8.0):
        return await asyncio.wait_for(self.queue.get(), timeout=timeout)

    async def collect(self, app_, timeout=12.0):
        self.start(app_)
        await asyncio.wait_for(self.done.wait(), timeout=timeout)
        return self


def _assert_envelope(env: dict, rid):
    assert env["jsonrpc"] == "2.0"
    assert env["id"] == rid
    assert "error" not in env
    result = env["result"]
    assert len(result) == 1, f"StreamResponse 必须恰一键：{result}"
    return result


@pytest.fixture()
def app_client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def clean_tasks():
    """全局 TaskManager 隔离（含流订阅表），事后逐字还原。"""
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


@pytest.fixture(autouse=True)
def isolate_intrusion_state():
    """WAF 单例测试隔离（test_a2a_push_notifications 坑记档：密集突发毒化共享 IP）。"""
    from src.api.immune import intrusion

    intrusion._ip_suspicion.clear()
    intrusion._request_counts.clear()
    yield
    intrusion._ip_suspicion.clear()
    intrusion._request_counts.clear()


class TestInputRequiredProduction:
    """产生面：无可提取文本 → input-required（修前 fabricated completed）。"""

    def test_no_text_message_goes_input_required(self, app_client, clean_tasks):
        task = _send(app_client, "")
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert [m["role"] for m in task["history"]] == ["ROLE_USER", "ROLE_AGENT"]
        question = task["history"][-1]["parts"][0]["text"]
        assert _QUESTION_MARK in question
        # status.message=问句（与 history 尾条同文），wire 面 role 为 ROLE_AGENT
        assert task["status"]["message"]["parts"][0]["text"] == question
        assert task["status"]["message"]["role"] == "ROLE_AGENT"

    def test_question_recorded_exactly_once(self, app_client, clean_tasks):
        """F4b-⑧ 入史纪律：问句恰入史一次（防双 append 复发）。"""
        task = _send(app_client, "")
        agent_msgs = [m for m in task["history"] if m["role"] == "ROLE_AGENT"]
        assert len(task["history"]) == 2
        assert len(agent_msgs) == 1
        # GetTask 真源一致（同一真源双读）
        again = _rpc(app_client, "GetTask", {"id": task["id"]})["result"]
        assert len(again["history"]) == 2
        assert again["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"

    @pytest.mark.parametrize(
        "parts",
        [
            [],
            [{"raw": "aGVsbG8=", "mediaType": "image/png"}],
            [{"data": {"k": "v"}}],
            [{"url": "https://example.com/x.png"}],
            [{"text": "   "}],
        ],
    )
    def test_non_text_only_message_triggers(self, app_client, clean_tasks, parts):
        """空/仅图片/仅文件/仅 data/仅 url/纯空白——均无可提取文本，同触发。"""
        data = _rpc(
            app_client,
            "SendMessage",
            {
                "message": {"role": "ROLE_USER", "parts": parts},
                "metadata": dict(STUB),
            },
            rid="ir-nt",
        )
        task = data["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert _QUESTION_MARK in task["history"][-1]["parts"][0]["text"]

    def test_text_message_zero_change(self, app_client, clean_tasks):
        """回归锁死：有文本消息仍走处理→completed（stub 确定性应答），artifacts 零变化。"""
        task = _send(app_client, "正常文本")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert "stub-llm" in task["history"][-1]["parts"][0]["text"]
        assert task.get("artifacts") in ([], None) or task["artifacts"] == []


class TestContinuationSection63:
    """§6.3 message.taskId 续接生产可达（修前仅测试手工置位可达）。"""

    def test_answer_continues_same_task(self, app_client, clean_tasks):
        task = _send(app_client, "")
        task_id = task["id"]
        data = _rpc(
            app_client,
            "SendMessage",
            {
                "message": _msg("这是补充的文字说明", taskId=task_id),
                "metadata": dict(STUB),
            },
            rid="ir-cont",
        )
        cont = data["result"]["task"]
        assert cont["id"] == task_id  # 同任务续接
        assert cont["status"]["state"] == "TASK_STATE_COMPLETED"
        roles = [m["role"] for m in cont["history"]]
        assert roles == ["ROLE_USER", "ROLE_AGENT", "ROLE_USER", "ROLE_AGENT"]
        assert _QUESTION_MARK in cont["history"][1]["parts"][0]["text"]
        assert cont["history"][2]["parts"][0]["text"] == "这是补充的文字说明"
        assert "stub-llm" in cont["history"][3]["parts"][0]["text"]

    def test_no_text_answer_reasks(self, app_client, clean_tasks):
        """续接又发无文本消息 → 再次 input-required（状态机连贯，history 不丢不重）。"""
        task = _send(app_client, "")
        data = _rpc(
            app_client,
            "SendMessage",
            {
                "message": {"role": "ROLE_USER", "parts": [], "taskId": task["id"]},
                "metadata": dict(STUB),
            },
            rid="ir-reask",
        )
        again = data["result"]["task"]
        assert again["id"] == task["id"]
        assert again["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        roles = [m["role"] for m in again["history"]]
        assert roles == ["ROLE_USER", "ROLE_AGENT", "ROLE_USER", "ROLE_AGENT"]

    def test_terminal_gate_unchanged(self, app_client, clean_tasks):
        """回归锁死：续接完成后任务回终态，再收消息仍 -32004（F4b-⑯d 零回归）。"""
        task = _send(app_client, "")
        _rpc(
            app_client,
            "SendMessage",
            {"message": _msg("补充", taskId=task["id"]), "metadata": dict(STUB)},
            rid="ir-fin",
        )
        data = _rpc(
            app_client,
            "SendMessage",
            {"message": _msg("再来", taskId=task["id"]), "metadata": dict(STUB)},
            rid="ir-gate",
        )
        assert "error" in data
        assert data["error"]["code"] == -32004


class TestInterruptedStreamClose:
    """中断态收流语义（§11.7「terminal or interrupted…stream closes」）。"""

    @pytest.mark.asyncio
    async def test_send_streaming_empty_text_closes_at_interrupted(self, clean_tasks):
        app_ = _make_app()
        c = await _SseClient(
            _wire_rpc(
                "SendStreamingMessage",
                {"message": _msg(""), "metadata": dict(STUB)},
                rid="ir-sse-1",
            )
        ).collect(app_)
        assert c.status == 200
        frames = c.frame_list
        # 帧序文法：[Task 首帧][statusUpdate×2][收尾 Task 快照] 收流（§3.1.2/§11.7）
        assert len(frames) == 4, [_kind(f) for f in frames]
        first = _assert_envelope(frames[0], "ir-sse-1")
        assert set(first) == {"task"}
        assert first["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
        kinds = [_kind(f) for f in frames[1:-1]]
        assert kinds == ["statusUpdate", "statusUpdate"]
        last = _assert_envelope(frames[-1], "ir-sse-1")
        assert set(last) == {"task"}
        # 中断态收流=末帧快照 input-required（不 fabricated completed/failed）
        assert last["task"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert _QUESTION_MARK in last["task"]["history"][-1]["parts"][0]["text"]
        # 末帧即收尾快照（end）→ collect 正常返回=流真收束（禁悬挂）

    @pytest.mark.asyncio
    async def test_subscribe_to_interrupted_task_single_frame_close(self, clean_tasks):
        """订阅已 input-required（中断态）任务：恰一帧快照即收流（修前流悬挂）。"""
        # 先造成 input-required 任务（非流式口）
        with TestClient(_make_app()) as client:
            task = _send(client, "", rid="ir-sub-new")
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"

        app_ = _make_app()
        c = await _SseClient(
            _wire_rpc("SubscribeToTask", {"id": task["id"]}, rid="ir-sub-1")
        ).collect(app_, timeout=8.0)
        frames = c.frame_list
        assert len(frames) == 1, [_kind(f) for f in frames]
        first = _assert_envelope(frames[0], "ir-sub-1")
        assert set(first) == {"task"}
        assert first["task"]["id"] == task["id"]
        assert first["task"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"

    @pytest.mark.asyncio
    async def test_continuation_stream_first_frame_has_answer(self, clean_tasks):
        """续接流式口：首帧含刚发出的回答（F4b-⑯e 对称语义零回归）→ 正常完成收流。"""
        app_ = _make_app()
        # 首轮：无文本 → input-required（流收束）
        c1 = await _SseClient(
            _wire_rpc(
                "SendStreamingMessage",
                {"message": _msg(""), "metadata": dict(STUB)},
                rid="ir-sse-2a",
            )
        ).collect(app_)
        task_id = _assert_envelope(c1.frame_list[0], "ir-sse-2a")["task"]["id"]

        # 续接：带 taskId 的回答（§6.3）→ 同任务处理 → completed
        c2 = await _SseClient(
            _wire_rpc(
                "SendStreamingMessage",
                {"message": _msg("补充的文字说明", taskId=task_id), "metadata": dict(STUB)},
                rid="ir-sse-2b",
            )
        ).collect(app_)
        frames = c2.frame_list
        first = _assert_envelope(frames[0], "ir-sse-2b")
        assert first["task"]["id"] == task_id
        assert [m["role"] for m in first["task"]["history"]] == [
            "ROLE_USER",
            "ROLE_AGENT",
            "ROLE_USER",
        ]
        assert first["task"]["history"][-1]["parts"][0]["text"] == "补充的文字说明"
        last = _assert_envelope(frames[-1], "ir-sse-2b")
        assert set(last) == {"task"}
        assert last["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


class TestRequireInputUnit:
    """require_input 唯一产生口单元语义。"""

    @pytest.mark.asyncio
    async def test_require_input_records_question_once(self, clean_tasks):
        from src.a2a.models import Message

        task = await task_manager.create_task(
            Message(role="user", parts=[{"type": "text", "text": "hi"}])
        )
        before = len(task.history)
        out = task_manager.require_input(task, "请问需要处理什么？")
        assert out is task
        assert task.status.state == "input-required"
        assert len(task.history) == before + 1  # 恰一次
        assert task.history[-1] is task.status.message  # 同一对象，禁双份
        assert task.history[-1].role == "agent"
        assert task.history[-1].parts[0]["text"] == "请问需要处理什么？"
        # 非终态（可续接 §6.3）
        assert task.status.state not in ("completed", "failed", "canceled", "rejected")

    @pytest.mark.asyncio
    async def test_push_payload_status_update_on_interrupted(self, clean_tasks, monkeypatch):
        """中断态推送= {"statusUpdate"}（_notify_push 非终态判定，§4.3.3）。"""
        from src.a2a.models import Message

        captured = []

        async def _capture(task, payload):
            captured.append(payload)

        monkeypatch.setattr(task_manager, "_dispatch_push", _capture)
        task = await task_manager.create_task(
            Message(role="user", parts=[{"type": "text", "text": "hi"}])
        )
        task_manager.create_push_config(task.id, TaskPushNotificationConfig(url="http://x"))
        task_manager.require_input(task, "需要补充输入")
        await task_manager._notify_push(task)
        assert len(captured) == 1
        assert set(captured[0]) == {"statusUpdate"}
        assert captured[0]["statusUpdate"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
