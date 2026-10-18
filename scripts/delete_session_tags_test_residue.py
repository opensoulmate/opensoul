#!/usr/bin/env python3
"""SESS-⑦ 删除执行件——session_tags 历史探针残留行按清单精确删+备份+回读。

SESS-③/④/⑤/⑥ 留档候选「session_tags 历史探针残留行清理（TL 系先例）」执行件。
背景（实证）：data/opensoul.db session_tags 表存数百行 client-key: 探针别名残留
（/tags/all 全局面可见污染）；client-key:<客户端会话 id> 别名行的全仓唯一写点=
acp-proxy soulmate_agent alias persist（客户端会话 id → agent 会话去重映射），
探针客户端 id（rej-/probe-/diag-/smoke-/term-probe- 自述前缀）的映射随探针死亡成残留。

判法（单一源）：census.classify_session_tag（禁发明新判法）——A=自述测试/探针硬前缀
闭集；temp-/om-/grp-（真实客户端会话 id，11 条 temp- 挂活用户会话）与 agent:* 功能
标签一律 U 不动（宁窄勿滥，边界留拍板）。

TL-④/⑤/⑫/⑮ 同款纪律：
  - 按清单精确删：只删清单 (session_id, tag_name) 行对；清单外新 A 残留单列报告不删
  - 删前逐行复核：fresh 重读再判（单一源）+created_at 一致，现判非 U→A 不成立/键错配
    = RefuseError 拒删一条不动（清单陈旧/错配=拒，禁删真用户标签）
  - 备份：sqlite3 backup API 全库一致快照 + sha256（删前必留，可整体回滚）
  - 回读：删后复点（待删行全 gone、删前在场的非目标行逐条不变、允许生产并发新增行
    只查删前在场项），报告逐键落 JSON 工件
默认 dry-run；--apply 才真删。

用法：
  python3 scripts/delete_session_tags_test_residue.py [--apply] \
      [--manifest data/xxx.json] [--report data/xxx.json]
  --manifest 给定=按工件清单删（清单形：{"checklist":[{"session_id","tag_name","created_at"}]}
  或裸 list）；缺省=以本次 fresh 扫描的 A 行为清单。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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


class RefuseError(RuntimeError):
    """删前复核不过 = 拒删（清单陈旧/键错配），一条不动。"""


def default_db_path() -> Path:
    """opensoul.db 路径（出处 src/api/sessions_api.py _OPENSOUL_DB：OPENSOUL_DB env 优先）。"""
    env = os.environ.get("OPENSOUL_DB")
    if env:
        return Path(env)
    root = os.environ.get("OPENSOUL_ROOT", str(REPO))
    return Path(root) / "data" / "opensoul.db"


def fetch_rows(db_path: Path) -> list[dict]:
    """全表只读取行（session_id, tag_name, created_at）。"""
    if not Path(db_path).exists():
        raise RefuseError(f"库不存在：{db_path}")
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            "SELECT session_id, tag_name, created_at FROM session_tags"
        ).fetchall()
        alive = {
            r[0]
            for r in db.execute("SELECT id FROM agent_sessions").fetchall()
        }
    finally:
        db.close()
    return [
        {
            "session_id": r["session_id"],
            "tag_name": r["tag_name"],
            "created_at": r["created_at"],
            "session_alive": r["session_id"] in alive,
        }
        for r in rows
    ]


def row_key(row: dict) -> tuple[str, str]:
    return (str(row.get("session_id", "")), str(row.get("tag_name", "")))


def scan(rows: list[dict]) -> dict:
    """逐行分类（单一源 census.classify_session_tag）。"""
    out_rows = []
    a_rows: list[dict] = []
    u_rows: list[dict] = []
    for row in rows:
        cls, rule = census.classify_session_tag(row["tag_name"])
        item = {**row, "cls": cls, "rule": rule}
        out_rows.append(item)
        (a_rows if cls == "A" else u_rows).append(item)
    return {"rows": out_rows, "A_rows": a_rows, "U_rows": u_rows, "total": len(out_rows)}


def load_manifest_checklist(manifest_path: Path) -> list[dict]:
    """从工件取待删清单（工件不可信=拒删）。

    形：{"checklist":[{"session_id","tag_name","created_at"}]} 或裸 list[同形]。
    """
    try:
        data = json.loads(Path(manifest_path).read_text())
    except FileNotFoundError as e:
        raise RefuseError(f"清单工件不存在：{manifest_path}") from e
    except json.JSONDecodeError as e:
        raise RefuseError(f"清单工件不是合法 JSON，拒删：{e}") from e
    raw = data.get("checklist") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise RefuseError("清单工件缺 checklist 列表（或非裸 list），工件不可信，拒删")
    checklist = []
    for r in raw:
        if not isinstance(r, dict) or not r.get("session_id") or not r.get("tag_name"):
            raise RefuseError(f"清单行缺 session_id/tag_name，工件不可信，拒删：{r!r}")
        checklist.append(
            {
                "session_id": str(r["session_id"]),
                "tag_name": str(r["tag_name"]),
                "created_at": r.get("created_at"),
            }
        )
    return checklist


def recheck(live_rows: list[dict], checklist: list[dict]) -> list[dict]:
    """删前逐行复核：fresh 重读每条再判（单一源）+键在场+created_at 一致，否则拒删。

    返回已在场的待删行；清单行不在场=missing（幂等重跑语义，不拒不删）。
    """
    live = {row_key(r): r for r in live_rows}
    bad = []
    present = []
    for t in checklist:
        key = row_key(t)
        row = live.get(key)
        if row is None:
            continue
        cls, rule = census.classify_session_tag(row["tag_name"])
        if cls != "A":
            bad.append({**t, "reason": f"现判非 A（{cls}/{rule}）"})
        elif t.get("created_at") is not None and str(row["created_at"]) != str(t["created_at"]):
            bad.append({**t, "reason": f"created_at 错配 live={row['created_at']} manifest={t['created_at']}"})
        else:
            present.append(row)
    if bad:
        raise RefuseError(f"{len(bad)} 条待删行复核不过（清单陈旧/键错配），拒删：{bad[:5]}")
    return present


def backup_db(db_path: Path, backup_dir: Path) -> dict:
    """sqlite3 backup API 全库一致快照 + sha256（删前必留，可整体回滚）。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = Path(backup_dir) / f"session_tags_pre_delete_{time.strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out = sqlite3.connect(str(dest))
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    return {"path": str(dest), "sha256": digest, "bytes": dest.stat().st_size}


def delete_exact(pairs: list[tuple[str, str]], db_path: Path) -> int:
    """按 (session_id, tag_name) 行对精确删（只命中目标行，键不匹配=原样保留）。"""
    if not pairs:
        return 0
    db = sqlite3.connect(str(db_path))
    try:
        cur = db.executemany(
            "DELETE FROM session_tags WHERE session_id = ? AND tag_name = ?", pairs
        )
        db.commit()
        return int(cur.rowcount or 0)
    finally:
        db.close()


def row_fingerprint(row: dict) -> str:
    """行内容指纹（canonical JSON sha256，键序无关——回读对账逐条不变判定）。"""
    core = {
        "session_id": row.get("session_id"),
        "tag_name": row.get("tag_name"),
        "created_at": row.get("created_at"),
    }
    return hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def run(
    db_path: Optional[Path] = None,
    apply: bool = False,
    manifest_path: Optional[Path] = None,
    report_path: Optional[Path] = None,
    backup_dir: Optional[Path] = None,
) -> dict:
    db = Path(db_path) if db_path else default_db_path()
    pre_rows = fetch_rows(db)
    pre = scan(pre_rows)
    if manifest_path:
        checklist = load_manifest_checklist(Path(manifest_path))
    else:
        checklist = [
            {"session_id": r["session_id"], "tag_name": r["tag_name"],
             "created_at": r["created_at"]}
            for r in pre["A_rows"]
        ]
    present = recheck(pre_rows, checklist)
    checklist_keys = {row_key(t) for t in checklist}
    present_keys = {row_key(r) for r in present}
    missing = sorted(checklist_keys - present_keys)
    beyond = [
        {"session_id": r["session_id"], "tag_name": r["tag_name"],
         "created_at": r["created_at"], "rule": r["rule"]}
        for r in pre["A_rows"]
        if row_key(r) not in checklist_keys
    ]
    pre_fp = {row_key(r): row_fingerprint(r) for r in pre_rows}
    target_pairs = [row_key(r) for r in present]
    report: dict = {
        "tool": "delete_session_tags_test_residue",
        "mode": "apply" if apply else "dry-run",
        "db": str(db),
        "manifest": str(manifest_path) if manifest_path else None,
        "checklist_rows": len(checklist),
        "matched": len(target_pairs),
        "missing_checklist": [{"session_id": s, "tag_name": t} for s, t in missing],
        "beyond_checklist": beyond,
        "pre": {
            "total": pre["total"],
            "A": len(pre["A_rows"]),
            "U": len(pre["U_rows"]),
            "A_alive_sessions": sum(1 for r in pre["A_rows"] if r.get("session_alive")),
        },
        "checklist": checklist,
    }
    if not apply:
        report["deleted"] = {"rows": 0}
        _emit(report, report_path)
        return report

    report["backup"] = backup_db(db, backup_dir or (REPO / "data" / "backups"))
    report["deleted"] = {"rows": delete_exact(target_pairs, db)}

    post_rows = fetch_rows(db)
    post = scan(post_rows)
    post_keys = {row_key(r) for r in post_rows}
    still = sorted(set(target_pairs) & post_keys)
    if still:
        raise RuntimeError(f"回读对账不过：待删行仍在场 {still[:5]}")
    pre_keys = set(pre_fp)
    gone_other = sorted((pre_keys - set(target_pairs)) - post_keys)
    if gone_other:
        raise RuntimeError(f"非目标行被误删 {gone_other[:5]}")
    post_fp = {row_key(r): row_fingerprint(r) for r in post_rows}
    changed = sorted(
        key
        for key in (pre_keys - set(target_pairs))
        if key in post_keys and post_fp[key] != pre_fp[key]
    )
    if changed:
        raise RuntimeError(f"非目标行内容被改写 {changed[:5]}")
    report.update(
        {
            "post": {
                "total": post["total"],
                "A": len(post["A_rows"]),
                "U": len(post["U_rows"]),
            },
            "post_A_rows": [
                {"session_id": r["session_id"], "tag_name": r["tag_name"]}
                for r in post["A_rows"]
            ],
            "non_target_missing": gone_other,
            "non_target_changed": changed,
            "concurrent_new_rows": sorted(post_keys - pre_keys),
        }
    )
    _emit(report, report_path)
    return report


def _emit(report: dict, report_path: Optional[Path]) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    print(text)
    if report_path:
        Path(report_path).write_text(text)
        print(f"[written] {report_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(default_db_path()))
    ap.add_argument("--manifest", help="按工件清单删（缺省=fresh 扫描 A 行为清单）")
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-dir", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report")
    args = ap.parse_args()
    run(
        Path(args.db),
        apply=args.apply,
        manifest_path=Path(args.manifest) if args.manifest else None,
        report_path=Path(args.report) if args.report else None,
        backup_dir=Path(args.backup_dir),
    )


if __name__ == "__main__":
    main()
