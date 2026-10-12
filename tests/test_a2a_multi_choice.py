"""F4b-⑰：a2a 多 choice 分块收口——非主 choice 独立 artifact，不再静默丢弃。

修前缺口（F4b-⑯b 留档诚实边界"多 choice 只流 choice[0]"）：上游 choices[i>0]
的增量在流式面被 _sse_delta_text 静默丢弃、JSON 整段面被 choices[0] 提取静默丢弃、
非流式面被 data["choices"][0] 静默丢弃——与 F4b-① gland"多 choice 按 index
各自聚合不静默丢"纪律相悖。

收口语义（主 choice 契约零变化）：
- 主 choice（index 0）：流式=分块 artifact "response"+回复文本（history 契约零
  变化）；非流式=仅回复文本（不建 artifact，F4b-⑯b 零变化面延续）。
- 非主 choice：独立 artifact name="response-{index}"——流式逐 delta 分块（各自
  append/lastChunk 精确收尾）、JSON 整段/非流式=单块物化，一律不静默丢。
- 失败分流零变化：任一 choice 已发块即"已发块不可撤回"→LLMStreamError 可见，
  全部 artifact lastChunk 收尾禁悬挂。

真栈非 mock：asyncio 真 socket 上游桩 + 直驱 ASGI 真路由层（test_a2a_artifact_stream
同范式）。
"""

import asyncio
import json
import time
from typing import cast

import pytest
from fastapi import FastAPI

from src.a2a.api import router as a2a_router
from src.a2a.task_manager import (
    _message_choice_texts,
    _sse_choice_deltas,
    task_manager,
)

_END = object()  # 收流哨兵


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(a2a_router)
    return app


def _msg(text="hello multi-choice", **extra):
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


@pytest.fixture(autouse=True)
def isolate_intrusion_state():
    """WAF 单例测试隔离（test_a2a_push_notifications 同款坑记档：密集突发毒化
    共享 IP 速率账本/自动封禁，逐测清账本）。"""
    from src.api.immune import intrusion

    intrusion._ip_suspicion.clear()
    intrusion._request_counts.clear()
    yield
    intrusion._ip_suspicion.clear()
    intrusion._request_counts.clear()


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


def _sse_choices(items) -> bytes:
    """多 choice SSE 帧：items=[(index, delta_text), ...]。"""
    obj = {
        "id": "cmpl-1",
        "choices": [{"index": idx, "delta": {"content": text}} for idx, text in items],
    }
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()


def _sse_single(delta: str) -> bytes:
    return _sse_choices([(0, delta)])


SSE_DONE = b"data: [DONE]\n\n"
_SSE_START = b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nconnection: close\r\n\r\n"


def _json_body(choices) -> bytes:
    """JSON 整段响应：choices=[(index, content), ...]。"""
    return json.dumps(
        {
            "choices": [
                {"index": idx, "message": {"role": "assistant", "content": text}}
                for idx, text in choices
            ]
        }
    ).encode()


def _json_ok(writer, choices):
    body = _json_body(choices)
    writer.write(
        b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
        + str(len(body)).encode()
        + b"\r\nconnection: close\r\n\r\n"
        + body
    )


async def _consume(client: _SseClient, on_frame=None, timeout=25.0):
    """消费到收流；on_frame(env) 每帧回调（门闩事件在此触发）。"""
    frames = []
    deadline = time.monotonic() + timeout
    while True:
        env = await asyncio.wait_for(
            client.queue.get(), timeout=max(0.1, deadline - time.monotonic())
        )
        if env is _END:
            break
        frames.append(env)
        if on_frame is not None:
            on_frame(env)
    return frames


def _per_artifact(frames):
    """artifactUpdate 帧按 artifact name 归组：{name: [(text, append, lastChunk), ...]}。"""
    out: dict = {}
    for f in frames:
        if _frame_kind(f) != "artifactUpdate":
            continue
        a = _art(f)
        out.setdefault(a["artifact"]["name"], []).append(
            (
                a["artifact"]["parts"][0]["text"],
                a["append"],
                a["lastChunk"],
            )
        )
    return out


class TestSseChoiceDeltas:
    """纯函数：逐 choice 归集（修前 _sse_delta_text 只取 choices[0] 静默丢）。"""

    def test_multi_index_separate(self):
        frame = {
            "choices": [
                {"index": 0, "delta": {"content": "a"}},
                {"index": 1, "delta": {"content": "b"}},
            ]
        }
        assert _sse_choice_deltas(frame) == {0: "a", 1: "b"}

    def test_missing_index_positional_fallback(self):
        # 单 choice 帧无 index → 位置 0（既有语义零变化）
        assert _sse_choice_deltas({"choices": [{"delta": {"content": "x"}}]}) == {0: "x"}

    def test_bool_index_fallback(self):
        frame = {"choices": [{"index": True, "delta": {"content": "t"}}]}
        assert _sse_choice_deltas(frame) == {0: "t"}

    def test_non_text_frames_ignored(self):
        assert _sse_choice_deltas({"choices": []}) == {}  # usage 收尾帧
        assert _sse_choice_deltas({"choices": [{"index": 0, "delta": {}}]}) == {}
        assert _sse_choice_deltas({"choices": [{"index": 0, "delta": {"content": None}}]}) == {}
        assert _sse_choice_deltas({"choices": [{"index": 0, "delta": {"content": ""}}]}) == {}
        assert _sse_choice_deltas(cast(dict, [])) == {}  # 形状不符不 fabricate（故意喂非 dict）

    def test_message_choice_texts_shape(self):
        choices = [
            {"index": 0, "message": {"content": "p"}},
            {"index": 1, "message": {"content": "q"}},
            {"index": 2, "message": {"content": None}},
        ]
        assert _message_choice_texts(choices) == {0: "p", 1: "q"}


class TestMultiChoiceStreamChunks:
    """流式面：每 choice 独立 artifact 分块（append/lastChunk 各自精确）。"""

    @pytest.mark.asyncio
    async def test_two_choices_per_artifact_and_assembly(self, monkeypatch):
        async def responder(req_body, writer):
            assert req_body.get("stream") is True
            writer.write(_SSE_START)
            writer.write(_sse_choices([(0, "A1"), (1, "B1")]))
            writer.write(_sse_choices([(0, "A2")]))
            writer.write(_sse_choices([(1, "B2")]))
            writer.write(_sse_choices([(0, "A3"), (1, "B3")]))
            writer.write(SSE_DONE)
            await writer.drain()

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))
            frames = await _consume(c)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        per = _per_artifact(frames)
        assert set(per) == {"response", "response-1"}  # 非主 choice 不静默丢
        assert per["response"] == [("A1", False, False), ("A2", True, False), ("A3", True, True)]
        assert per["response-1"] == [("B1", False, False), ("B2", True, False), ("B3", True, True)]
        # 同 artifact 同 ID（append 追加语义）
        ids = {}
        for f in frames:
            if _frame_kind(f) == "artifactUpdate":
                a = _art(f)["artifact"]
                ids.setdefault(a["name"], set()).add(a["artifactId"])
        assert all(len(v) == 1 for v in ids.values())
        assert len(set().union(*ids.values())) == 2  # 两 choice 两 artifactId

        final = frames[-1]["result"]["task"]
        arts = final["artifacts"]
        assert [a["name"] for a in arts] == ["response", "response-1"]
        assert ["".join(p["text"] for p in a["parts"]) for a in arts] == ["A1A2A3", "B1B2B3"]
        # history 契约零变化：恰 [user, agent]，agent 文本=主 choice（chat 单回复）
        assert len(final["history"]) == 2
        assert final["history"][0]["role"] == "ROLE_USER"
        assert final["history"][1]["role"] == "ROLE_AGENT"
        assert final["history"][1]["parts"] == [{"text": "A1A2A3"}]

    @pytest.mark.asyncio
    async def test_secondary_choice_lagging_no_prefabrication(self, monkeypatch):
        """非主 choice 懒创建：首个增量前零帧零 artifact，不为缺席 choice fabricate。"""

        async def responder(req_body, writer):
            writer.write(_SSE_START)
            writer.write(_sse_choices([(0, "A1")]))
            writer.write(_sse_choices([(0, "A2")]))
            await writer.drain()
            await asyncio.sleep(0.2)  # 非主 choice 迟到
            writer.write(_sse_choices([(1, "B1")]))
            writer.write(_sse_choices([(1, "B2")]))
            writer.write(SSE_DONE)
            await writer.drain()

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))
            frames = await _consume(c)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        names = [_art(f)["artifact"]["name"] for f in frames if _frame_kind(f) == "artifactUpdate"]
        assert names == ["response", "response-1", "response", "response-1"]
        per = _per_artifact(frames)
        assert per["response"] == [("A1", False, False), ("A2", True, True)]
        assert per["response-1"] == [("B1", False, False), ("B2", True, True)]

    @pytest.mark.asyncio
    async def test_single_choice_zero_change(self, monkeypatch):
        """单 choice 回归：恰一 artifact "response"，零 response-*（既有契约零变化）。"""

        async def responder(req_body, writer):
            writer.write(_SSE_START)
            writer.write(_sse_single("X1") + _sse_single("X2") + SSE_DONE)
            await writer.drain()

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))
            frames = await _consume(c)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        per = _per_artifact(frames)
        assert set(per) == {"response"}
        assert per["response"] == [("X1", False, False), ("X2", True, True)]
        final = frames[-1]["result"]["task"]
        assert [a["name"] for a in final["artifacts"]] == ["response"]
        assert final["history"][1]["parts"] == [{"text": "X1X2"}]

    @pytest.mark.asyncio
    async def test_json_upstream_multi_choice_single_blocks(self, monkeypatch):
        """上游忽略 stream:true 回 JSON 整段：各 choice 单块物化（不硬造伪增量）。"""

        async def responder(req_body, writer):
            assert req_body.get("stream") is True
            _json_ok(writer, [(0, "JP"), (1, "JQ"), (2, "JR")])

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            c = _SseClient(_rpc("SendStreamingMessage", {"message": _msg()}))
            run = asyncio.create_task(c._run(app))
            frames = await _consume(c)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        per = _per_artifact(frames)
        assert set(per) == {"response", "response-1", "response-2"}
        for name, blocks in per.items():
            assert blocks[0][1] is False  # append=False 新建
            assert blocks[-1][2] is True  # lastChunk=True 收尾
            assert len(blocks) == 1  # 单块（整段不硬造伪增量）
        final = frames[-1]["result"]["task"]
        assert [a["name"] for a in final["artifacts"]] == ["response", "response-1", "response-2"]
        assert final["history"][1]["parts"] == [{"text": "JP"}]  # 主 choice=回复

    @pytest.mark.asyncio
    async def test_failure_split_all_choices_closed(self, monkeypatch):
        """发块后坏帧：任一 choice 已发块即失败可见，全部 artifact lastChunk 收尾。"""
        f1 = asyncio.Event()

        async def responder(req_body, writer):
            writer.write(_SSE_START)
            writer.write(_sse_choices([(0, "a1"), (1, "b1")]))
            writer.write(_sse_choices([(0, "a2"), (1, "b2")]))
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
            frames = await _consume(c, on_frame=on_frame)
            await asyncio.wait_for(run, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        final = frames[-1]["result"]["task"]
        assert final["status"]["state"] == "TASK_STATE_FAILED"  # 失败可见禁谎报 ok
        assert "LLM stream interrupted" in final["status"]["message"]["parts"][0]["text"]
        assert len(final["history"]) == 1  # 错误不进 history（F4b-⑧）
        per = _per_artifact(frames)
        assert per["response"] == [("a1", False, False), ("a2", True, True)]
        assert per["response-1"] == [("b1", False, False), ("b2", True, True)]
        # 全部 artifact 收尾块 lastChunk=True（客户端不悬挂任一 choice）
        for blocks in per.values():
            assert blocks[-1][2] is True


class TestNonStreamMultiChoice:
    """非流式面（无订阅者）：非主 choice 物化为独立 artifact，主 choice 契约零变化。"""

    @pytest.mark.asyncio
    async def test_extras_materialized(self, monkeypatch):
        async def responder(req_body, writer):
            assert "stream" not in req_body  # 非流式请求零 stream 键（既有契约）
            _json_ok(writer, [(0, "NP"), (1, "NQ"), (2, "NR")])

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            env = await _json_call(app, _rpc("SendMessage", {"message": _msg()}))
        finally:
            server.close()
            await server.wait_closed()

        task_dump = env["result"]["task"]
        assert task_dump["status"]["state"] == "TASK_STATE_COMPLETED"
        # 主 choice=回复文本（history 契约零变化）
        assert task_dump["history"][1]["parts"] == [{"text": "NP"}]
        # 非主 choice 不静默丢：独立 artifact 单块
        arts = task_dump["artifacts"]
        assert [a["name"] for a in arts] == ["response-1", "response-2"]
        assert [a["parts"] for a in arts] == [[{"text": "NQ"}], [{"text": "NR"}]]

    @pytest.mark.asyncio
    async def test_single_choice_artifacts_empty(self, monkeypatch):
        """单 choice 非流式回归：artifacts==[]（F4b-⑯b 零变化面延续）。"""

        async def responder(req_body, writer):
            _json_ok(writer, [(0, "only")])

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            env = await _json_call(app, _rpc("SendMessage", {"message": _msg()}))
        finally:
            server.close()
            await server.wait_closed()

        task_dump = env["result"]["task"]
        assert task_dump["artifacts"] == []
        assert task_dump["history"][1]["parts"] == [{"text": "only"}]

    @pytest.mark.asyncio
    async def test_unindexed_choices_positional_names(self, monkeypatch):
        """上游 choices 无 index：位置兜底——主 choice=choices[0]，其余 response-1/2。"""

        async def responder(req_body, writer):
            body = json.dumps(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "UP"}},
                        {"message": {"role": "assistant", "content": "UQ"}},
                    ]
                }
            ).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
                + str(len(body)).encode()
                + b"\r\nconnection: close\r\n\r\n"
                + body
            )

        server, base_url, _ = await _start_stub(responder)
        _patch_llm(monkeypatch, base_url)
        app = _make_app()
        try:
            env = await _json_call(app, _rpc("SendMessage", {"message": _msg()}))
        finally:
            server.close()
            await server.wait_closed()

        task_dump = env["result"]["task"]
        assert task_dump["history"][1]["parts"] == [{"text": "UP"}]
        assert [a["name"] for a in task_dump["artifacts"]] == ["response-1"]
        assert task_dump["artifacts"][0]["parts"] == [{"text": "UQ"}]
