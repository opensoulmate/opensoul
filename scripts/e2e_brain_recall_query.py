#!/usr/bin/env python3
"""F3b-㉜ live e2e 探针：/api/brain/recall query 参数真消费（--pre 修前 / --post 修后）。

匿名自报 user_id 路径（身份轴零改动面，F3b-㉛ 匿名契约）——零生产库写入：
brain 记忆面为 in-memory store，探针行随服务重启清零（--post 后复重启断言）。

--pre（旧码，query 零消费）：带 query 照旧返回全部行=「声明了不消费」GAP 坐实。
--post（新码）：query 字面子串过滤、空 query 零过滤契约不变、形状逐键不变。
"""

import json
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8090"


def _req(method, path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode())


def _remember(uid, content):
    return _req(
        "POST",
        "/api/brain/remember",
        params={"user_id": uid, "content": content},
    )


def _recall(uid, **params):
    return _req("GET", "/api/brain/recall", params={"user_id": uid, **params})


def _timeline_count():
    import sqlite3
    from pathlib import Path

    db = Path(__file__).resolve().parent.parent / "data" / "timeline.db"
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
            return c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    except Exception:
        return None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--post"
    assert mode in ("--pre", "--post"), mode
    marker = f"recallq-e2e-{int(time.time())}"
    uid = f"{marker}-user"
    c1, c2, c3 = f"{marker} needle 甲", f"{marker} 无关乙", f"{marker} needle 丙"

    tl_before = _timeline_count()
    for content in (c1, c2, c3):
        status, _ = _remember(uid, content)
        assert status == 200, f"remember failed {status}"

    checks = []

    def check(name, ok):
        checks.append((name, ok))
        print(("PASS " if ok else "FAIL ") + name)

    s, all_rows = _recall(uid)
    contents = sorted(m["content"] for m in all_rows["memories"])
    check("baseline 3 rows written", s == 200 and len(contents) == 3)

    s, empty = _recall(uid, query="")
    check(
        "empty query contract unchanged (3 rows)",
        s == 200 and len(empty["memories"]) == 3 and empty["total"] == 3,
    )

    s, needle = _recall(uid, query="needle")
    n_count = len(needle["memories"])
    if mode == "--pre":
        # GAP：query 被静默忽略→仍回全部 3 行（含 无关乙）
        check(f"pre-gap query ignored (got {n_count}==3)", s == 200 and n_count == 3)
        s2, other = _recall(uid, query="绝不命中xyz")
        check(
            f"pre-gap unmatched query still returns all (got {len(other['memories'])})",
            s2 == 200 and len(other["memories"]) == 3,
        )
    else:
        got = sorted(m["content"] for m in needle["memories"])
        check(
            "query=needle filters to 2 needle rows",
            s == 200 and got == sorted([c1, c3]) and needle["total"] == 2,
        )
        s2, other = _recall(uid, query="无关乙")
        check(
            "query=无关乙 exact 1 row",
            s2 == 200
            and [m["content"] for m in other["memories"]] == [c2]
            and other["total"] == 1,
        )
        s3, none = _recall(uid, query="绝不命中xyz")
        check("unmatched query empty + total 0", s3 == 200 and none == {"memories": [], "total": 0})
        elem_keys = set(needle["memories"][0])
        check(
            "shape keys unchanged",
            set(needle) == {"memories", "total"}
            and elem_keys == {"id", "content", "importance", "tags", "retention"},
        )
        s4, ws = _recall(uid, query="   ")
        check("whitespace query = zero filter (3 rows)", s4 == 200 and len(ws["memories"]) == 3)

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
