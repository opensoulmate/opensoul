"""F4b-⑯c a2a 流帧序 v1.0 修齐——SendStreamingMessage 首帧 Task 对象（§3.1.2）。

修前缺口（F4b-⑯b 留档候选"F4b-⑯c 流帧序 v1.0 修齐"）：SendStreamingMessage
现行首帧=statusUpdate 无首帧 Task 对象，而 §3.1.2 Task lifecycle stream 明文
「the stream MUST begin with the Task object, followed by zero or more
TaskStatusUpdateEvent or TaskArtifactUpdateEvent objects」——v1.0 客户端
（a2a-python base_client 对 first_event 特判 Task/Message）按规范期待首帧 Task。
SubscribeToTask 已合规（send_current 首帧快照）零变化。

收口：SendStreamingMessage 订阅即入队订阅时点全量快照作首帧（first_frame_ends
=False——首帧恒信息帧，收流仍由处理的终态收尾快照驱动，禁提前收流）。
帧序文法（§3.1.2 + Outputs「Final completion indicator」）：
[Task 首帧] [零或多 statusUpdate/artifactUpdate] [终态 Task 快照] 收流。

真栈非 mock：直驱真 ASGI FastAPI 路由层 + 双向门闩（test_a2a_streaming_methods
同款 harness）。
"""

import asyncio
import json

import pytest
from fastapi import FastAPI

from src.a2a.api import router as a2a_router
from src.a2a.task_manager import task_manager

STUB = {"llm_mode": "stub"}
_END = object()

_TERMINAL = (
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(a2a_router)
    return app


def _msg(text="hello frame-order", **extra):
    m = {"role": "ROLE_USER", "parts": [{"text": text}]}
    m.update(extra)
    return m


def _rpc(method, params, rid="fo-rpc", with_id=True):
    payload = {"jsonrpc": "2.0", "method": method, "params": params}
    if with_id:
        payload["id"] = rid
    return payload


def _kind(env: dict) -> str:
    return next(iter(env["result"]))


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
            "client": ("127.0.0.1", 50002),
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


def _assert_envelope(env: dict, rid):
    """帧包络逐键（§5/§9.4.2）+ result 恰一键 StreamResponse。"""
    assert env["jsonrpc"] == "2.0"
    assert env["id"] == rid
    assert "error" not in env
    result = env["result"]
    assert len(result) == 1, f"StreamResponse 必须恰一键 oneof 成员：{result}"
    return result


def _assert_task_lifecycle_grammar(frames, rid):
    """§3.1.2 Task lifecycle stream 文法：首帧 Task → 中段只许事件 → 终态 Task 收流。

    - MUST begin with the Task object（修前缺首帧=违 MUST，控制组必炸）
    - 「followed by zero or more TaskStatusUpdateEvent or TaskArtifactUpdateEvent」
      ——中段禁再出现 Task/Message
    - 「MUST close when the task reaches a terminal state」——末帧=终态 Task
      快照（Outputs「Final completion indicator」）后收流
    """
    assert frames, "流无帧"
    first = _assert_envelope(frames[0], rid)
    assert set(first) == {"task"}, f"首帧必须是 Task 对象：{first}"
    for env in frames[1:-1]:
        mid = _assert_envelope(env, rid)
        assert next(iter(mid)) in ("statusUpdate", "artifactUpdate"), f"中段只许事件：{mid}"
    last = _assert_envelope(frames[-1], rid)
    assert set(last) == {"task"}, f"末帧=终态 Task 快照：{last}"
    assert last["task"]["status"]["state"] in _TERMINAL
    # 保真：全帧同任务
    task_id = first["task"]["id"]
    assert last["task"]["id"] == task_id
    return first["task"], last["task"]


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


class TestSendStreamingFrameOrder:
    """SendStreamingMessage 帧序 §3.1.2 修齐（本拍本体）。"""

    @pytest.mark.asyncio
    async def test_new_task_stream_must_begin_with_task_object(self):
        app = _make_app()
        c = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("fo-new"), "metadata": dict(STUB)},
                rid="fo-1",
            )
        ).collect(app)
        frames = c.frame_list
        assert c.status == 200
        assert c.headers["content-type"].startswith("text/event-stream")
        first_task, final_task = _assert_task_lifecycle_grammar(frames, "fo-1")
        # 首帧=订阅时点真态：create_task 已落首条消息（state=submitted、history=[user]）
        assert first_task["status"]["state"] == "TASK_STATE_SUBMITTED"
        assert [m["role"] for m in first_task["history"]] == ["ROLE_USER"]
        # 中段恰 working + 终态两条迁移（stub 零分块）
        kinds = [_kind(f) for f in frames[1:-1]]
        assert kinds == ["statusUpdate", "statusUpdate"]
        assert frames[1]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
        assert frames[2]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
        # 终稿快照照旧（Final completion indicator 契约零变化）
        assert [m["role"] for m in final_task["history"]] == ["ROLE_USER", "ROLE_AGENT"]

    @pytest.mark.asyncio
    async def test_continuation_first_frame_is_subscription_truth(self):
        """续接首帧=订阅时点真态（新消息入史在订阅后=「at the time of subscription」
        字面语义），终稿快照含全史 4 条——首帧/终稿各自如实，禁 fabricate。"""
        app = _make_app()
        c1 = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("turn-1"), "metadata": dict(STUB)},
                rid="fo-2a",
            )
        ).collect(app)
        task_id = c1.frame_list[0]["result"]["task"]["id"]

        c2 = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("turn-2", taskId=task_id), "metadata": dict(STUB)},
                rid="fo-2b",
            )
        ).collect(app)
        frames = c2.frame_list
        first_task, final_task = _assert_task_lifecycle_grammar(frames, "fo-2b")
        assert first_task["id"] == task_id  # 续接同任务
        # 订阅时点真态：前轮已终态（completed）+ 新消息尚未入史（history 恰 2 条）
        assert first_task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert [m["role"] for m in first_task["history"]] == ["ROLE_USER", "ROLE_AGENT"]
        # 终稿快照含新消息（history 恰 4 条，F4b-⑧ 契约零变化）
        assert [m["role"] for m in final_task["history"]] == [
            "ROLE_USER",
            "ROLE_AGENT",
            "ROLE_USER",
            "ROLE_AGENT",
        ]

    @pytest.mark.asyncio
    async def test_history_length_covers_first_frame(self):
        """§3.1.3 historyLength 对流内每个 Task 帧生效（MUST NOT 超发）：
        首帧快照同样收口——修前只有收尾快照收口。"""
        app = _make_app()
        c1 = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {"message": _msg("hl-1"), "metadata": dict(STUB)},
                rid="fo-3a",
            )
        ).collect(app)
        task_id = c1.frame_list[0]["result"]["task"]["id"]

        c2 = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {
                    "message": _msg("hl-2", taskId=task_id),
                    "metadata": dict(STUB),
                    "configuration": {"historyLength": 1},
                },
                rid="fo-3b",
            )
        ).collect(app)
        first_task, final_task = _assert_task_lifecycle_grammar(c2.frame_list, "fo-3b")
        assert len(first_task["history"]) == 1  # 首帧同样不超发
        assert first_task["history"][0]["role"] == "ROLE_AGENT"  # 最近 N=全量后缀
        assert len(final_task["history"]) == 1

        c3 = await _SseClient(
            _rpc(
                "SendStreamingMessage",
                {
                    "message": _msg("hl-3", taskId=task_id),
                    "metadata": dict(STUB),
                    "configuration": {"historyLength": 0},
                },
                rid="fo-3c",
            )
        ).collect(app)
        first0, final0 = _assert_task_lifecycle_grammar(c3.frame_list, "fo-3c")
        assert "history" not in first0  # 0=省略 history 键（首帧同收口）
        assert "history" not in final0

    @pytest.mark.asyncio
    async def test_first_frame_arrives_before_processing_completes(self, monkeypatch):
        """首帧=订阅时点交付而非收尾搭车：处理被门闩挂住时首帧已到达（真增量），
        且首帧是 Task 快照而非收尾快照（state 非终态、处理未完成）。"""
        gate = _Gate()
        gate.install(monkeypatch)
        app = _make_app()
        c = _SseClient(
            _rpc("SendStreamingMessage", {"message": _msg(), "metadata": dict(STUB)}, rid="fo-4")
        ).start(app)
        first = await c.next_frame(timeout=6)
        result = _assert_envelope(first, "fo-4")
        assert set(result) == {"task"}  # 首帧 Task 对象（修前首帧=statusUpdate 必炸）
        assert result["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
        assert not gate.finished  # 处理仍未完成 = 首帧确系订阅时点交付

        gate.release.set()
        frames = [first]
        while True:
            item = await c.next_frame(timeout=8)
            if item is _END:
                break
            frames.append(item)
        _assert_task_lifecycle_grammar(frames, "fo-4")
        assert gate.finished


class TestSubscribeFaceUnchanged:
    """SubscribeToTask 已合规面零变化（修齐只动 SendStreamingMessage）。"""

    @pytest.mark.asyncio
    async def test_subscribe_terminal_single_snapshot_then_close(self):
        app = _make_app()
        c = await _SseClient(
            _rpc("SendStreamingMessage", {"message": _msg(), "metadata": dict(STUB)}, rid="fo-5a")
        ).collect(app)
        task_id = c.frame_list[-1]["result"]["task"]["id"]

        sub = await _SseClient(_rpc("SubscribeToTask", {"id": task_id}, rid="fo-5b")).collect(app)
        assert len(sub.frame_list) == 1  # 已终态：恰一帧即收流（修前修后同形）
        result = _assert_envelope(sub.frame_list[0], "fo-5b")
        assert set(result) == {"task"}
        assert result["task"]["status"]["state"] == "TASK_STATE_COMPLETED"

    @pytest.mark.asyncio
    async def test_subscribe_midrun_first_frame_task_then_events(self, monkeypatch):
        gate = _Gate()
        gate.install(monkeypatch)
        app = _make_app()
        sender = _SseClient(
            _rpc("SendStreamingMessage", {"message": _msg(), "metadata": dict(STUB)}, rid="fo-6a")
        ).start(app)
        first = await sender.next_frame(timeout=6)  # 首帧=Task 快照
        task_id = first["result"]["task"]["id"]
        working = await sender.next_frame(timeout=6)  # working 帧=处理真在跑
        assert working["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"

        sub = _SseClient(_rpc("SubscribeToTask", {"id": task_id}, rid="fo-6b")).start(app)
        s_first = await sub.next_frame(timeout=6)
        assert set(s_first["result"]) == {"task"}  # §3.1.6 首帧=订阅时点快照
        assert s_first["result"]["task"]["status"]["state"] == "TASK_STATE_WORKING"
        gate.release.set()

        while True:
            item = await sub.next_frame(timeout=8)
            if item is _END:
                break
        tail = sub.frame_list[1:]
        kinds = [_kind(f) for f in tail]
        assert kinds == ["statusUpdate", "task"]  # 终态迁移 + 收尾快照（零变化）
        await asyncio.wait_for(sender.done.wait(), timeout=10)
