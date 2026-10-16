"""G1-④b 元认知纠错回流对话面测试：blind_spots/calibration → chat/ws_chat prompt。

G1-④a 留档候选「G1-④b 对话面（chat/ws_chat）」（F2b-②b 推荐回流对话面 /
DEC-③-①b episodic 回流对话面同型先例）。本文件钉住：
1. chat 流式（RAG 降级路径 / RAG 命中路径）：纠偏块真进 LLM prompt（真 socket
   上游桩请求体回读）、token 归因真记（metacognitive_advisory 逐行）、
   空观测=零注入
2. chat 非流式：纠偏块真进 prompt
3. 三块并存序稳定（learned→episodic→metacognitive，与执行面同序）
4. ws_chat：纠偏块真送达 agent prompt（真子进程 argv 回读）、口径=对话对象
   agent（跨 agent 不冒用）、取数失败 info 帧可见且不阻断对话
数据面全 tmp（metacognition_log 走生产写点符号落账），db_pool 换 tmp 走
生产适配器契约（test_episodic_advisory_chat.py 同款）。
"""

import asyncio
import inspect
import json
import socketserver
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
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
from src.mirror.metacognition import Metacognition
from src.services.auth import create_access_token
from src.services.tenant_binding import users_db_path


class _AsyncDB:
    """生产适配器注入（test_episodic_advisory_chat.py 同款：测试即生产路径）。"""

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
    """元认知/学习库故障注入——任何操作都炸（验证"故障可见不吞"面）。"""

    async def fetch(self, *args, **kwargs):
        raise RuntimeError("meta-db-broken")

    async def execute(self, *args, **kwargs):
        raise RuntimeError("meta-db-broken")

    async def fetchval(self, *args, **kwargs):
        raise RuntimeError("meta-db-broken")


def _mk_db(
    tmp_path,
    patterns=None,
    failures=None,
    meta_rows=None,
    meta_agent="default",
    name="backflow.db",
) -> Path:
    """建库：learned_patterns + experiences + metacognition_log（三轴生产 schema）
    + 可选行。metacognition 行走生产写点符号落账（Metacognition.log_decision/
    log_outcome，与 think/verify 双写点同真源），写→读闭环非裸 SQL 造数。

    name 参数：测试内覆盖数据面时须用独立文件名（同名=与 fixture 库混播行）。
    meta_rows=[{kind: decision|outcome, decision, outcome/confidence, ref}]，
    按序落账（校准配对按 (created_at,id) 因果序，decision 须先于其 outcome）。
    """
    path = tmp_path / name
    learning = LongTermLearning(_AsyncDB(path), "default", "default")
    asyncio.run(learning._ensure_table())
    memory = ExperienceMemory(_AsyncDB(path), "default", "default")
    asyncio.run(memory._ensure_table())
    meta = Metacognition(_AsyncDB(path), "default", meta_agent)
    for row in meta_rows or []:
        if row["kind"] == "outcome":
            asyncio.run(
                meta.log_outcome(
                    decision=row["decision"],
                    outcome=row["outcome"],
                    ref=row.get("ref", ""),
                )
            )
        else:
            asyncio.run(
                meta.log_decision(
                    decision=row["decision"],
                    reasoning=row.get("reasoning", ""),
                    confidence=row["confidence"],
                    ref=row.get("ref", ""),
                )
            )
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


# 盲区样例：同 decision 失败 >=2 次（get_blind_spots 门槛）
_BLIND_AREA = "e2e盲区动作域"
# 校准样例：decision 0.9 + 同 ref 成败 failure → 配对 avg 0.9 vs actual 0.0 → 偏高
_META_BLIND_MISALIGNED = [
    {"kind": "outcome", "decision": _BLIND_AREA, "outcome": "failure"},
    {"kind": "outcome", "decision": _BLIND_AREA, "outcome": "failure"},
    {"kind": "decision", "decision": _BLIND_AREA, "confidence": 0.9, "ref": "r1"},
    {"kind": "outcome", "decision": _BLIND_AREA, "outcome": "failure", "ref": "r1"},
]
# 校准 good 样例：decision 0.9 + 同 ref success → avg 0.9 vs actual 1.0 → 不出行
_META_BLIND_GOOD_CALIB = [
    {"kind": "outcome", "decision": _BLIND_AREA, "outcome": "failure"},
    {"kind": "outcome", "decision": _BLIND_AREA, "outcome": "failure"},
    {"kind": "decision", "decision": "规划决策域B", "confidence": 0.9, "ref": "rb"},
    {"kind": "outcome", "decision": "规划决策域B", "outcome": "success", "ref": "rb"},
]
_SEEDED_PATTERN = (
    "strategy",
    "查看当前目录",
    {"success_rate": 1.0, "recommendation": "patch"},
    1.0,
    4,
)
_SEEDED_FAILURE = ("帮我查看当前目录", "failure", "Permission denied: /root")


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
    db_path = _mk_db(tmp_path, meta_rows=_META_BLIND_MISALIGNED, name="fixture.db")
    monkeypatch.setattr(pg, "db_pool", _AsyncDB(db_path))

    # 生产记忆面/路由面不属本拍：隔离为固定桩（本拍测的是元认知纠偏注入）
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
    def test_degraded_path_metacognitive_injected(self, chat_env):
        """RAG 降级路径：纠偏块真进 LLM prompt + token 归因真记。"""
        frames = _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        body = chat_env["stub"].requests[0]
        prompt = body["messages"][0]["content"]
        assert prompt.startswith("帮我查看当前目录\n\n【元认知纠偏提醒】"), prompt
        assert "历史上反复失败" in prompt
        assert "自评信心偏高" in prompt
        assert "以任务要求为准" in prompt
        assert any(f.get("type") == "content" and "stub-answer" in f["content"] for f in frames)
        # 归因面：纠偏块逐行入账（kind=improvement，source=mirror.metacognition）
        rec = chat_api.get_attributor().recent(1)[0]
        assert rec["usage"]["sections"].get("improvement", 0) > 0
        names = [t["name"] for t in rec["usage"]["top_consumers"]]
        assert any(n.startswith("metacognitive_advisory[") for n in names), names

    def test_rag_path_metacognitive_injected(self, chat_env):
        """RAG 命中路径：纠偏块进 prompt（在 Context 之前），模板不变。"""
        chat_env["rag"]["results"] = [{"chunk": "森林防火知识", "id": "c1", "score": 0.9}]
        _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "【元认知纠偏提醒】" in prompt
        assert prompt.index("【元认知纠偏提醒】") < prompt.index("Context:")
        assert "森林防火知识" in prompt
        assert "Question: 帮我查看当前目录" in prompt

    def test_zero_injection_control(self, tmp_path, monkeypatch, chat_env):
        """空观测=零注入控制组：prompt 与修前逐字节一致（宁缺勿滥）。"""
        path = _mk_db(tmp_path, name="alt.db")
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        frames = _collect(chat_api.rag_stream("修复超时bug", uuid4(), 5))
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert prompt == "修复超时bug"
        assert "元认知纠偏提醒" not in prompt
        # 真零注入≠降级空块：无任何"获取失败"info 帧
        assert not [
            f for f in frames if f.get("type") == "info" and "获取失败" in f.get("content", "")
        ], frames

    def test_three_blocks_order_stable(self, tmp_path, monkeypatch, chat_env):
        """三块并存有序：learned→episodic→metacognitive（执行面 G1-④a 同序）。"""
        path = _mk_db(
            tmp_path,
            patterns=[_SEEDED_PATTERN],
            failures=[_SEEDED_FAILURE],
            meta_rows=_META_BLIND_MISALIGNED,
            name="alt.db",
        )
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "【历史经验建议】" in prompt
        assert "【历史失败提醒】" in prompt
        assert "【元认知纠偏提醒】" in prompt
        assert (
            prompt.index("【历史经验建议】")
            < prompt.index("【历史失败提醒】")
            < prompt.index("【元认知纠偏提醒】")
        ), prompt
        rec = chat_api.get_attributor().recent(1)[0]
        names = [t["name"] for t in rec["usage"]["top_consumers"]]
        assert any(n.startswith("learned_advisory[") for n in names), names
        assert any(n.startswith("episodic_advisory[") for n in names), names
        assert any(n.startswith("metacognitive_advisory[") for n in names), names

    def test_metacognitive_fault_visible_not_blocking(self, monkeypatch, chat_env):
        """元认知层故障：info 帧可见（禁静默）+ LLM 照常被调（不阻断）。"""
        monkeypatch.setattr(pg, "db_pool", _BrokenDB())
        frames = _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        infos = [f for f in frames if f.get("type") == "info"]
        assert any("元认知纠偏提醒获取失败" in f["content"] for f in infos), frames
        assert len(chat_env["stub"].requests) == 1  # 主路径没被元认知故障吃掉
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "元认知纠偏提醒" not in prompt

    def test_good_calibration_no_calibration_line(self, tmp_path, monkeypatch, chat_env):
        """校准 good=如实无校准行（盲区行照出）：G1-④a 渲染语义对话面零漂移。"""
        path = _mk_db(tmp_path, meta_rows=_META_BLIND_GOOD_CALIB, name="alt.db")
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "【元认知纠偏提醒】" in prompt
        assert "历史上反复失败" in prompt
        assert "自评信心" not in prompt

    def test_chat_scope_pins_default_agent(self, tmp_path, monkeypatch, chat_env):
        """chat 面口径钉 ('default','default')：其他 agent 的观测不进本面
        （每 agent 独立禁跨 agent 冒用聚合）。"""
        path = _mk_db(
            tmp_path, meta_rows=_META_BLIND_MISALIGNED, meta_agent="soulmate", name="alt.db"
        )
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        _collect(chat_api.rag_stream("帮我查看当前目录", uuid4(), 5))
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert prompt == "帮我查看当前目录"
        assert "元认知纠偏提醒" not in prompt


class TestChatNonStreamInjection:
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(chat_api.router, prefix="/api/chat")
        return TestClient(app)

    def test_nonstream_metacognitive_injected(self, chat_env):
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
        assert "【元认知纠偏提醒】" in prompt
        assert "历史上反复失败" in prompt
        assert "Question: 帮我查看当前目录" in prompt

    def test_nonstream_zero_injection_control(self, tmp_path, monkeypatch, chat_env):
        path = _mk_db(tmp_path, name="alt.db")
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(path))
        chat_env["rag"]["results"] = [{"chunk": "森林防火知识", "id": "c1", "score": 0.9}]
        client = self._client()
        resp = client.post(
            f"/api/chat/?user_id={uuid4()}",
            json={"question": "修复超时bug", "top_k": 5, "stream": False},
        )
        assert resp.status_code == 200
        prompt = chat_env["stub"].requests[0]["messages"][0]["content"]
        assert "元认知纠偏提醒" not in prompt


# ── 2. ws_chat 对话面（真子进程 argv 回读=注入送达证据）────────────────


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


def _ws_roundtrip(text: str, agent_id: str | None = "python3") -> list[dict]:
    frames: list[dict] = []
    client = _ws_client()
    with client.websocket_connect(f"/api/ws/chat?token={_ws_token()}") as ws:
        ws.send_text(
            json.dumps(
                {"type": "message", "text": text, "mode": "agent_proxy", "agent_id": agent_id}
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

    def test_metacognitive_reaches_agent_prompt(self, tmp_path, monkeypatch):
        """纠偏块真送达 agent prompt（真子进程 argv 回读），done 契约不变。"""
        self._arm_probe(monkeypatch, tmp_path)
        # ws 面口径=对话对象 agent（agent_id="python3"），行落其名下
        db_path = _mk_db(
            tmp_path, meta_rows=_META_BLIND_MISALIGNED, meta_agent="python3", name="alt.db"
        )
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(db_path))
        frames = _ws_roundtrip("帮我查看当前目录")
        text = _done_text(frames)
        # 注入送达证据：argv 回读=首行原问题、次行起=纠偏块（stdout 行泵丢
        # 空行是传输形态非注入缺陷，精确 \n\n 形已在 chat 桩请求体逐字节证过）
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert lines[0] == "帮我查看当前目录", text
        assert lines[1].startswith("【元认知纠偏提醒】"), text
        assert "历史上反复失败" in text

    def test_three_blocks_order_ws(self, tmp_path, monkeypatch):
        """三块并存：learned→episodic→metacognitive（与 chat/执行面同序）。"""
        self._arm_probe(monkeypatch, tmp_path)
        db_path = _mk_db(
            tmp_path,
            patterns=[_SEEDED_PATTERN],
            failures=[_SEEDED_FAILURE],
            meta_rows=_META_BLIND_MISALIGNED,
            meta_agent="python3",
            name="alt.db",
        )
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(db_path))
        frames = _ws_roundtrip("帮我查看当前目录")
        text = _done_text(frames)
        assert (
            text.index("【历史经验建议】")
            < text.index("【历史失败提醒】")
            < text.index("【元认知纠偏提醒】")
        ), text

    def test_ws_scope_per_agent_no_cross_injection(self, tmp_path, monkeypatch):
        """每 agent 独立禁跨 agent 冒用：他 agent 的观测不进本 agent 对话。"""
        self._arm_probe(monkeypatch, tmp_path)
        db_path = _mk_db(
            tmp_path, meta_rows=_META_BLIND_MISALIGNED, meta_agent="soulmate", name="alt.db"
        )
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(db_path))
        frames = _ws_roundtrip("帮我查看当前目录")
        text = _done_text(frames)
        assert "元认知纠偏提醒" not in text
        assert text.strip() == "帮我查看当前目录"  # 零注入=原样送达

    def test_ws_scope_missing_agent_defaults(self, tmp_path, monkeypatch):
        """消息缺 agent_id → 'default' 兜底不掉落（③b-2 旁路纪律）：判定口
        缺失/空白一律归 'default'，'default' 名下观测照常送达。
        （走判定口直测：agent_proxy 路由要求真值 agent_id，缺 agent_id 的消息
        落 ACP 分支=另一条路由，与本口径无关）"""
        db_path = _mk_db(tmp_path, meta_rows=_META_BLIND_MISALIGNED, name="alt.db")
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(db_path))
        for missing in (None, "", "  "):
            block, failed = asyncio.run(ws_chat_api._get_metacognitive_advisory(missing))
            assert not failed, missing
            assert "【元认知纠偏提醒】" in block, missing
            assert "历史上反复失败" in block, missing
        # 静态接线：消息 handler 真把 agent_id 传给判定口（写了≠接线了）
        assert "_get_metacognitive_advisory(agent_id)" in inspect.getsource(ws_chat_api)

    def test_zero_injection_control(self, tmp_path, monkeypatch):
        self._arm_probe(monkeypatch, tmp_path)
        db_path = _mk_db(tmp_path, name="alt.db")
        monkeypatch.setattr(pg, "db_pool", _AsyncDB(db_path))
        frames = _ws_roundtrip("修复超时bug")
        text = _done_text(frames)
        assert "元认知纠偏提醒" not in text
        assert text.strip() == "修复超时bug"  # 空观测=原样送达零注入
        # 真零注入≠降级空块：无任何"获取失败"info 帧
        assert not [
            f for f in frames if f.get("type") == "info" and "获取失败" in f.get("message", "")
        ], frames

    def test_metacognitive_fault_info_frame_not_blocking(self, tmp_path, monkeypatch):
        """元认知层故障：info 帧可见（禁静默）+ 对话照常完成（不阻断）。"""
        self._arm_probe(monkeypatch, tmp_path)
        monkeypatch.setattr(pg, "db_pool", _BrokenDB())
        frames = _ws_roundtrip("帮我查看当前目录")
        infos = [f for f in frames if f.get("type") == "info"]
        assert any("元认知纠偏提醒获取失败" in f.get("message", "") for f in infos), frames
        text = _done_text(frames)
        assert "元认知纠偏提醒" not in text
        assert "帮我查看当前目录" in text  # 主路径照常送达
