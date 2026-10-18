"""SESS-⑧ 悬挂别名行精确删语义（全 tmp，绝不碰生产库）。

覆盖：分类单一源契约（A=om-/grp- 别名+目标双轴皆空；temp- 拍板面/目标可恢复
（含「无 agent_sessions 行但有 agent_messages」懒会话）/非别名行一律 U）/
dry-run 零写入 / 按清单精确删（非目标行逐条内容不变）/ 删前逐行复核拒删（现判非 A
含目标复活、created_at 键错配）/ 清单缺行非致命幂等 / 清单外新悬挂单列不删 /
备份快照可回读 / 回读对账 / temp- 死目标行只进拍板证据不删 / 报告逐键 /
静态接线（工具真消费 census.classify_session_alias 单一源）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_session_alias_dangling.py"
_spec = importlib.util.spec_from_file_location("delete_session_alias_dangling", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

CENSUS = mod.census

# 行样本：A=om-/grp- 别名且目标双轴皆空（悬挂）；U=宁窄勿滥全族
A_ROWS = [
    ("om-deadtarget1", "client-key:om-01be63f61966", "2026-10-10 02:09:16"),
    ("om-deadtarget2", "client-key:grp-c00e5965527d", "2026-10-10 06:57:59"),
    ("om-deadtarget3", "client-key:om-f33410d05472", "2026-10-10 02:09:16"),
]
U_ROWS = [
    # 目标可恢复=有 agent_sessions 行（消息 0）
    ("om-live0001", "client-key:om-98242202c9c8", "2026-10-10 07:43:58"),
    # 目标可恢复=无行但有 agent_messages（懒会话/ws直建，消费方恢复条件 OR 轴二）
    ("om-lazysess1", "client-key:grp-e204af9bf0ba", "2026-10-10 06:58:00"),
    # temp- 拍板面：死目标也不删（SESS-⑦ 留档）
    ("om-deadtarget1", "client-key:temp-1791591663058", "2026-10-10 00:21:04"),
    # temp- 拍板面：活目标
    ("om-live0001", "client-key:temp-1791603521735", "2026-10-10 03:38:43"),
    # 非别名行 / 未知前缀
    ("om-live0001", "agent:soulmate", "2026-10-10 03:38:43"),
    ("om-live0002", "client-key:rej-x", "2026-10-14 21:28:35"),
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
    db.execute("CREATE TABLE agent_messages (session_id TEXT NOT NULL, content TEXT)")
    db.executemany("INSERT INTO agent_sessions (id) VALUES (?)", [("om-live0001",)])
    db.executemany(
        "INSERT INTO agent_messages (session_id, content) VALUES (?, ?)",
        [("om-lazysess1", "hello")],
    )
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
            ("client-key:om-01be63f61966", "alias_dead_target_om"),
            ("client-key:grp-c00e5965527d", "alias_dead_target_grp"),
        ],
    )
    def test_a_dead_target(self, tag, rule):
        assert CENSUS.classify_session_alias(tag, False, 0) == ("A", rule)

    @pytest.mark.parametrize(
        "tag,rule",
        [
            # 轴一：agent_sessions 行在场
            ("client-key:om-98242202c9c8", "alias_live_target_om"),
            ("client-key:grp-c00e5965527d", "alias_live_target_grp"),
            # 轴二：无行但有消息（懒会话/ws直建，消费方恢复条件 OR 轴）
            ("client-key:om-01be63f61966", "alias_live_target_om"),
            ("client-key:grp-c00e5965527d", "alias_live_target_grp"),
        ],
    )
    def test_u_recoverable_target(self, tag, rule):
        assert CENSUS.classify_session_alias(tag, True, 0) == ("U", rule)
        assert CENSUS.classify_session_alias(tag, False, 3) == ("U", rule)

    def test_u_temp_pending_decision(self):
        # temp- 拍板面：死活一律 U（宁窄勿滥，取舍留用户拍板），规则名分死/活供证据报告
        assert CENSUS.classify_session_alias("client-key:temp-1791591663058", False, 0) == (
            "U",
            "alias_temp_dead_pending_decision",
        )
        assert CENSUS.classify_session_alias("client-key:temp-1791591663058", True, 2) == (
            "U",
            "alias_temp_live_pending_decision",
        )
        assert CENSUS.classify_session_alias("client-key:temp-1791603521735", False, 0) == (
            "U",
            "alias_temp_dead_pending_decision",
        )
        assert CENSUS.classify_session_alias("client-key:temp-1791603521735", True, 0) == (
            "U",
            "alias_temp_live_pending_decision",
        )

    @pytest.mark.parametrize(
        "tag",
        ["agent:soulmate", "agent:hermes", "client-key:rej-x", "client-key:om", "", "  "],
    )
    def test_u_non_alias_or_unknown(self, tag):
        assert CENSUS.classify_session_alias(tag, False, 0)[0] == "U"
        assert CENSUS.classify_session_alias(tag, True, 5)[0] == "U"

    def test_msg_count_zero_string_and_none_safe(self):
        # 双轴容缺：消息数空/None 不 fabricate 判活
        assert CENSUS.classify_session_alias("client-key:om-x", False, None) == (
            "A",
            "alias_dead_target_om",
        )
        assert CENSUS.classify_session_alias("client-key:om-x", False, 0) == (
            "A",
            "alias_dead_target_om",
        )

    def test_prefix_table_is_closed(self):
        # 单一源契约：判活客户端 id 形态闭集恰 om-/grp- 两条（新增必须同步改本测试）
        assert list(CENSUS.SESSION_ALIAS_LIVE_PREFIXES) == ["om-", "grp-"]


class TestDryRunAndApply:
    def test_dry_run_zero_writes(self, tmp_path):
        db_path = _make_db(tmp_path)
        before = _all_rows(db_path)
        rep = mod.run(db_path, apply=False)
        assert rep["mode"] == "dry-run"
        assert rep["deleted"] == {"rows": 0}
        assert rep["matched"] == len(A_ROWS)
        assert _all_rows(db_path) == before
        assert not list(tmp_path.rglob("session_alias_pre_delete_*"))

    def test_apply_deletes_exactly_a_rows(self, tmp_path):
        db_path = _make_db(tmp_path)
        rep = mod.run(db_path, apply=True, backup_dir=tmp_path / "bak")
        assert rep["deleted"]["rows"] == len(A_ROWS)
        after = _all_rows(db_path)
        assert after == sorted((s, t, ts) for s, t, ts in U_ROWS)
        assert rep["post"] == {"total": len(U_ROWS), "A": 0, "U": len(U_ROWS)}
        assert rep["non_target_missing"] == []
        assert rep["non_target_changed"] == []

    def test_pre_counts_and_recoverable_evidence(self, tmp_path):
        db_path = _make_db(tmp_path)
        rep = mod.run(db_path, apply=False)
        assert rep["pre"] == {
            "total": len(A_ROWS) + len(U_ROWS),
            "A": len(A_ROWS),
            "U": len(U_ROWS),
            "A_target_recoverable": 0,
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
        assert ("om-deadtarget1", "client-key:om-01be63f61966") not in after
        assert ("om-deadtarget2", "client-key:grp-c00e5965527d") not in after
        # 清单外新悬挂只报告不动
        assert ("om-deadtarget3", "client-key:om-f33410d05472") in after
        beyond_keys = {(b["session_id"], b["tag_name"]) for b in rep["beyond_checklist"]}
        assert ("om-deadtarget3", "client-key:om-f33410d05472") in beyond_keys
        assert rep["post"]["A"] == len(A_ROWS) - 2

    def test_bare_list_manifest_accepted(self, tmp_path):
        db_path = _make_db(tmp_path)
        p = tmp_path / "bare.json"
        p.write_text(json.dumps([{"session_id": A_ROWS[0][0], "tag_name": A_ROWS[0][1]}]))
        rep = mod.run(db_path, apply=True, manifest_path=p, backup_dir=tmp_path / "bak")
        assert rep["matched"] == 1

    def test_missing_checklist_rows_not_fatal(self, tmp_path):
        db_path = _make_db(tmp_path)
        rows = A_ROWS[:2] + [("om-never-existed", "client-key:om-zzz", "2026-01-01 00:00:00")]
        m = self._manifest(tmp_path, rows)
        rep = mod.run(db_path, apply=True, manifest_path=m, backup_dir=tmp_path / "bak")
        assert rep["matched"] == 2
        assert rep["missing_checklist"] == [
            {"session_id": "om-never-existed", "tag_name": "client-key:om-zzz"}
        ]

    def test_recheck_refuses_now_u_row(self, tmp_path):
        db_path = _make_db(tmp_path)
        # 清单错配：把 temp- 拍板面行当待删行=现判非 A，拒删一条不动
        m = self._manifest(
            tmp_path, [("om-deadtarget1", "client-key:temp-1791591663058", "2026-10-10 00:21:04")]
        )
        with pytest.raises(mod.RefuseError, match="现判非 A"):
            mod.run(db_path, apply=True, manifest_path=m, backup_dir=tmp_path / "bak")
        assert len(_all_rows(db_path)) == len(A_ROWS) + len(U_ROWS)

    def test_recheck_refuses_revived_target(self, tmp_path):
        # 目标复活（清单落时双轴皆空，删前有消息落盘）=现判非 A 拒删
        db_path = _make_db(tmp_path)
        m = self._manifest(tmp_path, A_ROWS)
        db = sqlite3.connect(str(db_path))
        db.execute(
            "INSERT INTO agent_messages (session_id, content) VALUES (?, ?)",
            ("om-deadtarget1", "revived"),
        )
        db.commit()
        db.close()
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
        p.write_text(json.dumps({"checklist": [{"tag_name": "client-key:om-x"}]}))
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

        def lie(pairs, db):
            return 0  # 谎报删了但实际不动

        monkeypatch.setattr(mod, "delete_exact", lie)
        with pytest.raises(RuntimeError, match="待删行仍在场"):
            mod.run(db_path, apply=True, backup_dir=tmp_path / "bak")

    def test_readback_detects_non_target_loss(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path)
        real_delete = mod.delete_exact

        def overkill(pairs, db):
            real_delete(pairs, db)
            db2 = sqlite3.connect(str(db))
            db2.execute("DELETE FROM session_tags WHERE tag_name = 'agent:soulmate'")
            db2.commit()
            db2.close()
            return len(pairs)

        monkeypatch.setattr(mod, "delete_exact", overkill)
        with pytest.raises(RuntimeError, match="非目标行被误删"):
            mod.run(db_path, apply=True, backup_dir=tmp_path / "bak")

    def test_concurrent_new_rows_reported_not_deleted(self, tmp_path, monkeypatch):
        db_path = _make_db(tmp_path)
        real_delete = mod.delete_exact

        def with_concurrent(pairs, db):
            db2 = sqlite3.connect(str(db))
            db2.execute(
                "INSERT INTO session_tags (session_id, tag_name, created_at) VALUES (?, ?, ?)",
                ("om-concurrent1", "client-key:grp-newborn", "2026-10-18 12:00:00"),
            )
            db2.commit()
            db2.close()
            return real_delete(pairs, db)

        monkeypatch.setattr(mod, "delete_exact", with_concurrent)
        rep = mod.run(db_path, apply=True, backup_dir=tmp_path / "bak")
        assert rep["concurrent_new_rows"] == [("om-concurrent1", "client-key:grp-newborn")]
        after = {r[:2] for r in _all_rows(db_path)}
        assert ("om-concurrent1", "client-key:grp-newborn") in after

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
            "pending_decision_evidence",
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


class TestPendingDecisionEvidence:
    def test_temp_dead_rows_evidence_only_not_deleted(self, tmp_path):
        db_path = _make_db(tmp_path)
        rep = mod.run(db_path, apply=True, backup_dir=tmp_path / "bak")
        evidence = rep["pending_decision_evidence"]
        keys = {(e["session_id"], e["tag_name"]) for e in evidence}
        assert ("om-deadtarget1", "client-key:temp-1791591663058") in keys
        # temp- 活目标不进死证据
        assert ("om-live0001", "client-key:temp-1791603521735") not in keys
        after = {r[:2] for r in _all_rows(db_path)}
        # 证据行只报不删
        assert ("om-deadtarget1", "client-key:temp-1791591663058") in after


class TestStaticWiring:
    def test_tool_consumes_single_source_classifier(self):
        src = _SCRIPT.read_text()
        assert "census.classify_session_alias(" in src
        # 禁第二套判法：工具内不得出现客户端 id 形态字面手抄（判法只在 census）
        assert "client-key:om-" not in src
        assert "client-key:grp-" not in src
        assert "client-key:temp-" not in src

    def test_census_classifier_is_pure_single_source(self):
        assert callable(CENSUS.classify_session_alias)
        res = CENSUS.classify_session_alias("client-key:om-x", False, 0)
        assert isinstance(res, tuple) and res[0] == "A"
