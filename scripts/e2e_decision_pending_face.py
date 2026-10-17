#!/usr/bin/env python3
"""H1-②b-③ live e2e 探针：pending 复盘查询面（get_pending_entries 出口）。

用法：
  python3 scripts/e2e_decision_pending_face.py --pre   # 修前探针（期望 /decisions/pending 404=缺口实证）
  python3 scripts/e2e_decision_pending_face.py          # 修后全绿

真实栈 urllib → :8090：POST /api/hippo/decisions 建 3 条探针决策（不同 agent_id/domain）
→ GET /api/hippo/decisions/pending 盘点（升序复盘顺序/agent_id·domain 过滤/resolved 不列）
→ 按盘点出的 decision_id POST /decisions/{id}/outcome 回填 → 复查离开 pending（回填闭环）。
探针自带清痕（decision_log/decision_audit 按 MARK 行精确删+回读 remaining=0）。
测试行绝不留生产库。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/.hermes/opensoul/hippo/decision_log.db"
MARK = "e2e9e3"
sys.path.insert(0, "/home/climbing/opensoul")

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path, body=None, params=None) -> tuple[int, dict]:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:200]}


def cleanup() -> int:
    """按 MARK 精确清探针行（decision_log + decision_audit），回读 remaining。"""
    removed = 0
    with sqlite3.connect(DB) as conn:
        cur = conn.execute(
            "SELECT decision_id FROM decision_log WHERE decision LIKE ? OR agent_id LIKE ? "
            "OR domain LIKE ? OR context LIKE ?",
            (f"%{MARK}%", f"%{MARK}%", f"%{MARK}%", f"%{MARK}%"),
        )
        ids = [r[0] for r in cur.fetchall()]
        for did in ids:
            removed += conn.execute(
                "DELETE FROM decision_audit WHERE decision_id = ?", (did,)
            ).rowcount
            removed += conn.execute(
                "DELETE FROM decision_log WHERE decision_id = ?", (did,)
            ).rowcount
        conn.commit()
        rem = conn.execute(
            "SELECT COUNT(*) FROM decision_log WHERE decision LIKE ? OR agent_id LIKE ? "
            "OR domain LIKE ? OR context LIKE ?",
            (f"%{MARK}%", f"%{MARK}%", f"%{MARK}%", f"%{MARK}%"),
        ).fetchone()[0]
        rem_a = conn.execute(
            "SELECT COUNT(*) FROM decision_audit WHERE decision_id IN ("
            "SELECT decision_id FROM decision_log WHERE decision LIKE ?)",
            (f"%{MARK}%",),
        ).fetchone()[0]
    print(f"cleanup: removed={removed} remaining_log={rem} remaining_audit={rem_a}")
    return rem + rem_a


def main() -> int:
    pre = "--pre" in sys.argv
    agent_a = f"{MARK}-agent-a"
    agent_b = f"{MARK}-agent-b"
    dom = f"{MARK}-dom"
    try:
        st, health = call("GET", "/api/health")
        check(st == 200 and health.get("status") == "ok", "health 200", str(health)[:80])

        st, body = call("GET", "/api/hippo/decisions/pending")
        check(
            st == 200,
            "GET /decisions/pending 200" + ("（修前应 404）" if pre else ""),
            f"status={st}",
        )
        if st == 200:
            check(
                set(body.keys()) == {"entries", "count", "domain", "agent_id"},
                "响应键集恰 4 键",
                str(sorted(body.keys())),
            )

        # 建 3 条探针决策（2 条 agent_a + 1 条 agent_b）
        ids = []
        for i, (ag, dec) in enumerate(
            [
                (agent_a, f"{MARK} decision first"),
                (agent_a, f"{MARK} decision second"),
                (agent_b, f"{MARK} decision other-agent"),
            ]
        ):
            st, e = call(
                "POST",
                "/api/hippo/decisions",
                {"decision": dec, "domain": dom, "agent_id": ag, "context": MARK},
            )
            check(st == 200 and e.get("status") == "pending", f"store probe {i}", str(e)[:80])
            ids.append(e.get("decision_id", ""))
        time.sleep(0.05)

        st, body = call(
            "GET", "/api/hippo/decisions/pending", params={"agent_id": agent_a}
        )
        probe_rows = [e for e in body.get("entries", []) if e.get("decision_id") in ids]
        check(
            st == 200
            and [e["decision_id"] for e in probe_rows] == [ids[0], ids[1]],
            "agent_id 过滤 + 升序复盘顺序（FIFO）",
            f"got={[e.get('decision_id', '')[:12] for e in probe_rows]}",
        )

        st, body = call(
            "GET", "/api/hippo/decisions/pending", params={"domain": dom}
        )
        dom_rows = [e for e in body.get("entries", []) if e.get("decision_id") in ids]
        check(
            st == 200 and sorted(e["decision_id"] for e in dom_rows) == sorted(ids),
            "domain 过滤盘点 3 条探针行",
            f"count={len(dom_rows)}",
        )

        # 回填闭环：按盘点出的 decision_id 回填 → 离开 pending
        st, r = call(
            "POST",
            f"/api/hippo/decisions/{ids[0]}/outcome",
            {"outcome": f"{MARK} resolved by review", "success": True},
        )
        check(st == 200, "按盘点 id 回填 outcome", f"status={st}")
        st, body = call(
            "GET", "/api/hippo/decisions/pending", params={"agent_id": agent_a}
        )
        after = [e for e in body.get("entries", []) if e.get("decision_id") in ids]
        check(
            [e["decision_id"] for e in after] == [ids[1]],
            "回填后离开 pending（闭环）",
            f"got={[e.get('decision_id', '')[:12] for e in after]}",
        )
        st, body = call(
            "GET", "/api/hippo/decisions", params={"status": "resolved", "domain": dom}
        )
        resolved_ids = [e.get("decision_id") for e in body.get("decisions", [])]
        check(ids[0] in resolved_ids, "resolved 面可见回填条目", f"status={st}")
    finally:
        rem = cleanup()
        check(rem == 0, "清痕 remaining=0", f"remaining={rem}")

    ok = sum(1 for o, _ in RESULTS if o)
    print(f"\n== {ok}/{len(RESULTS)} ==" + (" [PRE]" if pre else ""))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
