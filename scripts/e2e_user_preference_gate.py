#!/usr/bin/env python3
"""e2e 探针：H1-②b-⑥ 用户风险偏好接线（should_auto_execute 真进执行门禁决策）。

--pre  = 旧码（服务未载新码）同探针：声明面 404/405 + 偏好落库后 think 照旧不读
         =缺口硬证；修后 = 偏好往返翻转决策真消费。

数据面：生产 user_memory 表（写前记录原值、写后恢复，remaining=0 清痕）。
"""

import json
import os
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request

BASE = "http://localhost:8090"
DB = os.path.expanduser("~/opensoul/data/opensoul.db")
LOW_INPUT = "帮我修复 foo.py 的 bug"
DESTRUCTIVE_INPUT = "永久删除生产数据"

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name} :: {detail}")


def req(method, path, body=None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read()
            return resp.status, (dict(json.loads(raw)) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, dict(json.loads(raw))
        except Exception:
            return e.code, {"raw": raw.decode()[:200]}


def db_exec(sql, args=()):
    conn = sqlite3.connect(DB)
    try:
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def ensure_table():
    db_exec(
        """CREATE TABLE IF NOT EXISTS user_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(tenant_id, user_id, key))"""
    )


def prior(tenant, user, key):
    rows = db_exec(
        "SELECT value FROM user_memory WHERE tenant_id=? AND user_id=? AND key=?",
        (tenant, user, key),
    )
    return rows[0][0] if rows else None


def seed(tenant, user, key, value_json):
    db_exec(
        """INSERT INTO user_memory (tenant_id, user_id, key, value, updated_at)
           VALUES (?, ?, ?, ?, strftime('%s','now'))
           ON CONFLICT(tenant_id, user_id, key) DO UPDATE SET value=excluded.value""",
        (tenant, user, key, value_json),
    )


def think(body):
    status, data = req("POST", "/api/brain/think", body)
    return status, data


def medium_case():
    repo = tempfile.mkdtemp(prefix="h1b6-repo-")
    names = []
    for i in range(4):
        name = f"f{i}.py"
        with open(os.path.join(repo, name), "w") as f:
            f.write("x = 1\n")
        names.append(name)
    return "重构 " + " ".join(names), repo


def main():
    pre = "--pre" in sys.argv
    ensure_table()
    print(f"== H1-②b-⑥ e2e {'--pre (旧码缺口取证)' if pre else '修后'} ==")

    # 写前原值记录（恢复用）
    saved = {}
    keys = ("risk_tolerance", "auto_confirm_low_risk")
    for user in ("default", "e2e-pref-u1"):
        for key in keys:
            saved[(user, key)] = prior("default", user, key)

    try:
        if pre:
            # ① 声明面缺口：旧码无 /preferences 路由
            s, _ = req("GET", "/api/brain/preferences")
            check("pre: GET /preferences 端点缺席(404/405)", s in (404, 405), f"status={s}")
            s, _ = req("PUT", "/api/brain/preferences", {"preferences": {"risk_tolerance": "high"}})
            check("pre: PUT /preferences 端点缺席(404/405)", s in (404, 405), f"status={s}")

            # ② 消费缺口：偏好真落库后 think 照旧不读
            seed("default", "default", "auto_confirm_low_risk", "false")
            s, data = think({"user_input": LOW_INPUT})
            check(
                "pre: 偏好落库后 think 照旧 auto（偏好不被读硬证）",
                s == 200 and data.get("decision", {}).get("execute_mode") == "auto",
                f"status={s} decision={data.get('decision')}",
            )
            check(
                "pre: 响应无 preference_allows_auto 键",
                "preference_allows_auto" not in data,
                f"keys={list(data.keys())}",
            )
        else:
            # ① 声明面：缺省 5 键
            s, data = req("GET", "/api/brain/preferences")
            prefs = data.get("preferences", {})
            check(
                "GET /preferences 200 + 缺省 5 键",
                s == 200
                and set(prefs.keys())
                == {
                    "edit_mode_preference",
                    "risk_tolerance",
                    "auto_confirm_low_risk",
                    "always_backup_before_edit",
                    "notification_level",
                },
                f"status={s} prefs={prefs}",
            )
            check(
                "缺省值（risk_tolerance=medium/auto_confirm_low_risk=True）",
                prefs.get("risk_tolerance") == "medium"
                and prefs.get("auto_confirm_low_risk") is True,
                f"prefs={prefs}",
            )

            # ② 收紧面：auto_confirm_low_risk=False → 低风险也不自动
            s, _ = req("PUT", "/api/brain/preferences", {"preferences": {"auto_confirm_low_risk": False}})
            s2, d2 = think({"user_input": LOW_INPUT})
            dec = d2.get("decision", {})
            check(
                "收紧：auto_confirm_low_risk=False → 低风险 confirm_required",
                s == 200 and s2 == 200 and dec.get("execute_mode") == "confirm_required",
                f"put={s} think={s2} decision={dec}",
            )
            check(
                "收紧问句带『用户偏好收紧』真因",
                "用户偏好收紧" in (dec.get("confirm_prompt") or ""),
                f"prompt={dec.get('confirm_prompt')}",
            )
            check(
                "preference_allows_auto=False 可观测",
                d2.get("preference_allows_auto") is False,
                f"val={d2.get('preference_allows_auto')}",
            )

            # ③ 对照：改回 True → auto（真消费往返翻转）
            req("PUT", "/api/brain/preferences", {"preferences": {"auto_confirm_low_risk": True}})
            s3, d3 = think({"user_input": LOW_INPUT})
            check(
                "对照：auto_confirm_low_risk=True → auto",
                s3 == 200 and d3.get("decision", {}).get("execute_mode") == "auto",
                f"decision={d3.get('decision')}",
            )

            # ④ 放宽面：risk_tolerance=high → medium 风险 auto（修前恒 confirm_required）
            msg, repo = medium_case()
            req("PUT", "/api/brain/preferences", {"preferences": {"risk_tolerance": "high"}})
            s4, d4 = think({"user_input": msg, "repo_root": repo})
            check(
                "放宽：tolerance=high → medium 风险 auto（修前 confirm）",
                s4 == 200
                and d4.get("risk", {}).get("overall_level") == "medium"
                and d4.get("decision", {}).get("execute_mode") == "auto",
                f"risk={d4.get('risk', {}).get('overall_level')} decision={d4.get('decision')}",
            )

            # ⑤ 底线：high 风险不被偏好放宽
            s5, d5 = think({"user_input": DESTRUCTIVE_INPUT})
            check(
                "底线：tolerance=high 仍不放宽 high 风险",
                s5 == 200
                and d5.get("risk", {}).get("overall_level") == "high"
                and d5.get("decision", {}).get("execute_mode") == "confirm_required",
                f"risk={d5.get('risk', {}).get('overall_level')} decision={d5.get('decision')}",
            )

            # ⑥ 校验电池：非法声明如实 422
            s6, d6 = req("PUT", "/api/brain/preferences", {"preferences": {"no_such_key": 1}})
            check("422 未知键", s6 == 422 and "unknown" in str(d6.get("detail")), f"{s6} {d6}")
            s7, d7 = req("PUT", "/api/brain/preferences", {"preferences": {"risk_tolerance": "extreme"}})
            check("422 越界值", s7 == 422, f"{s7} {d7}")

            # ⑦ 域隔离：e2e-pref-u1 不串 default
            req(
                "PUT",
                "/api/brain/preferences",
                {"preferences": {"risk_tolerance": "low"}, "user_id": "e2e-pref-u1"},
            )
            s8, d8 = req("GET", "/api/brain/preferences", None)
            s9, d9 = req("GET", "/api/brain/preferences?user_id=e2e-pref-u1")
            check(
                "域隔离：default 仍 high、e2e-pref-u1 low",
                d8.get("preferences", {}).get("risk_tolerance") == "high"
                and d9.get("preferences", {}).get("risk_tolerance") == "low",
                f"default={d8.get('preferences', {}).get('risk_tolerance')} u1={d9.get('preferences', {}).get('risk_tolerance')}",
            )
    finally:
        # 清痕：恢复写前原值（无原值=删行）
        for (user, key), val in saved.items():
            if val is None:
                db_exec(
                    "DELETE FROM user_memory WHERE tenant_id='default' AND user_id=? AND key=?",
                    (user, key),
                )
            else:
                seed("default", user, key, val)
        remaining = db_exec(
            "SELECT COUNT(*) FROM user_memory WHERE tenant_id='default' "
            "AND user_id IN ('default','e2e-pref-u1') AND key IN ('risk_tolerance','auto_confirm_low_risk')"
        )[0][0]
        baseline = sum(1 for v in saved.values() if v is not None)
        print(f"cleanup: remaining={remaining} (baseline={baseline})")
        check("清痕 remaining 回基线", remaining == baseline, f"remaining={remaining}")

    print(f"== result: {PASS} ok / {FAIL} fail ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
