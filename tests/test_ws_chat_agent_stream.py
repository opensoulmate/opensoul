"""F4b-③ /ws/chat agent_proxy on_event 接线：逐行帧真推 WS（接线了≠跑通了）。

红线：stdout 响应行走 chunk（逐块拼接即响应，与 ACP chunk 同消费语义），
stderr/tool_call/heartbeat 过程事件走 agent_event（不混入响应文本），done 契约不变。
全部走真实子进程 + 真 WS 往返（非 mock）。增量证明用双向门闩：上游（agent 脚本）
发出第一行后等客户端确认收到才发第二行——端点若缓冲/伪流式则 first 帧永不触发→超时失败。
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.ws_chat as ws_chat_api
from src.services.auth import create_access_token
from src.services.tenant_binding import users_db_path

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, "
    "is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)


@pytest.fixture(autouse=True)
def _identity_env(monkeypatch, tmp_path):
    """DEC-⑭-④ 判口换 bind_identity 后握手要求真实活跃身份行：身份真源归 tmp
    （USERS_DB/ENTERPRISE_DB），绝不写生产库。"""
    users_db = tmp_path / "users.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()


def _ws_client() -> TestClient:
    app = FastAPI()
    app.include_router(ws_chat_api.router, prefix="/api/ws")
    return TestClient(app)


def _token() -> str:
    uid = uuid4()
    with sqlite3.connect(users_db_path()) as c:
        c.execute(
            "INSERT OR REPLACE INTO users (id, username, is_active, tenant_id) "
            "VALUES (?, ?, 1, 'default')",
            (str(uid), f"user-{uid}"),
        )
        c.commit()
    return create_access_token(uid)


# 脚本 1：双向门闩真流式证明——发第一行后必须等门闩文件出现才发第二行
GATED_SCRIPT = """
import os, sys, time
print("stream-first-line", flush=True)
gate = {gate!r}
for _ in range(400):
    if os.path.exists(gate):
        break
    time.sleep(0.05)
print("stream-second-line", flush=True)
"""

# 脚本 2：事件分类面——stdout 响应行 / stderr 纯行 / stderr 工具活动行 / 心跳
EVENT_SCRIPT = """
import sys, time
print("resp-body-line", flush=True)
print("plain-stderr-line", file=sys.stderr, flush=True)
print("\\u23cd Running tool foo", file=sys.stderr, flush=True)
time.sleep(0.5)
print("resp-tail-line", flush=True)
"""

# 脚本 3：失败路径不回归——零 stdout、stderr 有内容、非零退出
FAIL_SCRIPT = """
import sys
print("boom-err-detail", file=sys.stderr, flush=True)
sys.exit(3)
"""


def _roundtrip(frames, error, gate=None):
    """完整 WS 往返：连上→发消息→收帧直到 done/error。异常如实回传给断言。"""
    try:
        client = _ws_client()
        with client.websocket_connect(f"/api/ws/chat?token={_token()}") as ws:
            ws.send_text(
                json.dumps(
                    {
                        "type": "message",
                        "text": "probe-msg",
                        "mode": "agent_proxy",
                        "agent_id": "python3",
                    }
                )
            )
            for _ in range(200):
                raw = ws.receive_text()
                frame = json.loads(raw)
                frames.append((time.monotonic(), frame))
                ftype = frame.get("type")
                if (
                    gate is not None
                    and ftype == "chunk"
                    and "stream-first-line" in frame.get("text", "")
                ):
                    # 客户端确认收到第一行 → 开门闩（端点若缓冲到进程结束，此帧根本到不了）
                    Path(gate).write_text("open", encoding="utf-8")
                if ftype in ("done", "error"):
                    break
    except Exception as exc:  # noqa: BLE001 —— 捕获后如实回传给断言
        error.append(repr(exc))


def _run(script_text, monkeypatch, tmp_path, gate_name=None, heartbeat_s=None):
    """装探针脚本→跑往返→返回 (frames, error)。"""
    p = tmp_path / "probe.py"
    p.write_text(script_text, encoding="utf-8")
    monkeypatch.setitem(ws_chat_api.CLI_ARGS, "python3", ["-u", str(p)])
    monkeypatch.setattr(ws_chat_api, "_PROXY_HEARTBEAT_S", heartbeat_s or 30.0)
    gate = str(tmp_path / gate_name) if gate_name else None
    frames, error = [], []
    t = threading.Thread(target=_roundtrip, args=(frames, error, gate), daemon=True)
    t.start()
    t.join(timeout=25)
    assert not t.is_alive(), f"WS roundtrip hung（帧数={len(frames)}）——流式断点或门闩死锁"
    return frames, error


def _by_type(frames, ftype):
    return [f for _, f in frames if f.get("type") == ftype]


def test_agent_proxy_streams_stdout_lines_as_chunk_frames(monkeypatch, tmp_path):
    """逐行流式硬证明（双向门闩）：第一行 chunk 到达客户端才放行第二行，
    端点若把输出攒到进程结束再发（缓冲/伪流式）则门闩永不触发→测试超时失败。"""
    script = GATED_SCRIPT.format(gate=str(tmp_path / "gate.latch"))
    frames, error = _run(script, monkeypatch, tmp_path, gate_name="gate.latch")

    assert not error, f"roundtrip error: {error}"
    chunks = _by_type(frames, "chunk")
    chunk_text = "".join(c.get("text", "") for c in chunks)
    assert "stream-first-line" in chunk_text
    assert "stream-second-line" in chunk_text
    # 每行独立成帧（逐行帧非整段）
    first_idx = next(i for i, c in enumerate(chunks) if "stream-first-line" in c["text"])
    second_idx = next(i for i, c in enumerate(chunks) if "stream-second-line" in c["text"])
    assert first_idx < second_idx, f"两行未分帧/顺序错: {chunks}"
    # 增量到达硬证据：第一行帧必须早于第二行帧到达（门闩下两帧相差>=一次往返）
    first_ts = [
        ts
        for ts, f in frames
        if f.get("type") == "chunk" and "stream-first-line" in f.get("text", "")
    ][0]
    second_ts = [
        ts
        for ts, f in frames
        if f.get("type") == "chunk" and "stream-second-line" in f.get("text", "")
    ][0]
    assert second_ts > first_ts, "两行同刻到达=缓冲整段伪流式"
    # done 契约不变：完整响应仍以 done 收尾
    dones = _by_type(frames, "done")
    assert len(dones) == 1
    assert "stream-first-line" in dones[0]["text"] and "stream-second-line" in dones[0]["text"]
    assert dones[0]["source"] == "python3"
    # done 必须在所有 chunk 之后（流式先行、终帧收尾）
    done_ts = [ts for ts, f in frames if f.get("type") == "done"][0]
    assert done_ts >= second_ts


def test_agent_event_frames_classified_and_chunk_stays_response_only(monkeypatch, tmp_path):
    """过程事件走 agent_event 如实分类（stderr/tool_call/heartbeat），
    chunk 只承载 stdout 响应文本（不混入 stderr/心跳=chunk 语义保护）。"""
    frames, error = _run(EVENT_SCRIPT, monkeypatch, tmp_path, heartbeat_s=0.15)

    assert not error, f"roundtrip error: {error}"
    events = _by_type(frames, "agent_event")
    by_event: dict[str, list[str]] = {}
    for e in events:
        by_event.setdefault(e.get("event", ""), []).append(e.get("text", ""))
    assert any("plain-stderr-line" in t for t in by_event.get("stderr", [])), events
    assert any("Running tool foo" in t for t in by_event.get("tool_call", [])), events
    assert any("执行进行中" in t for t in by_event.get("heartbeat", [])), events

    chunks = _by_type(frames, "chunk")
    chunk_text = "".join(c.get("text", "") for c in chunks)
    assert "resp-body-line" in chunk_text
    # chunk=响应文本：stderr 行/工具行/心跳绝不混入
    for leak in ("plain-stderr-line", "Running tool foo", "执行进行中"):
        assert leak not in chunk_text, f"{leak!r} 泄入 chunk: {chunks}"

    dones = _by_type(frames, "done")
    assert len(dones) == 1
    assert "resp-body-line" in dones[0]["text"]


def test_failure_path_still_reports_honestly(monkeypatch, tmp_path):
    """失败路径不回归：零 stdout + stderr 有内容 + 非零退出 → done 如实带 stderr 内容。"""
    frames, error = _run(FAIL_SCRIPT, monkeypatch, tmp_path)

    assert not error, f"roundtrip error: {error}"
    dones = _by_type(frames, "done")
    assert len(dones) == 1, frames
    assert "boom-err-detail" in dones[0]["text"]
    assert dones[0]["source"] == "python3"
    # stdout 无输出时无空 chunk 帧（run_agent_proxy 空行 continue，语义保持）
    chunks = _by_type(frames, "chunk")
    assert all(c.get("text", "").strip() for c in chunks), chunks
