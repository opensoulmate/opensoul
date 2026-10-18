#!/usr/bin/env python3
"""CD-① 修复件前置探针：四处真源形状只读取证（临时用户清痕）。"""
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
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def main():
    uname = "cd1fix-" + uuid.uuid4().hex[:8]
    s, d = req("POST", "/api/user/register",
               {"username": uname, "email": uname + "@e2e.local", "password": "E2ePass!234"})
    print("register:", s)
    s, d = req("POST", "/api/user/login",
               {"username": uname, "password": "E2ePass!234"}, form=True)
    tok = d.get("access_token") or d.get("token")
    print("login:", s, bool(tok))

    s, d = req("GET", "/api/permission/policies", token=tok)
    print("GET /api/permission/policies ->", s)
    print("  policies:", json.dumps(d.get("policies"), ensure_ascii=False)[:300])
    print("  roles:", json.dumps(d.get("roles"), ensure_ascii=False)[:300])

    s, d = req("GET", "/api/dir?path=/tmp", token=tok)
    print("GET /api/dir?path=/tmp ->", s, "keys:", list(d.keys()))
    print("  entries sample:", json.dumps((d.get("entries") or [])[:1], ensure_ascii=False)[:200])

    s, d = req("GET", "/api/workspace/dir?path=/tmp", token=tok)
    print("GET /api/workspace/dir?path=/tmp ->", s, str(d)[:100])

    s, d = req("GET", "/api/workspace/file?path=/tmp", token=tok)
    print("GET /api/workspace/file ->", s, str(d)[:60])
    s, d = req("GET", "/api/file?path=/etc/hostname", token=tok)
    print("GET /api/file?path=/etc/hostname ->", s, "keys:", list(d.keys()) if isinstance(d, dict) else type(d))

    s, d = req("GET", "/openapi.json")
    sch = d.get("components", {}).get("schemas", {}).get("ScreenshotResponse", {})
    print("openapi ScreenshotResponse:", json.dumps(sch.get("properties", {}).keys() and list(sch.get("properties", {}).keys()), ensure_ascii=False))
    ocr = d.get("components", {}).get("schemas", {})
    ocr_keys = [k for k in ocr if "OCR" in k]
    for k in ocr_keys:
        print("openapi", k, list(sch and ocr[k].get("properties", {}).keys()))

    db = sqlite3.connect("data/opensoul.db")
    cur = db.execute("DELETE FROM users WHERE username = ?", (uname,))
    db.commit()
    print("cleanup deleted:", cur.rowcount)
    db.close()


if __name__ == "__main__":
    main()
