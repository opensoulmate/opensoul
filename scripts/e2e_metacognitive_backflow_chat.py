#!/usr/bin/env python3
"""G1-④b live e2e 探针：元认知纠错回流对话面（blind_spots/calibration → chat prompt）。

控制组（('default','default') 账本无盲区=零注入，token-attribution 无
metacognitive_advisory 行）→ 真 /api/brain/think+verify 落账（think 决策行
ref=mk ↔ verify 成败行 ref=mk 配对 + 同 decision 失败×2 盲区）→ 盲区/校准
真现身 → 再跑 /api/chat/ → 纠偏块真进 prompt（token-attribution
metacognitive_advisory 逐行可观测）。
取证后 SQL 清 metacognition_log 探针行。
"""

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/opensoul.db"


def req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode() or "{}"
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"raw": raw[:2000]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:2000]}


def check(name, ok, detail=""):
    print(("PASS" if ok else "FAIL"), name, detail if not ok else "")
    return ok


def chat_and_attrib(question, user_id):
    """POST /api/chat/ stream=true（SSE）→ 返回最新归因记录。"""
    data = json.dumps(
        {"question": question, "top_k": 5, "stream": True}
    ).encode()
    r = urllib.request.Request(
        BASE + f"/api/chat/?user_id={user_id}", data=data, method="POST"
    )
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=120) as resp:
        resp.read()  # SSE 全量读尽（真 LLM 真流式）
    st, att = req("GET", BASE + "/api/chat/token-attribution?limit=3")
    recent = att.get("recent") or []
    return (recent[0] if recent else {}), st


def main():
    results = []
    mk = "G1D4B" + uuid.uuid4().hex[:8]
    area = f"e2e{mk}盲区动作"
    user_id = str(uuid.uuid4())

    st, health = req("GET", BASE + "/api/health")
    results.append(check("P1 health 200", st == 200))

    # 基线：('default','default') 账本无本探针盲区（既有观测如实记录）
    st, meta0 = req("GET", BASE + "/api/brain/metacognition?agent_id=default&tenant_id=default")
    bs0 = meta0.get("blind_spots") or []
    results.append(check("P2 基线无本探针盲区", all(area not in str(b) for b in bs0), str(bs0)[:200]))

    # 控制组：无观测=零注入（归因记录无 metacognitive_advisory 行）
    rec0, st = chat_and_attrib(f"帮我查看当前目录（探针{mk}A）", user_id)
    names0 = [t.get("name", "") for t in (rec0.get("usage", {}).get("top_consumers") or [])]
    results.append(check("P3 控制组零注入（归因无 metacognitive_advisory 行）",
                         not any(str(n).startswith("metacognitive_advisory[") for n in names0),
                         str(names0)[:300]))

    # 真落账（生产写点）：think 决策行（ref=mk）↔ verify 成败行（ref=mk）配对
    # + 同 decision 失败 ×2 → 盲区（fail_count>=2）
    st, th = req("POST", BASE + "/api/brain/think",
                 {"agent_id": "default", "user_input": area, "session_id": mk})
    results.append(check("P4 think 落账 200", st == 200, f"{st} {str(th)[:150]}"))
    for i, tid in enumerate((mk, f"{mk}-b", f"{mk}-c")):
        st, vr = req("POST", BASE + "/api/brain/verify",
                     {"agent_id": "default", "task_id": tid, "action": area,
                      "result": {"success": False}})
        results.append(check(f"P5.{i} verify 落账 200", st == 200, f"{st} {str(vr)[:150]}"))

    st, meta1 = req("GET", BASE + "/api/brain/metacognition?agent_id=default&tenant_id=default")
    bs1 = meta1.get("blind_spots") or []
    hit = [b for b in bs1 if area in str(b.get("area", ""))]
    results.append(check("P6 盲区真现身 fail_count>=2",
                         bool(hit) and int(hit[0].get("fail_count", 0)) >= 2, str(bs1)[:200]))
    calib = meta1.get("calibration") or {}
    results.append(check("P7 校准真配对（think↔verify 同 ref）",
                         calib.get("calibration") == "miscalibrated"
                         and calib.get("paired", 0) >= 1, str(calib)[:200]))

    # 注入真发生：归因记录含 metacognitive_advisory 逐行（kind=improvement）
    rec1, st = chat_and_attrib(f"帮我查看当前目录（探针{mk}B）", user_id)
    usage1 = rec1.get("usage", {})
    names1 = [t.get("name", "") for t in (usage1.get("top_consumers") or [])]
    meta_names = [n for n in names1 if str(n).startswith("metacognitive_advisory[")]
    results.append(check("P8 纠偏块真进 chat prompt（归因逐行可观测）",
                         bool(meta_names)
                         and float(usage1.get("sections", {}).get("improvement", 0) or 0) > 0,
                         f"names={names1} sections={usage1.get('sections')}"))

    # 清痕：metacognition_log 探针行
    conn = sqlite3.connect(DB)
    conn.execute(
        "DELETE FROM metacognition_log WHERE decision LIKE ? OR reasoning LIKE ? OR ref LIKE ?",
        (f"%{mk}%", f"%{mk}%", f"{mk}%"),
    )
    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM metacognition_log WHERE decision LIKE ? OR reasoning LIKE ? OR ref LIKE ?",
        (f"%{mk}%", f"%{mk}%", f"{mk}%"),
    ).fetchone()[0]
    conn.close()
    results.append(check("P9 清痕 remaining=0", remaining == 0, f"remaining={remaining}"))

    ok = all(results)
    print(f"\n== G1-④b e2e {sum(results)}/{len(results)} ==")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
