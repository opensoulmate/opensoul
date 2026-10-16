#!/usr/bin/env python3
"""ws_chat connected 帧 user_id 载荷 live 探针（修前/修后同探针双向硬证）。

断言面：
  P0 health 200
  P1 真 token 建连 → connected 帧
     修前：user_id= str(decode_token dict)（dict repr 怪形，含 "user_id" 字样与 UUID( 形）
     修后：user_id= 纯用户 id（str(UUID)，与生态参照形 acp-proxy ws_chat.py 同款）
  P2 user_id 载荷 == str(decode_token(token)["user_id"]) 纯 id 逐字节（修后）
  P3 载荷禁 dict repr（不含 "{'" / "UUID(" / "role"）
  P4 ping/pong 回归（通道完好）
清痕：decode_token UUID 路径零 DB 写入，取证后无痕可清。
用法：.venv/bin/python scripts/e2e_ws_chat_connected_frame.py [--base http://127.0.0.1:8090] [--pre]
"""

import asyncio
import json
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
EVID = []


def ev(name: str, ok, detail: object = ""):
    EVID.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {str(detail)[:160]}")


def _token_and_pure_id():
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.services.auth import create_access_token, decode_token

    uid = uuid4()
    tok = create_access_token(uid)
    payload = decode_token(tok)
    assert payload is not None
    pure = str(payload["user_id"])
    return tok, pure


async def main():
    tok, pure = _token_and_pure_id()

    r = httpx.get(f"{BASE}/api/ws/health", timeout=10)
    ev("P0 health 200", r.status_code == 200, f"status={r.status_code}")

    import websockets

    ws = await websockets.connect(
        f"{WS_BASE}{CHAT_PATH}?token={tok}", open_timeout=10
    )
    try:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        ev("P1 connected 帧", frame.get("type") == "connected", f"frame={str(frame)[:150]}")
        payload = frame.get("user_id")
        if PRE:
            ev(
                "P1-pre dict repr 怪形在场（修前实证）",
                isinstance(payload, str) and ("user_id" in payload or "UUID(" in payload),
                f"user_id={str(payload)[:120]}",
            )
        else:
            ev("P2 纯 id 逐字节 == decode_token user_id", payload == pure,
               f"payload={payload!r} pure={pure!r}")
            ev(
                "P3 载荷禁 dict repr",
                isinstance(payload, str)
                and "{'" not in payload
                and "UUID(" not in payload
                and "role" not in payload,
                f"payload={str(payload)[:120]}",
            )
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
        ev("P4 ping/pong 回归", pong == {"type": "pong"}, f"pong={pong}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass

    print("\n== SUMMARY ==")
    ok_n = sum(1 for e in EVID if e["ok"])
    print(f"{ok_n}/{len(EVID)} passed")
    return 0 if ok_n == len(EVID) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
