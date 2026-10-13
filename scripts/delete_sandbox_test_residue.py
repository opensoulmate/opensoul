#!/usr/bin/env python3
"""TL-⑮ 删除执行件③——~/.opensoul/sandboxes 沙箱残留目录按 TL-⑭ 证据清单删 + tar 备份 + 回读。

TL-⑭ 已定性（内容级+出处级清点）：4 个 sandbox-<hex> 目录全部为功能开发期验证产物
（目录 mtime 与 feature commit 提交时刻秒级吻合）、零用户数据（3 空目录 + 1x155B
演示快照 snap-8354c13d），删除零数据损失（tar+sha256 备份可整体回滚）。名字无硬
标识按纪律仍判 other（census.classify_sandbox 语义逐字保留），故本工具的删除依据
= TL-⑭ 证据清单（工件 files.sandboxes.entries 的内容事实）而非名字判 A。

本工具按 TL-④~⑬ 同款纪律：
  - 判单一源：census._tree 无关；分类/清点走 census.classify_sandbox /
    census 同源词表（禁发明新判法、禁手抄）
  - 按清单精确删：只删工件 files.sandboxes.entries 清单目录；清单外新目录只有
    census.classify_sandbox 判 A（目录名/内含文件名硬标识）才一并删且单列报告
    （beyond_checklist）；判 other 的新目录如实不动（可能是真实用户沙箱）
  - 删前逐目复核（证据仍在场才删）：fresh 全树清点（rglob 含子目录文件），
    文件清单与总字节必须与 TL-⑭ 记录逐值一致——目录内容已变（用户加了数据/
    新快照/子目录新文件）= 零用户数据证据不再成立，拒删一条不动；清单缺目录
    如实报非致命（幂等重跑）
  - 备份：目标目录 tar 全量归档（字节级可整体回滚=解包还原）+ 逐文件 sha256
    清单 + tar 自身 sha256（删前必留）
  - 回读：待删目录全 gone、删前在场非目标目录逐文件 sha256 不变且一个不少、
    总数算术对平、允许生产并发新建沙箱只查删前在场目录
  - 备份与报告默认落 data/backups、data/（gitignore 本地工件）
默认 dry-run 零写入；--apply 才真删。

诚实边界：删除依据是 TL-⑭ 证据（内容零用户数据 + git 出处比对）而非名字判 A，
  是对 TL-③「U 类不动」字面纪律的显式例外（TL-⑭「删除零数据损失待拍板」已拍）；
  名字无硬标识的真实用户沙箱若恰与清单同名且内容逐值同形则无法区分（如实记）；
  timeline 引用事件（sandbox-bcf4e66d 的 evt_1787006920738）不动（事件史非本件
  范围）；SandboxManager 状态为进程内存（无持久注册表），删目录即彻底，无需
  注册表清理。

用法：
  python3 scripts/delete_sandbox_test_residue.py [--apply] [--report data/xxx.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
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
    """删前复核不过 = 拒删（工件不可信 / 清单错配 / 证据不再成立），一条不动。"""


def load_artifact_entries(artifact: Path) -> dict:
    """从 TL-⑭ 清点工件取 sandboxes 证据清单（工件缺键/形状非法=拒删）。"""
    data = json.loads(Path(artifact).read_text())
    try:
        sb = data["files"]["sandboxes"]
        entries = sb["entries"]
    except (KeyError, TypeError, ValueError) as e:
        raise RefuseError(f"工件缺 files.sandboxes.entries 清单，工件不可信，拒删：{e}") from e
    if not isinstance(entries, list) or not entries:
        raise RefuseError("工件 files.sandboxes.entries 非非空 list，工件不可信，拒删")
    out = []
    for e in entries:
        if not isinstance(e, dict):
            raise RefuseError(f"清单条目非 dict，工件不可信，拒删：{e!r}")
        name = e.get("name")
        if not name or not isinstance(name, str) or Path(name).name != name:
            raise RefuseError(f"清单含非纯目录名（路径穿越风险），工件不可信，拒删：{name!r}")
        files = e.get("files")
        if not isinstance(files, list) or any(
            not isinstance(f, str) or not f or Path(f).name != f for f in files
        ):
            raise RefuseError(f"清单 {name} 文件清单非法（须纯文件名 list），工件不可信，拒删")
        total_bytes = e.get("total_bytes")
        if not isinstance(total_bytes, int) or isinstance(total_bytes, bool) or total_bytes < 0:
            raise RefuseError(f"清单 {name} total_bytes 非法，工件不可信，拒删：{total_bytes!r}")
        out.append({"name": name, "files": sorted(files), "total_bytes": total_bytes})
    return {"entries": out, "artifact_total": int(sb.get("total") or 0)}


def _tree_inventory(path: Path) -> dict:
    """全树文件清单+总字节（只读事实；rglob 含子目录文件——证据必须覆盖全树）。"""
    files = []
    total = 0
    for p in sorted(Path(path).rglob("*")):
        if p.is_file():
            files.append(p.relative_to(path).as_posix())
            total += p.stat().st_size
    return {"files": sorted(files), "total_bytes": total}


def _tree_sha(path: Path) -> dict:
    """全树逐文件 sha256（回读对账指纹）。"""
    out = {}
    for p in sorted(Path(path).rglob("*")):
        if p.is_file():
            out[p.relative_to(path).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def scan(sandbox_dir: Path) -> dict:
    """逐目录全树清点+分类（单一源 census.classify_sandbox）。"""
    rows = {}
    names = []
    base = Path(sandbox_dir)
    for p in sorted(base.iterdir()) if base.is_dir() else []:
        if not p.is_dir():
            continue
        inv = _tree_inventory(p)
        cls, rule = census.classify_sandbox(p.name, [Path(f).name for f in inv["files"]])
        rows[p.name] = {"name": p.name, "cls": cls, "rule": rule, **inv}
        names.append(p.name)
    return {"rows": rows, "names": sorted(names), "total": len(names)}


def recheck(sandbox_dir: Path, entries: list) -> None:
    """删前逐目复核（证据仍在场才删）：fresh 全树清点必须与 TL-⑭ 记录逐值一致。"""
    bad = []
    for e in entries:
        p = Path(sandbox_dir) / e["name"]
        if not p.is_dir():
            continue  # 清单缺目录=非致命（幂等重跑），run() 单列 missing
        inv = _tree_inventory(p)
        if inv["files"] != e["files"] or inv["total_bytes"] != e["total_bytes"]:
            bad.append({
                "name": e["name"],
                "reason": "内容已变（TL-⑭ 零用户数据证据不再成立）",
                "recorded": {"files": e["files"], "total_bytes": e["total_bytes"]},
                "fresh": inv,
            })
    if bad:
        raise RefuseError(f"{len(bad)} 个待删目录复核不过（清单陈旧/内容已变），拒删：{bad[:3]}")


def backup_dirs(names: list, sandbox_dir: Path, backup_root: Path) -> dict:
    """目标目录 tar 全量归档+逐文件 sha256 清单+tar 自身 sha256（删前必留，可整体回滚=解包还原）。"""
    dest_dir = Path(backup_root) / f"sandboxes_pre_delete_{time.strftime('%Y%m%d_%H%M%S')}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    tar_path = dest_dir / "sandboxes.tar.gz"
    manifest_entries = []
    with tarfile.open(tar_path, "w:gz") as tf:
        for n in names:
            src = Path(sandbox_dir) / n
            if not src.is_dir():
                continue
            tf.add(src, arcname=n)
            inv = _tree_inventory(src)
            manifest_entries.append({
                "name": n, "files": inv["files"], "total_bytes": inv["total_bytes"],
                "sha256": _tree_sha(src),
            })
    manifest = dest_dir / "backup_manifest.json"
    manifest.write_text(json.dumps(manifest_entries, ensure_ascii=False, indent=1))
    return {
        "path": str(dest_dir), "tar": str(tar_path),
        "tar_sha256": hashlib.sha256(tar_path.read_bytes()).hexdigest(),
        "dirs": len(manifest_entries),
        "total_bytes": sum(e["total_bytes"] for e in manifest_entries),
        "manifest": str(manifest),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }


def delete_exact(names: list, sandbox_dir: Path) -> dict:
    """按目录名精确删（逐个 rmtree，禁 glob 扫删；根外路径=拒删）。"""
    root = Path(sandbox_dir).resolve()
    n = 0
    for name in names:
        p = Path(sandbox_dir) / name
        if not p.is_dir():
            continue
        if p.resolve().parent != root:
            raise RefuseError(f"待删目录不在沙箱根下（路径穿越风险），拒删：{name!r}")
        shutil.rmtree(p)
        n += 1
    return {"dirs": n}


def run(sandbox_dir: Path, artifact: Path, apply: bool = False,
        backup_root: Optional[Path] = None, report_path: Optional[Path] = None) -> dict:
    art = load_artifact_entries(artifact)
    pre = scan(sandbox_dir)
    pre_sha = {n: _tree_sha(Path(sandbox_dir) / n) for n in pre["names"]}
    checklist = [e["name"] for e in art["entries"]]
    live = set(pre["names"])
    missing = [n for n in checklist if n not in live]
    present_entries = [e for e in art["entries"] if e["name"] in live]
    if present_entries:
        recheck(sandbox_dir, present_entries)
    beyond = [n for n in pre["names"] if n not in set(checklist) and pre["rows"][n]["cls"] == "A"]
    targets = [e["name"] for e in present_entries] + beyond
    report: dict = {
        "tool": "delete_sandbox_test_residue",
        "mode": "apply" if apply else "dry-run",
        "sandbox_dir": str(sandbox_dir), "artifact": str(artifact),
        "checklist_names": checklist, "live_names": pre["names"],
        "artifact_drift": sorted(checklist) != sorted(pre["names"]),
        "matched": len(present_entries), "missing_checklist": missing,
        "beyond_checklist": beyond,
        "pre": {"total": pre["total"],
                "A": sum(1 for r in pre["rows"].values() if r["cls"] == "A"),
                "other": sum(1 for r in pre["rows"].values() if r["cls"] != "A")},
        "deleted_names": targets,
    }
    if not apply:
        report["deleted"] = {"dirs": 0}
        _emit(report, report_path)
        return report

    report["backup"] = backup_dirs(targets, sandbox_dir, backup_root or (REPO / "data" / "backups"))
    report["deleted"] = delete_exact(targets, sandbox_dir) if targets else {"dirs": 0}

    post = scan(sandbox_dir)
    post_names = set(post["names"])
    still = set(targets) & post_names
    if still:
        raise RuntimeError(f"回读对账不过：待删目录仍在场 {sorted(still)}")
    gone_other = (live - set(targets)) - post_names
    if gone_other:
        raise RuntimeError(f"非目标目录被误删 {sorted(gone_other)}")
    changed = [n for n in sorted(live - set(targets))
               if n in post_names and _tree_sha(Path(sandbox_dir) / n) != pre_sha[n]]
    if changed:
        raise RuntimeError(f"非目标目录内容被改写 {changed}")
    concurrent_new = sorted(post_names - live)
    reconcile_ok = post["total"] == pre["total"] - report["deleted"]["dirs"] + len(concurrent_new)
    if not reconcile_ok:
        raise RuntimeError(
            f"总数算术不对平：post {post['total']} != pre {pre['total']} "
            f"- deleted {report['deleted']['dirs']} + concurrent_new {len(concurrent_new)}")
    report.update({
        "post": {"total": post["total"],
                 "A": sum(1 for r in post["rows"].values() if r["cls"] == "A"),
                 "other": sum(1 for r in post["rows"].values() if r["cls"] != "A")},
        "post_names": post["names"],
        "non_target_missing": sorted(gone_other),
        "non_target_changed": changed,
        "concurrent_new_dirs": concurrent_new,
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
    ap.add_argument("--dir", default=str(Path.home() / ".opensoul" / "sandboxes"))
    ap.add_argument("--artifact",
                    default=str(REPO / "data" / "test_residue_census_20261014_post_tl14.json"))
    ap.add_argument("--apply", action="store_true", help="真删（缺省 dry-run 零写入）")
    ap.add_argument("--backup-root", default=str(REPO / "data" / "backups"))
    ap.add_argument("--report")
    args = ap.parse_args()
    run(Path(args.dir), Path(args.artifact), apply=args.apply,
        backup_root=Path(args.backup_root),
        report_path=Path(args.report) if args.report else None)


if __name__ == "__main__":
    main()
