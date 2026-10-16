#!/usr/bin/env python3
"""DEC-⑱-③ link_gateway 连接审计面 live e2e 探针（真 :8090 栈，修前/修后同探针
双向硬证，取证后清痕 remaining=0）。

断言面（timeline=审计真源，data/timeline.db 直读回读）：
  P1 health 200
  P2 坏 token WS → 握手拒绝 + link_ws_rejected 行（has_token=True，cause=invalid_token，
     tenant=default，凭据材料只字不进事件）
  P3 匿名 WS 会话（send→断开）→ link_ws_connected/closed 成对（authed=False，
     user_id=default 如实、reason+duration_s 在场）
  P4 真 token WS 会话 → connected/closed 成对（user_id=派生、token 不在任何行）
  P5 连接期停用用户 → close(撤销) + link_ws_closed 行 reason=credential_revoked
  P6 坏 token SSE → 401 + link_sse_rejected 行（secret-free）
  P7 真 token SSE 开流→收流 → link_sse_connected/closed 成对
用法：python3 scripts/e2e_link_gateway_audit.py [--base http://127.0.0.1:8090] [--pre]
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
WS_PATH = "/api/link/ws"
SSE_PATH = "/api/link/stream"
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
                "SELECT * FROM events WHERE (event_type LIKE 'link_ws_%' OR event_type LIKE 'link_sse_%') "
                "AND timestamp >= ? ORDER BY timestamp",
                (ts,),
            ).fetchall()
        ]


async def _ws_session(token: str):
    """连接→发一条消息→断开：回 verdict。"""
    import websockets

    url = f"{WS_BASE}{WS_PATH}?token={token}" if token else f"{WS_BASE}{WS_PATH}"
    try:
        async with websockets.connect(url, open_timeout=6) as ws:
            await ws.send("e2e-probe-msg")
            await asyncio.sleep(0.4)
        return "SESSION_OK"
    except Exception as exc:  # noqa: BLE001
        return f"EXC({type(exc).__name__})"


async def _ws_reject_probe(token: str) -> str:
    import websockets

    url = f"{WS_BASE}{WS_PATH}?token={token}"
    try:
        async with websockets.connect(url, open_timeout=6) as ws:
            await asyncio.sleep(0.3)
        return "NO_REJECT"
    except Exception as exc:  # noqa: BLE001
        # 握手前 close 1008：uvicorn 表现为握手拒绝（InvalidStatus/HandshakeError 等）
        return "REJECTED"


async def _ws_revoke_probe(token: str) -> tuple[str, str]:
    """连接期停用用户（探针主线程随后停用）→ 等看门狗撤销关闭（生产 60s 节拍）。"""
    import websockets

    url = f"{WS_BASE}{WS_PATH}?token={token}"
    try:
        async with websockets.connect(url, open_timeout=6) as ws:
            await ws.send("e2e-revoke-msg")
            await asyncio.sleep(0.5)
            try:
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=75)
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "rcvd", None)
                code = getattr(code, "code", None) or getattr(exc, "code", None)
                return "SENT_OK", f"CLOSED(code={code},{type(exc).__name__})"
        return "SENT_OK", "STILL_OPEN"
    except Exception as exc:  # noqa: BLE001
        return "EXC", f"{type(exc).__name__}"


async def _sse_probe(token: str) -> str:
    """开流→短读→收流（客户端断开）。"""
    url = f"{BASE}{SSE_PATH}?token={token}"
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            async with c.stream("GET", url) as r:
                if r.status_code != 200:
                    return f"HTTP_{r.status_code}"
                await asyncio.sleep(2.0)  # 流真建立（connected 审计随首迭代落）
        return "STREAM_OK"
    except Exception as exc:  # noqa: BLE001
        return f"EXC({type(exc).__name__})"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)
    suffix = uuid.uuid4().hex[:6]
    uname, uname_off = f"e2e-lga-{suffix}", f"e2e-lga-off-{suffix}"
    pwd = "E2eLgaPass!42"
    probe_start = time.time() - 1.0

    r = c.get("/api/health")
    ev("P1 health 200", r.status_code == 200, r.status_code)

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
        uid_off = conn.execute(
            "SELECT id FROM users WHERE username = ?", (uname_off,)
        ).fetchone()[0]

    # P2 坏 token WS → 握手拒绝
    bad = "e2e-bad-token-secret-material"
    r2 = asyncio.run(_ws_reject_probe(bad))
    ev("P2 坏 token WS -> REJECTED", r2 == "REJECTED", r2)

    # P3 匿名 WS 会话（契约放行面）
    r3 = asyncio.run(_ws_session(""))
    ev("P3 匿名 WS -> SESSION_OK", r3 == "SESSION_OK", r3)

    # P4 真 token WS 会话
    r4 = asyncio.run(_ws_session(tokens[uname]))
    ev("P4 真 token WS -> SESSION_OK", r4 == "SESSION_OK", r4)

    # P5 连接期停用用户（revoked）
    async def _revoke_flow():
        task = asyncio.create_task(_ws_revoke_probe(tokens[uname_off]))
        await asyncio.sleep(3)  # 等握手+消息
        with sqlite3.connect(Path("data/opensoul.db")) as conn:
            conn.execute("UPDATE users SET is_active = 0 WHERE username = ?", (uname_off,))
            conn.commit()
        return await task

    sent5, close5 = asyncio.run(_revoke_flow())
    ev("P5 连接期停用 -> close(撤销)", sent5 == "SENT_OK" and "CLOSED" in close5, f"{sent5} {close5}")

    # P6 坏 token SSE → 401
    r6 = c.get(f"{SSE_PATH}?token={bad}")
    ev("P6 坏 token SSE -> 401", r6.status_code == 401, r6.status_code)

    # P7 真 token SSE 开流→收流
    r7 = asyncio.run(_sse_probe(tokens[uname]))
    ev("P7 真 token SSE -> STREAM_OK", r7 == "STREAM_OK", r7)

    # 收流后服务端收尾有界等待（客户端断开→取消兑现→finally 同步审计实测 ≤0.5s
    # 延迟到达；首轮探针即读行=读竞态自伤，禁裸长 sleep——有界轮询到行到场即止）
    _deadline = time.time() + 8
    while time.time() < _deadline:
        if any(r["event_type"] == "link_sse_closed" for r in _rows_since(probe_start)):
            break
        time.sleep(0.2)

    # 审计行回读（timeline=真源）
    rows = _rows_since(probe_start)
    by_type: dict[str, list] = {}
    for row in rows:
        by_type.setdefault(row["event_type"], []).append(row)
    exp_n = {
        "link_ws_rejected": 1,
        "link_ws_connected": 3,
        "link_ws_closed": 3,
        "link_sse_rejected": 1,
        "link_sse_connected": 1,
        "link_sse_closed": 1,
    }
    if PRE:
        # 修前硬证=审计缺口本身：六类行全缺
        ev(
            "PRE 审计缺口（六类行全缺=修前硬证）",
            rows == [],
            f"rows={[(r['event_type']) for r in rows]}",
        )
    else:
        for t, n in exp_n.items():
            ev(f"审计行 {t} == {n}", len(by_type.get(t, [])) == n, len(by_type.get(t, [])))
        rej_ws = by_type.get("link_ws_rejected", [])
        rej_sse = by_type.get("link_sse_rejected", [])
        ev(
            "rejected 形态（has_token/cause/tenant default）",
            all(
                '"has_token": true' in r["detail"]
                and '"cause": "invalid_token"' in r["detail"]
                and r["tenant_id"] == "default"
                for r in rej_ws + rej_sse
            ),
            [(r["event_type"], r["tenant_id"]) for r in rej_ws + rej_sse],
        )
        conn_rows = by_type.get("link_ws_connected", [])
        got_ids = set()
        got_auth = set()
        for r in conn_rows:
            d = json.loads(r["detail"])
            got_ids.add(d.get("user_id"))
            got_auth.add(d.get("authed"))
        ev(
            "connected user_id 派生集合=={default,P4,P5 用户} + authed 形态",
            got_ids == {"default", uid, uid_off} and got_auth == {False, True},
            f"ids={sorted(x or '?' for x in got_ids)} authed={sorted(x for x in got_auth)}",
        )
        ev(
            "connected/closed 租户键在场",
            all(r["tenant_id"] not in ("", None) for r in conn_rows + by_type.get("link_ws_closed", [])),
            [r["tenant_id"] for r in conn_rows],
        )
        closed = by_type.get("link_ws_closed", []) + by_type.get("link_sse_closed", [])
        reasons = sorted(json.loads(r["detail"]).get("reason", "?") for r in closed)
        ev(
            "closed reason 面（含 credential_revoked）",
            "credential_revoked" in reasons
            and all(x in ("client_disconnect", "error", "credential_revoked") for x in reasons),
            reasons,
        )
        ev(
            "closed duration_s 在场",
            all("duration_s" in json.loads(r["detail"]) for r in closed),
            [json.loads(r["detail"]).get("duration_s") for r in closed],
        )
        blob = json.dumps(rows, ensure_ascii=False)
        ev(
            "凭据材料只字不进事件（真/坏 token 均不在行）",
            tokens[uname] not in blob and tokens[uname_off] not in blob and bad not in blob,
            "token-free",
        )

    # 清痕：探针窗口审计行 + 探针用户双库删，remaining=0
    with sqlite3.connect(TIMELINE_DB) as conn:
        conn.execute(
            "DELETE FROM events WHERE (event_type LIKE 'link_ws_%' OR event_type LIKE 'link_sse_%') "
            "AND timestamp >= ?",
            (probe_start,),
        )
        conn.commit()
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM users WHERE username LIKE 'e2e-lga-%'")
            conn.commit()
    rem = len(_rows_since(probe_start))
    ru = 0
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            ru += conn.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-lga-%'"
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
