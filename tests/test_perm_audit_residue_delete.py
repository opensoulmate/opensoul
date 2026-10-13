"""TL-⑨ perm 决策审计 A 类删除——按清单精确删+备份+回读（全 tmp，绝不碰生产库）。

覆盖：dry-run 零写入 / 只删 A 类两组（会话硬标识 612 型 + 工具名硬标识 105 型，
非 A 含真实 om-* 一行不动）/ 复核拒删（现判非 A=一行不动）/ 清单词表漂移拒删 /
工件不可信拒删 / 计数漂移非致命 / 清单缺行非致命+清单外单列 / 幂等重跑 /
备份快照 sha256 可回读 / 总数对账 / 分类词表单一源（census.A_RULE_TOOLS 运行时真消费）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_perm_audit_residue.py"
_spec = importlib.util.spec_from_file_location("delete_perm_audit_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

from src.immune.permission_engine import PermissionStore  # noqa: E402

census = mod.census  # 分类词表单一源（脚本同源 import，禁第二套词表）

# A 会话硬标识组（census.A_SESSIONS / A_SESSION_PREFIX 原语义）
A_SESSION_ROWS = [
    ("d1", "t1", "read_file"),
    ("d2", "api-test", "write_file"),
    ("d3", "e2e", "read_file"),
    ("d4", "py-exfil-probe", "terminal"),
    ("d5", "p09-1791766147", "read_file"),
    ("d6", "p010-pre", "read_file"),
]
# A 工具名硬标识组（census.A_RULE_TOOLS，TL-⑤ 留档点名的 105 型）
A_TOOL_ROWS = [
    ("d7", "", "test_dangerous_tool"),
    ("d8", "", "test_dangerous_tool"),
]
# other=不动（空 session 无硬标识 / 真实 om-* 会话 / 无硬标识测试名会话）
OTHER_ROWS = [
    ("n1", "", "read_file"),
    ("n2", "om-abc123def456", "write_file"),
    ("n3", "evo-test-verify", "read_file"),
    ("n4", "probe", "terminal"),
]


def _db(tmp_path: Path) -> Path:
    return tmp_path / "permission.db"


def _seed(tmp_path: Path, extra_rows=()) -> Path:
    """PermissionStore 建 schema（DDL 单一源）+ 直插受控 decision_id 行。"""
    db = _db(tmp_path)
    PermissionStore(str(db))  # _init_db
    conn = sqlite3.connect(str(db))
    try:
        seq = 0
        for did, sess, tool in [*A_SESSION_ROWS, *A_TOOL_ROWS, *OTHER_ROWS, *extra_rows]:
            seq += 1
            conn.execute(
                "INSERT INTO perm_decision_audit (decision_id,session_id,tool_name,"
                "behavior,created_at) VALUES (?,?,?,'allow',?)",
                (did, sess, tool, 1700000000.0 + seq),
            )
        conn.commit()
    finally:
        conn.close()
    return db


def _artifact(tmp_path: Path, by_session: dict, a_total: int, drop_key: str | None = None) -> Path:
    perm = {"A_by_session": by_session, "A_total": a_total}
    if drop_key:
        perm.pop(drop_key)
    p = tmp_path / "artifact.json"
    p.write_text(json.dumps({"permission": perm}))
    return p


def _default_artifact(tmp_path: Path) -> Path:
    # 清单=6 个 A 会话键（A_TOOL_ROWS 归 A_RULE_TOOLS 工具键组，TL-⑤ 留档点名）
    return _artifact(
        tmp_path,
        {"t1": 1, "api-test": 1, "e2e": 1, "py-exfil-probe": 1, "p09-1791766147": 1, "p010-pre": 1},
        a_total=8,
    )


def _states(db: Path) -> dict:
    conn = sqlite3.connect(str(db))
    try:
        return {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                "SELECT decision_id,session_id,tool_name FROM perm_decision_audit"
            )
        }
    finally:
        conn.close()


def test_dry_run_zero_write(tmp_path):
    db = _seed(tmp_path)
    art = _default_artifact(tmp_path)
    before = _states(db)
    report = mod.run(db, art, apply=False)
    assert _states(db) == before  # dry-run 零写入
    assert report["mode"] == "dry-run"
    assert report["deleted"] == 0
    assert report["matched"] == 8
    assert report["artifact_drift"] is False


def test_apply_deletes_only_a_other_untouched(tmp_path):
    db = _seed(tmp_path)
    art = _default_artifact(tmp_path)
    report = mod.run(db, art, apply=True)
    after = _states(db)
    a_ids = {r[0] for r in [*A_SESSION_ROWS, *A_TOOL_ROWS]}
    assert a_ids.isdisjoint(after)  # A 两组全 gone
    for did, sess, tool in OTHER_ROWS:
        assert after[did] == (sess, tool)  # 非 A 含真实 om-* 一行不动
    assert report["deleted"] == 8
    assert report["deleted_by_rule"] == {"A_SESSIONS": 4, "A_SESSION_PREFIX": 2, "A_RULE_TOOLS": 2}
    assert report["total_reconcile"]["ok"] is True
    assert report["post_A_rows_not_in_deleted"] == []
    assert report["non_A_missing"] == []


def test_recheck_refuses_non_a_row(tmp_path):
    db = _seed(tmp_path)
    conn = sqlite3.connect(str(db))
    try:
        with pytest.raises(mod.RefuseError):
            mod.recheck(conn, ["n1", "n2"])  # 现判非 A=拒删
    finally:
        conn.close()
    assert "n1" in _states(db)  # 一行不动


def test_recheck_missing_row_nonfatal(tmp_path):
    db = _seed(tmp_path)
    conn = sqlite3.connect(str(db))
    try:
        missing = mod.recheck(conn, ["d1", "evt_gone"])
    finally:
        conn.close()
    assert missing == ["evt_gone"]  # 缺行如实报非致命


def test_checklist_wordlist_drift_refuses(tmp_path, monkeypatch):
    db = _seed(tmp_path)
    art = _default_artifact(tmp_path)
    before = _states(db)
    monkeypatch.setattr(census, "A_SESSIONS", set())  # 词表漂移：清单会话现判非 A
    with pytest.raises(mod.RefuseError):
        mod.run(db, art, apply=True)
    assert _states(db) == before  # 拒删一行不动


def test_artifact_untrusted_refuses(tmp_path):
    db = _seed(tmp_path)
    art = _artifact(tmp_path, {"t1": 1}, 1, drop_key="A_by_session")
    before = _states(db)
    with pytest.raises(mod.RefuseError):
        mod.run(db, art, apply=True)
    assert _states(db) == before


def test_artifact_count_drift_nonfatal(tmp_path):
    db = _seed(tmp_path)
    art = _artifact(
        tmp_path,
        {"t1": 1, "api-test": 1, "e2e": 1, "py-exfil-probe": 1, "p09-1791766147": 1, "p010-pre": 1},
        a_total=999,
    )  # 工件计数陈旧
    report = mod.run(db, art, apply=True)
    assert report["artifact_drift"] is True
    assert report["deleted"] == 8  # 计数漂移非致命，按单一源真删


def test_missing_session_and_beyond_checklist(tmp_path):
    db = _seed(tmp_path)
    # 清单缺 t2（无行）+ e2e 行落清单外（A 但不落清单会话组、非工具组）=单列
    art = _artifact(
        tmp_path,
        {"t1": 1, "api-test": 1, "t2": 1, "py-exfil-probe": 1, "p09-1791766147": 1, "p010-pre": 1},
        a_total=8,
    )
    report = mod.run(db, art, apply=True)
    assert report["checklist"]["missing_sessions"] == ["t2"]
    assert [r["decision_id"] for r in report["checklist"]["beyond_checklist"]] == ["d3"]
    assert report["checklist"]["tool_checklist_rows"] == 2
    assert report["deleted"] == 8  # 清单外同判据一并删、单列报告
    assert "d3" not in _states(db)


def test_idempotent_rerun(tmp_path):
    db = _seed(tmp_path)
    art = _default_artifact(tmp_path)
    mod.run(db, art, apply=True)
    report = mod.run(db, art, apply=True)  # 幂等重跑
    assert report["matched"] == 0
    assert report["deleted"] == 0
    assert report["checklist"]["missing_sessions"] == [
        "api-test",
        "e2e",
        "p010-pre",
        "p09-1791766147",
        "py-exfil-probe",
        "t1",
    ]


def test_backup_snapshot_readable(tmp_path):
    db = _seed(tmp_path)
    art = _default_artifact(tmp_path)
    backup_dir = tmp_path / "backups"
    report = mod.run(db, art, apply=True, backup_dir=backup_dir)
    b = report["backup"]
    bpath = Path(b["path"])
    assert bpath.exists()
    assert hashlib.sha256(bpath.read_bytes()).hexdigest() == b["sha256"]
    conn = sqlite3.connect(str(bpath))
    try:
        n = conn.execute("SELECT COUNT(*) FROM perm_decision_audit").fetchone()[0]
    finally:
        conn.close()
    assert n == len(A_SESSION_ROWS) + len(A_TOOL_ROWS) + len(OTHER_ROWS)  # 删前全行可回读


def test_single_source_runtime_consume(tmp_path, monkeypatch):
    """词表单一源运行时真消费：A_RULE_TOOLS 扩词表=新工具名行随之判 A 被删。"""
    db = _seed(tmp_path, extra_rows=[("x1", "", "evil_tool")])
    art = _default_artifact(tmp_path)
    monkeypatch.setattr(census, "A_RULE_TOOLS", {"test_dangerous_tool", "evil_tool"})
    report = mod.run(db, art, apply=True)
    assert report["deleted"] == 9
    assert report["deleted_by_rule"]["A_RULE_TOOLS"] == 3  # 真消费硬证
    assert "x1" not in _states(db)


def test_report_keys(tmp_path):
    db = _seed(tmp_path)
    art = _default_artifact(tmp_path)
    report = mod.run(db, art, apply=True, report_path=tmp_path / "report.json")
    for key in (
        "tool",
        "mode",
        "db",
        "artifact",
        "artifact_A_total",
        "live_A",
        "artifact_drift",
        "matched",
        "deleted_by_rule",
        "checklist",
        "recheck_missing",
        "pre",
        "post",
        "deleted_ids",
        "backup",
        "deleted",
        "post_A_rows_not_in_deleted",
        "non_A_missing",
        "concurrent_new_rows",
        "total_reconcile",
    ):
        assert key in report, key
    assert json.loads((tmp_path / "report.json").read_text())["tool"] == "delete_perm_audit_residue"
