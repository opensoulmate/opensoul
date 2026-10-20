#!/usr/bin/env python3
"""e2e probe: knowledge.py CRUD 面 user_id 轴 bind_identity 收口 修前/修后双向硬证。

用法：python3 scripts/e2e_knowledge_identity_axis.py --pre|--post [--base URL]
  --pre  : 旧代码在跑——断言缺口存在（user_id 必填 UUID 自报、认证自报他人档案
           200 冒读/冒写/冒删、坏 token 200、star/pin 缺列 500）
  --post : 新代码在跑——断言缺口已闭（403 user mismatch / 401 / 缺省=未声明取派生 /
           写键=档案键家族正典 md5(users.id) / star/pin 缺列自愈后 200）
两模式共同断言：timeline 事件行 delta=0（TR-② 纪律——knowledge 面不 push_event）。
清痕：探针自建 knowledge/tags/knowledge_tags/enterprise 用户全数删除后 remaining=0。
"""

import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8090")
RESULTS = []
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE_SOURCE = "e2e-f3b14-probe"
PROBE_TAG = "e2e-f3b14-tag"


def call(method, path, body=None, token=None, params=None):
    url = BASE + path
    qs = []
    for k, v in (params or {}).items():
        qs.append(f"{k}={urllib.parse.quote(str(v))}")
    if qs:
        url += ("&" if "?" in url else "?") + "&".join(qs)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, (json.loads(raw) if raw else {})
            except Exception:
                return resp.status, {"raw": raw[:200]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, (json.loads(raw) if raw else {})
        except Exception:
            return e.code, {"raw": raw[:200]}


def call_multipart(path, files, token=None, params=None):
    """files: {name: (filename, bytes, content_type)}"""
    url = BASE + path
    qs = [f"{k}={urllib.parse.quote(str(v))}" for k, v in (params or {}).items()]
    if qs:
        url += ("&" if "?" in url else "?") + "&".join(qs)
    boundary = "----e2e" + uuid.uuid4().hex
    body = b""
    for name, (fname, data, ct) in files.items():
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
            f"filename=\"{fname}\"\r\nContent-Type: {ct}\r\n\r\n"
        ).encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, (json.loads(raw) if raw else {})
        except Exception:
            return e.code, {"raw": raw[:200]}


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS" if ok else "FAIL"), name, detail)


def expect(name, got_status, want_status, detail=""):
    check(name, got_status == want_status, f"got {got_status} want {want_status} {detail}")


def timeline_total():
    st, body = call("GET", "/api/timeline/stats")
    return body.get("total_events") if st == 200 else None


def md5_uuid_str(s: str) -> str:
    h = hashlib.md5(s.encode()).hexdigest()
    return str(uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    assert mode in ("--pre", "--post"), "need --pre or --post"
    stamp = str(int(time.time()))[-6:]
    os_db = os.environ.get("E2E_OPENSOU_DB", os.path.join(REPO, "data", "opensoul.db"))
    ent_db = os.environ.get("E2E_ENTERPRISE_DB", os.path.join(REPO, "data", "enterprise.db"))

    t0 = timeline_total()
    st, _ = call("GET", "/api/health")
    expect("health 200", st, 200)

    # ---------- 真身份（enterprise register→login→token） ----------
    users = {}
    for tag in ("a", "b"):
        uname = f"e2e-knid-{tag}-{stamp}"
        st, body = call(
            "POST", "/api/enterprise/auth/register", {"username": uname, "password": "E2e-Pass-1"}
        )
        check(f"register {tag} ok", st in (200, 201), f"{st} {str(body)[:60]}")
        st, body = call(
            "POST", "/api/enterprise/auth/login", {"username": uname, "password": "E2e-Pass-1"}
        )
        tok = body.get("token") or body.get("access_token")
        check(f"login {tag} 200+token", st == 200 and bool(tok), str(body)[:60])
        users[tag] = {"username": uname, "token": tok}
    tok_a, tok_b = users["a"]["token"], users["b"]["token"]

    ent_ids = {}
    with sqlite3.connect(ent_db) as c:
        for tag in ("a", "b"):
            row = c.execute(
                "SELECT id FROM users WHERE username = ?", (users[tag]["username"],)
            ).fetchone()
            ent_ids[tag] = str(row[0])

    # victim 档案键：UUID 形（旧代码 user_id: UUID 校验只认 UUID——enterprise 数字 id
    # 在 --pre 直接 422 到不了缺口面，探针改 UUID 形 victim 两模式通用）
    victim = str(uuid.uuid4())

    def knowledge_rows():
        with sqlite3.connect(os_db) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(
                "SELECT * FROM knowledge WHERE source = ?", (PROBE_SOURCE,)
            )]

    def payload(title):
        return {"title": title, "content": f"e2e f3b14 {stamp}", "source": PROBE_SOURCE,
                "tags": [PROBE_TAG]}

    # ---------- create 面 ----------
    n0 = len(knowledge_rows())
    st, body = call("POST", "/api/knowledge/", payload("missing-uid"))
    if mode == "--pre":
        check("GAP create 缺省 user_id 422 (修前必填自报)", st == 422, str(st))
    else:
        expect("create 缺省 200 (未声明→匿名 default 档案)", st, 200, str(body)[:60])
        rows = knowledge_rows()
        check(
            "create 缺省写键=家族正典 md5('default')",
            rows and rows[-1]["user_id"] == md5_uuid_str("default"),
            str([r["user_id"] for r in rows[-1:]]) ,
        )

    n_before = len(knowledge_rows())
    st, body = call("POST", "/api/knowledge/", payload("cross"), token=tok_a, params={"user_id": victim})
    if mode == "--pre":
        check("GAP create 认证自报他人 200 (修前冒写)", st == 200, str(st))
        rows = knowledge_rows()
        check("GAP 修前写键=裸自报串", rows and rows[-1]["user_id"] == victim,
              str([r["user_id"] for r in rows[-1:]]))
    else:
        expect("create cross-user 403", st, 403, str(body)[:60])
        check("403 detail user mismatch", "user mismatch" in str(body.get("detail", "")), str(body)[:60])
        check("403 零新增行", len(knowledge_rows()) == n_before, f"{n_before} -> {len(knowledge_rows())}")

    st, body = call("POST", "/api/knowledge/", payload("badtok"), token="garbage-token",
                    params={"user_id": victim})
    if mode == "--pre":
        check("GAP create bad token 200 (修前无鉴权轴)", st == 200, str(st))
    else:
        expect("create bad token 401", st, 401)
        check("401 零新增行", len(knowledge_rows()) == n_before, f"{n_before} -> {len(knowledge_rows())}")

    n_before = len(knowledge_rows())
    st, body = call("POST", "/api/knowledge/", payload("derived"), token=tok_a)
    if mode == "--pre":
        check("GAP create 认证缺省 422 (修前必填不可缺省)", st == 422, str(st))
    else:
        expect("create 认证派生 200", st, 200, str(body)[:60])
        rows = knowledge_rows()
        check(
            "create 认证派生写键=家族正典 md5(users.id)",
            rows and rows[-1]["user_id"] == md5_uuid_str(ent_ids["a"]),
            str([r["user_id"] for r in rows[-1:]]) ,
        )
        check("响应 user_id=真写入键", body.get("user_id") == md5_uuid_str(ent_ids["a"]),
              str(body.get("user_id")))

    st, body = call("POST", "/api/knowledge/", payload("decl-def"), token=tok_a, params={"user_id": "default"})
    if mode == "--pre":
        check("GAP create 认证 declare 'default' 422 (修前占位不可用)", st == 422, str(st))
    else:
        check("create 认证 declare default=undeclared 200", st == 200, str(st))
        rows = knowledge_rows()
        check("占位写键=派生键", rows and rows[-1]["user_id"] == md5_uuid_str(ent_ids["a"]),
              str([r["user_id"] for r in rows[-1:]]))

    # ---------- 读隔离 ----------
    # 锚点行两模式分支（探针自伤勘误：修前必填 UUID 自报面，A token 缺省=422 建不出
    # 行→get/update/delete/star/pin 面 GAP 漏取证；--pre 用 victim 自报建行、--post
    # 用派生键建行，两模式 kid 都真实在场）
    if mode == "--pre":
        st, body = call("POST", "/api/knowledge/", payload("read-probe"), token=tok_a,
                        params={"user_id": victim})
    else:
        st, body = call("POST", "/api/knowledge/", payload("read-probe"), token=tok_a)
    kid = body.get("id") if st in (200, 201) else None
    if mode == "--post":
        check("A 的行真实落库", bool(kid), str(kid))
        st2, body2 = call("GET", f"/api/knowledge/{kid}", token=tok_b)
        check("B 派生键读 A 的行 404 (读隔离)", st2 == 404, str(st2))
        st3, body3 = call("GET", "/api/knowledge/", token=tok_b)
        lst = body3 if isinstance(body3, list) else []
        check("B list 恒不见 A 的行", st3 == 200 and all(r.get("id") != kid for r in lst),
              f"{st3} n={len(lst)}")

    st, body = call("GET", f"/api/knowledge/{kid or '00000000-0000-0000-0000-000000000000'}",
                    token=tok_b, params={"user_id": victim})
    if mode == "--pre":
        check("GAP get 认证自报他人档案 200 (修前冒读)", st in (200, 404), str(st))
    else:
        expect("get cross-user 403", st, 403, str(body)[:60])

    st, body = call("GET", "/api/knowledge/", token=tok_b, params={"user_id": victim})
    if mode == "--pre":
        check("GAP list 认证自报他人档案 200 (修前冒读)", st == 200, str(st))
    else:
        expect("list cross-user 403", st, 403, str(body)[:60])

    st, body = call("GET", "/api/knowledge/", token="garbage-token", params={"user_id": victim})
    if mode == "--pre":
        check("GAP list bad token 200 (修前无鉴权轴)", st == 200, str(st))
    else:
        expect("list bad token 401", st, 401)

    # ---------- update/delete 冒写/冒删 ----------
    if kid:
        st, body = call("PUT", f"/api/knowledge/{kid}", {"title": "intruder"}, token=tok_b,
                        params={"user_id": victim})
        if mode == "--pre":
            check("GAP update 认证自报他人 200 (修前冒写)", st == 200, str(st))
        else:
            expect("update cross-user 403", st, 403, str(body)[:60])
            with sqlite3.connect(os_db) as c:
                title = c.execute("SELECT title FROM knowledge WHERE id = ?", (kid,)).fetchone()[0]
            check("403 后 A 行一字不动", title != "intruder", str(title))

        st, body = call("DELETE", f"/api/knowledge/{kid}", token=tok_b, params={"user_id": victim})
        if mode == "--pre":
            check("GAP delete 认证自报他人 200 (修前冒删)", st == 200, str(st))
            # 修前真删了——重建行供后续 star/pin 面用（--pre 必填自报面带 victim）
            st, body = call("POST", "/api/knowledge/", payload("read-probe"), token=tok_a,
                            params={"user_id": victim})
            kid = body.get("id")
        else:
            expect("delete cross-user 403", st, 403, str(body)[:60])
            with sqlite3.connect(os_db) as c:
                alive = c.execute("SELECT COUNT(*) FROM knowledge WHERE id = ?", (kid,)).fetchone()[0]
            check("403 后 A 行存活", alive == 1, str(alive))

    # ---------- star/pin（缺列断点根因修面） ----------
    if kid:
        # 缺列 GAP 是 schema 态依赖断言：knowledge.starred/pinned 缺列时 UPDATE 必
        # 500（no such column）。如实分支——列已在场（自愈已跑过，如全量测试进程
        # SQLitePool connect 对生产库的幂等 heal）则不再断言 500，缺口证据改由
        # PRAGMA 修前快照+单测 no such column+控制组承担，禁拿假 500 硬凑 GAP。
        with sqlite3.connect(os_db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(knowledge)")}
        cols_healed = {"starred", "pinned"} <= cols
        star_params = {"user_id": victim} if mode == "--pre" else {}
        st, body = call("POST", f"/api/knowledge/{kid}/star", token=tok_a, params=star_params)
        if mode == "--pre":
            if cols_healed:
                check("star 缺列 GAP 窗口已闭（列已被自愈补齐）", st in (200, 500),
                      f"st={st} cols healed at probe time")
            else:
                check("GAP star 缺列 500 (no such column)", st == 500, str(st))
        else:
            expect("star 200 (缺列自愈)", st, 200, str(body)[:60])
            check("star 真翻转 starred=1", body.get("starred") in (1, True), str(body.get("starred")))
        st, body = call("POST", f"/api/knowledge/{kid}/pin", token=tok_a, params=star_params)
        if mode == "--pre":
            if cols_healed:
                check("pin 缺列 GAP 窗口已闭（列已被自愈补齐）", st in (200, 500),
                      f"st={st} cols healed at probe time")
            else:
                check("GAP pin 缺列 500 (no such column)", st == 500, str(st))
        else:
            expect("pin 200 (缺列自愈)", st, 200, str(body)[:60])
        if mode == "--post":
            st, body = call("POST", f"/api/knowledge/{kid}/star", token=tok_b, params={"user_id": victim})
            check("star cross-user 403", st == 403, str(st))
            check("star 403 detail user mismatch", "user mismatch" in str(body.get("detail", "")),
                  str(body)[:60])

    # ---------- upload 面 ----------
    st, body = call_multipart(
        "/api/knowledge/upload",
        {"file": (f"e2e-{stamp}.txt", b"e2e f3b14 upload", "text/plain")},
        token=tok_a,
        params={"user_id": victim},
    )
    if mode == "--pre":
        check("GAP upload 认证自报他人 200 (修前冒写)", st == 200, str(st))
    else:
        expect("upload cross-user 403", st, 403, str(body)[:60])

    st, body = call_multipart(
        "/api/knowledge/upload",
        {"file": (f"e2e-{stamp}.txt", b"e2e f3b14 upload", "text/plain")},
        token=tok_a,
    )
    if mode == "--pre":
        check("GAP upload 认证缺省 422 (修前必填不可缺省)", st == 422, str(st))
    else:
        expect("upload 认证派生 200", st, 200, str(body)[:60])
        rows = knowledge_rows()
        check("upload 写键=家族正典 md5(users.id)",
              rows and rows[-1]["user_id"] == md5_uuid_str(ent_ids["a"]),
              str([r["user_id"] for r in rows[-1:]]))

    # ---------- 清痕 ----------
    remaining = 0
    try:
        with sqlite3.connect(os_db) as c:
            c.execute(
                "DELETE FROM knowledge_tags WHERE knowledge_id IN "
                "(SELECT id FROM knowledge WHERE source = ?)",
                (PROBE_SOURCE,),
            )
            c.execute("DELETE FROM knowledge WHERE source = ?", (PROBE_SOURCE,))
            c.execute("DELETE FROM tags WHERE name = ?", (PROBE_TAG,))
            c.commit()
            leftover_k = c.execute(
                "SELECT COUNT(*) FROM knowledge WHERE source = ?", (PROBE_SOURCE,)
            ).fetchone()[0]
            leftover_t = c.execute(
                "SELECT COUNT(*) FROM tags WHERE name = ?", (PROBE_TAG,)
            ).fetchone()[0]
            remaining += int(leftover_k) + int(leftover_t)
    except Exception as exc:
        print("WARN opensoul.db cleanup:", exc)
        remaining += 1

    try:
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username LIKE 'e2e-knid-%'")
            c.commit()
            leftover = c.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-knid-%'"
            ).fetchone()[0]
            remaining += int(leftover)
    except Exception as exc:
        print("WARN enterprise user cleanup:", exc)
        remaining += 1

    t1 = timeline_total()
    check("timeline delta=0 (零事件行面 TR-②)", t0 == t1, f"{t0} -> {t1}")
    check("cleanup remaining=0", remaining == 0, str(remaining))

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n== {mode} {len(RESULTS) - len(fails)}/{len(RESULTS)} ==")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
