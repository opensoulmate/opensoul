#!/usr/bin/env python3
"""TL-⑤ perm 测试规则失效化——生产权限引擎里的测试残留规则软删（is_deleted=1，可逆）。

TL-③ 清点最重要发现：105 条 test_dangerous_tool 测试残留规则仍活在生产权限引擎
（perm_rules is_deleted=0，参与每次权限判定）。本工具按清单失效化（软删=引擎不再
消费、行保留可回滚），不发明新判法：
  - 删前逐行复核：清单里每一行必须仍被 census.classify_rule（单一源）判为 A，
    否则拒（清单陈旧/错配=拒，禁动非测试规则）
  - 清单外新残留：活跃 A 类行（同单一源判 A）一并失效化，报告单列 beyond_checklist
  - 备份：sqlite3 backup API 全库一致快照 + sha256（执行前必留，可整体回滚）
  - 回读：活跃 A=0 硬闸 + 修前非 A 活跃行一律仍在（禁误伤）+ 行仍在（软删非硬删）

范围（TL-⑤）：perm_rules 测试残留规则失效化。
不在本拍：perm 决策审计 612 测试行（TL-④ 拆件② 删除面）/B 类取舍/其余六面。

用法：
  python3 scripts/deactivate_test_perm_rules.py [--apply] [--report data/xxx.json]
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
    """复核不过=拒执行（清单陈旧/错配），一行不动。"""


def load_artifact_rule_ids(artifact: Path) -> list[str]:
    """从 TL-③ 清点工件取 perm_rules A 类 rule_id 清单（保序去重）。"""
    data = json.loads(Path(artifact).read_text())
    ids = [r["rule_id"] for r in data["permission"]["rules_A"]]
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def snapshot(conn: sqlite3.Connection) -> dict:
    """逐行分类（单一源 census.classify_rule），返回计数与行集。"""
    rows = conn.execute(
        "SELECT rule_id,tool_name,source,behavior,is_deleted FROM perm_rules"
    ).fetchall()
    active_a: dict[str, str] = {}
    non_a_active: dict[tuple, int] = {}
    all_ids: set[str] = set()
    deleted_ids: set[str] = set()
    for rid, tool, source, behavior, is_deleted in rows:
        all_ids.add(rid)
        cls, _rule = census.classify_rule(tool, source)
        if is_deleted:
            deleted_ids.add(rid)
        elif cls == "A":
            active_a[rid] = tool
        else:
            key = (source, behavior)
            non_a_active[key] = non_a_active.get(key, 0) + 1
    audit_rows = conn.execute("SELECT COUNT(*) FROM perm_decision_audit").fetchone()[0]
    return {
        "total": len(rows),
        "audit_rows": audit_rows,
        "ids": all_ids,
        "deleted_ids": deleted_ids,
        "active_A_ids": active_a,
        "non_A_active": {"|".join(k): v for k, v in non_a_active.items()},
        "non_A_active_ids": {
            r[0] for r in rows
            if not r[4] and census.classify_rule(r[1], r[2])[0] != "A"
        },
    }


def backup_db(src: Path, backup_dir: Path) -> dict:
    """sqlite3 backup API 全库一致快照 + sha256。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"perm_rules_pre_deactivate_{time.strftime('%Y%m%d_%H%M%S')}.db"
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


def deactivate_exact(conn: sqlite3.Connection, ids: list[str]) -> int:
    """按清单精确软删（单事务，只动 rule_id 恰在清单里的活跃行）。"""
    cur = conn.executemany(
        "UPDATE perm_rules SET is_deleted=1 WHERE rule_id = ? AND is_deleted=0",
        [(i,) for i in ids],
    )
    conn.commit()
    return cur.rowcount


def run(db_path: Path, artifact: Path, apply: bool = False,
        backup_dir: Path | None = None, report_path: Path | None = None) -> dict:
    ids = load_artifact_rule_ids(artifact)
    conn = sqlite3.connect(str(db_path))
    try:
        pre = snapshot(conn)

        # 逐行复核：清单里的行必须存在且仍判 A（单一源），否则拒
        rows_by_id = {r[0]: r for r in conn.execute(
            "SELECT rule_id,tool_name,source,behavior,is_deleted FROM perm_rules")}
        missing = [i for i in ids if i not in rows_by_id]
        if missing:
            raise RefuseError(f"{len(missing)} 条清单行不存在（清单陈旧/错配），拒执行：{missing[:5]}")
        bad = []
        for rid in ids:
            _rid, tool, source, _behavior, _del = rows_by_id[rid]
            cls, rule = census.classify_rule(tool, source)
            if cls != "A":
                bad.append({"rule_id": rid, "cls": cls, "rule": rule})
        if bad:
            raise RefuseError(f"{len(bad)} 条清单行现判非 A（清单陈旧/错配），拒执行：{bad[:5]}")

        already_inactive = [i for i in ids if rows_by_id[i][4]]
        checklist_active = [i for i in ids if not rows_by_id[i][4]]
        # 清单外新残留（同一单一源判 A 的活跃行）一并失效化，报告单列
        beyond = sorted(set(pre["active_A_ids"]) - set(ids))
        targets = checklist_active + beyond

        report: dict = {
            "tool": "deactivate_test_perm_rules",
            "mode": "apply" if apply else "dry-run",
            "db": str(db_path), "artifact": str(artifact),
            "checklist": len(ids),
            "checklist_active": len(checklist_active),
            "already_inactive": len(already_inactive),
            "beyond_checklist": beyond,
            "targets": len(targets),
            "pre": {
                "total_rows": pre["total"],
                "audit_rows": pre["audit_rows"],
                "active_total": len(pre["active_A_ids"]) + sum(pre["non_A_active"].values()),
                "active_A": len(pre["active_A_ids"]),
                "non_A_active": pre["non_A_active"],
            },
        }
        if not apply:
            report["deactivated"] = 0
            _emit(report, report_path)
            return report

        report["backup"] = backup_db(db_path, backup_dir or (REPO / "data" / "backups"))
        deactivated = deactivate_exact(conn, targets)

        post = snapshot(conn)
        # 回读硬闸 1：活跃 A=0
        if post["active_A_ids"]:
            raise RuntimeError(
                f"回读对账不过：仍有 {len(post['active_A_ids'])} 条活跃 A 规则")
        # 回读硬闸 2：修前非 A 活跃行一律仍在（允许生产并发新增，禁误伤既有行）
        lost = pre["non_A_active_ids"] - post["non_A_active_ids"]
        if lost:
            raise RuntimeError(f"非 A 活跃规则被误伤 {len(lost)} 条：{sorted(lost)[:5]}")
        # 回读硬闸 3：软删非硬删——目标行与全部行仍在表内
        still_present = sum(1 for i in targets if i in post["ids"])
        if still_present != len(targets):
            raise RuntimeError(
                f"软删语义被破坏：目标行 {len(targets)} 条仅 {still_present} 条仍在表内")
        report.update({
            "deactivated": deactivated,
            "post": {
                "total_rows": post["total"],
                "audit_rows": post["audit_rows"],
                "active_total": sum(post["non_A_active"].values()),
                "active_A": len(post["active_A_ids"]),
                "non_A_active": post["non_A_active"],
            },
            "readback": {
                "active_A_after": len(post["active_A_ids"]),
                "non_A_pre_rows_still_active": len(pre["non_A_active_ids"]) - len(lost),
                "target_rows_still_present": still_present,
            },
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
    ap.add_argument("--db", default=str(
        Path.home() / ".hermes" / "opensoul" / "immune" / "permission.db"))
    ap.add_argument("--artifact", default=str(REPO / "data" / "test_residue_census_20261013.json"))
    ap.add_argument("--apply", action="store_true", help="真失效化（缺省 dry-run 零写入）")
    ap.add_argument("--backup-dir", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report", help="把执行报告落成 JSON 工件")
    args = ap.parse_args()
    run(Path(args.db), Path(args.artifact), apply=args.apply,
        backup_dir=Path(args.backup_dir),
        report_path=Path(args.report) if args.report else None)


if __name__ == "__main__":
    main()
