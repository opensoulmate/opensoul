#!/usr/bin/env python3
"""e2e probe: chat·export·feedback 面 user_id 轴 bind_identity 收口 修前/修后双向硬证。

用法：python3 scripts/e2e_chat_export_feedback_identity_axis.py --pre|--post [--base URL]
  --pre  : 旧代码在跑——断言缺口存在（user_id 必填自报、认证自报他人档案 200 泄露/
           冒写、坏 token 200、export 聚合查询 array_agg SQLite 必 500）
  --post : 新代码在跑——断言缺口已闭（403 user mismatch / 401 / 缺省=未声明取派生 /
           写键=档案键家族正典）
两模式共同断言：timeline 事件行 delta=0（TR-② 纪律——三面均不 push_event）。
清痕：探针自建 knowledge/tags/enterprise 用户全数删除后 remaining=0。
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
PROBE_SOURCE = "e2e-f3b13-probe"


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


CONV = "发现 SQLite 嵌套连接会死锁 e2e-f3b13-%s"


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
        uname = f"e2e-cefid-{tag}-{stamp}"
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
    tok_a = users["a"]["token"]

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

    # tags 预存快照（清理只删新增，禁误删既有行）
    probe_keys = [md5_uuid_str("default"), md5_uuid_str("alice-cef"), md5_uuid_str(ent_ids["a"])]
    tag_names = ("auto-extracted", "knowledge", "user-correction", "decision", "discovery")
    pre_tag_ids = set()
    with sqlite3.connect(os_db) as c:
        for key in probe_keys:
            for r in c.execute(
                "SELECT id FROM tags WHERE user_id = ? AND name IN (?,?,?,?,?)", (key, *tag_names)
            ):
                pre_tag_ids.add(r[0])

    def knowledge_rows(src=PROBE_SOURCE):
        with sqlite3.connect(os_db) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM knowledge WHERE source = ?", (src,))]

    # ---------- chat 面 ----------
    st, _ = call("POST", "/api/chat/", {"question": f"e2e f3b13 {stamp}", "stream": False})
    if mode == "--pre":
        check("GAP chat 缺省 user_id 422 (修前必填自报)", st == 422, str(st))
    else:
        expect("chat 缺省 200 (未声明→派生)", st, 200)

    st, body = call(
        "POST",
        "/api/chat/",
        {"question": f"e2e f3b13 {stamp}", "stream": False},
        token=tok_a,
        params={"user_id": victim},
    )
    if mode == "--pre":
        check("GAP chat 认证自报他人 200 (修前冒读)", st == 200, str(st))
    else:
        expect("chat cross-user 403", st, 403, str(body)[:60])
        check("403 detail user mismatch", "user mismatch" in str(body.get("detail", "")), str(body)[:60])

    st, _ = call(
        "POST",
        "/api/chat/",
        {"question": f"e2e f3b13 {stamp}", "stream": False},
        token="garbage-token",
        params={"user_id": md5_uuid_str("default")},
    )
    if mode == "--pre":
        check("GAP chat bad token 200 (修前无鉴权轴)", st == 200, str(st))
    else:
        expect("chat bad token 401", st, 401)

    st, _ = call(
        "POST",
        "/api/chat/",
        {"question": f"e2e f3b13 {stamp}", "stream": False},
        token=tok_a,
        params={"user_id": "default"},
    )
    if mode == "--pre":
        check("GAP chat 认证 declare 'default' 422 (修前占位/缺省不可用)", st == 422, str(st))
    else:
        check("chat 认证 declare default=undeclared 200", st == 200, str(st))

    # ---------- export 面 ----------
    st, body = call("GET", "/api/export/json")
    if mode == "--pre":
        check("GAP export/json 缺省 422 (修前必填自报)", st == 422, str(st))
    else:
        expect("export/json 缺省 200", st, 200)
        check("export/json 形状 {knowledge,entities,tags,exported_at}", set(body.keys()) >= {"knowledge", "entities", "tags", "exported_at"}, str(list(body.keys())))

    st, body = call("GET", "/api/export/json", token=tok_a, params={"user_id": victim})
    if mode == "--pre":
        check("GAP export/json 认证自报他人 200 (修前冒读)", st == 200, str(st))
    else:
        expect("export/json cross-user 403", st, 403, str(body)[:60])

    st, _ = call("GET", "/api/export/json", token="garbage-token", params={"user_id": md5_uuid_str("default")})
    if mode == "--pre":
        check("GAP export bad token 200 (修前无鉴权轴)", st == 200, str(st))
    else:
        expect("export bad token 401", st, 401)

    for path in ("/api/export/markdown", "/api/export/markdown-json", "/api/export/csv"):
        params = {"user_id": md5_uuid_str("default")}
        if path.endswith("/csv"):
            params["table"] = "knowledge"
        st, body = call("GET", path, params=params)
        if mode == "--pre":
            check(f"GAP {path} SQLite 聚合 500 (array_agg)", st == 500, str(st))
        else:
            expect(f"{path} 200 (GROUP_CONCAT 收口)", st, 200, str(body)[:60])

    # ---------- feedback 面 ----------
    conv = CONV % stamp
    st, body = call("POST", "/api/feedback/extract", {"conversation": conv, "source": PROBE_SOURCE})
    if mode == "--pre":
        check("GAP feedback/extract 缺省 422 (修前必填自报)", st == 422, str(st))
    else:
        expect("feedback/extract 缺省 200", st, 200, str(body)[:60])
        rows = knowledge_rows()
        check("extract 写键=家族正典 md5('default')", rows and all(r["user_id"] == md5_uuid_str("default") for r in rows), str([r["user_id"] for r in rows][:2]))

    n_before = len(knowledge_rows())
    st, body = call(
        "POST",
        "/api/feedback/extract",
        {"conversation": conv, "source": PROBE_SOURCE},
        token=tok_a,
        params={"user_id": victim},
    )
    if mode == "--pre":
        check("GAP feedback 认证自报他人 200 (修前冒写)", st == 200, str(st))
        rows = knowledge_rows()
        check("GAP 修前写键=裸自报串", rows and all(r["user_id"] == victim for r in rows), str([r["user_id"] for r in rows][:2]))
    else:
        expect("feedback cross-user 403", st, 403, str(body)[:60])
        check("403 零新增知识行", len(knowledge_rows()) == n_before, f"{n_before} -> {len(knowledge_rows())}")

    st, _ = call("POST", "/api/feedback/extract", {"conversation": conv, "source": PROBE_SOURCE}, token="garbage-token", params={"user_id": md5_uuid_str("default")})
    if mode == "--pre":
        check("GAP feedback bad token 200 (修前无鉴权轴)", st == 200, str(st))
    else:
        expect("feedback bad token 401", st, 401)
        check("401 零新增知识行", len(knowledge_rows()) == n_before, f"{n_before} -> {len(knowledge_rows())}")

    n_before = len(knowledge_rows())
    st, body = call("POST", "/api/feedback/extract", {"conversation": conv, "source": PROBE_SOURCE}, token=tok_a)
    if mode == "--pre":
        check("GAP feedback 认证缺省 422 (修前必填自报不可缺省)", st == 422, str(st))
    else:
        check("feedback 认证派生 200", st == 200, str(st))
        new_rows = knowledge_rows()[n_before:]
        check(
            "认证派生写键=家族正典 md5(users.id)",
            new_rows and all(r["user_id"] == md5_uuid_str(ent_ids["a"]) for r in new_rows),
            str([r["user_id"] for r in new_rows][:2]),
        )

    st, body = call("GET", "/api/feedback/entries", token=tok_a, params={"user_id": victim})
    if mode == "--pre":
        check("GAP entries 认证自报他人 200 (修前冒读)", st == 200, str(st))
    else:
        expect("entries cross-user 403", st, 403, str(body)[:60])

    st, body = call("GET", "/api/feedback/stats", token=tok_a)
    if mode == "--pre":
        check("GAP stats 缺省 422 (修前必填自报)", st == 422, str(st))
    else:
        expect("stats 缺省 200", st, 200, str(body)[:60])
        check("stats total=1 (只计派生档案行)", body.get("total") == 1, str(body.get("total")))

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
            new_tag_rows = c.execute(
                "SELECT id FROM tags WHERE user_id IN (?,?,?) AND name IN (?,?,?,?,?)",
                (*probe_keys, *tag_names),
            ).fetchall()
            for (tid,) in new_tag_rows:
                if tid not in pre_tag_ids:
                    c.execute("DELETE FROM tags WHERE id = ?", (tid,))
            c.commit()
            leftover_k = c.execute(
                "SELECT COUNT(*) FROM knowledge WHERE source = ?", (PROBE_SOURCE,)
            ).fetchone()[0]
            leftover_t = c.execute(
                "SELECT COUNT(*) FROM tags WHERE user_id IN (?,?,?) AND name IN (?,?,?,?,?)"
                " AND id NOT IN ({})".format(
                    ",".join("?" * len(pre_tag_ids)) if pre_tag_ids else "''"
                ),
                (*probe_keys, *tag_names, *tuple(pre_tag_ids)),
            ).fetchone()[0]
            remaining += int(leftover_k) + int(leftover_t)
    except Exception as exc:
        print("WARN opensoul.db cleanup:", exc)
        remaining += 1

    try:
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username LIKE 'e2e-cefid-%'")
            c.commit()
            leftover = c.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-cefid-%'"
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
