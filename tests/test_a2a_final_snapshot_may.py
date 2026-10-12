"""F4b-⑯f a2a 收尾快照两读判定——契约锁定（判定即切片本体，零行为改动）。

判定问题（F4b-⑯c 诚实边界①留档）：SendStreamingMessage/SubscribeToTask 的收尾
Task 快照是否违反 §3.1.2 pattern 2 字面文法（「the stream MUST begin with the
Task object, followed by zero or more TaskStatusUpdateEvent or
TaskArtifactUpdateEvent objects」）——严格读=事件序列后不许 Task 帧，收尾快照
须移除？

判定=保留（合规），两读归一（同规范 §11.7 明文豁免）：
- §11.7 Streaming 流模型：「Streaming responses are simple, linearly ordered
  sequences: first a Task (or single Message), then zero or more status or
  artifact update events until the task reaches a terminal or interrupted
  state, at which point the stream closes. Implementations SHOULD avoid
  re-ordering events and MAY optionally resend a final Task snapshot before
  closing.」——收尾快照是规范明文许可的可选帧（MAY 行使），pattern 2 描述
  必需骨架，MAY 句是同一规范对收尾快照的显式豁免；跨绑定适用依据=§5.1
  Functional Equivalence（各绑定 MUST 功能等价）+§9.4.2 帧形委托 StreamResponse
  无相反规则。
- 「Final completion indicator」（§3.1.2 Outputs 并列 bullet）=收流本身，非
  收尾快照——whats-new-v1.md：final 布尔已移除，「Leverage protocol binding
  specific stream closure mechanism instead」。F4b-⑯c 把它归因到收尾快照，本拍
  勘误（行为不变，归因改对）。
- a2a-python 参考实现：TaskUpdater.complete() 只发终态 TaskStatusUpdateEvent
  （SDK 默认不发收尾快照=MAY 未行使，同样合规）；EventConsumer.consume_all 以
  终态/中断态事件（Task|TaskStatusUpdateEvent，含 INPUT_REQUIRED）为 final event
  收流——旧记法「以终态 Task 为 final event」不精确，一并勘误。

本文件锁定判定为契约（双向锁死）：收尾快照在场（本实现行使 MAY）=末帧+其后
立即收流、=GetTask 真源禁 fabricate、全帧零 in-band 完成标志（final/kind）、
中段零 Task 帧、已终态订阅恰一帧即收流、收流后零订阅泄漏。若未来按"移除"读法
改动（同样合规）须先改本文件契约；若快照被 fabricate/悬挂/混入 in-band 标志
必炸。

真栈非 mock：直驱真 ASGI FastAPI 路由层（test_a2a_stream_frame_order 同款
harness），stub 处理零 LLM。
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


def _msg(text="hello final-snapshot-may", **extra):
    m = {"role": "ROLE_USER", "parts": [{"text": text}]}
    m.update(extra)
    return m


def _rpc(method, params, rid, with_id=True):
    payload = {"jsonrpc": "2.0", "method": method, "params": params}
    if with_id:
        payload["id"] = rid
    return payload


def _kind(env: dict) -> str:
    return next(iter(env["result"]))


def _walk_keys(obj):
    """深扫 dict/list 全部键（in-band 完成标志排查用）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


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
            "client": ("127.0.0.1", 50012),
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


async def _run_stream(app, text, rid):
    """SendStreamingMessage stub 一轮，返回收集完的客户端。"""
    return await _SseClient(
        _rpc("SendStreamingMessage", {"message": _msg(text), "metadata": dict(STUB)}, rid=rid)
    ).collect(app)


class TestFinalSnapshotMay:
    """收尾快照两读判定=保留（§11.7 MAY）的契约锁定。"""

    @pytest.mark.asyncio
    async def test_may_final_snapshot_is_last_frame_then_close(self):
        """帧序文法精确：[Task 首帧][statusUpdate×2][终态 Task 快照]→立即收流。

        收尾快照（本实现行使 §11.7 MAY）在场、恰为末帧、其后零帧悬挂。"""
        app = _make_app()
        c = await _run_stream(app, "may-1", "may-1")
        assert c.status == 200
        assert c.headers["content-type"].startswith("text/event-stream")
        frames = c.frame_list
        # 恰 4 帧：首帧 Task + working 迁移 + 终态迁移 + 收尾快照（stub 零分块）
        assert [_kind(f) for f in frames] == ["task", "statusUpdate", "statusUpdate", "task"]
        first = frames[0]["result"]["task"]
        final = frames[-1]["result"]["task"]
        # 首帧 MUST Task 对象（§3.1.2）；收尾快照=终态 Task
        assert first["status"]["state"] == "TASK_STATE_SUBMITTED"
        assert final["status"]["state"] in _TERMINAL
        assert final["id"] == first["id"]
        # 收流=紧随末帧（done 已置=ASGI 响应真终止，无第 5 帧）
        assert c.done.is_set()
        assert len(frames) == 4

    @pytest.mark.asyncio
    async def test_final_snapshot_equals_gettask_truth(self):
        """收尾快照=GetTask 真源逐键一致（禁 fabricate）；收流后真源仍可取。"""
        app = _make_app()
        c = await _run_stream(app, "may-2", "may-2")
        final = c.frame_list[-1]["result"]["task"]
        task_id = final["id"]
        g = await _SseClient(_rpc("GetTask", {"id": task_id}, rid="may-2-get")).collect(app)
        got = g.json_body()["result"]
        assert got == final, "收尾快照必须与 GetTask 真源逐键一致"

    @pytest.mark.asyncio
    async def test_completion_indicator_is_closure_not_frame_member(self):
        """「Final completion indicator」=收流本身：全帧零 in-band 完成标志。

        v1.0 无 final 字段（whats-new-v1 已移除）、无 kind 判别键（F4b-⑭）——
        完成语义只由收流表达，任何帧内完成标志=与 v1.0 漂移。"""
        app = _make_app()
        c = await _run_stream(app, "may-3", "may-3")
        assert c.done.is_set()  # 收流真发生=完成指示
        for env in c.frame_list:
            keys = set(_walk_keys(env))
            assert "final" not in keys, f"in-band 完成标志 final 复活：{env}"
            assert "kind" not in keys, f"0.x 判别键 kind 复活：{env}"
            # 包络成员互斥（F4b-⑩ envelope 契约）：恰 jsonrpc/id/result
            assert set(env.keys()) == {"jsonrpc", "id", "result"}
            assert env["id"] == "may-3"

    @pytest.mark.asyncio
    async def test_middle_frames_contain_no_task_object(self):
        """中段只许事件（§3.1.2「zero or more TaskStatusUpdateEvent or
        TaskArtifactUpdateEvent」）：Task 帧只许出现在首帧与 MAY 收尾快照两处。"""
        app = _make_app()
        c = await _run_stream(app, "may-4", "may-4")
        frames = c.frame_list
        assert _kind(frames[0]) == "task"
        assert _kind(frames[-1]) == "task"
        for env in frames[1:-1]:
            assert _kind(env) in ("statusUpdate", "artifactUpdate"), f"中段混入 Task/Message：{env}"

    @pytest.mark.asyncio
    async def test_subscribe_terminal_task_single_snapshot_then_close(self):
        """已终态任务 SubscribeToTask：恰一帧（Task 快照=订阅时点真态）即收流。

        §3.1.6「MUST return a Task object as the first event ... at the time of
        subscription」+终态即收流——首帧快照本身就是 §11.7 收流前的最后快照，
        禁再追加第二帧。"""
        app = _make_app()
        c0 = await _run_stream(app, "may-5", "may-5a")
        task_id = c0.frame_list[-1]["result"]["task"]["id"]
        c = await _SseClient(_rpc("SubscribeToTask", {"id": task_id}, rid="may-5b")).collect(app)
        frames = c.frame_list
        assert [_kind(f) for f in frames] == ["task"]
        snap = frames[0]["result"]["task"]
        assert snap["id"] == task_id
        assert snap["status"]["state"] in _TERMINAL
        assert c.done.is_set()

    @pytest.mark.asyncio
    async def test_close_unsubscribes_no_leak(self):
        """收流即退订：流订阅表零残留（收流语义=交互周期收束，禁悬挂订阅）。"""
        app = _make_app()
        c = await _run_stream(app, "may-6", "may-6")
        task_id = c.frame_list[-1]["result"]["task"]["id"]
        assert c.done.is_set()
        assert task_manager._stream_subs.get(task_id) in (None, [])
