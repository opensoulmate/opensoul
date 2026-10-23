#!/usr/bin/env python3
"""RV-①b-⑭-1 live e2e 探针——VersionRegistry 三面持久化 修前/修后双向硬证。

用法（三阶段，各自独立跑）：
  python3 scripts/e2e_rv1b14_registry_persist.py --pre      # 修前（旧进程）：GAP 坐实
  python3 scripts/e2e_rv1b14_registry_persist.py --post     # 修后：restart 载新码再验
  python3 scripts/e2e_rv1b14_registry_persist.py --cleanup  # 清痕（精确删探针行+复核 remaining=0）

纪律（既有探针自伤坑记档）：--post 首段 restart() 先载新码；修前探针写入跑在旧进程
（内存态），restart 后必须丢=丢弃面反证；修后写入跑在新码进程，restart 后必须存活。
探针行识别=component_id 前缀 rv1b14-probe（changelog/migration 随行同前缀），行 id 落
/tmp/e2e_rv1b14_rows.json 供 --cleanup 精确删。生产 platform_version 不在探针范围
（bump 只增不回退，探针不改用户可见标量态；该面由 pytest 覆盖）。
"""

import json
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/heredity_registry.db"
STATE = "/tmp/e2e_rv1b14_rows.json"
PROBE = "rv1b14-probe-comp"  # 探针组件 id（识别+清痕锚）

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")


def api(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
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


def db_rows(table: str, where: str = "") -> list[tuple]:
    if not DB_EXISTS():
        return []
    conn = sqlite3.connect(DB)
    try:
        return conn.execute(f"SELECT * FROM {table} {where}").fetchall()
    finally:
        conn.close()


def DB_EXISTS() -> bool:
    import os

    return os.path.exists(DB)


def write_probe_state() -> dict:
    """写探针状态：API 注册组件 v1→v2（v1 转 deprecated）+ 迁移计划 + execute。"""
    st, _ = api(
        "POST",
        "/api/heredity/components",
        {
            "component_id": PROBE,
            "component_name": "Rv1b14Probe",
            "version": "1.0.0",
            "release_notes": "rv1b14 probe v1",
        },
    )
    check("POST /components v1 → 200", st == 200, f"status={st}")
    st, _ = api(
        "POST",
        "/api/heredity/components",
        {
            "component_id": PROBE,
            "component_name": "Rv1b14Probe",
            "version": "2.0.0",
            "release_notes": "rv1b14 probe v2",
        },
    )
    check("POST /components v2 → 200", st == 200, f"status={st}")
    st, mig = api(
        "POST",
        "/api/heredity/migrations",
        {
            "component_id": PROBE,
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "steps": [{"action": "probe", "description": "rv1b14 probe step"}],
        },
    )
    check("POST /migrations → 200", st == 200 and "migration_id" in mig, f"status={st}")
    mid = mig.get("migration_id", "")
    st, _ = api("POST", f"/api/heredity/migrations/{mid}/execute")
    check("POST /migrations/{id}/execute → 200", st == 200, f"status={st}")
    return {"migration_id": mid}


def visible_state(tag: str) -> None:
    """读面核验：组件版本状态/迁移 execute 状态/changelog。"""
    st, comp = api("GET", f"/api/heredity/components/{PROBE}")
    if st != 200:
        check(f"{tag} GET /components/{PROBE}", False, f"status={st}")
        return
    check(f"{tag} 组件 current=2.0.0", comp.get("current", {}).get("version") == "2.0.0",
          str(comp.get("current", {}).get("version")))
    versions = {v["version"]: v["status"] for v in comp.get("versions", [])}
    check(f"{tag} v1 deprecated/v2 active",
          versions.get("1.0.0") == "deprecated" and versions.get("2.0.0") == "active",
          str(versions))
    st, lst = api("GET", "/api/heredity/migrations")
    rows = {m["migration_id"]: m for m in lst.get("migrations", [])}
    row = rows.get(_STATE.get("migration_id", ""), {})
    check(f"{tag} 迁移 status=completed", row.get("status") == "completed", str(row.get("status")))
    st, cl = api("GET", f"/api/heredity/changelog?component_id={PROBE}")
    descs = [e["description"] for e in cl.get("changelog", [])]
    check(f"{tag} changelog 在场", any("rv1b14 probe" in d for d in descs), f"n={len(descs)}")


_STATE: dict = {}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--pre":
        print("== 修前（旧进程）：GAP 坐实 ==")
        check("data/heredity_registry.db 不存在（零落盘口）", not DB_EXISTS(), DB)
        _STATE.update(write_probe_state())
        visible_state("pre")
        json.dump(_STATE, open(STATE, "w"))
        conn = sqlite3.connect(DB) if DB_EXISTS() else None
        rows = 0
        if conn:
            try:
                rows = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='component_versions'").fetchone()[0]
            finally:
                conn.close()
        check("修前零落盘表（component_versions 表不存在）", rows == 0, f"rows={rows}")
    elif mode == "--post":
        print("== 修后：先 restart 载新码 ==")
        restart()
        # 修前探针写入是内存态：restart 后必须丢=丢弃面反证（GAP 坐实的活证）
        st, comp = api("GET", f"/api/heredity/components/{PROBE}")
        check("修前探针状态 restart 后真丢（GAP 活证）", st == 404, f"status={st}")
        print("== 修后：写入→restart→存活 ==")
        _STATE.update(write_probe_state())
        json.dump(_STATE, open(STATE, "w"))
        visible_state("post-pre-restart")
        st, stats_before = api("GET", "/api/heredity/stats")
        check("stats 计数=行真源", st == 200 and stats_before.get("registry", {}).get("total_versions", 0) >= 3,
              str(stats_before.get("registry", {})))
        restart()  # 真重启：新码回载
        visible_state("post-restart")
        st, stats = api("GET", "/api/heredity/stats")
        reg = stats.get("registry", {})
        # SEED 只补缺：两次 restart 后组件数=28 seed + 1 探针（禁 56/重复播种）
        check("SEED 不重复播种（total_components==29）", reg.get("total_components") == 29,
              f"total_components={reg.get('total_components')}")
        st, lst = api("GET", "/api/heredity/components")
        check("组件列表 29 行（28 seed+1 探针）", lst.get("total") == 29, f"total={lst.get('total')}")
    elif mode == "--cleanup":
        print("== 清痕 ==")
        _STATE.update(json.load(open(STATE)))
        conn = sqlite3.connect(DB)
        try:
            conn.execute("DELETE FROM component_versions WHERE component_id = ?", (PROBE,))
            conn.execute("DELETE FROM migration_plans WHERE component_id = ?", (PROBE,))
            conn.execute("DELETE FROM changelog_entries WHERE component_id = ?", (PROBE,))
            conn.commit()
            left = conn.execute(
                "SELECT COUNT(*) FROM component_versions WHERE component_id = ?", (PROBE,)
            ).fetchone()[0]
            left_m = conn.execute(
                "SELECT COUNT(*) FROM migration_plans WHERE component_id = ?", (PROBE,)
            ).fetchone()[0]
            left_c = conn.execute(
                "SELECT COUNT(*) FROM changelog_entries WHERE component_id = ?", (PROBE,)
            ).fetchone()[0]
        finally:
            conn.close()
        check("探针行清零 remaining=0", left + left_m + left_c == 0,
              f"components={left} migrations={left_m} changelog={left_c}")
        # 清痕走库外 SQL 删除=内存真源陈旧（list 29 vs 行 28 不对齐窗口）——
        # restart 回载收口（观察即缺陷即修：清痕与内存对齐是清痕的一部分）。
        restart()
        st, lst = api("GET", "/api/heredity/components")
        st2, stats = api("GET", "/api/heredity/stats")
        reg = stats.get("registry", {})
        check(
            "restart 后 list/stats 对齐（28/28）",
            lst.get("total") == 28 and reg.get("total_components") == 28,
            f"list={lst.get('total')} stats={reg.get('total_components')}",
        )
    else:
        print(__doc__)
        return 2

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n== {passed}/{len(RESULTS)} PASS ==")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
