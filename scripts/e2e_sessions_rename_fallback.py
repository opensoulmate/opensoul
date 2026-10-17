#!/usr/bin/env python3
"""e2e：rename_session hermes 路径 DatabaseError 回落韧性（SESS-④）真服务探针。

live :8090 的 ~/.hermes/state.db 是真损坏库（SESS-① 取证：not-a-db）——降级条件
真实存在，无需模拟。双向硬证：
- --pre（重启前旧码内存）：真 token PATCH agent 会话 → 500（「works for both」
  契约破硬证：hermes SELECT DatabaseError 直通 500，agent 回落整条路被堵死）
- --post（重启载新码）：同探针 → 200 真改 agent 行 + 缺行 404 诚实真因
- 取证后清痕 remaining=0（delete 同族缺陷走 SQL 直清，见诚实边界）

用法：python3 scripts/e2e_sessions_rename_fallback.py [--pre|--post]
"""

import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:8090"
OPENVEN = "/home/climbing/opensoul"
SID = f"e2e-renamefb-{uuid.uuid4().hex[:8]}"
DB = f"{OPENVEN}/data/opensoul.db"

sys.path.insert(0, OPENVEN)
from src.services.auth import create_access_token  # noqa: E402

MODE = "--pre" if "--pre" in sys.argv else "--post"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}")


def req(method, path, token=None, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"exc": str(e)}


def agent_title(sid):
    with sqlite3.connect(DB) as c:
        row = c.execute(
            "SELECT title FROM agent_sessions WHERE id = ?", (sid,)
        ).fetchone()
    return row[0] if row else None


def cleanup():
    with sqlite3.connect(DB) as c:
        c.execute("DELETE FROM agent_sessions WHERE id = ?", (SID,))
        c.commit()
    return agent_title(SID)


def main():
    tok = create_access_token(user_id=uuid.UUID(str(uuid.uuid4())), role="user")

    s, d = req("GET", "/api/sessions/health")
    check("health", s == 200 and d.get("component") == "SessionsAPI", f"{s}")

    # 建 agent 会话（只碰 agent 面，不触 hermes）
    s, d = req("POST", "/api/sessions", token=tok, body={"id": SID, "name": "e2e-title"})
    check("create agent session", s == 200 and d.get("session_id") == SID, f"{s} {d}")
    check("created row readable", agent_title(SID) == "e2e-title", f"{agent_title(SID)}")

    # 核心面：真 token PATCH agent 会话改名
    s, d = req("PATCH", f"/api/sessions/{SID}", token=tok, body={"title": "renamed-e2e"})
    if MODE == "--pre":
        check(
            "PATCH agent rename blocked by hermes DatabaseError (gap)",
            s == 500,
            f"status={s} detail={d}",
        )
    else:
        check(
            "PATCH agent rename 200 falls back despite corrupt hermes",
            s == 200
            and d == {"success": True, "id": SID, "title": "renamed-e2e"}
            and agent_title(SID) == "renamed-e2e",
            f"status={s} detail={d} db={agent_title(SID)}",
        )

    # 缺行面
    s, d = req("PATCH", "/api/sessions/no-such-e2e", token=tok, body={"title": "x"})
    if MODE == "--pre":
        check("missing id blocked by hermes DatabaseError (gap)", s == 500, f"status={s} detail={d}")
    else:
        detail = d.get("detail", "")
        check(
            "missing id 404 with honest cause (not bare not-found)",
            s == 404 and "unreadable" in detail and detail != "Session not found",
            f"status={s} detail={detail}",
        )

    # 健康不变面：鉴权门照旧
    s, d = req("PATCH", f"/api/sessions/{SID}", body={"title": "hacked"})
    check("anon PATCH 401 unchanged", s == 401, f"{s}")

    left = cleanup()
    check("cleanup remaining=0", left is None, f"left={left}")

    print(f"\n== {MODE} result: {len(PASS)} pass / {len(FAIL)} fail ==")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
