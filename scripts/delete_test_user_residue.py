#!/usr/bin/env python3
"""TL-⑩ 删除执行件②b-2b-2a——测试用户 4+2（opensoul.db users 4 + enterprise.db users 2）
按清单精确删+备份+回读（跨库两面一把删）。

TL-③ 清点（scripts/census_test_residue.py + data/test_residue_census_20261013.json）
定性：测试用户 4+2 = opensoul.db users 4（test/admin2/autobot/uiprobe，用户名硬标识）
+ enterprise.db users 2（test/admin2，同词表）。本工具按单一源判法精确删
（TL-④~⑨ 同款纪律）：
  - 判单一源：census.classify_user（禁发明新判法；词表=A_USERNAMES 原语义 +
    email 含 'test'，与 TL-③ 清点逐字一致；enterprise 无 email 列仅按用户名判）
  - 删前逐行复核（fresh 重读再判），现判非 A=拒删一行不动（清单陈旧/错配=拒，
    禁删真实用户行）
  - 清单=工件 sqlite.opensoul_users.A + sqlite.enterprise_users.A 的 id 清单：
    工件不可信（缺键）=拒删；清单用户词表漂移（现判非 A）=拒删；清单缺行=如实报
    非致命（幂等重跑）；清单外新残留（现判 A 但不在清单）同判据一并删、单列报告
  - 备份：sqlite3 backup API 全库一致快照 + sha256（两库删前必留，可整体回滚）
  - 回读：删后复点（待删行全 gone、非 A 一行不动、总数算术对平、允许生产并发
    新增行，只查删前在场行），报告逐键落 JSON 工件
  - 跨库 FK 面如实不动：enterprise audit_log.user_id / user_roles.user_id 引用
    待删用户的行不属于清单（历史审计行），本工具只报数不删——读面既有
    COALESCE((SELECT tenant_id FROM users …), 'default') 兜底不破（enterprise.py:121），
    被删用户 tenant_id 均 'default'，兜底值与原值一致=零语义漂移
默认 dry-run；--apply 才真删。

范围（拆件②b-2b-2a）：测试用户 4+2 两库 users 行。
拆件②b-2b-2b 留待拍：探针组 2（ai_groups delete_group 级联，需拍板）。

诚实边界：判法是用户名/邮箱硬标识非语义理解（evo-test-* 等无硬标识名如实不猜）；
audit_log/user_roles 引用行不动（孤儿行读面安全，如需随删另拍）；B/U 类用户
（admin/default/hermes）一行不动。

用法：
  python3 scripts/delete_test_user_residue.py [--apply] [--report data/xxx.json]
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

# 两面定义（库/表/读列/工件键；id 类型以行为准原样删，禁强转）
FACES = {
    "opensoul": {"db": REPO / "data" / "opensoul.db", "artifact_key": "opensoul_users",
                 "cols": "id,username,email"},
    "enterprise": {"db": REPO / "data" / "enterprise.db", "artifact_key": "enterprise_users",
                   "cols": "id,username"},
}


class RefuseError(RuntimeError):
    """删前复核不过 / 工件不可信 / 清单错配 = 拒删，一行不动。"""


def load_artifact_users(artifact: Path) -> dict:
    """从 TL-③ 清点工件取测试用户清单（工件不可信=拒删）。

    清单=sqlite.opensoul_users.A + sqlite.enterprise_users.A 的 {id, username} 列表。
    """
    data = json.loads(Path(artifact).read_text())
    out: dict = {}
    for face, spec in FACES.items():
        try:
            a_list = data["sqlite"][spec["artifact_key"]]["A"]
            rows = [{"id": r["id"], "username": r["username"]} for r in a_list]
        except (KeyError, TypeError) as e:
            raise RefuseError(
                f"工件缺 sqlite.{spec['artifact_key']}.A 键，工件不可信，拒删：{e}") from e
        out[face] = rows
    return out


def scan(conn: sqlite3.Connection, face: str) -> dict:
    """逐行分类（单一源 census.classify_user）。"""
    cols = FACES[face]["cols"]
    if face == "opensoul":
        rows = conn.execute(f"SELECT {cols} FROM users").fetchall()
        judged = [(r[0], r[1], census.classify_user(r[1], r[2])) for r in rows]
    else:
        rows = conn.execute(f"SELECT {cols} FROM users").fetchall()
        judged = [(r[0], r[1], census.classify_user(r[1])) for r in rows]
    a_rows: list = []
    other_ids: set = set()
    for uid, uname, (cls, rule) in judged:
        if cls == "A":
            a_rows.append({"id": uid, "username": uname, "rule": rule})
        else:
            other_ids.add(uid)
    return {"total": len(rows), "A_rows": a_rows, "other_ids": other_ids}


def checklist_gates(art: dict, pre: dict, face: str) -> dict:
    """清单闸：清单用户必须现判 A（词表漂移=拒删）；缺行/清单外如实分流。"""
    drifted = []
    for r in art[face]:
        cls, rule = census.classify_user(r["username"])
        if cls != "A":
            drifted.append({"id": r["id"], "username": r["username"],
                            "reason": f"现判非 A（{cls}/{rule}）"})
    if drifted:
        raise RefuseError(f"清单用户词表漂移（工件与单一源判法不一致），拒删：{drifted[:5]}")
    art_ids = {r["id"] for r in art[face]}
    live_ids = {r["id"] for r in pre[face]["A_rows"]}
    missing = sorted((str(i) for i in art_ids - live_ids))
    beyond = [r for r in pre[face]["A_rows"] if r["id"] not in art_ids]
    count_drift = {"artifact": len(art_ids), "live_matched": len(art_ids & live_ids)}
    return {"missing_checklist_ids": missing, "beyond_checklist": beyond,
            "count_drift": count_drift}


def recheck(conn: sqlite3.Connection, face: str, ids: list) -> list:
    """删前逐行复核：fresh 重读每行再判（单一源），现判非 A=拒删；缺行如实报非致命。"""
    bad = []
    missing = []
    for uid in ids:
        if face == "opensoul":
            row = conn.execute(
                "SELECT username,email FROM users WHERE id=?", (uid,)).fetchone()
        else:
            row = conn.execute(
                "SELECT username FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            missing.append(uid)
            continue
        judge = census.classify_user(row[0], row[1]) if face == "opensoul" \
            else census.classify_user(row[0])
        cls, rule = judge
        if cls != "A":
            bad.append({"id": uid, "username": row[0], "cls": cls, "rule": rule})
    if bad:
        raise RefuseError(f"{len(bad)} 条待删行复核不过（现判非 A，清单陈旧/错配），拒删：{bad[:5]}")
    return missing


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


def delete_exact(conn: sqlite3.Connection, face: str, ids: list) -> int:
    """按 id 精确删（单事务，禁全表扫删）。id 类型以行为准原样删。"""
    if not ids:
        return 0
    ph = ",".join("?" * len(ids))
    deleted = conn.execute(f"DELETE FROM users WHERE id IN ({ph})", ids).rowcount
    conn.commit()
    return deleted


def fk_refs(conn: sqlite3.Connection, face: str, ids: list) -> dict:
    """跨库 FK 引用面清点（只报数不删——历史审计行不属于清单）。"""
    out: dict = {}
    if face != "enterprise" or not ids:
        return out
    ph = ",".join("?" * len(ids))
    for table in ("audit_log", "user_roles"):
        try:
            n = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id IN ({ph})", ids).fetchone()[0]
            out[table] = n
        except sqlite3.OperationalError:
            out[table] = "table_missing"
    return out


def run(artifact: Path, apply: bool = False, backup_dir: Path | None = None,
        report_path: Path | None = None,
        db_overrides: dict | None = None) -> dict:
    art = load_artifact_users(artifact)
    report: dict = {
        "tool": "delete_test_user_residue",
        "mode": "apply" if apply else "dry-run",
        "artifact": str(artifact),
        "faces": {},
    }
    conns: dict = {}
    pre: dict = {}
    gates: dict = {}
    ids_by_face: dict = {}
    missing_by_face: dict = {}
    try:
        for face, spec in FACES.items():
            db_path = Path((db_overrides or {}).get(face, spec["db"]))
            conn = sqlite3.connect(str(db_path))
            conns[face] = (conn, db_path)
            pre[face] = scan(conn, face)
            gates[face] = checklist_gates(art, pre, face)
            # 清单行 + 清单外同判据新残留（单列报告）一并删
            ids_by_face[face] = [r["id"] for r in pre[face]["A_rows"]]
            # 删前逐行复核：fresh 重读再判（单一源），现判非 A=拒删
            missing_by_face[face] = recheck(conn, face, ids_by_face[face])

        for face in FACES:
            conn, db_path = conns[face]
            ids = ids_by_face[face]
            report["faces"][face] = {
                "db": str(db_path),
                "artifact_checklist": art[face],
                "artifact_checklist_n": len(art[face]),
                "live_A": len(ids),
                "artifact_drift": len(art[face]) != len(
                    [r for r in pre[face]["A_rows"] if r["id"] in {a["id"] for a in art[face]}]),
                "matched": len(ids),
                "deleted_by_rule": _by_rule(pre[face]["A_rows"]),
                "checklist": {
                    "missing_checklist_ids": gates[face]["missing_checklist_ids"],
                    "beyond_checklist": gates[face]["beyond_checklist"],
                    "count_drift": gates[face]["count_drift"],
                },
                "recheck_missing": missing_by_face[face],
                "pre": {"total": pre[face]["total"], "A": len(ids),
                        "other": len(pre[face]["other_ids"])},
                "deleted_ids": ids,
                "fk_refs_to_deleted": fk_refs(conn, face, ids),
            }

        if not apply:
            for face in FACES:
                report["faces"][face]["deleted"] = 0
            report["deleted_total"] = 0
            _emit(report, report_path)
            return report

        backup_root = backup_dir or (REPO / "data" / "backups")
        deleted_total = 0
        for face in FACES:
            conn, db_path = conns[face]
            face_rep = report["faces"][face]
            ids = ids_by_face[face]
            face_rep["backup"] = backup_db(db_path, backup_root, f"{face}_users")
            face_rep["deleted"] = delete_exact(conn, face, ids)
            deleted_total += face_rep["deleted"]

            post = scan(conn, face)
            # 回读对账（允许生产并发新增行，只查删前在场行）
            post_ids = {r["id"] for r in post["A_rows"]} | post["other_ids"]
            still_there = set(ids) & post_ids
            if still_there:
                raise RuntimeError(f"回读对账不过（{face}）：待删行仍在场 {sorted(map(str, still_there))[:5]}")
            missing_other = pre[face]["other_ids"] - post_ids
            if missing_other:
                raise RuntimeError(
                    f"非 A 行被误删（{face}）{len(missing_other)} 行：{sorted(map(str, missing_other))[:5]}")
            new_rows = post_ids - (set(ids) | pre[face]["other_ids"])
            face_rep.update({
                "post": {"total": post["total"], "A": len(post["A_rows"]),
                         "other": len(post["other_ids"])},
                "post_A_rows_not_in_deleted": sorted(
                    map(str, (r["id"] for r in post["A_rows"] if r["id"] not in set(ids)))),
                "non_A_missing": sorted(map(str, missing_other)),
                "concurrent_new_rows": len(new_rows),
                "total_reconcile": {
                    "pre": pre[face]["total"], "deleted": face_rep["deleted"],
                    "new": len(new_rows), "post": post["total"],
                    "ok": post["total"] == pre[face]["total"] - face_rep["deleted"] + len(new_rows),
                },
                "fk_refs_to_deleted_after": fk_refs(conn, face, ids),
            })
        report["deleted_total"] = deleted_total
        _emit(report, report_path)
        return report
    finally:
        for conn, _ in conns.values():
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
    ap.add_argument("--artifact", default=str(REPO / "data" / "test_residue_census_20261013.json"))
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-dir", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report", help="把执行报告落成 JSON 工件")
    args = ap.parse_args()
    run(Path(args.artifact), apply=args.apply,
        backup_dir=Path(args.backup_dir),
        report_path=Path(args.report) if args.report else None)


if __name__ == "__main__":
    main()
