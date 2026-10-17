"""SESS-① 取证工具 tests/test_inspect_hermes_state_db.py——只读取证脚本的 tmp 隔离单测。

全 tmp 绝不读生产 ~/.hermes（各用例自带 tmp 路径），跑生产取证=scripts/inspect_hermes_state_db.py。
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "inspect_hermes_state_db.py"
_spec = importlib.util.spec_from_file_location("inspect_hermes_state_db", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

SQLITE_MAGIC = b"SQLite format 3\x00"


def test_inspect_file_sqlite_magic(tmp_path: Path):
    p = tmp_path / "good.db"
    p.write_bytes(SQLITE_MAGIC + b"\x00" * 200)
    out = mod.inspect_file(p)
    assert out["exists"] is True
    assert out["sqlite_magic"] is True
    assert out["looks_like_sqlite"] is True
    # 真 SQLite 形态不产 content_sample（只对非 SQLite 文件抽样）
    assert "content_sample" not in out


def test_inspect_file_notadb_samples_content(tmp_path: Path):
    p = tmp_path / "smashed.db"
    p.write_bytes(b"\x0d\x00\x00\x00\x01\x01" + b"CREATE TABLE sessions(id)" + b"\x00" * 100)
    out = mod.inspect_file(p)
    assert out["sqlite_magic"] is False
    assert out["looks_like_sqlite"] is False
    assert out["head_hex"].startswith("0d0000000101")
    assert out["content_sample"]["strings"], "非 SQLite 文件应产出可读串抽样"


def test_inspect_file_missing(tmp_path: Path):
    out = mod.inspect_file(tmp_path / "nope.db")
    assert out == {"path": str(tmp_path / "nope.db"), "exists": False}


def test_page_probe_true_sqlite_pages_align(tmp_path: Path):
    """真 SQLite 多页库：4096 页界首字节大量为 b-tree 页型=判据在真库上成立。"""
    p = tmp_path / "real.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE t(x TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [(("payload-" + "x" * 200),) for _ in range(4000)])
    conn.commit()
    conn.close()
    out = mod.page_probe(p, p.stat().st_size)
    probe = out["page_size_4096"]
    assert probe["boundaries_total"] > 0
    assert probe["hit_ratio"] > 0.5, f"真库页界应对齐 b-tree 页型，got {probe}"
    assert out["create_table_string_offset"] is not None, "真库应含 CREATE TABLE 串"


def test_page_probe_smashed_header_still_sees_pages(tmp_path: Path):
    """头被砸（首 16 字节换掉）的真库副本：内页判据仍在=「头砸内页完好」可探测。"""
    src = tmp_path / "real.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE t(x TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [(("payload-" + "y" * 200),) for _ in range(4000)])
    conn.commit()
    conn.close()
    smashed = tmp_path / "smashed.db"
    data = bytearray(src.read_bytes())
    data[:16] = b"\x0d\x00\x00\x00\x01\x01\x61\x00" + b"\x00" * 8
    smashed.write_bytes(bytes(data))
    out = mod.page_probe(smashed, smashed.stat().st_size)
    assert out["page_size_4096"]["hit_ratio"] > 0.5
    assert out["create_table_string_offset"] is not None


def test_page_probe_random_bytes_low_hit(tmp_path: Path):
    """随机字节对照组：页界命中率应显著低于真库=判据非恒真（禁自嗨断言）。"""
    import random

    rng = random.Random(42)
    p = tmp_path / "noise.bin"
    p.write_bytes(bytes(rng.randrange(256) for _ in range(4096 * 64)))
    out = mod.page_probe(p, p.stat().st_size)
    assert out["page_size_4096"]["hit_ratio"] < 0.2


def test_session_jsonl_facts_by_suffix(tmp_path: Path):
    d = tmp_path / "sessions"
    d.mkdir()
    (d / "a.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (d / "b.json").write_text("{}", encoding="utf-8")
    (d / "c.json").write_text("{}", encoding="utf-8")
    out = mod.session_jsonl_facts(d)
    assert out["exists"] is True
    assert out["file_count_total"] == 3
    assert out["by_suffix"] == {".jsonl": 1, ".json": 2}
    assert out["jsonl_count"] == 1
    assert out["newest_mtime_any"] is not None


def test_session_jsonl_facts_missing_dir(tmp_path: Path):
    out = mod.session_jsonl_facts(tmp_path / "nope")
    assert out["exists"] is False


def test_opensoul_consumers_scans_tmp_repo(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text('_DB_PATH = "~/.hermes/state.db"\n', encoding="utf-8")
    (tmp_path / "src" / "y.py").write_text("unrelated = 1\n", encoding="utf-8")
    out = mod.opensoul_consumers(tmp_path)
    assert len(out["consumer_lines"]) == 1
    assert out["consumer_lines"][0].startswith("src/x.py:1:")


def test_hermes_toolchain_facts_missing_dir(tmp_path: Path):
    out = mod.hermes_toolchain_facts(tmp_path / "nope")
    assert out["hermes_agent_dir"].endswith("nope")
    assert all(m["exists"] is False for m in out["modules"])
    assert out["state_db_tests"] == []
