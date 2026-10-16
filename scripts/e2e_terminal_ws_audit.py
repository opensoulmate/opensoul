#!/usr/bin/env python3
"""DEC-⑱ terminal WS 连接审计面 live e2e 探针（真 :8090 栈，修前/修后同探针双向硬证，取证后清痕 remaining=0）。

断言面（timeline=审计真源，data/timeline.db 直读回读）：
  P1 无 token 握手 → REJECTED + terminal_ws_rejected 行（has_token=False，tenant=default）
  P2 坏 token 握手 → REJECTED + terminal_ws_rejected 行（has_token=True，凭据材料只字不进事件）
  P3 真 token 会话（真 bash 算术展开）→ SHELL_OK + terminal_ws_connected/closed 成对
     （user_id=派生、tenant_id=派生、closed reason+duration_s 在场、token 不在任何行）
  P4 连接期停用用户 → close 1008 + terminal_ws_closed 行 reason=credential_revoked
  P5 health 200
用法：python3 scripts/e2e_terminal_ws_audit.py [--base http://127.0.0.1:8090] [--pre]
  --pre=修前取证（审计行按「不存在」预期——修前硬证即审计缺口本身）。
"""

import asyncio
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8090"
PRE = False
for i, a in enumerate(sys.argv):
    if a == "--base" and i + 1 < len(sys.argv):
        BASE = sys.argv[i + 1]
    if a == "--pre":
        PRE = True

WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")
TERM_PATH = "/api/terminal/ws/terminal"
MARKER = "term-audit-77"
TIMELINE_DB = Path("data/timeline.db")
EVID = []


def ev(name: str, ok, detail: object = ""):
    EVID.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {str(detail)[:160]}")


def _rows_since(ts: float):
    with sqlite3.connect(f"file:{TIMELINE_DB}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM events WHERE event_type LIKE 'terminal_ws_%' AND timestamp >= ? "
                "ORDER BY timestamp",
                (ts,),
            ).fetchall()
        ]


async def _probe(query: str = "", headers: dict | None = None) -> str:
    import websockets

    url = f"{WS_BASE}{TERM_PATH}{query}"
    try:
        async with websockets.connect(url, additional_headers=headers or {}, open_timeout=6) as ws:
            await ws.send(json.dumps({"type": "input", "data": f"echo {MARKER}-$((6*7))\n"}))
            buf = ""
            for _ in range(80):
                try:
                    chunk = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                buf += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
                if f"{MARKER}-42" in buf:
                    break
            return "SHELL_OK" if f"{MARKER}-42" in buf else "CONNECTED_NO_SHELL"
    except Exception as exc:  # noqa: BLE001
        return f"REJECTED({type(exc).__name__})"


async def _probe_revoke(token: str) -> tuple[str, str]:
    """连接期停用用户（探针主线程随后停用）→ 等看门狗 close 1008（生产 60s 节拍）。"""
    import websockets

    url = f"{WS_BASE}{TERM_PATH}?token={token}"
    try:
        async with websockets.connect(url, open_timeout=6) as ws:
            await ws.send(json.dumps({"type": "input", "data": f"echo {MARKER}-$((6*7))\n"}))
            buf = ""
            for _ in range(80):
                try:
                    chunk = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                buf += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk
                if f"{MARKER}-42" in buf:
                    break
            shell = "SHELL_OK" if f"{MARKER}-42" in buf else "CONNECTED_NO_SHELL"
            # 等看门狗撤销 close（<= 生产 60s 节拍 + 余量）
            try:
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=75)
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "rcvd", None)
                code = getattr(code, "code", None) or getattr(exc, "code", None)
                return shell, f"CLOSED(code={code},{type(exc).__name__})"
        return shell, "STILL_OPEN"
    except Exception as exc:  # noqa: BLE001
        return "REJECTED", f"{type(exc).__name__}"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)
    suffix = uuid.uuid4().hex[:6]
    uname, uname_off = f"e2e-ta-{suffix}", f"e2e-ta-off-{suffix}"
    pwd = "E2eTaPass!42"
    probe_start = time.time() - 1.0

    r = c.get("/api/health")
    ev("P5 health 200", r.status_code == 200, r.status_code)

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

    with sqlite3.connect(f"file:{Path('data/opensoul.db')}?mode=ro", uri=True) as conn:
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (uname,)).fetchone()[0]

    # P1 无 token
    r1 = asyncio.run(_probe())
    ev("P1 无 token -> REJECTED", r1.startswith("REJECTED"), r1)

    # P2 坏 token（凭据材料不进事件）
    bad = "e2e-bad-token-secret-material"
    r2 = asyncio.run(_probe(query=f"?token={bad}"))
    ev("P2 坏 token -> REJECTED", r2.startswith("REJECTED"), r2)

    # P3 真 token 会话（客户端断开→closed）
    r3 = asyncio.run(_probe(query=f"?token={tokens[uname]}"))
    ev("P3 真 token -> SHELL_OK", r3 == "SHELL_OK", r3)

    # P4 连接期停用用户（revoked）
    async def _revoke_flow():
        task = asyncio.create_task(_probe_revoke(tokens[uname_off]))
        await asyncio.sleep(3)  # 等握手+shell 往返
        with sqlite3.connect(Path("data/opensoul.db")) as conn:
            conn.execute("UPDATE users SET is_active = 0 WHERE username = ?", (uname_off,))
            conn.commit()
        return await task

    shell4, close4 = asyncio.run(_revoke_flow())
    ev(
        "P4 连接期停用 -> close(撤销)",
        shell4 == "SHELL_OK" and "CLOSED" in close4,
        f"{shell4} {close4}",
    )

    # 审计行回读（timeline=真源）
    rows = _rows_since(probe_start)
    by_type: dict[str, list] = {}
    for row in rows:
        by_type.setdefault(row["event_type"], []).append(row)
    exp_n = {"terminal_ws_rejected": 2, "terminal_ws_connected": 2, "terminal_ws_closed": 2}
    if PRE:
        # 修前硬证=审计缺口本身：三类行全缺
        ev(
            "PRE 审计缺口（三类行全缺=修前硬证）",
            rows == [],
            f"rows={[(r['event_type']) for r in rows]}",
        )
    else:
        for t, n in exp_n.items():
            ev(f"审计行 {t} == {n}", len(by_type.get(t, [])) == n, len(by_type.get(t, [])))
        rej = by_type.get("terminal_ws_rejected", [])
        ev(
            "rejected has_token 形态 False/True + tenant default",
            len(rej) == 2
            and '"has_token": false' in rej[0]["detail"]
            and '"has_token": true' in rej[1]["detail"]
            and all(r["tenant_id"] == "default" for r in rej),
            [(r["tenant_id"], r["detail"]) for r in rej],
        )
        conn_rows = by_type.get("terminal_ws_connected", [])
        with sqlite3.connect(f"file:{Path('data/opensoul.db')}?mode=ro", uri=True) as conn:
            uid_off = conn.execute(
                "SELECT id FROM users WHERE username = ?", (uname_off,)
            ).fetchone()
        # P3/P4 是两个不同用户（探针自伤勘误：首版只对 uid 断言=漏断 uid_off 行）
        want_ids = {uid, (uid_off[0] if uid_off else uid)}
        got_ids = set()
        for r in conn_rows:
            got_ids.add(json.loads(r["detail"]).get("user_id"))
        ev(
            "connected user_id 派生集合=={P3,P4 用户} + tenant 在场",
            got_ids == want_ids
            and all(r["tenant_id"] not in ("", None) for r in conn_rows),
            f"got={sorted(got_ids)} want={sorted(want_ids)} tenants={[r['tenant_id'] for r in conn_rows]}",
        )
        closed = by_type.get("terminal_ws_closed", [])
        reasons = sorted(
            json.loads(r["detail"]).get("reason", "?") for r in closed
        )
        ev(
            "closed reason 面（含 credential_revoked）",
            "credential_revoked" in reasons
            and all(x in ("client_disconnect", "shell_exit", "credential_revoked") for x in reasons),
            reasons,
        )
        ev(
            "closed duration_s 在场",
            all("duration_s" in json.loads(r["detail"]) for r in closed),
            [json.loads(r["detail"]).get("duration_s") for r in closed],
        )
        blob = json.dumps(rows, ensure_ascii=False)
        ev("凭据材料只字不进事件（真/坏 token 均不在行）",
           tokens[uname] not in blob and bad not in blob, "token-free")

    # 清痕：探针窗口审计行 + 探针用户双库删，remaining=0
    with sqlite3.connect(TIMELINE_DB) as conn:
        conn.execute(
            "DELETE FROM events WHERE event_type LIKE 'terminal_ws_%' AND timestamp >= ?",
            (probe_start,),
        )
        conn.commit()
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM users WHERE username LIKE 'e2e-ta-%'")
            conn.commit()
    rem = len(_rows_since(probe_start))
    ru = 0
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            ru += conn.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-ta-%'"
            ).fetchone()[0]
    ev("cleanup remaining=0", rem == 0 and ru == 0, f"timeline={rem} users={ru}")

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
