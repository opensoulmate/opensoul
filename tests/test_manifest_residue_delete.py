"""TL-⑧ 删除执行件②b-2a——manifests.json 悬挂测试条目精确删语义（全 tmp，绝不碰生产文件）。

覆盖：dry-run 零写入 / 按清单精确删（非目标条目逐条内容不变）/ 删前逐行复核拒删
（名错配 / 现判非 A / tar 在盘仍可用）/ 工件不可信拒删 / manifests 非 dict 拒读 /
清单缺条目非致命 / 清单外新残留单列不删 / 备份快照可回读 / 原子写回 / 报告逐键 /
分类词表单一源契约。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_manifest_test_residue.py"
_spec = importlib.util.spec_from_file_location("delete_manifest_test_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

# 条目样本：分类判法单一源=census.classify_manifest（name 含测试备份名硬标识=A）
ENTRIES = {
    "backup_real_1": {
        "backup_id": "backup_real_1",
        "name": "Daily Data Backup",
        "created_at": 1.0,
        "size_bytes": 10,
        "file_count": 2,
        "description": "prod",
        "tags": [],
        "checksum": "aa",
        "status": "complete",
    },
    "backup_real_2": {
        "backup_id": "backup_real_2",
        "name": "weekly-prod",
        "created_at": 2.0,
        "size_bytes": 20,
        "file_count": 3,
        "description": "prod",
        "tags": [],
        "checksum": "bb",
        "status": "complete",
    },
    "backup_1786674357": {
        "backup_id": "backup_1786674357",
        "name": "test-sense-backup",
        "created_at": 3.0,
        "size_bytes": 30,
        "file_count": 4,
        "description": "test backup of sense module",
        "tags": [],
        "checksum": "cc",
        "status": "complete",
    },
    "backup_1789984955": {
        "backup_id": "backup_1789984955",
        "name": "test_backup_integration",
        "created_at": 4.0,
        "size_bytes": 40,
        "file_count": 5,
        "description": "Created by integration test",
        "tags": [],
        "checksum": "dd",
        "status": "complete",
    },
    "backup_extra_1": {
        "backup_id": "backup_extra_1",
        "name": "test_backup_integration",
        "created_at": 5.0,
        "size_bytes": 50,
        "file_count": 6,
        "description": "Created by integration test",
        "tags": [],
        "checksum": "ee",
        "status": "complete",
    },
}
CHECKLIST = [
    {"backup_id": "backup_1786674357", "name": "test-sense-backup"},
    {"backup_id": "backup_1789984955", "name": "test_backup_integration"},
]


def make_manifest(tmp_path: Path) -> Path:
    p = tmp_path / "manifests.json"
    p.write_text(json.dumps(ENTRIES, indent=2))
    return p


def make_artifact(tmp_path: Path, checklist=None) -> Path:
    art = tmp_path / "artifact.json"
    rows = CHECKLIST if checklist is None else checklist
    art.write_text(
        json.dumps(
            {"files": {"backups": {"manifest_entries": 5, "A_dangling_test_entries": rows}}},
            ensure_ascii=False,
        )
    )
    return art


def live_ids(p: Path) -> set:
    return set(json.loads(p.read_text()).keys())


class TestClassifySingleSource:
    def test_a_markers(self):
        assert mod.census.classify_manifest("test_backup_integration") == (
            "A",
            "A_MANIFEST_NAME_MARKERS",
        )
        assert mod.census.classify_manifest("test-sense-backup") == ("A", "A_MANIFEST_NAME_MARKERS")
        assert mod.census.classify_manifest("my test_backup_integration v2")[0] == "A"  # 子串语义

    def test_other_forms(self):
        for name in (
            "Daily Data Backup",
            "weekly-prod",
            "backup_20260101",
            "",
            None,
            "test in description only",
        ):
            cls, _rule = mod.census.classify_manifest(name)
            assert cls == "other", name

    def test_census_files_consumes_single_source(self, monkeypatch):
        """census_files 的 manifests A 判定收编 classify_manifest（monkeypatch 运行时真消费）。"""
        calls = []
        orig = mod.census.classify_manifest

        def spy(name):
            calls.append(name)
            return orig(name)

        monkeypatch.setattr(mod.census, "classify_manifest", spy)
        items = [{"name": "test_backup_integration"}, {"name": "Daily Data Backup"}]
        got = [
            it["name"]
            for it in items
            if mod.census.classify_manifest(str(it.get("name", "")))[0] == "A"
        ]
        assert got == ["test_backup_integration"]
        assert "test_backup_integration" in calls


class TestDeleteSemantics:
    def test_dry_run_zero_writes(self, tmp_path):
        m = make_manifest(tmp_path)
        before = m.read_bytes()
        rep = mod.run(m, make_artifact(tmp_path), apply=False)
        assert rep["mode"] == "dry-run"
        assert rep["deleted"] == {"manifest_entries": 0}
        assert m.read_bytes() == before
        assert sorted(rep["deleted_ids"]) == ["backup_1786674357", "backup_1789984955"]

    def test_apply_deletes_exactly_checklist(self, tmp_path):
        m = make_manifest(tmp_path)
        rep = mod.run(m, make_artifact(tmp_path), apply=True)
        assert rep["deleted"] == {"manifest_entries": 2}
        after = live_ids(m)
        assert "backup_1786674357" not in after and "backup_1789984955" not in after
        # 非目标条目含真备份 / 清单外 A 一条不动
        assert {"backup_real_1", "backup_real_2", "backup_extra_1"} <= after
        assert rep["non_target_missing"] == [] and rep["non_target_changed"] == []

    def test_non_target_content_unchanged(self, tmp_path):
        m = make_manifest(tmp_path)
        orig = json.loads(m.read_text())
        mod.run(m, make_artifact(tmp_path), apply=True)
        after = json.loads(m.read_text())
        for bid in ("backup_real_1", "backup_real_2", "backup_extra_1"):
            assert mod.entry_fingerprint(after[bid]) == mod.entry_fingerprint(orig[bid])

    def test_recheck_name_mismatch_refuses(self, tmp_path):
        m = make_manifest(tmp_path)
        art = make_artifact(
            tmp_path, checklist=[{"backup_id": "backup_1786674357", "name": "renamed-real"}]
        )
        with pytest.raises(mod.RefuseError, match="名错配"):
            mod.run(m, art, apply=True)
        assert "backup_1786674357" in live_ids(m)  # 一条不动

    def test_recheck_now_non_a_refuses(self, tmp_path):
        m = make_manifest(tmp_path)
        data = json.loads(m.read_text())
        data["backup_1786674357"]["name"] = "prod-real"
        m.write_text(json.dumps(data))
        with pytest.raises(mod.RefuseError, match="现判非 A"):
            mod.run(m, make_artifact(tmp_path), apply=True)
        assert "backup_1786674357" in live_ids(m)

    def test_tar_on_disk_refuses(self, tmp_path):
        """tar 在盘=仍可用备份（非悬挂），拒删禁制造孤儿备份文件。"""
        m = make_manifest(tmp_path)
        (tmp_path / "backup_1786674357.tar.gz").write_bytes(b"tar")
        with pytest.raises(mod.RefuseError, match="tar 在盘"):
            mod.run(m, make_artifact(tmp_path), apply=True, tar_dir=tmp_path)
        assert "backup_1786674357" in live_ids(m)

    def test_missing_checklist_entry_nonfatal(self, tmp_path):
        m = make_manifest(tmp_path)
        art = make_artifact(
            tmp_path,
            checklist=CHECKLIST + [{"backup_id": "backup_gone_1", "name": "test-sense-backup"}],
        )
        rep = mod.run(m, art, apply=True)
        assert rep["missing_checklist"] == ["backup_gone_1"]
        assert rep["deleted"] == {"manifest_entries": 2}

    def test_beyond_checklist_reported_not_deleted(self, tmp_path):
        m = make_manifest(tmp_path)
        rep = mod.run(m, make_artifact(tmp_path), apply=True)
        assert rep["beyond_checklist"] == [
            {"backup_id": "backup_extra_1", "name": "test_backup_integration"}
        ]
        assert "backup_extra_1" in live_ids(m)
        assert rep["post_A_ids"] == ["backup_extra_1"]

    def test_artifact_missing_key_refuses(self, tmp_path):
        m = make_manifest(tmp_path)
        art = tmp_path / "artifact.json"
        art.write_text(json.dumps({"files": {"exports": {}}}))
        with pytest.raises(mod.RefuseError, match="工件不可信"):
            mod.run(m, art, apply=True)
        assert len(live_ids(m)) == len(ENTRIES)

    def test_manifest_list_shape_refuses(self, tmp_path):
        """真源=BackupManager dict{backup_id: entry}；list 形态拒读禁猜。"""
        m = tmp_path / "manifests.json"
        m.write_text(json.dumps([{"backup_id": "x", "name": "test-sense-backup"}]))
        with pytest.raises(mod.RefuseError, match="顶层不是 dict"):
            mod.run(m, make_artifact(tmp_path), apply=True)
        assert len(json.loads(m.read_text())) == 1  # 一条不动

    def test_manifest_bad_json_refuses(self, tmp_path):
        m = tmp_path / "manifests.json"
        m.write_text("{not json")
        with pytest.raises(mod.RefuseError, match="不是合法 JSON"):
            mod.run(m, make_artifact(tmp_path), apply=True)
        assert m.read_text() == "{not json"  # 禁写坏真源


class TestBackupAndReport:
    def test_backup_snapshot_readable(self, tmp_path):
        m = make_manifest(tmp_path)
        art = make_artifact(tmp_path)
        rep = mod.run(m, art, apply=True, backup_dir=tmp_path / "bks")
        bpath = Path(rep["backup"]["path"])
        assert bpath.exists()
        assert rep["backup"]["sha256"]
        snap = json.loads(bpath.read_text())
        assert set(snap.keys()) == set(ENTRIES.keys())  # 删前全条目可回读

    def test_report_keys(self, tmp_path):
        m = make_manifest(tmp_path)
        rep = mod.run(
            m,
            make_artifact(tmp_path),
            apply=True,
            backup_dir=tmp_path / "bks",
            report_path=tmp_path / "rep.json",
        )
        for key in (
            "tool",
            "mode",
            "checklist_ids",
            "live_A_ids",
            "artifact_drift",
            "matched",
            "missing_checklist",
            "beyond_checklist",
            "pre",
            "post",
            "deleted",
            "deleted_ids",
            "backup",
            "non_target_missing",
            "non_target_changed",
            "concurrent_new_entries",
        ):
            assert key in rep, key
        on_disk = json.loads((tmp_path / "rep.json").read_text())
        assert on_disk["tool"] == "delete_manifest_test_residue"

    def test_concurrent_new_entry_allowed(self, tmp_path, monkeypatch):
        """允许生产并发新条目（如 cron 新备份）：只查删前在场项，新条目保留并上报。"""
        m = make_manifest(tmp_path)
        art = make_artifact(tmp_path)

        real_backup = mod.backup_file

        def backup_with_concurrent(src, backup_dir):
            rep = real_backup(src, backup_dir)
            data = json.loads(Path(src).read_text())
            data["backup_new_1"] = {"backup_id": "backup_new_1", "name": "Daily Data Backup"}
            Path(src).write_text(json.dumps(data))
            return rep

        monkeypatch.setattr(mod, "backup_file", backup_with_concurrent)
        rep = mod.run(m, art, apply=True, backup_dir=tmp_path / "bks")
        assert rep["concurrent_new_entries"] == ["backup_new_1"]
        assert "backup_new_1" in live_ids(m)

    def test_no_tmp_left_behind(self, tmp_path):
        """原子写回：不残留 .tmp 半截文件。"""
        m = make_manifest(tmp_path)
        mod.run(m, make_artifact(tmp_path), apply=True, backup_dir=tmp_path / "bks")
        assert not (tmp_path / "manifests.json.tmp").exists()
