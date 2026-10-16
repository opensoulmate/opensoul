#!/usr/bin/env python3
"""DEC-⑲-② live e2e 探针：link.py callback_token 轮换·撤销端点。

用法：
  python3 scripts/e2e_link_token_lifecycle.py --pre   # 修前探针（期望 404 缺口）
  python3 scripts/e2e_link_token_lifecycle.py          # 修后全绿

真实栈 httpx/urllib → :8090；探针自带清痕（delete connector + timeline 行回读）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
TL_DB = "/home/climbing/opensoul/data/timeline.db"

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str, body=None, headers=None, raw=False):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if raw:
            data = body
        else:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def tl_rows(marker: str):
    con = sqlite3.connect(TL_DB)
    con.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in con.execute(
            "SELECT event_id, event_type, summary, detail FROM events "
            "WHERE summary LIKE ? OR detail LIKE ?",
            (f"%{marker}%", f"%{marker}%"),
        )
    ]
    con.close()
    return rows


def tl_delete(event_ids):
    con = sqlite3.connect(TL_DB)
    for eid in event_ids:
        con.execute("DELETE FROM events WHERE event_id = ?", (eid,))
    con.commit()
    con.close()


def main():
    pre = "--pre" in sys.argv
    marker = "e2e-tok-" + uuid.uuid4().hex[:6]

    st, body = call("GET", "/api/link/health")
    check(st == 200, "P0 health 200", f"st={st}")

    # 注册（register 签发 callback_token）
    st, body = call(
        "POST",
        "/api/link/connectors",
        {"name": marker, "type": "webhook_in", "secret": ""},
    )
    if st != 200:
        check(False, "P1 register", f"st={st} {body[:120]}")
        return finish(pre)
    reg = json.loads(body)
    cid, tok = reg["connector_id"], reg["callback_token"]
    check(len(tok) == 32, "P1 register 签发 32hex", tok[:8] + "…")

    if pre:
        st, _ = call("POST", f"/api/link/connectors/{cid}/callback-token/rotate")
        check(st == 404, "P2 rotate 修前 404 缺口", f"st={st}")
        st, _ = call("DELETE", f"/api/link/connectors/{cid}/callback-token")
        check(st == 404, "P3 revoke 修前 404 缺口", f"st={st}")
        return finish(pre)

    # P2 轮换：新证签发+旧证即刻作废
    st, body = call("POST", f"/api/link/connectors/{cid}/callback-token/rotate")
    check(st == 200, "P2 rotate 200", f"st={st}")
    rot = json.loads(body)
    new = rot["callback_token"]
    check(len(new) == 32 and new != tok, "P2 新证 32hex≠旧证")

    st, _ = call(
        "POST",
        f"/api/link/webhook/{cid}",
        {"probe": "old"},
        headers={"X-Callback-Token": tok},
    )
    check(st == 401, "P2 旧证即刻 401", f"st={st}")
    st, body = call("GET", f"/api/link/connectors/{cid}")
    check(json.loads(body).get("trigger_count") == 0, "P2 旧证 401 零副作用")

    st, _ = call(
        "POST",
        f"/api/link/webhook/{cid}",
        {"probe": "new"},
        headers={"X-Callback-Token": new},
    )
    check(st == 200, "P2 新证放行 200", f"st={st}")

    # P3 明文不回吐
    st, body = call("GET", f"/api/link/connectors/{cid}")
    got = json.loads(body)
    check(
        got.get("has_callback_token") is True and "callback_token" not in got and new not in body,
        "P3 get 不回吐明文",
    )
    st, body = call("GET", "/api/link/connectors")
    check(new not in body and tok not in body, "P3 list 不回吐明文")

    # P4 撤销 fail-closed
    st, body = call("DELETE", f"/api/link/connectors/{cid}/callback-token")
    rev = json.loads(body) if st == 200 else {}
    check(
        st == 200 and rev.get("revoked") is True and rev.get("has_callback_token") is False,
        "P4 revoke 200",
        f"st={st}",
    )
    st, _ = call(
        "POST",
        f"/api/link/webhook/{cid}",
        {"probe": "revoked"},
        headers={"X-Callback-Token": new},
    )
    check(st == 401, "P4 撤销后 401 fail-closed", f"st={st}")

    # P5 撤销后重新签发
    st, body = call("POST", f"/api/link/connectors/{cid}/callback-token/rotate")
    again = json.loads(body)["callback_token"] if st == 200 else ""
    check(st == 200 and len(again) == 32, "P5 撤销后再签发 200", f"st={st}")
    st, _ = call(
        "POST",
        f"/api/link/webhook/{cid}",
        {"probe": "re"},
        headers={"X-Callback-Token": again},
    )
    check(st == 200, "P5 再签发证放行 200", f"st={st}")

    # P6 HMAC 独立轴不动（撤销 token 后 secret 签名仍收）
    st, body = call(
        "POST",
        "/api/link/connectors",
        {"name": marker + "-hmac", "type": "webhook_in", "secret": "e2e-s3cret"},
    )
    cid2 = json.loads(body)["connector_id"]
    call("DELETE", f"/api/link/connectors/{cid2}/callback-token")
    raw = json.dumps({"probe": "hmac"}).encode()
    sig = "sha256=" + hmac.new(b"e2e-s3cret", raw, hashlib.sha256).hexdigest()
    st, _ = call(
        "POST",
        f"/api/link/webhook/{cid2}",
        raw,
        headers={"Content-Type": "application/json", "X-Signature": sig},
        raw=True,
    )
    check(st == 200, "P6 撤销后 HMAC 等效证明仍收 200", f"st={st}")

    # P7 契约面：缺行 404 / 坏 token 401
    st, _ = call("POST", "/api/link/connectors/conn-none/callback-token/rotate")
    check(st == 404, "P7 rotate 缺行 404", f"st={st}")
    st, _ = call("DELETE", "/api/link/connectors/conn-none/callback-token")
    check(st == 404, "P7 revoke 缺行 404", f"st={st}")
    st, _ = call(
        "POST",
        f"/api/link/connectors/{cid}/callback-token/rotate",
        headers={"Authorization": "Bearer garbage"},
    )
    check(st == 401, "P7 坏 token 401 先于写", f"st={st}")

    # P8 事件留痕（timeline 真源）
    rows = tl_rows(cid)
    types = {r["event_type"] for r in rows}
    check(
        "connector_token_rotated" in types and "connector_token_revoked" in types,
        "P8 轮换/撤销事件落 timeline",
        str(sorted(types)),
    )

    return finish(pre, [(cid, marker), (cid2, marker + "-hmac")])


def finish(pre, connectors=()):
    # 清痕：删 connector + timeline 探针行
    remain = 0
    for cid, name in connectors:
        st, _ = call("DELETE", f"/api/link/connectors/{cid}")
        if st != 200:
            remain += 1
        rows = tl_rows(cid) + tl_rows(name)
        tl_delete([r["event_id"] for r in rows])
        remain += len(tl_rows(cid)) + len(tl_rows(name))
    passed = sum(1 for ok, _ in RESULTS if ok)
    print(f"\n{'--pre ' if pre else ''}{passed}/{len(RESULTS)} PASS, remaining={remain}")
    return 0 if passed == len(RESULTS) and remain == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
