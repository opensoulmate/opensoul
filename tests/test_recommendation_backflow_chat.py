"""F2b-②b 推荐回流对话面测试：learned_patterns → chat/ws_chat prompt（行为优化真闭环）。

F2b-② 记档"消费面仅任务执行路径，chat/ws_chat/proactive/evolve 仍不读推荐（可另拍）"。
本文件钉住 chat（SSE 流式两路径 + 非流式）与 ws_chat 对话面：
1. 共用取数口 learned_advisory_for（单一口径 default/default + 异常如实抛禁静默吞）
2. chat 流式（RAG 降级路径 / RAG 命中路径）：建议块真进 LLM prompt（真 socket 上游
   桩请求体回读）、token 归因真记（learned_advisory 逐行）、无关=零注入
3. chat 非流式：建议块真进 prompt
4. ws_chat：建议块真送达 agent prompt（真子进程 argv 回读）、取数失败 info 帧
   可见且不阻断对话
数据面全 tmp（learned_patterns 行直插），db_pool 换 tmp 走生产适配器契约。
"""

import asyncio
import json
import socketserver
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.api.chat as chat_api
import src.api.ws_chat as ws_chat_api
import src.database.postgres as pg
from src.learn.long_term import LongTermLearning, learned_advisory_for
from src.services.auth import create_access_token
from src.services.tenant_binding import users_db_path


class _AsyncDB:
    """生产适配器注入（test_recommendation_backflow.py 同款：测试即生产路径）。"""

    def __init__(self, path: Path):
        self._path = str(path)

    async def _wrapper(self):
        import aiosqlite

        from src.database.postgres import _SQLiteConnection

        conn = await aiosqlite.connect(self._path)
        conn.row_factory = sqlite3.Row
        return _SQLiteConnection(conn), conn

    async def fetch(self, query, *args):
        wrapper, conn = await self._wrapper()
        try:
            return await wrapper.fetch(query, *args)
        finally:
            await conn.close()

    async def execute(self, query, *args):
        wrapper, conn = await self._wrapper()
        try:
            return await wrapper.execute(query, *args)
        finally:
            await conn.close()

    async def fetchval(self, query, *args):
        wrapper, conn = await self._wrapper()
        try:
            return await wrapper.fetchval(query, *args)
        finally:
            await conn.close()


class _BrokenDB:
    """学习库故障注入——任何操作都炸（验证"故障可见不吞"面）。"""

    async def fetch(self, *args, **kwargs):
        raise RuntimeError("learn-db-broken")

    async def execute(self, *args, **kwargs):
        raise RuntimeError("learn-db-broken")

    async def fetchval(self, *args, **kwargs):
        raise RuntimeError("learn-db-broken")


def _mk_db(tmp_path, patterns=None) -> Path:
    """建库：learned_patterns（LongTermLearning 生产 schema）+ 可选模式行。"""
    path = tmp_path / "learn.db"
    learning = LongTermLearning(_AsyncDB(path), "default", "default")
    asyncio.run(learning._ensure_table())
    conn = sqlite3.connect(path)
    now = time.time()
    for rtype, key, data, conf, samples in patterns or []:
        conn.execute(
            "INSERT INTO learned_patterns (tenant_id, agent_id, pattern_type, "
            "pattern_key, pattern_data, confidence, sample_count, created_at, updated_at) "
            "VALUES ('default','default',?,?,?,?,?,?,?)",
            (rtype, key, json.dumps(data), conf, samples, now, now),
        )
    conn.commit()
    conn.close()
    return path


# 精确命中样例：normalize("帮我查看当前目录") == normalize("查看当前目录")
_SEEDED = (
    "strategy",
    "查看当前目录",
    {"success_rate": 1.0, "recommendation": "patch"},
    1.0,
    4,
)
_UNRELATED = (
    "strategy",
    "background_job:hippo.dream",
    {"success_rate": 1.0, "recommendation": "patch"},
    1.0,
    111,
)


# ── 1. 共用取数口（learned_advisory_for 单一口径）────────────────────────


class TestLearnedAdvisorySeam:
    def test_seed_to_block(self, tmp_path, monkeypatch):
        path = _mk_db(tmp_path, [_SEEDED])
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        block, n = asyncio.run(learned_advisory_for("帮我查看当前目录"))
        assert "【历史经验建议】" in block
        assert "查看当前目录" in block
        assert n == 1

    def test_no_relevance_zero_injection(self, tmp_path, monkeypatch):
        path = _mk_db(tmp_path, [_UNRELATED])
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        block, n = asyncio.run(learned_advisory_for("修复超时bug"))
        assert block == ""
        assert n == 0

    def test_failure_raises_not_silent(self, monkeypatch):
        """取数口禁静默吞故障造"无推荐"假象——异常如实抛给消费方降级。"""
        monkeypatch.setattr(pg, "db_pool", _BrokenDB())
        with pytest.raises(Exception, match="learn-db-broken"):
            asyncio.run(learned_advisory_for("帮我查看当前目录"))


# ── 2. chat 对话面（SSE 流式两路径 + 非流式）───────────────────────────


class _LLMStub:
    """真 socket LLM 上游桩：请求体全录；stream 请求回 SSE，非 stream 回 JSON。"""

    def __init__(self):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n))
                outer.requests.append(body)
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(
                        b'data: {"choices": [{"delta": {"content": "stub-answer"}}]}\n\n'
                    )
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    payload = json.dumps(
                        {
                            "choices": [{"message": {"content": "stub-answer"}}],
                            "usage": {"prompt_tokens": 7},
                        }
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture()
def chat_env(tmp_path, monkeypatch):
    """真实 chat 提示装配路径 + 真取数口（tmp 生产适配器）+ 真 socket LLM 桩。"""
    stub = _LLMStub()
    learn_path = _mk_db(tmp_path, [_SEEDED])
    monkeypatch.setattr(pg, "db_pool", _AsyncDB(learn_path))

    # 生产记忆面/路由面不属本拍：隔离为固定桩（本拍测的是学习建议注入）
    monkeypatch.setattr(chat_api, "_get_memory_context", lambda q: "")

    rag: dict = {"results": []}

    async def fake_semantic_search(question, user_id, limit=5):
        return rag["results"]

    monkeypatch.setattr(chat_api, "semantic_search", fake_semantic_search)

    def fake_route(message):
        return (
            [
                {
                    "provider": "stub",
                    "base_url": stub.base_url,
                    "model": "stub-model",
                    "api_key": "",
                }
            ],
            {"mode": "stub"},
        )

    monkeypatch.setattr(chat_api, "_route_llm_targets", fake_route)
    # 路由决策记账是旁路观测面（写生产决策库），本拍隔离为 no-op
    monkeypatch.setattr(chat_api, "_route_decision_begin", lambda *a, **k: "")
    monkeypatch.setattr(chat_api, "_route_decision_end", lambda *a, **k: None)

    yield {"stub": stub, "rag": rag, "learn_path": learn_path}
    stub.stop()


def _collect(coro_gen) -> list[dict]:
    """跑 async 生成器收 SSE 帧。"""

    async def _run():
        frames = []
        async for raw in coro_gen:
            for part in raw.strip().split("\n\n"):
                if part.startswith("data: ") and part[6:].strip() != "[DONE]":
                    frames.append(json.loads(part[6:]))
        return frames

    return asyncio.run(_run())


class TestChatStreamInjection:
    def test_degraded_path_advisory_injected(self, chat_env):
        """RAG 降级路径：建议块真进 LLM prompt + token 归因真记。"""
        frames = _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        body = chat_env["stub"].requests[0]
        prompt = body["messages"][0]["content"]
        assert prompt.startswith("帮我查看当前目录\n\n【历史经验建议】"), prompt
        assert "查看当前目录" in prompt
        assert any(f.get("type") == "content" and "stub-answer" in f["content"] for f in frames)
        # 归因面：建议块逐行入账（kind=improvement，source=learn.long_term）
        rec = chat_api.get_attributor().recent(1)[0]
        assert rec["usage"]["sections"].get("improvement", 0) > 0
        names = [t["name"] for t in rec["usage"]["top_consumers"]]
        assert any(n.startswith("learned_advisory[") for n in names), names

    def test_rag_path_advisory_injected(self, chat_env):
        """RAG 命中路径：建议块进 prompt（在 Context 之前），上下文模板不变。"""
        chat_env["rag"]["results"] = [{"chunk": "森林防火知识", "id": "c1", "score": 0.9}]
        _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "【历史经验建议】" in prompt
        assert prompt.index("【历史经验建议】") < prompt.index("Context:")
        assert "森林防火知识" in prompt
        assert "Question: 帮我查看当前目录" in prompt

    def test_no_relevance_zero_injection_control(self, tmp_path, monkeypatch, chat_env):
        """无关意图=零注入控制组：prompt 与修前逐字节一致（宁缺勿滥）。"""
        path = _mk_db(tmp_path, [_UNRELATED])
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        _collect(chat_api.rag_stream("修复超时bug", uuid4(), 5))
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert prompt == "修复超时bug"
        assert "历史经验建议" not in prompt

    def test_learning_failure_visible_not_blocking(self, monkeypatch, chat_env):
        """学习层故障：info 帧可见（禁静默）+ LLM 照常被调（不阻断）。"""
        monkeypatch.setattr(pg, "db_pool", _BrokenDB())
        frames = _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        infos = [f for f in frames if f.get("type") == "info"]
        assert any("学习建议获取失败" in f["content"] for f in infos), frames
        assert len(chat_env["stub"].requests) == 1  # 主路径没被学习层故障吃掉
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "历史经验建议" not in prompt


class TestChatNonStreamInjection:
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(chat_api.router, prefix="/api/chat")
        return TestClient(app)

    def test_nonstream_advisory_injected(self, chat_env):
        # 非流式路径无 RAG 降级分支（无结果=直接早退），须给检索结果才到 prompt 装配
        chat_env["rag"]["results"] = [{"chunk": "森林防火知识", "id": "c1", "score": 0.9}]
        client = self._client()
        resp = client.post(
            f"/api/chat/?user_id={uuid4()}",
            json={"question": "帮我查看当前目录", "top_k": 5, "stream": False},
        )
        assert resp.status_code == 200
        assert resp.json()["answer"] == "stub-answer"
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "【历史经验建议】" in prompt
        assert "Question: 帮我查看当前目录" in prompt

    def test_nonstream_zero_injection_control(self, tmp_path, monkeypatch, chat_env):
        path = _mk_db(tmp_path, [_UNRELATED])
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        chat_env["rag"]["results"] = [{"chunk": "森林防火知识", "id": "c1", "score": 0.9}]
        client = self._client()
        resp = client.post(
            f"/api/chat/?user_id={uuid4()}",
            json={"question": "修复超时bug", "top_k": 5, "stream": False},
        )
        assert resp.status_code == 200
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "历史经验建议" not in prompt


# ── 3. ws_chat 对话面（真子进程 argv 回读=注入送达证据）────────────────


@pytest.fixture(autouse=True)
def _ws_identity_env(monkeypatch, tmp_path):
    """DEC-⑭-④ 判口换 bind_identity 后 ws 握手要求真实活跃身份行：身份真源归
    tmp（USERS_DB/ENTERPRISE_DB），绝不写生产库。"""
    users_db = tmp_path / "users.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(users_db) as c:
        c.execute(
            "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, "
            "is_active INTEGER NOT NULL DEFAULT 1, "
            "tenant_id TEXT NOT NULL DEFAULT 'default')"
        )
        c.commit()


def _ws_token() -> str:
    uid = uuid4()
    with sqlite3.connect(users_db_path()) as c:
        c.execute(
            "INSERT OR REPLACE INTO users (id, username, is_active, tenant_id) "
            "VALUES (?, ?, 1, 'default')",
            (str(uid), f"user-{uid}"),
        )
        c.commit()
    return create_access_token(uid)


def _ws_client() -> TestClient:
    app = FastAPI()
    app.include_router(ws_chat_api.router, prefix="/api/ws")
    return TestClient(app)


# 探针脚本：把收到的 prompt（argv[1]）原样回显——argv 回读=真送达证据
ECHO_ARGV_SCRIPT = """
import sys
print(sys.argv[1], flush=True)
"""


def _ws_roundtrip(text: str) -> list[dict]:
    frames: list[dict] = []
    client = _ws_client()
    with client.websocket_connect(f"/api/ws/chat?token={_ws_token()}") as ws:
        ws.send_text(
            json.dumps(
                {"type": "message", "text": text, "mode": "agent_proxy", "agent_id": "python3"}
            )
        )
        for _ in range(100):
            frame = json.loads(ws.receive_text())
            frames.append(frame)
            if frame.get("type") in ("done", "error"):
                break
    return frames


def _done_text(frames: list[dict]) -> str:
    done = [f for f in frames if f.get("type") == "done"]
    assert done, frames
    return done[0]["text"]


class TestWsChatInjection:
    def _arm_probe(self, monkeypatch, tmp_path) -> None:
        p = tmp_path / "echo_argv.py"
        p.write_text(ECHO_ARGV_SCRIPT, encoding="utf-8")
        monkeypatch.setitem(ws_chat_api.CLI_ARGS, "python3", ["-u", str(p)])

    def test_advisory_reaches_agent_prompt(self, tmp_path, monkeypatch):
        """建议块真送达 agent prompt（真子进程 argv 回读），done 契约不变。"""
        self._arm_probe(monkeypatch, tmp_path)
        learn_path = _mk_db(tmp_path, [_SEEDED])
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(learn_path))
        frames = _ws_roundtrip("帮我查看当前目录")
        text = _done_text(frames)
        # 注入送达证据：argv 回读=首行原问题、次行起=建议块（stdout 行泵丢空行
        # 是传输形态非注入缺陷，精确 \n\n 形已在 chat 桩请求体逐字节证过）
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert lines[0] == "帮我查看当前目录", text
        assert lines[1].startswith("【历史经验建议】"), text
        assert "查看当前目录" in text

    def test_no_relevance_zero_injection_control(self, tmp_path, monkeypatch):
        self._arm_probe(monkeypatch, tmp_path)
        learn_path = _mk_db(tmp_path, [_UNRELATED])
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(learn_path))
        frames = _ws_roundtrip("修复超时bug")
        text = _done_text(frames)
        assert "历史经验建议" not in text
        assert text.strip() == "修复超时bug"  # 无关=原样送达零注入

    def test_learning_failure_info_frame_not_blocking(self, tmp_path, monkeypatch):
        """学习层故障：info 帧可见（禁静默）+ 对话照常完成（不阻断）。"""
        self._arm_probe(monkeypatch, tmp_path)
        monkeypatch.setattr(pg, "db_pool", _BrokenDB())
        frames = _ws_roundtrip("帮我查看当前目录")
        infos = [f for f in frames if f.get("type") == "info"]
        assert any("学习建议获取失败" in f.get("message", "") for f in infos), frames
        text = _done_text(frames)
        assert "历史经验建议" not in text
        assert "帮我查看当前目录" in text  # 主路径照常送达
