"""F4b-⑯ a2a 真流式两方法 v1.0——SendStreamingMessage / SubscribeToTask（JSON-RPC-over-SSE）。

修前缺口（F4b-⑮ 留档"SubscribeToTask/SendStreamingMessage 真流式留待拍"）：
两方法未实现（错误面已随 §3.3.4 如实 -32004），而卡面 protocolVersion 声明 1.0.0
——v1.0 §5.3 方法映射表最后一块空白。

收口（JSON-RPC-over-SSE 传输层 + 任务增量事件发射）：
- §9.4.2：HTTP 200 text/event-stream，每个 SSE data 帧 = 一个 JSON-RPC 2.0 响应
  包络（id 保真回显），result = StreamResponse 单键（{"statusUpdate"}|{"task"}，
  判别式=oneof 成员名本身，whats-new-v1.md）。
- v1.0 无 final 字段（0.x 已移除）：终态由**收流**表示——帧序 = 状态迁移事件 +
  终态全量快照后收流。
- 事件总线：状态迁移逐帧推（working/终态），多并发流同收同序事件。

真栈非 mock：直驱真 ASGI FastAPI 路由层（test_llm_completions_stream 同款）+ 双向
门闩硬证明真增量交付（缓冲即超时失败，控制组语义内建）。
"""

import asyncio
import json

import pytest
from fastapi import FastAPI

from src.a2a import api as a2a_api
from src.a2a.api import router as a2a_router
from src.a2a.task_manager import task_manager

STUB = {"llm_mode": "stub"}
_END = object()  # 收流哨兵（区别于任何真实帧）


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(a2a_router)
    return app


def _msg(text="hello stream", **extra):
    # v1.0 平铺 part 形（{"text": ...}，判别式=成员名本身）+ ROLE_* 输入形
    m = {"role": "ROLE_USER", "parts": [{"text": text}]}
    m.update(extra)
    return m


def _rpc(method, params, rid="s-rpc", with_id=True):
    payload = {"jsonrpc": "2.0", "method": method, "params": params}
    if with_id:
        payload["id"] = rid
    return payload


class _SseClient:
    """直驱 ASGI 的单请求客户端：SSE 逐帧解码（增量进 queue + 全量进 frame_list）。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.queue: asyncio.Queue = asyncio.Queue()  # 增量消费口（收流置 _END）
        self.frame_list: list = []  # 全量帧（不含哨兵）
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
            "client": ("127.0.0.1", 50001),
            "server": ("testserver", 80),
        }
        state = {"sent": False}

        async def receive():
            if not state["sent"]:
                state["sent"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(3600)  # 不再给 disconnect；流收束即返回
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
        """增量下一帧（_END=收流）；超时如实抛（缓冲/伪流式实现下门闩用例必炸）。"""
        return await asyncio.wait_for(self.queue.get(), timeout=timeout)

    async def collect(self, app, timeout=15.0):
        """跑完整个请求后返回 self（frame_list=全量帧 / json_body()=JSON 响应）。"""
        self.start(app)
        await asyncio.wait_for(self.done.wait(), timeout=timeout)
        return self

    def json_body(self):
        return json.loads(self.body.decode("utf-8"))


def _assert_v10_frame(env: dict, rid):
    """帧包络逐键（§5/§9.4.2）：jsonrpc+id 保真回显+result 单键 StreamResponse。"""
    assert env["jsonrpc"] == "2.0"
    assert env["id"] == rid
    assert "error" not in env
    result = env["result"]
    assert len(result) == 1, f"StreamResponse 必须恰一键 oneof 成员：{result}"
    assert next(iter(result)) in ("statusUpdate", "artifactUpdate", "task", "message")
    return result


def _assert_no_deprecated_keys(obj):
    """v1.0 禁 0.x 残留：无 kind 判别键、无 final 字段（whats-new-v1.md BREAKING）。"""
    if isinstance(obj, dict):
        assert "kind" not in obj, f"0.x kind 判别键残留：{obj}"
        assert "final" not in obj, f"0.x final 字段残留（v1.0 由收流表示终态）：{obj}"
        for v in obj.values():
            _assert_no_deprecated_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_deprecated_keys(v)


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
    """全局 TaskManager 隔离：任务/推送配置/投递留痕/流订阅表清空，事后逐字还原。

    _stream_subs 走 getattr 容缺（控制组旧代码无此属性时本 fixture 仍可跑，
    让控制组失败暴露在行为断言上而非 fixture 自身）。
    """
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


def _frame_kinds(frames):
    return [next(iter(f["result"])) for f in frames]


class TestSendStreamingFrameFace:
    """SendStreamingMessage：帧包络/帧序/终态收流（§9.4.2）。"""

    @pytest.mark.asyncio
    async def test_frame_envelope_order_and_v10_shape(self):
        app = _make_app()
        c = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("hello f-1"), "metadata": dict(STUB)},
                rid="f-1",
            )
        ).collect(app)
        frames = c.frame_list

        assert c.status == 200
        assert c.headers["content-type"].startswith("text/event-stream")
        # 帧序 = working 迁移 → 终态迁移 → 全量快照（收流）
        assert len(frames) == 3
        r0 = _assert_v10_frame(frames[0], "f-1")
        r1 = _assert_v10_frame(frames[1], "f-1")
        r2 = _assert_v10_frame(frames[2], "f-1")
        assert set(r0) == {"statusUpdate"}
        assert set(r1) == {"statusUpdate"}
        assert set(r2) == {"task"}
        assert set(r0["statusUpdate"]) == {"taskId", "contextId", "status"}
        assert r0["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
        assert r1["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
        snap = r2["task"]
        assert snap["status"]["state"] == "TASK_STATE_COMPLETED"
        # 消息历史 [user, agent] 且枚举/平铺 part 全 v1.0 形
        assert [m["role"] for m in snap["history"]] == ["ROLE_USER", "ROLE_AGENT"]
        assert all(set(p) == {"text"} for m in snap["history"] for p in m["parts"])
        for f in frames:
            _assert_no_deprecated_keys(f)

    @pytest.mark.asyncio
    async def test_snapshot_matches_get_task_truth(self):
        app = _make_app()
        c = await _SseClient(
            _rpc("SendStreamingMessage", {"message": _msg(), "metadata": dict(STUB)}, rid="f-2")
        ).collect(app)
        snap = c.frame_list[-1]["result"]["task"]

        got = await _SseClient(_rpc("GetTask", {"id": snap["id"]}, rid="f-2g")).collect(app)
        got_env = got.json_body()
        assert got_env["id"] == "f-2g"
        assert got_env["result"] == snap  # 收尾快照与真源逐键一致

    @pytest.mark.asyncio
    async def test_history_length_on_stream_snapshot(self):
        app = _make_app()
        params = {
            "message": _msg(),
            "metadata": dict(STUB),
            "configuration": {"historyLength": 1},
        }
        c = await _SseClient(_rpc("SendStreamingMessage", params, rid="f-3")).collect(app)
        snap = c.frame_list[-1]["result"]["task"]
        assert len(snap["history"]) == 1
        assert snap["history"][0]["role"] == "ROLE_AGENT"  # 最近 N 条=全量后缀

        params0 = {
            "message": _msg(),
            "metadata": dict(STUB),
            "configuration": {"historyLength": 0},
        }
        c0 = await _SseClient(_rpc("SendStreamingMessage", params0, rid="f-3b")).collect(app)
        assert "history" not in c0.frame_list[-1]["result"]["task"]  # 0=省略 history 键

    @pytest.mark.asyncio
    async def test_invalid_history_length_rejected_before_stream(self):
        """开流前校验：非法 historyLength 如实 -32602（JSON 错误响应，禁伪 SSE）。"""
        app = _make_app()
        c = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {
                    "message": _msg(),
                    "metadata": dict(STUB),
                    "configuration": {"historyLength": -1},
                },
                rid="f-4",
            )
        ).collect(app)
        assert c.status == 200
        assert not c.headers["content-type"].startswith("text/event-stream")
        env = c.json_body()
        assert env["error"]["code"] == -32602
        assert "historyLength" in env["error"]["message"]
        assert env["id"] == "f-4"


class TestTrueIncrementalDelivery:
    """真增量交付（双向门闩硬证明）：working 帧在处理完成前就到达客户端。"""

    @pytest.mark.asyncio
    async def test_working_frame_arrives_before_processing_completes(self, monkeypatch):
        gate = _Gate()
        gate.install(monkeypatch)
        app = _make_app()
        c = _SseClient(
            _rpc("SendStreamingMessage", {"message": _msg(), "metadata": dict(STUB)}, rid="inc-1")
        ).start(app)

        # 门闩：处理被挂住时首帧（working）必须已经到达——缓冲/伪流式实现下
        # 首帧永不出现，wait_for 超时即失败（控制组语义内建）
        first = await c.next_frame(timeout=6)
        _assert_v10_frame(first, "inc-1")
        assert first["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
        assert gate.entered.is_set()
        assert not gate.finished  # 处理仍未完成 = 首帧确系增量交付

        gate.release.set()
        frames = [first]
        while True:
            item = await c.next_frame(timeout=8)
            if item is _END:
                break
            frames.append(item)
        assert _frame_kinds(frames) == ["statusUpdate", "statusUpdate", "task"]
        assert gate.finished


class TestSubscribeToTask:
    """SubscribeToTask：订阅既有任务更新流（§9.4.6，同 SSE 形）。"""

    @pytest.mark.asyncio
    async def test_two_subscribers_same_ordered_events_with_sender(self, monkeypatch):
        gate = _Gate()
        gate.install(monkeypatch)
        app = _make_app()
        sender = _SseClient(
            _rpc("SendStreamingMessage", {"message": _msg(), "metadata": dict(STUB)}, rid="sub-a")
        ).start(app)
        first = await sender.next_frame(timeout=6)  # working 帧=处理真在跑
        task_id = first["result"]["statusUpdate"]["taskId"]

        sub_b = _SseClient(_rpc("SubscribeToTask", {"id": task_id}, rid="sub-b")).start(app)
        sub_c = _SseClient(_rpc("SubscribeToTask", {"id": task_id}, rid="sub-c")).start(app)
        b_first = await sub_b.next_frame(timeout=6)  # 首帧=当前快照（working，非终态不收流）
        c_first = await sub_c.next_frame(timeout=6)
        gate.release.set()

        a = [first] + await _all_frames(sender)  # 首帧已消费，并回全序
        b = [b_first] + await _all_frames(sub_b)  # 同上：订阅时点快照并回
        c = [c_first] + await _all_frames(sub_c)
        # 发送方：working 迁移 + 终态迁移 + 快照
        assert _frame_kinds(a) == ["statusUpdate", "statusUpdate", "task"]
        # 两订阅方：同序同形（v1.0「all receive same ordered events」）——
        # 首帧为订阅时点快照，随后与发送方同批事件
        assert _frame_kinds(b) == ["task", "statusUpdate", "task"]
        assert [f["result"] for f in b] == [f["result"] for f in c]
        assert b[0]["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
        assert b[1]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
        assert b[2]["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"

    @pytest.mark.asyncio
    async def test_subscribe_terminal_task_single_snapshot_then_close(self):
        app = _make_app()
        c = await _SseClient(
            _rpc("SendStreamingMessage", {"message": _msg(), "metadata": dict(STUB)}, rid="sub-d")
        ).collect(app)
        task_id = c.frame_list[-1]["result"]["task"]["id"]

        sub = await _SseClient(_rpc("SubscribeToTask", {"id": task_id}, rid="sub-e")).collect(app)
        # 已终态：恰一条全量快照（终态在快照 status.state 里如实）即收流
        assert len(sub.frame_list) == 1
        result = _assert_v10_frame(sub.frame_list[0], "sub-e")
        assert set(result) == {"task"}
        assert result["task"]["status"]["state"] == "TASK_STATE_COMPLETED"

    @pytest.mark.asyncio
    async def test_subscribe_errors_are_json_not_sse(self):
        app = _make_app()
        c1 = await _SseClient(_rpc("SubscribeToTask", {}, rid="sub-f")).collect(app)
        assert c1.json_body()["error"]["code"] == -32602
        c2 = await _SseClient(_rpc("SubscribeToTask", {"id": "no-such-task"}, rid="sub-g")).collect(
            app
        )
        env = c2.json_body()
        assert env["error"]["code"] == -32001
        assert "no-such-task" in env["error"]["message"]
        assert not c2.headers["content-type"].startswith("text/event-stream")


class TestSendStreamingEdges:
    """续接/Notification/旧线名边界。"""

    @pytest.mark.asyncio
    async def test_continuation_via_message_task_id(self):
        app = _make_app()
        c = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("turn-1"), "metadata": dict(STUB)},
                rid="ct-1",
            )
        ).collect(app)
        task_id = c.frame_list[-1]["result"]["task"]["id"]

        c2 = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("turn-2", taskId=task_id), "metadata": dict(STUB)},
                rid="ct-2",
            )
        ).collect(app)
        snap = c2.frame_list[-1]["result"]["task"]
        assert snap["id"] == task_id  # 续接同任务，非新建
        assert [m["role"] for m in snap["history"]] == [
            "ROLE_USER",
            "ROLE_AGENT",
            "ROLE_USER",
            "ROLE_AGENT",
        ]

    @pytest.mark.asyncio
    async def test_continuation_missing_task_error_before_stream(self):
        app = _make_app()
        c = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg(taskId="no-such-task"), "metadata": dict(STUB)},
                rid="ct-3",
            )
        ).collect(app)
        env = c.json_body()
        assert env["error"]["code"] == -32001
        assert not c.headers["content-type"].startswith("text/event-stream")

    @pytest.mark.asyncio
    async def test_notification_executes_without_reply(self):
        """§5 MUST NOT reply to a Notification：无 id → 204 空体，但照常执行。"""
        app = _make_app()
        c = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("notify-me"), "metadata": dict(STUB)},
                with_id=False,
            )
        ).collect(app)
        assert c.status == 204
        assert c.body == b""

        # fire-and-forget 但真执行：任务在后台跑完（stub 确定性应答）
        for _ in range(200):
            listed = await _SseClient(_rpc("ListTasks", {}, rid="nt-1")).collect(app)
            tasks = listed.json_body()["result"]["tasks"]
            if tasks and tasks[0]["status"]["state"] == "TASK_STATE_COMPLETED":
                return
            await asyncio.sleep(0.02)
        raise AssertionError("Notification 任务未执行完成（fire-and-forget 断点）")

    @pytest.mark.asyncio
    async def test_legacy_stream_names_stay_method_not_found(self):
        """0.x 旧线名（message/stream、tasks/sendSubscribe）本实现从未有过——
        不收（F4b-⑮ 同纪律），如实 -32601；能力门不再拦（streaming 已真支持）。"""
        app = _make_app()
        for name in ("message/stream", "tasks/sendSubscribe"):
            c = await _SseClient(_rpc(name, {"message": _msg()}, rid="lg-1")).collect(app)
            env = c.json_body()
            assert env["error"]["code"] == -32601, name


class TestCancelDuringSubscription:
    """取消=交互周期收束：订阅方收到 canceled 迁移+快照后收流。"""

    @pytest.mark.asyncio
    async def test_cancel_closes_subscriber_stream(self, monkeypatch):
        gate = _Gate()
        gate.install(monkeypatch)
        app = _make_app()
        sender = _SseClient(
            _rpc("SendStreamingMessage", {"message": _msg(), "metadata": dict(STUB)}, rid="cx-1")
        ).start(app)
        first = await sender.next_frame(timeout=6)  # working 帧=处理真在跑
        task_id = first["result"]["statusUpdate"]["taskId"]

        sub = _SseClient(_rpc("SubscribeToTask", {"id": task_id}, rid="cx-2")).start(app)
        await sub.next_frame(timeout=6)  # 订阅时点快照

        cancelled = await _SseClient(_rpc("CancelTask", {"id": task_id}, rid="cx-3")).collect(app)
        assert cancelled.json_body()["result"]["status"]["state"] == "TASK_STATE_CANCELED"

        b = await _all_frames(sub, timeout=8)
        assert _frame_kinds(b) == ["statusUpdate", "task"]
        assert b[0]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_CANCELED"
        assert b[1]["result"]["task"]["status"]["state"] == "TASK_STATE_CANCELED"

        gate.release.set()  # 放行挂住的处理，别把 25s 预算挂到测试结束
        await asyncio.wait_for(sender.done.wait(), timeout=10)


class TestCapabilityDerivation:
    """声明从实现派生（F4b-⑤）：真注册流式方法后 streaming 自动 True（禁漂移）。"""

    def test_streaming_capability_true_matches_derivation(self):
        assert a2a_api.streaming_supported() is True
        assert "SendStreamingMessage" in a2a_api.RPC_HANDLERS
        assert "SubscribeToTask" in a2a_api.RPC_HANDLERS
        # 发布值恒等于派生值（发布口 agent_card 与本对账同一套派生函数）
        card = _published_card()
        assert card["capabilities"]["streaming"] is True
        assert card["capabilities"]["streaming"] == a2a_api.streaming_supported()


def _published_card() -> dict:
    card = task_manager.get_agent_card().model_copy(deep=True)
    card.capabilities.streaming = a2a_api.streaming_supported()
    card.capabilities.pushNotifications = a2a_api.push_notifications_supported()
    card.capabilities.extendedAgentCard = a2a_api.extended_agent_card_supported()
    return card.published()


async def _all_frames(client: _SseClient, timeout=10.0) -> list:
    """读到收流为止（不含哨兵）。"""
    out = []
    while True:
        item = await client.next_frame(timeout=timeout)
        if item is _END:
            return out
        out.append(item)
