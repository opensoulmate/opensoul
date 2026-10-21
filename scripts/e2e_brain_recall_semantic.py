#!/usr/bin/env python3
"""MEM-① live e2e 探针：/api/brain/recall mode 语义检索面（--pre 修前 / --post 修后）。

匿名自报 user_id 路径（F3b-㉛ 匿名契约）——零生产库写入：brain 记忆面为
in-memory store，探针行随服务重启清零（--post 后复重启 --residual 断言）。

--pre（旧码，mode 零消费）：mode=semantic 被静默忽略回关键词结果=「客户端要
语义被静默降级」GAP 坐实；mode=semantic 空 query / 非法 mode 照常 200。
--post（新码）：semantic=真语义面（embedding 可用=排序+similarity+retrieval
可观测；上游不可用=503 带真因禁静默降级）、空 query/非法 mode 如实 422、
keyword 形状逐键不变、归属过滤先于排名。
--residual（复重启后）：marker 桶回空集=探针行清零。

marker 记 /tmp/e2e_brain_recall_semantic.marker 供 --residual 复读。
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8090"
MARKER_FILE = Path("/tmp/e2e_brain_recall_semantic.marker")


def _req(method, path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw}


def _remember(uid, content):
    return _req("POST", "/api/brain/remember", params={"user_id": uid, "content": content})


def _recall(uid, **params):
    return _req("GET", "/api/brain/recall", params={"user_id": uid, **params})


def _timeline_count():
    import sqlite3

    db = Path(__file__).resolve().parent.parent / "data" / "timeline.db"
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
            return c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    except Exception:
        return None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--post"
    assert mode in ("--pre", "--post", "--residual"), mode
    checks = []

    def check(name, ok):
        checks.append((name, ok))
        print(("PASS " if ok else "FAIL ") + name)

    if mode == "--residual":
        marker = MARKER_FILE.read_text().strip()
        uid = f"{marker}-user"
        s, rows = _recall(uid)
        check(f"residual 0 rows for {uid} (got {len(rows.get('memories', []))})", s == 200 and rows.get("memories") == [])
        failed = [n for n, ok in checks if not ok]
        print(f"\n{mode} {len(checks) - len(failed)}/{len(checks)} passed")
        return 1 if failed else 0

    marker = f"recallsem-e2e-{int(time.time())}"
    uid = f"{marker}-user"
    MARKER_FILE.write_text(marker)
    c1, c2, c3 = f"{marker} 周末计划去爬山", f"{marker} 半相关主题", f"{marker} 复习概率论"

    tl_before = _timeline_count()
    for content in (c1, c2, c3):
        status, _ = _remember(uid, content)
        assert status == 200, f"remember failed {status}"

    s, baseline = _recall(uid)
    check("baseline 3 rows written", s == 200 and len(baseline["memories"]) == 3)

    if mode == "--pre":
        # GAP①：mode=semantic 被静默忽略→回关键词/全量结果（无 retrieval 键）
        s1, sem = _recall(uid, query="爬山", mode="semantic")
        check(
            f"pre-gap mode=semantic silently ignored (200 no retrieval key, got {len(sem.get('memories', []))} rows)",
            s1 == 200 and "retrieval" not in sem and "similarity" not in (sem.get("memories") or [{}])[0],
        )
        # GAP②：语义模式空 query 无 422
        s2, sem_empty = _recall(uid, query="", mode="semantic")
        check(f"pre-gap semantic empty query not 422 (got {s2})", s2 == 200)
        # GAP③：非法 mode 无 422
        s3, bad = _recall(uid, query="x", mode="vector")
        check(f"pre-gap invalid mode not 422 (got {s3})", s3 == 200)
    else:
        # semantic 面：embedding 可用=真排序；上游不可用=503 带真因（禁静默降级）
        s1, sem = _recall(uid, query="爬山", mode="semantic")
        if s1 == 503:
            check(
                "semantic embedding unavailable -> honest 503 (no silent keyword fallback)",
                "semantic retrieval unavailable" in str(sem.get("detail", "")),
            )
        else:
            check("semantic 200 with retrieval.mode", s1 == 200 and sem.get("retrieval", {}).get("mode") == "semantic")
            sims = [m.get("similarity") for m in sem["memories"]]
            check(f"per-item similarity key present ({sims})", all(isinstance(x, (int, float)) for x in sims))
            check(
                "ranked by similarity desc",
                sims == sorted(sims, reverse=True) and sem["retrieval"]["candidates"] == 3,
            )
            check(
                "semantic elem keys additive",
                set(sem) == {"memories", "total", "retrieval"}
                and set(sem["memories"][0]) == {"id", "content", "importance", "tags", "retention", "similarity"},
            )
            s1b, other = _recall("nobody-x", query="爬山", mode="semantic")
            check("owner isolation (other bucket empty)", s1b == 200 and other["memories"] == [])

        # 诚实失败面：空 query / 非法 mode 如实 422
        s2, sem_empty = _recall(uid, query="  ", mode="semantic")
        check(
            "semantic empty query 422 with real reason",
            s2 == 422 and "semantic" in str(sem_empty.get("detail", "")),
        )
        s3, bad = _recall(uid, query="x", mode="vector")
        check(
            "invalid mode 422 with real reason",
            s3 == 422 and "invalid mode" in str(bad.get("detail", "")),
        )

        # keyword 形状逐键不变（零回归）
        s4, needle = _recall(uid, query="爬山")
        elem = (needle.get("memories") or [{}])[0]
        check(
            "keyword shape unchanged",
            s4 == 200
            and set(needle) == {"memories", "total"}
            and (not elem or set(elem) == {"id", "content", "importance", "tags", "retention"}),
        )

    tl_after = _timeline_count()
    check(
        f"timeline delta=0 ({tl_before}->{tl_after})",
        tl_before is None or tl_after is None or tl_before == tl_after,
    )

    failed = [n for n, ok in checks if not ok]
    print(f"\n{mode} {len(checks) - len(failed)}/{len(checks)} passed; rows={uid} in-memory (restart clears)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
