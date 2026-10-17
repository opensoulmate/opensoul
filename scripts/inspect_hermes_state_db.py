#!/usr/bin/env python3
"""SESS-① 只读取证工具：~/.hermes/state.db 损坏缺陷决策材料件（只读，零写入零处置）。

背景（GLAND-④ 留档候选首件）：PATCH /api/sessions 恒 500，根因指向
~/.hermes/state.db 3.87GB 无 SQLite 魔数损坏；该文件在 ~/opensoul 之外
（Hermes 平台自身 SessionDB），处置需拍板。本工具只采集事实不动数据：
①文件本体（魔数/大小/mtime/WAL 状态）
②恢复源盘点（~/.hermes/sessions/*.jsonl 会话正文真源、sessions.db、
  hermes-agent 侧 state.db 修复工具链是否在场）
③blast radius 静态面（opensoul 侧消费点 file:line）

用法：python3 scripts/inspect_hermes_state_db.py [--json 输出路径]
输出：JSON 工件（默认 data/sessions_db_forensics.json）+ stdout 摘要。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def inspect_file(path: Path) -> dict:
    st = path.stat() if path.exists() else None
    out = {
        "path": str(path),
        "exists": st is not None,
    }
    if st is None:
        return out
    with path.open("rb") as f:
        head = f.read(32)
        f.seek(max(0, st.st_size - 32))
        tail = f.read(32)
    out.update(
        {
            "size_bytes": st.st_size,
            "mtime": _iso(st.st_mtime),
            "head_hex": head.hex(),
            "tail_hex": tail.hex(),
            "sqlite_magic": head.startswith(SQLITE_MAGIC),
            "looks_like_sqlite": head.startswith(b"SQLite format 3"),
        }
    )
    if not out["looks_like_sqlite"] and st.st_size > 0:
        out["content_sample"] = sample_content(path, st.st_size)
    return out


def sample_content(path: Path, size: int) -> dict:
    """只读内容抽样：帮拍板判断「文件是什么/数据是否可救」——不改文件一个字节。"""
    import math

    offsets = sorted({0, size // 3, (2 * size) // 3, max(0, size - 1_048_576)})
    blocks = []
    entropy = []
    strings_found = []
    with path.open("rb") as f:
        for off in offsets:
            f.seek(off)
            blk = f.read(1_048_576)
            blocks.append({"offset": off, "head_hex": blk[:32].hex()})
            if blk:
                counts: dict[int, int] = {}
                for b in blk:
                    counts[b] = counts.get(b, 0) + 1
                n = len(blk)
                entropy.append(
                    round(-sum((c / n) * math.log2(c / n) for c in counts.values()), 3)
                )
                for m in re.findall(rb"[\x20-\x7e]{12,}", blk)[:8]:
                    strings_found.append(m.decode("ascii", "replace")[:120])
    return {"sample_blocks": blocks, "entropy_per_1mb": entropy, "strings": strings_found}


def session_jsonl_facts(sessions_dir: Path) -> dict:
    if not sessions_dir.is_dir():
        return {"dir": str(sessions_dir), "exists": False}
    all_files = [p for p in sessions_dir.iterdir() if p.is_file()]
    by_suffix: dict[str, int] = {}
    for p in all_files:
        by_suffix[p.suffix or "<none>"] = by_suffix.get(p.suffix or "<none>", 0) + 1
    files = sorted(sessions_dir.glob("*.jsonl"))
    total = 0
    newest = None
    bad = 0
    for p in files:
        try:
            st = p.stat()
            total += st.st_size
            if newest is None or st.st_mtime > newest:
                newest = st.st_mtime
            with p.open("rb") as f:
                if f.read(1) != b"{":
                    bad += 1
        except OSError:
            bad += 1
    newest_any = None
    total_all = 0
    for p in all_files:
        try:
            st = p.stat()
            total_all += st.st_size
            if newest_any is None or st.st_mtime > newest_any:
                newest_any = st.st_mtime
        except OSError:
            continue
    return {
        "dir": str(sessions_dir),
        "exists": True,
        "file_count_total": len(all_files),
        "by_suffix": by_suffix,
        "total_bytes_all": total_all,
        "newest_mtime_any": _iso(newest_any) if newest_any else None,
        "jsonl_count": len(files),
        "jsonl_total_bytes": total,
        "jsonl_newest_mtime": _iso(newest) if newest else None,
        "non_jsonl_shaped": bad,
    }


def opensoul_consumers(repo: Path) -> dict:
    """静态 blast radius：opensoul 侧读 state.db 的消费点（只读源码扫描）。"""
    hits = []
    pat = re.compile(r"state\.db")
    for sub in ("src",):
        for p in (repo / sub).rglob("*.py"):
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if pat.search(line):
                        hits.append(f"{p.relative_to(repo)}:{i}:{line.strip()[:120]}")
            except OSError:
                continue
    return {"consumer_lines": hits}


def hermes_toolchain_facts(hermes_agent: Path) -> dict:
    """Hermes 侧是否自带 state.db 修复/重建工具链（只读存在性盘点）。"""
    markers = []
    if hermes_agent.is_dir():
        for name in (
            "hermes_state.py",
            "hermes_state_portability.py",
            "hermes_state_schema.py",
            "hermes_state_search.py",
        ):
            p = hermes_agent / name
            markers.append({"file": name, "exists": p.exists()})
        repair_tests = sorted(
            str(p.name) for p in (hermes_agent / "tests").glob("*state_db*") if (hermes_agent / "tests").is_dir()
        )
    else:
        repair_tests = []
    return {"hermes_agent_dir": str(hermes_agent), "modules": markers, "state_db_tests": repair_tests}


def page_probe(path: Path, size: int) -> dict:
    """只读页界探针：判断「SQLite 内页是否完好、只是头被砸」——可救性核心证据。

    只做顺序读+边界首字节统计，不写不改：若大量页界首字节 ∈ {0x02,0x05,0x0a,0x0d}
    （SQLite b-tree 页型）且按某 page_size 对齐，说明内页结构在=数据大概率可抽取。
    """
    page_types = {0x02, 0x05, 0x0A, 0x0D}
    out = {}
    schema_hit = None
    block_size = 8 * 1024 * 1024
    for page_size in (4096, 8192):
        hit = 0
        total = 0
        with path.open("rb") as f:
            off = 0
            while off < size:
                blk = f.read(block_size)
                if not blk:
                    break
                if schema_hit is None:
                    idx = blk.find(b"CREATE TABLE")
                    if idx >= 0:
                        schema_hit = off + idx
                # 块内页界位置：首个满足 (off+i) % page_size == 0 且 abs>0 的 i，步进 page_size
                i = (page_size - (off % page_size)) % page_size
                while i < len(blk):
                    abs_off = off + i
                    if abs_off > 0:
                        total += 1
                        if blk[i] in page_types:
                            hit += 1
                    i += page_size
                off += len(blk)
        out[f"page_size_{page_size}"] = {
            "boundaries_total": total,
            "btree_type_hits": hit,
            "hit_ratio": round(hit / total, 4) if total else None,
        }
    out["create_table_string_offset"] = schema_hit
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None, help="输出工件路径（默认 data/sessions_db_forensics.json）")
    ap.add_argument("--hermes-home", default=os.path.expanduser("~/.hermes"))
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--deep", action="store_true", help="页界探针（顺序读全文件，只读）")
    args = ap.parse_args()

    home = Path(args.hermes_home)
    repo = Path(args.repo)
    out_path = Path(args.json) if args.json else repo / "data" / "sessions_db_forensics.json"

    facts = {
        "generated_at": datetime.now(tz=timezone.utc).astimezone().isoformat(timespec="seconds"),
        "state_db": inspect_file(home / "state.db"),
        "state_db_wal": inspect_file(home / "state.db-wal"),
        "sessions_db_legacy": inspect_file(home / "sessions.db"),
        "sessions_jsonl": session_jsonl_facts(home / "sessions"),
        "opensoul_consumers": opensoul_consumers(repo),
        "hermes_toolchain": hermes_toolchain_facts(home / "hermes-agent"),
    }
    if args.deep and facts["state_db"].get("exists") and not facts["state_db"].get("looks_like_sqlite"):
        facts["state_db"]["page_probe"] = page_probe(home / "state.db", facts["state_db"]["size_bytes"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")

    sd = facts["state_db"]
    print(f"state.db exists={sd['exists']} size={sd.get('size_bytes')} sqlite_magic={sd.get('sqlite_magic')}")
    print(f"  head_hex={sd.get('head_hex')}")
    print(f"  mtime={sd.get('mtime')}")
    sj = facts["sessions_jsonl"]
    print(
        f"sessions/ files={sj.get('file_count_total')} by_suffix={sj.get('by_suffix')} "
        f"total_bytes={sj.get('total_bytes_all')} newest={sj.get('newest_mtime_any')}"
    )
    print(
        f"sessions/*.jsonl count={sj.get('jsonl_count')} total_bytes={sj.get('jsonl_total_bytes')} "
        f"newest={sj.get('jsonl_newest_mtime')}"
    )
    print(f"opensoul consumer lines={len(facts['opensoul_consumers']['consumer_lines'])}")
    if "page_probe" in sd:
        print(f"page_probe={sd['page_probe']}")
    print(f"artifact -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
