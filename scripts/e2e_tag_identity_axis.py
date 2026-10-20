#!/usr/bin/env python3
"""e2e probe: tag.py 面 user_id 轴 bind_identity 收口 修前/修后双向硬证。

用法：python3 scripts/e2e_tag_identity_axis.py --pre|--post [--base URL]
  --pre  : 旧代码在跑——断言缺口存在（user_id 必填 UUID 自报且零鉴权：匿名自报
           他人档案 200 冒读/冒写/冒删、坏 token 200、缺省 422）
  --post : 新代码在跑——断言缺口已闭（403 user mismatch / 401 / 缺省=未声明取派生 /
           写键=档案键家族正典 md5(users.id) / 匿名自报保留契约零变化）
两模式共同断言：timeline 事件行 delta=0（TR-② 纪律——tags 面不 push_event）。
清痕：探针自建 tags 行（name 前缀 e2e-f3b15-）+enterprise 用户全数删除后
remaining=0（含事件行面复核）。
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
from typing import Any

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8090")
RESULTS = []
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME_PFX = "e2e-f3b15-"


def call(method, path, body=None, token=None, params=None) -> tuple[int, Any]:
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
        uname = f"e2e-tg15-{tag}-{stamp}"
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
    derived_a = md5_uuid_str(ent_ids["a"])
    derived_b = md5_uuid_str(ent_ids["b"])
    victim = "99999999-9999-4999-8999-999999999999"

    def tag_rows():
        with sqlite3.connect(os_db) as c:
            c.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM tags WHERE name LIKE ?", (NAME_PFX + "%",)
                ).fetchall()
            ]

    def payload(n):
        return {"name": f"{NAME_PFX}{n}-{stamp}", "color": "#22c55e"}

    # ---------- 匿名自报面（契约零变化锚） ----------
    st, body = call("POST", "/api/tags/", payload("anon"), params={"user_id": victim})
    anon_tid = body.get("id")
    if mode == "--pre":
        check("GAP 匿名自报他人 200 冒写（零鉴权）", st == 200, str(st))
    else:
        expect("匿名自报保留 200", st, 200, str(body)[:60])
        rows = [r for r in tag_rows() if r["id"] == str(anon_tid)]
        check("匿名自报键原样（victim 档案）", rows and rows[0]["user_id"] == victim,
              str([r.get("user_id") for r in rows]))

    st, body = call("GET", "/api/tags/", params={"user_id": victim})
    if mode == "--pre":
        check("GAP 匿名自报他人 200 冒读（看到刚写的行）",
              st == 200 and any(t.get("id") == anon_tid for t in body), str(st))
    else:
        check("匿名自报读面照旧可见（契约零变化）",
              st == 200 and any(t.get("id") == anon_tid for t in body), str(st))

    # ---------- 认证派生/占位/确认绑定 ----------
    st, body = call("POST", "/api/tags/", payload("derived"), token=tok_a)
    derived_tid = body.get("id")
    if mode == "--pre":
        check("GAP 认证缺省 422（修前必填不可缺省）", st == 422, str(st))
    else:
        expect("认证缺省 200", st, 200, str(body)[:60])
        rows = [r for r in tag_rows() if r["id"] == str(derived_tid)]
        check("写键=家族正典 md5(users.id)", rows and rows[0]["user_id"] == derived_a,
              str([r.get("user_id") for r in rows]))

    st, body = call("POST", "/api/tags/", payload("ph"), token=tok_a,
                    params={"user_id": "00000000-0000-0000-0000-000000000000"})
    ph_tid = body.get("id")
    if mode == "--pre":
        check("GAP 认证占位自报 200 落占位桶（admin-ui 形）", st == 200, str(st))
    else:
        expect("认证占位=未声明取派生 200", st, 200, str(body)[:60])
        rows = [r for r in tag_rows() if r["id"] == str(ph_tid)]
        check("占位行落派生键", rows and rows[0]["user_id"] == derived_a,
              str([r.get("user_id") for r in rows]))

    # 确认绑定=声明身份原键（identity.user_id=users.id 字符串；派生档案键的 md5
    # 归一发生在 bind_identity 之后的 _resolve_user_id——声明面只认身份原键，
    # md5 形声明=矛盾 403（bind_identity 既有语义 F3b-②/⑥，非本拍新增）。
    st, body = call("POST", "/api/tags/", payload("cfm"), token=tok_a, params={"user_id": ent_ids["a"]})
    if mode == "--pre":
        check("GAP 认证确认绑定 200", st == 200, str(st))
    else:
        expect("确认绑定 200", st, 200, str(body)[:60])
        st2, body2 = call("POST", "/api/tags/", payload("cfm2"), token=tok_a,
                          params={"user_id": derived_a})
        check("md5 形声明=矛盾 403（声明面只认身份原键）", st2 == 403, str(st2))

    # ---------- 矛盾 403 / 坏 token 401 ----------
    st, body = call("POST", "/api/tags/", payload("mis"), token=tok_b, params={"user_id": derived_a})
    if mode == "--pre":
        check("GAP 认证自报他人 200 冒写", st == 200, str(st))
    else:
        expect("矛盾 403", st, 403, str(body)[:60])
        check("403 detail user mismatch", "user mismatch" in str(body.get("detail", "")),
              str(body)[:60])
        check("403 零副作用（mis 行未落库）",
              not any(r["name"] == f"{NAME_PFX}mis-{stamp}" for r in tag_rows()), "")

    st, body = call("POST", "/api/tags/", payload("bad"), token="not-a-token",
                    params={"user_id": victim})
    if mode == "--pre":
        check("GAP 坏 token 200（无鉴权轴）", st == 200, str(st))
    else:
        expect("坏 token 401", st, 401, str(body)[:60])

    # ---------- 缺省契约 ----------
    st, body = call("POST", "/api/tags/", payload("dflt"))
    if mode == "--pre":
        check("GAP 缺省 422（修前必填）", st == 422, str(st))
    else:
        expect("缺省=未声明默认桶 200", st, 200, str(body)[:60])
        rows = [r for r in tag_rows() if r["name"] == f"{NAME_PFX}dflt-{stamp}"]
        check("默认桶键=md5('default')", rows and rows[0]["user_id"] == md5_uuid_str("default"),
              str([r.get("user_id") for r in rows]))

    # ---------- 跨用户电池（B 自报 A=403）+ 读隔离 ----------
    # 电池目标行：两模式都建（--pre 的 derived_tid 不存在（认证缺省 422 GAP），
    # 目标行改匿名自报落 A 档案桶——匿名保留自报契约两模式通用）。
    st, body = call("POST", "/api/tags/", payload("tgt"), params={"user_id": derived_a})
    tgt_tid = body.get("id")
    check("电池目标行就位", st == 200 and bool(tgt_tid), str(st))

    st, body = call("GET", "/api/tags/", token=tok_b)
    if mode == "--pre":
        check("GAP B 认证缺省 422/或全见面", st in (200, 422), str(st))
    else:
        expect("B 派生列表 200", st, 200, str(body)[:60])
        check("读隔离：B 恒不见 A 的行",
              not any(t.get("id") in (derived_tid, ph_tid, tgt_tid) for t in body), str(len(body)))

    batt = [
        ("POST", "/api/tags/", None, payload("bat")),
        ("GET", "/api/tags/", None, None),
        ("PATCH", f"/api/tags/{tgt_tid}", tgt_tid, {"name": f"{NAME_PFX}bat-{stamp}"}),
        ("DELETE", f"/api/tags/{tgt_tid}", tgt_tid, None),
    ]
    for method, path, tid, body_arg in batt:
        st, body = call(method, path, body_arg, token=tok_b, params={"user_id": derived_a})
        if mode == "--pre":
            check(f"GAP B 自报 A {method} 200 冒写/冒删", st == 200, str(st))
        else:
            expect(f"B 自报 A {method} 403", st, 403, str(body)[:60])
    if mode == "--post":
        rows = [r for r in tag_rows() if r["id"] == str(tgt_tid)]
        check("电池零副作用（目标行存活名字未改）",
              rows and rows[0]["name"] == f"{NAME_PFX}tgt-{stamp}", str([r.get("name") for r in rows]))

    if mode == "--post":
        # A 属主正对照
        st, body = call("GET", "/api/tags/", token=tok_a)
        expect("A 属主列表 200", st, 200, str(body)[:60])
        check("A 看得到自己的派生行",
              any(t.get("id") == derived_tid for t in body), str(len(body)))
        st, body = call("PATCH", f"/api/tags/{derived_tid}", {"color": "#ef4444"},
                        token=tok_a, params={"user_id": ent_ids["a"]})
        expect("A 属主 patch 200", st, 200, str(body)[:60])
        st, body = call("DELETE", f"/api/tags/{ph_tid}", token=tok_a)
        expect("A 属主 delete 200", st, 200, str(body)[:60])

    # ---------- 清痕（含事件行面 TR-② 复核） ----------
    remaining = 0
    try:
        with sqlite3.connect(os_db) as c:
            c.execute("DELETE FROM tags WHERE name LIKE ?", (NAME_PFX + "%",))
            c.commit()
            leftover = c.execute(
                "SELECT COUNT(*) FROM tags WHERE name LIKE ?", (NAME_PFX + "%",)
            ).fetchone()[0]
            remaining += int(leftover)
    except Exception as exc:
        print("WARN opensoul.db cleanup:", exc)
        remaining += 1

    try:
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username LIKE 'e2e-tg15-%'")
            c.commit()
            leftover = c.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE 'e2e-tg15-%'"
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
