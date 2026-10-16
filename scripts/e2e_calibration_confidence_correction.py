#!/usr/bin/env python3
"""G1-④c live e2e：校准→confidence 派生纠偏真链（真双写点→失准→纠偏生效）。

探针（urllib 真栈，零测试面依赖）：
- OC agent：高自评（声明 0.9）+连败 ×2 真双写点 → /api/brain/metacognition 校准真回
  miscalibrated → 未声明 think 生效信心被下修（raw 0.4 → 0.0）+ 纠偏量 −0.9 可观测
- UC agent：低自评+连胜 ×2 → 上修（raw 0.9 → 1.0）+ 纠偏量 +0.9
- DC agent：失准账本下声明路径不被纠偏顶替（0.9 / 0.0）
- NC agent：无校准观测契约不变（0.8 / 0.0）
- 清痕：metacognition_log + timeline events 探针行 remaining=0
"""

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
MARK = "e2e4c"
OK = 0
FAIL = 0


def check(name, cond, detail=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def seed_pair(agent, ref, declared, ok):
    s, d = call(
        "POST",
        "/api/brain/think",
        {
            "user_input": f"{MARK}-marker 帮我修复一个 bug",
            "agent_id": agent,
            "session_id": ref,
            "agent_confidence": declared,
        },
    )
    check(f"seed think {agent}/{ref} 200", s == 200, f"status={s} body={d}")
    s, d = call(
        "POST",
        "/api/brain/verify",
        {
            "agent_id": agent,
            "task_id": ref,
            "action": f"{MARK}-marker 执行动作",
            "result": {"success": ok},
        },
    )
    check(f"seed verify {agent}/{ref} 200", s == 200, f"status={s} body={d}")


def undeclared_think(agent):
    s, d = call(
        "POST",
        "/api/brain/think",
        {"user_input": f"{MARK}-marker 帮我修复一个 bug", "agent_id": agent},
    )
    return s, d


def main():
    s, d = call("GET", "/api/health")
    check("health 200", s == 200, f"status={s}")

    # ── OC：高自评+连败 → 下修 ──
    seed_pair(f"{MARK}-oc", f"{MARK}-t1", 0.9, False)
    seed_pair(f"{MARK}-oc", f"{MARK}-t2", 0.9, False)
    s, d = call("GET", f"/api/brain/metacognition?agent_id={MARK}-oc")
    cal = d.get("calibration", {})
    check(
        "OC calibration miscalibrated",
        cal.get("calibration") == "miscalibrated",
        f"cal={cal}",
    )
    check(
        "OC calibration values",
        cal.get("avg_confidence") == 0.9 and cal.get("actual_success_rate") == 0.0,
        f"cal={cal}",
    )
    s, d = undeclared_think(f"{MARK}-oc")
    check("OC undeclared think 200", s == 200, f"status={s} body={d}")
    check(
        "OC correction == -0.9",
        d.get("confidence_correction") == -0.9,
        f"got {d.get('confidence_correction')}",
    )
    check(
        "OC confidence corrected 0.0 (raw 0.4)",
        d.get("agent_confidence") == 0.0,
        f"got {d.get('agent_confidence')}",
    )
    s, d = call("GET", f"/api/brain/metacognition?agent_id={MARK}-oc")
    check(
        "OC decision row carries corrected confidence",
        d.get("recent_decisions") and d["recent_decisions"][0].get("confidence") == 0.0,
        f"recent={d.get('recent_decisions')}",
    )

    # ── UC：低自评+连胜 → 上修 ──
    seed_pair(f"{MARK}-uc", f"{MARK}-u1", 0.1, True)
    seed_pair(f"{MARK}-uc", f"{MARK}-u2", 0.1, True)
    s, d = undeclared_think(f"{MARK}-uc")
    check("UC undeclared think 200", s == 200, f"status={s}")
    check(
        "UC correction == +0.9",
        d.get("confidence_correction") == 0.9,
        f"got {d.get('confidence_correction')}",
    )
    check(
        "UC confidence corrected 1.0 (raw 0.9)",
        d.get("agent_confidence") == 1.0,
        f"got {d.get('agent_confidence')}",
    )

    # ── DC：声明路径不被纠偏顶替 ──
    seed_pair(f"{MARK}-dc", f"{MARK}-d1", 0.9, False)
    seed_pair(f"{MARK}-dc", f"{MARK}-d2", 0.9, False)
    s, d = call(
        "POST",
        "/api/brain/think",
        {
            "user_input": f"{MARK}-marker 帮我修复一个 bug",
            "agent_id": f"{MARK}-dc",
            "agent_confidence": 0.9,
        },
    )
    check(
        "DC declared stays 0.9",
        s == 200 and d.get("agent_confidence") == 0.9,
        f"status={s} got {d.get('agent_confidence')}",
    )
    check(
        "DC correction 0.0 (declared never corrected)",
        d.get("confidence_correction") == 0.0,
        f"got {d.get('confidence_correction')}",
    )

    # ── NC：无校准观测契约不变 ──
    s, d = undeclared_think(f"{MARK}-nc")
    check(
        "NC default 0.8 / correction 0.0",
        s == 200 and d.get("agent_confidence") == 0.8 and d.get("confidence_correction") == 0.0,
        f"status={s} body={d}",
    )

    # ── 清痕 ──
    time.sleep(0.5)
    mc = sqlite3.connect("data/opensoul.db")
    before_mc = mc.execute(
        "SELECT COUNT(*) FROM metacognition_log WHERE agent_id LIKE ?", (f"{MARK}-%",)
    ).fetchone()[0]
    mc.execute("DELETE FROM metacognition_log WHERE agent_id LIKE ?", (f"{MARK}-%",))
    mc.commit()
    after_mc = mc.execute(
        "SELECT COUNT(*) FROM metacognition_log WHERE agent_id LIKE ?", (f"{MARK}-%",)
    ).fetchone()[0]
    mc.close()
    tl = sqlite3.connect("data/timeline.db")
    before_tl = tl.execute(
        "SELECT COUNT(*) FROM events WHERE summary LIKE ? OR detail LIKE ?",
        (f"%{MARK}%", f"%{MARK}%"),
    ).fetchone()[0]
    tl.execute(
        "DELETE FROM events WHERE summary LIKE ? OR detail LIKE ?",
        (f"%{MARK}%", f"%{MARK}%"),
    )
    tl.commit()
    after_tl = tl.execute(
        "SELECT COUNT(*) FROM events WHERE summary LIKE ? OR detail LIKE ?",
        (f"%{MARK}%", f"%{MARK}%"),
    ).fetchone()[0]
    tl.close()
    print(f"  cleanup: metacognition_log {before_mc}->{after_mc}, timeline {before_tl}->{after_tl}")
    check("cleanup remaining=0", after_mc == 0 and after_tl == 0)

    print(f"\n== RESULT: {OK} PASS / {FAIL} FAIL ==")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
