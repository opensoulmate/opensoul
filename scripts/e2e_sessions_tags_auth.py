#!/usr/bin/env python3
"""e2e：tags 写端点身份轴收口（SESS-③）真服务探针。

对 live :8090 真栈（urllib）双向硬证：
- 匿名/坏 token 写口 401 零落库（修前 200 真写=缺口面）
- 真 token add/remove 真落/真删（响应+DB 回读双证）
- 读面保留既有浏览语义（无鉴权 200）
- 取证后清痕 remaining=0

用法：python3 scripts/e2e_sessions_tags_auth.py
"""
import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:8090"
OPENVEN = "/home/climbing/opensoul"
SID = f"e2e-tags-{uuid.uuid4().hex[:8]}"
DB = f"{OPENVEN}/data/opensoul.db"

sys.path.insert(0, OPENVEN)
from src.services.auth import create_access_token  # noqa: E402

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
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def rows():
    with sqlite3.connect(DB) as c:
        try:
            return [
                r[0]
                for r in c.execute(
                    "SELECT tag_name FROM session_tags WHERE session_id = ? ORDER BY tag_name",
                    (SID,),
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            return []


def cleanup():
    with sqlite3.connect(DB) as c:
        c.execute("DELETE FROM session_tags WHERE session_id = ?", (SID,))
        c.commit()
    return rows()


def main():
    tok = create_access_token(user_id=uuid.UUID(str(uuid.uuid4())), role="user")

    s, d = req("GET", "/api/sessions/health")
    check("health", s == 200 and d.get("component") == "SessionsAPI", f"{s}")

    # 匿名/坏 token 写口拒绝零副作用
    s, d = req("POST", f"/api/sessions/{SID}/tags", body={"tag_name": "hacked"})
    check("anon add 401", s == 401 and rows() == [], f"{s} rows={rows()}")
    s, d = req(
        "POST",
        f"/api/sessions/{SID}/tags",
        token="garbage-token",
        body={"tag_name": "hacked"},
    )
    check("bad token add 401", s == 401 and rows() == [], f"{s} rows={rows()}")

    # 真 token add 真落库
    s, d = req("POST", f"/api/sessions/{SID}/tags", token=tok, body={"tag_name": "RealTag"})
    check(
        "authed add 200 + shape",
        s == 200
        and d == {"session_id": SID, "tag": "realtag", "status": "added"}
        and rows() == ["realtag"],
        f"{s} {d} rows={rows()}",
    )

    # 匿名/坏 token remove 拒绝行存活
    s, d = req("DELETE", f"/api/sessions/{SID}/tags/realtag")
    check("anon remove 401 row survives", s == 401 and rows() == ["realtag"], f"{s} rows={rows()}")
    s, d = req("DELETE", f"/api/sessions/{SID}/tags/realtag", token="garbage-token")
    check(
        "bad token remove 401 row survives", s == 401 and rows() == ["realtag"], f"{s} rows={rows()}"
    )

    # 真 token remove 真删
    s, d = req("DELETE", f"/api/sessions/{SID}/tags/realtag", token=tok)
    check(
        "authed remove 200 + shape",
        s == 200
        and d == {"session_id": SID, "tag": "realtag", "status": "removed"}
        and rows() == [],
        f"{s} {d} rows={rows()}",
    )

    # 读面不变（既有浏览语义无鉴权）
    req("POST", f"/api/sessions/{SID}/tags", token=tok, body={"tag_name": "visible"})
    s, d = req("GET", f"/api/sessions/{SID}/tags")
    ok_read = s == 200 and [t["name"] for t in d.get("tags", [])] == ["visible"]
    s2, d2 = req("GET", "/api/sessions/tags/all")
    names = [t["name"] for t in d2.get("tags", [])]
    check(
        "read faces no-auth 200",
        ok_read and s2 == 200 and "visible" in names,
        f"{s}/{s2} names={names}",
    )

    remaining = cleanup()
    check("cleanup remaining=0", remaining == [], f"remaining={remaining}")

    print(f"\n== {len(PASS)}/{len(PASS) + len(FAIL)} PASS ==")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
