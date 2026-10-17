#!/usr/bin/env python3
"""H1-②b-⑤ live e2e 探针：clear_session 授权清理面。

用法：
  python3 scripts/e2e_clear_session_grants.py --pre   # 修前探针（期望 404 无出口 + 会话删除后授权残留=缺口实证）
  python3 scripts/e2e_clear_session_grants.py          # 修后全绿

真实栈 urllib → :8090：
  ① 出口面：POST /api/mcp/sessions/{sid}/grants 建授权行 → DELETE .../grants 整批清理
    （幂等 cleared 0、他会话不动、逐条 revoke 面契约不变）
  ② 生命周期接线：POST /api/hippo/sessions 建会话 → 授权 → DELETE /api/hippo/sessions/{sid}
    → 授权行随之清除（修前永不清）+ 404 零副作用。
探针自带清痕（MARK 授权行 0 残留回读 + hippo 探针会话 0 残留）。测试行绝不留生产库。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/mcp/mcp.db"
MARK = "e2e9b5clr"
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


def grant_rows(session_id: str) -> list[dict]:
    _, body = call("GET", f"/api/mcp/sessions/{session_id}/grants")
    return body.get("grants", [])


def cleanup() -> int:
    """清 MARK 探针授权行（有出口用出口、无出口退逐条 revoke），回读 remaining。"""
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            "SELECT session_id, server_id, tool_name FROM mcp_session_grants WHERE session_id LIKE ?",
            (f"%{MARK}%",),
        ).fetchall()
    for sid in {r[0] for r in rows}:
        status, _ = call("DELETE", f"/api/mcp/sessions/{sid}/grants")
        if status != 200:  # 修前无出口：逐条 revoke 兜底
            for _, server_id, tool_name in rows:
                call("DELETE", f"/api/mcp/sessions/{sid}/grants/{server_id}", params={"tool_name": tool_name})
    # hippo 探针会话兜底清（正常路径已删）
    _, sess_body = call("GET", "/api/hippo/sessions", params={"limit": 200})
    for s in sess_body.get("sessions", []):
        if MARK in (s.get("user_id") or "") or MARK in (s.get("session_id") or ""):
            call("DELETE", f"/api/hippo/sessions/{s['session_id']}")
    with sqlite3.connect(DB) as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM mcp_session_grants WHERE session_id LIKE ?", (f"%{MARK}%",)
        ).fetchone()[0]
    print("cleanup remaining =", remaining)
    return remaining


def main() -> int:
    pre = "--pre" in sys.argv

    # ── ① 出口面：整批清理 ─────────────────────────────────────
    sid_a, sid_b = f"{MARK}-a", f"{MARK}-b"
    for sid, servers in ((sid_a, ["mcp-github", "mcp-github", "mcp-memory"]), (sid_b, ["mcp-github"])):
        tools = ["*", "list_issues", "*"] if sid == sid_a else ["*"]
        for server_id, tool_name in zip(servers, tools):
            status, _ = call("POST", f"/api/mcp/sessions/{sid}/grants", body={"server_id": server_id, "tool_name": tool_name})
            check(status == 200, f"grant 落行 {sid}/{server_id}/{tool_name}")

    status, body = call("DELETE", f"/api/mcp/sessions/{sid_a}/grants")
    if pre:
        check(status in (404, 405), "--pre 出口端点缺失（404/405=缺口实证）", f"got {status}")
    else:
        check(status == 200, "整批清理 200")
        check(body == {"session_id": sid_a, "cleared": 3}, "响应恰 2 键 cleared=3 如实", str(body))
        check(grant_rows(sid_a) == [], "本会话授权行全清")
        check(len(grant_rows(sid_b)) == 1, "他会话授权不动")
        status2, body2 = call("DELETE", f"/api/mcp/sessions/{sid_a}/grants")
        check(status2 == 200 and body2.get("cleared") == 0, "幂等二次清理 cleared=0 如实")

    # 逐条 revoke 面契约不变
    call("POST", f"/api/mcp/sessions/{sid_b}/grants", body={"server_id": "mcp-x", "tool_name": "*"})
    status, body = call("DELETE", f"/api/mcp/sessions/{sid_b}/grants/mcp-x")
    check(status == 200 and body.get("revoked") is True, "逐条 revoke 面契约不变")
    status, _ = call("DELETE", f"/api/mcp/sessions/{sid_b}/grants/mcp-x")
    check(status == 404, "二次 revoke 404 契约不变")

    # ── ② 生命周期接线：会话删除=授权随之清 ────────────────────
    status, sess = call("POST", "/api/hippo/sessions", body={"user_id": MARK, "title": "e2e clear_session"})
    check(status == 200 and "session_id" in sess, "hippo 探针会话建立", str(sess)[:80])
    hid = sess.get("session_id", "")
    for server_id, tool_name in (("mcp-github", "*"), ("mcp-memory", "recall")):
        call("POST", f"/api/mcp/sessions/{hid}/grants", body={"server_id": server_id, "tool_name": tool_name})
    call("POST", f"/api/mcp/sessions/{MARK}-keep/grants", body={"server_id": "mcp-github", "tool_name": "*"})
    check(len(grant_rows(hid)) == 2, "会话授权 2 行就位")

    status, body = call("DELETE", f"/api/hippo/sessions/{hid}")
    check(status == 200 and body == {"deleted": True}, "会话删除契约逐键不变", str(body))
    if pre:
        check(len(grant_rows(hid)) == 2, "--pre 授权行永不清（会话已删授权残留=缺口实证）", f"got {len(grant_rows(hid))}")
    else:
        check(grant_rows(hid) == [], "会话删除=授权行随之清除")
        check(len(grant_rows(f"{MARK}-keep")) == 1, "他会话授权不动")

    # 404 零副作用
    call("POST", f"/api/mcp/sessions/{MARK}-ghost/grants", body={"server_id": "mcp-github", "tool_name": "*"})
    status, _ = call("DELETE", f"/api/hippo/sessions/{MARK}-ghost-missing")
    check(status == 404, "缺会话 404 契约不变")
    check(len(grant_rows(f"{MARK}-ghost")) == 1, "404 零副作用（清理不越权执行）")

    remaining = cleanup()
    check(remaining == 0, "清痕 remaining=0")

    ok = sum(1 for v, _ in RESULTS if v)
    print(f"\n== {ok}/{len(RESULTS)} PASS ==")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
