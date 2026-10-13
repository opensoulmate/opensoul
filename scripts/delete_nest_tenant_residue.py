#!/usr/bin/env python3
"""TL-⑦ 删除执行件②b-1——e2e 租户（nest.db tenants A 类）按清单精确删+备份+回读。

TL-③ 清点（scripts/census_test_residue.py + data/test_residue_census_20261013.json）
定性：nest.db tenants 2 行 A 类（e2e-t8c-a / e2e-t8c-b = e2e 探针漏网租户）。本工具
按单一源判法精确删（TL-④/⑤/⑥ 同款纪律）：
  - 判单一源：census.classify_tenant（禁发明新判法）；删前逐行复核（fresh 重读再判
    +清单名一致），现判非 A / 名错配 = 拒删一行不动（清单陈旧/错配=拒，禁删真租户）
  - 按清单精确删：只删工件 sqlite.nest_tenants.A 清单行；清单外新残留单列报告不删
  - 引用面拒删闸：删前扫描 db_dir 下 *.db 的结构化租户引用（列名含 tenant 的列；
    tenants 自身除外；纯审计表 access_log 只报告不拒删——审计史处置归 perm 决策审计
    面同批另拍），发现身份/绑定类引用=拒删（禁制造悬空绑定），报告 pre/post 引用计数
  - 备份：sqlite3 backup API 全库一致快照 + sha256（删前必留，可整体回滚）
  - 回读：删后复点（待删行全 gone、非目标行含 default 一行不动、允许生产并发新写入，
    只查删前在场行），报告逐键落 JSON 工件
默认 dry-run；--apply 才真删。

范围（拆件②b-1）：nest.db tenants e2e 租户 2 行。
拆件②b-2+ 留待拍：测试用户 4+2 / perm 决策审计 612 / manifests 悬挂 5 /
探针组 2（各自级联语义需各自备份+回读）。

诚实边界：引用扫描只看列名含 tenant 的结构化列（tenant_id/namespace 等），
自由文本中的租户 id 不扫；access_log 纯审计表引用只报告不拒删不删。

用法：
  python3 scripts/delete_nest_tenant_residue.py [--apply] [--report data/xxx.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
for _p in (str(SCRIPTS), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import census_test_residue as census  # noqa: E402  单一源分类判法，禁手抄

AUDIT_TABLES = {"access_log"}  # 纯审计表：引用只报告不拒删（审计史处置另拍）


class RefuseError(RuntimeError):
    """删前复核不过 / 存在身份引用 = 拒删（工件不可信 / 清单错配 / 会制造悬空绑定），
    一行不动。"""


def load_artifact_tenants(artifact: Path) -> dict:
    """从 TL-③ 清点工件取 nest_tenants 清单（工件不可信=拒删）。"""
    data = json.loads(Path(artifact).read_text())
    try:
        tn = data["sqlite"]["nest_tenants"]
        raw = tn["A"]
        checklist = [{"tenant_id": str(r["tenant_id"]), "name": str(r["name"])} for r in raw]
    except (KeyError, TypeError, ValueError) as e:
        raise RefuseError(f"工件缺 sqlite.nest_tenants.A 清单，工件不可信，拒删：{e}") from e
    return {"A": checklist, "total": int(tn.get("total") or 0)}


def scan(conn: sqlite3.Connection) -> dict:
    """逐行分类（单一源 census.classify_tenant）。"""
    rows = conn.execute("SELECT tenant_id,name FROM tenants").fetchall()
    out = []
    a_ids: list = []
    other_ids: list = []
    for tid, name in rows:
        cls, rule = census.classify_tenant(name)
        out.append({"tenant_id": tid, "name": name, "cls": cls, "rule": rule})
        (a_ids if cls == "A" else other_ids).append(tid)
    return {"rows": out, "A_ids": a_ids, "other_ids": other_ids, "total": len(rows)}


def recheck(conn: sqlite3.Connection, checklist: list) -> None:
    """删前逐行复核：fresh 重读每行再判（单一源）+清单名一致，否则拒删。"""
    bad = []
    for t in checklist:
        row = conn.execute(
            "SELECT name FROM tenants WHERE tenant_id=?", (t["tenant_id"],)).fetchone()
        if row is None:
            bad.append({"tenant_id": t["tenant_id"], "reason": "行缺失"})
            continue
        cls, rule = census.classify_tenant(row[0])
        if cls != "A":
            bad.append({"tenant_id": t["tenant_id"], "name": row[0],
                        "reason": f"现判非 A（{cls}/{rule}）"})
        elif row[0] != t["name"]:
            bad.append({"tenant_id": t["tenant_id"], "reason":
                        f"名错配 live={row[0]} checklist={t['name']}"})
    if bad:
        raise RefuseError(f"{len(bad)} 条待删行复核不过（清单陈旧/错配），拒删：{bad[:5]}")


def reference_scan(db_dir: Path, ids: list) -> dict:
    """扫描 db_dir 下 *.db 的结构化租户引用（列名含 tenant 的列；tenants 自身除外）。

    纯审计表（AUDIT_TABLES）计入 audit 只报告不拒；其余表计入 binding（存在即拒删）。
    自由文本引用不扫（诚实边界）。
    """
    binding: list = []
    audit: list = []
    if not ids:
        return {"binding": binding, "audit": audit}
    ph = ",".join("?" * len(ids))
    for db in sorted(Path(db_dir).glob("*.db")):
        try:
            c = sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            tables = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tables:
                if t == "tenants":
                    continue
                cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
                for col in cols:
                    if "tenant" not in col.lower():
                        continue
                    n = c.execute(
                        f"SELECT COUNT(*) FROM {t} WHERE {col} IN ({ph})", ids
                    ).fetchone()[0]
                    if n:
                        rec = {"db": db.name, "table": t, "column": col, "rows": n}
                        (audit if t in AUDIT_TABLES else binding).append(rec)
        finally:
            c.close()
    return {"binding": binding, "audit": audit}


def backup_db(src: Path, backup_dir: Path) -> dict:
    """sqlite3 backup API 全库一致快照 + sha256。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"nest_pre_delete_{time.strftime('%Y%m%d_%H%M%S')}.db"
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


def delete_exact(conn: sqlite3.Connection, ids: list) -> dict:
    """按 tenant_id 精确删（单事务）。"""
    ph = ",".join("?" * len(ids))
    n = conn.execute(f"DELETE FROM tenants WHERE tenant_id IN ({ph})", ids).rowcount
    conn.commit()
    return {"tenants": n}


def run(db_path: Path, artifact: Path, apply: bool = False,
        backup_dir: Optional[Path] = None, report_path: Optional[Path] = None,
        db_dir: Optional[Path] = None) -> dict:
    art = load_artifact_tenants(artifact)
    conn = sqlite3.connect(str(db_path))
    try:
        pre = scan(conn)
        checklist = art["A"]
        cids = [t["tenant_id"] for t in checklist]
        live_ids = {r["tenant_id"] for r in pre["rows"]}
        missing = [i for i in cids if i not in live_ids]
        present = [t for t in checklist if t["tenant_id"] in live_ids]
        if present:
            recheck(conn, present)
        beyond = [{"tenant_id": r["tenant_id"], "name": r["name"]}
                  for r in pre["rows"]
                  if r["cls"] == "A" and r["tenant_id"] not in set(cids)]
        refs_pre = reference_scan(Path(db_dir) if db_dir else (REPO / "data"), cids)
        if refs_pre["binding"]:
            raise RefuseError(
                f"待删租户存在身份/绑定类引用 {refs_pre['binding']}，拒删（禁制造悬空绑定）")
        target_ids = [t["tenant_id"] for t in present]
        report: dict = {
            "tool": "delete_nest_tenant_residue",
            "mode": "apply" if apply else "dry-run",
            "db": str(db_path), "artifact": str(artifact),
            "checklist_ids": cids, "live_A_ids": pre["A_ids"],
            "artifact_drift": sorted(cids) != sorted(pre["A_ids"]),
            "matched": len(target_ids), "missing_checklist": missing,
            "beyond_checklist": beyond,
            "pre": {"total": pre["total"], "A": len(pre["A_ids"]),
                    "other": len(pre["other_ids"])},
            "refs_pre": refs_pre,
            "deleted_ids": target_ids,
        }
        if not apply:
            report["deleted"] = {"tenants": 0}
            _emit(report, report_path)
            return report

        report["backup"] = backup_db(db_path, backup_dir or (REPO / "data" / "backups"))
        report["deleted"] = delete_exact(conn, target_ids) if target_ids else {"tenants": 0}

        post = scan(conn)
        post_ids = {r["tenant_id"] for r in post["rows"]}
        still = set(target_ids) & post_ids
        if still:
            raise RuntimeError(f"回读对账不过：待删行仍在场 {sorted(still)}")
        gone_other = set(pre["other_ids"]) - post_ids
        if gone_other:
            raise RuntimeError(f"非目标行被误删 {sorted(gone_other)}")
        refs_post = reference_scan(Path(db_dir) if db_dir else (REPO / "data"), cids)
        if refs_post["binding"]:
            raise RuntimeError(f"回读对账不过：删后仍有身份/绑定引用 {refs_post['binding']}")
        report.update({
            "post": {"total": post["total"], "A": len(post["A_ids"]),
                     "other": len(post["other_ids"])},
            "post_A_ids": post["A_ids"],
            "non_target_missing": sorted(gone_other),
            "concurrent_new_rows": sorted(post_ids - live_ids),
            "refs_post": refs_post,
        })
        _emit(report, report_path)
        return report
    finally:
        conn.close()


def _emit(report: dict, report_path: Optional[Path]) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    print(text)
    if report_path:
        Path(report_path).write_text(text)
        print(f"[written] {report_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "data" / "nest.db"))
    ap.add_argument("--artifact", default=str(REPO / "data" / "test_residue_census_20261013.json"))
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-dir", default=str(REPO / "data" / "backups"))
    ap.add_argument("--db-dir", default=str(REPO / "data"), help="引用扫描目录")
    ap.add_argument("--report", help="把执行报告落成 JSON 工件")
    args = ap.parse_args()
    run(Path(args.db), Path(args.artifact), apply=args.apply,
        backup_dir=Path(args.backup_dir),
        report_path=Path(args.report) if args.report else None,
        db_dir=Path(args.db_dir))


if __name__ == "__main__":
    main()
