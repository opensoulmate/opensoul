#!/usr/bin/env python3
"""TL-⑬ 删除执行件②d——~/.opensoul/exports 测试残留导出文件按清单精确删+备份+回读。

TL-③ 清点清单列过「exports 308」残留面，但 TL-④~⑫ 各拆件清单漏记此面（TL-④ 拆件②
清单只列 knowledge/测试用户/e2e 租户/perm 审计/manifests/探针组）——本件补收漏网面。
定性（生产文件面实证）：~/.opensoul/exports 309 个 *.json 全为测试残留——
  308 × export_export_<ts>.json（55 字节，内容逐字节同 tests/test_marrow.py:75
        fixture [{"type":"knowledge","name":"test"}]）
  1 × test-export_export_<ts>.json（文件名硬标识 test- 前缀 + test/test2 探针内容）
本工具按单一源判法精确删（TL-④~⑫ 同款纪律）：
  - 判单一源：census.classify_export（禁发明新判法）
  - 按清单精确删：只删工件 files.exports.A_files 清单文件；清单外同判据新残留
    一并删但单列报告（beyond_checklist）
  - 删前逐行复核：fresh 重读再判（单一源），现判非 A = 拒删一条不动（清单陈旧/
    错配=拒，禁删真导出）；清单缺行如实报非致命（幂等重跑）；清单含路径穿越名
    （非纯文件名）= 工件不可信拒删
  - 备份：目标文件逐个字节快照拷贝 + 逐文件 sha256 清单（删前必留，可整体回滚）
  - 回读：待删文件全 gone、删前在场非目标文件逐个 sha256 不变且一个不少、
    总数算术对平、允许生产并发新导出只查删前在场文件
默认 dry-run 零写入；--apply 才真删。

诚实边界：判法=文件名硬标识（test- 前缀）+内容硬标识（entry.name 全为
A_EXPORT_ENTRY_NAMES 测试字面），非语义理解；默认名 export_export_* 本身刻意
不作标识（真实用户走 /api/marrow/export 缺省 name=export 同名），只删内容/名字
有硬标识的文件；解析失败/顶层非 list/空 list 如实判 other 不动。

用法：
  python3 scripts/delete_export_test_residue.py [--apply] [--report data/xxx.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
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
    """删前复核不过 = 拒删（工件不可信 / 清单错配 / 现判非 A），一条不动。"""


def load_artifact_names(artifact: Path) -> dict:
    """从清点工件取 exports A 文件清单（工件缺键=拒删）。"""
    data = json.loads(Path(artifact).read_text())
    try:
        ex = data["files"]["exports"]
        checklist = [str(r) for r in ex["A_files"]]
    except (KeyError, TypeError, ValueError) as e:
        raise RefuseError(f"工件缺 files.exports.A_files 清单，工件不可信，拒删：{e}") from e
    for n in checklist:
        if not n or Path(n).name != n:
            raise RefuseError(f"清单含非纯文件名（路径穿越风险），工件不可信，拒删：{n!r}")
    return {"A": checklist, "artifact_total": int(ex.get("total") or 0)}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def scan(export_dir: Path) -> dict:
    """逐文件分类（单一源 census.classify_export）。"""
    rows = []
    a_names = []
    other_names = []
    for p in sorted(Path(export_dir).glob("*.json")):
        cls, rule = census.classify_export(p.name, _read_text(p))
        rows.append({"name": p.name, "cls": cls, "rule": rule})
        (a_names if cls == "A" else other_names).append(p.name)
    return {"rows": rows, "A_names": a_names, "other_names": other_names, "total": len(rows)}


def recheck(export_dir: Path, names: list) -> None:
    """删前逐行复核：fresh 重读每个文件再判（单一源），现判非 A = 拒删一条不动。"""
    bad = []
    for n in names:
        p = Path(export_dir) / n
        if not p.exists():
            continue  # 清单缺行=非致命（幂等重跑），run() 单列 missing
        cls, rule = census.classify_export(n, _read_text(p))
        if cls != "A":
            bad.append({"name": n, "reason": f"现判非 A（{cls}/{rule}）"})
    if bad:
        raise RefuseError(f"{len(bad)} 个待删文件复核不过（清单陈旧/内容已变），拒删：{bad[:5]}")


def backup_files(names: list, export_dir: Path, backup_root: Path) -> dict:
    """目标文件逐个字节快照拷贝 + 逐文件 sha256（删前必留，可整体回滚=拷回）。"""
    dest_dir = Path(backup_root) / f"exports_pre_delete_{time.strftime('%Y%m%d_%H%M%S')}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for n in names:
        src = Path(export_dir) / n
        if not src.exists():
            continue
        shutil.copyfile(src, dest_dir / n)
        entries.append({"name": n, "bytes": src.stat().st_size,
                        "sha256": hashlib.sha256(src.read_bytes()).hexdigest()})
    manifest = dest_dir / "backup_manifest.json"
    manifest.write_text(json.dumps(entries, ensure_ascii=False, indent=1))
    return {"path": str(dest_dir), "files": len(entries),
            "total_bytes": sum(e["bytes"] for e in entries),
            "manifest": str(manifest),
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}


def delete_exact(names: list, export_dir: Path) -> dict:
    """按文件名精确删（逐个 unlink，禁 glob 扫删）。"""
    n = 0
    for name in names:
        p = Path(export_dir) / name
        if p.exists():
            p.unlink()
            n += 1
    return {"files": n}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(export_dir: Path, artifact: Path, apply: bool = False,
        backup_root: Optional[Path] = None, report_path: Optional[Path] = None) -> dict:
    art = load_artifact_names(artifact)
    pre = scan(export_dir)
    pre_sha = {p.name: _sha(p) for p in sorted(Path(export_dir).glob("*.json"))}
    checklist = art["A"]
    live = set(pre_sha)
    missing = [n for n in checklist if n not in live]
    present = [n for n in checklist if n in live]
    if present:
        recheck(export_dir, present)
    beyond = [n for n in pre["A_names"] if n not in set(checklist)]
    targets = present + beyond
    report: dict = {
        "tool": "delete_export_test_residue",
        "mode": "apply" if apply else "dry-run",
        "export_dir": str(export_dir), "artifact": str(artifact),
        "checklist_names": checklist, "live_A_names": pre["A_names"],
        "artifact_drift": sorted(checklist) != sorted(pre["A_names"]),
        "matched": len(present), "missing_checklist": missing,
        "beyond_checklist": beyond,
        "pre": {"total": pre["total"], "A": len(pre["A_names"]),
                "other": len(pre["other_names"])},
        "deleted_names": targets,
    }
    if not apply:
        report["deleted"] = {"files": 0}
        _emit(report, report_path)
        return report

    report["backup"] = backup_files(targets, export_dir, backup_root or (REPO / "data" / "backups"))
    report["deleted"] = delete_exact(targets, export_dir) if targets else {"files": 0}

    post_names = {p.name for p in Path(export_dir).glob("*.json")}
    still = set(targets) & post_names
    if still:
        raise RuntimeError(f"回读对账不过：待删文件仍在场 {sorted(still)}")
    gone_other = (live - set(targets)) - post_names
    if gone_other:
        raise RuntimeError(f"非目标文件被误删 {sorted(gone_other)}")
    changed = [n for n in (live - set(targets))
               if n in post_names and _sha(Path(export_dir) / n) != pre_sha[n]]
    if changed:
        raise RuntimeError(f"非目标文件内容被改写 {sorted(changed)}")
    concurrent_new = sorted(post_names - live)
    post = scan(export_dir)
    reconcile_ok = post["total"] == pre["total"] - report["deleted"]["files"] + len(concurrent_new)
    if not reconcile_ok:
        raise RuntimeError(
            f"总数算术不对平：post {post['total']} != pre {pre['total']} "
            f"- deleted {report['deleted']['files']} + concurrent_new {len(concurrent_new)}")
    report.update({
        "post": {"total": post["total"], "A": len(post["A_names"]),
                 "other": len(post["other_names"])},
        "post_A_names": post["A_names"],
        "non_target_missing": sorted(gone_other),
        "non_target_changed": changed,
        "concurrent_new_files": concurrent_new,
        "reconcile_ok": reconcile_ok,
    })
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
    ap.add_argument("--dir", default=str(Path.home() / ".opensoul" / "exports"))
    ap.add_argument("--artifact",
                    default=str(REPO / "data" / "test_residue_census_20261014_pre_tl13.json"))
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-root", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report")
    args = ap.parse_args()
    run(Path(args.dir), Path(args.artifact), apply=args.apply,
        backup_root=Path(args.backup_root),
        report_path=Path(args.report) if args.report else None)


if __name__ == "__main__":
    main()
