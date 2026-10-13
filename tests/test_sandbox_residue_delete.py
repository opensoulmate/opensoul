"""TL-⑮ sandboxes 沙箱残留目录删除工具测试——全 tmp 绝不碰生产文件面。

覆盖：证据清单闸（缺键/路径穿越/形状非法拒删）、删前逐目复核（内容已变/子目录
新文件/字节变化=证据不再成立拒删一条不动）、dry-run 零写入、精确删非目标不动、
清单外 A（单一源硬标识）单列删/other 不动、缺目录非致命幂等、tar 备份可整体
回滚（解包还原逐文件 sha 对账）、回读对账（误删/改写/算术）、单一源运行时真消费。
"""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
for _p in (str(SCRIPTS), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import census_test_residue as census  # noqa: E402
import delete_sandbox_test_residue as dsr  # noqa: E402

SNAP = json.dumps(
    {
        "snapshot_id": "snap-8354c13d",
        "name": "initial",
        "created_at": 1786674811.2548223,
        "log_length": 1,
        "variables": {"env": "staging"},
    },
    indent=1,
)


def make_sandbox_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sandboxes"
    d.mkdir()
    (d / "sandbox-aaaa1111").mkdir()
    (d / "sandbox-bbbb2222").mkdir()
    s3 = d / "sandbox-cccc3333"
    s3.mkdir()
    (s3 / "snap-8354c13d.json").write_text(SNAP)
    real = d / "sandbox-real9999"
    real.mkdir()
    (real / "data.txt").write_text("real user data")
    return d


def make_artifact(tmp_path: Path, sandbox_dir: Path, names) -> Path:
    entries = []
    for n in names:
        inv = dsr._tree_inventory(sandbox_dir / n)
        entries.append(
            {
                "name": n,
                "cls": "other",
                "rule": "unmatched",
                "files": inv["files"],
                "total_bytes": inv["total_bytes"],
            }
        )
    art = {"files": {"sandboxes": {"entries": entries, "total": len(entries)}}}
    p = tmp_path / "artifact.json"
    p.write_text(json.dumps(art))
    return p


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class TestArtifactGates:
    def test_missing_entries_key_refuses(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = tmp_path / "artifact.json"
        art.write_text(json.dumps({"files": {"sandboxes": {"total": 4}}}))
        with pytest.raises(dsr.RefuseError, match="entries"):
            dsr.run(d, art)

    def test_empty_entries_refuses(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = tmp_path / "artifact.json"
        art.write_text(json.dumps({"files": {"sandboxes": {"entries": [], "total": 0}}}))
        with pytest.raises(dsr.RefuseError):
            dsr.run(d, art)

    def test_path_traversal_name_refuses(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = tmp_path / "artifact.json"
        art.write_text(
            json.dumps(
                {
                    "files": {
                        "sandboxes": {
                            "entries": [{"name": "../evil", "files": [], "total_bytes": 0}],
                            "total": 1,
                        }
                    }
                }
            )
        )
        with pytest.raises(dsr.RefuseError, match="路径穿越"):
            dsr.run(d, art)

    def test_nested_file_name_in_checklist_refuses(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = tmp_path / "artifact.json"
        art.write_text(
            json.dumps(
                {
                    "files": {
                        "sandboxes": {
                            "entries": [
                                {
                                    "name": "sandbox-aaaa1111",
                                    "files": ["sub/x.txt"],
                                    "total_bytes": 1,
                                }
                            ],
                            "total": 1,
                        }
                    }
                }
            )
        )
        with pytest.raises(dsr.RefuseError, match="文件清单非法"):
            dsr.run(d, art)

    def test_bad_total_bytes_refuses(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = tmp_path / "artifact.json"
        art.write_text(
            json.dumps(
                {
                    "files": {
                        "sandboxes": {
                            "entries": [
                                {"name": "sandbox-aaaa1111", "files": [], "total_bytes": "155"}
                            ],
                            "total": 1,
                        }
                    }
                }
            )
        )
        with pytest.raises(dsr.RefuseError, match="total_bytes"):
            dsr.run(d, art)


class TestRecheck:
    def test_content_changed_refuses_all_untouched(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-aaaa1111", "sandbox-cccc3333"])
        (d / "sandbox-aaaa1111" / "new_user_file.txt").write_text("user added this")
        with pytest.raises(dsr.RefuseError, match="内容已变"):
            dsr.run(d, art, apply=True)
        assert (d / "sandbox-aaaa1111" / "new_user_file.txt").exists()
        assert (d / "sandbox-cccc3333").is_dir()

    def test_bytes_changed_refuses(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-cccc3333"])
        with (d / "sandbox-cccc3333" / "snap-8354c13d.json").open("a") as f:
            f.write(" ")
        with pytest.raises(dsr.RefuseError, match="内容已变"):
            dsr.run(d, art, apply=True)
        assert (d / "sandbox-cccc3333").is_dir()

    def test_nested_new_file_refuses(self, tmp_path):
        # 证据覆盖全树：子目录里塞文件同样拒删（top-level 文件清单不变也拦）
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-cccc3333"])
        sub = d / "sandbox-cccc3333" / "workspace"
        sub.mkdir()
        (sub / "hidden.txt").write_text("hidden user data")
        with pytest.raises(dsr.RefuseError, match="内容已变"):
            dsr.run(d, art, apply=True)

    def test_empty_subdir_ok_evidence_still_holds(self, tmp_path):
        # 空子目录零用户数据，证据仍成立（可删）
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-aaaa1111"])
        (d / "sandbox-aaaa1111" / "workspace").mkdir()
        rep = dsr.run(d, art, apply=True)
        assert rep["deleted"]["dirs"] == 1
        assert not (d / "sandbox-aaaa1111").exists()


class TestDelete:
    def test_dry_run_zero_write(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(
            tmp_path, d, ["sandbox-aaaa1111", "sandbox-bbbb2222", "sandbox-cccc3333"]
        )
        before = sorted(x.name for x in d.iterdir())
        rep = dsr.run(d, art, apply=False, backup_root=tmp_path / "backups")
        assert rep["mode"] == "dry-run"
        assert rep["deleted"] == {"dirs": 0}
        assert rep["matched"] == 3
        assert sorted(x.name for x in d.iterdir()) == before
        assert not (tmp_path / "backups").exists()

    def test_apply_deletes_only_checklist(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(
            tmp_path, d, ["sandbox-aaaa1111", "sandbox-bbbb2222", "sandbox-cccc3333"]
        )
        real_sha = sha(d / "sandbox-real9999" / "data.txt")
        rep = dsr.run(d, art, apply=True, backup_root=tmp_path / "backups")
        assert rep["deleted"]["dirs"] == 3
        for n in ("sandbox-aaaa1111", "sandbox-bbbb2222", "sandbox-cccc3333"):
            assert not (d / n).exists()
        assert (d / "sandbox-real9999" / "data.txt").exists()
        assert sha(d / "sandbox-real9999" / "data.txt") == real_sha
        assert rep["reconcile_ok"] is True

    def test_missing_checklist_dir_nonfatal_idempotent(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-aaaa1111", "sandbox-bbbb2222"])
        import shutil

        shutil.rmtree(d / "sandbox-aaaa1111")
        rep = dsr.run(d, art, apply=True, backup_root=tmp_path / "backups")
        assert rep["missing_checklist"] == ["sandbox-aaaa1111"]
        assert rep["deleted"]["dirs"] == 1
        assert not (d / "sandbox-bbbb2222").exists()
        # 幂等重跑：零目标非致命
        rep2 = dsr.run(d, art, apply=True, backup_root=tmp_path / "backups")
        assert rep2["deleted"] == {"dirs": 0}
        assert sorted(rep2["missing_checklist"]) == ["sandbox-aaaa1111", "sandbox-bbbb2222"]

    def test_beyond_checklist_A_deleted_other_untouched(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-aaaa1111"])
        (d / "test-sandbox-xyz").mkdir()
        (d / "sandbox-new9999").mkdir()
        (d / "sandbox-new9999" / "keep.txt").write_text("maybe a real sandbox")
        rep = dsr.run(d, art, apply=True, backup_root=tmp_path / "backups")
        assert rep["beyond_checklist"] == ["test-sandbox-xyz"]
        assert not (d / "test-sandbox-xyz").exists()
        assert (d / "sandbox-new9999" / "keep.txt").exists()
        assert rep["deleted"]["dirs"] == 2

    def test_delete_exact_traversal_guard(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        (tmp_path / "outside").mkdir()
        with pytest.raises(dsr.RefuseError, match="路径穿越"):
            dsr.delete_exact(["../outside"], d)
        assert (tmp_path / "outside").is_dir()


class TestBackup:
    def test_tar_restores_byte_identical(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-aaaa1111", "sandbox-cccc3333"])
        pre_sha = {n: dsr._tree_sha(d / n) for n in ("sandbox-aaaa1111", "sandbox-cccc3333")}
        rep = dsr.run(d, art, apply=True, backup_root=tmp_path / "backups")
        tar_path = Path(rep["backup"]["tar"])
        assert tar_path.exists()
        assert rep["backup"]["tar_sha256"] == sha(tar_path)
        restore = tmp_path / "restore"
        restore.mkdir()
        with tarfile.open(tar_path) as tf:
            tf.extractall(restore)
        for n, want in pre_sha.items():
            assert dsr._tree_sha(restore / n) == want

    def test_manifest_per_file_sha(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-cccc3333"])
        rep = dsr.run(d, art, apply=True, backup_root=tmp_path / "backups")
        man = Path(rep["backup"]["manifest"])
        assert rep["backup"]["manifest_sha256"] == sha(man)
        entries = json.loads(man.read_text())
        assert entries[0]["name"] == "sandbox-cccc3333"
        assert entries[0]["sha256"] == {
            "snap-8354c13d.json": hashlib.sha256(SNAP.encode()).hexdigest()
        }
        assert entries[0]["total_bytes"] == len(SNAP.encode())

    def test_dry_run_creates_no_backup(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-aaaa1111"])
        dsr.run(d, art, apply=False, backup_root=tmp_path / "backups")
        assert not (tmp_path / "backups").exists()


class TestReadback:
    def test_report_keys_and_reconcile(self, tmp_path):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(
            tmp_path, d, ["sandbox-aaaa1111", "sandbox-bbbb2222", "sandbox-cccc3333"]
        )
        rep = dsr.run(d, art, apply=True, backup_root=tmp_path / "backups")
        for k in (
            "tool",
            "mode",
            "checklist_names",
            "matched",
            "missing_checklist",
            "beyond_checklist",
            "pre",
            "post",
            "backup",
            "deleted",
            "non_target_missing",
            "non_target_changed",
            "concurrent_new_dirs",
            "reconcile_ok",
        ):
            assert k in rep
        assert rep["tool"] == "delete_sandbox_test_residue"
        assert rep["post"]["total"] == 1
        assert rep["post_names"] == ["sandbox-real9999"]
        assert rep["non_target_missing"] == []
        assert rep["non_target_changed"] == []

    def test_non_target_changed_detected(self, tmp_path, monkeypatch):
        # 回读闸真执行：删的过程中非目标目录被改写必须报错（不静默放过）
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-aaaa1111"])
        orig = dsr.delete_exact

        def sabotaged(names, sandbox_dir):
            out = orig(names, sandbox_dir)
            (Path(sandbox_dir) / "sandbox-real9999" / "data.txt").write_text("mutated")
            return out

        monkeypatch.setattr(dsr, "delete_exact", sabotaged)
        with pytest.raises(RuntimeError, match="非目标目录内容被改写"):
            dsr.run(d, art, apply=True, backup_root=tmp_path / "backups")

    def test_non_target_missing_detected(self, tmp_path, monkeypatch):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-aaaa1111"])
        orig = dsr.delete_exact

        def sabotaged(names, sandbox_dir):
            out = orig(names, sandbox_dir)
            import shutil

            shutil.rmtree(Path(sandbox_dir) / "sandbox-real9999")
            return out

        monkeypatch.setattr(dsr, "delete_exact", sabotaged)
        with pytest.raises(RuntimeError, match="非目标目录被误删"):
            dsr.run(d, art, apply=True, backup_root=tmp_path / "backups")

    def test_concurrent_new_dir_reported_not_touched(self, tmp_path, monkeypatch):
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-aaaa1111"])
        orig = dsr.delete_exact

        def with_concurrent(names, sandbox_dir):
            out = orig(names, sandbox_dir)
            (Path(sandbox_dir) / "sandbox-concur1").mkdir()
            return out

        monkeypatch.setattr(dsr, "delete_exact", with_concurrent)
        rep = dsr.run(d, art, apply=True, backup_root=tmp_path / "backups")
        assert rep["concurrent_new_dirs"] == ["sandbox-concur1"]
        assert (d / "sandbox-concur1").is_dir()
        assert rep["reconcile_ok"] is True


class TestSingleSource:
    def test_classify_sandbox_runtime_consumed(self, tmp_path, monkeypatch):
        # beyond 判定真消费 census.classify_sandbox（单一源）：override 后判定随之变
        d = make_sandbox_dir(tmp_path)
        art = make_artifact(tmp_path, d, ["sandbox-aaaa1111"])
        monkeypatch.setattr(census, "classify_sandbox", lambda name, files: ("A", "forced-by-test"))
        rep = dsr.run(d, art, apply=True, backup_root=tmp_path / "backups")
        assert set(rep["beyond_checklist"]) == {
            "sandbox-bbbb2222",
            "sandbox-cccc3333",
            "sandbox-real9999",
        }
        assert rep["deleted"]["dirs"] == 4

    def test_inventory_covers_nested_files(self, tmp_path):
        p = tmp_path / "s"
        (p / "a" / "b").mkdir(parents=True)
        (p / "a" / "b" / "deep.txt").write_text("x")
        (p / "top.txt").write_text("y")
        inv = dsr._tree_inventory(p)
        assert inv["files"] == ["a/b/deep.txt", "top.txt"]
        assert inv["total_bytes"] == 2
