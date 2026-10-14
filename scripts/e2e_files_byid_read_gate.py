#!/usr/bin/env python3
"""DEC-⑦-⑩ live e2e 探针：files 面 by-ID 读面归属闸（真租户双向硬证）。

跑法：python3 scripts/e2e_files_byid_read_gate.py（服务 :8090 已部署本拍代码）。
自清痕：探针文件/租户/用户/timeline 探针行全清，remaining=0 才算过。
"""

import json
import sqlite3
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
MARK = "e2e-byid-" + str(int(time.time()))[-6:]
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def req(method, path, token=None, body=None, raw_body=None, content_type=None):
    url = BASE + path
    data = None
    headers = {}
    if raw_body is not None:
        data = raw_body
        headers["Content-Type"] = content_type or "application/octet-stream"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def jreq(method, path, token=None, body=None):
    code, data = req(method, path, token, body)
    try:
        return code, json.loads(data)
    except Exception:
        return code, {}


def multipart(fields, filename, content, mime="text/plain"):
    boundary = "----e2e" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        )
    parts.append(
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def upload(token, filename, content):
    body, ctype = multipart({}, filename, content)
    code, data = req("POST", "/api/vein/upload", token, raw_body=body, content_type=ctype)
    assert code == 200, f"upload failed {code} {data[:200]}"
    return json.loads(data)["file_id"]


def tl_cleanup(db_path, baseline_rowid, needles):
    def hit(summary, detail, etype):
        blob = (summary or "") + "|" + (detail or "")
        if any(n in blob for n in needles):
            return True
        # batch 汇总事件 summary 不带文件名（detail 只有计数）——窗口内按事件型收
        return etype == "batch_auto_processed"

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT rowid, event_id, summary, detail, event_type FROM events WHERE rowid > ?",
            (baseline_rowid,),
        ).fetchall()
        target = [r for r in rows if hit(r[2], r[3], r[4])]
        for _, eid, _, _, _ in target:
            con.execute("DELETE FROM events WHERE event_id = ?", (eid,))
        con.commit()
        after = con.execute(
            "SELECT rowid, event_id, summary, detail, event_type FROM events WHERE rowid > ?",
            (baseline_rowid,),
        ).fetchall()
        remaining = sum(1 for r in after if hit(r[2], r[3], r[4]))
        return remaining, len(target), len(after)
    finally:
        con.close()


def main():
    code, health = jreq("GET", "/api/health")
    check("health 200", code == 200, str(health.get("status")))

    tl_db = str(__import__("pathlib").Path.home() / "opensoul" / "data" / "timeline.db")
    con = sqlite3.connect(tl_db)
    tl_base = con.execute("SELECT COALESCE(MAX(rowid),0) FROM events").fetchone()[0]
    con.close()

    # 真租户×2 + enterprise register→login→token
    tenants, tokens = {}, {}
    for tag in ("a", "b"):
        code, t = jreq("POST", "/api/nest/tenants", body={"name": f"{MARK}-{tag}"})
        assert code == 200, f"tenant create {code} {t}"
        tenants[tag] = t["tenant_id"]
        uname = f"{MARK}-user-{tag}"
        code, r = jreq(
            "POST",
            "/api/enterprise/auth/register",
            body={"username": uname, "password": "e2e-pass-1", "tenant_id": t["tenant_id"]},
        )
        assert code == 200, f"register {code} {r}"
        code, r = jreq(
            "POST", "/api/enterprise/auth/login", body={"username": uname, "password": "e2e-pass-1"}
        )
        assert code == 200, f"login {code} {r}"
        tokens[tag] = r["access_token"]
    check("真租户×2+token", len(tokens) == 2, str(tenants))

    fid = upload(tokens["a"], f"{MARK}-a.txt", b"alpha-secret-e2e")
    fid_d = upload(None, f"{MARK}-default.txt", b"delta-e2e")

    # 行归属+by-ID 读面三向
    code, meta = jreq("GET", f"/api/vein/files/{fid}", tokens["a"])
    check("A 读己行 200+tenant_id 键", code == 200 and meta.get("tenant_id") == tenants["a"], str(meta.get("tenant_id")))
    code, _ = jreq("GET", f"/api/vein/files/{fid}", tokens["b"])
    check("B 冒读 A meta 404", code == 404)
    code, _ = jreq("GET", f"/api/vein/files/{fid}")
    check("匿名读租户行 404", code == 404)
    code, meta_d = jreq("GET", f"/api/vein/files/{fid_d}")
    check("匿名读 default 行 200 契约不变", code == 200 and meta_d.get("tenant_id") == "default")

    # download（含缓存面）
    code, data = req("GET", f"/api/vein/files/{fid}/download", tokens["a"])
    check("A download 200 真内容", code == 200 and data == b"alpha-secret-e2e")
    code, data = req("GET", f"/api/vein/files/{fid}/download", tokens["b"])
    check("B download 404 零内容泄露", code == 404 and b"alpha-secret" not in data)
    code, _ = req("GET", f"/api/vein/files/{fid_d}/download", tokens["b"])
    check("B download default 行 404", code == 404)

    # versions（先真更新出版本行）
    body, ctype = multipart({}, f"{MARK}-a.txt", b"alpha-v2-e2e")
    code, _ = req("PUT", f"/api/vein/files/{fid}/content", tokens["a"], raw_body=body, content_type=ctype)
    check("A 更新 200", code == 200)
    code, _ = jreq("GET", f"/api/vein/files/{fid}/versions", tokens["b"])
    check("B 读版本史 404", code == 404)
    code, _ = jreq("GET", f"/api/vein/files/{fid}/versions/1", tokens["b"])
    check("B 读单版本 404", code == 404)
    code, vh = jreq("GET", f"/api/vein/files/{fid}/versions", tokens["a"])
    check("A 读版本史 200", code == 200 and vh.get("versions"), str(len(vh.get("versions", []))))

    # promote/auto-process 零副作用
    k_before = jreq("GET", "/api/health")[1].get("knowledge_entries", 0)
    code, _ = jreq("POST", f"/api/vein/files/{fid}/promote", tokens["b"], body={"user_id": "bob"})
    k_after = jreq("GET", "/api/health")[1].get("knowledge_entries", 0)
    check("B promote 404 零入库", code == 404 and k_after == k_before, f"{k_before}->{k_after}")
    code, _ = jreq("POST", f"/api/vein/files/{fid}/auto-process", tokens["b"], body={"user_id": "bob"})
    check("B auto-process 404", code == 404)

    # pipeline run
    code, _ = jreq(
        "POST",
        "/api/pipeline/run",
        tokens["b"],
        body={"file_id": fid, "skip_immune": True, "skip_knowledge": True},
    )
    check("B pipeline/run 404", code == 404)
    code, pr = jreq(
        "POST",
        "/api/pipeline/run",
        tokens["a"],
        body={"file_id": fid, "skip_immune": True, "skip_knowledge": True},
    )
    check("A pipeline/run 200", code == 200 and pr.get("file_id") == fid)

    # batch 只见本租户
    code, batch = jreq(
        "POST",
        "/api/vein/auto-process/batch",
        tokens["a"],
        body={"mime_filter": ["text/"], "limit": 50},
    )
    got = {x["file_id"] for x in batch.get("results", [])}
    check(
        "A batch 只见己行",
        code == 200 and got == {fid} and batch.get("total_files") == 1,
        f"got={got} total={batch.get('total_files')}",
    )
    code, batch_b = jreq(
        "POST",
        "/api/vein/auto-process/batch",
        tokens["b"],
        body={"mime_filter": ["text/"], "limit": 50},
    )
    check("B batch 零跨租户行", code == 200 and batch_b.get("total_files") == 0)

    # 清痕
    code, _ = jreq("DELETE", f"/api/vein/files/{fid}", tokens["a"])
    ok1 = code == 200
    code, _ = jreq("DELETE", f"/api/vein/files/{fid_d}")
    ok2 = code == 200
    check("探针文件清痕", ok1 and ok2)

    for tag in ("a", "b"):
        code, _ = jreq("DELETE", f"/api/nest/tenants/{tenants[tag]}")
        check(f"租户 {tag} 清痕", code in (200, 204, 404), str(code))

    # enterprise 用户 SQL 清（无删除端点）
    ent_db = str(__import__("pathlib").Path.home() / "opensoul" / "data" / "enterprise.db")
    con = sqlite3.connect(ent_db)
    for tag in ("a", "b"):
        con.execute("DELETE FROM users WHERE username = ?", (f"{MARK}-user-{tag}",))
    con.commit()
    left = con.execute("SELECT COUNT(*) FROM users WHERE username LIKE ?", (MARK + "%",)).fetchone()[0]
    con.close()
    check("enterprise 用户清痕", left == 0, f"remaining={left}")

    remaining, touched, window_rows = tl_cleanup(tl_db, tl_base, [MARK, fid, fid_d])
    check(
        "timeline 探针行清痕",
        remaining == 0,
        f"touched={touched} remaining={remaining} window={window_rows}",
    )

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n=== {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
