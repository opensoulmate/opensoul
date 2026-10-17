#!/usr/bin/env python3
"""e2e：delete/get/messages hermes 路径 DatabaseError 回落韧性（SESS-④ 同族）真服务探针。

live :8090 的 ~/.hermes/state.db 是真损坏库（SESS-① 取证：not-a-db）——降级条件
真实存在，无需模拟。双向硬证：
- --pre（重启前旧码内存）：真 token GET/DELETE/{id}/messages agent 会话 → 500
  （「works for both」契约破硬证：hermes SELECT DatabaseError 直通 500，agent
  回落整条路被堵死）
- --post（重启载新码）：同探针 → GET 200 / messages 200 带 hermes_unreadable
  加性键 / DELETE 200 真删 + 缺行诚实真因
- 取证后清痕 remaining=0（delete 端点自身即清理通道）

用法：python3 scripts/e2e_sessions_delete_get_fallback.py [--pre|--post]
"""

import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:8090"
OPENVEN = "/home/climbing/opensoul"
SID = f"e2e-dgfb-{uuid.uuid4().hex[:8]}"
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


def agent_row_count(table, sid):
    with sqlite3.connect(DB) as c:
        return c.execute(
            f"SELECT COUNT(*) FROM {table} WHERE session_id = ?", (sid,)
        ).fetchone()[0]


def cleanup():
    # delete 端点修通后=自身清理通道；失败回落 SQL 直清（探针自足）
    s, d = req("DELETE", f"/api/sessions/{SID}", token=TOK)
    if s != 200 or d.get("success") is not True:
        with sqlite3.connect(DB) as c:
            c.execute("DELETE FROM agent_sessions WHERE id = ?", (SID,))
            c.commit()
    with sqlite3.connect(DB) as c:
        left = c.execute("SELECT COUNT(*) FROM agent_sessions WHERE id = ?", (SID,)).fetchone()[0]
    return left + agent_row_count("agent_messages", SID)


TOK = None


def main():
    global TOK
    TOK = create_access_token(user_id=uuid.UUID(str(uuid.uuid4())), role="user")

    s, d = req("GET", "/api/sessions/health")
    check("health", s == 200 and d.get("component") == "SessionsAPI", f"{s}")

    # 建 agent 会话（只碰 agent 面，不触 hermes）
    s, d = req("POST", "/api/sessions", token=TOK, body={"id": SID, "name": "e2e-dgfb"})
    check("create agent session", s == 200 and d.get("session_id") == SID, f"{s} {d}")

    # 核心面 1：GET agent 会话
    s, d = req("GET", f"/api/sessions/{SID}", token=TOK)
    if MODE == "--pre":
        check("GET agent session blocked by hermes DatabaseError (gap)", s == 500, f"status={s} detail={d}")
    else:
        check(
            "GET agent session 200 falls back despite corrupt hermes",
            s == 200 and d.get("id") == SID and d.get("title") == "e2e-dgfb",
            f"status={s} detail={d}",
        )

    # 核心面 2：GET messages（降级加性键）
    s, d = req("GET", f"/api/sessions/{SID}/messages", token=TOK)
    if MODE == "--pre":
        check("GET messages blocked by hermes DatabaseError (gap)", s == 500, f"status={s} detail={d}")
    else:
        check(
            "GET messages 200 + hermes_unreadable honest marker",
            s == 200 and d.get("hermes_unreadable") is True and d.get("messages") == [],
            f"status={s} detail={d}",
        )

    # 缺行诚实面（get）
    s, d = req("GET", "/api/sessions/no-such-e2e", token=TOK)
    if MODE == "--pre":
        check("GET missing blocked by hermes DatabaseError (gap)", s == 500, f"status={s} detail={d}")
    else:
        detail = d.get("detail", "")
        check(
            "GET missing 404 with honest cause (not bare not-found)",
            s == 404 and "unreadable" in detail and detail != "Session not found",
            f"status={s} detail={detail}",
        )

    # 缺行诚实面（delete）
    s, d = req("DELETE", "/api/sessions/no-such-e2e", token=TOK)
    if MODE == "--pre":
        check("DELETE missing blocked by hermes DatabaseError (gap)", s == 500, f"status={s} detail={d}")
    else:
        err = d.get("error", "")
        check(
            "DELETE missing honest error (not bare not-found)",
            s == 200 and d.get("success") is False and "unreadable" in err and err != "Session not found",
            f"status={s} detail={d}",
        )

    # 核心面 3：DELETE agent 会话真删
    s, d = req("DELETE", f"/api/sessions/{SID}", token=TOK)
    if MODE == "--pre":
        check("DELETE agent session blocked by hermes DatabaseError (gap)", s == 500, f"status={s} detail={d}")
    else:
        check(
            "DELETE agent session 200 truly removes row",
            s == 200 and d == {"success": True, "deleted_session": SID},
            f"status={s} detail={d}",
        )

    # 健康不变面：鉴权门照旧
    s, d = req("GET", f"/api/sessions/{SID}")
    check("anon GET 401 unchanged", s == 401, f"{s}")

    left = cleanup()
    check("cleanup remaining=0", left == 0, f"left={left}")

    print(f"\n== {MODE} result: {len(PASS)} pass / {len(FAIL)} fail ==")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
