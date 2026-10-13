#!/usr/bin/env python3
"""TL-⑰ B 类可删三组按清单精确删（备份+回读）——storm 组保留。

TL-⑯（scripts/census_test_residue.py + data/test_residue_census_20261014_post_tl16.json）
已按内容级+出处级清点给出四组处置建议：storm 22,030=OpenImmune 开发验证流量（真实
安全日志史）建议保留；exec 666/pipeline 421/brain 11 三组逐字钉测试夹具=建议可删。
本工具执行三组删除，storm 一行不动：

  - 删前逐行复核：清单里每一行必须仍被 classify_timeline（单一源）判为 B，且 rule
    属于删除三组，否则拒删（清单陈旧/错配=拒，一行不动）；storm 组清单行恒保留
  - 备份：sqlite3 backup API 全库一致快照 + sha256（删前必留，可整体回滚）
  - 回读：删后复点 total 与行 id 集，storm/A/U 与非目标行一行不动，报告逐键落 JSON
  - 清单外同判据新残留只报告不删（TL-④ timeline 面先例：只删清单内）
默认 dry-run；--apply 才真删。

清单=TL-⑯ 工件 timeline.B_event_ids ∩ fresh 判定删除三组的行。
删除依据=TL-⑯ 出处级证据（命令/文件名/文案逐字钉 tests/ 夹具），非新判法。

用法：
  python3 scripts/delete_timeline_b_residue.py [--apply] [--report data/xxx.json]
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
import census_test_residue as census  # noqa: E402  单一源分类判法+rule 名，禁手抄

# 删除三组（TL-⑯ 建议可删）；storm 保留（TL-⑯ 建议保留：真实安全日志史）
DELETE_RULES = {census.B_RULE_EXEC, census.B_RULE_PIPELINE, census.B_RULE_BRAIN}
KEEP_RULES = {census.B_RULE_STORM}


class RefuseError(RuntimeError):
    """删前复核不过=拒删（清单陈旧/错配/工件不可信），一行不动。"""


def load_artifact_ids(artifact: Path) -> tuple[list[str], list[str]]:
    """从 TL-⑯ 清点工件取 A/B 类 event_id 全清单（缺键=工件不可信拒删）。"""
    data = json.loads(Path(artifact).read_text())
    tl = data.get("timeline")
    if not isinstance(tl, dict):
        raise RefuseError("工件缺 timeline 段，不可信，拒删")
    if "A_event_ids" not in tl or "B_event_ids" not in tl:
        raise RefuseError("工件缺 A_event_ids/B_event_ids 键，不可信，拒删")
    return list(tl["A_event_ids"]), list(tl["B_event_ids"])


def scan(conn: sqlite3.Connection) -> dict:
    """逐行分类（单一源 census.classify_timeline），B 行按 rule 分组。"""
    rows = conn.execute(
        "SELECT event_id,organ,event_type,summary,COALESCE(detail,''),timestamp FROM events"
    ).fetchall()
    a_ids: set[str] = set()
    u = 0
    b_by_rule: dict[str, set[str]] = {}
    for eid, organ, etype, summary, detail, _ts in rows:
        cls, rule = census.classify_timeline(organ, etype, summary, detail)
        if cls == "A":
            a_ids.add(eid)
        elif cls == "B":
            b_by_rule.setdefault(rule, set()).add(eid)
        else:
            u += 1
    b_ids = set().union(*b_by_rule.values()) if b_by_rule else set()
    return {
        "total": len(rows),
        "A": len(a_ids), "B": len(b_ids), "U": u,
        "ids": {r[0] for r in rows},
        "A_ids": a_ids,
        "B_ids": b_ids,
        "B_by_rule": {k: len(v) for k, v in sorted(b_by_rule.items())},
        "rule_ids": b_by_rule,
    }


def backup_db(src: Path, backup_dir: Path) -> dict:
    """sqlite3 backup API 全库一致快照 + sha256。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"timeline_b_pre_delete_{time.strftime('%Y%m%d_%H%M%S')}.db"
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
    if (set(DELETE_RULES) | KEEP_RULES) != set(census.B_RULES_ALL):
        raise RefuseError("删除/保留分组未覆盖 B_RULES_ALL 全集，拒删")

    conn = sqlite3.connect(str(db_path))
    try:
        pre = scan(conn)
        # 删前逐行复核：清单里的 B 行必须仍判 B（单一源），否则=清单陈旧/错配拒删
        present = [i for i in ids_b if i in pre["ids"]]
        missing = [i for i in ids_b if i not in pre["ids"]]
        pre_rows = {r[0]: r for r in conn.execute(
            "SELECT event_id,organ,event_type,summary,COALESCE(detail,''),timestamp FROM events")}
        bad = []
        rule_of: dict[str, str] = {}
        for eid in present:
            row = pre_rows[eid]
            cls, rule = census.classify_timeline(row[1], row[2], row[3], row[4])
            if cls != "B":
                bad.append({"event_id": eid, "cls": cls, "rule": rule})
            else:
                rule_of[eid] = rule
        if bad:
            raise RefuseError(f"{len(bad)} 条清单行现判非 B（清单陈旧/错配），拒删：{bad[:5]}")

        targets = [i for i in present if rule_of[i] in DELETE_RULES]
        kept_storm = [i for i in present if rule_of[i] in KEEP_RULES]
        # 清单外同判据新残留：只报告不删（TL-④ timeline 面先例：只删清单内）
        beyond = sorted(
            eid for rule in DELETE_RULES
            for eid in pre["rule_ids"].get(rule, set()) - set(ids_b))

        report: dict = {
            "tool": "delete_timeline_b_residue",
            "mode": "apply" if apply else "dry-run",
            "db": str(db_path), "artifact": str(artifact),
            "artifact_A": len(ids_a), "artifact_B": len(ids_b),
            "delete_rules": sorted(DELETE_RULES), "keep_rules": sorted(KEEP_RULES),
            "matched": len(present), "missing": len(missing),
            "targets": len(targets), "kept_storm": len(kept_storm),
            "pre_B_by_rule": pre["B_by_rule"],
            "beyond_checklist": beyond,
            "pre": _counts(pre),
        }
        if not apply:
            report["deleted"] = 0
            _emit(report, report_path)
            return report

        report["backup"] = backup_db(db_path, backup_dir or (REPO / "data" / "backups"))
        deleted = delete_exact(conn, targets)

        post = scan(conn)
        new_ids = post["ids"] - pre["ids"]
        expected_total = pre["total"] - deleted + len(new_ids)
        if post["total"] != expected_total:
            raise RuntimeError(
                f"回读对账不过：post.total={post['total']} != expected={expected_total}")
        if deleted != len(targets):
            raise RuntimeError(f"删除数不符：deleted={deleted} != targets={len(targets)}")
        # 保留组一行不动：storm 清单行在场数不变
        storm_before = sum(1 for i in kept_storm)
        storm_after = sum(1 for i in kept_storm if i in post["ids"])
        if storm_after != storm_before:
            raise RuntimeError(f"storm 保留组被误删：{storm_before} -> {storm_after}")
        # 非目标行一行不动（清单 B 行保留组+A/U 全部行）
        non_target = (pre["ids"] - set(targets))
        lost = sorted(non_target - post["ids"])
        if lost:
            raise RuntimeError(f"非目标行被误删 {len(lost)} 条：{lost[:5]}")
        # 删除三组在清单集合内清零
        remain_targets = [i for i in targets if i in post["ids"]]
        if remain_targets:
            raise RuntimeError(f"清单目标残留 {len(remain_targets)} 条：{remain_targets[:5]}")
        report.update({
            "deleted": deleted,
            "post": _counts(post),
            "post_B_by_rule": post["B_by_rule"],
            "storm_rows_before": storm_before, "storm_rows_after": storm_after,
            "non_target_rows_before": len(non_target),
            "post_B_ids_not_in_artifact": sorted(post["B_ids"] - set(ids_b)),
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
    ap.add_argument("--artifact", default=str(REPO / "data" / "test_residue_census_20261014_post_tl16.json"))
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-dir", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report", help="把执行报告落成 JSON 工件")
    args = ap.parse_args()
    run(Path(args.db), Path(args.artifact), apply=args.apply,
        backup_dir=Path(args.backup_dir),
        report_path=Path(args.report) if args.report else None)


if __name__ == "__main__":
    main()
