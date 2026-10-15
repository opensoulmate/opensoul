#!/usr/bin/env python3
"""DEC-⑦-㉕ vein chunked 上传会话面行租户键加轴 live e2e 探针。

真栈（urllib）打 live :8090：nest 建真租户×2 → enterprise register→login→token
真身份 → A/B/匿名建会话行归属（含自报丢弃）→ list 恒过滤三面互斥（修前全量
泄露文件名）→ by-ID 归属闸（status/chunk/complete/cancel 跨租户与缺行同契约
+分片零投毒+零内容窃取+行零破坏）→ own 全流程 complete 落库行归属 → 坏 token
电池 401 零副作用 → /stats 计数基线往返 → 清痕 remaining=0。

用法：python3 scripts/e2e_vein_chunked_axis.py [--base http://127.0.0.1:8090]
修前后各跑一次同探针（before=旧代码现场、after=重启新代码），对照取证。
"""

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8090"
RESULTS = []
MARKER = "e2e-vca-"


def call(method, path, body=None, token=None, params=None):
    url = BASE + path
    if params:
        qs = urllib.parse.urlencode(params)
        url += ("&" if "?" in path else "?") + qs
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt) if txt else None
        except Exception:
            return e.code, {"raw": txt[:200]}


def call_multipart(path, field, filename, payload, token=None):
    """multipart/form-data 单文件上传（分片面专用）。"""
    boundary = "----e2e" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt) if txt else None
        except Exception:
            return e.code, {"raw": txt[:200]}


def check(name: str, ok: object, detail: object = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail) if detail else ""))


def marker_sessions(rows) -> list:
    return [r for r in rows if str(r.get("filename", "")).startswith(MARKER)]


def marker_files(rows) -> list:
    return [r for r in rows if str(r.get("name", "")).startswith(MARKER)]


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base

    st, health = call("GET", "/api/health")
    check("health", st == 200, health)
    st, vs0 = call("GET", "/api/vein/stats")
    G0 = (vs0 or {}).get("uploads", {}).get("active_sessions", -1)
    check("vein stats baseline", st == 200 and G0 >= 0, vs0)

    # ── 坏 token 电池 401×6 + 零副作用（先于一切写点）────────────────
    # 注：chunk 口必须带真实分片 part（缺 part=422 先于 handler，探针自伤已修）。
    codes = []
    st, r = call(
        "POST",
        "/api/vein/upload/chunked/init",
        {"filename": "badtok.bin", "total_size": 2, "chunk_size": 2},
        token="not-a-token",
    )
    codes.append(st)
    badtok_sid = (r or {}).get("upload_id", "")  # 修前 200 会建会话=零副作用反证
    st, _ = call_multipart("/api/vein/upload/chunked/nope/chunk/0", "file", "c0.bin", b"ab", token="not-a-token")
    codes.append(st)
    for method, path, body in (
        ("POST", "/api/vein/upload/chunked/nope/complete", None),
        ("GET", "/api/vein/upload/chunked/nope", None),
        ("GET", "/api/vein/upload/chunked", None),
        ("DELETE", "/api/vein/upload/chunked/nope", None),
    ):
        st, _ = call(method, path, body, token="not-a-token")
        codes.append(st)
    check("bad token 401 x6", codes == [401] * 6, codes)
    st, vs1 = call("GET", "/api/vein/stats")
    check(
        "bad token zero side effect",
        (vs1 or {}).get("uploads", {}).get("active_sessions") == G0,
        (G0, vs1),
    )

    # ── 真租户×2 + enterprise 真身份 ─────────────────────────────────
    tag = MARKER + uuid.uuid4().hex[:8]
    tenants = {}
    users = {}
    for who in ("a", "b"):
        name = f"tenant-{tag}-{who}"
        st, r = call("POST", "/api/nest/tenants", {"tenant_id": name, "name": name})
        real_tenant = (r or {}).get("tenant_id", "")
        check(f"nest create {who}", st == 200 and bool(real_tenant), r)
        tenants[who] = real_tenant
        st, r = call(
            "POST",
            "/api/enterprise/auth/register",
            {"username": f"{tag}-{who}", "password": "pw-" + tag, "tenant_id": real_tenant},
        )
        check(f"ent register {who}", st in (200, 201), r)
        st, r = call(
            "POST",
            "/api/enterprise/auth/login",
            {"username": f"{tag}-{who}", "password": "pw-" + tag},
        )
        check(f"ent login {who}", st == 200 and r and r.get("access_token"), r)
        users[who] = (r or {}).get("access_token", "")

    # ── create 写点归属：A×5（a2 含自报）/B×1/匿名×1 ─────────────────
    sids = {}
    plan = [
        ("a1", "a"),  # own 全流程
        ("a2", "a"),  # 自报+list+跨 status
        ("a3", "a"),  # 跨 chunk 投毒面
        ("a4", "a"),  # 跨 complete 窃取面
        ("a5", "a"),  # 跨 cancel 破坏面
        ("b1", "b"),
        ("d1", None),
    ]
    for key, who in plan:
        body = {"filename": f"{tag}-{key}.bin", "total_size": 4, "chunk_size": 2}
        if key == "a2":
            body["tenant_id"] = "attacker-tenant"  # 自报必须不进落行
        st, r = call("POST", "/api/vein/upload/chunked/init", body, token=users[who] if who else None)
        check(f"init {key}", st == 200 and r and r.get("upload_id"), r)
        sids[key] = (r or {}).get("upload_id", "")
        want = tenants[who] if who else "default"
        check(f"row tenant {key}", (r or {}).get("tenant_id") == want, (r or {}).get("tenant_id"))

    # ── list 恒过滤三面互斥 + 文件名零跨租户泄露 ─────────────────────
    st, la = call("GET", "/api/vein/upload/chunked", token=users["a"])
    st, lb = call("GET", "/api/vein/upload/chunked", token=users["b"])
    st, ln = call("GET", "/api/vein/upload/chunked")
    fa = {s.get("filename") for s in marker_sessions((la or {}).get("sessions", []))}
    fb = {s.get("filename") for s in marker_sessions((lb or {}).get("sessions", []))}
    fn = {s.get("filename") for s in marker_sessions((ln or {}).get("sessions", []))}
    check("list A scoped", fa == {f"{tag}-{k}.bin" for k in ("a1", "a2", "a3", "a4", "a5")}, fa)
    check("list B scoped", fb == {f"{tag}-b1.bin"}, fb)
    check("list anon default face", fn == {f"{tag}-d1.bin"}, fn)
    check("no cross filename leak", not (f"{tag}-b1.bin" in fa or f"{tag}-a1.bin" in fb), (fa, fb))

    # ── by-ID 归属闸四面 ───────────────────────────────────────────
    st, cross = call("GET", f"/api/vein/upload/chunked/{sids['a2']}", token=users["b"])
    st2, missing = call("GET", "/api/vein/upload/chunked/no-such-sess", token=users["b"])
    check("cross status 404", cross.get("status") is None and cross.get("detail") == "Upload session not found", cross)
    check(
        "cross==missing same detail",
        cross.get("detail") == missing.get("detail") == "Upload session not found",
        (cross, missing),
    )

    st, po = call_multipart(
        f"/api/vein/upload/chunked/{sids['a3']}/chunk/0", "file", "c0.bin", b"XX", token=users["b"]
    )
    check("cross chunk 404", st == 404 and (po or {}).get("detail") == "Upload session not found", po)
    st, sa3 = call("GET", f"/api/vein/upload/chunked/{sids['a3']}", token=users["a"])
    check("cross chunk zero poison", (sa3 or {}).get("received_chunks") == [], sa3)

    # a4 先由 A 填满（窃取面需要 complete-able 会话）
    for i, blob in enumerate((b"ab", b"cd")):
        st, _ = call_multipart(f"/api/vein/upload/chunked/{sids['a4']}/chunk/{i}", "file", f"c{i}.bin", blob, token=users["a"])
    st, th = call("POST", f"/api/vein/upload/chunked/{sids['a4']}/complete", token=users["b"])
    check("cross complete 404", st == 404 and (th or {}).get("detail") == "Upload session not found", th)
    st, fb_files = call("GET", "/api/vein/files", token=users["b"])
    st, fa_files = call("GET", "/api/vein/files", token=users["a"])
    check(
        "cross complete zero file row",
        not marker_files((fb_files or {}).get("files", []))
        and not marker_files((fa_files or {}).get("files", [])),
        (fb_files, fa_files),
    )
    st, sa4 = call("GET", f"/api/vein/upload/chunked/{sids['a4']}", token=users["a"])
    check("theft session survives", st == 200 and (sa4 or {}).get("is_complete") is True, sa4)

    st, cx = call("DELETE", f"/api/vein/upload/chunked/{sids['a5']}", token=users["b"])
    check("cross cancel 404", st == 404 and (cx or {}).get("detail") == "Upload session not found", cx)
    st, sa5 = call("GET", f"/api/vein/upload/chunked/{sids['a5']}", token=users["a"])
    check("cross cancel row survives", st == 200, sa5)

    # ── own 全流程：fill→complete 落库行归属+会话清面 ────────────────
    for i, blob in enumerate((b"ab", b"cd")):
        st, _ = call_multipart(f"/api/vein/upload/chunked/{sids['a1']}/chunk/{i}", "file", f"c{i}.bin", blob, token=users["a"])
    st, done = call("POST", f"/api/vein/upload/chunked/{sids['a1']}/complete", token=users["a"])
    check("own complete 200", st == 200 and (done or {}).get("file_id"), done)
    st, fa2 = call("GET", "/api/vein/files", token=users["a"])
    own_rows = [r for r in (fa2 or {}).get("files", []) if r.get("name") == f"{tag}-a1.bin"]
    check(
        "own file row attributed",
        len(own_rows) == 1 and own_rows[0].get("tenant_id") == tenants["a"],
        own_rows,
    )
    st, gone = call("GET", f"/api/vein/upload/chunked/{sids['a1']}", token=users["a"])
    check("own session cleaned", st == 404, gone)

    # ── /stats 计数系统视图（本拍会话数对账）────────────────────────
    st, vs2 = call("GET", "/api/vein/stats")
    alive = len(plan) - 1  # a1 已完成清面
    check(
        "stats active sessions accounted",
        (vs2 or {}).get("uploads", {}).get("active_sessions") == G0 + alive,
        (G0, alive, vs2),
    )

    # ── own cancel 正对照（a5 本人取消）─────────────────────────────
    st, oc = call("DELETE", f"/api/vein/upload/chunked/{sids['a5']}", token=users["a"])
    check("own cancel 200", st == 200, oc)

    # ── 清痕 ────────────────────────────────────────────────────────
    if badtok_sid:  # 修前坏 token 建会话残留（修后 401 不会存在）
        call("DELETE", f"/api/vein/upload/chunked/{badtok_sid}")
    for key, sid in sids.items():
        if not sid:
            continue
        tok = users["a"] if key.startswith("a") else (users["b"] if key.startswith("b") else None)
        call("DELETE", f"/api/vein/upload/chunked/{sid}", token=tok)
    for who in ("a", "b"):
        st, sweep = call("GET", "/api/vein/files", token=users[who])
        for row in (sweep or {}).get("files", []):
            if str(row.get("name", "")).startswith(MARKER):
                call("DELETE", "/api/vein/files/" + row["file_id"], token=users[who])
    st, sweep_n = call("GET", "/api/vein/files")
    for row in (sweep_n or {}).get("files", []):
        if str(row.get("name", "")).startswith(MARKER):
            call("DELETE", "/api/vein/files/" + row["file_id"])

    for tok in (users["a"], users["b"], None):  # marker 会话全量清扫（含历史残留）
        st, sweep = call("GET", "/api/vein/upload/chunked", token=tok)
        for row in marker_sessions((sweep or {}).get("sessions", [])):
            call("DELETE", "/api/vein/upload/chunked/" + row["upload_id"], token=tok)

    time.sleep(0.2)
    ent_db = Path.home() / "opensoul" / "data" / "enterprise.db"
    nest_db = Path.home() / "opensoul" / "data" / "nest.db"
    for who in ("a", "b"):
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username=?", (f"{tag}-{who}",))
            c.commit()
    with sqlite3.connect(nest_db) as c:
        for who in ("a", "b"):
            c.execute("DELETE FROM access_log WHERE tenant_id=?", (tenants[who],))
        c.commit()
    with sqlite3.connect(nest_db) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(tenants)")]
        key = "tenant_id" if "tenant_id" in cols else cols[0]
        for who in ("a", "b"):
            c.execute(f"DELETE FROM tenants WHERE {key}=?", (tenants[who],))
        c.commit()

    remaining = []
    for tok in (users["a"], users["b"], None):
        st, sweep = call("GET", "/api/vein/upload/chunked", token=tok)
        remaining += [r.get("upload_id") for r in marker_sessions((sweep or {}).get("sessions", []))]
        st, swf = call("GET", "/api/vein/files", token=tok)
        remaining += [r.get("file_id") for r in marker_files((swf or {}).get("files", []))]
    st, vs3 = call("GET", "/api/vein/stats")
    check(
        "remaining=0",
        not remaining
        and (vs3 or {}).get("uploads", {}).get("active_sessions") == G0,
        (remaining, (vs3 or {}).get("uploads", {}), G0),
    )

    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"\n== {ok}/{len(RESULTS)} ==")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
