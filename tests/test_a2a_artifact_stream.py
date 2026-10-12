"""F4b-⑯b：a2a token 级增量——LLM 回调贯穿 _handle_chat → artifactUpdate 分块。

修前缺口（F4b-⑯ 留档"事件粒度=状态迁移级+终态快照（token 级 artifact 分块=
F4b-⑯b 候选）"）：流式面只有状态迁移帧，生成过程整段黑箱到终态。

收口（TaskArtifactUpdateEvent 分块，字段逐键照抄 spec §4.2.2）：
- 有活跃流订阅者时 _handle_chat 真流式消费上游 SSE，逐 delta 发
  {"artifactUpdate"} 分块（append/lastChunk 语义精确，延迟一拍发射）。
- 分块同刻累积进 task.artifacts 真源：SubscribeToTask 中途订阅快照恰含已生成
  部分（§3.1.6 防信息丢失），终态 {"task"} 快照含全量 artifact。
- 失败分流以"已发块不可撤回"为界：发块前→既有 ACP 降级零变化；发块后→
  LLMStreamError 任务 failed 可见（禁降级文本与已发流互相矛盾）。
- history 契约零变化（F4b-⑧）；无订阅者/llm_mode=stub 路径字节零变化。

真栈非 mock：asyncio 真 socket 上游 + 直驱 ASGI 真路由层 + 双向门闩硬证明真增量
（缓冲/伪流式实现门闩永不触发→latch_timeouts 非空即失败，控制组语义内建）。
"""

import asyncio
import json
import time

import pytest
from fastapi import FastAPI

from src.a2a.api import router as a2a_router
from src.a2a.task_manager import task_manager

_END = object()  # 收流哨兵


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(a2a_router)
    return app


def _msg(text="hello stream", **extra):
    m = {"role": "ROLE_USER", "parts": [{"text": text}]}
    m.update(extra)
    return m


def _rpc(method, params, rid="a-rpc"):
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


def _frame_kind(env: dict) -> str:
    return next(iter(env["result"]))


def _art(env: dict) -> dict:
    return env["result"]["artifactUpdate"]


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
            "client": ("127.0.0.1", 50001),
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
    """直驱 ASGI 发普通 JSON-RPC（非流式对照面）。"""
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


async def _consume(client: _SseClient, on_frame=None, timeout=25.0):
    """消费到收流；on_frame(env) 每帧回调（门闩事件在此触发）。"""
    frames = []
    times = []
    deadline = time.monotonic() + timeout
    while True:
        env = await asyncio.wait_for(
            client.queue.get(), timeout=max(0.1, deadline - time.monotonic())
        )
        if env is _END:
            break
        frames.append(env)
        times.append(time.monotonic())
        if on_frame is not None:
            on_frame(env)
    return frames, times


class TestTokenIncrementalChunks:
    """token 级增量：artifactUpdate 分块真流式 + 形状 v1.0 + 聚合契约。"""

    @pytest.mark.asyncio
    async def test_true_incremental_latch(self, monkeypatch):
        """双向门闩硬证明：块 i 的 artifactUpdate 帧到达客户端后，上游才发块 i+2。

        发射延迟一拍（pending buffer 保 lastChunk 语义）：帧 d_i 在 d_{i+1} 入口即发。
        缓冲/伪流式实现（整段读完再发）→ 门闩永不触发→latch_timeouts 非空必炸。
        """
        f1, f2 = asyncio.Event(), asyncio.Event()
        latch_timeouts = []
        seen = {"texts": []}

        async def gate(ev):
            try:
                await asyncio.wait_for(ev.wait(), timeout=8)
            except TimeoutError:
                latch_timeouts.append(True)

        async def responder(req_body, writer):
            assert req_body.get("stream") is True  # 真流式请求
            writer.write(_SSE_START)
            writer.write(_sse("He") + _sse("llo "))
            await writer.drain()
            await gate(f1)  # 帧 "He" 已到客户端才继续
            writer.write(_sse("world"))
            await writer.drain()
            await gate(f2)  # 帧 "llo " 已到客户端才继续
            writer.write(_sse("!") + SSE_DONE)
            await writer.drain()

        server, base_url, received = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()

        def on_frame(env):
            if _frame_kind(env) == "artifactUpdate":
                seen["texts"].append(_art(env)["artifact"]["parts"][0]["text"])
                if seen["texts"] == ["He"]:
                    f1.set()
                elif seen["texts"] == ["He", "llo "]:
                    f2.set()

        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))
            frames, times = await _consume(c, on_frame=on_frame)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        assert latch_timeouts == []  # 缓冲即死锁（控制组语义内建）
        kinds = [_frame_kind(f) for f in frames]
        # 帧序（F4b-⑯c）：首帧 Task 快照（§3.1.2）→ working → 4 分块 → 终态 → 快照
        assert kinds == ["task", "statusUpdate"] + ["artifactUpdate"] * 4 + ["statusUpdate", "task"]
        assert seen["texts"] == ["He", "llo ", "world", "!"]
        # 到达时间戳严格递增（真逐帧交付非一整块）
        assert all(b > a for a, b in zip(times, times[1:]))

    @pytest.mark.asyncio
    async def test_chunk_frame_shape_v10(self, monkeypatch):
        """§4.2.2 字段逐键 + append/lastChunk 语义精确 + 同 ID 追加。"""
        got = []

        async def responder(req_body, writer):
            writer.write(_SSE_START)
            writer.write(_sse("A") + _sse("B") + _sse("C") + SSE_DONE)
            await writer.drain()

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()

        def on_frame(env):
            if _frame_kind(env) == "artifactUpdate":
                got.append(_art(env))

        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))
            frames, _ = await _consume(c, on_frame=on_frame)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        assert [g["artifact"]["parts"] for g in got] == [
            [{"text": "A"}],
            [{"text": "B"}],
            [{"text": "C"}],
        ]
        # 事件键集恰 §4.2.2（metadata 未设置整键省略）
        for g in got:
            assert set(g) == {"taskId", "contextId", "artifact", "append", "lastChunk"}
            assert set(g["artifact"]) == {"artifactId", "name", "parts"}
            assert g["artifact"]["name"] == "response"
        task_id = frames[0]["result"]["task"]["id"]  # 首帧=Task 快照（F4b-⑯c）
        context_id = frames[0]["result"]["task"]["contextId"]
        assert all(g["taskId"] == task_id and g["contextId"] == context_id for g in got)
        # append：首块 False（新建）、后续 True（同 ID 追加）；lastChunk 恰末块 True
        assert [g["append"] for g in got] == [False, True, True]
        assert [g["lastChunk"] for g in got] == [False, False, True]
        assert len({g["artifact"]["artifactId"] for g in got}) == 1  # 同一 artifact

    @pytest.mark.asyncio
    async def test_aggregation_and_snapshot_truth(self, monkeypatch):
        """逐块拼接=终稿全文（与非流式 content 同契约）+ 终态快照/GetTask 真源一致。"""

        async def responder(req_body, writer):
            writer.write(_SSE_START)
            writer.write(_sse("你好") + _sse("，世界") + SSE_DONE)
            await writer.drain()

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))
            frames, _ = await _consume(c)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        task_dump = frames[-1]["result"]["task"]
        # history 契约零变化（F4b-⑧）：恰 [user, agent]，agent 终稿=逐块拼接
        assert len(task_dump["history"]) == 2
        assert task_dump["history"][0]["role"] == "ROLE_USER"
        assert task_dump["history"][1]["role"] == "ROLE_AGENT"
        assert task_dump["history"][1]["parts"] == [{"text": "你好，世界"}]
        # 分块同刻累积真源：终态快照 artifact 全量 parts
        assert len(task_dump["artifacts"]) == 1
        assert task_dump["artifacts"][0]["parts"] == [{"text": "你好"}, {"text": "，世界"}]
        # GetTask 真源一致
        resp = await _json_call(app, _rpc("GetTask", {"id": task_dump["id"]}, rid="g1"))
        assert resp["result"]["artifacts"] == task_dump["artifacts"]
        assert resp["result"]["history"] == task_dump["history"]

    @pytest.mark.asyncio
    async def test_json_upstream_single_chunk(self, monkeypatch):
        """上游忽略 stream:true 回 JSON → 整段作单块（append=False/lastChunk=True）
        ——按真实形态消费，不硬造伪增量。"""

        async def responder(req_body, writer):
            assert req_body.get("stream") is True
            _json_ok(writer, "整段回复")

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))
            frames, _ = await _consume(c)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        arts = [_art(f) for f in frames if _frame_kind(f) == "artifactUpdate"]
        assert len(arts) == 1  # 恰单块，禁伪增量
        assert arts[0]["artifact"]["parts"] == [{"text": "整段回复"}]
        assert arts[0]["append"] is False and arts[0]["lastChunk"] is True
        assert frames[-1]["result"]["task"]["history"][1]["parts"] == [{"text": "整段回复"}]


class TestSubscribeCatchUp:
    """§3.1.6 中途订阅：快照恰含已生成部分 + 后续 append 块续上=零信息丢失。"""

    @pytest.mark.asyncio
    async def test_subscribe_midstream_catches_up(self, monkeypatch):
        f1 = asyncio.Event()

        async def responder(req_body, writer):
            writer.write(_SSE_START)
            writer.write(_sse("d1") + _sse("d2"))
            await writer.drain()
            try:
                await asyncio.wait_for(f1.wait(), timeout=8)
            except TimeoutError:
                pass
            writer.write(_sse("d3") + _sse("d4") + SSE_DONE)
            await writer.drain()

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        sub_frames = []

        def on_frame(env):
            if _frame_kind(env) == "artifactUpdate":
                if _art(env)["artifact"]["parts"][0]["text"] == "d1":
                    f1.set()

        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))

            async def consume_main():
                return await _consume(c, on_frame=on_frame)

            main_task = asyncio.create_task(consume_main())
            # 门闩开（d1 块已到达）后中途订阅
            await asyncio.wait_for(f1.wait(), timeout=10)
            task_id = c.frame_list[0]["result"]["task"]["id"]  # 首帧=Task 快照（F4b-⑯c）
            s = _SseClient(_rpc("SubscribeToTask", {"id": task_id}, rid="sub-1"))
            sub_run = asyncio.create_task(s._run(app))
            frames, _ = await main_task
            sub_frames, _ = await _consume(s)
            await asyncio.wait_for(run, timeout=10)
            await asyncio.wait_for(sub_run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        # 订阅首帧=快照（§3.1.6 MUST first event=Task），恰含订阅时点已生成部分
        # （d1 恒在；d2 是否已累积取决于订阅与发射竞态——两种合法时序都无丢无重）
        assert _frame_kind(sub_frames[0]) == "task"
        snap = sub_frames[0]["result"]["task"]
        assert snap["artifacts"][0]["parts"] in ([{"text": "d1"}], [{"text": "d1"}, {"text": "d2"}])
        # 后续 append 块续上：客户端装配= d1+d2+d3+d4 无丢无重
        assembled = [p["text"] for p in snap["artifacts"][0]["parts"]]
        for f in sub_frames[1:]:
            if _frame_kind(f) == "artifactUpdate":
                a = _art(f)
                assert a["append"] is True
                assembled.extend(p["text"] for p in a["artifact"]["parts"])
        assert assembled == ["d1", "d2", "d3", "d4"]
        # 主流同样全量
        main_texts = [
            _art(f)["artifact"]["parts"][0]["text"]
            for f in frames
            if _frame_kind(f) == "artifactUpdate"
        ]
        assert main_texts == ["d1", "d2", "d3", "d4"]


class TestFailureSplit:
    """失败分流：发块前→既有降级零变化；发块后→failed 可见（禁谎报 ok）。"""

    @pytest.mark.asyncio
    async def test_precommit_failure_fallback_no_artifact(self, monkeypatch):
        async def responder(req_body, writer):
            writer.write(
                b"HTTP/1.1 500 Internal Server Error\r\ncontent-length: 4\r\nconnection: close\r\n\r\nboom"
            )

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        acp_calls = []

        async def fake_acp(text, task):
            acp_calls.append(text)
            return "ACP降级回复"

        monkeypatch.setattr(task_manager, "_handle_acp_chat", fake_acp)
        app = _make_app()
        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))
            frames, _ = await _consume(c)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        assert acp_calls == ["hello stream"]  # 发块前失败=既有降级语义零变化
        assert all(_frame_kind(f) != "artifactUpdate" for f in frames)  # 无痕可撤
        task_dump = frames[-1]["result"]["task"]
        assert task_dump["status"]["state"] == "TASK_STATE_COMPLETED"
        assert task_dump["artifacts"] == []
        assert task_dump["history"][1]["parts"] == [{"text": "ACP降级回复"}]

    @pytest.mark.asyncio
    async def test_postcommit_failure_task_failed_visible(self, monkeypatch):
        """d1/d2 已发后上游坏帧 → LLMStreamError → 任务 failed 可见（错误进
        status.message 不进 history，F4b-⑧）；剩余块 lastChunk 收尾禁悬挂。"""
        f1 = asyncio.Event()

        async def responder(req_body, writer):
            writer.write(_SSE_START)
            writer.write(_sse("p1") + _sse("p2"))
            await writer.drain()
            try:
                await asyncio.wait_for(f1.wait(), timeout=8)
            except TimeoutError:
                pass
            writer.write(b"data: {bad-json-not-a-frame\n\n")  # 坏帧如实抛
            await writer.drain()

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()

        def on_frame(env):
            if _frame_kind(env) == "artifactUpdate":
                f1.set()

        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))
            frames, _ = await _consume(c, on_frame=on_frame)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        task_dump = frames[-1]["result"]["task"]
        assert task_dump["status"]["state"] == "TASK_STATE_FAILED"  # 失败可见禁谎报 ok
        assert task_dump["status"]["message"]["parts"][0]["text"].startswith("Error:")
        assert len(task_dump["history"]) == 1  # 错误不进 history（F4b-⑧）
        arts = [_art(f) for f in frames if _frame_kind(f) == "artifactUpdate"]
        assert [a["artifact"]["parts"][0]["text"] for a in arts] == ["p1", "p2"]
        assert arts[-1]["lastChunk"] is True  # 收尾块：客户端不悬挂

    @pytest.mark.asyncio
    async def test_unknown_content_type_precommit_fallback(self, monkeypatch):
        async def responder(req_body, writer):
            body = b"plain text"
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\ncontent-length: "
                + str(len(body)).encode()
                + b"\r\nconnection: close\r\n\r\n"
                + body
            )

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        acp_calls = []

        async def fake_acp(text, task):
            acp_calls.append(text)
            return "ACP降级回复"

        monkeypatch.setattr(task_manager, "_handle_acp_chat", fake_acp)
        app = _make_app()
        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))
            frames, _ = await _consume(c)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        assert acp_calls == ["hello stream"]  # 禁猜形态→如实走降级
        assert all(_frame_kind(f) != "artifactUpdate" for f in frames)
        assert frames[-1]["result"]["task"]["history"][1]["parts"] == [{"text": "ACP降级回复"}]


class TestZeroDriftFaces:
    """零变化面：无订阅者/stub 模式字节零漂移 + 推送面不被分块刷爆。"""

    @pytest.mark.asyncio
    async def test_no_subscriber_nonstream_path_unchanged(self, monkeypatch):
        async def responder(req_body, writer):
            assert "stream" not in req_body  # 非流式请求字节零变化
            _json_ok(writer, "整段回复")

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            resp = await _json_call(app, _rpc("SendMessage", {"message": _msg()}))
        finally:
            server.close()
            await server.wait_closed()

        task_dump = resp["result"]["task"]
        assert task_dump["artifacts"] == []  # 无人观看=无分块无 artifact
        assert task_dump["history"][1]["parts"] == [{"text": "整段回复"}]

    @pytest.mark.asyncio
    async def test_stub_mode_zero_chunk_regression(self):
        """llm_mode=stub 确定性路径零分块（F4b-⑯ 既有帧序契约零变化）。"""
        app = _make_app()
        c = _SseClient(
            _rpc("SendStreamingMessage", {"message": _msg(), "metadata": {"llm_mode": "stub"}})
        )
        run = asyncio.create_task(c._run(app))
        frames, _ = await _consume(c)
        await asyncio.wait_for(run, timeout=10)

        # F4b-⑯c 帧序修齐：首帧 Task 快照 + F4b-⑯ 既有事件序零变化
        assert [_frame_kind(f) for f in frames] == ["task", "statusUpdate", "statusUpdate", "task"]
        assert frames[-1]["result"]["task"]["artifacts"] == []

    @pytest.mark.asyncio
    async def test_push_webhook_not_flooded_by_chunks(self, monkeypatch):
        """分块不逐 token 打 webhook（终态 {"task"} 全量含 artifact，push 消费者不丢
        内容）——推送面保持状态迁移粒度（如实边界，见 roadmap）。"""

        async def webhook(req_body, writer):
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\nconnection: close\r\n\r\nok")

        wh_server, wh_url, wh_received = await _start_stub(webhook)

        async def responder(req_body, writer):
            writer.write(_SSE_START)
            writer.write(_sse("x") + _sse("y") + SSE_DONE)
            await writer.drain()

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            c = _SseClient(
                _rpc(
                    "SendStreamingMessage",
                    {
                        "message": _msg(),
                        "configuration": {"taskPushNotificationConfig": {"url": wh_url}},
                    },
                )
            )
            run = asyncio.create_task(c._run(app))
            frames, _ = await _consume(c)
            await asyncio.wait_for(run, timeout=10)
            await asyncio.sleep(0.2)  # 投递是后台尽力，留收尾窗口
        finally:
            server.close()
            await server.wait_closed()
            wh_server.close()
            await wh_server.wait_closed()

        push_bodies = wh_received["bodies"]
        assert push_bodies, "webhook 至少收到状态迁移投递"
        # 分块零推送（禁每 token 一 POST 刷爆）
        assert all(next(iter(b)) != "artifactUpdate" for b in push_bodies)
        assert {next(iter(b)) for b in push_bodies} <= {"statusUpdate", "task"}
        # 终态 {"task"} 投递含全量 artifact（push 消费者不丢内容）
        terminal = [b for b in push_bodies if next(iter(b)) == "task"]
        assert terminal and terminal[-1]["task"]["artifacts"][0]["parts"] == [
            {"text": "x"},
            {"text": "y"},
        ]
        # SSE 面照常分块
        assert [_frame_kind(f) for f in frames].count("artifactUpdate") == 2


class TestMultiTurn:
    """多轮续接：每回合独立 artifact（同任务内 artifactId 唯一）。"""

    @pytest.mark.asyncio
    async def test_continuation_distinct_artifacts(self, monkeypatch):
        async def responder(req_body, writer):
            writer.write(_SSE_START)
            writer.write(_sse("r1") + _sse("r2") + SSE_DONE)
            await writer.drain()

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            c1 = _SseClient(_rpc("SendStreamingMessage", {"message": _msg("turn one")}))
            run1 = asyncio.create_task(c1._run(app))
            frames1, _ = await _consume(c1)
            await asyncio.wait_for(run1, timeout=10)
            task_id = frames1[-1]["result"]["task"]["id"]

            c2 = _SseClient(
                _rpc(
                    "SendStreamingMessage",
                    {"message": _msg("turn two", taskId=task_id), "id": task_id},
                )
            )
            run2 = asyncio.create_task(c2._run(app))
            frames2, _ = await _consume(c2)
            await asyncio.wait_for(run2, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        dump = frames2[-1]["result"]["task"]
        assert len(dump["artifacts"]) == 2  # 每回合独立 artifact
        ids = [a["artifactId"] for a in dump["artifacts"]]
        assert len(set(ids)) == 2  # 同任务内 artifactId 唯一（§4.1.7）
        assert dump["artifacts"][1]["parts"] == [{"text": "r1"}, {"text": "r2"}]
        # history 恰 4 条 [u,a,u,a]（F4b-⑧ 契约零变化）
        assert [m["role"] for m in dump["history"]] == [
            "ROLE_USER",
            "ROLE_AGENT",
            "ROLE_USER",
            "ROLE_AGENT",
        ]
        # 第二回合 chunk 帧引用第二个 artifactId
        arts2 = [_art(f) for f in frames2 if _frame_kind(f) == "artifactUpdate"]
        assert {a["artifact"]["artifactId"] for a in arts2} == {ids[1]}
