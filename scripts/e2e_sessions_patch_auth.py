#!/usr/bin/env python3
"""PATCH /api/sessions/{id} 身份轴收口 e2e 探针（SESS-① 留档候选）。

用法：
  --pre   服务仍跑旧码：匿名 PATCH 应 200 真改（缺口 live 硬证）
  --post  服务已载新码：匿名 PATCH 应 401 零写入 + 真 token PATCH 应 200 真改

探针行直接落 data/opensoul.db agent_sessions（--probe-id 前缀），取证后 --cleanup 清痕。
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
DB = os.path.join(os.path.expanduser("~/opensoul"), "data", "opensoul.db")
PROBE_ID = "e2e-sess-patch-auth-probe"


def _req(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def _seed():
    with sqlite3.connect(DB) as c:
        c.execute(
            "INSERT OR REPLACE INTO agent_sessions (id, agent_id, title, created_at, "
            "last_activity_at, message_count) VALUES (?, 'e2e-probe', 'probe-old-title', "
            "strftime('%s','now'), strftime('%s','now'), 0)",
            (PROBE_ID,),
        )
        c.commit()


def _title():
    with sqlite3.connect(DB) as c:
        row = c.execute("SELECT title FROM agent_sessions WHERE id = ?", (PROBE_ID,)).fetchone()
    return row[0] if row else None


def _cleanup():
    with sqlite3.connect(DB) as c:
        c.execute("DELETE FROM agent_sessions WHERE id = ?", (PROBE_ID,))
        try:
            c.execute("DELETE FROM session_tags WHERE session_id = ?", (PROBE_ID,))
        except sqlite3.Error:
            pass
        c.commit()
    left = _title()
    print("cleanup remaining:", 0 if left is None else f"LEFTOVER title={left}")


def _token():
    sys.path.insert(0, os.path.expanduser("~/opensoul"))
    from src.services.auth import create_access_token
    import uuid

    return {"Authorization": f"Bearer {create_access_token(user_id=uuid.uuid4(), role='user')}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", action="store_true")
    ap.add_argument("--post", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()

    if args.cleanup:
        _cleanup()
        return 0

    _seed()
    checks = []

    if args.pre:
        st, body = _req("PATCH", f"/api/sessions/{PROBE_ID}", {"title": "probe-hacked-by-anon"})
        renamed = _title()
        checks.append(("匿名 PATCH 修前 200（缺口 live 硬证）", st == 200))
        checks.append(("修前匿名真改标题", renamed == "probe-hacked-by-anon"))
        print("pre:", st, body, "title now:", renamed)
    elif args.post:
        # 1) 匿名拒绝 + 零写入
        st, body = _req("PATCH", f"/api/sessions/{PROBE_ID}", {"title": "probe-hacked-by-anon"})
        checks.append(("匿名 PATCH 401", st == 401))
        checks.append(("匿名零写入（标题未变）", _title() == "probe-old-title"))
        # 2) 坏 token 拒绝
        st, _ = _req(
            "PATCH",
            f"/api/sessions/{PROBE_ID}",
            {"title": "x"},
            {"Authorization": "Bearer garbage"},
        )
        checks.append(("坏 token 401", st == 401))
        # 3) 真 token 真改（agent 路径）
        st, body = _req(
            "PATCH", f"/api/sessions/{PROBE_ID}", {"title": "probe-renamed-ok"}, _token()
        )
        checks.append(("真 token 200", st == 200))
        checks.append(("响应形状恰 3 键", set(body.keys()) == {"success", "id", "title"}))
        checks.append(("真改标题回读", _title() == "probe-renamed-ok"))
        # 4) 缺行 404 契约
        st, body = _req("PATCH", "/api/sessions/e2e-no-such-session", {"title": "x"}, _token())
        checks.append(("缺行 404", st == 404 and body.get("detail") == "Session not found"))
        # 5) 空标题 400 契约
        st, body = _req("PATCH", f"/api/sessions/{PROBE_ID}", {"title": "  "}, _token())
        checks.append(("空标题 400", st == 400 and body.get("detail") == "Title is required"))
        # 6) health 回归
        st, _ = _req("GET", "/api/sessions/health")
        checks.append(("health 200", st == 200))
    else:
        print("need --pre/--post/--cleanup")
        return 2

    ok = sum(1 for _, v in checks if v)
    for name, v in checks:
        print(("PASS" if v else "FAIL"), name)
    print(f"result: {ok}/{len(checks)}")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
