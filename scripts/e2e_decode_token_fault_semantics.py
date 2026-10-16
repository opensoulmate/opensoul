#!/usr/bin/env python3
"""decode_token 故障语义收口 live e2e 探针（DEC-⑭-⑤，真 :8090 栈）。

断言面：
  P0 health 200（服务真起）
  P1 /api/cron/list 坏 token → 401 "Invalid or expired token"（凭据无效契约不变）
  P2 /api/cron/list 真标准 token → 200（happy path 零回归，消费面=src.api.user
     get_current_user 真依赖链）
  P3 /api/cron/list 真企业 token（scope=enterprise，双库真行）→ 200
     （decode_token 企业分支重写后查库口真解析回 opensoul UUID，活面可达）
  P4 /api/cron/list 错签名 token → 401（凭据无效契约不变）
诚实边界：身份库故障注入（P5）不在 live 跑——须破坏生产身份库，禁做；故障语义
（TokenLookupFault→503）由 in-process HTTP 层测试覆盖（tests/test_decode_token_
fault_semantics.py 真 FastAPI 依赖链+真坏库文件）。
清痕：探针播种 opensoul.db/enterprise.db users 行各 1，取证后删除回读 remaining=0。
用法：.venv/bin/python scripts/e2e_decode_token_fault_semantics.py [--base http://127.0.0.1:8090]
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx

BASE = "http://127.0.0.1:8090"
for i, a in enumerate(sys.argv):
    if a == "--base" and i + 1 < len(sys.argv):
        BASE = sys.argv[i + 1]

EVID = []


def ev(name: str, ok, detail: object = ""):
    EVID.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"{'PASS' if ok else 'FAIL'} {name}: {str(detail)[:160]}")


def _seed():
    import sqlite3

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from jose import jwt as jose_jwt

    from src.config import settings
    from src.services.auth import create_access_token

    uid = uuid4()
    ent_id = 880000 + uuid4().int % 9000
    uname = f"e2e-dtf-{uuid4().hex[:6]}"
    with sqlite3.connect("data/opensoul.db") as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id)"
            " VALUES (?, ?, 'x', 1, 'default')",
            (str(uid), uname),
        )
        conn.commit()
    with sqlite3.connect("data/enterprise.db") as conn:
        conn.execute(
            "INSERT INTO users (id, username, password, is_active) VALUES (?, ?, 'x', 1)",
            (ent_id, uname),
        )
        conn.commit()
    std = create_access_token(uid)
    ent = jose_jwt.encode(
        {
            "sub": str(ent_id),
            "username": uname,
            "scope": "enterprise",
            "exp": datetime.now(UTC) + timedelta(minutes=30),
            "iat": datetime.now(UTC),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return uid, ent_id, uname, std, ent


def _cleanup(uid, ent_id, uname):
    import sqlite3

    with sqlite3.connect("data/opensoul.db") as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (str(uid),))
        conn.commit()
        left_os = conn.execute("SELECT COUNT(*) FROM users WHERE username = ?", (uname,)).fetchone()[0]
    with sqlite3.connect("data/enterprise.db") as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (ent_id,))
        conn.commit()
        left_ent = conn.execute("SELECT COUNT(*) FROM users WHERE username = ?", (uname,)).fetchone()[0]
    return left_os, left_ent


def main() -> int:
    uid, ent_id, uname, std, ent = _seed()
    try:
        with httpx.Client(base_url=BASE, timeout=15) as c:
            r = c.get("/api/health")
            ev("P0 health 200", r.status_code == 200, f"status={r.status_code}")

            r = c.get("/api/cron/list", headers={"Authorization": "Bearer not-a-jwt"})
            ev(
                "P1 坏 token 401 契约不变",
                r.status_code == 401 and r.json().get("detail") == "Invalid or expired token",
                f"status={r.status_code} detail={r.json().get('detail') if r.headers.get('content-type','').startswith('application/json') else ''}",
            )

            r = c.get("/api/cron/list", headers={"Authorization": f"Bearer {std}"})
            ev("P2 真标准 token 200（happy path 零回归）", r.status_code == 200, f"status={r.status_code}")

            r = c.get("/api/cron/list", headers={"Authorization": f"Bearer {ent}"})
            ev(
                "P3 真企业 token 200（企业查库口活面可达）",
                r.status_code == 200,
                f"status={r.status_code}",
            )

            from jose import jwt as _jose

            from src.config import settings as _settings

            bad = _jose.encode(
                {
                    "sub": str(uid),
                    "role": "user",
                    "exp": datetime.now(UTC) + timedelta(minutes=30),
                    "iat": datetime.now(UTC),
                },
                _settings.jwt_secret + "-wrong",
                algorithm=_settings.jwt_algorithm,
            )
            r = c.get("/api/cron/list", headers={"Authorization": f"Bearer {bad}"})
            ev("P4 错签名 token 401 契约不变", r.status_code == 401, f"status={r.status_code}")
    finally:
        left_os, left_ent = _cleanup(uid, ent_id, uname)
        ev("清痕 remaining=0", left_os == 0 and left_ent == 0, f"opensoul={left_os} enterprise={left_ent}")

    ok = all(e["ok"] for e in EVID)
    print(f"\n{'ALL PASS' if ok else 'HAS FAIL'}: {sum(e['ok'] for e in EVID)}/{len(EVID)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
