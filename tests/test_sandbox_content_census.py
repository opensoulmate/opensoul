"""TL-⑭ sandboxes 内容级清点测试——全 tmp 绝不碰生产 ~/.opensoul/sandboxes。

覆盖：classify_sandbox 纯函数（目录名/文件名硬标识判 A、随机 id 目录如实 other）、
inspect_sandbox_dir 内容事实（文件/字节/快照元数据解析与解析失败如实标注）、
sandbox_timeline_xref 引用计数、census_files 收编（加性键真消费分类+内容事实）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
for _p in (str(SCRIPTS), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import census_test_residue as census  # noqa: E402

SNAP = {
    "snapshot_id": "snap-8354c13d",
    "name": "initial",
    "created_at": 1786674811.2548223,
    "variables": {"env": "staging"},
    "log_length": 1,
}


def make_sandboxes(tmp_path: Path) -> Path:
    sdir = tmp_path / ".opensoul" / "sandboxes"
    sdir.mkdir(parents=True)
    return sdir


def make_timeline(tmp_path: Path, rows) -> sqlite3.Connection:
    db = tmp_path / "data" / "timeline.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE events (event_id TEXT PRIMARY KEY, organ TEXT, emoji TEXT,"
        " event_type TEXT, summary TEXT, detail TEXT, timestamp REAL,"
        " collected_at REAL, tenant_id TEXT)"
    )
    conn.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


class TestClassifySandbox:
    def test_random_hex_names_other(self):
        for n in ("sandbox-2ce51cab", "sandbox-36ab8f8a", "sandbox-3f79b4e6", "sandbox-bcf4e66d"):
            assert census.classify_sandbox(n, [])[0] == "other"

    def test_dir_name_hard_marker_a(self):
        assert census.classify_sandbox("sandbox-test-abc", []) == (
            "A",
            "A_SANDBOX_NAME_MARKERS:test",
        )

    def test_file_name_hard_marker_a(self):
        cls, rule = census.classify_sandbox("sandbox-2ce51cab", ["snap-1.json", "test.txt"])
        assert cls == "A"
        assert rule.startswith("A_SANDBOX_NAME_MARKERS:")

    def test_case_insensitive_marker(self):
        assert census.classify_sandbox("SANDBOX-E2E-1", [])[0] == "A"

    def test_real_snapshot_file_name_not_marker(self):
        # 生产快照名 snap-<hex>.json 无硬标识——判法不误伤（TL-⑬ 同款宁窄勿滥）
        assert census.classify_sandbox("sandbox-3f79b4e6", ["snap-8354c13d.json"])[0] == "other"

    def test_empty_inputs_other(self):
        assert census.classify_sandbox("", None) == ("other", "unmatched")


class TestInspectSandboxDir:
    def test_empty_dir_facts(self, tmp_path):
        d = tmp_path / "sandbox-aaaa0001"
        d.mkdir()
        e = census.inspect_sandbox_dir(d)
        assert e["name"] == "sandbox-aaaa0001"
        assert e["file_count"] == 0
        assert e["total_bytes"] == 0
        assert e["files"] == []
        assert e["snapshots"] == []
        assert e["cls"] == "other"
        assert e["dir_mtime"] is not None

    def test_files_and_bytes_counted(self, tmp_path):
        d = tmp_path / "sandbox-aaaa0002"
        d.mkdir()
        (d / "a.txt").write_text("12345")
        (d / "b.bin").write_text("123")
        e = census.inspect_sandbox_dir(d)
        assert e["file_count"] == 2
        assert e["total_bytes"] == 8
        assert e["files"] == ["a.txt", "b.bin"]

    def test_snapshot_meta_parsed(self, tmp_path):
        d = tmp_path / "sandbox-aaaa0003"
        d.mkdir()
        (d / "snap-8354c13d.json").write_text(json.dumps(SNAP), encoding="utf-8")
        e = census.inspect_sandbox_dir(d)
        assert len(e["snapshots"]) == 1
        snap = e["snapshots"][0]
        assert snap["parsed"] is True
        assert snap["snapshot_id"] == "snap-8354c13d"
        assert snap["name"] == "initial"
        assert snap["variables"] == {"env": "staging"}
        assert snap["log_length"] == 1

    def test_snapshot_parse_failure_honest(self, tmp_path):
        d = tmp_path / "sandbox-aaaa0004"
        d.mkdir()
        (d / "snap-broken.json").write_text("{not json", encoding="utf-8")
        e = census.inspect_sandbox_dir(d)
        assert e["snapshots"][0]["parsed"] is False
        assert "snapshot_id" not in e["snapshots"][0]

    def test_snapshot_non_dict_honest(self, tmp_path):
        d = tmp_path / "sandbox-aaaa0005"
        d.mkdir()
        (d / "snap-list.json").write_text("[1, 2]", encoding="utf-8")
        e = census.inspect_sandbox_dir(d)
        assert e["snapshots"][0]["parsed"] is False

    def test_non_snapshot_json_not_meta(self, tmp_path):
        d = tmp_path / "sandbox-aaaa0006"
        d.mkdir()
        (d / "other.json").write_text(json.dumps(SNAP), encoding="utf-8")
        e = census.inspect_sandbox_dir(d)
        assert e["snapshots"] == []
        assert e["file_count"] == 1


class TestTimelineXref:
    def test_ref_count_and_sample(self, tmp_path):
        conn = make_timeline(
            tmp_path,
            [
                (
                    "evt-1",
                    "mirror",
                    "",
                    "sandbox_from_template",
                    "created",
                    '{"sandbox_id": "sandbox-bcf4e66d"}',
                    1.0,
                    1.0,
                    "default",
                ),
                ("evt-2", "mirror", "", "sandbox_exec", "exec", "{}", 2.0, 2.0, "default"),
            ],
        )
        xref = census.sandbox_timeline_xref(conn, ["sandbox-bcf4e66d", "sandbox-ghost00"])
        assert xref["sandbox-bcf4e66d"]["count"] == 1
        assert xref["sandbox-bcf4e66d"]["sample"] == ["evt-1"]
        assert xref["sandbox-ghost00"]["count"] == 0

    def test_summary_side_also_matched(self, tmp_path):
        conn = make_timeline(
            tmp_path,
            [
                (
                    "evt-9",
                    "mirror",
                    "",
                    "sandbox_exec",
                    "Sandbox exec: sandbox-xyz12345",
                    "{}",
                    1.0,
                    1.0,
                    "default",
                ),
            ],
        )
        xref = census.sandbox_timeline_xref(conn, ["sandbox-xyz12345"])
        assert xref["sandbox-xyz12345"]["count"] == 1


class TestCensusFilesAdoption:
    def test_section_shape_and_aggregates(self, tmp_path, monkeypatch):
        sdir = make_sandboxes(tmp_path)
        (sdir / "sandbox-aaaa0007").mkdir()
        d2 = sdir / "sandbox-aaaa0008"
        d2.mkdir()
        (d2 / "snap-x.json").write_text(json.dumps(SNAP), encoding="utf-8")
        (sdir / "sandbox-test-abc").mkdir()
        bdir = tmp_path / ".opensoul" / "backups"
        bdir.mkdir()
        (bdir / "manifests.json").write_text("{}", encoding="utf-8")
        make_timeline(
            tmp_path,
            [
                (
                    "evt-1",
                    "mirror",
                    "",
                    "sandbox_from_template",
                    "created",
                    '{"sandbox_id": "sandbox-aaaa0008"}',
                    1.0,
                    1.0,
                    "default",
                ),
            ],
        )
        monkeypatch.setattr(census, "HOME", tmp_path)
        monkeypatch.setattr(census, "REPO", tmp_path)
        out = {}
        census.census_files(out)
        sb = out["files"]["sandboxes"]
        assert sb["total"] == 3
        assert sb["A_names"] == ["sandbox-test-abc"]
        assert sorted(sb["U_names"]) == ["sandbox-aaaa0007", "sandbox-aaaa0008"]
        assert sb["empty_dirs"] == 2
        assert sb["user_data_bytes"] == len(json.dumps(SNAP))
        by_name = {e["name"]: e for e in sb["entries"]}
        assert by_name["sandbox-aaaa0008"]["timeline_xref"]["count"] == 1
        assert by_name["sandbox-aaaa0007"]["timeline_xref"]["count"] == 0
        assert by_name["sandbox-aaaa0008"]["snapshots"][0]["name"] == "initial"

    def test_missing_timeline_xref_empty_not_explode(self, tmp_path, monkeypatch):
        sdir = make_sandboxes(tmp_path)
        (sdir / "sandbox-aaaa0009").mkdir()
        bdir = tmp_path / ".opensoul" / "backups"
        bdir.mkdir()
        (bdir / "manifests.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(census, "HOME", tmp_path)
        monkeypatch.setattr(census, "REPO", tmp_path)  # tmp 下无 data/timeline.db
        out = {}
        census.census_files(out)
        e = out["files"]["sandboxes"]["entries"][0]
        assert e["timeline_xref"] == {"count": 0, "sample": []}
