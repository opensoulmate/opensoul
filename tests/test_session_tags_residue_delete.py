"""SESS-⑦ 删除执行件——session_tags 历史探针残留行精确删语义（全 tmp，绝不碰生产库）。

覆盖：分类单一源契约（A 闭集前缀 / temp-|om-|grp-|agent:* 一律 U 宁窄勿滥）/
dry-run 零写入 / 按清单精确删（非目标行逐条内容不变）/ 删前逐行复核拒删（现判非 A、
created_at 键错配）/ 清单缺行非致命幂等 / 清单外新残留单列不删 / 备份快照可回读 /
回读对账 / 报告逐键 / 静态接线（工具真消费 census.classify_session_tag 单一源）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_session_tags_test_residue.py"
_spec = importlib.util.spec_from_file_location("delete_session_tags_test_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

CENSUS = mod.census

# 行样本：A=自述探针前缀闭集；U=真实客户端会话 id 形态/功能标签（宁窄勿滥）
A_ROWS = [
    ("om-dead0001", "client-key:rej-006187b7daae", "2026-10-14 21:28:35"),
    ("om-dead0002", "client-key:probe-1", "2026-10-10 05:00:00"),
    ("om-dead0003", "client-key:diag-p115-1", "2026-10-13 00:00:00"),
    ("om-dead0004", "client-key:smoke-1791609316", "2026-10-10 05:15:17"),
    ("om-dead0005", "client-key:term-probe-e2e-budget-1791774456", "2026-10-12 03:07:38"),
]
U_ROWS = [
    ("om-live0001", "client-key:temp-1791603521735", "2026-10-10 03:38:43"),
    ("om-live0002", "client-key:om-f33410d05472", "2026-10-10 02:09:16"),
    ("om-live0003", "client-key:grp-02f7bbb5c4cb", "2026-10-10 06:58:02"),
    ("om-live0001", "agent:soulmate", "2026-10-10 03:38:43"),
    ("om-live0004", "client-key:rejection-not-rej", "2026-10-10 03:00:00"),
    ("om-live0005", "client-key:rej", "2026-10-10 03:00:01"),
]


def _make_db(tmp_path: Path, rows=None) -> Path:
    db_path = tmp_path / "opensoul.db"
    db = sqlite3.connect(str(db_path))
    db.execute(
        """CREATE TABLE session_tags (
            session_id TEXT NOT NULL,
            tag_name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (session_id, tag_name))"""
    )
    db.execute("CREATE TABLE agent_sessions (id TEXT PRIMARY KEY)")
    db.executemany("INSERT INTO agent_sessions (id) VALUES (?)", [("om-live0001",)])
    for sid, tag, ts in rows if rows is not None else A_ROWS + U_ROWS:
        db.execute(
            "INSERT INTO session_tags (session_id, tag_name, created_at) VALUES (?, ?, ?)",
            (sid, tag, ts),
        )
    db.commit()
    db.close()
    return db_path


def _all_rows(db_path: Path) -> list[tuple]:
    db = sqlite3.connect(str(db_path))
    try:
        return sorted(db.execute("SELECT session_id, tag_name, created_at FROM session_tags"))
    finally:
        db.close()


class TestClassifySingleSource:
    @pytest.mark.parametrize(
        "tag,rule",
        [
            ("client-key:rej-006187b7daae", "client_key_rej_probe"),
            ("client-key:probe-1", "client_key_probe"),
            ("client-key:probe-sm-140523544312592", "client_key_probe"),
            ("client-key:diag-p115-1", "client_key_diag_probe"),
            ("client-key:smoke-1791609316", "client_key_smoke_probe"),
            ("client-key:term-probe-e2e-normal-1791774830", "client_key_term_probe_e2e"),
        ],
    )
    def test_a_closed_prefixes(self, tag, rule):
        assert CENSUS.classify_session_tag(tag) == ("A", rule)

    @pytest.mark.parametrize(
        "tag",
        [
            "agent:soulmate",
            "agent:hermes",
            "client-key:temp-1791603521735",
            "client-key:om-f33410d05472",
            "client-key:grp-02f7bbb5c4cb",
            "client-key:rejection-not-rej",
            "client-key:rej",
            "client-key:probe",
            "",
            "  ",
        ],
    )
    def test_u_narrow_keep(self, tag):
        assert CENSUS.classify_session_tag(tag) == ("U", "unmatched")

    def test_prefix_table_is_closed(self):
        # 单一源契约：闭集恰 5 条（新增前缀必须同步改本测试=判法变更可见）
        assert [p for p, _ in CENSUS.SESSION_TAG_A_PREFIXES] == [
            "client-key:rej-",
            "client-key:probe-",
            "client-key:diag-",
            "client-key:smoke-",
            "client-key:term-probe-",
        ]


class TestDryRunAndApply:
    def test_dry_run_zero_writes(self, tmp_path):
        db_path = _make_db(tmp_path)
        before = _all_rows(db_path)
        rep = mod.run(db_path, apply=False)
        assert rep["mode"] == "dry-run"
        assert rep["deleted"] == {"rows": 0}
        assert rep["matched"] == len(A_ROWS)
        assert _all_rows(db_path) == before
        assert not list(tmp_path.rglob("session_tags_pre_delete_*"))

    def test_apply_deletes_exactly_a_rows(self, tmp_path):
        db_path = _make_db(tmp_path)
        rep = mod.run(db_path, apply=True, backup_dir=tmp_path / "bak")
        assert rep["deleted"]["rows"] == len(A_ROWS)
        after = _all_rows(db_path)
        assert after == sorted((s, t, ts) for s, t, ts in U_ROWS)
        assert rep["post"] == {"total": len(U_ROWS), "A": 0, "U": len(U_ROWS)}
        assert rep["non_target_missing"] == []
        assert rep["non_target_changed"] == []

    def test_pre_counts_and_alive_evidence(self, tmp_path):
        db_path = _make_db(tmp_path)
        rep = mod.run(db_path, apply=False)
        assert rep["pre"] == {
            "total": len(A_ROWS) + len(U_ROWS),
            "A": len(A_ROWS),
            "U": len(U_ROWS),
            "A_alive_sessions": 0,
        }

    def test_idempotent_rerun(self, tmp_path):
        db_path = _make_db(tmp_path)
        mod.run(db_path, apply=True, backup_dir=tmp_path / "bak")
        rep2 = mod.run(db_path, apply=True, backup_dir=tmp_path / "bak")
        assert rep2["matched"] == 0
        assert rep2["deleted"] == {"rows": 0}
        assert len(_all_rows(db_path)) == len(U_ROWS)


class TestManifestChecklist:
    def _manifest(self, tmp_path: Path, rows, path_name="m.json") -> Path:
        p = tmp_path / path_name
        p.write_text(
            json.dumps(
                {
                    "checklist": [
                        {"session_id": s, "tag_name": t, "created_at": ts} for s, t, ts in rows
                    ]
                }
            )
        )
        return p

    def test_only_manifest_rows_deleted(self, tmp_path):
        db_path = _make_db(tmp_path)
        m = self._manifest(tmp_path, A_ROWS[:2])
        rep = mod.run(db_path, apply=True, manifest_path=m, backup_dir=tmp_path / "bak")
        assert rep["matched"] == 2
        after = {r[:2] for r in _all_rows(db_path)}
        assert ("om-dead0001", "client-key:rej-006187b7daae") not in after
        assert ("om-dead0002", "client-key:probe-1") not in after
        # 清单外新 A 残留只报告不动
        assert ("om-dead0003", "client-key:diag-p115-1") in after
        beyond_keys = {(b["session_id"], b["tag_name"]) for b in rep["beyond_checklist"]}
        assert ("om-dead0003", "client-key:diag-p115-1") in beyond_keys
        assert rep["post"]["A"] == len(A_ROWS) - 2

    def test_bare_list_manifest_accepted(self, tmp_path):
        db_path = _make_db(tmp_path)
        p = tmp_path / "bare.json"
        p.write_text(json.dumps([{"session_id": A_ROWS[0][0], "tag_name": A_ROWS[0][1]}]))
        rep = mod.run(db_path, apply=True, manifest_path=p, backup_dir=tmp_path / "bak")
        assert rep["matched"] == 1

    def test_missing_checklist_rows_not_fatal(self, tmp_path):
        db_path = _make_db(tmp_path)
        rows = A_ROWS[:2] + [("om-never-existed", "client-key:rej-zzz", "2026-01-01 00:00:00")]
        m = self._manifest(tmp_path, rows)
        rep = mod.run(db_path, apply=True, manifest_path=m, backup_dir=tmp_path / "bak")
        assert rep["matched"] == 2
        assert rep["missing_checklist"] == [
            {"session_id": "om-never-existed", "tag_name": "client-key:rej-zzz"}
        ]

    def test_recheck_refuses_now_u_row(self, tmp_path):
        db_path = _make_db(tmp_path)
        # 清单错配：把 U 行（agent: 功能标签）当待删行=现判非 A，拒删一条不动
        m = self._manifest(tmp_path, [("om-live0001", "agent:soulmate", "2026-10-10 03:38:43")])
        with pytest.raises(mod.RefuseError, match="现判非 A"):
            mod.run(db_path, apply=True, manifest_path=m, backup_dir=tmp_path / "bak")
        assert len(_all_rows(db_path)) == len(A_ROWS) + len(U_ROWS)

    def test_recheck_refuses_created_at_mismatch(self, tmp_path):
        db_path = _make_db(tmp_path)
        m = self._manifest(tmp_path, [(A_ROWS[0][0], A_ROWS[0][1], "2030-01-01 00:00:00")])
        with pytest.raises(mod.RefuseError, match="created_at 错配"):
            mod.run(db_path, apply=True, manifest_path=m, backup_dir=tmp_path / "bak")
        assert len(_all_rows(db_path)) == len(A_ROWS) + len(U_ROWS)

    def test_untrusted_manifest_refused(self, tmp_path):
        db_path = _make_db(tmp_path)
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"checklist": [{"tag_name": "client-key:rej-x"}]}))
        with pytest.raises(mod.RefuseError, match="工件不可信"):
            mod.run(db_path, apply=True, manifest_path=p, backup_dir=tmp_path / "bak")
        p2 = tmp_path / "bad2.json"
        p2.write_text("not-json")
        with pytest.raises(mod.RefuseError):
            mod.run(db_path, apply=True, manifest_path=p2, backup_dir=tmp_path / "bak")
        assert len(_all_rows(db_path)) == len(A_ROWS) + len(U_ROWS)


class TestBackupAndReadback:
    def test_backup_snapshot_restorable(self, tmp_path):
        db_path = _make_db(tmp_path)
        before = _all_rows(db_path)
        rep = mod.run(db_path, apply=True, backup_dir=tmp_path / "bak")
        bak = rep["backup"]
        bak_path = Path(bak["path"])
        assert bak_path.exists()
        assert hashlib.sha256(bak_path.read_bytes()).hexdigest() == bak["sha256"]
        # 快照=删前全库（可整体回滚）：恢复后行集与删前逐条一致
        restored = sqlite3.connect(str(tmp_path / "restored.db"))
        src = sqlite3.connect(str(bak_path))
        src.backup(restored)
        src.close()
        got = sorted(restored.execute("SELECT session_id, tag_name, created_at FROM session_tags"))
        restored.close()
        assert got == before

    def test_readback_detects_missing_target(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path)
        real_delete = mod.delete_exact

        def lie(pairs, db):
            return 0  # 谎报删了但实际不动

        monkeypatch.setattr(mod, "delete_exact", lie)
        with pytest.raises(RuntimeError, match="待删行仍在场"):
            mod.run(db_path, apply=True, backup_dir=tmp_path / "bak")
        monkeypatch.setattr(mod, "delete_exact", real_delete)

    def test_report_keys(self, tmp_path):
        db_path = _make_db(tmp_path)
        rep = mod.run(db_path, apply=True, backup_dir=tmp_path / "bak")
        for key in (
            "tool",
            "mode",
            "db",
            "checklist_rows",
            "matched",
            "missing_checklist",
            "beyond_checklist",
            "pre",
            "checklist",
            "backup",
            "deleted",
            "post",
            "post_A_rows",
            "non_target_missing",
            "non_target_changed",
            "concurrent_new_rows",
        ):
            assert key in rep, key
        assert len(rep["checklist"]) == len(A_ROWS)


class TestStaticWiring:
    def test_tool_consumes_single_source_classifier(self):
        src = _SCRIPT.read_text()
        assert "census.classify_session_tag(" in src
        # 禁第二套判法：工具内不得出现 A 前缀字面手抄
        assert "client-key:rej-" not in src

    def test_census_classifier_is_pure_single_source(self):
        assert callable(CENSUS.classify_session_tag)
        res = CENSUS.classify_session_tag("client-key:rej-x")
        assert isinstance(res, tuple) and res[0] == "A"
