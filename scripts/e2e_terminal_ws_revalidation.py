#!/usr/bin/env python3
"""DEC-⑭-② terminal WS 连接期凭据周期重校验 live e2e 探针（真 :8090 栈，取证后清痕 remaining=0）。

断言面：
  P0 health 200
  P1 真 token 建连 → SHELL_OK（基线：会话存活、真 bash 算术展开）
  P2 连接期停用用户 → 服务端主动 close 1008（≤ 生产节拍 60s+余量；修前=会话延续至断开）
  P3 控制连接（另一活跃用户）同窗存活+shell 仍活（禁误杀）
用法：python3 scripts/e2e_terminal_ws_revalidation.py [--base http://127.0.0.1:8090]
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
MARKER = "term-ok-42"
CLOSE_BUDGET_S = 95.0  # 生产重校验节拍 60s + 余量
EVID = []


def ev(name: str, ok, detail: object = ""):
    EVID.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {str(detail)[:160]}")


async def _marker(ws, cmd, marker, budget=12.0):
    await ws.send(json.dumps({"type": "input", "data": cmd}))
    buf = ""
    try:
        for _ in range(int(budget / 0.25)):
            chunk = await asyncio.wait_for(ws.recv(), timeout=0.5)
            buf += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
            if marker in buf:
                return True
    except asyncio.TimeoutError:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"  marker recv stopped: {type(exc).__name__}: {exc}")
    return False


async def _revocation_probe(token_a, token_b, disable_a) -> dict:
    """P1 基线 → P2 撤销关闭（close code）→ P3 控制连接存活。"""
    import websockets

    out = {}
    url_a = f"{WS_BASE}{TERM_PATH}?token={token_a}"
    url_b = f"{WS_BASE}{TERM_PATH}?token={token_b}"
    async with websockets.connect(url_a, open_timeout=8) as ws_a, websockets.connect(
        url_b, open_timeout=8
    ) as ws_b:
        out["p1"] = await _marker(ws_a, "echo term-ok-$((6*7))\n", MARKER)
        out["p3_pre"] = await _marker(ws_b, "echo term-keep-$((2*21))\n", "term-keep-42")

        disable_a()  # P2 触发：停用 A（连接已建立，握手已过）

        # 等服务端看门狗主动 close（websockets 抛 ConnectionClosed，rcvd.code=1008）
        try:
            await asyncio.wait_for(ws_a.recv(), timeout=CLOSE_BUDGET_S)
            out["p2"] = ("no-close-frame", None)  # 又来一帧=还没关
        except asyncio.TimeoutError:
            out["p2"] = ("timeout", None)
        except Exception as exc:  # noqa: BLE001 —— websockets.ConnectionClosed 家族
            code = getattr(getattr(exc, "rcvd", None), "code", None) or getattr(
                getattr(exc, "ws_exc", None), "rcvd", None
            )
            code = getattr(code, "code", code)
            out["p2"] = (type(exc).__name__, code)

        # P3：A 被撤销后 B 仍活（禁误杀）
        out["p3"] = await _marker(ws_b, "echo term-keep2-$((3*14))\n", "term-keep2-42")
    return out


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)
    suffix = uuid.uuid4().hex[:6]
    uname_a, uname_b = f"e2e-term-reval-{suffix}-a", f"e2e-term-reval-{suffix}-b"
    pwd = "E2eTermReval!42"

    r = c.get("/api/health")
    ev("P0 health 200", r.status_code == 200, r.status_code)

    tokens = {}
    for name in (uname_a, uname_b):
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

    def disable_a():
        with sqlite3.connect(Path("data/opensoul.db")) as conn:
            conn.execute("UPDATE users SET is_active = 0 WHERE username = ?", (uname_a,))
            conn.commit()

    out = asyncio.run(_revocation_probe(tokens[uname_a], tokens[uname_b], disable_a))

    ev("P1 真 token SHELL_OK（基线）", out.get("p1") is True, out.get("p1"))
    kind, code = out.get("p2", (None, None))
    ev("P2 撤销→close 1008", code == 1008, f"exc={kind} code={code}")
    ev("P3 控制连接禁误杀（pre）", out.get("p3_pre") is True, out.get("p3_pre"))
    ev("P3 控制连接禁误杀（post）", out.get("p3") is True, out.get("p3"))

    # 清痕：探针用户双库删，remaining=0
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM users WHERE username LIKE 'e2e-term-reval-%'")
            conn.commit()
    ru = 0
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            ru += conn.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-term-reval-%'"
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
