#!/usr/bin/env python3
"""ws_chat /chat 连接期凭据周期重校验 live e2e 探针（真 :8090 栈）。

断言面：
  P0 health 200
  P1 真长 token 建连 → connected + ping/pong（基线：通道可用）
  P2 短时效 token 建连（握手时有效）→ 过期后服务端主动 close 1008
     （≤ 生产节拍 60s+余量；修前=连接延续至断开，失效凭据继续对话）
  P3 控制连接（有效 token）同窗存活 + P2 关闭后仍 ping/pong（禁误杀）
清痕：ws_chat 凭据判定（decode_token UUID 路径）零 DB 写入，取证后无痕可清。
用法：.venv/bin/python scripts/e2e_ws_chat_revalidation.py [--base http://127.0.0.1:8090]
"""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

BASE = "http://127.0.0.1:8090"
for i, a in enumerate(sys.argv):
    if a == "--base" and i + 1 < len(sys.argv):
        BASE = sys.argv[i + 1]

WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")
CHAT_PATH = "/api/ws/chat"
CLOSE_BUDGET_S = 95.0  # 生产重校验节拍 60s + 余量
EVID = []


def ev(name: str, ok, detail: object = ""):
    EVID.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {str(detail)[:160]}")


def _tokens():
    import sys as _sys

    _sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from jose import jwt as jose_jwt

    from src.config import settings
    from src.services.auth import create_access_token

    valid = create_access_token(uuid4())
    payload = {
        "sub": str(uuid4()),
        "role": "user",
        "exp": datetime.now(UTC) + timedelta(seconds=2),
        "iat": datetime.now(UTC),
    }
    expiring = jose_jwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    return valid, expiring


async def _connect(url):
    import websockets

    ws = await websockets.connect(url, open_timeout=10)
    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    return ws, frame


async def _ping_ok(ws, budget=8.0):
    await ws.send(json.dumps({"type": "ping"}))
    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=budget))
    return frame == {"type": "pong"}


async def main():
    valid, expiring = _tokens()

    # P0 health
    r = httpx.get(f"{BASE}/api/ws/health", timeout=10)
    ev("P0 health 200", r.status_code == 200, f"status={r.status_code}")

    # P1 基线：有效 token 建连 + ping/pong
    ws_keep, frame = await _connect(f"{WS_BASE}{CHAT_PATH}?token={valid}")
    ev("P1 connected frame", frame.get("type") == "connected", str(frame)[:120])
    ok = await _ping_ok(ws_keep)
    ev("P1 ping/pong baseline", ok, "pong")

    # P2 短时效 token：握手时有效，过期后生产 60s 节拍内 close 1008
    ws_exp, frame2 = await _connect(f"{WS_BASE}{CHAT_PATH}?token={expiring}")
    ev("P2 connected (exp token)", frame2.get("type") == "connected", str(frame2)[:120])
    ok = await _ping_ok(ws_exp)
    ev("P2 ping before expiry", ok, "pong")

    close_code, close_reason, waited = None, "", 0.0
    try:
        while waited < CLOSE_BUDGET_S:
            try:
                msg = await asyncio.wait_for(ws_exp.recv(), timeout=5.0)
                waited += 5.0
                _ = msg  # 过期前不应有服务端主动帧
            except asyncio.TimeoutError:
                waited += 5.0  # 无帧=继续等撤销 close
                continue
    except Exception as exc:  # noqa: BLE001 —— close 到达/断联
        rcvd = getattr(exc, "rcvd", None)
        if rcvd is not None:
            close_code, close_reason = rcvd.code, rcvd.reason or ""
        else:
            close_code, close_reason = f"type={type(exc).__name__}", str(exc)[:80]
    ev(
        "P2 close 1008 after expiry",
        close_code == 1008 and "invalid" in (close_reason or ""),
        f"code={close_code} reason={close_reason!r} waited~{waited:.0f}s",
    )

    # P3 控制连接同窗存活（禁误杀）
    ok = await _ping_ok(ws_keep)
    ev("P3 control connection alive", ok, "pong after P2 window")

    for ws in (ws_keep, ws_exp):
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass

    passed = sum(1 for e in EVID if e["ok"])
    print(f"\nRESULT {passed}/{len(EVID)}")
    for e in EVID:
        print(f"  {'PASS' if e['ok'] else 'FAIL'} {e['check']}: {e['detail']}")
    return 0 if passed == len(EVID) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
