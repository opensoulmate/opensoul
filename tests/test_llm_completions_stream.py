"""F4b-②：/api/llm/completions stream 透传（LLMRequest.stream + StreamingResponse）。

定性（F4 审计④）：LLMRequest 无 stream 字段，客户端带 stream:true 被 pydantic
静默忽略 + resp.json() 整段回 = 想要流式被静默降级。本组测试锁四态：
①stream=true 真流式透传（逐块转发上游原始帧，增量交付硬证明——双向门闩，
  缓冲即死锁失败）②stream=False 字节恒等零行为漂移（payload 不带 stream 键）
③上游忽略 stream 回 JSON → 如实整段回（不硬造 SSE 伪流式）④失败 typed 可见
（502/504，禁静默空流）+ 非 JSON 非 SSE 形态原样透传（透传代理不猜形态）。

真栈非 mock：asyncio 真 socket 上游 + 直驱 ASGI app（真 FastAPI 路由层）。
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient  # noqa: F401  （保留 import 面供调试）

import src.api.llm as llm_mod
from src.api.llm import LLMRequest
from src.api.llm import router as llm_router

SSE_FRAME_1 = b'data: {"id":"cmpl-1","choices":[{"index":0,"delta":{"role":"assistant","content":"Hel"}}]}\n\n'
SSE_FRAME_2 = b'data: {"id":"cmpl-1","choices":[{"index":0,"delta":{"content":"lo"}}]}\n\n'
SSE_FRAME_DONE = b"data: [DONE]\n\n"


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(llm_router, prefix="/api/llm")
    return app


def _patch_slot(monkeypatch, base_url: str):
    """completions 走 _active_slot('api_key'|'base_url'|'model') 取槽位——指到本地桩。"""
    slots = {"api_key": "test-key", "base_url": base_url, "model": "stub-model"}
    monkeypatch.setattr(llm_mod, "_active_slot", lambda field: slots[field])


async def _start_stub(responder):
    """真 socket 上游桩。responder(req_body: dict, writer) 写完整 HTTP 响应。"""
    received = {}

    async def _handle(reader, writer):
        try:
            req = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in req.decode("latin-1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1])
            body = json.loads((await reader.readexactly(length)).decode())
            received["body"] = body
            received["raw"] = req.decode("latin-1")
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


async def _asgi_call(app, payload: dict, first_chunk_event=None):
    """直驱 ASGI app 发 POST /api/llm/completions，返回 (status, headers, body, chunks)。"""
    body = json.dumps(payload).encode()
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/llm/completions",
        "raw_path": b"/api/llm/completions",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    state = {"sent": False}

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)  # 不再给 disconnect；流完成即返回
        return {"type": "http.disconnect"}

    messages = []

    async def send(message):
        messages.append(message)
        if (
            first_chunk_event is not None
            and message["type"] == "http.response.body"
            and message.get("body")
        ):
            first_chunk_event.set()

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    chunks = [m.get("body", b"") for m in messages if m["type"] == "http.response.body"]
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, b"".join(chunks), chunks


class TestStreamTrueSseRelay:
    """stream=true：真流式透传（逐块转发上游原始帧，字节级不改写）。"""

    @pytest.mark.asyncio
    async def test_sse_relay_incremental_delivery(self, monkeypatch):
        gate_rest = asyncio.Event()

        async def responder(req_body, writer):
            # 请求真带 stream:true（修前此键根本不存在）
            assert req_body["stream"] is True
            assert req_body["model"] == "stub-model"
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\nconnection: close\r\n\r\n"
            )
            writer.write(SSE_FRAME_1)
            await writer.drain()
            # 双向门闩：第二段等客户端确认已收到第一段才发——
            # 若端点先整段读完再回（缓冲/伪流式），first_chunk 永不触发→测试超时失败
            try:
                await asyncio.wait_for(gate_rest.wait(), timeout=4)
            except TimeoutError:
                pass
            writer.write(SSE_FRAME_2 + SSE_FRAME_DONE)
            await writer.drain()

        server, base_url, received = await _start_stub(responder)
        _patch_slot(monkeypatch, base_url)
        app = _make_app()
        first_chunk = asyncio.Event()
        try:
            task = asyncio.create_task(
                _asgi_call(
                    app,
                    {"messages": [{"role": "user", "content": "hi"}], "stream": True},
                    first_chunk_event=first_chunk,
                )
            )
            # 第一段必须在上游发第二段之前就到达客户端（真增量交付硬证明）
            await asyncio.wait_for(first_chunk.wait(), timeout=3)
            gate_rest.set()
            status, headers, body, chunks = await asyncio.wait_for(task, timeout=10)
        finally:
            server.close()
            await server.wait_closed()

        assert status == 200
        assert headers["content-type"].startswith("text/event-stream")
        # 字节级透传：上游原始帧（含注释/[DONE]）不改写不聚合
        assert body == SSE_FRAME_1 + SSE_FRAME_2 + SSE_FRAME_DONE
        assert len(chunks) >= 2  # 分段转发非一整块
        assert received["body"]["stream"] is True


class TestStreamFalseZeroDrift:
    """stream=False/缺省：行为与既有字节恒等（零行为漂移）。"""

    @pytest.mark.asyncio
    async def test_payload_has_no_stream_key_and_json_unchanged(self, monkeypatch):
        upstream_json = {"choices": [{"message": {"role": "assistant", "content": "pong"}}]}

        async def responder(req_body, writer):
            # 缺省 False + 显式 False 都不带 stream 键（payload 字节恒等）
            assert "stream" not in req_body
            payload = json.dumps(upstream_json).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
                + f"content-length: {len(payload)}\r\nconnection: close\r\n\r\n".encode()
            )
            writer.write(payload)

        server, base_url, received = await _start_stub(responder)
        _patch_slot(monkeypatch, base_url)
        app = _make_app()
        try:
            # ①缺省（无 stream 键）
            status, headers, body, _ = await _asgi_call(
                app, {"messages": [{"role": "user", "content": "hi"}]}
            )
            assert status == 200
            assert headers["content-type"].startswith("application/json")
            assert json.loads(body) == upstream_json
            assert "stream" not in received["body"]
            # ②显式 False
            status, headers, body, _ = await _asgi_call(
                app, {"messages": [{"role": "user", "content": "hi"}], "stream": False}
            )
            assert status == 200
            assert json.loads(body) == upstream_json
            assert "stream" not in received["body"]
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_stream_true_upstream_ignores_stream_json(self, monkeypatch):
        # 上游忽略 stream:true 回 JSON（真实存在）→ 按真实形态整段回，
        # content-type 如实告诉客户端（不硬造 SSE 帧把整段伪装成流式）
        upstream_json = {"choices": [{"message": {"role": "assistant", "content": "整段"}}]}

        async def responder(req_body, writer):
            assert req_body["stream"] is True
            payload = json.dumps(upstream_json).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
                + f"content-length: {len(payload)}\r\nconnection: close\r\n\r\n".encode()
            )
            writer.write(payload)

        server, base_url, _ = await _start_stub(responder)
        _patch_slot(monkeypatch, base_url)
        app = _make_app()
        try:
            status, headers, body, _ = await _asgi_call(
                app, {"messages": [{"role": "user", "content": "hi"}], "stream": True}
            )
            assert status == 200
            assert headers["content-type"].startswith("application/json")
            assert json.loads(body) == upstream_json
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_unknown_content_type_passthrough(self, monkeypatch):
        # 透传代理语义：非 JSON 非 SSE 形态原样透传带上游真实 content-type
        # （与 gland 聚合层"未知形态如实拒"不同——直通 API 不解析就不猜不拒）
        async def responder(req_body, writer):
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: text/plain\r\n"
                b"content-length: 7\r\nconnection: close\r\n\r\n"
            )
            writer.write(b"garbage")

        server, base_url, _ = await _start_stub(responder)
        _patch_slot(monkeypatch, base_url)
        app = _make_app()
        try:
            status, headers, body, _ = await _asgi_call(
                app, {"messages": [{"role": "user", "content": "hi"}], "stream": True}
            )
            assert status == 200
            assert headers["content-type"].startswith("text/plain")
            assert body == b"garbage"
        finally:
            server.close()
            await server.wait_closed()


class TestTypedErrors:
    """首字节前失败 typed 可见（与非流式路径同款 502/504 映射，禁静默空流）。"""

    @pytest.mark.asyncio
    async def test_upstream_http_error_502(self, monkeypatch):
        async def responder(req_body, writer):
            err = b'{"error":"boom"}'
            writer.write(
                b"HTTP/1.1 500 Internal Server Error\r\ncontent-type: application/json\r\n"
                + f"content-length: {len(err)}\r\nconnection: close\r\n\r\n".encode()
            )
            writer.write(err)

        server, base_url, _ = await _start_stub(responder)
        _patch_slot(monkeypatch, base_url)
        app = _make_app()
        try:
            status, headers, body, _ = await _asgi_call(
                app, {"messages": [{"role": "user", "content": "hi"}], "stream": True}
            )
            assert status == 502
            detail = json.loads(body)["detail"]
            assert detail.startswith("LLM API error: 500")
            assert "boom" in detail
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_upstream_unreachable_502(self, monkeypatch):
        # 关死的端口：connect 必败 → typed 502（禁裸 500/静默空流）
        import socket

        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()
        _patch_slot(monkeypatch, f"http://127.0.0.1:{dead_port}")
        app = _make_app()
        status, headers, body, _ = await _asgi_call(
            app, {"messages": [{"role": "user", "content": "hi"}], "stream": True}
        )
        assert status == 502
        assert "LLM upstream unreachable" in json.loads(body)["detail"]


class TestRequestModel:
    def test_stream_field_contract(self):
        req = LLMRequest(messages=[{"role": "user", "content": "hi"}])
        assert req.stream is False  # 缺省 False=零行为漂移
        req2 = LLMRequest(messages=[], stream=True)
        assert req2.stream is True
        req3 = LLMRequest(messages=[], stream=False)
        assert req3.stream is False
