#!/usr/bin/env python3
"""DEC-⑲ link.py receive_webhook token 化 live e2e 探针（OpenLink connector 入口）。

用法：--pre  修前（旧代码进程）：connector_id 即凭证、无证照收=伪造面实证
      --post 修后（新代码进程）：缺/错证 401 零副作用、持证 200、HMAC 等效
             证明、Authorization 通道锁死、404/503 契约序、token 不回吐
      --clean 清痕（connector API 删 + timeline 探针行按 cid/marker 删）

状态文件 data/e2e_link_wh_cbauth_cids.json 记录探针建过的 cid（--clean 消费）。
"""
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
MARKER = "e2e-linkwh-cbauth-20261016"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "data", "e2e_link_wh_cbauth_cids.json")
TL_DB = os.path.join(ROOT, "data", "timeline.db")


def call(method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        rawb = e.read().decode() or "{}"
        try:
            return e.code, json.loads(rawb)
        except Exception:
            return e.code, {"raw": rawb}


def check(name, ok, detail=None):
    print(("PASS" if ok else "FAIL"), name, "" if detail is None else str(detail))
    return ok


def _save_cids(cids):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    old = []
    if os.path.exists(STATE):
        old = json.load(open(STATE))
    json.dump(sorted(set(old) | set(cids)), open(STATE, "w"))


def _sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "--post"
    results = []

    if phase == "--clean":
        cids = json.load(open(STATE)) if os.path.exists(STATE) else []
        for cid in cids:
            call("DELETE", f"/api/link/connectors/{cid}")
        conn = sqlite3.connect(TL_DB)
        killed = 0
        for cid in cids:
            cur = conn.execute(
                "SELECT event_id FROM events WHERE summary LIKE ? OR CAST(detail AS TEXT) LIKE ?",
                (f"%{cid}%", f"%{cid}%"),
            )
            for (eid,) in cur.fetchall():
                conn.execute("DELETE FROM events WHERE event_id = ?", (eid,))
                killed += 1
        cur = conn.execute(
            "SELECT event_id FROM events WHERE summary LIKE ? OR CAST(detail AS TEXT) LIKE ?",
            (f"%{MARKER}%", f"%{MARKER}%"),
        )
        for (eid,) in cur.fetchall():
            conn.execute("DELETE FROM events WHERE event_id = ?", (eid,))
            killed += 1
        conn.commit()
        left = 0
        for cid in cids:
            left += conn.execute(
                "SELECT COUNT(*) FROM events WHERE summary LIKE ? OR CAST(detail AS TEXT) LIKE ?",
                (f"%{cid}%", f"%{cid}%"),
            ).fetchone()[0]
        left += conn.execute(
            "SELECT COUNT(*) FROM events WHERE summary LIKE ? OR CAST(detail AS TEXT) LIKE ?",
            (f"%{MARKER}%", f"%{MARKER}%"),
        ).fetchone()[0]
        conn.close()
        os.path.exists(STATE) and os.remove(STATE)
        s, lc = call("GET", "/api/link/connectors")
        conns: list = list(lc.get("connectors", [])) if isinstance(lc, dict) else []
        gone = s == 200 and all(str(c.get("connector_id")) not in cids for c in conns)
        print("CLEANED", killed, "rows; remaining", left, "connectors_gone", gone)
        return 0 if left == 0 and gone else 1

    # ── 注册（两阶段共用）─────────────────────────────────────────────
    s, reg = call("POST", "/api/link/connectors", {"name": MARKER + "-c1", "type": "webhook_in"})
    results.append(check(f"{phase} register connector 200", s == 200, f"{s}"))
    cid = reg.get("connector_id", "")
    tok = reg.get("callback_token", "")
    _save_cids([cid])

    if phase == "--pre":
        results.append(check("pre register 响应无 callback_token（修前）", not tok, sorted(reg)))
        s, resp = call("POST", f"/api/link/webhook/{cid}", {"data": MARKER})
        results.append(check("pre 匿名无证照收（伪造面）", s == 200 and resp.get("received") is True, f"{s}"))
        s, resp = call("POST", f"/api/link/webhook/{cid}", {"data": MARKER}, {"X-Callback-Token": "wrong"})
        results.append(check("pre 任意 token 照收（伪造面）", s == 200, f"{s}"))
        s, resp = call(
            "POST",
            f"/api/link/webhook/{cid}",
            {"data": MARKER},
            {"Authorization": "Bearer garbage-not-a-jwt"},
        )
        results.append(check("pre 垃圾 Authorization 不打断（1a 契约）", s == 200, f"{s}"))
        s, got = call("GET", f"/api/link/connectors/{cid}")
        results.append(check("pre get 无 callback_token 键", "callback_token" not in got, sorted(got)))
        print("PRE_RESULT", sum(results), "/", len(results))
        return 0 if all(results) else 1

    # ── --post：修后 ─────────────────────────────────────────────
    results.append(check("post register 签发 callback_token", bool(tok) and len(tok) == 32, f"len={len(tok)}"))
    s, got = call("GET", f"/api/link/connectors/{cid}")
    results.append(
        check(
            "post get 不漏 token（has_callback_token 键）",
            s == 200 and "callback_token" not in got and tok not in json.dumps(got) and got.get("has_callback_token") is True,
            sorted(got),
        )
    )
    s, lc = call("GET", "/api/link/connectors")
    results.append(check("post list 不漏 token", s == 200 and tok not in json.dumps(lc), f"{s}"))

    s, resp = call("POST", f"/api/link/webhook/{cid}", {"data": MARKER})
    results.append(
        check("post 缺证 401", s == 401 and "X-Callback-Token" in resp.get("detail", ""), f"{s} {resp.get('detail','')[:60]}")
    )
    s, resp = call("POST", f"/api/link/webhook/{cid}", {"data": MARKER}, {"X-Callback-Token": "wrong"})
    results.append(check("post 错证 401", s == 401 and "invalid" in resp.get("detail", ""), f"{s}"))
    s, got = call("GET", f"/api/link/connectors/{cid}")
    results.append(
        check("post 401 零副作用（trigger_count==0）", s == 200 and got.get("trigger_count") == 0, str(got.get("trigger_count")))
    )

    s, resp = call("POST", f"/api/link/webhook/{cid}", {"data": MARKER}, {"X-Callback-Token": tok})
    results.append(check("post 持证 200", s == 200 and resp.get("received") is True, f"{s}"))
    s, resp = call(
        "POST",
        f"/api/link/webhook/{cid}",
        {"data": MARKER},
        {"X-Callback-Token": tok, "Authorization": "Bearer garbage-not-a-jwt"},
    )
    results.append(check("post 垃圾 Authorization 不打断（1a 契约）", s == 200, f"{s}"))

    # HMAC 等效证明（发方持 secret 即持证，DEC-⑬ 同款 OR 语义）
    s, reg2 = call(
        "POST",
        "/api/link/connectors",
        {"name": MARKER + "-c2", "type": "webhook_in", "secret": "e2e-secret"},
    )
    cid2 = reg2.get("connector_id", "")
    tok2 = reg2.get("callback_token", "")
    _save_cids([cid2])
    body = json.dumps({"data": MARKER}).encode()
    s, resp = call(
        "POST",
        f"/api/link/webhook/{cid2}",
        raw=body,
        headers={"X-Signature": _sign("e2e-secret", body)},
    )
    results.append(check("post HMAC 等效证明 200（无 token）", s == 200 and resp.get("received") is True, f"{s}"))
    s, resp = call(
        "POST",
        f"/api/link/webhook/{cid2}",
        raw=body,
        headers={"X-Signature": "sha256=bad", "X-Callback-Token": tok2},
    )
    results.append(check("post OR 语义：坏签名+真 token 200", s == 200, f"{s}"))

    s, resp = call("POST", "/api/link/webhook/conn-nope0000", {"data": MARKER}, {"X-Callback-Token": tok})
    results.append(check("post 缺行 404 契约先于凭据", s == 404, f"{s}"))
    call("PATCH", f"/api/link/connectors/{cid}", {"status": "paused"})
    s, resp = call("POST", f"/api/link/webhook/{cid}", {"data": MARKER}, {"X-Callback-Token": tok})
    results.append(check("post 暂停 503 契约先于凭据", s == 503, f"{s}"))

    # timeline 回读：webhook_received 行=持证×2（cid）+持证×2（cid2：HMAC+OR 语义），
    # 缺/错证 401 零行
    conn = sqlite3.connect(TL_DB)
    n1 = conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type='webhook_received' AND CAST(detail AS TEXT) LIKE ?",
        (f"%{cid}%",),
    ).fetchone()[0]
    n2 = conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type='webhook_received' AND CAST(detail AS TEXT) LIKE ?",
        (f"%{cid2}%",),
    ).fetchone()[0]
    conn.close()
    results.append(check("timeline 回读：持证 2 行+缺/错证 0 行", n1 == 2, f"n1={n1}"))
    results.append(check("timeline 回读：cid2 持证 2 行（HMAC+OR 语义各 1）", n2 == 2, f"n2={n2}"))

    s, h = call("GET", "/api/health")
    results.append(check("health 200", s == 200, f"{s}"))

    print("POST_RESULT", sum(results), "/", len(results))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
