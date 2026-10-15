#!/usr/bin/env python3
"""DEC-⑬ link_gateway 外部回调入口鉴权化 live e2e 探针。

用法：--pre  修前（旧代码进程）：回调无证照收=伪造面实证
      --post 修后（新代码进程）：缺/错 401 零副作用、持证 200、契约零变化
      --clean 清痕（timeline 探针行按 marker 删）
"""
import json
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
MARKER = "e2e-cbauth-20261016"


def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL"), name, detail)
    return ok


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "--post"
    results = []

    if phase == "--pre":
        s, wh = call("POST", "/api/link/webhooks", {"name": MARKER + "-wh", "direction": "in"})
        results.append(check("pre register webhook 200", s == 200, f"{s}"))
        results.append(check("pre register response 无 callback_token", "callback_token" not in wh, sorted(wh)))
        wh_id = wh.get("webhook_id", "")
        s, resp = call("POST", f"/api/link/webhooks/{wh_id}/incoming", {"data": MARKER})
        results.append(check("pre incoming 无证照收（伪造面）", s == 200 and resp.get("received") is True, f"{s}"))
        s, sysr = call("POST", "/api/link/systems", {"name": MARKER + "-sys", "type": "custom"})
        results.append(check("pre register system 200", s == 200, f"{s}"))
        results.append(check("pre system response 无 callback_token", "callback_token" not in sysr, sorted(sysr)))
        sys_id = sysr.get("system_id", "")
        s, resp = call("POST", f"/api/link/systems/{sys_id}/heartbeat")
        results.append(check("pre heartbeat 无证照收（伪造面）", s == 200 and resp.get("status") == "online", f"{s}"))
        print("PRE_RESULT", sum(results), "/", len(results))
        return 0 if all(results) else 1

    if phase == "--clean":
        db = "/home/climbing/opensoul/data/timeline.db"
        conn = sqlite3.connect(db)
        cur = conn.execute(
            "SELECT event_id, summary FROM events WHERE summary LIKE ? OR CAST(detail AS TEXT) LIKE ?",
            (f"%{MARKER}%", f"%{MARKER}%"),
        )
        rows = cur.fetchall()
        for eid, _ in rows:
            conn.execute("DELETE FROM events WHERE event_id = ?", (eid,))
        conn.commit()
        left = conn.execute(
            "SELECT COUNT(*) FROM events WHERE summary LIKE ? OR CAST(detail AS TEXT) LIKE ?",
            (f"%{MARKER}%", f"%{MARKER}%"),
        ).fetchone()[0]
        conn.close()
        print("CLEANED", len(rows), "remaining", left)
        return 0 if left == 0 else 1

    # ── --post：修后 ─────────────────────────────────────────────
    s, wh = call("POST", "/api/link/webhooks", {"name": MARKER + "-wh", "direction": "in"})
    results.append(check("post register webhook 200", s == 200, f"{s}"))
    tok = wh.get("callback_token", "")
    results.append(check("post register 签发 callback_token", bool(tok), f"len={len(tok)}"))
    wh_id = wh.get("webhook_id", "")

    s, lw = call("GET", "/api/link/webhooks")
    results.append(check("post list 不漏 token", s == 200 and tok not in json.dumps(lw), f"{s}"))

    s, resp = call("POST", f"/api/link/webhooks/{wh_id}/incoming", {"data": MARKER})
    results.append(check("post incoming 缺证 401", s == 401 and "X-Callback-Token" in resp.get("detail", ""), f"{s} {resp.get('detail','')[:60]}"))
    s, resp = call("POST", f"/api/link/webhooks/{wh_id}/incoming", {"data": MARKER}, {"X-Callback-Token": "wrong"})
    results.append(check("post incoming 错证 401", s == 401 and "invalid" in resp.get("detail", ""), f"{s}"))
    s, resp = call("POST", f"/api/link/webhooks/{wh_id}/incoming", {"data": MARKER}, {"X-Callback-Token": tok})
    results.append(check("post incoming 持证 200", s == 200 and resp.get("received") is True, f"{s}"))
    s, resp = call(
        "POST",
        f"/api/link/webhooks/{wh_id}/incoming",
        {"data": MARKER},
        {"X-Callback-Token": tok, "Authorization": "Bearer garbage-not-a-jwt"},
    )
    results.append(check("post 垃圾 Authorization 不打断（1a 契约）", s == 200, f"{s}"))
    s, resp = call("POST", "/api/link/webhooks/nope/incoming", {"data": MARKER}, {"X-Callback-Token": tok})
    results.append(check("post 缺行 404 契约不变", s == 404, f"{s}"))

    s, sysr = call("POST", "/api/link/systems", {"name": MARKER + "-sys", "type": "custom"})
    results.append(check("post register system 200", s == 200, f"{s}"))
    stok = sysr.get("callback_token", "")
    results.append(check("post system 签发 callback_token", bool(stok), f"len={len(stok)}"))
    sys_id = sysr.get("system_id", "")

    s, resp = call("POST", f"/api/link/systems/{sys_id}/heartbeat")
    results.append(check("post heartbeat 缺证 401", s == 401, f"{s}"))
    s, resp = call("POST", f"/api/link/systems/{sys_id}/heartbeat", None, {"X-Callback-Token": "wrong"})
    results.append(check("post heartbeat 错证 401", s == 401, f"{s}"))
    s, ls = call("GET", "/api/link/systems")
    hb0 = [x for x in ls.get("systems", []) if x.get("system_id") == sys_id]
    results.append(check("post 失败心跳零副作用（count==0）", bool(hb0) and hb0[0].get("heartbeat_count") == 0, f"{hb0}"))
    s, resp = call("POST", f"/api/link/systems/{sys_id}/heartbeat", None, {"X-Callback-Token": stok})
    results.append(check("post heartbeat 持证 200", s == 200 and resp.get("status") == "online", f"{s}"))
    s, ls = call("GET", "/api/link/systems")
    hb1 = [x for x in ls.get("systems", []) if x.get("system_id") == sys_id]
    results.append(check("post 成功心跳 count==1", bool(hb1) and hb1[0].get("heartbeat_count") == 1, f"{hb1}"))
    results.append(check("post system list 不漏 token", stok not in json.dumps(ls), ""))
    s, resp = call("POST", "/api/link/systems/nope/heartbeat", None, {"X-Callback-Token": stok})
    results.append(check("post heartbeat 缺行 404 契约不变", s == 404, f"{s}"))

    s, h = call("GET", "/api/health")
    results.append(check("health 200", s == 200, f"{s}"))

    print("POST_RESULT", sum(results), "/", len(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
