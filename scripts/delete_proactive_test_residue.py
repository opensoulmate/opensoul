#!/usr/bin/env python3
"""PROACT-④ 删除执行件——proactive.db 测试噪音按清单精确删（备份+回读）。

PROACT-② 清点（scripts/census_test_residue.py census_proactive +
data/test_residue_census_20261014_post_proact2.json）已给出处置建议：
删 A（同秒同 watcher 重复=确证测试残留）+ orphan 层 B/U（非服务可归因=测试 burst
形态），留 near_start/cadence 层 B/U（生产可归因）。本工具按该建议执行，不发明新判法：
  - 清单=工件 A_event_ids + B_orphan_ids + U_orphan_ids（缺键=工件不可信拒删）
  - 删前逐行复核：清单里每一行必须仍被 census 单一源判为同类同层（A 任意层 /
    B·U 须仍 orphan 层），否则拒删（清单陈旧/错配=整轮拒，一行不动）
  - 证据层有效性闸：工件 facts.orphan_reliable 与运行时 journal 可用性双查——
    orphan 清单依赖 journal 证据层，journal 不可用时 orphan 判定不可信禁删
  - 备份：sqlite3 backup API 全库一致快照 + sha256（删前必留，可整体回滚）
  - 回读：删后复点 total/A/B/U 与行 id 集，keep 清单行在场数不变，报告落 JSON 工件
默认 dry-run；--apply 才真删。清单外新残留只单列报告不删（生产服务持续写入，
宁窄勿滥——TL-④ 先例「零清单外补删」）。

用法：
  python3 scripts/delete_proactive_test_residue.py [--apply] [--report data/xxx.json]
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
import census_test_residue as census  # noqa: E402  单一源分类/证据层判法，禁手抄

DELETE_KEYS = ("A_event_ids", "B_orphan_ids", "U_orphan_ids")
KEEP_KEYS = ("B_near_start_ids", "B_cadence_ids", "U_near_start_ids", "U_cadence_ids")
# 清单键 -> 现判期望 (cls, layer)；A 任意层可删（机制硬判据），B/U 须仍 orphan 层
EXPECTED = {
    "A_event_ids": ("A", None),
    "B_orphan_ids": ("B", "orphan"),
    "U_orphan_ids": ("U", "orphan"),
}


class RefuseError(RuntimeError):
    """删前复核不过=拒删（清单陈旧/错配/工件不可信），一行不动。"""


def load_artifact(artifact: Path) -> dict:
    """从 PROACT-② 清点工件取删除/保留清单与 orphan 可靠性。"""
    data = json.loads(Path(artifact).read_text())
    p = data["proactive"]
    for key in DELETE_KEYS + KEEP_KEYS:
        if key not in p:
            raise RefuseError(f"工件缺 proactive.{key} 键，工件不可信，拒删")
    facts = p.get("facts") or {}
    if not facts.get("orphan_reliable"):
        raise RefuseError(
            "工件 facts.orphan_reliable=False（清点时 journal 不可用），"
            "orphan 清单不可信，拒删")
    delete_ids = {k: list(p[k]) for k in DELETE_KEYS}
    keep_ids = {k: list(p[k]) for k in KEEP_KEYS}
    # 清单互斥闸（六清单两两不相交：删除清单间、删除 vs 保留）
    seen = {}
    for k in DELETE_KEYS + KEEP_KEYS:
        src = delete_ids[k] if k in delete_ids else keep_ids[k]
        for i in src:
            if i in seen and seen[i] != k:
                raise RefuseError(
                    f"清单键 {seen[i]} 与 {k} 相交（{i}），工件不可信，拒删")
            seen[i] = k
    return {"delete": delete_ids, "keep": keep_ids,
            "orphan_reliable": bool(facts.get("orphan_reliable"))}


def scan(conn: sqlite3.Connection, starts) -> dict:
    """逐行 (分类, 规则, 证据层)（单一源 census.proactive_row_verdicts）。"""
    rows = conn.execute(
        "SELECT id, watcher, started_at FROM proactive_events ORDER BY started_at, id"
    ).fetchall()
    verdicts, excess = census.proactive_row_verdicts(rows, starts)
    counts = {"A": 0, "B": 0, "U": 0}
    layers = {"near_start": 0, "cadence": 0, "orphan": 0}
    for _rid, (_cls, _rule, layer) in verdicts.items():
        counts[_cls] += 1
        layers[layer] += 1
    return {"total": len(rows), "counts": counts, "layers": layers,
            "verdicts": verdicts, "ids": {r[0] for r in rows}, "excess": excess}


def backup_db(src: Path, backup_dir: Path) -> dict:
    """sqlite3 backup API 全库一致快照 + sha256。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"proactive_pre_delete_{time.strftime('%Y%m%d_%H%M%S')}.db"
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


def delete_exact(conn: sqlite3.Connection, ids: list) -> int:
    """按清单精确删（单事务，只删 id 恰在清单里的行）。"""
    cur = conn.executemany("DELETE FROM proactive_events WHERE id = ?",
                           [(i,) for i in ids])
    conn.commit()
    return cur.rowcount


def _is_target(rid, verdicts, checklist_ids) -> bool:
    """同判据删除目标：A（任意层）或 B/U orphan 层。"""
    cls, _rule, layer = verdicts[rid]
    return cls == "A" or (cls in ("B", "U") and layer == "orphan")


def run(db_path: Path, artifact: Path, apply: bool = False,
        backup_dir: Path | None = None, report_path: Path | None = None) -> dict:
    spec = load_artifact(artifact)
    starts = census.load_journal_starts()
    if starts is None:
        raise RefuseError(
            "运行时 journal 不可用（load_journal_starts=None），orphan 证据层不可复算，拒删")

    conn = sqlite3.connect(str(db_path))
    try:
        pre = scan(conn, starts)
        # ---- 删前逐行复核：清单行必须仍同类同层（单一源），否则整轮拒删 ----
        checklist = {k: spec["delete"][k] for k in DELETE_KEYS}
        missing = {k: [] for k in DELETE_KEYS}
        bad = []
        target_ids = []
        for key in DELETE_KEYS:
            want_cls, want_layer = EXPECTED[key]
            for rid in checklist[key]:
                if rid not in pre["ids"]:
                    missing[key].append(rid)
                    continue
                cls, rule, layer = pre["verdicts"][rid]
                if cls != want_cls or (want_layer is not None and layer != want_layer):
                    bad.append({"id": rid, "want": key,
                                "got": {"cls": cls, "rule": rule, "layer": layer}})
                else:
                    target_ids.append(rid)
        if bad:
            raise RefuseError(
                f"{len(bad)} 条清单行现判错类错层（清单陈旧/错配），整轮拒删：{bad[:5]}")

        keep_ids = [i for k in KEEP_KEYS for i in spec["keep"][k]]
        keep_before = sum(1 for i in keep_ids if i in pre["ids"])
        # 清单外同判据新残留：只单列报告不删（生产服务持续写入，宁窄勿滥 TL-④ 先例）
        checklist_all = set(target_ids)
        for k in DELETE_KEYS:
            checklist_all.update(checklist[k])
        beyond = sorted(rid for rid in pre["ids"] - checklist_all
                        if _is_target(rid, pre["verdicts"], checklist_all))

        report = {
            "tool": "delete_proactive_test_residue",
            "mode": "apply" if apply else "dry-run",
            "db": str(db_path), "artifact": str(artifact),
            "artifact_delete": {k: len(checklist[k]) for k in DELETE_KEYS},
            "artifact_keep": {k: len(spec["keep"][k]) for k in KEEP_KEYS},
            "matched": len(target_ids),
            "missing": {k: len(v) for k, v in missing.items()},
            "missing_ids": missing,
            "pre": {"total": pre["total"], **pre["counts"], **pre["layers"]},
            "keep_rows_before": keep_before,
            "beyond_checklist": beyond[:50],
            "beyond_checklist_count": len(beyond),
        }
        if not apply:
            report["deleted"] = 0
            _emit(report, report_path)
            return report

        report["backup"] = backup_db(db_path, backup_dir or (REPO / "data" / "backups"))
        deleted = delete_exact(conn, target_ids)

        post = scan(conn, starts)
        new_ids = post["ids"] - pre["ids"]
        expected_total = pre["total"] - deleted + len(new_ids)
        if post["total"] != expected_total:
            raise RuntimeError(
                f"回读对账不过：post.total={post['total']} != expected={expected_total}")
        gone = [i for i in target_ids if i in post["ids"]]
        if gone:
            raise RuntimeError(f"清单目标残留 {len(gone)} 条未删净：{gone[:5]}")
        keep_after = sum(1 for i in keep_ids if i in post["ids"])
        if keep_after != keep_before:
            raise RuntimeError(f"保留清单行被误删：{keep_before} -> {keep_after}")
        report.update({
            "deleted": deleted,
            "post": {"total": post["total"], **post["counts"], **post["layers"]},
            "keep_rows_after": keep_after,
            "new_rows_during_run": len(new_ids),
        })
        _emit(report, report_path)
        return report
    finally:
        conn.close()


def _emit(report: dict, report_path: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    print(text)
    if report_path:
        Path(report_path).write_text(text)
        print(f"[written] {report_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "data" / "proactive.db"))
    ap.add_argument("--artifact",
                    default=str(REPO / "data" / "test_residue_census_20261014_post_proact2.json"))
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-dir", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report", help="把执行报告落成 JSON 工件")
    args = ap.parse_args()
    run(Path(args.db), Path(args.artifact), apply=args.apply,
        backup_dir=Path(args.backup_dir),
        report_path=Path(args.report) if args.report else None)


if __name__ == "__main__":
    main()
