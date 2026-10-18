#!/usr/bin/env python3
"""CD-① 取证探针：/api/permission/roles/all 语义错位 live 硬证据（只读探针+临时用户清痕）。"""
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"


def req(method, path, body=None, token=None, form=False):
    data = body
    if form:
        data = urllib.parse.urlencode(body or {}).encode()
    elif body is not None:
        data = json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if not form:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    uname = "cd1probe-" + uuid.uuid4().hex[:8]
    s, d = req("POST", "/api/user/register",
               {"username": uname, "email": uname + "@e2e.local", "password": "E2ePass!234"})
    print("user register:", s, str(d)[:80])
    s, d = req("POST", "/api/user/login",
               {"username": uname, "password": "E2ePass!234"}, form=True)
    tok = d.get("access_token") or d.get("token")
    print("user login:", s, bool(tok))

    s, d = req("GET", "/api/permission/roles/all", token=tok)
    print("GET /api/permission/roles/all ->", s, "resp:", json.dumps(d, ensure_ascii=False)[:160])

    s, d2 = req("GET", "/api/permission/roles/" + uname, token=tok)
    print("GET /api/permission/roles/<real> ->", s, json.dumps(d2, ensure_ascii=False)[:120])

    db = sqlite3.connect("data/opensoul.db")
    cur = db.execute("DELETE FROM users WHERE username = ?", (uname,))
    db.commit()
    print("cleanup deleted:", cur.rowcount)
    db.close()


if __name__ == "__main__":
    main()
