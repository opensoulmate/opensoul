#!/usr/bin/env python3
"""DEC-⑦-㉕b live e2e 探针：vein 缓存 API 键面行租户键加轴（真租户双向硬证）。

跑法：python3 scripts/e2e_vein_cache_tenant_axis.py（服务 :8090 已部署本拍代码）。
修前/修后同探针：旧进程先跑一遍（泄露/破坏/覆盖/清全史 FAIL=修前硬证），重启
部署新代码后再跑（全 PASS）。自清痕：探针文件/缓存键/租户/用户/timeline 探针
行全清，remaining=0 且缓存 entries 回基线才算过。
"""

import json
import sqlite3
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
MARK = "e2e-cache-" + str(int(time.time()))[-6:]
RESULTS = []

CACHE_MISS_DETAIL = "Cache miss or expired"
CACHE_ABSENT_DETAIL = "Key not in cache"


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


def b64(raw: bytes) -> str:
    import base64

    return base64.b64encode(raw).decode()


def cput(token, key, raw):
    return jreq("PUT", "/api/vein/cache", token, body={"key": key, "data": b64(raw)})


def cget(token, key):
    return jreq("GET", f"/api/vein/cache/{key}", token)


def cdel(token, key):
    return jreq("DELETE", f"/api/vein/cache/{key}", token)


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
    def hit(summary, detail):
        blob = (summary or "") + "|" + (detail or "")
        return any(n in blob for n in needles)

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT rowid, event_id, summary, detail FROM events WHERE rowid > ?",
            (baseline_rowid,),
        ).fetchall()
        target = [r for r in rows if hit(r[2], r[3])]
        for _, eid, _, _ in target:
            con.execute("DELETE FROM events WHERE event_id = ?", (eid,))
        con.commit()
        after = con.execute(
            "SELECT rowid, event_id, summary, detail FROM events WHERE rowid > ?",
            (baseline_rowid,),
        ).fetchall()
        remaining = sum(1 for r in after if hit(r[2], r[3]))
        return remaining, len(target), len(after)
    finally:
        con.close()


def main():
    code, health = jreq("GET", "/api/health")
    check("health 200", code == 200, str(health.get("status")))

    code, st0 = jreq("GET", "/api/vein/cache/stats")
    entries0 = st0.get("entries", 0)

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

    # ① 写点归属+加性键
    code, r = cput(tokens["a"], f"{MARK}-k1", b"alpha-secret")
    check(
        "A put 行归属派生",
        code == 200 and r.get("tenant_id") == tenants["a"],
        f"code={code} tenant_id={r.get('tenant_id')} expect={tenants['a']}",
    )
    code, r = cput(None, f"{MARK}-k6", b"anon")
    check("匿名 put=default 契约", code == 200 and r.get("tenant_id") == "default", str(r))

    # ② by-ID 读闸：跨租户与 miss 同 detail 不差分
    code, owner = cget(tokens["a"], f"{MARK}-k1")
    check(
        "A get 己行",
        code == 200 and owner.get("data", "").startswith(b"alpha-secret".hex()),
        f"code={code}",
    )
    code, intruder = cget(tokens["b"], f"{MARK}-k1")
    code2, miss = cget(tokens["b"], f"{MARK}-nope")
    check(
        "B 冒读 404 同 detail 不差分",
        code == 404 and code2 == 404
        and intruder.get("detail") == miss.get("detail") == CACHE_MISS_DETAIL,
        f"intruder={intruder.get('detail')} miss={miss.get('detail')}",
    )
    code, anon = cget(None, f"{MARK}-k1")
    check("匿名 default 读不到 tenant 行", code == 404, str(anon.get("detail")))

    # ③ 删闸：同 detail+行零破坏
    code, rd = cdel(tokens["b"], f"{MARK}-k1")
    code2, rm = cdel(tokens["b"], f"{MARK}-nope")
    check(
        "B 冒删 404 同 detail",
        code == 404 and code2 == 404
        and rd.get("detail") == rm.get("detail") == CACHE_ABSENT_DETAIL,
        f"intruder={rd.get('detail')} miss={rm.get('detail')}",
    )
    code, still = cget(tokens["a"], f"{MARK}-k1")
    check("B 删后 A 行零破坏", code == 200 and still.get("data", "").startswith(b"alpha-secret".hex()), str(code))

    # ④ 同 key 异租户独立行（修前 B 覆盖 A）
    cput(tokens["a"], f"{MARK}-k2", b"AAA")
    cput(tokens["b"], f"{MARK}-k2", b"BBB")
    code, ra = cget(tokens["a"], f"{MARK}-k2")
    code2, rb = cget(tokens["b"], f"{MARK}-k2")
    check(
        "同 key 异租户互不覆盖",
        code == 200 and code2 == 200
        and ra.get("data", "").startswith(b"AAA".hex())
        and rb.get("data", "").startswith(b"BBB".hex()),
        f"a={ra.get('data','')[:6]} b={rb.get('data','')[:6]}",
    )

    # ⑤ clear 租户范围（修前=清全史）
    cput(tokens["a"], f"{MARK}-k3", b"1")
    cput(tokens["a"], f"{MARK}-k4", b"2")
    cput(tokens["b"], f"{MARK}-k5", b"3")
    # A 名下此刻恰 4 行（k1 读闸例+k2 异租户例+k3+k4）
    code, rc = jreq("POST", "/api/vein/cache/clear", tokens["a"])
    check("A clear 租户范围", code == 200 and rc.get("cleared") == 4, str(rc))
    code, _ = cget(tokens["a"], f"{MARK}-k3")
    code2, keep = cget(tokens["b"], f"{MARK}-k5")
    check("clear 后 A 空 B 行存活", code == 404 and code2 == 200, f"a={code} b={code2}")

    # ⑥ file: 热缓存行归属（⑦-⑩ 缓存旁路面收口）
    fid = upload(tokens["a"], f"{MARK}-a.txt", b"hot-file-payload-e2e")
    code, fget_b = cget(tokens["b"], f"file:{fid}")
    code2, fget_a = cget(tokens["a"], f"file:{fid}")
    check(
        "file: 热缓存面 B 404 A 200",
        code == 404
        and code2 == 200
        and fget_a.get("data", "").startswith(b"hot-file-payload-e2e".hex()),
        f"b={code} a={code2}",
    )
    code, _ = jreq("GET", f"/api/vein/files/{fid}/download", tokens["b"])
    check("download 行闸回归钉 B 404", code == 404, str(code))
    code, dl = req("GET", f"/api/vein/files/{fid}/download", tokens["a"])
    check("A download 缓存命中面", code == 200 and dl == b"hot-file-payload-e2e", f"code={code}")

    # ⑦ stats 系统视图+坏 token 401
    code, st = jreq("GET", "/api/vein/cache/stats")
    check(
        "stats 系统视图键集不变",
        code == 200
        and set(st.keys())
        == {
            "entries",
            "current_size_bytes",
            "max_size_bytes",
            "usage_percent",
            "hits",
            "misses",
            "hit_rate",
            "default_ttl_seconds",
        },
        str(sorted(st.keys())),
    )
    bad = "not-a-jwt-token"
    code, _ = cput(bad, f"{MARK}-evil", b"x")
    code2, _ = cget(bad, f"{MARK}-k1")
    code3, _ = jreq("POST", "/api/vein/cache/clear", bad)
    check("坏 token 401×3", code == 401 and code2 == 401 and code3 == 401, f"{code}/{code2}/{code3}")

    # 清痕
    code, _ = jreq("DELETE", f"/api/vein/files/{fid}", tokens["a"])
    ok1 = code == 200
    rc_a = jreq("POST", "/api/vein/cache/clear", tokens["a"])[1]
    rc_b = jreq("POST", "/api/vein/cache/clear", tokens["b"])[1]
    rc_n = jreq("POST", "/api/vein/cache/clear", None)[1]
    cleared = (rc_a.get("cleared", 0) or 0) + (rc_b.get("cleared", 0) or 0) + (rc_n.get("cleared", 0) or 0)
    code, st1 = jreq("GET", "/api/vein/cache/stats")
    check(
        "缓存清痕回基线",
        ok1 and st1.get("entries") == entries0,
        f"cleared={cleared} entries {entries0}->{st1.get('entries')}",
    )

    for tag in ("a", "b"):
        code, _ = jreq("DELETE", f"/api/nest/tenants/{tenants[tag]}")
        check(f"租户 {tag} 清痕", code in (200, 204, 404), str(code))

    ent_db = str(__import__("pathlib").Path.home() / "opensoul" / "data" / "enterprise.db")
    con = sqlite3.connect(ent_db)
    for tag in ("a", "b"):
        con.execute("DELETE FROM users WHERE username = ?", (f"{MARK}-user-{tag}",))
    con.commit()
    left = con.execute("SELECT COUNT(*) FROM users WHERE username LIKE ?", (MARK + "%",)).fetchone()[0]
    con.close()
    check("enterprise 用户清痕", left == 0, f"remaining={left}")

    remaining, touched, window_rows = tl_cleanup(tl_db, tl_base, [MARK, fid])
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
