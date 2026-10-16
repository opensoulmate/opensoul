#!/usr/bin/env python3
"""DEC-⑭-① /api/terminal/ws/terminal 身份轴 live e2e 探针（真 :8090 栈，修前/修后同探针双向硬证，取证后清痕 remaining=0）。

断言面：
  P1 无 token 握手 → 修前 SHELL_OK（RCE 面硬证：匿名真 shell）/修后 REJECTED
  P2 坏 token 握手 → 修后 REJECTED
  P3 真 token（query ?token= 主口）→ CONNECTED + 真 bash 算术展开 marker（SHELL_OK）
  P4 真 token（Authorization: Bearer 头兜底）→ SHELL_OK
  P5 停用用户 token → 修后 REJECTED（bind_identity is_active 门）
  I1 信息项：/ws/terminal（OpenMate 面板期望路径）现状取证（非断言）
用法：python3 scripts/e2e_terminal_ws_auth.py [--base http://127.0.0.1:8090]
"""

import asyncio
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8090"
for i, a in enumerate(sys.argv):
    if a == "--base" and i + 1 < len(sys.argv):
        BASE = sys.argv[i + 1]

WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")
TERM_PATH = "/api/terminal/ws/terminal"
PANEL_PATH = "/ws/terminal"  # OpenMate terminal-panel 期望路径（勘误面信息项）
MARKER = "term-ok-42"
EVID = []


def ev(name: str, ok, detail: object = ""):
    EVID.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {str(detail)[:160]}")


async def _probe(path: str, query: str = "", headers: dict | None = None) -> str:
    import websockets

    url = f"{WS_BASE}{path}{query}"
    try:
        async with websockets.connect(url, additional_headers=headers or {}, open_timeout=6) as ws:
            # 真 shell 往返：echo term-ok-$((6*7)) —— marker 只可能来自 bash 求值
            # （回显的输入行含 $((6*7)) 不含 42）
            await ws.send(json.dumps({"type": "input", "data": "echo term-ok-$((6*7))\n"}))
            buf = ""
            for _ in range(80):
                try:
                    chunk = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                buf += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
                if MARKER in buf:
                    break
            return "SHELL_OK" if MARKER in buf else "CONNECTED_NO_SHELL"
    except Exception as exc:  # noqa: BLE001
        return f"REJECTED({type(exc).__name__})"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)
    suffix = uuid.uuid4().hex[:6]
    uname, uname_off = f"e2e-term-{suffix}", f"e2e-term-off-{suffix}"
    pwd = "E2eTermPass!42"

    r = c.get("/api/health")
    ev("health 200", r.status_code == 200, r.status_code)

    # 真身份：register→login→token（活用户 + 停用用户各一）
    tokens = {}
    for name in (uname, uname_off):
        rr = c.post(
            "/api/user/register",
            json={"username": name, "email": f"{name}@e2e.local", "password": pwd},
        )
        if rr.status_code != 200:
            ev(f"register {name}", False, rr.text)
            return 1
        lr = c.post("/api/user/login", data={"username": name, "password": pwd})
        assert lr.status_code == 200, lr.text
        tokens[name] = lr.json()["access_token"]
    ev("register→login tokens", len(tokens) == 2, list(tokens))

    results = []

    # P1 无 token（含 OpenMate 面板期望路径信息项 I1）
    r1 = asyncio.run(_probe(TERM_PATH))
    results.append(("P1 无 token", r1, "REJECTED"))
    i1 = asyncio.run(_probe(PANEL_PATH))
    print(f"INFO I1 panel path {PANEL_PATH}: {i1}")

    # P2 坏 token
    r2 = asyncio.run(_probe(TERM_PATH, query="?token=garbage-not-a-jwt"))
    results.append(("P2 坏 token", r2, "REJECTED"))

    # P3 真 token query（主口）
    r3 = asyncio.run(_probe(TERM_PATH, query=f"?token={tokens[uname]}"))
    results.append(("P3 真 token query", r3, "SHELL_OK"))

    # P4 真 token Authorization 头（兜底口）
    r4 = asyncio.run(
        _probe(TERM_PATH, headers={"Authorization": f"Bearer {tokens[uname]}"})
    )
    results.append(("P4 真 token header", r4, "SHELL_OK"))

    # P5 停用用户 token
    with sqlite3.connect(Path("data/opensoul.db")) as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE username = ?", (uname_off,))
        conn.commit()
    r5 = asyncio.run(_probe(TERM_PATH, query=f"?token={tokens[uname_off]}"))
    results.append(("P5 停用用户 token", r5, "REJECTED"))

    for name, got, want in results:
        # 探针作者纪律：REJECTED(具体异常) 前缀即拒（websockets/starlette 各自异常名不同，禁精确串误伤）
        ok = got.startswith("REJECTED") if want == "REJECTED" else got == want
        ev(f"{name} -> {want}", ok, got)

    # 清痕：探针用户双库删，remaining=0
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM users WHERE username LIKE 'e2e-term-%'")
            conn.commit()
    ru = 0
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            ru += conn.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-term-%'"
            ).fetchone()[0]
    ev("cleanup remaining=0", ru == 0, f"users={ru}")

    failed = [e for e in EVID if not e["ok"]]
    print(
        json.dumps(
            {"total": len(EVID), "passed": len(EVID) - len(failed), "failed": len(failed)},
            ensure_ascii=False,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
