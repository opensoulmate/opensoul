#!/usr/bin/env python3
"""RV-①b-⑭-2 live e2e 探针——MigrationEngine 两面（注册 schema/迁移脚本 applied 状态）
持久化 修前/修后双向硬证。

用法（三阶段，各自独立跑）：
  python3 scripts/e2e_rv1b14_2_migration_persist.py --pre      # 修前（旧进程）：GAP 坐实
  python3 scripts/e2e_rv1b14_2_migration_persist.py --post     # 修后：restart 载新码再验
  python3 scripts/e2e_rv1b14_2_migration_persist.py --cleanup  # 清痕（精确删探针行+复核 remaining=0）

纪律（既有探针自伤坑记档）：--post 首段 restart() 先载新码；修前探针写入跑在旧进程
（内存态），restart 后必须丢=丢弃面反证；修后写入跑在新码进程，restart 后必须存活。
探针行识别=component_id 前缀 rv1b142-probe（schema/script 行同前缀），行 id 落
/tmp/e2e_rv1b14_2_rows.json 供 --cleanup 精确删。
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
DB = "/home/climbing/opensoul/data/heredity_migration.db"
STATE = "/tmp/e2e_rv1b14_2_rows.json"
PROBE = "rv1b142-probe-comp"  # 探针组件 id（识别+清痕锚）

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


def db_exists() -> bool:
    return os.path.exists(DB)


def db_count(table: str, where: str = "", params: tuple = ()) -> int:
    if not db_exists():
        return 0
    conn = sqlite3.connect(DB)
    try:
        has_table = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name=?", (table,)
        ).fetchone()[0]
        if not has_table:
            return 0
        return conn.execute(f"SELECT COUNT(*) FROM {table} {where}", params).fetchone()[0]
    finally:
        conn.close()


def write_probe_state() -> dict:
    """写探针状态：注册 schema v1→v2 + 迁移脚本 + apply（applied 状态）。"""
    st, _ = api(
        "POST",
        "/api/heredity/schemas",
        {
            "component_id": PROBE,
            "version": "1.0.0",
            "fields": [{"name": "title", "field_type": "string", "required": True}],
        },
    )
    check("POST /schemas v1 → 200", st == 200, f"status={st}")
    st, _ = api(
        "POST",
        "/api/heredity/schemas",
        {
            "component_id": PROBE,
            "version": "2.0.0",
            "fields": [{"name": "title", "field_type": "number", "required": False, "default": 0}],
        },
    )
    check("POST /schemas v2 → 200", st == 200, f"status={st}")
    st, scr = api(
        "POST",
        "/api/heredity/scripts",
        {
            "migration_type": "kb_schema",
            "component_id": PROBE,
            "from_version": "1.0.0",
            "to_version": "2.0.0",
            "up_sql": "ALTER TABLE probe ADD c TEXT",
            "down_sql": "ALTER TABLE probe DROP c",
        },
    )
    check("POST /scripts → 200", st == 200 and "script_id" in scr, f"status={st}")
    sid = scr.get("script_id", "")
    st, _ = api("POST", f"/api/heredity/scripts/{sid}/apply")
    check("POST /scripts/{id}/apply → 200", st == 200, f"status={st}")
    return {"script_id": sid}


def visible_state(tag: str) -> None:
    """读面核验：schema 版本/迁移脚本 applied 状态/stats 行真源。"""
    st, sch = api("GET", f"/api/heredity/schemas/{PROBE}")
    if st != 200:
        check(f"{tag} GET /schemas/{PROBE}", False, f"status={st}")
        return
    check(f"{tag} schema latest=2.0.0", sch.get("version") == "2.0.0", str(sch.get("version")))
    field = (sch.get("fields") or [{}])[0]
    check(
        f"{tag} schema fields 保真（number/非必填/default 0）",
        field.get("field_type") == "number" and field.get("required") is False and field.get("default") == 0,
        str(field),
    )
    st, v1 = api("GET", f"/api/heredity/schemas/{PROBE}?version=1.0.0")
    check(
        f"{tag} schema v1 按版本可取（string/必填）",
        st == 200
        and v1.get("version") == "1.0.0"
        and (v1.get("fields") or [{}])[0].get("field_type") == "string"
        and (v1.get("fields") or [{}])[0].get("required") is True,
        f"status={st}",
    )
    st, lst = api("GET", "/api/heredity/scripts")
    rows = {s["script_id"]: s for s in lst.get("scripts", [])}
    row = rows.get(_STATE.get("script_id", ""), {})
    check(f"{tag} 脚本 applied=true", row.get("applied") is True, str(row.get("applied")))
    check(f"{tag} 脚本 applied_at 在场", row.get("applied_at") is not None, str(row.get("applied_at")))
    st, stats = api("GET", "/api/heredity/stats")
    mig = stats.get("migrations", {})
    check(
        f"{tag} stats 计数=行真源（applied_scripts>=1）",
        st == 200 and mig.get("applied_scripts", 0) >= 1,
        str(mig),
    )


_STATE: dict = {}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--pre":
        print("== 修前（旧进程）：GAP 坐实 ==")
        check(
            "data/heredity_migration.db 零落盘口（文件/表不存在）",
            db_count("schema_versions") == 0 and db_count("migration_scripts") == 0,
            f"db_exists={db_exists()}",
        )
        _STATE.update(write_probe_state())
        visible_state("pre")
        json.dump(_STATE, open(STATE, "w"))
        check(
            "修前零落盘（schema_versions/migration_scripts 表零行）",
            db_count("schema_versions") == 0 and db_count("migration_scripts") == 0,
            f"schema={db_count('schema_versions')} script={db_count('migration_scripts')}",
        )
    elif mode == "--post":
        print("== 修后：先 restart 载新码 ==")
        restart()
        # 修前探针写入是内存态：restart 后必须丢=丢弃面反证（GAP 坐实的活证）
        st, _ = api("GET", f"/api/heredity/schemas/{PROBE}")
        check("修前探针 schema restart 后真丢（GAP 活证）", st == 404, f"status={st}")
        st, lst = api("GET", "/api/heredity/scripts")
        check(
            "修前探针 script restart 后真丢（GAP 活证）",
            all(s.get("component_id") != PROBE for s in lst.get("scripts", [])),
            f"n={len(lst.get('scripts', []))}",
        )
        print("== 修后：写入→restart→存活 ==")
        _STATE.update(write_probe_state())
        json.dump(_STATE, open(STATE, "w"))
        visible_state("post-pre-restart")
        check(
            "行写穿（schema_versions=2/migration_scripts=1）",
            db_count("schema_versions", "WHERE component_id=?", (PROBE,)) == 2
            and db_count("migration_scripts", "WHERE component_id=?", (PROBE,)) == 1,
            f"schema={db_count('schema_versions', 'WHERE component_id=?', (PROBE,))}"
            f" script={db_count('migration_scripts', 'WHERE component_id=?', (PROBE,))}",
        )
        restart()  # 真重启：新码回载
        visible_state("post-restart")
    elif mode == "--cleanup":
        print("== 清痕 ==")
        _STATE.update(json.load(open(STATE)))
        conn = sqlite3.connect(DB)
        try:
            conn.execute("DELETE FROM schema_versions WHERE component_id = ?", (PROBE,))
            conn.execute("DELETE FROM migration_scripts WHERE component_id = ?", (PROBE,))
            conn.commit()
        finally:
            conn.close()
        left = db_count("schema_versions", "WHERE component_id=?", (PROBE,)) + db_count(
            "migration_scripts", "WHERE component_id=?", (PROBE,)
        )
        check("清痕 remaining=0（库面）", left == 0, f"remaining={left}")
        restart()  # 同步内存 ring（行删内存不删=列表面残留）
        st, sch = api("GET", f"/api/heredity/schemas/{PROBE}")
        check("清痕后 GET /schemas 404（内存面同步）", st == 404, f"status={st}")
        st, lst = api("GET", "/api/heredity/scripts")
        check(
            "清痕后 scripts 列表零探针行（内存面同步）",
            all(s.get("component_id") != PROBE for s in lst.get("scripts", [])),
            f"n={len(lst.get('scripts', []))}",
        )
        if os.path.exists(STATE):
            os.remove(STATE)
    else:
        print(__doc__)
        return 2

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n== {mode} {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
