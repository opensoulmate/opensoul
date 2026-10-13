#!/usr/bin/env python3
"""TL-⑧ 删除执行件②b-2a——backups manifests.json 悬挂测试条目按清单精确删+备份+回读。

TL-③ 清点（scripts/census_test_residue.py + data/test_residue_census_20261013.json）
定性：~/.opensoul/backups/manifests.json 5 条 A 类悬挂测试条目（test-sense-backup ×1 +
test_backup_integration ×4，tar 已不在盘=悬挂条目）。本工具按单一源判法精确删
（TL-④/⑤/⑥/⑦ 同款纪律）：
  - 判单一源：census.classify_manifest（禁发明新判法）；删前逐行复核（fresh 重读再判
    +清单名一致），现判非 A / 名错配 = 拒删一条不动（清单陈旧/错配=拒，禁删真备份）
  - 悬挂判定：tar 不在盘才删；tar 在盘=仍可用备份，拒删（禁制造"有 tar 无清单"孤儿）
  - 按清单精确删：只删工件 files.backups.A_dangling_test_entries 清单条目；
    清单外新残留单列报告不删
  - 备份：manifests.json 整文件字节快照 + sha256（删前必留，可整体回滚）
  - 回读：删后复点（待删条目全 gone、删前在场的非目标条目逐条内容不变、允许生产并发
    新条目只查删前在场项），报告逐键落 JSON 工件
默认 dry-run；--apply 才真删。写回原子（临时文件+replace，防写半截损毁 manifests）。

范围（拆件②b-2a）：manifests.json A 类悬挂条目 5 条。
拆件②b-2b+ 留待拍：测试用户 4+2 / perm 决策审计 612 / 探针组 2（各自级联语义需各自
备份+回读）。

诚实边界：判法是名字硬标识子串（test_backup_integration / test-sense-backup），非语义
理解；description 含 test 而名字无标识的条目如实判 other 不动；manifests.json 结构按
BackupManager 真源=dict{backup_id: entry}（list 形态拒读=真源不长那样，禁猜）。

用法：
  python3 scripts/delete_manifest_test_residue.py [--apply] [--report data/xxx.json]
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
    """删前复核不过 = 拒删（工件不可信 / 清单错配 / tar 在盘仍可用），一条不动。"""


def load_artifact_manifests(artifact: Path) -> dict:
    """从 TL-③ 清点工件取 manifests 悬挂清单（工件不可信=拒删）。"""
    data = json.loads(Path(artifact).read_text())
    try:
        bk = data["files"]["backups"]
        raw = bk["A_dangling_test_entries"]
        checklist = [{"backup_id": str(r["backup_id"]), "name": str(r["name"])} for r in raw]
    except (KeyError, TypeError, ValueError) as e:
        raise RefuseError(f"工件缺 files.backups.A_dangling_test_entries 清单，工件不可信，拒删：{e}") from e
    return {"A": checklist, "manifest_entries": int(bk.get("manifest_entries") or 0)}


def load_manifests(manifest_path: Path) -> dict:
    """读 manifests.json（真源=dict{backup_id: entry}；list 形态拒读禁猜）。"""
    try:
        data = json.loads(Path(manifest_path).read_text())
    except FileNotFoundError as e:
        raise RefuseError(f"manifests 不存在：{manifest_path}") from e
    except json.JSONDecodeError as e:
        raise RefuseError(f"manifests 不是合法 JSON，拒删（禁写坏真源）：{e}") from e
    if not isinstance(data, dict):
        raise RefuseError(f"manifests 顶层不是 dict（真源=BackupManager dict{{backup_id: entry}}），拒删：{type(data).__name__}")
    return data


def scan(manifests: dict) -> dict:
    """逐条分类（单一源 census.classify_manifest）。"""
    rows = []
    a_ids: list = []
    other_ids: list = []
    for bid, entry in manifests.items():
        name = str((entry or {}).get("name", "")) if isinstance(entry, dict) else ""
        cls, rule = census.classify_manifest(name)
        rows.append({"backup_id": bid, "name": name, "cls": cls, "rule": rule})
        (a_ids if cls == "A" else other_ids).append(bid)
    return {"rows": rows, "A_ids": a_ids, "other_ids": other_ids, "total": len(rows)}


def recheck(manifests: dict, checklist: list, tar_dir: Path) -> None:
    """删前逐行复核：fresh 重读每条再判（单一源）+清单名一致+tar 不在盘，否则拒删。"""
    bad = []
    for t in checklist:
        entry = manifests.get(t["backup_id"])
        if not isinstance(entry, dict):
            bad.append({"backup_id": t["backup_id"], "reason": "条目缺失/非 dict"})
            continue
        name = str(entry.get("name", ""))
        cls, rule = census.classify_manifest(name)
        if cls != "A":
            bad.append({"backup_id": t["backup_id"], "name": name,
                        "reason": f"现判非 A（{cls}/{rule}）"})
        elif name != t["name"]:
            bad.append({"backup_id": t["backup_id"], "reason":
                        f"名错配 live={name} checklist={t['name']}"})
        elif (Path(tar_dir) / f"{t['backup_id']}.tar.gz").exists():
            bad.append({"backup_id": t["backup_id"], "reason":
                        "tar 在盘仍可用（非悬挂），拒删禁制造孤儿备份文件"})
    if bad:
        raise RefuseError(f"{len(bad)} 条待删条目复核不过（清单陈旧/错配/tar 在盘），拒删：{bad[:5]}")


def backup_file(src: Path, backup_dir: Path) -> dict:
    """manifests.json 整文件字节快照 + sha256（删前必留，可整体回滚）。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = Path(backup_dir) / f"manifests_pre_delete_{time.strftime('%Y%m%d_%H%M%S')}.json"
    shutil.copyfile(src, dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    return {"path": str(dest), "sha256": digest, "bytes": dest.stat().st_size}


def delete_exact(ids: list, manifest_path: Path) -> dict:
    """按 backup_id 精确删并原子写回（临时文件+replace）。

    写回前 fresh 重读真源再删目标 id（不是把早前载入的 dict 直接覆盖写回）——
    防删的瞬间生产并发新备份条目（BackupManager.create_backup 随写随落盘）被覆盖丢失。
    """
    fresh = load_manifests(manifest_path)
    n = 0
    for bid in ids:
        if fresh.pop(bid, None) is not None:
            n += 1
    tmp = Path(manifest_path).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(fresh, indent=2))
    tmp.replace(manifest_path)
    return {"manifest_entries": n}


def entry_fingerprint(entry) -> str:
    """条目内容指纹（canonical JSON sha256，键序无关——回读对账逐条内容不变判定）。"""
    return hashlib.sha256(
        json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def run(manifest_path: Path, artifact: Path, apply: bool = False,
        backup_dir: Optional[Path] = None, report_path: Optional[Path] = None,
        tar_dir: Optional[Path] = None) -> dict:
    art = load_artifact_manifests(artifact)
    manifests = load_manifests(manifest_path)
    tdir = Path(tar_dir) if tar_dir else Path(manifest_path).parent
    pre = scan(manifests)
    checklist = art["A"]
    cids = [t["backup_id"] for t in checklist]
    live_ids = set(manifests.keys())
    missing = [i for i in cids if i not in live_ids]
    present = [t for t in checklist if t["backup_id"] in live_ids]
    if present:
        recheck(manifests, present, tdir)
    beyond = [{"backup_id": r["backup_id"], "name": r["name"]}
              for r in pre["rows"]
              if r["cls"] == "A" and r["backup_id"] not in set(cids)]
    pre_fp = {bid: entry_fingerprint(manifests[bid]) for bid in live_ids}
    target_ids = [t["backup_id"] for t in present]
    report: dict = {
        "tool": "delete_manifest_test_residue",
        "mode": "apply" if apply else "dry-run",
        "manifest": str(manifest_path), "artifact": str(artifact),
        "checklist_ids": cids, "live_A_ids": pre["A_ids"],
        "artifact_drift": sorted(cids) != sorted(pre["A_ids"]),
        "matched": len(target_ids), "missing_checklist": missing,
        "beyond_checklist": beyond,
        "pre": {"total": pre["total"], "A": len(pre["A_ids"]),
                "other": len(pre["other_ids"])},
        "deleted_ids": target_ids,
    }
    if not apply:
        report["deleted"] = {"manifest_entries": 0}
        _emit(report, report_path)
        return report

    report["backup"] = backup_file(manifest_path, backup_dir or (REPO / "data" / "backups"))
    report["deleted"] = delete_exact(target_ids, manifest_path) if target_ids else {"manifest_entries": 0}

    post = load_manifests(manifest_path)
    post_ids = set(post.keys())
    still = set(target_ids) & post_ids
    if still:
        raise RuntimeError(f"回读对账不过：待删条目仍在场 {sorted(still)}")
    gone_other = (live_ids - set(target_ids)) - post_ids
    if gone_other:
        raise RuntimeError(f"非目标条目被误删 {sorted(gone_other)}")
    changed = [bid for bid in (live_ids - set(target_ids))
               if bid in post and entry_fingerprint(post[bid]) != pre_fp[bid]]
    if changed:
        raise RuntimeError(f"非目标条目内容被改写 {sorted(changed)}")
    post_scan = scan(post)
    report.update({
        "post": {"total": post_scan["total"], "A": len(post_scan["A_ids"]),
                 "other": len(post_scan["other_ids"])},
        "post_A_ids": post_scan["A_ids"],
        "non_target_missing": sorted(gone_other),
        "non_target_changed": sorted(changed),
        "concurrent_new_entries": sorted(post_ids - live_ids),
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
    ap.add_argument("--manifest",
                    default=str(Path.home() / ".opensoul" / "backups" / "manifests.json"))
    ap.add_argument("--artifact", default=str(REPO / "data" / "test_residue_census_20261013.json"))
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-dir", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report")
    args = ap.parse_args()
    run(Path(args.manifest), Path(args.artifact), apply=args.apply,
        backup_dir=Path(args.backup_dir), report_path=Path(args.report) if args.report else None)


if __name__ == "__main__":
    main()
