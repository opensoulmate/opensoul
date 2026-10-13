"""TL-⑬ exports 测试残留删除工具测试——全 tmp 绝不碰生产文件面。

覆盖：classify_export 纯函数（内容硬标识/文件名硬标识/不动面）、census_files
收编（A_files 加性键真消费分类）、删除工具三硬闸（dry-run 零写入 / 逐行复核拒删 /
工件不可信拒删 / 精确删非目标不动 / 备份可回读 / 幂等 / 清单外单列 / 算术对平）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
for _p in (str(SCRIPTS), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import census_test_residue as census  # noqa: E402
import delete_export_test_residue as det  # noqa: E402

FIXTURE = '[\n  {\n    "type": "knowledge",\n    "name": "test"\n  }\n]'
REAL = '[\n  {\n    "type": "knowledge",\n    "name": "real-data"\n  }\n]'


def make_export_dir(tmp_path: Path) -> Path:
    d = tmp_path / "exports"
    d.mkdir()
    (d / "export_export_1.json").write_text(FIXTURE)
    (d / "export_export_2.json").write_text(FIXTURE)
    (d / "export_export_3.json").write_text(FIXTURE)
    (d / "test-export_export_9.json").write_text(
        '[{"name": "test", "value": 1}, {"name": "test2", "value": 2}]'
    )
    (d / "export_export_real.json").write_text(REAL)
    (d / "broken.json").write_text("{not json")
    return d


def make_artifact(tmp_path: Path, names, key: str = "A_files") -> Path:
    art = {"files": {"exports": {key: list(names), "total": len(names)}}}
    p = tmp_path / "artifact.json"
    p.write_text(json.dumps(art))
    return p


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class TestClassifyExport:
    def test_content_fixture_is_a(self):
        cls, rule = census.classify_export("export_export_1.json", FIXTURE)
        assert (cls, rule) == ("A", "A_EXPORT_ENTRY_NAMES")

    def test_test2_content_is_a(self):
        cls, rule = census.classify_export("x.json", '[{"name": "test2"}]')
        assert (cls, rule) == ("A", "A_EXPORT_ENTRY_NAMES")

    def test_name_prefix_is_a_even_unparsed(self):
        cls, rule = census.classify_export("test-export_export_9.json", "garbage")
        assert (cls, rule) == ("A", "A_EXPORT_NAME_PREFIX")

    def test_both_rules_name_wins(self):
        cls, rule = census.classify_export("test-x.json", FIXTURE)
        assert (cls, rule) == ("A", "A_EXPORT_NAME_PREFIX")

    def test_real_export_other(self):
        cls, rule = census.classify_export("export_export_real.json", REAL)
        assert (cls, rule) == ("other", "unmatched")

    def test_unparsed_other(self):
        assert census.classify_export("a.json", "{not json") == ("other", "unparsed")

    def test_empty_list_and_dict_other(self):
        assert census.classify_export("a.json", "[]") == ("other", "not_nonempty_list")
        assert census.classify_export("a.json", '{"name": "test"}') == (
            "other",
            "not_nonempty_list",
        )

    def test_mixed_entries_not_all_fixture_other(self):
        # 真实导出里混着一条名为 test 的条目不整文件定罪（宁窄勿滥）
        raw = '[{"name": "test"}, {"name": "real"}]'
        assert census.classify_export("a.json", raw) == ("other", "unmatched")

    def test_vocab_single_source_consumed(self, monkeypatch):
        # 词表运行时真消费：改词表分类随之翻转（禁手抄第二套判法）
        monkeypatch.setattr(census, "A_EXPORT_ENTRY_NAMES", {"test2"})
        cls, _ = census.classify_export("a.json", FIXTURE)
        assert cls == "other"


class TestCensusFilesCollection:
    def test_census_files_emits_a_files(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        edir = home / ".opensoul" / "exports"
        edir.mkdir(parents=True)
        (home / ".opensoul" / "backups").mkdir(parents=True)
        (home / ".opensoul" / "backups" / "manifests.json").write_text("{}")
        (edir / "export_export_1.json").write_text(FIXTURE)
        (edir / "export_export_real.json").write_text(REAL)
        monkeypatch.setattr(census, "HOME", home)
        out = {}
        census.census_files(out)
        ex = out["files"]["exports"]
        assert ex["A_files"] == ["export_export_1.json"]
        assert ex["A_total"] == 1
        assert ex["A_by_rule"] == {"A_EXPORT_ENTRY_NAMES": 1}
        assert ex["total"] == 2
        assert ex["B_uniform_export_export"] == 2  # 旧键语义不变（名字模式计数）


class TestDeleteTool:
    def test_dry_run_zero_write(self, tmp_path):
        d = make_export_dir(tmp_path)
        art = make_artifact(
            tmp_path,
            [
                "export_export_1.json",
                "export_export_2.json",
                "export_export_3.json",
                "test-export_export_9.json",
            ],
        )
        before = {p.name: sha(p) for p in d.iterdir()}
        rep = det.run(d, art, apply=False, backup_root=tmp_path / "bak")
        assert rep["mode"] == "dry-run"
        assert rep["deleted"] == {"files": 0}
        assert {p.name: sha(p) for p in d.iterdir()} == before
        assert not (tmp_path / "bak").exists()

    def test_apply_exact_delete(self, tmp_path):
        d = make_export_dir(tmp_path)
        targets = [
            "export_export_1.json",
            "export_export_2.json",
            "export_export_3.json",
            "test-export_export_9.json",
        ]
        art = make_artifact(tmp_path, targets)
        real_before = sha(d / "export_export_real.json")
        rep = det.run(d, art, apply=True, backup_root=tmp_path / "bak")
        assert rep["deleted"] == {"files": 4}
        for t in targets:
            assert not (d / t).exists()
        # 非目标一字不动
        assert (d / "export_export_real.json").read_text() == REAL
        assert sha(d / "export_export_real.json") == real_before
        assert (d / "broken.json").exists()
        assert rep["post"] == {"total": 2, "A": 0, "other": 2}
        assert rep["reconcile_ok"] is True
        assert rep["non_target_missing"] == []
        assert rep["non_target_changed"] == []

    def test_recheck_refuses_when_content_changed(self, tmp_path):
        d = make_export_dir(tmp_path)
        art = make_artifact(tmp_path, ["export_export_1.json"])
        # 工件生成后文件内容变为真实导出 → 现判非 A → 拒删一条不动
        (d / "export_export_1.json").write_text(REAL)
        before = {p.name: sha(p) for p in d.iterdir()}
        with pytest.raises(det.RefuseError, match="现判非 A"):
            det.run(d, art, apply=True, backup_root=tmp_path / "bak")
        assert {p.name: sha(p) for p in d.iterdir()} == before

    def test_artifact_missing_key_refuses(self, tmp_path):
        d = make_export_dir(tmp_path)
        art = make_artifact(tmp_path, [], key="wrong_key")
        with pytest.raises(det.RefuseError, match="A_files"):
            det.run(d, art, apply=True, backup_root=tmp_path / "bak")
        assert len(list(d.iterdir())) == 6

    def test_path_traversal_name_refuses(self, tmp_path):
        d = make_export_dir(tmp_path)
        art = make_artifact(tmp_path, ["../evil.json"])
        with pytest.raises(det.RefuseError, match="纯文件名"):
            det.run(d, art, apply=True, backup_root=tmp_path / "bak")
        assert len(list(d.iterdir())) == 6

    def test_missing_checklist_row_nonfatal_and_idempotent(self, tmp_path):
        d = make_export_dir(tmp_path)
        art = make_artifact(tmp_path, ["export_export_1.json", "gone.json"])
        rep = det.run(d, art, apply=True, backup_root=tmp_path / "bak")
        assert rep["missing_checklist"] == ["gone.json"]
        # 1 清单内 + 3 清单外同判据（beyond）一并删
        assert rep["deleted"] == {"files": 4}
        assert not (d / "export_export_1.json").exists()
        # 幂等重跑：零目标
        rep2 = det.run(d, art, apply=True, backup_root=tmp_path / "bak2")
        assert rep2["deleted"] == {"files": 0}
        assert sorted(rep2["missing_checklist"]) == ["export_export_1.json", "gone.json"]

    def test_beyond_checklist_deleted_and_listed(self, tmp_path):
        d = make_export_dir(tmp_path)
        art = make_artifact(tmp_path, ["export_export_1.json"])
        rep = det.run(d, art, apply=True, backup_root=tmp_path / "bak")
        assert rep["beyond_checklist"] == [
            "export_export_2.json",
            "export_export_3.json",
            "test-export_export_9.json",
        ]
        assert rep["deleted"] == {"files": 4}
        assert not (d / "test-export_export_9.json").exists()

    def test_backup_byte_identical_and_rollback(self, tmp_path):
        d = make_export_dir(tmp_path)
        targets = ["export_export_1.json", "test-export_export_9.json"]
        art = make_artifact(tmp_path, targets)
        pre_sha = {t: sha(d / t) for t in targets}
        rep = det.run(d, art, apply=True, backup_root=tmp_path / "bak")
        bdir = Path(rep["backup"]["path"])
        # 备份覆盖全部删除目标（清单内 2 + beyond 2）
        assert rep["backup"]["files"] == 4
        for t in targets:
            assert sha(bdir / t) == pre_sha[t]
        manifest = json.loads((bdir / "backup_manifest.json").read_text())
        assert {m["name"]: m["sha256"] for m in manifest}.items() >= pre_sha.items()
        # 回滚=拷回
        for t in targets:
            (bdir / t).replace(d / t)
        assert {t: sha(d / t) for t in targets} == pre_sha

    def test_report_keys(self, tmp_path):
        d = make_export_dir(tmp_path)
        art = make_artifact(tmp_path, ["export_export_1.json"])
        rep = det.run(d, art, apply=True, backup_root=tmp_path / "bak")
        for k in (
            "tool",
            "mode",
            "export_dir",
            "artifact",
            "checklist_names",
            "live_A_names",
            "artifact_drift",
            "matched",
            "missing_checklist",
            "beyond_checklist",
            "pre",
            "deleted_names",
            "backup",
            "deleted",
            "post",
            "post_A_names",
            "non_target_missing",
            "non_target_changed",
            "concurrent_new_files",
            "reconcile_ok",
        ):
            assert k in rep, k

    def test_concurrent_new_reported_not_deleted_check(self, tmp_path):
        d = make_export_dir(tmp_path)
        art = make_artifact(tmp_path, ["export_export_1.json"])
        # 模拟删后并发新写入（在 delete 后出现）：直接跑后手工加文件不触发对账
        rep = det.run(d, art, apply=True, backup_root=tmp_path / "bak")
        (d / "export_export_new.json").write_text(FIXTURE)
        assert rep["concurrent_new_files"] == []
        assert (d / "export_export_new.json").exists()
