#!/usr/bin/env python3
"""H1-②b-⑪ 自驱循环驱动 live e2e 探针（修前/修后同探针双向硬证）。

--pre  旧服务：POST /api/will/goals/{id}/loop 404（驱动轴无出口硬证）+ loop_count 恒 0
--post 新服务：真 LLM 步→事件留痕→record_loop 计次→max_loops 停机判定生产可达
       + goal_report chatter 不可过事件验证 + 跨租户 403 + proactive goal_loop 在册
清痕：探针 goal 全部 DELETE + GET 404 回读 remaining=0。
"""

import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=120) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"err": f"{type(e).__name__}: {e}"}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--post"
    tag = uuid.uuid4().hex[:6]
    sid = f"e2e-gl-{tag}"

    st, health = call("GET", "/api/brain/health")
    check("health 200", st == 200, f"st={st}")

    st, body = call("POST", "/api/will/goals", {"session_id": sid, "description": f"e2e 自驱循环探针 {tag}", "max_loops": 2})
    check("create goal 200", st == 200 and "goal_id" in body, f"st={st} {body}")
    gid = body.get("goal_id", "")

    st, body = call("POST", f"/api/will/goals/{gid}/loop")
    if mode == "--pre":
        check("--pre loop 端点缺席（404/405）", st in (404, 405), f"st={st}")
    else:
        check("loop #1 200 performed", st == 200 and body.get("performed") is True, f"st={st} {body}")
        check("loop #1 loop_count==1", body.get("loop_count") == 1, f"{body}")
        check("loop #1 事件 outcome=none|failed 如实", body.get("event_outcome") in ("none", "failed"), f"{body}")
        check("loop #1 step 非空真产出", bool(body.get("step")), f"{body}")
        st2, body2 = call("POST", f"/api/will/goals/{gid}/loop")
        check("loop #2 200", st2 == 200, f"st={st2} {body2}")
        st3, body3 = call("GET", f"/api/will/goals/{gid}")
        check(
            "max_loops 停机判定生产可达（state=failed）",
            st3 == 200 and body3.get("state") == "failed",
            f"{body3}",
        )
        check("stop reason=Max loops", any("Max loops" in (h.get("reason") or "") for h in body3.get("state_history", [])), f"{body3.get('state_history')}")
        check("loop_count==2 + 两次工作事件留痕", body3.get("loop_count") == 2 and len(body3.get("events", [])) == 2, f"lc={body3.get('loop_count')} ev={len(body3.get('events', []))}")

        # goal_report chatter 不可过事件验证（全 NONE→自报 complete=PAUSED）
        sid2 = f"e2e-gl2-{tag}"
        st, b2 = call("POST", "/api/will/goals", {"session_id": sid2, "description": "e2e chatter", "max_loops": 50})
        gid2 = b2.get("goal_id", "")
        call("POST", f"/api/will/goals/{gid2}/loop")
        st, b2 = call("POST", f"/api/will/goals/{gid2}/report", {"status": "complete", "reason": "我说完了"})
        check("chatter 后自报 complete→paused（no success evidence）", st == 200 and b2.get("state") == "paused", f"{b2}")
        st, b2 = call("POST", f"/api/will/goals/{gid2}/events", {"tool_name": "bash", "result": {"exit_code": 0}})
        st, b2 = call("POST", f"/api/will/goals/{gid2}/report", {"status": "complete", "reason": "done"})
        check("真工具证据后 complete→completed", st == 200 and b2.get("state") == "completed", f"{b2}")

        # 非 ACTIVE 如实 skipped
        st, b2 = call("POST", f"/api/will/goals/{gid2}/loop")
        check("终态 goal skipped 不触达执行器", st == 200 and b2.get("performed") is False and str(b2.get("skipped", "")).startswith("not_active"), f"{b2}")

        # 404 契约
        st, _ = call("POST", "/api/will/goals/no-such-goal/loop")
        check("缺 goal 404", st == 404, f"st={st}")

        # proactive goal_loop 在册
        st, ps = call("GET", "/api/proactive/status")
        names = {w.get("name"): w for w in ps.get("watchers", [])}
        check("proactive goal_loop 在册 interval=300", "goal_loop" in names and names["goal_loop"].get("interval_seconds") == 300, f"{sorted(names)}")

    # 清痕（--pre/--post 都清）
    st, _ = call("DELETE", f"/api/will/goals/{gid}")
    st2, b = call("GET", f"/api/will/goals/{gid}")
    remaining = 0
    if st2 != 404:
        remaining += 1
    if mode == "--post":
        st, _ = call("DELETE", f"/api/will/goals/{gid2}")
        st2, b = call("GET", f"/api/will/goals/{gid2}")
        if st2 != 404:
            remaining += 1
    check("清痕 remaining=0", remaining == 0, f"remaining={remaining}")

    print(f"\n== {mode} result: {PASS} PASS / {FAIL} FAIL ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
