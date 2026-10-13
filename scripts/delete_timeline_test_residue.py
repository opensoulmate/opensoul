#!/usr/bin/env python3
"""TL-④ 删除执行件①——timeline A 类测试残留按清单精确删（备份+回读）。

TL-③ 清点（scripts/census_test_residue.py + data/test_residue_census_20261013.json）
已留 A 类 event_id 全清单（8,612 条）。本工具按清单精确删，不发明新判法：
  - 删前逐行复核：清单里每一行必须仍被 classify_timeline（单一源）判为 A，否则拒删
    （清单陈旧/错配=拒，禁删非测试行）
  - 备份：sqlite3 backup API 全库一致快照 + sha256（删前必留，可整体回滚）
  - 回读：删后复点 total/A/B/U 与行 id 集，报告逐键落 JSON 工件
默认 dry-run；--apply 才真删。B/U 类一行不动（B_event_ids 全清单在场核对）。

范围（拆件①）：timeline A 类 8,612 行。
拆件②留待拍：knowledge 391（级联 chunks/tags）/测试用户 4+2/e2e 租户 2/
perm 决策审计 612/manifests 悬挂 5/探针组 2；perm 测试规则失效化为独立候选。

用法：
  python3 scripts/delete_timeline_test_residue.py [--apply] [--report data/xxx.json]
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
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import census_test_residue as census  # noqa: E402  单一源分类判法，禁手抄


class RefuseError(RuntimeError):
    """删前复核不过=拒删（清单陈旧/错配），一行不动。"""


def load_artifact_ids(artifact: Path) -> tuple[list[str], list[str]]:
    """从 TL-③ 清点工件取 A/B 类 event_id 全清单。"""
    data = json.loads(Path(artifact).read_text())
    tl = data["timeline"]
    return list(tl["A_event_ids"]), list(tl["B_event_ids"])


def scan(conn: sqlite3.Connection) -> dict:
    """逐行分类（单一源 census.classify_timeline），返回行 id 集合计数。"""
    rows = conn.execute(
        "SELECT event_id,organ,event_type,summary,COALESCE(detail,''),timestamp FROM events"
    ).fetchall()
    a_ids: set[str] = set()
    b_ids: set[str] = set()
    u = 0
    for eid, organ, etype, summary, detail, _ts in rows:
        cls, _rule = census.classify_timeline(organ, etype, summary, detail)
        if cls == "A":
            a_ids.add(eid)
        elif cls == "B":
            b_ids.add(eid)
        else:
            u += 1
    return {
        "total": len(rows),
        "A": len(a_ids), "B": len(b_ids), "U": u,
        "ids": {r[0] for r in rows},
        "A_ids": a_ids, "B_ids": b_ids,
    }


def backup_db(src: Path, backup_dir: Path) -> dict:
    """sqlite3 backup API 全库一致快照 + sha256。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"timeline_pre_delete_{time.strftime('%Y%m%d_%H%M%S')}.db"
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


def delete_exact(conn: sqlite3.Connection, ids: list[str]) -> int:
    """按清单精确删（单事务，只删 event_id 恰在清单里的行）。"""
    cur = conn.executemany("DELETE FROM events WHERE event_id = ?", [(i,) for i in ids])
    conn.commit()
    return cur.rowcount


def run(db_path: Path, artifact: Path, apply: bool = False,
        backup_dir: Path | None = None, report_path: Path | None = None) -> dict:
    ids_a, ids_b = load_artifact_ids(artifact)
    overlap = set(ids_a) & set(ids_b)
    if overlap:
        raise RefuseError(f"A/B 清单相交 {len(overlap)} 条，工件不可信，拒删")

    conn = sqlite3.connect(str(db_path))
    try:
        pre = scan(conn)
        # 删前逐行复核：清单里的行必须仍被判为 A（单一源），否则拒删
        present = [i for i in ids_a if i in pre["ids"]]
        missing = [i for i in ids_a if i not in pre["ids"]]
        pre_rows = {r[0]: r for r in conn.execute(
            "SELECT event_id,organ,event_type,summary,COALESCE(detail,''),timestamp FROM events")}
        bad = []
        for eid in present:
            row = pre_rows[eid]
            cls, rule = census.classify_timeline(row[1], row[2], row[3], row[4])
            if cls != "A":
                bad.append({"event_id": eid, "cls": cls, "rule": rule})
        if bad:
            raise RefuseError(f"{len(bad)} 条清单行现判非 A（清单陈旧/错配），拒删：{bad[:5]}")

        report: dict = {
            "tool": "delete_timeline_test_residue",
            "mode": "apply" if apply else "dry-run",
            "db": str(db_path), "artifact": str(artifact),
            "artifact_A": len(ids_a), "artifact_B": len(ids_b),
            "matched": len(present), "missing": len(missing),
            "pre": _counts(pre),
        }
        if not apply:
            report["deleted"] = 0
            _emit(report, report_path)
            return report

        report["backup"] = backup_db(db_path, backup_dir or (REPO / "data" / "backups"))
        deleted = delete_exact(conn, present)

        post = scan(conn)
        new_ids = post["ids"] - pre["ids"]
        expected_total = pre["total"] - deleted + len(new_ids)
        if post["total"] != expected_total:
            raise RuntimeError(
                f"回读对账不过：post.total={post['total']} != expected={expected_total}")
        # B/U 类一行不动：工件 B 清单在场行数不变（允许生产并发新写入，只查清单行）
        b_before = sum(1 for i in ids_b if i in pre["ids"])
        b_after = sum(1 for i in ids_b if i in post["ids"])
        if b_after != b_before:
            raise RuntimeError(f"B 类清单行被误删：{b_before} -> {b_after}")
        report.update({
            "deleted": deleted,
            "post": _counts(post),
            "post_A_ids_not_in_artifact": sorted(post["A_ids"] - set(ids_a)),
            "b_list_rows_before": b_before, "b_list_rows_after": b_after,
            "new_rows_during_run": len(new_ids),
        })
        _emit(report, report_path)
        return report
    finally:
        conn.close()


def _counts(scan_result: dict) -> dict:
    return {k: scan_result[k] for k in ("total", "A", "B", "U")}


def _emit(report: dict, report_path: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    print(text)
    if report_path:
        Path(report_path).write_text(text)
        print(f"[written] {report_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "data" / "timeline.db"))
    ap.add_argument("--artifact", default=str(REPO / "data" / "test_residue_census_20261013.json"))
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-dir", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report", help="把执行报告落成 JSON 工件")
    args = ap.parse_args()
    run(Path(args.db), Path(args.artifact), apply=args.apply,
        backup_dir=Path(args.backup_dir),
        report_path=Path(args.report) if args.report else None)


if __name__ == "__main__":
    main()
