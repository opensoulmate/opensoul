#!/usr/bin/env python3
"""DEC-⑦-⑱ skill_learner 面行租户键加轴 live e2e 探针。

真栈（urllib）打 live :8090：nest 建真租户×2 → enterprise register→login→token
真身份 → extract 写点归属（A×1/B×1/匿名×1+自报丢弃）→ /skill 三向互斥+加性键 →
by-ID 归属闸电池（B 冒读写 A 全 404/空面零副作用）→ 劫持 403 行零改写 →
recommend/audit 过滤 → 坏 token 401 零副作用 → /skill/health 系统视图 →
purge 作用域清理 → 清痕 remaining=0。

用法：python3 scripts/e2e_gene_skill_axis.py [--base http://127.0.0.1:8090]
修前后各跑一次同探针（before=旧代码现场、after=重启新代码），对照取证。
"""

import argparse
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8090"
RESULTS = []
MARKER = ""
SKILLS_DB = str(Path.home() / ".hermes" / "opensoul" / "gene" / "skills.db")


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


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail) if detail else ""))


def db_count():
    try:
        with sqlite3.connect(f"file:{SKILLS_DB}?mode=ro", uri=True) as c:
            total = c.execute("SELECT COUNT(*) FROM learned_skills").fetchone()[0]
            marked = c.execute(
                "SELECT COUNT(*) FROM learned_skills WHERE name LIKE ?",
                (MARKER + "%",),
            ).fetchone()[0]
            marked_audit = c.execute(
                "SELECT COUNT(*) FROM skill_audit WHERE skill_id IN "
                "(SELECT skill_id FROM learned_skills WHERE name LIKE ?)",
                (MARKER + "%",),
            ).fetchone()[0]
        return total, marked, marked_audit
    except Exception as exc:  # 只读探针，读不到如实报
        return None, None, str(exc)


def extract(task, token=None, source="test", self_report=None):
    body = {
        "task_description": task,
        "execution_log": json.dumps(
            [{"function": {"name": n, "arguments": {}}} for n in ("read_file", "write_file")]
        ),
        "success": True,
        "source": source,
    }
    if self_report is not None:
        body["tenant_id"] = self_report
    return call("POST", "/api/gene/skill/extract", body, token=token)


def main():
    global BASE, MARKER
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base
    MARKER = "e2e-g18-"

    st, health = call("GET", "/api/health")
    check("health", st == 200, health)
    total0, marked0, _ = db_count()
    check("baseline db read", total0 is not None, (total0, marked0))

    # ── 真租户×2 + enterprise 真身份 ─────────────────────────────────
    tag = MARKER + uuid.uuid4().hex[:8]
    tenants, users = {}, {}
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

    # ── extract 写点归属：A×1 / B×1 / 匿名×1 / 自报丢弃 ──────────────
    st, ra = extract(f"{tag}-a1 A面任务", token=users["a"])
    check("extract a1", st == 200 and ra and ra.get("skill_id"), ra)
    sid_a = (ra or {}).get("skill_id", "")
    check("extract a1 tenant key", (ra or {}).get("tenant_id") == tenants["a"], ra)
    st, rb = extract(f"{tag}-b1 B面任务", token=users["b"])
    check("extract b1", st == 200 and rb and rb.get("skill_id"), rb)
    sid_b = (rb or {}).get("skill_id", "")
    st, rself = extract(f"{tag}-self 自报丢弃任务", token=users["a"], self_report="attacker-tenant")
    check(
        "extract self-report dropped",
        st == 200 and (rself or {}).get("tenant_id") == tenants["a"],
        rself,
    )
    st, rd = extract(f"{tag}-d1 匿名任务")
    check("extract anon default", st == 200 and (rd or {}).get("tenant_id") == "default", rd)

    # ── /skill 三向互斥 + 加性键 + 自报不改作用域 ────────────────────
    st, la = call("GET", "/api/gene/skill", token=users["a"])
    a_ids = sorted(s["skill_id"] for s in (la or {}).get("skills", []))
    check("list A total", st == 200 and (la or {}).get("total") == 2, la and la.get("total"))
    check("list A rows own", a_ids == sorted([sid_a, (rself or {}).get("skill_id", "")]), a_ids)
    check("list A key", all(s.get("tenant_id") == tenants["a"] for s in la["skills"]), "")
    st, lb = call("GET", "/api/gene/skill", token=users["b"])
    b_ids = sorted(s["skill_id"] for s in (lb or {}).get("skills", []))
    check("list B only own", b_ids == [sid_b] and (lb or {}).get("total") == 1, b_ids)
    st, ld = call("GET", "/api/gene/skill")
    d_ids = sorted(s["skill_id"] for s in (ld or {}).get("skills", []))
    d_own = (rd or {}).get("skill_id", "")
    # 匿名 'default' 面=自己的行 + 生产存量（全 'default' 回填），不见 A/B 行
    check(
        "list anon default view",
        d_own in d_ids and (ld or {}).get("total") == total0 + 1,
        {"own_in": d_own in d_ids, "total": (ld or {}).get("total"), "baseline": total0},
    )
    check("three sides disjoint", not set(a_ids) & set(b_ids) and not set(a_ids) & set(d_ids), "")
    st, ls = call("GET", "/api/gene/skill", token=users["a"], params={"tenant_id": "attacker-tenant"})
    check("self-report no scope change", (ls or {}).get("total") == 2, ls and ls.get("total"))

    # ── by-ID 归属闸电池：B 冒读写 A ─────────────────────────────────
    st, r = call("POST", f"/api/gene/skill/{sid_a}/usage", {"success": True}, token=users["b"])
    check("B usage A -> 404", st == 404, r)
    st, r = call("GET", f"/api/gene/skill/{sid_a}/history", token=users["b"])
    check("B history A empty", st == 200 and (r or {}).get("events") == [], r)
    st, r = call("GET", f"/api/gene/skill/{sid_a}/revert-preview", token=users["b"])
    check("B preview A -> 404", st == 404, r)
    st, r = call("POST", f"/api/gene/skill/{sid_a}/revert", token=users["b"])
    check("B revert A -> 404", st == 404, r)
    st, r = call("DELETE", f"/api/gene/skill/{sid_a}", token=users["b"])
    check("B delete A -> 404", st == 404, r)
    st, la2 = call("GET", "/api/gene/skill", token=users["a"])
    a_row = next(
        (s for s in la2["skills"] if s["skill_id"] == sid_a), None
    )
    check("A row untouched after B battery", a_row and a_row["usage_count"] == 1, a_row)
    st, r = call("POST", f"/api/gene/skill/{sid_a}/usage", {"success": True}, token=users["a"])
    check("A usage own 200", st == 200 and (r or {}).get("usage_count") == 2, r)

    # ── 劫持 403 行零改写 ───────────────────────────────────────────
    st, r = extract(f"{tag}-a1 A面任务", token=users["b"])  # 同任务同工具序列→同 skill_id
    check("hijack -> 403", st == 403, r)
    st, la3 = call("GET", "/api/gene/skill", token=users["a"])
    a_row2 = next((s for s in la3["skills"] if s["skill_id"] == sid_a), None)
    check("hijack row untouched", a_row2 and a_row2["usage_count"] == 2, a_row2)
    st, lb3 = call("GET", "/api/gene/skill", token=users["b"])
    check("B sees no A rows", (lb3 or {}).get("total") == 1, lb3 and lb3.get("total"))

    # ── recommend / audit 过滤 ──────────────────────────────────────
    st, r = call(
        "POST", "/api/gene/skill/recommend", {"task_description": "A面任务"}, token=users["a"]
    )
    rec_ids = {s["skill_id"] for s in (r or {}).get("skills", [])}
    check("recommend A scoped", st == 200 and rec_ids <= set(a_ids), sorted(rec_ids))
    st, r = call("GET", "/api/gene/skill/audit", token=users["a"])
    check(
        "audit A scoped",
        st == 200 and all(e.get("tenant_id") == tenants["a"] for e in r["events"]),
        len((r or {}).get("events", [])),
    )
    st, r = call("GET", f"/api/gene/skill/{sid_b}/history", token=users["a"])
    check("history cross empty face", st == 200 and (r or {}).get("events") == [], r)

    # ── 坏 token 401 零副作用 ───────────────────────────────────────
    bad = "garbage-" + tag
    st, r = extract(f"{tag}-bad 坏token任务", token=bad)
    check("bad token extract 401", st == 401, r)
    st, r = call("GET", "/api/gene/skill", token=bad)
    check("bad token list 401", st == 401, r)
    st, r = call("POST", "/api/gene/skill/purge-source", {"source": "test"}, token=bad)
    check("bad token purge 401", st == 401, r)

    # ── /skill 面 system 聚合（gene /health 零传参=系统视图）──────────
    st, r = call("GET", "/api/gene/health")
    check("gene health system view", st == 200 and "skills" in (r or {}), "")

    # ── purge 作用域清理 + 清痕 ─────────────────────────────────────
    st, r = call("POST", "/api/gene/skill/purge-source", {"source": "test"}, token=users["a"])
    check("purge A scope", st == 200 and (r or {}).get("deleted_skills") == 2, r)
    st, r = call("POST", "/api/gene/skill/purge-source", {"source": "test"}, token=users["b"])
    check("purge B scope", st == 200 and (r or {}).get("deleted_skills") == 1, r)
    st, r = call("POST", "/api/gene/skill/purge-source", {"source": "test"})
    check("purge anon scope", st == 200 and (r or {}).get("deleted_skills") == 1, r)

    total1, marked1, marked_audit = db_count()
    check("remaining marked rows 0", marked1 == 0, (total1, marked1))
    check("remaining marked audit 0", marked_audit == 0, marked_audit)
    check("total reconcile", total1 == total0, (total0, total1))

    # 租户清理（nest + enterprise 用户 SQL 级）
    import os

    nest_db = os.path.expanduser("~/opensoul/data/nest.db")
    ent_db = os.path.expanduser("~/opensoul/data/enterprise.db")
    try:
        with sqlite3.connect(nest_db) as c:
            for who in ("a", "b"):
                c.execute("DELETE FROM tenants WHERE tenant_id = ?", (tenants[who],))
            c.commit()
        with sqlite3.connect(ent_db) as c:
            for who in ("a", "b"):
                c.execute("DELETE FROM users WHERE username = ?", (f"{tag}-{who}",))
            c.commit()
        check("cleanup tenants+users", True, "")
    except Exception as exc:
        check("cleanup tenants+users", False, str(exc))

    ok = sum(1 for _, v, _ in RESULTS if v)
    print(f"\n== {ok}/{len(RESULTS)} PASS ==")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
