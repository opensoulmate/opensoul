#!/usr/bin/env python3
"""F3b-⑲ live e2e 探针：hippo 自动建会话注册键错位收口。

用法：
  python3 scripts/e2e_hippo_session_registration_key.py --pre   # 修前探针（缺口坐实）
  python3 scripts/e2e_hippo_session_registration_key.py          # 修后全绿

真实栈 urllib → :8090；SessionManager 纯内存行经 API 面观测（键错位孤儿行=
同 title 多行、by-ID 不可达）。探针自带清痕：探针记忆 API 删除 + timeline
delta=0 含事件行面 TR-② 纪律（孤儿内存行随服务重启清空=纯内存无历史存量）。
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
TIMELINE_DB = "/home/climbing/opensoul/data/timeline.db"
MARK = "e2ef3b20"
SID = f"auto-{MARK}-{int(time.time())}"  # 运行期唯一，防多次探针运行互相污染

PRE = "--pre" in sys.argv
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str, body=None, params=None):
    if params:
        path = path + ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
    data = None
    hdrs = {}
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def jbody(text: str):
    try:
        return json.loads(text)
    except Exception:
        return {}


def timeline_count() -> int:
    con = sqlite3.connect(TIMELINE_DB)
    try:
        return con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        con.close()


tl0 = timeline_count()

# P1/P2 同 session_id 两条记忆（自动建会话路径）
st1, b1 = call("POST", "/api/hippo/memories", {"content": f"{MARK} 记忆一", "session_id": SID})
st2, b2 = call("POST", "/api/hippo/memories", {"content": f"{MARK} 记忆二", "session_id": SID})
check(st1 == 200 and st2 == 200, "P1 两条记忆写入 200", f"{st1}/{st2}")
mid1 = jbody(b1).get("memory_id", "")
mid2 = jbody(b2).get("memory_id", "")

# P3 by-ID 真可达（修前键错位恒 404）
st3, b3 = call("GET", "/api/hippo/sessions/" + SID, params={"user_id": "default"})
if PRE:
    check(st3 == 404, "P3 GAP：by-ID 不可达（键错位）", f"got {st3}")
else:
    d3 = jbody(b3)
    check(
        st3 == 200 and d3.get("session_id") == SID,
        "P3 by-ID 真可达且身份=注册键",
        f"got {st3} sid={d3.get('session_id')}",
    )

# P4 increment_memory_count 真达（修前恒 0）
if not PRE:
    d4 = jbody(b3)
    check(
        d4.get("memory_count") == 2,
        "P4 计数真达（2 条记忆=2）",
        f"got {d4.get('memory_count')}",
    )

# P5 孤儿行：同 title 行数（修前逐条记忆各建一孤儿=2 行）
st5, b5 = call("GET", "/api/hippo/sessions", params={"user_id": "default", "limit": 200})
rows = [s for s in jbody(b5).get("sessions", []) if s.get("title") == f"Auto: {SID}"]
if PRE:
    check(len(rows) == 2, "P5 GAP：同 title 孤儿行 2（逐条记忆各建一会话）", f"got {len(rows)}")
else:
    check(len(rows) == 1, "P5 零孤儿：同 title 恰 1 行", f"got {len(rows)}")

# P6 touch 真达（记忆访问刷新会话活动；修前 no-op 不可达）
if not PRE:
    time.sleep(1.1)
    st6a, b6a = call("GET", "/api/hippo/sessions/" + SID, params={"user_id": "default"})
    before = jbody(b6a).get("last_active_at", 0)
    call("GET", "/api/hippo/memories/" + mid1)
    st6b, b6b = call("GET", "/api/hippo/sessions/" + SID, params={"user_id": "default"})
    after = jbody(b6b).get("last_active_at", 0)
    check(
        st6a == 200 and st6b == 200 and after > before,
        "P6 touch 真达（记忆访问刷新 last_active_at）",
        f"{before} -> {after}",
    )

# P7 by-ID 写面可达（archive；修前 404）
st7, _ = call("POST", "/api/hippo/sessions/" + SID + "/archive", params={"user_id": "default"})
if PRE:
    check(st7 == 404, "P7 GAP：archive 不可达（键错位）", f"got {st7}")
else:
    check(st7 == 200, "P7 by-ID archive 可达", f"got {st7}")

# 清痕：探针记忆删除 + 会话删除（修前删除同 404=孤儿行 API 不可达删不掉，
# 纯内存行随服务重启清空无历史存量——"删不掉"本身即 GAP 证据）
for mid in (mid1, mid2):
    call("DELETE", "/api/hippo/memories/" + str(mid))
st8, _ = call("DELETE", "/api/hippo/sessions/" + SID, params={"user_id": "default"})
if not PRE:
    check(st8 == 200, "P8 清痕：会话删除", f"got {st8}")
st9, b9 = call("GET", "/api/hippo/sessions", params={"user_id": "default", "limit": 200})
left = [s for s in jbody(b9).get("sessions", []) if (s.get("title") or "") == f"Auto: {SID}"]
if PRE:
    check(
        len(left) == 2 and st8 == 404,
        "P9 GAP：孤儿行删不掉（by-ID 不可达=键错位连清痕都不可达）",
        f"left={len(left)} del={st8}（随重启清）",
    )
else:
    check(left == [], "P9 清痕回读：探针会话不可见", f"left={len(left)}")

tl1 = timeline_count()
check(tl0 == tl1, "P10 timeline delta=0（含事件行面 TR-② 纪律）", f"{tl0} -> {tl1}")

failed = [n for ok, n in RESULTS if not ok]
print(f"\n{'PRE' if PRE else 'POST'} {len(RESULTS) - len(failed)}/{len(RESULTS)}", "FAIL:" if failed else "", failed)
sys.exit(1 if failed else 0)
