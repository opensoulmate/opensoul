#!/usr/bin/env python3
"""CD-①-C 修复件前置探针②：admin 身份下 /api/permission/policies 真源元素形状只读取证。"""
import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/home/climbing/opensoul")
BASE = "http://127.0.0.1:8090"


def req(method, path, token=None):
    r = urllib.request.Request(BASE + path, method=method)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def main():

    import sqlite3

    from src.services.auth import create_access_token

    db = sqlite3.connect("data/opensoul.db")
    row = db.execute("SELECT id, username FROM users WHERE username='admin'").fetchone()
    db.close()
    print("admin row:", row)
    uid = row[0]
    try:
        uuid_id = __import__("uuid").UUID(str(uid))
    except Exception:
        uuid_id = uid
    tok = create_access_token(uuid_id)
    st, d = req("GET", "/api/permission/policies", token=tok)
    print("GET /api/permission/policies ->", st)
    print("  policies:", json.dumps(d.get("policies"), ensure_ascii=False)[:400])
    print("  roles:", json.dumps(d.get("roles"), ensure_ascii=False)[:400])
    st2, d2 = req("GET", "/api/permission/roles/admin", token=tok)
    print("GET /api/permission/roles/admin ->", st2, json.dumps(d2, ensure_ascii=False)[:120])


if __name__ == "__main__":
    main()
