#!/usr/bin/env python3
"""DEC-⑱-④ collab agent WS 连接审计面 live e2e 探针（真 :8090 栈，修前/修后同探针
双向硬证，取证后清痕 remaining=0）。

断言面（timeline=审计真源，data/timeline.db 直读回读）：
  P0 /api/collab/health 200
  P1 无 token claim 注册身份 → 握手拒（close 前拒=HTTP 握手失败）+ collab_ws_rejected
     行（has_token=False，cause=missing_token，tenant=default）
  P2 坏 token → collab_ws_rejected 行（has_token=True，cause=invalid_token，凭据材料
     只字不进事件）
  P3 顶号（持他人真 token 冒 victim）→ collab_ws_rejected 行 agent_id=victim（被 claim
     的目标身份入审计）
  P4 真 token 会话（ping/pong 通道真可用）→ collab_ws_connected/closed 成对
     （agent_id 在场、closed reason+duration_s 在场、租户键==agents 行租户单一源、
     token 不在任何行）
  P5 连接期 token 轮换（重注册换 token 同语义）→ close 1008 + collab_ws_closed 行
     reason=credential_revoked（生产 60s 看门狗节拍）
  P6 未注册 agent_id（②d 既有语义）→ 连接照常 + connected/closed 行 tenant=default
用法：python3 scripts/e2e_collab_ws_audit.py [--base http://127.0.0.1:8090] [--pre]
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
WS_PATH = "/api/collab/ws/agent"
COLLAB_DB = Path("data/agent_collaboration.db")
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
                "SELECT * FROM events WHERE event_type LIKE 'collab_ws_%' AND timestamp >= ? "
                "ORDER BY timestamp",
                (ts,),
            ).fetchall()
        ]


def _register(client: httpx.Client, agent_id: str) -> str:
    r = client.post("/api/collab/register", json={"agent_id": agent_id, "name": agent_id})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _reject_probe(url: str) -> str:
    """claim 注册身份凭证无效=accept 前 close=HTTP 握手拒绝。回 verdict。"""
    import websockets

    try:
        async with websockets.connect(url, open_timeout=6) as ws:  # pragma: no cover
            return "CONNECTED(不应发生)"
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        return f"REJECTED({name})" if name in ("InvalidStatus", "ConnectionClosed", "ProtocolError") else f"EXC({name})"


async def _session_probe(url: str) -> tuple[str, list]:
    """连接→ping/pong→客户端断开。回 (verdict, frames)。"""
    import websockets

    frames: list = []
    try:
        async with websockets.connect(url, open_timeout=6) as ws:
            got_pong = False
            for _ in range(30):
                try:
                    if not got_pong:
                        await ws.send(json.dumps({"event": "ping"}))
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                frames.append(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
                if any('"pong"' in f for f in frames):
                    got_pong = True
                    break
            return ("SESSION_OK" if got_pong else "NO_PONG"), frames
    except Exception as exc:  # noqa: BLE001
        return f"EXC({type(exc).__name__})", frames


async def _revoke_probe(url: str) -> tuple[str, str]:
    """连接期 token 轮换（探针主线程随后改行 token）→ 等看门狗 close 1008（生产 60s 节拍）。"""
    import websockets

    try:
        async with websockets.connect(url, open_timeout=6) as ws:
            got_pong = False
            for _ in range(30):
                try:
                    await ws.send(json.dumps({"event": "ping"}))
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if '"pong"' in (raw if isinstance(raw, str) else ""):
                    got_pong = True
                    break
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
    a_miss = f"e2e16x-{suffix}-miss"
    a_bad = f"e2e16x-{suffix}-bad"
    a_victim = f"e2e16x-{suffix}-victim"
    a_attacker = f"e2e16x-{suffix}-atk"
    a_sess = f"e2e16x-{suffix}-sess"
    a_rv = f"e2e16x-{suffix}-rv"
    a_ghost = f"e2e16x-{suffix}-ghost"
    probe_start = time.time() - 1.0

    r = c.get("/api/collab/health")
    ev("P0 health 200", r.status_code == 200, f"{r.status_code} {r.text[:60]}")

    tokens = {}
    for name in (a_miss, a_bad, a_victim, a_attacker, a_sess, a_rv):
        tokens[name] = _register(c, name)
    ev("register×6 签发 token", len(set(tokens.values())) == 6, "tokens distinct")

    # P1 无 token claim 注册身份
    v1 = asyncio.run(_reject_probe(f"{WS_BASE}{WS_PATH}/{a_miss}"))
    ev("P1 无 token -> 握手拒", v1.startswith("REJECTED"), v1)

    # P2 坏 token
    bad = "e2e-bad-agent-token-secret-material"
    v2 = asyncio.run(_reject_probe(f"{WS_BASE}{WS_PATH}/{a_bad}?token={bad}"))
    ev("P2 坏 token -> 握手拒", v2.startswith("REJECTED"), v2)

    # P3 顶号：持 attacker 真 token 冒 victim
    v3 = asyncio.run(_reject_probe(f"{WS_BASE}{WS_PATH}/{a_victim}?token={tokens[a_attacker]}"))
    ev("P3 顶号 -> 握手拒", v3.startswith("REJECTED"), v3)

    # P4 真 token 会话（客户端断开→closed）
    v4, _ = asyncio.run(_session_probe(f"{WS_BASE}{WS_PATH}/{a_sess}?token={tokens[a_sess]}"))
    ev("P4 真 token -> SESSION_OK(pong)", v4 == "SESSION_OK", v4)

    # P5 连接期 token 轮换→撤销（生产 60s 看门狗节拍）

    async def _revoke_flow():
        task = asyncio.create_task(_revoke_probe(f"{WS_BASE}{WS_PATH}/{a_rv}?token={tokens[a_rv]}"))
        await asyncio.sleep(3)  # 等握手+pong 往返
        with sqlite3.connect(COLLAB_DB) as conn:
            conn.execute("UPDATE agents SET token = 'rotated-by-e2e' WHERE agent_id = ?", (a_rv,))
            conn.commit()
        return await task

    pong5, close5 = asyncio.run(_revoke_flow())
    ev(
        "P5 连接期 token 轮换 -> close(撤销)",
        pong5 == "PONG_OK" and "CLOSED" in close5,
        f"{pong5} {close5}",
    )

    # P6 未注册 agent_id 既有语义（可连接、'default' 兜底）
    v6, _ = asyncio.run(_session_probe(f"{WS_BASE}{WS_PATH}/{a_ghost}"))
    ev("P6 未注册 agent -> SESSION_OK(②d 契约)", v6 == "SESSION_OK", v6)

    # 审计行回读（timeline=真源）
    rows = _rows_since(probe_start)
    by_type: dict[str, list] = {}
    for row in rows:
        by_type.setdefault(row["event_type"], []).append(row)
    if PRE:
        ev(
            "PRE 审计缺口（collab_ws 行全缺=修前硬证）",
            rows == [],
            f"rows={[(r['event_type']) for r in rows]}",
        )
    else:
        exp_n = {"collab_ws_rejected": 3, "collab_ws_connected": 3, "collab_ws_closed": 3}
        for t, n in exp_n.items():
            ev(f"审计行 {t} == {n}", len(by_type.get(t, [])) == n, len(by_type.get(t, [])))

        rej = by_type.get("collab_ws_rejected", [])
        rej_details = [json.loads(r["detail"]) for r in rej]
        causes = sorted(d.get("cause") for d in rej_details)
        ev(
            "rejected cause/has_token 形态 + tenant default + agent_id 目标在场",
            causes == ["invalid_token", "invalid_token", "missing_token"]
            and all(r["tenant_id"] == "default" for r in rej)
            and {d.get("agent_id") for d in rej_details} == {a_miss, a_bad, a_victim}
            and any(d.get("has_token") is False for d in rej_details),
            [(r["tenant_id"], r["detail"][:80]) for r in rej],
        )

        conn_rows = by_type.get("collab_ws_connected", [])
        got_agents = {json.loads(r["detail"]).get("agent_id") for r in conn_rows}
        # 租户键=agents 行租户单一源（注册行）；未注册 agent 行缺失→'default' 兜底
        # （②d/③b-2 既有语义，探针断言按两态分判）。
        with sqlite3.connect(f"file:{COLLAB_DB}?mode=ro", uri=True) as conn:
            row_tenants = {
                a: t
                for a, t in conn.execute("SELECT agent_id, tenant_id FROM agents").fetchall()
            }

        def _tenant_ok(r):
            aid = json.loads(r["detail"]).get("agent_id")
            if aid in row_tenants:
                return r["tenant_id"] == row_tenants[aid]
            return r["tenant_id"] == "default"  # 未注册兜底

        tenant_match = all(_tenant_ok(r) for r in conn_rows)
        ev(
            "connected agent 集合=={P4,P5,P6} + 租户键==agents 行真源",
            got_agents == {a_sess, a_rv, a_ghost} and tenant_match,
            f"got={sorted(x or '?' for x in got_agents)} tenants={[r['tenant_id'] for r in conn_rows]}",
        )

        closed = by_type.get("collab_ws_closed", [])
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
            bad not in blob and all(t not in blob for t in tokens.values()),
            "token-free",
        )

    # 清痕：探针窗口审计行 + 探针 agents 行，remaining=0
    with sqlite3.connect(TIMELINE_DB) as conn:
        conn.execute(
            "DELETE FROM events WHERE event_type LIKE 'collab_ws_%' AND timestamp >= ?",
            (probe_start,),
        )
        conn.commit()
    with sqlite3.connect(COLLAB_DB) as conn:
        conn.execute("DELETE FROM agents WHERE agent_id LIKE ?", (f"e2e16x-{suffix}-%",))
        conn.commit()
    with sqlite3.connect(f"file:{TIMELINE_DB}?mode=ro", uri=True) as conn:
        remain_t = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type LIKE 'collab_ws_%' AND timestamp >= ?",
            (probe_start,),
        ).fetchone()[0]
    with sqlite3.connect(f"file:{COLLAB_DB}?mode=ro", uri=True) as conn:
        remain_a = conn.execute(
            "SELECT COUNT(*) FROM agents WHERE agent_id LIKE ?", (f"e2e16x-{suffix}-%",)
        ).fetchone()[0]
    ev("清痕 remaining=0", remain_t == 0 and remain_a == 0, f"timeline={remain_t} agents={remain_a}")

    passed = sum(1 for e in EVID if e["ok"])
    print(f"\n== {passed}/{len(EVID)} PASS ==")
    Path("/tmp/e2e_collab_ws_audit_result.json").write_text(
        json.dumps({"passed": passed, "total": len(EVID), "evidence": EVID}, ensure_ascii=False)
    )
    return 0 if passed == len(EVID) else 1


if __name__ == "__main__":
    sys.exit(main())
