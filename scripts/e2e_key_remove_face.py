#!/usr/bin/env python3
"""H1-②b-④ KeyManager remove_key 单钥移除面 live e2e 探针。

用法：
  python3 scripts/e2e_key_remove_face.py --pre   # 修前（旧服务）：DELETE 面 404 缺口硬证
  python3 scripts/e2e_key_remove_face.py         # 修后：全链行为

安全纪律：只触碰探针自建 provider（e2e-keyprov-*）的槽位；其余 provider 键数
前后对账不变（绝不摘除生产密钥）。探针钥为内存态假钥，收尾全删清痕 remaining=0。
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
PROV = "e2e-keyprov-" + str(int(time.time()))
PASS = 0
FAIL = 0


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw}


def check(name, cond, detail: object = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f" FAIL {name} {detail}")


def main():
    pre = "--pre" in sys.argv

    st, baseline = req("GET", "/api/gland/keys")
    check("GET /keys 200", st == 200, st)
    baseline_counts = {p: len(v) for p, v in baseline.items()}

    st, _ = req("POST", "/api/gland/keys", {"provider": PROV, "api_key": "e2e-key-aaaa"})
    check("POST add key1", st == 200, st)
    st, _ = req("POST", "/api/gland/keys", {"provider": PROV, "api_key": "e2e-key-bbbb"})
    check("POST add key2", st == 200, st)
    st, listing = req("GET", "/api/gland/keys")
    check("seed 2 keys in probe provider", len(listing.get(PROV, [])) == 2, listing.get(PROV))

    if pre:
        # 修前：单钥删除面不存在——DELETE /keys/{p}/{i} 404 且钥删不掉
        st, body = req("DELETE", f"/api/gland/keys/{PROV}/0")
        check("PRE-GAP: DELETE single key 404 (face missing)", st == 404, (st, body))
        check("PRE-GAP: detail is generic route-miss", body.get("detail") == "Not Found", body)
        st, listing = req("GET", "/api/gland/keys")
        check("PRE-GAP: both keys still present (no removal path)", len(listing.get(PROV, [])) == 2)
        st, body = req("DELETE", f"/api/gland/providers/{PROV}")
        check("whole-provider removal is the only delete face (status recorded)", st in (200, 404), (st, body))
        print(f"\n--pre summary: PASS={PASS} FAIL={FAIL} (FAIL=expected gaps on old code)")
        return

    st, body = req("DELETE", f"/api/gland/keys/{PROV}/0")
    check("DELETE single key 200", st == 200, (st, body))
    check("response keys exactly 4", set(body.keys()) == {"status", "provider", "index", "remaining"}, body)
    check("status ok / index 0 / remaining 1",
          body.get("status") == "ok" and body.get("index") == 0 and body.get("remaining") == 1, body)

    st, listing = req("GET", "/api/gland/keys")
    rows = listing.get(PROV, [])
    check("only key2 survives (position order)", len(rows) == 1 and rows[0]["masked_key"] == "e2e-...bbbb", rows)

    for path, label in [
        (f"/api/gland/keys/{PROV}/5", "out-of-range"),
        (f"/api/gland/keys/{PROV}/-1", "negative index"),
        ("/api/gland/keys/e2e-keyprov-unknown/0", "unknown provider"),
    ]:
        st, body = req("DELETE", path)
        check(f"miss {label} -> 404 honest detail", st == 404 and "Key not found" in body.get("detail", ""), (st, body))

    st, listing = req("GET", "/api/gland/keys")
    check("404 zero side effect (still 1 key)", len(listing.get(PROV, [])) == 1)

    st, body = req("DELETE", f"/api/gland/keys/{PROV}/0")
    check("delete last key remaining=0", st == 200 and body.get("remaining") == 0, (st, body))

    st, listing = req("GET", "/api/gland/keys")
    others_after = {p: len(v) for p, v in listing.items() if p != PROV}
    others_before = {p: n for p, n in baseline_counts.items() if p != PROV}
    check("other providers untouched (no production key removed)", others_after == others_before,
          {"before": others_before, "after": others_after})
    check("cleanup: probe provider rows remaining=0", len(listing.get(PROV, [])) == 0, listing.get(PROV))

    print(f"\nsummary: PASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
