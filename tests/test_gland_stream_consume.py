"""F4b-①：gland ModelRouter._call_chat stream=True 真流式消费收口。

定性（F4 审计）：stream 参数进 payload 但消费层 resp.json() 整段解析，stream=True
对回 text/event-stream 的上游必抛 JSONDecodeError（wired 但不 functional 潜在断点，
全仓 stream=True 调用点=0）。本组测试锁三态：SSE 帧聚合、JSON 透传兼容、
协议故障可见（禁静默空串）+ 链降级接线。
"""

import asyncio
import json

import httpx
import pytest

from src.gland.router import (
    ModelRouter,
    StreamingProtocolError,
    _aggregate_sse_frames,
)

SSE_BODY = (
    'data: {"id":"chatcmpl-1","model":"m","created":1728000000,'
    '"choices":[{"index":0,"delta":{"role":"assistant","content":"你"},"finish_reason":null}]}\n\n'
    ": keepalive\n\n"
    'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":"好"},"finish_reason":null}]}\n\n'
    'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":"！"},"finish_reason":"stop"}]}\n\n'
    'data: {"id":"chatcmpl-1","choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
    "data: [DONE]\n\n"
    'data: {"choices":[{"index":0,"delta":{"content":"AFTER-DONE-LEAK"}}]}\n\n'
).encode()


def _sse_lines(body: bytes) -> list[str]:
    return body.decode("utf-8").splitlines()


class TestAggregateSseFrames:
    """纯函数层：帧聚合语义。"""

    def test_aggregates_deltas_and_passthrough(self):
        out = _aggregate_sse_frames(_sse_lines(SSE_BODY))
        assert out["choices"][0]["message"] == {"role": "assistant", "content": "你好！"}
        assert out["choices"][0]["finish_reason"] == "stop"
        assert out["id"] == "chatcmpl-1"
        assert out["model"] == "m"
        assert out["created"] == 1728000000
        assert out["usage"] == {"prompt_tokens": 3, "completion_tokens": 2}

    def test_ignores_comments_blanks_and_stops_at_done(self):
        # [DONE] 之后的帧若被解析会拼出 "AFTER-DONE-LEAK"——必须不可见
        out = _aggregate_sse_frames(_sse_lines(SSE_BODY))
        assert "AFTER-DONE-LEAK" not in out["choices"][0]["message"]["content"]
        # 非 data 行（event:/id:/注释/空行）不炸不计入
        lines = [
            "event: ping",
            "id: 7",
            "",
            ": hi",
            'data: {"choices":[{"delta":{"content":"x"}}]}',
        ]
        assert _aggregate_sse_frames(lines)["choices"][0]["message"]["content"] == "x"

    def test_no_frames_raises(self):
        with pytest.raises(StreamingProtocolError, match="no SSE data frames"):
            _aggregate_sse_frames(["", ": keepalive", "event: ping"])

    def test_bad_json_frame_raises(self):
        with pytest.raises(StreamingProtocolError, match="not valid JSON"):
            _aggregate_sse_frames(
                ['data: {"choices":[{"delta":{"content":"x"}}]}', "data: {not json}"]
            )

    def test_non_object_frame_raises(self):
        with pytest.raises(StreamingProtocolError, match="not a JSON object"):
            _aggregate_sse_frames(["data: [1,2,3]"])

    def test_missing_fields_not_fabricated(self):
        out = _aggregate_sse_frames(['data: {"choices":[{"delta":{"content":"仅内容"}}]}'])
        choice = out["choices"][0]
        assert choice["message"]["content"] == "仅内容"
        assert choice["finish_reason"] is None  # 上游没发不硬造 "stop"
        assert "usage" not in out
        assert "id" not in out
        assert "model" not in out
        assert choice["message"]["role"] == "assistant"  # 回复方角色无歧义缺省

    def test_multi_choice_aggregated_by_index(self):
        lines = [
            'data: {"choices":[{"index":0,"delta":{"content":"A"}},{"index":1,"delta":{"content":"1"}}]}',
            'data: {"choices":[{"index":0,"delta":{"content":"B"},"finish_reason":"stop"},'
            '{"index":1,"delta":{"content":"2"},"finish_reason":"length"}]}',
        ]
        out = _aggregate_sse_frames(lines)
        assert [c["index"] for c in out["choices"]] == [0, 1]
        assert out["choices"][0]["message"]["content"] == "AB"
        assert out["choices"][1]["message"]["content"] == "12"
        assert out["choices"][1]["finish_reason"] == "length"


def _make_router(handler) -> ModelRouter:
    router = ModelRouter()
    router._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
    router.key_manager.add_key("mock", "test-key")
    router.add_provider("mock", "http://mock", models={"chat": "m", "embedding": "e"})
    return router


class TestRouterStreamTrue:
    """路由层：stream=True 消费真流式响应（修复前 resp.json() 必炸）。"""

    @pytest.mark.asyncio
    async def test_stream_true_consumes_sse(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream; charset=utf-8"},
                content=SSE_BODY,
            )

        router = _make_router(handler)
        try:
            result = await router.chat([{"role": "user", "content": "hi"}], stream=True)
            assert seen["payload"]["stream"] is True  # 请求真带 stream:true
            assert result["choices"][0]["message"]["content"] == "你好！"
            assert result["usage"] == {"prompt_tokens": 3, "completion_tokens": 2}
            # 控制组：响应体不是合法 JSON —— 旧 resp.json() 路径必抛 JSONDecodeError
            with pytest.raises(json.JSONDecodeError):
                json.loads(SSE_BODY)
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_stream_false_json_path_unchanged(self):
        body = {"choices": [{"message": {"role": "assistant", "content": "pong"}}]}

        def handler(request: httpx.Request) -> httpx.Response:
            assert json.loads(request.content)["stream"] is False
            return httpx.Response(200, json=body)

        router = _make_router(handler)
        try:
            result = await router.chat([{"role": "user", "content": "hi"}])
            assert result == body  # 非流式路径整段透传零变化
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_stream_true_provider_returns_json_compat(self):
        # 上游忽略 stream:true 回 JSON（真实存在）——按真实形态解析，不误当 SSE
        body = {"choices": [{"message": {"role": "assistant", "content": "整段"}}]}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"content-type": "application/json"}, content=json.dumps(body).encode()
            )

        router = _make_router(handler)
        try:
            result = await router.chat([{"role": "user", "content": "hi"}], stream=True)
            assert result["choices"][0]["message"]["content"] == "整段"
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_unknown_content_type_rejected_visibly(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"garbage")

        router = _make_router(handler)
        try:
            from src.gland.router import AllProvidersFailedError

            with pytest.raises(AllProvidersFailedError):
                await router.chat([{"role": "user", "content": "hi"}], stream=True)
        finally:
            await router.shutdown()

    @pytest.mark.asyncio
    async def test_protocol_error_fails_over_to_next_provider(self):
        calls = {"a": 0, "b": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "mock-a":
                calls["a"] += 1
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=b"data: {broken frame}\n\n",
                )
            calls["b"] += 1
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'data: {"choices":[{"delta":{"role":"assistant","content":"ok-b"}}]}\n\n'
                    b"data: [DONE]\n\n"
                ),
            )

        router = ModelRouter()
        router._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
        router.key_manager.add_key("a", "key-a")
        router.key_manager.add_key("b", "key-b")
        router.add_provider("a", "http://mock-a", models={"chat": "m"}, priority=0)
        router.add_provider("b", "http://mock-b", models={"chat": "m"}, priority=1)
        try:
            result = await router.chat([{"role": "user", "content": "hi"}], stream=True)
            assert result["choices"][0]["message"]["content"] == "ok-b"
            assert calls["a"] == 1 and calls["b"] == 1  # 协议故障真降级到备胎
            trace = router._last_chain_trace
            assert any("not valid JSON" in str(t.get("error", "")) for t in trace)
        finally:
            await router.shutdown()


class TestStreamOverRealSocket:
    """真 socket 实弹：SSE 分段跨 TCP 写出，逐帧聚合不丢不重。"""

    @pytest.mark.asyncio
    async def test_chat_stream_true_real_http_server(self):
        received = {}

        async def _handle(reader, writer):
            req = await reader.readuntil(b"\r\n\r\n")
            received["req"] = req.decode("latin-1")
            length = 0
            for line in req.decode("latin-1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1])
            received["body"] = json.loads((await reader.readexactly(length)).decode())
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nconnection: close\r\n\r\n"
            )
            # 分两次写：验证增量行装配（半行跨段不丢）
            writer.write(b'data: {"choices":[{"delta":{"role":"assistant","content":"Hel"}}]}\n\n')
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.write(b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\ndata: [DONE]\n\n')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        router = ModelRouter()
        router.key_manager.add_key("mock", "test-key")
        router.add_provider("mock", f"http://127.0.0.1:{port}", models={"chat": "m"})
        try:
            result = await router.chat([{"role": "user", "content": "hi"}], stream=True)
            assert result["choices"][0]["message"]["content"] == "Hello"
            assert received["body"]["stream"] is True
        finally:
            await router.shutdown()
            server.close()
            await server.wait_closed()
