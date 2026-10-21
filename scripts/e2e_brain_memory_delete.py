#!/usr/bin/env python3
"""MEM-③ brain 记忆面删除口 live e2e 探针（--pre 修前 GAP 实证 / --post 修后全绿）。

打真 :8090 栈：/remember（标量 query 参数面——MEM-② 探针坑：JSON body 被
FastAPI 静默忽略，必须 params）→ /recall 回读 → DELETE /api/brain/memories/{id}
各身份轴形态 → 清痕（用删除口自身清自建行）+ /api/brain/status 计数回基线。

修前预期 GAP：删除口路由不存在→owner DELETE 404（"Not Found"）等 6 项失败；
修后预期全绿。
"""

import argparse
import sys
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8090"
MARKER = f"mem3-e2e-{uuid.uuid4().hex[:8]}"
SESS = f"mem3-e2e-sess-{uuid.uuid4().hex[:6]}"

results: list[tuple[bool, str]] = []


def check(ok: bool, name: str, detail: str = ""):
    results.append((ok, name))
    print(("PASS" if ok else "FAIL"), name, detail)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["pre", "post"], default="post")
    args = ap.parse_args()
    c = httpx.Client(base_url=BASE, timeout=30)

    # 0. health + 基线计数
    r = c.get("/api/brain/health")
    check(r.status_code == 200, "P0 health 200", str(r.status_code))
    base_total = c.get("/api/brain/status").json()["memory"]["total_memories"]

    # 1. 写 2 条（query 参数面！）
    ids = []
    for i in (1, 2):
        r = c.post(
            "/api/brain/remember",
            params={
                "session_id": SESS,
                "content": f"{MARKER}-第{i}条待删记忆",
                "user_id": "default",
            },
        )
        ok = r.status_code == 200 and r.json().get("stored") is True
        check(ok, f"P1-{i} /remember 写入", str(r.status_code))
        if ok:
            ids.append(r.json()["memory_id"])

    # 2. /recall 回读 2 条
    r = c.get("/api/brain/recall", params={"session_id": SESS, "query": MARKER})
    rows = r.json()["memories"]
    check(len(rows) == 2, "P2 /recall 回读 2 条", f"n={len(rows)}")

    if len(ids) < 2:
        print("写入失败，后续跳过")
        return 1

    # 3. 跨用户删除 404 行存活（冒删面）
    r = c.delete(
        f"/api/brain/memories/{ids[0]}", params={"user_id": "mem3-attacker"}
    )
    check(
        r.status_code == 404 and r.json().get("detail") == "Memory not found",
        "P3 跨用户 DELETE 404 契约",
        f"{r.status_code} {r.text[:80]}",
    )
    r2 = c.get("/api/brain/recall", params={"session_id": SESS, "query": MARKER})
    check(len(r2.json()["memories"]) == 2, "P3b 行存活（recall 仍 2 条）")

    # 4. 缺行 404 与越权同契约（禁存在性探测）
    r_miss = c.delete("/api/brain/memories/no-such-id", params={"user_id": "mem3-attacker"})
    check(
        r_miss.status_code == 404 and r_miss.json() == r.json(),
        "P4 缺行与越权逐字同契约",
        f"{r_miss.status_code} {r_miss.text[:60]}",
    )

    # 5. 坏 token 401 行存活
    r = c.delete(
        f"/api/brain/memories/{ids[0]}",
        params={"user_id": "default"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    check(r.status_code == 401, "P5 坏 token 401", str(r.status_code))

    # 6. 属主删除成功（匿名自报 default 契约）
    r = c.delete(f"/api/brain/memories/{ids[0]}", params={"user_id": "default"})
    check(
        r.status_code == 200 and r.json() == {"memory_id": ids[0], "deleted": True},
        "P6 属主 DELETE 200 形状",
        f"{r.status_code} {r.text[:80]}",
    )
    r2 = c.get("/api/brain/recall", params={"session_id": SESS, "query": MARKER})
    check(len(r2.json()["memories"]) == 1, "P6b 删除后 recall 1 条")

    # 7. 重复删除=缺行 404（计数不双退）
    r = c.delete(f"/api/brain/memories/{ids[0]}", params={"user_id": "default"})
    check(r.status_code == 404, "P7 重复 DELETE 404（缺行契约）", str(r.status_code))

    # 8. 清痕：删除口自身清第二条 → 残留 0 + 计数回基线
    r = c.delete(f"/api/brain/memories/{ids[1]}", params={"user_id": "default"})
    check(r.status_code == 200, "P8 清痕 DELETE 第二条", str(r.status_code))
    r2 = c.get("/api/brain/recall", params={"session_id": SESS, "query": MARKER})
    check(len(r2.json()["memories"]) == 0, "P8b 清痕 remaining=0")
    end_total = c.get("/api/brain/status").json()["memory"]["total_memories"]
    check(end_total == base_total, "P8c status 计数回基线", f"{base_total} -> {end_total}")

    ok_n = sum(1 for ok, _ in results if ok)
    print(f"\n--mode={args.mode}  {ok_n}/{len(results)}")
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
