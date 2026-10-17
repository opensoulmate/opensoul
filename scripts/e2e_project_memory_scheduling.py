#!/usr/bin/env python3
"""e2e 探针：H1-②b-⑩ project_memory 缓存增量调度。

--pre  = 旧码（服务未载新码）同探针：think 每请求全仓重扫（t2≈t1，两次都付全扫）
         + save_cache 零消费（缓存从未落盘）=缺口硬证。
--post = 新码：共享实例 TTL 节流（t2≪t1 零扫描）+ 缓存真落盘（~/.opensoul 真件）
         + /refresh 强制刷新共享真源（新增文件真发现，与 think 同一实例）。

数据面：探针临时仓（/tmp，用完删）+ 生产 timeline.db events 与 opensoul.db
metacognition_log 探针行（标记文件名 pm_e2e_<id>.py 进 target_files→reasoning/
detail 可归因），取证后清痕 remaining=0。
"""

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://localhost:8090"
TIMELINE_DB = os.path.expanduser("~/opensoul/data/timeline.db")
OPENSOUL_DB = os.path.expanduser("~/opensoul/data/opensoul.db")
CACHE_DIR = os.path.expanduser("~/.opensoul")
N_FILES = 400

MODE = sys.argv[1] if len(sys.argv) > 1 else "--post"
MARKER = f"pm_e2e_{int(time.time())}"
MARKER_FILE = f"{MARKER}.py"

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name} :: {detail}")


def req(method, path, body=None, params=None) -> tuple[int, dict]:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read()
            return resp.status, (dict(json.loads(raw)) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, dict(json.loads(raw))
        except Exception:
            return e.code, {"raw": raw.decode()[:200]}
    finally:
        req.last_elapsed = time.monotonic() - t0


req.last_elapsed = 0.0


def make_repo() -> str:
    repo = tempfile.mkdtemp(prefix="pm_e2e_repo_")
    for i in range(N_FILES):
        prev = f"import mod_{i - 1:03d}\n" if i else ""
        body = "\n".join(f"val_{i}_{j} = {j}" for j in range(50))
        with open(os.path.join(repo, f"mod_{i:03d}.py"), "w") as f:
            f.write(prev + body + "\n")
    with open(os.path.join(repo, MARKER_FILE), "w") as f:
        f.write("marker = 1\n")
    return repo


def cache_file(repo: str) -> str:
    digest = hashlib.sha1(os.path.realpath(repo).encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"project_memory_{digest}.json")


def db_exec(db, sql, args=()):
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def cleanup_probe_rows() -> tuple[int, int]:
    like = f"%{MARKER}%"
    n1 = db_exec(
        TIMELINE_DB,
        "SELECT COUNT(*) FROM events WHERE detail LIKE ? OR summary LIKE ?",
        (like, like),
    )[0][0]
    db_exec(
        TIMELINE_DB,
        "DELETE FROM events WHERE detail LIKE ? OR summary LIKE ?",
        (like, like),
    )
    n2 = db_exec(
        OPENSOUL_DB,
        "SELECT COUNT(*) FROM metacognition_log WHERE reasoning LIKE ? OR ref LIKE ?",
        (like, like),
    )[0][0]
    db_exec(
        OPENSOUL_DB,
        "DELETE FROM metacognition_log WHERE reasoning LIKE ? OR ref LIKE ?",
        (like, like),
    )
    left1 = db_exec(
        TIMELINE_DB,
        "SELECT COUNT(*) FROM events WHERE detail LIKE ? OR summary LIKE ?",
        (like, like),
    )[0][0]
    left2 = db_exec(
        OPENSOUL_DB,
        "SELECT COUNT(*) FROM metacognition_log WHERE reasoning LIKE ? OR ref LIKE ?",
        (like, like),
    )[0][0]
    return (n1 + n2), (left1 + left2)


def main():
    print(f"== e2e project_memory scheduling [{MODE}] marker={MARKER} ==")
    repo = make_repo()
    think_body = {"user_input": f"修复 {MARKER_FILE} 的 bug", "repo_root": repo}
    try:
        # 1. think 计时（cold 先行——/refresh 会预热共享实例，时序禁反）：
        #    t1=首请求（--post 修前冷扫描/修后落盘前冷扫），t2/t3=TTL 内再取
        c1, _ = req("POST", "/api/brain/think", body=think_body)
        t1 = req.last_elapsed
        c2, _ = req("POST", "/api/brain/think", body=think_body)
        t2 = req.last_elapsed
        c3, _ = req("POST", "/api/brain/think", body=think_body)
        t3 = req.last_elapsed
        check("T think ×3 全 200", c1 == c2 == c3 == 200, f"{c1}/{c2}/{c3}")
        print(f"     think 耗时 t1={t1:.3f}s t2={t2:.3f}s t3={t3:.3f}s")

        cfile = cache_file(repo)
        if MODE == "--pre":
            # 修前：save_cache 全仓零消费=缓存从未落盘
            check("P1 缓存从未落盘（修前缺口硬证）", not os.path.exists(cfile), cfile)
            # 修前：每请求全仓重扫=两次都付全扫（t2 不显著快于 t1）
            check(
                "P2 每请求全仓重扫 t2>0.4·t1（修前缺口硬证）",
                t2 > 0.4 * t1,
                f"t1={t1:.3f} t2={t2:.3f}",
            )
        else:
            # 修后：缓存真落盘
            check("P1 缓存真落盘", os.path.exists(cfile), cfile)
            try:
                cdata = json.load(open(cfile))
                check(
                    "P1 缓存件含对账键",
                    all(
                        "size_bytes" in m and "last_modified" in m
                        for m in cdata["files"].values()
                    ),
                    "staleness keys",
                )
            except Exception as e:
                check("P1 缓存件可解析", False, str(e))
            # 修后：TTL 内零扫描（t2/t3 ≪ t1）
            check(
                "P2 TTL 内零扫描 t2<0.5·t1",
                t2 < 0.5 * t1,
                f"t1={t1:.3f} t2={t2:.3f}",
            )
            check(
                "P2 TTL 内零扫描 t3<0.5·t1",
                t3 < 0.5 * t1,
                f"t1={t1:.3f} t3={t3:.3f}",
            )

        # 2. 基线：/refresh 统计契约（两代同形）
        code, data = req("POST", "/api/brain/refresh", params={"repo_root": repo})
        check("R0 /refresh 200", code == 200, f"code={code}")
        base_total = int(data.get("total_files") or 0)
        check(
            "R0 基线 total_files=N+1",
            base_total == N_FILES + 1,
            f"total_files={base_total}",
        )
        for key in ("status", "total_files", "core_files", "total_dependencies", "last_scan"):
            check(f"R0 stats 契约键 {key}", key in data, f"keys={sorted(data)}")

        if MODE == "--post":
            # 3. 新增文件→/refresh 强制刷新共享真源（真发现）
            with open(os.path.join(repo, "late_mod.py"), "w") as f:
                f.write("late = 1\n")
            code, data = req("POST", "/api/brain/refresh", params={"repo_root": repo})
            check("P3 /refresh 200", code == 200, f"code={code}")
            check(
                "P3 新增文件真发现 total_files=基线+1",
                data.get("total_files") == base_total + 1,
                f"{base_total} -> {data.get('total_files')}",
            )
            # 4. 刷新后 think 仍走同一共享真源（TTL 内零扫描不回退）
            c4, _ = req("POST", "/api/brain/think", body=think_body)
            t4 = req.last_elapsed
            check("P4 think 200", c4 == 200, f"code={c4}")
            check("P4 刷新后 think 仍快 t4<0.5·t1", t4 < 0.5 * t1, f"t1={t1:.3f} t4={t4:.3f}")
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        if os.path.exists(cfile):
            os.remove(cfile)
        n, left = cleanup_probe_rows()
        check("C1 清痕 remaining=0", left == 0, f"cleaned={n} left={left}")
        print(f"     清理: 探针行 {n} 行删除, 临时仓/缓存件已删")

    print(f"== {MODE} 结果: {PASS} ok / {FAIL} fail ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
