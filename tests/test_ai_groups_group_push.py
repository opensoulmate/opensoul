"""_notify_group_chat 群聊实时推送（周期#81 · 13.4 验收行4根因修复）。

背景：任务链"每步结果回群聊"此前只落 discussion_messages + task_progress 时间线帧，
聊天流前端只认 agent_message 帧 → 群里实时看不到 Agent 回复。
opensoul ai_groups.GROUP_MSG_PUSH 常量此前为死常量（零调用点）+ acp-proxy 无对应
端点。修复=常量真实消费（POST 到 ws_group /internal/group-message 广播
agent_message 帧），落库仍以 _notify_group_chat 直写为单一真源（禁双写）。

覆盖：
1. 落库：discussion_messages agent_reply 行 + task_execution_events 事件行
2. 推送：真实 HTTP POST 到 GROUP_MSG_PUSH（真实捕获服务器），body 契约齐全
3. 长 content 推送截断 2000（与事件行同口径）
4. 旁路纪律：推送失败（死端口）只记日志，落库照常、不抛异常
"""

import asyncio
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).parent.parent


def _tmp_get_db_factory(db_path: Path):
    def _get_db():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    return _get_db


@pytest.fixture()
def group_env(monkeypatch, tmp_path):
    """src.api.ai_groups 延迟 import + 数据面/推送面隔离（不摸生产库不发真实网络）。"""
    import src.api.ai_groups as ai_groups

    db_path = tmp_path / "ai_groups.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE discussion_messages (
            id TEXT, group_id TEXT, task_id TEXT, agent_id TEXT, agent_name TEXT,
            intent TEXT, content TEXT, metadata TEXT, round_num INTEGER, created_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE task_execution_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT, task_id TEXT,
            agent_id TEXT, event_type TEXT, content TEXT, created_at TEXT)"""
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(ai_groups, "get_db", _tmp_get_db_factory(db_path))

    received: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            received.append(self.rfile.read(length))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *args):  # 静音
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    monkeypatch.setattr(
        ai_groups, "GROUP_MSG_PUSH", f"http://127.0.0.1:{port}/internal/group-message"
    )
    yield ai_groups, db_path, received
    server.shutdown()


def test_notify_persists_and_pushes(group_env):
    import json

    ai_groups, db_path, received = group_env
    asyncio.run(ai_groups._notify_group_chat("g-p1", "soulmate", "SoulMate", "步骤结果X", "t-p1"))
    # 落库单一真源：agent_reply 行 + 事件行
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT group_id, task_id, agent_id, intent, content FROM discussion_messages"
    ).fetchall()
    events = conn.execute("SELECT event_type, content FROM task_execution_events").fetchall()
    conn.close()
    assert rows == [("g-p1", "t-p1", "soulmate", "agent_reply", "步骤结果X")]
    assert ("agent_reply", "步骤结果X") in events
    # 推送真实发出且 body 契约齐全
    assert len(received) == 1
    body = json.loads(received[0])
    assert body["group_id"] == "g-p1"
    assert body["agent_id"] == "soulmate"
    assert body["agent_name"] == "SoulMate"
    assert body["content"] == "步骤结果X"
    assert body["task_id"] == "t-p1"
    assert body["intent"] == "agent_reply"
    # 单一写入源纪律：推送不带 persist（禁双写）
    assert "persist" not in body


def test_push_content_truncated_2000(group_env):
    import json

    ai_groups, db_path, received = group_env
    long_content = "长" * 3000
    asyncio.run(ai_groups._notify_group_chat("g-p2", "mimo", "MiMo", long_content, "t-p2"))
    body = json.loads(received[0])
    assert len(body["content"]) == 2000
    # 落库侧原样保存完整内容（单一真源不截断），截断只在推送/事件面
    conn = sqlite3.connect(str(db_path))
    (content,) = conn.execute("SELECT content FROM discussion_messages").fetchone()
    conn.close()
    assert content == long_content


def test_push_failure_is_bypass(group_env, monkeypatch):
    """旁路纪律：推送失败只记日志，落库照常、不抛。"""
    ai_groups, db_path, received = group_env
    monkeypatch.setattr(ai_groups, "GROUP_MSG_PUSH", "http://127.0.0.1:1/internal/group-message")
    # 死端口：不应抛异常
    asyncio.run(ai_groups._notify_group_chat("g-p3", "hermes", "Hermes", "步骤结果Y", "t-p3"))
    conn = sqlite3.connect(str(db_path))
    n = conn.execute("SELECT COUNT(*) FROM discussion_messages").fetchone()[0]
    conn.close()
    assert n == 1
    assert received == []


def test_default_agent_fallback_names(group_env):
    import json

    ai_groups, db_path, received = group_env
    asyncio.run(ai_groups._notify_group_chat("g-p4", "", "", "内容Z", "t-p4"))
    body = json.loads(received[0])
    assert body["agent_id"] == "soulmate"
    assert body["agent_name"] == "SoulMate"
