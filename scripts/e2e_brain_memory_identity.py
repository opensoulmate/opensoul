#!/usr/bin/env python3
"""F3b-㉛ live e2e 探针：brain 记忆面（/remember·/recall + 写点）身份轴收口。

用法：
  python3 scripts/e2e_brain_memory_identity.py --pre   # 修前探针（缺口坐实）
  python3 scripts/e2e_brain_memory_identity.py --post  # 修后全绿（含清痕）

真实栈 urllib → :8090；真身份=enterprise register→login→token。断言面：
/remember 冒写 404 零副作用、/recall 显式桶越权 404（越权=与缺行同契约禁探测）、
缺省扫描「用户看自己的」（修前=全量泄露）、自动建会话行归属=派生身份（行为证：
own 桶可读/他人 404）、think 写点行归属=派生身份（真 LLM 路径可选，--think 开）、
坏 token 401 电池、自报矛盾 403、匿名自报契约零变化。
清痕：enterprise users 行删除（SQL）+ brain 记忆面=in-memory 进程生命周期面
（重启即清，无持久残留）+ timeline delta=0 含事件行面 TR-② 纪律。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8090"
REPO = os.path.expanduser("~/opensoul")
TIMELINE_DB = os.path.join(REPO, "data", "timeline.db")
MARK = "e2e-bm21"
PRE = "--pre" in sys.argv
POST = "--post" in sys.argv
RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def call(method: str, path: str, body=None, params=None, token=None):
    if params:
        path = path + ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
    data = None
    hdrs = {}
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, (json.loads(raw) if raw else {})
        except Exception:
            return e.code, {"raw": raw[:200]}


def timeline_total():
    try:
        with sqlite3.connect(TIMELINE_DB) as c:
            row = c.execute("SELECT COUNT(*) FROM events").fetchone()
        return row[0]
    except Exception:
        return None


def timeline_max_rowid():
    try:
        with sqlite3.connect(TIMELINE_DB) as c:
            row = c.execute("SELECT COALESCE(MAX(rowid), 0) FROM events").fetchone()
        return row[0]
    except Exception:
        return None


def timeline_trim_after(base_rowid):
    """取证后清痕：删本探针新增事件行（rowid>baseline 精确差集）。"""
    try:
        with sqlite3.connect(TIMELINE_DB) as c:
            c.execute("DELETE FROM events WHERE rowid > ?", (base_rowid,))
            c.commit()
        return True
    except Exception:
        return False


def recall_texts(body):
    return [m.get("content", "") for m in body.get("memories", [])]


def main():
    if not (PRE or POST):
        print("need --pre or --post")
        return 2
    stamp = str(int(time.time()))[-6:]
    ent_db = os.path.join(REPO, "data", "enterprise.db")
    t0 = timeline_total()
    tl_base = timeline_max_rowid()

    st, _ = call("GET", "/api/health")
    check(st == 200, "health 200", str(st))

    # ---------- 真身份（enterprise register→login→token） ----------
    users = {}
    for tag in ("a", "b"):
        uname = f"{MARK}-{tag}-{stamp}"
        st, body = call(
            "POST",
            "/api/enterprise/auth/register",
            {"username": uname, "password": "E2e-Pass-1"},
        )
        check(st in (200, 201), f"register {tag}", str(body)[:60])
        st, body = call(
            "POST",
            "/api/enterprise/auth/login",
            {"username": uname, "password": "E2e-Pass-1"},
        )
        tok = body.get("token") or body.get("access_token")
        check(st == 200 and bool(tok), f"login {tag} token", str(body)[:60])
        users[tag] = {"username": uname, "token": tok}
    tok_a, tok_b = users["a"]["token"], users["b"]["token"]
    ent_ids = {}
    with sqlite3.connect(ent_db) as c:
        for tag in ("a", "b"):
            row = c.execute(
                "SELECT id FROM users WHERE username = ?", (users[tag]["username"],)
            ).fetchone()
            ent_ids[tag] = str(row[0])
    uid_a, uid_b = ent_ids["a"], ent_ids["b"]

    sid_a = f"{MARK}-sa-{stamp}"
    secret = f"A 记忆 e2e-{stamp}"

    # ---------- A 写入自有桶 ----------
    st, body = call(
        "POST",
        "/api/brain/remember",
        params={"session_id": sid_a, "content": secret},
        token=tok_a,
    )
    check(st == 200 and body.get("stored") is True, "A write own bucket", str(body)[:60])

    if PRE:
        # 修前缺口硬证（预期 FAIL 面）：
        st, body = call(
            "POST",
            "/api/brain/remember",
            params={"session_id": sid_a, "content": "B 冒写 e2e"},
            token=tok_b,
        )
        check(st == 200, "[gap] B cross-user write lands 200", str(st))
        st, body = call("GET", "/api/brain/recall")
        check(
            any(secret in t for t in recall_texts(body)),
            "[gap] anonymous scan sees A memory (全量泄露)",
            str(recall_texts(body))[:80],
        )
        st, body = call("GET", "/api/brain/recall", params={"session_id": sid_a}, token=tok_b)
        check(
            st == 200 and any(secret in t for t in recall_texts(body)),
            "[gap] B explicit bucket read 200 (冒读)",
            str(st),
        )
        st, body = call(
            "POST",
            "/api/brain/remember",
            params={"session_id": f"{MARK}-bad-{stamp}", "content": "x"},
            token="not-a-real-token",
        )
        check(st == 200, "[gap] bad token write lands 200", str(st))
    else:
        # 1) 冒写面收口：B 写 A 桶 404 零副作用
        st, body = call(
            "POST",
            "/api/brain/remember",
            params={"session_id": sid_a, "content": "B 冒写 e2e"},
            token=tok_b,
        )
        check(st == 404, "B cross-user write 404", f"{st} {str(body)[:60]}")
        st, body = call("GET", "/api/brain/recall", params={"session_id": sid_a}, token=tok_a)
        texts = recall_texts(body)
        check(
            st == 200 and texts == [secret],
            "A 桶恰 1 行零冒写（行零改写零新增）",
            str(texts)[:80],
        )
        # 2) 显式桶冒读收口：越权=与缺行同契约 404（禁存在性探测）
        st, body = call("GET", "/api/brain/recall", params={"session_id": sid_a}, token=tok_b)
        check(st == 404 and "Session not found" in str(body), "B explicit bucket 404", str(st))
        st, body = call(
            "GET", "/api/brain/recall", params={"session_id": f"{MARK}-nope-{stamp}"}, token=tok_b
        )
        check(st == 404, "缺桶同契约 404（不可差分）", str(st))
        # 3) 缺省扫描「用户看自己的」（修前=全量泄露）
        st, body = call("GET", "/api/brain/recall", token=tok_a)
        a_texts = recall_texts(body)
        check(any(secret in t for t in a_texts), "A scan sees own row", str(a_texts)[:80])
        st, body = call("GET", "/api/brain/recall", token=tok_b)
        check(not any(secret in t for t in recall_texts(body)), "B scan 不见 A 行", "")
        st, body = call("GET", "/api/brain/recall")
        check(not any(secret in t for t in recall_texts(body)), "anonymous scan 不见 A 行", "")
        # 4) 自动建会话行归属=派生身份（行为证：own 桶可读/他人 404 已由 1/2 锁定；
        #    派生键一致性：A 以声明 default（未声明）写入仍派生到 A）
        st, body = call(
            "POST",
            "/api/brain/remember",
            params={"session_id": f"{MARK}-auto-{stamp}", "content": f"auto 归属 {stamp}"},
            token=tok_a,
        )
        check(st == 200, "A auto-session write（未声明 user_id→派生）", str(body)[:60])
        st, body = call(
            "GET", "/api/brain/recall", params={"session_id": f"{MARK}-auto-{stamp}"}, token=tok_a
        )
        check(st == 200, "A read auto bucket 200", str(st))
        st, body = call(
            "GET", "/api/brain/recall", params={"session_id": f"{MARK}-auto-{stamp}"}, token=tok_b
        )
        check(st == 404, "B read auto bucket 404（归属=派生身份）", str(st))
        # 5) 匿名自报契约（本地多档案语义零变化）
        sid_self = f"{MARK}-sself-{stamp}"
        st, body = call(
            "POST",
            "/api/brain/remember",
            params={"session_id": sid_self, "content": "匿名自报", "user_id": "profile-x"},
        )
        check(st == 200, "anon self-report write 200", str(body)[:60])
        st, body = call(
            "GET", "/api/brain/recall", params={"session_id": sid_self, "user_id": "profile-x"}
        )
        check(
            st == 200 and any("匿名自报" in t for t in recall_texts(body)),
            "anon self-report bucket 可见",
            str(st),
        )
        st, body = call("GET", "/api/brain/recall", params={"session_id": sid_self})
        check(st == 404, "default 身份读 profile-x 桶 404（自报桶隔离）", str(st))
        # 6) 自报矛盾 403 + 坏 token 401 电池
        st, body = call(
            "POST",
            "/api/brain/remember",
            params={"session_id": sid_a, "content": "x", "user_id": uid_b},
            token=tok_a,
        )
        check(st == 403 and "user mismatch" in str(body), "self-report mismatch 403", str(body)[:80])
        bad_hits = []
        for method, path, params, body_arg in (
            ("POST", "/api/brain/remember", {"session_id": f"{MARK}-bad2-{stamp}", "content": "x"}, None),
            ("GET", "/api/brain/recall", None, None),
            ("GET", "/api/brain/recall", {"session_id": sid_a}, None),
        ):
            st, _ = call(method, path, body_arg, params=params, token="not-a-real-token")
            bad_hits.append((method, st))
        check(all(s == 401 for _, s in bad_hits), "bad token 401 电池×3 零副作用", str(bad_hits))
        # 7) 写点行归属=派生身份（真 think 路径）：A think 带桶→A 可读/他人 404
        sid_task = f"{MARK}-task-{stamp}"
        st, body = call(
            "POST",
            "/api/brain/think",
            {"action": f"e2e think {stamp}", "session_id": sid_task},
            token=tok_a,
        )
        check(st == 200, "A think 200", str(st)[:60])
        st, body = call("GET", "/api/brain/recall", params={"session_id": sid_task}, token=tok_a)
        check(
            st == 200 and any("[think]" in t for t in recall_texts(body)),
            "think 写点行 A 可读（归属=派生身份）",
            str(recall_texts(body))[:80],
        )
        st, body = call("GET", "/api/brain/recall", params={"session_id": sid_task}, token=tok_b)
        check(st == 404, "think 写点行 B 404", str(st))

    # ---------- enterprise users 清痕 ----------
    try:
        with sqlite3.connect(ent_db) as c:
            c.execute("DELETE FROM users WHERE username LIKE ?", (f"{MARK}-%",))
            c.commit()
        with sqlite3.connect(ent_db) as c:
            left = c.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE ?", (f"{MARK}-%",)
            ).fetchone()[0]
        check(left == 0, "cleanup enterprise users remaining=0", str(left))
    except Exception as exc:
        check(False, "cleanup enterprise users", str(exc))

    # ---------- 清痕：探针新增 timeline 事件行（think emit）精确差集删除 ----------
    trimmed = timeline_trim_after(tl_base)
    check(trimmed, "cleanup timeline probe rows trimmed", str(tl_base))
    t1 = timeline_total()
    check(t1 == t0, "timeline delta=0（事件行面 TR-② 纪律）", f"{t0}->{t1}")

    passed = sum(1 for ok, _ in RESULTS if ok)
    print(f"\n== {passed}/{len(RESULTS)} checks passed ==")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
