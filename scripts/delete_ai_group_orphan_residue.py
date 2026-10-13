#!/usr/bin/env python3
"""TL-⑫ 删除执行件——ai_groups 悬挂残留行（组行已不在场的子表行）按清单精确删
+ 备份 + 回读。

TL-⑪ 诚实边界（勘误面）定性：ai_groups.db 悬挂行 756（37 消息+694 事件+12 评分
+13 能力，含 test-group-123/test-broadcast-*/a1proof 硬标识、8 个已删探针组 id、
2 行 e2e 租户消息）不在清单且无组行可判，只报数不删——本工具收该留档候选面。
本工具按单一源判法精确删（TL-④~⑪ 同款纪律）：
  - 判单一源：census.classify_orphan_group + census.orphan_group_texts
    （禁发明新判法/禁第二套扫描；词表=组 id 硬标识+探针字面+A_GOAL_MARKERS
    既有 A 类硬标识，泛化短语刻意不收防误杀真用户=宁窄勿滥）
  - 组级判定（任一行文本命中即整组 A）＝TL-③「随组处置比逐行删稳」语义
  - 删前逐组复核（fresh 重读再判），现判非 A=拒删一行不动（清单陈旧/错配=拒）；
    组行已回场（不再是悬挂行）=拒删（禁动活组的子表行）
  - 清单=工件 sqlite.ai_groups.orphan_groups_A（工件缺键=拒删）；清单缺组=如实报
    非致命（幂等重跑）；清单外新残留（现判 A 但不在清单）同判据一并删、单列报告
  - 精确删=按扫描到的行 id 逐行删（禁按 group_id 扫删——同组并发新增行不在清单，
    如实留）；他组/活组行一律不动
  - 备份：sqlite3 backup API 全库一致快照 + sha256（删前必留，可整体回滚）
  - 回读：删后复点（待删行全 gone、非目标行一行不动、总数算术对平、允许生产
    并发新增行，只查删前在场行），报告逐键落 JSON 工件
默认 dry-run；--apply 才真删。

范围（TL-⑫）：ai_groups.db 悬挂残留行 6 表扫描面（现存 4 表有行）。
诚实边界：判法是组 id/行文本硬标识非语义理解（改写探针内容如实不猜）；无硬标识
悬挂行如实不动（other 报数）；同组并发新增行不在清单不删（如实报）；B/U 类与
活组（组行在场）行一行不动。

用法：
  python3 scripts/delete_ai_group_orphan_residue.py [--apply] [--report data/xxx.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
for _p in (str(SCRIPTS), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import census_test_residue as census  # noqa: E402  单一源分类判法，禁手抄

DB_PATH = REPO / "data" / "ai_groups.db"
SCAN_TABLES = census.ORPHAN_SCAN_TABLES


class RefuseError(RuntimeError):
    """删前复核不过 / 工件不可信 / 清单错配 = 拒删，一行不动。"""


def load_artifact_orphans(artifact: Path) -> dict:
    """从清点工件取悬挂残留 A 组清单（工件不可信=拒删）。

    清单=sqlite.ai_groups.orphan_groups_A 的 group_id（单一源产出，禁手抄）；
    工件缺键=拒删（老工件无此面=不可信，禁猜清单）。
    """
    data = json.loads(Path(artifact).read_text())
    try:
        entries = data["sqlite"]["ai_groups"]["orphan_groups_A"]
        rows = [{"group_id": r["group_id"], "artifact_rule": r.get("rule", "")}
                for r in entries]
    except (KeyError, TypeError) as e:
        raise RefuseError(
            f"工件缺 sqlite.ai_groups.orphan_groups_A 键，工件不可信，拒删：{e}") from e
    return {"checklist": rows}


def scan(conn: sqlite3.Connection) -> dict:
    """逐组分类（单一源 census.classify_orphan_group + orphan_group_texts）+
    行 id 归集（删除与回读用）。"""
    texts = census.orphan_group_texts(conn)  # 单一源取数口，禁第二套扫描
    ids_by_group: dict = {}
    for table in SCAN_TABLES:
        cur = conn.execute(
            f"SELECT id, group_id FROM {table}"
            " WHERE group_id NOT IN (SELECT id FROM ai_groups)")
        for rid, gid in cur:
            ids_by_group.setdefault(gid, {}).setdefault(table, []).append(rid)
    a_rows: list = []
    other_rows: list = []
    for gid in sorted(texts, key=str):
        cls, rule = census.classify_orphan_group(gid, texts[gid])  # 单一源，禁手抄
        entry = {
            "group_id": gid, "rule": rule, "cls": cls,
            "rows_by_table": {t: list(v) for t, v in ids_by_group.get(gid, {}).items()},
            "rows": sum(len(v) for v in ids_by_group.get(gid, {}).values()),
        }
        (a_rows if cls == "A" else other_rows).append(entry)
    return {"A_rows": a_rows, "other_rows": other_rows,
            "total_groups": len(texts), "ids_by_group": ids_by_group}


def checklist_gates(art: dict, pre: dict) -> dict:
    """清单闸：清单组仍悬挂且现判 A（词表漂移/组行回场=拒删）；缺组/清单外分流。"""
    live_by_id = {r["group_id"]: r for r in pre["A_rows"]}
    other_ids = {r["group_id"] for r in pre["other_rows"]}
    art_ids = [r["group_id"] for r in art["checklist"]]
    drifted = [{"group_id": g, "reason": "现判非 A（悬挂行文本/词表漂移）"}
               for g in art_ids if g in other_ids]
    if drifted:
        raise RefuseError(
            f"清单组词表漂移（工件与单一源判法不一致），拒删：{drifted[:5]}")
    missing = sorted(str(g) for g in art_ids if g not in live_by_id and g not in other_ids)
    beyond = [r for r in pre["A_rows"] if r["group_id"] not in set(art_ids)]
    count_drift = {"artifact": len(art_ids),
                   "live_matched": len([g for g in art_ids if g in live_by_id])}
    return {"missing_checklist_groups": missing, "beyond_checklist": beyond,
            "count_drift": count_drift}


def recheck(conn: sqlite3.Connection, targets: list) -> dict:
    """删前逐组复核：fresh 重读再判（单一源），现判非 A=拒删；
    组行已回场（非悬挂）=拒删（禁动活组子表行）；清单行缺行如实报非致命。"""
    bad = []
    back_alive = []
    texts = census.orphan_group_texts(conn)  # fresh 重读
    missing_rows: list = []
    for t in targets:
        gid = t["group_id"]
        live_group = conn.execute(
            "SELECT 1 FROM ai_groups WHERE id=?", (gid,)).fetchone()
        if live_group is not None:
            back_alive.append(gid)
            continue
        ts = texts.get(gid)
        if ts is None:
            continue  # 行已全不在场=幂等重跑缺行，非致命
        cls, rule = census.classify_orphan_group(gid, ts)
        if cls != "A":
            bad.append({"group_id": gid, "cls": cls, "rule": rule})
        fresh_ids = set()
        for table, row_ids in t["rows_by_table"].items():
            fresh_ids = {r[0] for r in conn.execute(
                f"SELECT id FROM {table} WHERE group_id=?", (gid,))}
            missing_rows.extend(
                {"table": table, "id": rid} for rid in row_ids if rid not in fresh_ids)
    if bad:
        raise RefuseError(
            f"{len(bad)} 条待删悬挂组复核不过（现判非 A，清单陈旧/错配），拒删：{bad[:5]}")
    if back_alive:
        raise RefuseError(
            f"待删悬挂组行已回场（组行在场=非悬挂行），拒删禁动活组子表行：{back_alive[:5]}")
    return {"recheck_missing_rows": missing_rows}


def row_index(conn: sqlite3.Connection, table: str) -> dict:
    """全表行 id -> group_id 索引（回读对账用；含活组行=非目标行面）。"""
    return {rid: gid for rid, gid in conn.execute(
        f"SELECT id, group_id FROM {table}")}


def backup_db(src: Path, backup_dir: Path, tag: str) -> dict:
    """sqlite3 backup API 全库一致快照 + sha256。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"{tag}_pre_delete_{time.strftime('%Y%m%d_%H%M%S')}.db"
    src_conn = sqlite3.connect(str(src))
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    return {"path": str(dest), "sha256": digest, "bytes": dest.stat().st_size}


def delete_exact(conn: sqlite3.Connection, targets: list) -> dict:
    """按扫描到的行 id 逐行精确删（单事务，禁按 group_id 扫删——同组并发新增行
    不在清单如实留）。"""
    deleted: dict = {}
    for table in SCAN_TABLES:
        n = 0
        for t in targets:
            for rid in t["rows_by_table"].get(table, []):
                n += conn.execute(
                    f"DELETE FROM {table} WHERE id=?", (rid,)).rowcount
        deleted[table] = n
    conn.commit()
    return deleted


def _emit(report: dict, report_path: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    print(text)
    if report_path:
        Path(report_path).write_text(text)
        print(f"[written] {report_path}")


def run(artifact: Path, apply: bool = False, backup_dir: Path | None = None,
        report_path: Path | None = None, db_override: Path | None = None) -> dict:
    art = load_artifact_orphans(artifact)
    db_path = Path(db_override or DB_PATH)
    report: dict = {
        "tool": "delete_ai_group_orphan_residue",
        "mode": "apply" if apply else "dry-run",
        "artifact": str(artifact),
        "db": str(db_path),
        "artifact_checklist": art["checklist"],
        "artifact_checklist_n": len(art["checklist"]),
    }
    conn = sqlite3.connect(str(db_path))
    try:
        pre = scan(conn)
        gates = checklist_gates(art, pre)
        # 清单行 + 清单外同判据新残留（单列报告）一并删
        targets = pre["A_rows"]
        report.update({
            "live_A_groups": len(targets),
            "matched": len(targets),
            "checklist": gates,
            "deleted_by_rule": _by_rule(targets),
            "pre_orphan_groups": {
                "total": pre["total_groups"], "A": len(targets),
                "other": len(pre["other_rows"])},
            "deleted_group_ids": [t["group_id"] for t in targets],
            "pre_rows_by_group": {t["group_id"]: t["rows"] for t in targets},
        })
        report.update(recheck(conn, targets))

        pre_index = {table: row_index(conn, table) for table in SCAN_TABLES}
        pre_totals = {table: len(idx) for table, idx in pre_index.items()}
        report["pre_totals"] = pre_totals

        if not apply:
            report["deleted"] = {t: 0 for t in SCAN_TABLES}
            report["deleted"]["total"] = 0
            _emit(report, report_path)
            return report

        report["backup"] = backup_db(db_path, backup_dir or (REPO / "data" / "backups"),
                                     "ai_groups_orphan")
        deleted = delete_exact(conn, targets)
        deleted_total = sum(deleted.values())
        report["deleted"] = {**deleted, "total": deleted_total}

        post_index = {table: row_index(conn, table) for table in SCAN_TABLES}
        target_ids = {table: {rid for t in targets
                              for rid in t["rows_by_table"].get(table, [])}
                      for table in SCAN_TABLES}
        for table in SCAN_TABLES:
            still = target_ids[table] & set(post_index[table])
            if still:
                raise RuntimeError(
                    f"回读对账不过（{table}）：待删行仍在场 {sorted(map(str, still))[:5]}")
            pre_ids = set(pre_index[table])
            missing_other = pre_ids - set(post_index[table]) - target_ids[table]
            if missing_other:
                raise RuntimeError(
                    f"非目标行被误删（{table}）：{sorted(map(str, missing_other))[:5]}")
            new_rows = set(post_index[table]) - pre_ids
            post_total = len(post_index[table])
            report.setdefault("post_totals", {})[table] = {
                "post": post_total,
                "concurrent_new_rows": len(new_rows),
                "total_reconcile": {
                    "pre": pre_totals[table], "deleted": deleted[table],
                    "new": len(new_rows), "post": post_total,
                    "ok": post_total == pre_totals[table] - deleted[table]
                    + len(new_rows),
                },
            }
        post = scan(conn)
        report["post_orphan_groups"] = {
            "total": post["total_groups"], "A": len(post["A_rows"]),
            "other": [r["group_id"] for r in post["other_rows"]],
        }
        _emit(report, report_path)
        return report
    finally:
        conn.close()


def _by_rule(rows: list) -> dict:
    out: dict = {}
    for r in rows:
        out[r["rule"]] = out.get(r["rule"], 0) + 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact",
                    default=str(REPO / "data" / "test_residue_census_20261014_post_tl10.json"))
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-dir", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report", help="把执行报告落成 JSON 工件")
    ap.add_argument("--db", help="ai_groups.db 路径覆盖（测试隔离用）")
    args = ap.parse_args()
    run(Path(args.artifact), apply=args.apply,
        backup_dir=Path(args.backup_dir),
        report_path=Path(args.report) if args.report else None,
        db_override=Path(args.db) if args.db else None)


if __name__ == "__main__":
    main()
