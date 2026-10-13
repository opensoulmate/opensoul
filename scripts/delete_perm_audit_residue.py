#!/usr/bin/env python3
"""TL-⑨ 删除执行件②b-2b-1——perm 决策审计 A 类（permission.db perm_decision_audit）
按清单精确删+备份+回读。

TL-③ 清点（scripts/census_test_residue.py + data/test_residue_census_20261013.json）
定性：perm_decision_audit 612 行 A 类（测试会话 t1/t2/api-test/e2e/py-exfil-probe/p0*）
+ TL-⑤ 留档点名"test_dangerous_tool 历史决策 105 行留拆件②"（session='' 工具名硬标识），
共 717 行测试决策审计残留（真实 om-* 会话审计与其余 U 类一行不动）。本工具按单一源
判法精确删（TL-④~⑧ 同款纪律）：
  - 判单一源：census.classify_perm_audit（禁发明新判法；词表=A_SESSIONS/A_SESSION_PREFIX
    原语义 + A_RULE_TOOLS 复用 TL-⑤ 硬标识）；删前逐行复核（fresh 重读再判），现判非 A
    =拒删一行不动（清单陈旧/错配=拒，禁删真实审计行）
  - 清单=工件 permission.A_by_session 会话键 + A_RULE_TOOLS 工具键（TL-⑤ 留档点名）：
    工件不可信（缺键）=拒删；清单会话词表漂移（现判非 A）=拒删；清单会话缺行=如实报
    非致命（幂等重跑）；清单外新残留（A 但不落清单组）同判据一并删、单列报告
  - 备份：sqlite3 backup API 全库一致快照 + sha256（删前必留，可整体回滚）
  - 回读：删后复点（待删行全 gone、非 A 含真实 om-* 一行不动、允许生产并发新写入，
    只查删前在场行），报告逐键落 JSON 工件
默认 dry-run；--apply 才真删。

范围（拆件②b-2b-1）：perm_decision_audit 717 行（612 会话类 + 105 工具名类）。
拆件②b-2b-2+ 留待拍：测试用户 4+2（跨库 FK 面）/ 探针组 2（delete_group 级联）。

诚实边界：判法是会话名/工具名硬标识非语义理解（空 session 的其余 937 行 U 类不动，
evo-test-*/probe/verify 等无硬标识会话如实不猜）；perm_rules 本体不动（TL-⑤ 软删已收）；
U/B 混合面（TL-③ 如实落 U）不属本拍。

用法：
  python3 scripts/delete_perm_audit_residue.py [--apply] [--report data/xxx.json]
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

CHUNK = 500  # SQLite 变量上限防御（DELETE IN 分批）


class RefuseError(RuntimeError):
    """删前复核不过 / 工件不可信 / 清单错配 = 拒删，一行不动。"""


def load_artifact_audit(artifact: Path) -> dict:
    """从 TL-③ 清点工件取 perm 决策审计清单（工件不可信=拒删）。

    清单组：permission.A_by_session 会话键（612 行）+ A_RULE_TOOLS 工具键
    （TL-⑤ 留档点名 105 行，词表=census.A_RULE_TOOLS 单一源）。
    """
    data = json.loads(Path(artifact).read_text())
    try:
        perm = data["permission"]
        by_sess = {str(k): int(v) for k, v in dict(perm["A_by_session"]).items()}
        a_total = int(perm["A_total"])
    except (KeyError, TypeError, ValueError) as e:
        raise RefuseError(f"工件缺 permission.A_by_session/A_total 键，工件不可信，拒删：{e}") from e
    return {"A_by_session": by_sess, "A_total": a_total,
            "A_decision_ids": list(perm.get("A_decision_ids") or [])}


def scan(conn: sqlite3.Connection) -> dict:
    """逐行分类（单一源 census.classify_perm_audit）。"""
    rows = conn.execute(
        "SELECT decision_id,session_id,tool_name FROM perm_decision_audit").fetchall()
    a_rows: list = []
    other_ids: set = set()
    for did, sess, tool in rows:
        cls, rule = census.classify_perm_audit(sess, tool)
        if cls == "A":
            a_rows.append({"decision_id": did, "session_id": sess or "",
                           "tool_name": tool, "rule": rule})
        else:
            other_ids.add(did)
    return {"total": len(rows), "A_rows": a_rows, "other_ids": other_ids}


def checklist_gates(art: dict, pre: dict) -> dict:
    """清单闸：会话键必须现判 A（词表漂移=拒删）；缺行/清单外如实分流。"""
    a_sessions = {r["session_id"] for r in pre["A_rows"]}
    drifted = []
    for s in art["A_by_session"]:
        cls, rule = census.classify_perm_audit(s, "")
        if cls != "A":
            drifted.append({"session_id": s, "reason": f"现判非 A（{cls}/{rule}）"})
    if drifted:
        raise RefuseError(f"清单会话词表漂移（工件与单一源判法不一致），拒删：{drifted[:5]}")
    art_sessions = set(art["A_by_session"])
    missing_sessions = sorted(s for s in art_sessions if s not in a_sessions)
    tool_rows = [r for r in pre["A_rows"] if r["rule"] == "A_RULE_TOOLS"]
    beyond = [r for r in pre["A_rows"]
              if r["session_id"] not in art_sessions and r["rule"] != "A_RULE_TOOLS"]
    count_drift = {s: {"artifact": n, "live": sum(1 for r in pre["A_rows"] if r["session_id"] == s)}
                   for s, n in art["A_by_session"].items()
                   if n != sum(1 for r in pre["A_rows"] if r["session_id"] == s)}
    return {"missing_checklist_sessions": missing_sessions,
            "tool_checklist_rows": len(tool_rows),
            "beyond_checklist": beyond,
            "count_drift": count_drift}


def recheck(conn: sqlite3.Connection, ids: list) -> list:
    """删前逐行复核：fresh 重读每行再判（单一源），现判非 A=拒删；缺行如实报非致命。"""
    bad = []
    missing = []
    for did in ids:
        row = conn.execute(
            "SELECT session_id,tool_name FROM perm_decision_audit WHERE decision_id=?",
            (did,)).fetchone()
        if row is None:
            missing.append(did)
            continue
        cls, rule = census.classify_perm_audit(row[0], row[1])
        if cls != "A":
            bad.append({"decision_id": did, "session_id": row[0], "tool_name": row[1],
                        "cls": cls, "rule": rule})
    if bad:
        raise RefuseError(f"{len(bad)} 条待删行复核不过（现判非 A，清单陈旧/错配），拒删：{bad[:5]}")
    return missing


def backup_db(src: Path, backup_dir: Path) -> dict:
    """sqlite3 backup API 全库一致快照 + sha256。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"permission_pre_delete_{time.strftime('%Y%m%d_%H%M%S')}.db"
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
    """按 decision_id 精确删（单事务分批，禁全表扫删）。"""
    deleted = 0
    for i in range(0, len(ids), CHUNK):
        batch = ids[i:i + CHUNK]
        ph = ",".join("?" * len(batch))
        deleted += conn.execute(
            f"DELETE FROM perm_decision_audit WHERE decision_id IN ({ph})", batch).rowcount
    conn.commit()
    return deleted


def run(db_path: Path, artifact: Path, apply: bool = False,
        backup_dir: Path | None = None, report_path: Path | None = None) -> dict:
    art = load_artifact_audit(artifact)
    conn = sqlite3.connect(str(db_path))
    try:
        pre = scan(conn)
        gates = checklist_gates(art, pre)
        ids = [r["decision_id"] for r in pre["A_rows"]]
        # 删前逐行复核：fresh 重读再判（单一源），现判非 A=拒删
        missing = recheck(conn, ids) if ids else []

        report: dict = {
            "tool": "delete_perm_audit_residue",
            "mode": "apply" if apply else "dry-run",
            "db": str(db_path), "artifact": str(artifact),
            "artifact_A_total": art["A_total"], "live_A": len(ids),
            "artifact_drift": art["A_total"] != len(ids),
            "matched": len(ids),
            "deleted_by_rule": _by_rule(pre["A_rows"]),
            "checklist": {
                "artifact_sessions": sorted(art["A_by_session"]),
                "tool_rule": sorted(census.A_RULE_TOOLS),
                "missing_sessions": gates["missing_checklist_sessions"],
                "tool_checklist_rows": gates["tool_checklist_rows"],
                "beyond_checklist": gates["beyond_checklist"],
                "count_drift": gates["count_drift"],
            },
            "recheck_missing": missing,
            "pre": {"total": pre["total"], "A": len(ids), "other": len(pre["other_ids"])},
            "deleted_ids": ids,
        }
        if not apply:
            report["deleted"] = 0
            _emit(report, report_path)
            return report

        report["backup"] = backup_db(db_path, backup_dir or (REPO / "data" / "backups"))
        report["deleted"] = delete_exact(conn, ids) if ids else 0

        post = scan(conn)
        # 回读对账（允许生产并发新写入，只查删前在场行）
        post_ids = {r["decision_id"] for r in post["A_rows"]} | post["other_ids"]
        still_there = set(ids) & post_ids
        if still_there:
            raise RuntimeError(f"回读对账不过：待删行仍在场 {sorted(still_there)[:5]}")
        missing_other = pre["other_ids"] - post_ids
        if missing_other:
            raise RuntimeError(f"非 A 行被误删 {len(missing_other)} 行：{sorted(missing_other)[:5]}")
        new_rows = post_ids - (set(ids) | pre["other_ids"])
        report.update({
            "post": {"total": post["total"], "A": len(post["A_rows"]),
                     "other": len(post["other_ids"])},
            "post_A_rows_not_in_deleted": sorted(
                r["decision_id"] for r in post["A_rows"] if r["decision_id"] not in set(ids)),
            "non_A_missing": sorted(missing_other),
            "concurrent_new_rows": len(new_rows),
            "total_reconcile": {
                "pre": pre["total"], "deleted": report["deleted"],
                "new": len(new_rows), "post": post["total"],
                "ok": post["total"] == pre["total"] - report["deleted"] + len(new_rows),
            },
        })
        _emit(report, report_path)
        return report
    finally:
        conn.close()


def _by_rule(rows: list) -> dict:
    out: dict = {}
    for r in rows:
        out[r["rule"]] = out.get(r["rule"], 0) + 1
    return out


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
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-dir", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report", help="把执行报告落成 JSON 工件")
    args = ap.parse_args()
    run(Path(args.db), Path(args.artifact), apply=args.apply,
        backup_dir=Path(args.backup_dir),
        report_path=Path(args.report) if args.report else None)


if __name__ == "__main__":
    main()
