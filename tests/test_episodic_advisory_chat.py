"""DEC-③-① episodic 回流对话面测试：相似失败经验 error → chat/ws_chat prompt。

DEC-③-① 留档候选「episodic 回流 chat/ws_chat 对话面（可另拍）」（F2b-②b
推荐回流对话面同型先例）。本文件钉住 chat（SSE 流式两路径 + 非流式）与
ws_chat 对话面：
1. 共用取数口 episodic_advisory_for（单一口径 + 异常如实抛禁静默吞）
2. chat 流式（RAG 降级路径 / RAG 命中路径）：失败教训块真进 LLM prompt
   （真 socket 上游桩请求体回读）、token 归因真记（episodic_advisory 逐行）、
   无关=零注入
3. chat 非流式：失败教训块真进 prompt
4. ws_chat：失败教训块真送达 agent prompt（真子进程 argv 回读）、取数失败
   info 帧可见且不阻断对话
试水形态 B 纪律（pending-decisions ③）：仅 failure 行 error 字段进块。
数据面全 tmp（experiences/learned_patterns 行直插），db_pool 换 tmp 走生产
适配器契约。
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
from src.learn.experience import ExperienceMemory
from src.learn.long_term import LongTermLearning
from src.services.auth import create_access_token


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


def _mk_db(tmp_path, patterns=None, failures=None, name="backflow.db") -> Path:
    """建库：learned_patterns + experiences（两轴生产 schema）+ 可选行。

    name 参数：测试内覆盖数据面时须用独立文件名（同名=与 fixture 库混播行）。
    """
    path = tmp_path / name
    learning = LongTermLearning(_AsyncDB(path), "default", "default")
    asyncio.run(learning._ensure_table())
    memory = ExperienceMemory(_AsyncDB(path), "default", "default")
    asyncio.run(memory._ensure_table())
    conn = sqlite3.connect(path)
    now = time.time()
    for rtype, key, data, conf, samples in patterns or []:
        conn.execute(
            "INSERT INTO learned_patterns (tenant_id, agent_id, pattern_type, "
            "pattern_key, pattern_data, confidence, sample_count, created_at, updated_at) "
            "VALUES ('default','default',?,?,?,?,?,?,?)",
            (rtype, key, json.dumps(data), conf, samples, now, now),
        )
    for intent, outcome, error in failures or []:
        conn.execute(
            "INSERT INTO experiences (tenant_id, agent_id, action, intent_summary, "
            "outcome, error, fix, relevance_score, created_at, metadata) "
            "VALUES ('default','default','job:seed',?,?,?,NULL,1.0,?, '{}')",
            (intent, outcome, error, now),
        )
    conn.commit()
    conn.close()
    return path


# 精确命中样例：literal_overlap("帮我查看当前目录", 同文) 达门槛
_SEEDED_FAILURE = ("帮我查看当前目录", "failure", "Permission denied: /root")
_SEEDED_SUCCESS = ("帮我查看当前目录", "success", "成功的行不进块")
_UNRELATED_FAILURE = ("部署数据库集群到生产环境", "failure", "磁盘满")
_SEEDED_PATTERN = (
    "strategy",
    "查看当前目录",
    {"success_rate": 1.0, "recommendation": "patch"},
    1.0,
    4,
)


# ── 1. chat 对话面（SSE 流式两路径 + 非流式）───────────────────────────


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
    db_path = _mk_db(tmp_path, failures=[_SEEDED_FAILURE], name="fixture.db")
    monkeypatch.setattr(pg, "db_pool", _AsyncDB(db_path))

    # 生产记忆面/路由面不属本拍：隔离为固定桩（本拍测的是失败教训注入）
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

    yield {"stub": stub, "rag": rag, "db_path": db_path}
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
    def test_degraded_path_episodic_injected(self, chat_env):
        """RAG 降级路径：失败教训块真进 LLM prompt + token 归因真记。"""
        frames = _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        body = chat_env["stub"].requests[0]
        prompt = body["messages"][0]["content"]
        assert prompt.startswith("帮我查看当前目录\n\n【历史失败提醒】"), prompt
        assert "Permission denied: /root" in prompt
        assert any(f.get("type") == "content" and "stub-answer" in f["content"] for f in frames)
        # 归因面：失败教训块逐行入账（kind=improvement，source=learn.experience）
        rec = chat_api.get_attributor().recent(1)[0]
        assert rec["usage"]["sections"].get("improvement", 0) > 0
        names = [t["name"] for t in rec["usage"]["top_consumers"]]
        assert any(n.startswith("episodic_advisory[") for n in names), names

    def test_rag_path_episodic_injected(self, chat_env):
        """RAG 命中路径：失败教训块进 prompt（在 Context 之前），模板不变。"""
        chat_env["rag"]["results"] = [{"chunk": "森林防火知识", "id": "c1", "score": 0.9}]
        _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "【历史失败提醒】" in prompt
        assert prompt.index("【历史失败提醒】") < prompt.index("Context:")
        assert "森林防火知识" in prompt
        assert "Question: 帮我查看当前目录" in prompt

    def test_zero_injection_control(self, tmp_path, monkeypatch, chat_env):
        """无关意图=零注入控制组：prompt 与修前逐字节一致（宁缺勿滥）。"""
        path = _mk_db(tmp_path, failures=[_UNRELATED_FAILURE], name="alt.db")
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        frames = _collect(chat_api.rag_stream("修复超时bug", uuid4(), 5))
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert prompt == "修复超时bug"
        assert "历史失败提醒" not in prompt
        # 真零注入≠降级空块：无任何"获取失败"info 帧
        assert not [
            f for f in frames if f.get("type") == "info" and "获取失败" in f.get("content", "")
        ], frames

    def test_learned_and_episodic_blocks_coexist(self, tmp_path, monkeypatch, chat_env):
        """learned+episodic 双块并存有序：learned 在前、episodic 在后（执行面同序）。"""
        path = _mk_db(
            tmp_path, patterns=[_SEEDED_PATTERN], failures=[_SEEDED_FAILURE], name="alt.db"
        )
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "【历史经验建议】" in prompt
        assert "【历史失败提醒】" in prompt
        assert prompt.index("【历史经验建议】") < prompt.index("【历史失败提醒】")
        # learned 块自身字节零变化（F2b-②b 既有契约）
        assert "查看当前目录" in prompt
        rec = chat_api.get_attributor().recent(1)[0]
        names = [t["name"] for t in rec["usage"]["top_consumers"]]
        assert any(n.startswith("learned_advisory[") for n in names), names
        assert any(n.startswith("episodic_advisory[") for n in names), names

    def test_episodic_fault_visible_not_blocking(self, monkeypatch, chat_env):
        """episodic 层故障：info 帧可见（禁静默）+ LLM 照常被调（不阻断）。"""
        monkeypatch.setattr(pg, "db_pool", _BrokenDB())
        frames = _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        infos = [f for f in frames if f.get("type") == "info"]
        assert any("历史失败教训获取失败" in f["content"] for f in infos), frames
        assert len(chat_env["stub"].requests) == 1  # 主路径没被 episodic 故障吃掉
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "历史失败提醒" not in prompt

    def test_success_rows_not_injected(self, tmp_path, monkeypatch, chat_env):
        """试水形态 B 纪律：success 行 error 不入块（仅 failure 行）。"""
        path = _mk_db(tmp_path, failures=[_SEEDED_SUCCESS], name="alt.db")
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert prompt == "帮我查看当前目录"
        assert "成功的行不进块" not in prompt


class TestChatNonStreamInjection:
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(chat_api.router, prefix="/api/chat")
        return TestClient(app)

    def test_nonstream_episodic_injected(self, chat_env):
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
        assert "【历史失败提醒】" in prompt
        assert "Permission denied: /root" in prompt
        assert "Question: 帮我查看当前目录" in prompt

    def test_nonstream_zero_injection_control(self, tmp_path, monkeypatch, chat_env):
        path = _mk_db(tmp_path, failures=[_UNRELATED_FAILURE], name="alt.db")
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        chat_env["rag"]["results"] = [{"chunk": "森林防火知识", "id": "c1", "score": 0.9}]
        client = self._client()
        resp = client.post(
            f"/api/chat/?user_id={uuid4()}",
            json={"question": "修复超时bug", "top_k": 5, "stream": False},
        )
        assert resp.status_code == 200
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "历史失败提醒" not in prompt


# ── 2. ws_chat 对话面（真子进程 argv 回读=注入送达证据）────────────────


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
    with client.websocket_connect(f"/api/ws/chat?token={create_access_token(uuid4())}") as ws:
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

    def test_episodic_reaches_agent_prompt(self, tmp_path, monkeypatch):
        """失败教训块真送达 agent prompt（真子进程 argv 回读），done 契约不变。"""
        self._arm_probe(monkeypatch, tmp_path)
        db_path = _mk_db(tmp_path, failures=[_SEEDED_FAILURE], name="alt.db")
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(db_path))
        frames = _ws_roundtrip("帮我查看当前目录")
        text = _done_text(frames)
        # 注入送达证据：argv 回读=首行原问题、次行起=失败教训块（stdout 行泵丢
        # 空行是传输形态非注入缺陷，精确 \n\n 形已在 chat 桩请求体逐字节证过）
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert lines[0] == "帮我查看当前目录", text
        assert lines[1].startswith("【历史失败提醒】"), text
        assert "Permission denied: /root" in text

    def test_learned_and_episodic_coexist_ws(self, tmp_path, monkeypatch):
        """双块并存：learned 在前、episodic 在后（与 chat/执行面同序）。"""
        self._arm_probe(monkeypatch, tmp_path)
        db_path = _mk_db(
            tmp_path, patterns=[_SEEDED_PATTERN], failures=[_SEEDED_FAILURE], name="alt.db"
        )
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(db_path))
        frames = _ws_roundtrip("帮我查看当前目录")
        text = _done_text(frames)
        assert text.index("【历史经验建议】") < text.index("【历史失败提醒】"), text

    def test_zero_injection_control(self, tmp_path, monkeypatch):
        self._arm_probe(monkeypatch, tmp_path)
        db_path = _mk_db(tmp_path, failures=[_UNRELATED_FAILURE], name="alt.db")
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(db_path))
        frames = _ws_roundtrip("修复超时bug")
        text = _done_text(frames)
        assert "历史失败提醒" not in text
        assert text.strip() == "修复超时bug"  # 无关=原样送达零注入
        # 真零注入≠降级空块：无任何"获取失败"info 帧
        assert not [
            f for f in frames if f.get("type") == "info" and "获取失败" in f.get("message", "")
        ], frames

    def test_episodic_fault_info_frame_not_blocking(self, tmp_path, monkeypatch):
        """episodic 层故障：info 帧可见（禁静默）+ 对话照常完成（不阻断）。"""
        self._arm_probe(monkeypatch, tmp_path)
        monkeypatch.setattr(pg, "db_pool", _BrokenDB())
        frames = _ws_roundtrip("帮我查看当前目录")
        infos = [f for f in frames if f.get("type") == "info"]
        assert any("历史失败教训获取失败" in f.get("message", "") for f in infos), frames
        text = _done_text(frames)
        assert "历史失败提醒" not in text
        assert "帮我查看当前目录" in text  # 主路径照常送达
