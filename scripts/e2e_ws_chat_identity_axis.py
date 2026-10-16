#!/usr/bin/env python3
"""DEC-⑭-④ ws_chat 判口换 bind_identity live e2e 探针（真 :8090 栈，修前/修后同探针
双向硬证，取证后清痕 remaining=0）。

断言面（--pre=修前预期，缺省=修后预期）：
  P0 health 200
  P1 真身份（register→login→token）建连 → connected + 载荷==纯用户 id==users.id
     （两模式同断言：DEC-⑰ 纯 id 契约零回归）
  P2 未知用户行 token（签名合法但 users 行不存在）
     修前：connected（decode_token 不查行）；修后：error Invalid token
  P3 停用用户 token（is_active=0）
     修前：connected；修后：error Invalid token
  P4 连接期撤销：建连后 SQL 删身份行
     修后：≤95s 内 close 1008（生产重校验节拍 60s+余量）；修前：窗口内不关（失效凭据延续）
  P5 控制连接（另一真活跃身份）同窗 ping/pong 存活（禁误杀）
清痕：探针用户删（username LIKE 'e2e-wschat-%'），opensoul.db/enterprise.db 双库 remaining=0。
用法：.venv/bin/python scripts/e2e_ws_chat_identity_axis.py [--base http://127.0.0.1:8090] [--pre]
"""

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

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
CLOSE_BUDGET_S = 95.0  # 生产重校验节拍 60s + 余量
PRE_OPEN_BUDGET_S = 70.0  # 修前「不关」窗口（≥ 2 个重校验拍）
EVID = []


def ev(name: str, ok, detail: object = ""):
    EVID.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {str(detail)[:160]}")


def _sys_path():
    sys.path.insert(0, str(Path(__file__).parent.parent))


def _register_login(c: httpx.Client, uname: str, pwd: str) -> str:
    rr = c.post(
        "/api/user/register",
        json={"username": uname, "email": f"{uname}@e2e.local", "password": pwd},
    )
    assert rr.status_code == 200, rr.text
    lr = c.post("/api/user/login", data={"username": uname, "password": pwd})
    assert lr.status_code == 200, lr.text
    return lr.json()["access_token"]


async def _connect(url):
    import websockets

    ws = await websockets.connect(url, open_timeout=10)
    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    return ws, frame


async def _ping_ok(ws, budget=8.0) -> bool:
    await ws.send(json.dumps({"type": "ping"}))
    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=budget))
    return frame == {"type": "pong"}


async def _await_close(ws, budget: float):
    """等服务端主动 close（e2e_ws_chat_revalidation 同款收帧循环）。
    返回 (close_code, waited)；窗口内未关返回 (None, budget)。"""
    close_code, waited = None, 0.0
    try:
        while waited < budget:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                waited += 5.0
                _ = msg  # 撤销前不应有服务端主动帧
            except asyncio.TimeoutError:
                waited += 5.0
                continue
    except Exception as exc:  # noqa: BLE001 —— close 到达/断联
        rcvd = getattr(exc, "rcvd", None)
        close_code = rcvd.code if rcvd is not None else f"type={type(exc).__name__}"
    return close_code, waited


async def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)
    suffix = uuid4().hex[:6]
    pwd = "E2eWsChatPass!42"
    uname_a, uname_b = f"e2e-wschat-a-{suffix}", f"e2e-wschat-b-{suffix}"
    uname_off = f"e2e-wschat-off-{suffix}"

    r = c.get("/api/ws/health", timeout=10)
    ev("P0 health 200", r.status_code == 200, f"status={r.status_code}")

    tok_a = _register_login(c, uname_a, pwd)
    tok_b = _register_login(c, uname_b, pwd)
    tok_off = _register_login(c, uname_off, pwd)
    ev("P1 register->login tokens", True, [uname_a, uname_b, uname_off])

    _sys_path()
    from src.services.auth import create_access_token

    # P1 真身份建连：connected + 载荷==纯用户 id==users.id（两模式同断言）
    ws_a, frame = await _connect(f"{WS_BASE}{CHAT_PATH}?token={tok_a}")
    with sqlite3.connect(Path("data/opensoul.db")) as conn:
        row_id = conn.execute(
            "SELECT id FROM users WHERE username = ?", (uname_a,)
        ).fetchone()
    want_id = str(row_id[0]) if row_id else ""
    ok = (
        frame.get("type") == "connected"
        and frame.get("user_id") == want_id
        and "{" not in str(frame.get("user_id", ""))
    )
    ev("P1 connected payload==users.id pure", ok, f"{frame} want={want_id}")

    # P2 未知用户行 token（签名合法、users 行不存在）
    tok_unknown = create_access_token(uuid4())
    ws_u, frame_u = await _connect(f"{WS_BASE}{CHAT_PATH}?token={tok_unknown}")
    got_u = frame_u.get("type")
    want_u = "connected" if PRE else "error"
    ev(f"P2 unknown-row token -> {want_u}", got_u == want_u, str(frame_u)[:120])
    try:
        await ws_u.close()
    except Exception:
        pass

    # P3 停用用户 token（is_active=0）
    with sqlite3.connect(Path("data/opensoul.db")) as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE username = ?", (uname_off,))
        conn.commit()
    ws_off, frame_off = await _connect(f"{WS_BASE}{CHAT_PATH}?token={tok_off}")
    got_off = frame_off.get("type")
    want_off = "connected" if PRE else "error"
    ev(f"P3 disabled-user token -> {want_off}", got_off == want_off, str(frame_off)[:120])
    try:
        await ws_off.close()
    except Exception:
        pass

    # P5 控制连接（另一真活跃身份）先开——同窗对照禁误杀
    ws_ctrl, frame_ctrl = await _connect(f"{WS_BASE}{CHAT_PATH}?token={tok_b}")
    ev("P5 control connected", frame_ctrl.get("type") == "connected", str(frame_ctrl)[:80])

    # P4 连接期撤销：删身份行（两模式同动作）→ 修后 close 1008 / 修前窗口内不关
    with sqlite3.connect(Path("data/opensoul.db")) as conn:
        conn.execute("DELETE FROM users WHERE username = ?", (uname_a,))
        conn.commit()
    if PRE:
        await asyncio.sleep(PRE_OPEN_BUDGET_S)
        alive = await _ping_ok(ws_a)
        ev("P4 deleted-row conn survives (pre)", alive, f"open>{PRE_OPEN_BUDGET_S}s")
    else:
        close_code, waited = await _await_close(ws_a, CLOSE_BUDGET_S)
        ev(
            "P4 deleted-row conn closes 1008",
            close_code == 1008,
            f"code={close_code} waited~{waited:.0f}s",
        )

    # P5 控制连接仍活（禁误杀）
    ok = await _ping_ok(ws_ctrl)
    ev("P5 control survives (no false kill)", ok, "pong")
    try:
        await ws_ctrl.close()
    except Exception:
        pass
    try:
        await ws_a.close()
    except Exception:
        pass

    # 清痕：探针用户双库删，remaining=0
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM users WHERE username LIKE 'e2e-wschat-%'")
            conn.commit()
    ru = 0
    for db in (Path("data/opensoul.db"), Path("data/enterprise.db")):
        with sqlite3.connect(db) as conn:
            ru += conn.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-wschat-%'"
            ).fetchone()[0]
    ev("cleanup remaining=0", ru == 0, f"users={ru}")

    failed = [e for e in EVID if not e["ok"]]
    print(f"\n{'PRE' if PRE else 'POST'} RESULT: {len(EVID) - len(failed)}/{len(EVID)} ok")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
