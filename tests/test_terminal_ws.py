"""Integration tests for Terminal WebSocket API."""

import json
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.terminal_ws as terminal_ws_api


class TestTerminalHealth:
    def test_health(self, client):
        resp = client.get("/api/terminal/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ── Gate3：真 spawn + 真执行（接线了≠跑通了）────────────────────────────
# 背景（2026-10-11 17:20）：terminal_ws.py 此前用 pty.openpty()（从不 fork，返回
# (master_fd, slave_fd) 且 master_fd != 0）却按 `if child_pid == 0` 分支 exec bash ——
# 该分支是死代码，/ws/terminal 从未真正拉起 shell（只开了个没人驱动的 pty 对），是
# "wired 但不 functional" 的真断点（docs/roadmap.md 最紧迫问题#3：Agent 不能操作电脑）。
# 已改 os.forkpty()（子分支真返 0→exec bash 真拉起）+非阻塞读泵（防事件循环冻死）。
MARKER = "term-ok-42"


def _ws_client():
    app = FastAPI()
    app.include_router(terminal_ws_api.router, prefix="/api/terminal")
    return TestClient(app)


def _run_roundtrip(client, collected, error):
    try:
        with client.websocket_connect("/api/terminal/ws/terminal") as ws:
            # 让 bash 做算术展开：只有真 shell 才会把 $((6*7)) 求值成 42。回显的输入行是
            # `echo term-ok-$((6*7))`（含 $((6*7))，绝不含 42），故标记只可能来自 bash 输出。
            ws.send_text(json.dumps({"type": "input", "data": "echo term-ok-$((6*7))\n"}))
            buf = ""
            for _ in range(60):  # 有界读：bash 远在这些帧内回应
                buf += ws.receive_text()
                if MARKER in buf:
                    break
            collected.append(buf)
    except Exception as exc:  # noqa: BLE001 —— 捕获后如实回传给断言
        error.append(repr(exc))


def test_terminal_spawns_real_bash_and_executes():
    client = _ws_client()
    collected, error = [], []
    t = threading.Thread(target=_run_roundtrip, args=(client, collected, error), daemon=True)
    t.start()
    t.join(timeout=20)
    # 挂死（读泵死锁 / 未拉起 shell）会在此干净失败，而非卡住整个测试会话。
    assert not t.is_alive(), "terminal WS hung —— 读泵死锁或没有 shell 被拉起"
    assert not error, f"roundtrip error: {error}"
    buf = collected[0] if collected else ""
    assert MARKER in buf, f"bash 未真执行命令（无算术展开）；实际收到: {buf!r}"
