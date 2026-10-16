#!/usr/bin/env python3
"""DEC-⑱-② ws_chat 连接审计面 live e2e 探针（真 :8090 栈，修前/修后同探针双向硬证，取证后清痕 remaining=0）。

断言面（timeline=审计真源，data/timeline.db 直读回读）：
  P1 无 token 握手 → error 帧 Missing token + close + ws_chat_rejected 行（has_token=False，
     cause=missing_token，tenant=default）
  P2 坏 token 握手 → error 帧 Invalid token + close + ws_chat_rejected 行（has_token=True，
     cause=invalid_token，凭据材料只字不进事件）
  P3 真 token 会话（connected 帧纯 user_id + ping/pong）→ ws_chat_connected/closed 成对
     （user_id=派生、tenant_id=派生、closed reason+duration_s 在场、token 不在任何行）
  P4 连接期停用用户 → close 1008 + ws_chat_closed 行 reason=credential_revoked
  P5 health 200
用法：python3 scripts/e2e_ws_chat_audit.py [--base http://127.0.0.1:8090] [--pre]
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
CHAT_PATH = "/api/ws/chat"
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
                "SELECT * FROM events WHERE event_type LIKE 'ws_chat_%' AND timestamp >= ? "
                "ORDER BY timestamp",
                (ts,),
            ).fetchall()
        ]


async def _session(token: str) -> tuple[str, list]:
    """连接→读帧到收口：回 (verdict, frames)。ping 往返=通道真可用。"""
    import websockets

    url = f"{WS_BASE}{CHAT_PATH}?token={token}" if token else f"{WS_BASE}{CHAT_PATH}"
    frames: list = []
    try:
        async with websockets.connect(url, open_timeout=6) as ws:
            if token:
                got_ping = False
                for _ in range(40):
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    except asyncio.TimeoutError:
                        if not got_ping:
                            await ws.send(json.dumps({"type": "ping"}))
                            got_ping = True
                        continue
                    frames.append(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
                    if any('"pong"' in f for f in frames):
                        break
                verdict = "SESSION_OK" if any('"connected"' in f for f in frames) else "NO_CONNECTED"
            else:
                # 拒绝面：error 帧 + close
                for _ in range(20):
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break
                    frames.append(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
                verdict = "REJECTED" if any("Missing token" in f for f in frames) else "NO_REJECT"
        return verdict, frames
    except Exception as exc:  # noqa: BLE001
        return f"EXC({type(exc).__name__})", frames


async def _session_bad(token: str) -> tuple[str, list]:
    import websockets

    url = f"{WS_BASE}{CHAT_PATH}?token={token}"
    frames: list = []
    try:
        async with websockets.connect(url, open_timeout=6) as ws:
            for _ in range(20):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break
                frames.append(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        verdict = "REJECTED" if any("Invalid token" in f for f in frames) else "NO_REJECT"
        return verdict, frames
    except Exception as exc:  # noqa: BLE001
        return f"EXC({type(exc).__name__})", frames


async def _revoke_probe(token: str) -> tuple[str, str]:
    """连接期停用用户（探针主线程随后停用）→ 等看门狗 close 1008（生产 60s 节拍）。"""
    import websockets

    url = f"{WS_BASE}{CHAT_PATH}?token={token}"
    try:
        async with websockets.connect(url, open_timeout=6) as ws:
            await ws.send(json.dumps({"type": "ping"}))
            got_pong = False
            for _ in range(40):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if '"pong"' in (raw if isinstance(raw, str) else ""):
                    got_pong = True
                    break
            # 等看门狗撤销 close（<= 生产 60s 节拍 + 余量）
            try:
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=75)
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "rcvd", None)
                code = getattr(code, "code", None) or getattr(exc, "code", None)
                return ("PONG_OK" if got_pong else "NO_PONG"), f"CLOSED(code={code},{type(exc).__name__})"
        return ("PONG_OK" if got_pong else "NO_PONG"), "STILL_OPEN"
    except Exception as exc:  # noqa: BLE001
        return "EXC", f"{type(exc).__name__}"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)
    suffix = uuid.uuid4().hex[:6]
    uname, uname_off = f"e2e-wca-{suffix}", f"e2e-wca-off-{suffix}"
    pwd = "E2eWcaPass!42"
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
    r1, _ = asyncio.run(_session(""))
    ev("P1 无 token -> REJECTED(Missing token)", r1 == "REJECTED", r1)

    # P2 坏 token（凭据材料不进事件）
    bad = "e2e-bad-token-secret-material"
    r2, _ = asyncio.run(_session_bad(bad))
    ev("P2 坏 token -> REJECTED(Invalid token)", r2 == "REJECTED", r2)

    # P3 真 token 会话（客户端断开→closed）
    r3, frames3 = asyncio.run(_session(tokens[uname]))
    ev(
        "P3 真 token -> SESSION_OK（connected 纯 user_id + pong）",
        r3 == "SESSION_OK"
        and any("connected" in f and f'"user_id"' in f and uid in f for f in frames3),
        f"{r3} uid={uid} frames={frames3[:2]}",
    )

    # P4 连接期停用用户（revoked）
    async def _revoke_flow():
        task = asyncio.create_task(_revoke_probe(tokens[uname_off]))
        await asyncio.sleep(3)  # 等握手+pong 往返
        with sqlite3.connect(Path("data/opensoul.db")) as conn:
            conn.execute("UPDATE users SET is_active = 0 WHERE username = ?", (uname_off,))
            conn.commit()
        return await task

    pong4, close4 = asyncio.run(_revoke_flow())
    ev(
        "P4 连接期停用 -> close(撤销)",
        pong4 == "PONG_OK" and "CLOSED" in close4,
        f"{pong4} {close4}",
    )

    # 审计行回读（timeline=真源）
    rows = _rows_since(probe_start)
    by_type: dict[str, list] = {}
    for row in rows:
        by_type.setdefault(row["event_type"], []).append(row)
    exp_n = {"ws_chat_rejected": 2, "ws_chat_connected": 2, "ws_chat_closed": 2}
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
        rej = by_type.get("ws_chat_rejected", [])
        ev(
            "rejected has_token/cause 形态 + tenant default",
            len(rej) == 2
            and '"has_token": false' in rej[0]["detail"]
            and '"has_token": true' in rej[1]["detail"]
            and '"cause": "missing_token"' in rej[0]["detail"]
            and '"cause": "invalid_token"' in rej[1]["detail"]
            and all(r["tenant_id"] == "default" for r in rej),
            [(r["tenant_id"], r["detail"][:80]) for r in rej],
        )
        conn_rows = by_type.get("ws_chat_connected", [])
        with sqlite3.connect(f"file:{Path('data/opensoul.db')}?mode=ro", uri=True) as conn:
            uid_off = conn.execute(
                "SELECT id FROM users WHERE username = ?", (uname_off,)
            ).fetchone()
        # P3/P4 是两个不同用户
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
        closed = by_type.get("ws_chat_closed", [])
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
            tokens[uname] not in blob and bad not in blob,
            "token-free",
        )

    # 清痕：探针窗口审计行 + 探针用户双库删，remaining=0
    with sqlite3.connect(TIMELINE_DB) as conn:
        conn.execute(
            "DELETE FROM events WHERE event_type LIKE 'ws_chat_%' AND timestamp >= ?",
            (probe_start,),
        )
        conn.commit()
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM users WHERE username LIKE 'e2e-wca-%'")
            conn.commit()
    rem = len(_rows_since(probe_start))
    ru = 0
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            ru += conn.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-wca-%'"
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
