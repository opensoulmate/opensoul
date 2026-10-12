"""F4b-⑲ a2a 预算超时态语义收口——超时=failed 可见，禁 fabricated completed。

修前缺口（F4b-⑱ 留档⑤"25s 超时降级文本仍标 completed（完成态语义存疑，F1 同型
'谎报 ok'候选）"）：
① run_processing 25s 总预算耗尽时把"AI处理超时…请稍后重试"降级文本当成功回复
   入史并标 completed——机器可读面谎报成功（客户端/看板按 state 统计=成功任务）。
② 超时=asyncio 取消 _process_message（CancelledError 不经 _handle_chat 的
   except Exception）——已发分块流不收尾（artifact 无 lastChunk 收尾，客户端
   悬挂面；LLMStreamError docstring"只适用于零分块路径"的声明此前无结构保证）。

收口：
- 预算耗尽 → failed 可见：state=failed、超时说明进 status.message 不进 history
  （F4b-⑧"错误不入史"）；预算单一源 TaskManager.PROCESS_BUDGET_S（类属性）。
- 已发块不悬挂：_handle_chat finally 收尾（committed 才收——发块前失败
  "无痕可撤/pending 块不出"语义零变化；成功/LLMStreamError 路径幂等 no-op）。

真栈非 mock：真 socket 上游桩（挂死/发零块挂死/发块后挂死/正常回）+ 直驱真
ASGI + 真实 asyncio.wait_for 计时（预算短缩非 mock 时间）。
"""

import asyncio
import json

import pytest
from fastapi import FastAPI

from src.a2a.api import router as a2a_router
from src.a2a.task_manager import TaskManager, task_manager

_END = object()  # 收流哨兵


def _msg(text="hello timeout", **extra):
    m = {"role": "ROLE_USER", "parts": [{"text": text}], "messageId": f"to-{text}"}
    m.update(extra)
    return m


def _rpc(method, params, rid="timeout-rpc"):
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


def _frame_kind(env: dict) -> str:
    return next(iter(env["result"]))


def _art(env: dict) -> dict:
    return env["result"]["artifactUpdate"]


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(a2a_router)
    return app


def _patch_llm(monkeypatch, base_url: str):
    """_handle_chat 走 settings.llm_* 取配置——指到本地桩（生产读法零改动）。"""
    from src.config import settings

    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_base_url", base_url)
    monkeypatch.setattr(settings, "llm_model", "stub-model")


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


async def _json_call(app, payload: dict):
    """直驱 ASGI 发普通 JSON-RPC（非流式面）。"""
    client = _SseClient(payload)
    await client._run(app)
    assert client.body, "empty JSON response body"
    return json.loads(client.body)


async def _start_stub(responder):
    """真 socket 上游桩。responder(req_body: dict, writer) 写完整 HTTP 响应。"""
    received: dict = {"bodies": [], "raw": []}

    async def _handle(reader, writer):
        try:
            req = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in req.decode("latin-1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1])
            body = json.loads((await reader.readexactly(length)).decode())
            received["bodies"].append(body)
            received["raw"].append(req.decode("latin-1"))
            await responder(body, writer)
            await writer.drain()
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}", received


def _sse(delta: str) -> bytes:
    obj = {"id": "cmpl-1", "choices": [{"index": 0, "delta": {"content": delta}}]}
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()


SSE_DONE = b"data: [DONE]\n\n"
_SSE_START = b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nconnection: close\r\n\r\n"


def _json_ok(writer, content: str):
    body = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]}
    ).encode()
    writer.write(
        b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
        + str(len(body)).encode()
        + b"\r\nconnection: close\r\n\r\n"
        + body
    )


async def _consume(client: _SseClient, timeout=25.0):
    """消费到收流哨兵。"""
    frames = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        env = await asyncio.wait_for(
            client.queue.get(), timeout=max(0.1, deadline - asyncio.get_event_loop().time())
        )
        if env is _END:
            break
        frames.append(env)
    return frames


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


class TestTimeoutSemantics:
    """预算超时=failed 可见（修前 fabricated completed）+ 已发块不悬挂。"""

    @pytest.mark.asyncio
    async def test_timeout_goes_failed_not_completed(self, monkeypatch, clean_tasks):
        """上游挂死→预算耗尽：state=failed（修前 completed）、超时说明不入 history。"""
        gate = asyncio.Event()

        async def responder(req_body, writer):
            await gate.wait()  # 上游挂死：永不回

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        monkeypatch.setattr(TaskManager, "PROCESS_BUDGET_S", 0.5, raising=False)
        app = _make_app()
        try:
            data = await _json_call(app, _rpc("SendMessage", {"message": _msg("hello timeout")}))
        finally:
            gate.set()
            server.close()
            await server.wait_closed()

        task = data["result"]["task"]
        # 硬反证：修前 TASK_STATE_COMPLETED + 降级文本入史当成功回复
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        text = task["status"]["message"]["parts"][0]["text"]
        assert "AI处理超时" in text and "总预算耗尽" in text
        # F4b-⑧：错误进 status.message 不进 history
        assert [m["role"] for m in task["history"]] == ["ROLE_USER"]
        hist_texts = "".join(p.get("text", "") for m in task["history"] for p in m["parts"])
        assert "AI处理超时" not in hist_texts  # fabricated 回复不再入史
        assert "收到您的消息" not in hist_texts  # 修前降级文本含此 echo

    @pytest.mark.asyncio
    async def test_timeout_zero_chunks_no_artifact(self, monkeypatch, clean_tasks):
        """订阅者在场但上游零 delta→超时：零 artifact（发块前'无痕可撤'语义零变化）。"""
        gate = asyncio.Event()

        async def responder(req_body, writer):
            writer.write(_SSE_START)  # 响应头到达但零 delta
            await writer.drain()
            await gate.wait()

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        monkeypatch.setattr(TaskManager, "PROCESS_BUDGET_S", 0.5, raising=False)
        app = _make_app()
        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg("hello no chunk")}))
            run = asyncio.create_task(c._run(app))
            frames = await _consume(c)
            await asyncio.wait_for(run, timeout=10)
        finally:
            gate.set()
            server.close()
            await server.wait_closed()

        kinds = [_frame_kind(f) for f in frames]
        assert kinds == ["task", "statusUpdate", "statusUpdate", "task"]
        final = frames[-1]["result"]["task"]
        assert final["status"]["state"] == "TASK_STATE_FAILED"
        assert not final.get("artifacts")  # 零块不 fabricate artifact
        assert [m["role"] for m in final["history"]] == ["ROLE_USER"]

    @pytest.mark.asyncio
    async def test_timeout_closes_committed_chunks(self, monkeypatch, clean_tasks):
        """已发块不悬挂：超时取消也以 lastChunk 收尾（修前取消路径不收尾）。"""
        gate = asyncio.Event()

        async def responder(req_body, writer):
            writer.write(_SSE_START)
            # A 入 pending、B 到→A 发出（延迟一拍）；B 停 pending 等收尾
            writer.write(_sse("A") + _sse("B"))
            await writer.drain()
            await gate.wait()  # [DONE] 永不来：靠取消路径收尾 B

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        # 预算留足 A/B 到达余量（本地 socket 毫秒级），到期即真取消
        monkeypatch.setattr(TaskManager, "PROCESS_BUDGET_S", 1.5, raising=False)
        app = _make_app()
        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg("hello chunks")}))
            run = asyncio.create_task(c._run(app))
            frames = await _consume(c)
            await asyncio.wait_for(run, timeout=10)
        finally:
            gate.set()
            server.close()
            await server.wait_closed()

        kinds = [_frame_kind(f) for f in frames]
        assert kinds == [
            "task",
            "statusUpdate",
            "artifactUpdate",
            "artifactUpdate",
            "statusUpdate",
            "task",
        ]
        arts = [_art(f) for f in frames if _frame_kind(f) == "artifactUpdate"]
        # A 发出（append=False）→ B 由取消路径 finally 收尾（append=True,
        # lastChunk=True）：修前 B 永驻 pending、artifact 无 lastChunk 收尾
        assert [(g["append"], g["lastChunk"]) for g in arts] == [
            (False, False),
            (True, True),
        ]
        assert [g["artifact"]["parts"][0]["text"] for g in arts] == ["A", "B"]
        final = frames[-1]["result"]["task"]
        assert final["status"]["state"] == "TASK_STATE_FAILED"
        assert "AI处理超时" in final["status"]["message"]["parts"][0]["text"]
        # 部分产出如实保留为 artifact；fabricated 回复不入 history
        assert [p["text"] for a in final["artifacts"] for p in a["parts"]] == ["A", "B"]
        assert [m["role"] for m in final["history"]] == ["ROLE_USER"]
        # GetTask 真源与终稿快照一致
        gt = await _json_call(app, _rpc("GetTask", {"id": frames[0]["result"]["task"]["id"]}))
        assert gt["result"]["status"]["state"] == "TASK_STATE_FAILED"
        assert [p["text"] for a in gt["result"]["artifacts"] for p in a["parts"]] == [
            "A",
            "B",
        ]

    @pytest.mark.asyncio
    async def test_cancel_mid_stream_closes_chunks(self, monkeypatch, clean_tasks):
        """取消路径收尾（结构性直证，不借预算间接）：_handle_chat 被 cancel 时
        已发块仍以 lastChunk 收尾——修前 CancelledError 不经 except Exception，
        pending 块永驻不发、artifact 无 lastChunk 收尾（客户端悬挂面）。"""
        gate = asyncio.Event()

        async def responder(req_body, writer):
            writer.write(_SSE_START)
            writer.write(_sse("A") + _sse("B"))  # A 发出后 B 停 pending
            await writer.drain()
            await gate.wait()

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        try:
            from src.a2a.models import Message

            task = await task_manager.create_task(
                Message(role="user", parts=[{"type": "text", "text": "hi cancel"}])
            )
            q = task_manager.subscribe_stream(task.id)  # 订阅者在场→分块流真建
            t = asyncio.create_task(task_manager._handle_chat("hi cancel", task))
            # A 块真发出（committed=True；B 尚在 pending）才取消——确定性无竞态
            first, _end = await asyncio.wait_for(q.get(), timeout=5)
            assert first["artifactUpdate"]["artifact"]["parts"][0]["text"] == "A"
            assert first["artifactUpdate"]["lastChunk"] is False
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t
            # finally 收尾：B 以 lastChunk=True 发出（修前永驻 pending 不出）
            second, _end2 = await asyncio.wait_for(q.get(), timeout=5)
            assert second["artifactUpdate"]["artifact"]["parts"][0]["text"] == "B"
            assert second["artifactUpdate"]["append"] is True
            assert second["artifactUpdate"]["lastChunk"] is True
        finally:
            gate.set()
            server.close()
            await server.wait_closed()


class TestUnchangedFaces:
    """不变面（新旧皆过）：正常 completed 与 F4b-⑱ input-required 语义零变化。"""

    @pytest.mark.asyncio
    async def test_completed_path_zero_change(self, monkeypatch, clean_tasks):
        """正常完成面零变化：completed + 回复入史（既有契约锁死）。"""
        gate = asyncio.Event()
        gate.set()  # responder 立即回

        async def responder(req_body, writer):
            _json_ok(writer, "stub reply")

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            data = await _json_call(app, _rpc("SendMessage", {"message": _msg("hello ok")}))
        finally:
            server.close()
            await server.wait_closed()

        task = data["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert [m["role"] for m in task["history"]] == ["ROLE_USER", "ROLE_AGENT"]
        assert task["history"][1]["parts"][0]["text"] == "stub reply"

    @pytest.mark.asyncio
    async def test_input_required_path_unchanged(self, clean_tasks):
        """无可提取文本→input-required 零变化（F4b-⑱ 语义不被本拍动）。"""
        app = _make_app()
        data = await _json_call(
            app,
            _rpc(
                "SendMessage",
                {"message": {"role": "ROLE_USER", "parts": [], "messageId": "to-empty"}},
            ),
        )
        task = data["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert [m["role"] for m in task["history"]] == ["ROLE_USER", "ROLE_AGENT"]
