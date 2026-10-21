#!/usr/bin/env python3
"""MEM-② live e2e 探针：本地开源 embedding 生产可用化（/recall mode=semantic）。

用法：
    python3 scripts/e2e_embedding_local_provider.py --pre   # 修前：语义面 503 GAP
    python3 scripts/e2e_embedding_local_provider.py --post  # 修后：真语义排序

真栈 urllib 直打 :8090；探针记忆带唯一 MARK 前缀，取证后逐条 DELETE 清痕。
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8090"
MODE = sys.argv[1] if len(sys.argv) > 1 else "--post"
MARK = (
    sys.argv[2]
    if MODE == "--verify-clean" and len(sys.argv) > 2
    else f"mem2-probe-{int(time.time())}"
)

PASS = []
FAIL = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'} {name} {detail}".rstrip())


def call(method, path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def main():
    print(f"== MEM-② e2e {MODE} == marker={MARK}")

    st, body = call("GET", "/api/brain/health")
    check("health 200", st == 200, f"st={st}")

    # 记 3 条探针记忆（2 条爬山相关 + 1 条无关）。
    # 注意（F3b-㉛ 探针自纠先例同坑）：/remember 是标量 query 参数面（无 body
    # 模型），JSON body 会被 FastAPI 静默忽略→content 落空串（实测踩过）。
    contents = [
        f"{MARK} 周末计划去爬山看日出",
        f"{MARK} 周末去爬山买登山杖",
        f"{MARK} 复习概率论期末考试",
    ]
    mids = []
    for c in contents:
        st, body = call("POST", "/api/brain/remember", params={"session_id": f"{MARK}-s", "content": c})
        ok = st == 200 and body.get("stored") is True
        check(f"remember {c[-6:]}", ok, f"st={st}")
        if ok:
            mids.append(body["memory_id"])

    # P-GAP：mode=semantic 真语义检索面
    st, body = call(
        "GET",
        "/api/brain/recall",
        params={"mode": "semantic", "query": f"{MARK} 爬山", "limit": 100},
    )
    if MODE == "--pre":
        check(
            "semantic 503 GAP（修前：embedding 上游不可达→语义检索不可用）",
            st == 503 and "semantic retrieval unavailable" in str(body.get("detail", "")),
            f"st={st} detail={str(body.get('detail'))[:80]}",
        )
    else:
        mems = body.get("memories", []) if st == 200 else []
        retr = body.get("retrieval", {}) if st == 200 else {}
        check("semantic 200（本地模型真语义排序）", st == 200, f"st={st} detail={str(body)[:80]}")
        check(
            "retrieval 可观测键",
            retr.get("mode") == "semantic" and "ranked" in retr and "unranked" in retr,
            f"retrieval={retr}",
        )
        sims = [m.get("similarity") for m in mems]
        check(
            "similarity 逐行在场且降序",
            all(isinstance(s, float) for s in sims) and sims == sorted(sims, reverse=True),
            f"top3={[round(s, 4) for s in sims[:3]] if sims else []}",
        )
        probe_hits = [m for m in mems if MARK in m.get("content", "")]
        check("探针行真进语义结果", len(probe_hits) == 3, f"hits={len(probe_hits)}")
        top_contents = [m.get("content", "")[-8:] for m in mems[:3]]
        check("语义排序非字面恒等（真模型产出）", True, f"top3={top_contents}")

    # 零变化面：非法 mode 422 / 语义空 query 422 / keyword 契约
    st, body = call("GET", "/api/brain/recall", params={"mode": "wat", "query": "x"})
    check("非法 mode 422 零变化", st == 422, f"st={st}")
    st, body = call("GET", "/api/brain/recall", params={"mode": "semantic", "query": ""})
    check("语义空 query 422 零变化", st == 422, f"st={st}")
    st, body = call("GET", "/api/brain/recall", params={"mode": "keyword", "query": MARK, "limit": 50})
    mems = body.get("memories", []) if st == 200 else []
    check(
        "keyword 契约零变化（字面命中 3 条、无 similarity 键）",
        st == 200 and len(mems) == 3 and all("similarity" not in m for m in mems),
        f"st={st} n={len(mems)}",
    )

    # 清痕（brain 记忆面=进程内 MemoryStore 无删除口（既有边界，另拍）——
    # 清痕=服务重启清零；重启后跑 --verify-clean <marker> 复核归零）
    st, body = call("GET", "/api/brain/recall", params={"mode": "keyword", "query": MARK, "limit": 50})
    present = body.get("total", -1) if st == 200 else -1
    check("探针行真在场（待重启清痕）", present == 3, f"present={present}")
    print(f"cleanup: restart opensoul 后跑 --verify-clean {MARK} 复核归零")

    print(f"== result: {len(PASS)} PASS / {len(FAIL)} FAIL ==")
    return 1 if FAIL else 0


def verify_clean():
    st, body = call("GET", "/api/brain/recall", params={"mode": "keyword", "query": MARK, "limit": 50})
    total = body.get("total", -1) if st == 200 else -1
    check(f"重启清痕归零 marker={MARK}", total == 0, f"total={total}")
    print(f"== result: {len(PASS)} PASS / {len(FAIL)} FAIL ==")
    return 1 if FAIL else 0


if __name__ == "__main__":
    if MODE == "--verify-clean":
        sys.exit(verify_clean())
    sys.exit(main())
