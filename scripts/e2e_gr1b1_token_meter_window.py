#!/usr/bin/env python3
"""GR-①b-① live e2e 探针——TokenMeter 内存窗+计数真源化 修前/修后双向硬证。

用法（三阶段，各自独立跑）：
  python3 scripts/e2e_gr1b1_token_meter_window.py --pre      # 修前（旧进程）：GAP 坐实
  python3 scripts/e2e_gr1b1_token_meter_window.py --post     # 修后：首行 restart() 载新码再验
  python3 scripts/e2e_gr1b1_token_meter_window.py --cleanup  # 清痕（SQL 删探针行+restart 同步缓存+复核 remaining=0）

纪律（既有探针自伤坑记档）：--post 首行必 restart()——写入跑在载新码重启之前
=全部 FAIL 打旧进程（RV-①b-⑤-2/⑥-1 同款自伤再现防）。

GAP 可观测面（修前/修后 API 契约零差异=设计目标「零可观测变化」，双向硬证取
「读面是否恒对账行真源」这一真差异）：
  - 修前：读面=进程内存折叠——库外落行（真源先行）summary 不可见直到重启；
  - 修后：summary 恒读 gland_usage 行聚合（MEM-⑥ 计数真源化）——库外落行即可见。
另验：真实 LLM 写穿路径（/api/gland/chat→ModelRouter→token_meter.record→行落盘）、
restart 后读面==行聚合逐键（回载窗不漏计）、API 键形契约零变化。
"""

import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/gland_usage.db"
MARKER_TASK = "e2e-gr1b1-marker"
STATE = "/tmp/e2e_gr1b1_state.json"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


def api(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"raw": raw}


def restart() -> None:
    subprocess.run(["systemctl", "--user", "restart", "opensoul.service"], check=True)
    for _ in range(60):
        time.sleep(1)
        try:
            status, _ = api("GET", "/api/health")
            if status == 200:
                return
        except Exception:
            continue
    raise RuntimeError("service did not come back")


def sql(queries: list[tuple[str, tuple]]) -> list:
    conn = sqlite3.connect(DB)
    try:
        out = []
        for q, args in queries:
            out.append(conn.execute(q, args).fetchall())
        conn.commit()
        return out
    finally:
        conn.close()


def db_aggregates() -> dict:
    rows = sql([("SELECT COUNT(*), COALESCE(SUM(total_tokens),0) FROM gland_usage", ())])[0]
    cnt, total = rows[0]
    return {"call_count": int(cnt), "total_tokens": int(total)}


def marker_rows() -> list[tuple]:
    (rows,) = sql(
        [("SELECT id, task, total_tokens FROM gland_usage WHERE task = ?", (MARKER_TASK,))]
    )
    return rows


def summary() -> dict:
    status, body = api("GET", "/api/gland/usage")
    if status != 200:
        raise RuntimeError(f"usage {status}: {body}")
    return body


def finish() -> None:
    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n== {len(RESULTS) - n_fail}/{len(RESULTS)} PASS ==")
    sys.exit(1 if n_fail else 0)


def phase_pre() -> None:
    print("== --pre（旧进程）==")
    agg0 = db_aggregates()
    s0 = summary()
    check("baseline 读面==行聚合", s0["call_count"] == agg0["call_count"] and s0["total_tokens"] == agg0["total_tokens"],
          f"summary({s0['call_count']},{s0['total_tokens']}) vs sql({agg0})")
    (base_id_row,) = sql([("SELECT COALESCE(MAX(id),0) FROM gland_usage", ())])
    base_id = int(base_id_row[0][0])  # 基线行界：其后全部=探针痕迹（含真 LLM 写穿行）

    # ① 真实 LLM 写穿路径（用户真实路径：gland chat→record→行落盘）
    status, body = api("POST", "/api/gland/chat", {
        "messages": [{"role": "user", "content": "只回一个数字：1+1=?"}],
        "task": "chat",
        "max_tokens": 16,
    })
    check("真 LLM chat 200", status == 200, f"status={status}")
    agg1 = db_aggregates()
    check("写穿：行真源 +N", agg1["call_count"] > agg0["call_count"], f"{agg0['call_count']}->{agg1['call_count']}")
    s1 = summary()
    check("写后 读面==行聚合", s1["call_count"] == agg1["call_count"] and s1["total_tokens"] == agg1["total_tokens"],
          f"summary({s1['call_count']},{s1['total_tokens']}) vs sql({agg1})")
    # ② GAP 坐实：库外落行（真源先行）旧读面看不见（内存折叠，无真源对账）
    sql([("INSERT INTO gland_usage (provider, model, prompt_tokens, completion_tokens,"
          " total_tokens, user_id, task, timestamp) VALUES (?,?,?,?,?,?,?,?)",
          ("e2e-marker", "m", 3, 4, 7, None, MARKER_TASK, time.time()))])
    agg2 = db_aggregates()
    s2 = summary()
    stale = s2["call_count"] == agg1["call_count"]  # 旧码：读面停在内存值
    check("GAP：库外行 summary 不可见（修前预期 True）", stale,
          f"summary={s2['call_count']} sql={agg2['call_count']}（修前应 stale）")

    # 契约键形快照（--post 对比零变化）
    keys = list(s2.keys())
    check("summary 键形契约在场", keys[:5] == ["total_tokens", "total_prompt_tokens",
          "total_completion_tokens", "budget_limit", "remaining_budget"], str(keys))
    status, recent = api("GET", "/api/gland/usage/recent?limit=5")
    elem_keys = set(recent.get("records", [{}])[0]) if recent.get("records") else set()
    check("recent 元素键形契约在场", elem_keys == {"provider", "model", "prompt_tokens",
          "completion_tokens", "total_tokens", "user_id", "task", "timestamp"}, str(sorted(elem_keys)))

    json.dump({"agg0": agg0, "base_id": base_id, "summary_keys": keys,
               "s0": s0}, open(STATE, "w"))
    print(f"  [state] baseline={agg0} base_id={base_id} -> {STATE}")
    finish()


def phase_post() -> None:
    print("== --post（restart 载新码）==")
    restart()
    agg = db_aggregates()
    s = summary()
    check("restart 后 读面==行聚合（含库外 marker 行）",
          s["call_count"] == agg["call_count"] and s["total_tokens"] == agg["total_tokens"],
          f"summary({s['call_count']},{s['total_tokens']}) vs sql({agg})")
    check("marker 行计入计数（修前 stale）", any(r[1] == MARKER_TASK for r in marker_rows()) and
          s["call_count"] == agg["call_count"], f"marker={marker_rows()}")

    # ③ 修后行为：库外落行即可见（读面恒对账行聚合）
    before = db_aggregates()
    sql([("INSERT INTO gland_usage (provider, model, prompt_tokens, completion_tokens,"
          " total_tokens, user_id, task, timestamp) VALUES (?,?,?,?,?,?,?,?)",
          ("e2e-marker", "m", 1, 1, 2, None, MARKER_TASK, time.time()))])
    after = db_aggregates()
    s2 = summary()
    check("库外行 summary 即时可见（修后收口硬证）",
          s2["call_count"] == after["call_count"] and s2["call_count"] == before["call_count"] + 1,
          f"before={before['call_count']} after={after['call_count']} summary={s2['call_count']}")

    st = json.load(open(STATE))
    check("summary 键形契约零变化", list(s2.keys()) == st["summary_keys"],
          f"{list(s2.keys())}")
    status, recent = api("GET", "/api/gland/usage/recent?limit=200")
    rows = sql([("SELECT provider, model, task, total_tokens FROM gland_usage ORDER BY id DESC LIMIT 200", ())])[0]
    check("recent==行真源尾序（回载窗不漏计）",
          [(r["provider"], r["model"], r["task"], r["total_tokens"]) for r in recent.get("records", [])]
          == [tuple(r) for r in reversed(rows)], f"n={len(recent.get('records', []))}")
    finish()


def phase_cleanup() -> None:
    print("== --cleanup ==")
    st = json.load(open(STATE))
    (rows,) = sql([("SELECT id FROM gland_usage WHERE id > ? OR task = ?",
                    (st["base_id"], MARKER_TASK))])
    victim_ids = [r[0] for r in rows]
    sql([("DELETE FROM gland_usage WHERE id > ? OR task = ?", (st["base_id"], MARKER_TASK))])
    (left,) = sql([("SELECT COUNT(*) FROM gland_usage WHERE id > ? OR task = ?",
                    (st["base_id"], MARKER_TASK))])
    check("探针行全清 remaining=0", left[0][0] == 0, f"deleted={victim_ids} remaining={left[0][0]}")
    restart()  # 同步内存窗缓存（读面回落/快路径都归基线）
    s = summary()
    agg = db_aggregates()
    check("清痕后 读面==行聚合==基线", s["call_count"] == agg["call_count"] == st["agg0"]["call_count"]
          and s["total_tokens"] == agg["total_tokens"] == st["agg0"]["total_tokens"],
          f"summary({s['call_count']},{s['total_tokens']}) sql({agg}) baseline({st['agg0']})")
    status, recent = api("GET", "/api/gland/usage/recent?limit=50")
    bad = [r for r in recent.get("records", []) if r.get("task") == MARKER_TASK or r.get("provider") == "e2e-marker"]
    check("recent 无探针残痕", not bad, f"bad={bad[:3]}")
    finish()


if __name__ == "__main__":
    {"--pre": phase_pre, "--post": phase_post, "--cleanup": phase_cleanup}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: print("usage: --pre|--post|--cleanup")
    )()
