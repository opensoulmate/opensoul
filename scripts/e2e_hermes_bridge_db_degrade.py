#!/usr/bin/env python3
"""e2e：hermes 读面 DatabaseError 同族收口（SESS-⑥ hermes_bridge 3 处不同形 +
sessions_api search 同病第 4 面）真服务探针。

live :8090 的 ~/.hermes/state.db 是真损坏库（SESS-① 取证：not-a-db）——降级条件
真实存在，无需模拟。双向硬证：
- --pre（重启前旧码内存）：四读面 error 形态各不相同（messages 500 裸 str(e)、
  delete 200 success:False 裸 str(e)、bridge search 200 裸 str(e)、sessions
  search 200 裸 str(e)）=「不同形」硬证，无 hermes_unreadable 键
- --post（重启载新码）：同探针 → 四面统一降级形态（error「hermes session store
  unreadable」+ 加性键 hermes_unreadable=True + 消息/搜索空载荷不谎报"真无消息/
  无结果"）；健康面 /api/hermes/health 裸文案字节不变
- 取证零副作用（四面只读/对不存在会话幂等删除，不建行不落库）

用法：python3 scripts/e2e_hermes_bridge_db_degrade.py [--pre|--post]
"""

import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:8090"
OPENVEN = "/home/climbing/opensoul"
SID = f"e2e-hb6-{uuid.uuid4().hex[:8]}"

sys.path.insert(0, OPENVEN)
from src.services.auth import create_access_token  # noqa: E402

MODE = "--pre" if "--pre" in sys.argv else "--post"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}")


def req(method, path, token=None, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read().decode() or "{}"
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"_raw": raw}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except Exception:
            return e.code, {"_raw": raw}
    except Exception as e:
        return 0, {"exc": str(e)}


TOK = create_access_token(user_id=uuid.UUID(str(uuid.uuid4())), role="user")

# P0 健康面裸文案字节不变
s, d = req("GET", "/api/hermes/health", token=TOK)
check("P0 health 200", s == 200, f"status={s}")
check("P0 health 裸文案不变", d == {"status": "ok", "component": "HermesBridge"}, f"body={d}")

# P1 bridge messages（修前 500 裸 str(e)）
s, d = req("GET", f"/api/hermes/sessions/{SID}/messages", token=TOK)
print(f"  [messages] status={s} body={d}")
if MODE == "--pre":
    check("P1 修前 messages 500 直通", s == 500, f"status={s}")
    check("P1 修前 error=裸 sqlite 真因", "not a database" in str(d), f"body={d}")
    check("P1 修前 无 hermes_unreadable 键", "hermes_unreadable" not in d)
else:
    check("P1 降级 200 空载荷", s == 200 and d.get("messages") == [] and d.get("total") == 0, f"body={d}")
    check("P1 真因文案", d.get("error") == "hermes session store unreadable", f"body={d}")
    check("P1 加性键 hermes_unreadable", d.get("hermes_unreadable") is True, f"body={d}")

# P2 bridge delete（修前 200 success:False 裸 str(e)）
s, d = req("DELETE", f"/api/hermes/sessions/{SID}", token=TOK)
print(f"  [delete] status={s} body={d}")
if MODE == "--pre":
    check("P2 修前 success False", s == 200 and d.get("success") is False, f"body={d}")
    check("P2 修前 error=裸 sqlite 真因", "not a database" in str(d.get("error", "")), f"body={d}")
    check("P2 修前 无 hermes_unreadable 键", "hermes_unreadable" not in d)
else:
    check("P2 降级 success False 契约不变", s == 200 and d.get("success") is False, f"body={d}")
    check("P2 真因文案", d.get("error") == "hermes session store unreadable", f"body={d}")
    check("P2 加性键 hermes_unreadable", d.get("hermes_unreadable") is True, f"body={d}")

# P3 bridge search（修前 200 裸 str(e)）
s, d = req("GET", "/api/hermes/sessions/search?q=probe-needle", token=TOK)
print(f"  [bridge search] status={s} body={d}")
if MODE == "--pre":
    check("P3 修前 sessions 空", s == 200 and d.get("sessions") == [], f"body={d}")
    check("P3 修前 error=裸 sqlite 真因", "not a database" in str(d.get("error", "")), f"body={d}")
    check("P3 修前 无 hermes_unreadable 键", "hermes_unreadable" not in d)
else:
    check("P3 降级 200 空载荷", s == 200 and d.get("sessions") == [], f"body={d}")
    check("P3 真因文案", d.get("error") == "hermes session store unreadable", f"body={d}")
    check("P3 加性键 hermes_unreadable", d.get("hermes_unreadable") is True, f"body={d}")

# P4 sessions search 同病第 4 面（修前 200 裸 str(e)）
s, d = req("GET", "/api/sessions/search?q=probe-needle", token=TOK)
print(f"  [sessions search] status={s} body={d}")
if MODE == "--pre":
    check("P4 修前 sessions 空", s == 200 and d.get("sessions") == [], f"body={d}")
    check("P4 修前 error=裸 sqlite 真因", "not a database" in str(d.get("error", "")), f"body={d}")
    check("P4 修前 无 hermes_unreadable 键", "hermes_unreadable" not in d)
else:
    check("P4 降级 200 空载荷", s == 200 and d.get("sessions") == [], f"body={d}")
    check("P4 真因文案", d.get("error") == "hermes session store unreadable", f"body={d}")
    check("P4 加性键 hermes_unreadable", d.get("hermes_unreadable") is True, f"body={d}")

# P5 空 q 契约字节不变（不触库）
s, d = req("GET", "/api/hermes/sessions/search?q=", token=TOK)
check("P5 bridge search 空q 契约不变", s == 200 and d == {"sessions": [], "query": ""}, f"body={d}")
s, d = req("GET", "/api/sessions/search?q=", token=TOK)
check("P5 sessions search 空q 契约不变", s == 200 and d == {"sessions": [], "query": ""}, f"body={d}")

# P6 鉴权门零回归（匿名 401）
s, d = req("GET", f"/api/hermes/sessions/{SID}/messages")
check("P6 匿名 401", s == 401, f"status={s}")

print(f"\nMODE={MODE} PASS={len(PASS)} FAIL={len(FAIL)}")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
print("ALL PASS")
