#!/usr/bin/env python3
"""TL-⑪ 删除执行件②b-2b-2b——探针组 2（ai_groups.db A群组/B群组）按清单精确删
+ delete_group 级联 + 备份 + 回读。

TL-③ 清点（scripts/census_test_residue.py + data/test_residue_census_20261013.json）
定性：ai_groups.db 组 2（A群组/B群组=探针组，2026-08-29 e2e 探针创建），
任务 A 10/B 16/U 40 随组处置（TL-③ 诚实边界③"随组处置比逐行删稳"）。
本工具按单一源判法精确删（TL-④~⑩ 同款纪律）：
  - 判单一源：census.classify_group（禁发明新判法；词表=A_GROUP_NAMES
    探针组名硬标识，与 TL-③ 清点"探针组 A群组/B群组"判据逐字一致）
  - 删前逐行复核（fresh 重读再判），现判非 A=拒删一行不动（清单陈旧/错配=拒，
    禁删真实组行）；组名与工件不一致（错配）=拒删
  - 清单=工件 sqlite.ai_groups.groups 中现判 A 的条目（工件缺 groups 键=拒删）；
    工件里现判非 A 的条目如实排除不动；清单缺行=如实报非致命（幂等重跑）；
    清单外新残留（现判 A 但不在清单）同判据一并删、单列报告
  - 级联=src/api/ai_groups.py delete_group 同款语义（F3b-③b-3）：6 子表
    DELETE WHERE group_id=? AND tenant_id=组行租户，组行 DELETE WHERE id+tenant
    ——他租户行一律不动（禁跨租户破坏优先于不留孤儿，外来行只报数不删）
  - 备份：sqlite3 backup API 全库一致快照 + sha256（删前必留，可整体回滚）
  - 回读：删后复点（待删行全 gone、非目标行一行不动、总数算术对平、允许生产
    并发新增行，只查删前在场行），报告逐键落 JSON 工件
  - 悬挂行（组行已不在场的子表行）与跨组 task_id 引用只报数不删——不在清单
    且无组行可判（禁猜），留作下一切片候选
默认 dry-run；--apply 才真删。

范围（拆件②b-2b-2b）：ai_groups.db 探针组 2 + delete_group 级联。
诚实边界：判法是组名硬标识非语义理解（改名探针组如实不猜）；悬挂子表行
（组行已删的历史残留）不在本拍（只报数）；B/U 类组一行不动。

用法：
  python3 scripts/delete_ai_group_probe_residue.py [--apply] [--report data/xxx.json]
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

# 与 src/api/ai_groups.py delete_group 级联清单同集（F3b-③b-3）：6 子表+组行
CHILD_TABLES = (
    "ai_group_tasks",
    "discussion_messages",
    "task_execution_events",
    "agent_scores",
    "agent_capabilities",
    "ai_group_agents",
)
TASK_REF_TABLES = ("discussion_messages", "task_execution_events", "agent_scores")


class RefuseError(RuntimeError):
    """删前复核不过 / 工件不可信 / 清单错配 = 拒删，一行不动。"""


def load_artifact_groups(artifact: Path) -> dict:
    """从 TL-③ 清点工件取探针组清单（工件不可信=拒删）。

    清单=sqlite.ai_groups.groups 中现判 A 的条目（单一源，禁手抄）；
    工件里现判非 A 的条目如实排除不动（artifact_excluded）。
    """
    data = json.loads(Path(artifact).read_text())
    try:
        entries = data["sqlite"]["ai_groups"]["groups"]
        rows = [{"group_id": r["group_id"], "name": r["name"]} for r in entries]
    except (KeyError, TypeError) as e:
        raise RefuseError(
            f"工件缺 sqlite.ai_groups.groups 键，工件不可信，拒删：{e}") from e
    checklist = [r for r in rows if census.classify_group(r["name"])[0] == "A"]
    excluded = [r for r in rows if census.classify_group(r["name"])[0] != "A"]
    return {"checklist": checklist, "artifact_excluded": excluded}


def scan(conn: sqlite3.Connection) -> dict:
    """逐组分类（单一源 census.classify_group）。"""
    rows = conn.execute("SELECT id,name,tenant_id FROM ai_groups").fetchall()
    a_rows: list = []
    other_ids: set = set()
    for gid, name, tenant in rows:
        cls, rule = census.classify_group(name)
        if cls == "A":
            a_rows.append({"group_id": gid, "name": name,
                           "tenant_id": tenant or "default", "rule": rule})
        else:
            other_ids.add(gid)
    return {"total": len(rows), "A_rows": a_rows, "other_ids": other_ids}


def checklist_gates(art: dict, pre: dict) -> dict:
    """清单闸：清单组必须现判 A（词表漂移=拒删）+ 组名一致（错配=拒删）；
    缺行/清单外如实分流。"""
    drifted = []
    mismatched = []
    live_by_id = {r["group_id"]: r for r in pre["A_rows"]}
    for r in art["checklist"]:
        cls, rule = census.classify_group(r["name"])
        if cls != "A":
            drifted.append({"group_id": r["group_id"], "name": r["name"],
                            "reason": f"现判非 A（{cls}/{rule}）"})
        live = live_by_id.get(r["group_id"])
        if live is not None and live["name"] != r["name"]:
            mismatched.append({"group_id": r["group_id"],
                               "artifact_name": r["name"], "live_name": live["name"]})
    if drifted:
        raise RefuseError(
            f"清单组词表漂移（工件与单一源判法不一致），拒删：{drifted[:5]}")
    if mismatched:
        raise RefuseError(f"清单组名错配（工件与现库不一致），拒删：{mismatched[:5]}")
    art_ids = {r["group_id"] for r in art["checklist"]}
    live_ids = set(live_by_id)
    missing = sorted(str(i) for i in art_ids - live_ids)
    beyond = [r for r in pre["A_rows"] if r["group_id"] not in art_ids]
    count_drift = {"artifact": len(art_ids), "live_matched": len(art_ids & live_ids)}
    return {"missing_checklist_ids": missing, "beyond_checklist": beyond,
            "count_drift": count_drift}


def recheck(conn: sqlite3.Connection, targets: list) -> list:
    """删前逐行复核：fresh 重读每组再判（单一源），现判非 A=拒删；
    组名错配=拒删；缺行如实报非致命。"""
    bad = []
    mismatched = []
    missing = []
    for t in targets:
        row = conn.execute(
            "SELECT name,tenant_id FROM ai_groups WHERE id=?",
            (t["group_id"],)).fetchone()
        if row is None:
            missing.append(t["group_id"])
            continue
        cls, rule = census.classify_group(row[0])
        if cls != "A":
            bad.append({"group_id": t["group_id"], "name": row[0],
                        "cls": cls, "rule": rule})
        if row[0] != t["name"]:
            mismatched.append({"group_id": t["group_id"],
                               "checklist_name": t["name"], "live_name": row[0]})
    if bad:
        raise RefuseError(
            f"{len(bad)} 条待删组行复核不过（现判非 A，清单陈旧/错配），拒删：{bad[:5]}")
    if mismatched:
        raise RefuseError(f"待删组行名错配，拒删：{mismatched[:5]}")
    return missing


def child_rows(conn: sqlite3.Connection, table: str, targets: list) -> dict:
    """子表行三分：级联目标（group_id+组行租户，delete_group 同款）/ 他租户
    外来行（同组 id 异租户，只报数不删）/ 非目标行（含悬挂行，一行不动）。"""
    rows = conn.execute(
        f"SELECT id,group_id,tenant_id FROM {table}").fetchall()
    tgt_pairs = {(t["group_id"], t["tenant_id"]) for t in targets}
    tgt_gids = {t["group_id"] for t in targets}
    target_ids: list = []
    foreign: list = []
    other_ids: set = set()
    for rid, gid, tenant in rows:
        key = (gid, tenant or "default")
        if key in tgt_pairs:
            target_ids.append(rid)
        elif gid in tgt_gids:
            foreign.append({"id": rid, "group_id": gid,
                            "tenant_id": tenant or "default"})
        else:
            other_ids.add(rid)
    return {"total": len(rows), "target_ids": target_ids,
            "foreign_under_target": foreign, "other_ids": other_ids}


def orphan_rows(conn: sqlite3.Connection, table: str) -> dict:
    """悬挂行（组行已不在场）按 group_id 计数——只报数不删（不在清单且无组行
    可判，禁猜）。"""
    rows = conn.execute(
        f"SELECT group_id,COUNT(*) FROM {table} WHERE group_id NOT IN"
        " (SELECT id FROM ai_groups) GROUP BY group_id").fetchall()
    return {str(g): n for g, n in rows}


def cross_group_task_refs(conn: sqlite3.Connection, targets: list,
                          target_rows: dict) -> dict:
    """跨组 task_id 引用面清点（只报数）：task_id 属于待删任务集但行本身不随
    级联删（group_id 异组）——如存在即级联后新悬挂行，如实报。"""
    out: dict = {}
    task_ids = [r[0] for r in conn.execute(
        "SELECT id FROM ai_group_tasks WHERE group_id IN ({})".format(
            ",".join("?" * len(targets))),
        [t["group_id"] for t in targets]).fetchall()] if targets else []
    if not task_ids:
        return {t: 0 for t in TASK_REF_TABLES}
    ph_t = ",".join("?" * len(task_ids))
    for table in TASK_REF_TABLES:
        deleted_rows = set(target_rows[table]["target_ids"])
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE task_id IN ({ph_t})", task_ids).fetchall()
        # 存活行（不随级联删）却引用待删任务=级联后新悬挂，如实报
        out[table] = len([r for r in rows if r[0] not in deleted_rows])
    return out


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
    """按 delete_group 级联语义精确删（单事务，禁全表扫删）：6 子表
    (group_id, tenant_id) 双键 + 组行 (id, tenant_id) 双键。"""
    deleted: dict = {}
    for table in CHILD_TABLES:
        n = 0
        for t in targets:
            n += conn.execute(
                f"DELETE FROM {table} WHERE group_id=? AND tenant_id=?",
                (t["group_id"], t["tenant_id"])).rowcount
        deleted[table] = n
    n = 0
    for t in targets:
        n += conn.execute(
            "DELETE FROM ai_groups WHERE id=? AND tenant_id=?",
            (t["group_id"], t["tenant_id"])).rowcount
    deleted["ai_groups"] = n
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
    art = load_artifact_groups(artifact)
    db_path = Path(db_override or DB_PATH)
    report: dict = {
        "tool": "delete_ai_group_probe_residue",
        "mode": "apply" if apply else "dry-run",
        "artifact": str(artifact),
        "db": str(db_path),
        "artifact_checklist": art["checklist"],
        "artifact_checklist_n": len(art["checklist"]),
        "artifact_excluded": art["artifact_excluded"],
    }
    conn = sqlite3.connect(str(db_path))
    try:
        pre = scan(conn)
        gates = checklist_gates(art, pre)
        # 清单行 + 清单外同判据新残留（单列报告）一并删
        targets = pre["A_rows"]
        report.update({
            "live_A": len(targets),
            "matched": len(targets),
            "checklist": gates,
            "deleted_by_rule": _by_rule(targets),
            "pre_groups": {"total": pre["total"], "A": len(targets),
                           "other": len(pre["other_ids"])},
            "deleted_group_ids": [t["group_id"] for t in targets],
        })
        report["recheck_missing"] = recheck(conn, targets)

        pre_children = {t: child_rows(conn, t, targets) for t in CHILD_TABLES}
        pre_orphans = {t: orphan_rows(conn, t) for t in CHILD_TABLES}
        report["pre_children"] = {
            t: {"total": pre_children[t]["total"],
                "target": len(pre_children[t]["target_ids"]),
                "non_target": len(pre_children[t]["other_ids"]),
                "foreign_under_target": pre_children[t]["foreign_under_target"]}
            for t in CHILD_TABLES}
        report["orphan_rows_before"] = pre_orphans
        report["cross_group_task_refs"] = cross_group_task_refs(
            conn, targets, pre_children)

        if not apply:
            report["deleted"] = {"ai_groups": 0, "total": 0}
            for t in CHILD_TABLES:
                report["deleted"][t] = 0
            _emit(report, report_path)
            return report

        report["backup"] = backup_db(db_path, backup_dir or (REPO / "data" / "backups"),
                                     "ai_groups")
        deleted = delete_exact(conn, targets)
        deleted_total = sum(deleted.values())
        report["deleted"] = {**deleted, "total": deleted_total}

        post = scan(conn)
        post_children = {t: child_rows(conn, t, targets) for t in CHILD_TABLES}
        # 回读对账（允许生产并发新增行，只查删前在场行）
        for t in CHILD_TABLES:
            pre_row_ids = (set(pre_children[t]["target_ids"])
                           | set(pre_children[t]["other_ids"])
                           | {f["id"] for f in pre_children[t]["foreign_under_target"]})
            post_row_ids = (set(post_children[t]["target_ids"])
                            | set(post_children[t]["other_ids"])
                            | {f["id"] for f in post_children[t]["foreign_under_target"]})
            still = set(pre_children[t]["target_ids"]) & post_row_ids
            if still:
                raise RuntimeError(
                    f"回读对账不过（{t}）：级联待删行仍在场 {sorted(map(str, still))[:5]}")
            missing_other = set(pre_children[t]["other_ids"]) - post_row_ids
            missing_foreign = ({f["id"] for f in pre_children[t]["foreign_under_target"]}
                               - post_row_ids)
            if missing_other or missing_foreign:
                raise RuntimeError(
                    f"非目标行被误删（{t}）：非目标 {sorted(map(str, missing_other))[:5]}"
                    f" 外来 {sorted(map(str, missing_foreign))[:5]}")
            new_rows = post_row_ids - pre_row_ids
            report.setdefault("post_children", {})[t] = {
                "total": post_children[t]["total"],
                "non_target": len(post_children[t]["other_ids"]),
                "foreign_under_target": post_children[t]["foreign_under_target"],
                "concurrent_new_rows": len(new_rows),
                "total_reconcile": {
                    "pre": pre_children[t]["total"], "deleted": deleted[t],
                    "new": len(new_rows), "post": post_children[t]["total"],
                    "ok": post_children[t]["total"] == pre_children[t]["total"]
                    - deleted[t] + len(new_rows),
                },
            }
        post_ids = {r["group_id"] for r in post["A_rows"]} | post["other_ids"]
        still_groups = {t["group_id"] for t in targets} & post_ids
        if still_groups:
            raise RuntimeError(
                f"回读对账不过（ai_groups）：待删组行仍在场 {sorted(still_groups)[:5]}")
        missing_other_groups = pre["other_ids"] - post_ids
        if missing_other_groups:
            raise RuntimeError(
                f"非 A 组行被误删：{sorted(map(str, missing_other_groups))[:5]}")
        new_groups = post_ids - ({t["group_id"] for t in targets} | pre["other_ids"])
        report["post_groups"] = {
            "total": post["total"], "A": len(post["A_rows"]),
            "other": len(post["other_ids"]),
            "concurrent_new_rows": len(new_groups),
            "total_reconcile": {
                "pre": pre["total"], "deleted": deleted["ai_groups"],
                "new": len(new_groups), "post": post["total"],
                "ok": post["total"] == pre["total"] - deleted["ai_groups"]
                + len(new_groups),
            },
        }
        report["orphan_rows_after"] = {t: orphan_rows(conn, t) for t in CHILD_TABLES}
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
                    default=str(REPO / "data" / "test_residue_census_20261013.json"))
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
