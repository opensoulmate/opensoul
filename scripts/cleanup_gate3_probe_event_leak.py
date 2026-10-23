#!/usr/bin/env python3
"""GATE-③ 残留闸门破口收口：按工件差集精确删探针漏清的 timeline 事件行。

清单语义（禁发明新判法）：清单 = 残留清点工件的 A_event_ids 差集
（--post 工件有、--pre 工件无）= 上一闸门基线之后新浮出的 A 类行。
逐行删前复核：行仍在场 + classify_timeline 单一源（census_test_residue）现判仍 A
（清单陈旧/错配=拒删一行不动）。备份 = sqlite3 backup API 全库一致快照 + sha256。
回读对账 = post total == pre total - deleted、删后 A_event_ids 恰回 --pre 集合。

默认 dry-run 零写入；--apply 才真删（TL-④ 纪律）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from census_test_residue import classify_timeline  # noqa: E402  判法单一源禁手抄

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "timeline.db"
DEFAULT_PRE = ROOT / "data" / "test_residue_census_gate2_rerun_20261021.json"
DEFAULT_POST = ROOT / "data" / "test_residue_census_gate3_rerun_20261023.json"


class RefuseError(RuntimeError):
    """工件不可信/清单错配——拒删一行不动。"""


def load_a_ids(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = data.get("timeline", {}).get("A_event_ids")
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
        raise RefuseError(f"工件 timeline.A_event_ids 形态非法: {path}")
    return ids


def backup_db(db: Path, out_dir: Path) -> tuple[Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"timeline_pre_gate3_cleanup_{time.strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    return dest, sha


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--pre", default=str(DEFAULT_PRE), help="上一闸门基线工件（其 A_event_ids 不许动）")
    ap.add_argument("--post", default=str(DEFAULT_POST), help="本次闸门工件（A_event_ids 与 pre 差集=清单）")
    ap.add_argument("--backup-dir", default=str(ROOT / "data" / "backups"))
    ap.add_argument("--report", default=str(ROOT / "data" / "gate3_probe_event_leak_cleanup.json"))
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    args = ap.parse_args()

    db = Path(args.db)
    pre_ids = set(load_a_ids(Path(args.pre)))
    post_ids = load_a_ids(Path(args.post))
    checklist = [x for x in post_ids if x not in pre_ids]
    if not checklist:
        print("差集为空：无新浮出 A 行，无需清理")
        return 0

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        pre_total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        matched, missing, refused = [], [], []
        for eid in checklist:
            row = conn.execute(
                "SELECT event_id, organ, event_type, summary, detail FROM events WHERE event_id = ?",
                (eid,),
            ).fetchone()
            if row is None:
                missing.append(eid)
                continue
            verdict, rule = classify_timeline(
                row["organ"], row["event_type"], row["summary"], row["detail"] or ""
            )
            if verdict != "A":
                refused.append({"event_id": eid, "verdict": verdict, "rule": rule})
                continue
            matched.append({"event_id": eid, "rule": rule, "summary": row["summary"]})
    finally:
        conn.close()

    if refused:
        raise RefuseError(f"清单行现判非 A（工件陈旧/错配）拒删: {refused}")

    report = {
        "checklist": checklist,
        "matched": matched,
        "missing": missing,
        "pre_total": pre_total,
        "applied": bool(args.apply),
    }

    if not args.apply:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("[dry-run] 零写入。加 --apply 真删")
        return 0

    backup_path, sha = backup_db(db, Path(args.backup_dir))
    report["backup"] = {"path": str(backup_path), "sha256": sha}

    conn = sqlite3.connect(db)
    try:
        with conn:
            for item in matched:
                conn.execute("DELETE FROM events WHERE event_id = ?", (item["event_id"],))
        post_total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()

    if post_total != pre_total - len(matched):
        raise RefuseError(
            f"回读对账不符: post {post_total} != pre {pre_total} - deleted {len(matched)}"
        )

    # 复核：删后 A 集合恰回 --pre 基线（194），基线行一行不动
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        remain_a = []
        for row in conn.execute(
            "SELECT event_id, organ, event_type, summary, detail FROM events"
        ):
            verdict, _ = classify_timeline(
                row[1], row[2], row[3], row[4] or ""
            )
            if verdict == "A":
                remain_a.append(row[0])
    finally:
        conn.close()
    report["post_total"] = post_total
    report["remain_a_not_in_pre"] = sorted(set(remain_a) - pre_ids)
    report["pre_rows_missing_after"] = sorted(pre_ids - set(remain_a) - set(missing))
    report["remain_a_count"] = len(remain_a)

    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"[applied] deleted={len(matched)} report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
