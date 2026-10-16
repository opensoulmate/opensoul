#!/usr/bin/env python3
"""DEC-⑯ terminal 路径对齐 live e2e 探针（真 :8090 栈，修前/修后同探针双向硬证，取证后清痕 remaining=0）。

背景：OpenMate terminal-panel（openmate/src/components/terminal-panel.tsx:49）连
`ws://${hostname}:8090/ws/terminal?token=...`（顶层），本仓修前只挂
/api/terminal/ws/terminal → 面板连不上终端（live 404/403，DEC-⑭-① 边界④留档）。
收口=本仓加挂顶层别名（同一 handler，鉴权同源），canonical 面零变化。

断言面：
  P1 health 200
  P2 别名（面板 URL 形：顶层 /ws/terminal?token=）→ 修前 REJECTED（面板连不上
     硬证）/修后 SHELL_OK（真 bash 算术展开）
  P3 别名 无 token → REJECTED（真鉴权拒绝：别名不放宽鉴权）
  P4 别名 坏 token → REJECTED
  P5 canonical 真 token → SHELL_OK（零回归）
  P6 canonical 无 token → REJECTED（零回归）
用法：python3 scripts/e2e_terminal_ws_path_alias.py [--base http://127.0.0.1:8090]
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
PANEL_PATH = "/ws/terminal"  # OpenMate terminal-panel 期望路径（DEC-⑯ 别名面）
MARKER = "term-alias-42"
EVID = []


def ev(name: str, ok, detail: object = ""):
    EVID.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {str(detail)[:160]}")


async def _probe(path: str, query: str = "", headers: dict | None = None) -> str:
    import websockets

    url = f"{WS_BASE}{path}{query}"
    try:
        async with websockets.connect(url, additional_headers=headers or {}, open_timeout=6) as ws:
            # 真 shell 往返：echo term-alias-$((6*7)) —— marker 只可能来自 bash 求值
            await ws.send(
                json.dumps({"type": "input", "data": "echo term-alias-$((6*7))\n"})
            )
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
    uname = f"e2e-talias-{suffix}"
    pwd = "E2eTermPass!42"

    r = c.get("/api/health")
    ev("health 200", r.status_code == 200, r.status_code)

    rr = c.post(
        "/api/user/register",
        json={"username": uname, "email": f"{uname}@e2e.local", "password": pwd},
    )
    if rr.status_code != 200:
        ev(f"register {uname}", False, rr.text)
        return 1
    lr = c.post("/api/user/login", data={"username": uname, "password": pwd})
    assert lr.status_code == 200, lr.text
    token = lr.json()["access_token"]
    ev("register→login token", bool(token), uname)

    results = []

    # P2 别名=面板 URL 形（顶层 + ?token= query）
    r2 = asyncio.run(_probe(PANEL_PATH, query=f"?token={token}"))
    results.append(("P2 alias panel-url real token", r2, "SHELL_OK"))

    # P3 别名 无 token
    r3 = asyncio.run(_probe(PANEL_PATH))
    results.append(("P3 alias no token", r3, "REJECTED"))

    # P4 别名 坏 token
    r4 = asyncio.run(_probe(PANEL_PATH, query="?token=garbage-not-a-jwt"))
    results.append(("P4 alias garbage token", r4, "REJECTED"))

    # P5 canonical 真 token（零回归）
    r5 = asyncio.run(_probe(TERM_PATH, query=f"?token={token}"))
    results.append(("P5 canonical real token", r5, "SHELL_OK"))

    # P6 canonical 无 token（零回归）
    r6 = asyncio.run(_probe(TERM_PATH))
    results.append(("P6 canonical no token", r6, "REJECTED"))

    for name, got, want in results:
        # 探针作者纪律：REJECTED(具体异常) 前缀即拒（异常名各栈不同，禁精确串误伤）
        ok = got.startswith("REJECTED") if want == "REJECTED" else got == want
        ev(f"{name} -> {want}", ok, got)

    # 清痕：探针用户双库删，remaining=0
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM users WHERE username LIKE 'e2e-talias-%'")
            conn.commit()
    ru = 0
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            ru += conn.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-talias-%'"
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
