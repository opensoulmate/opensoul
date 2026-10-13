"""TL-⑤ perm 测试规则失效化——软删语义（全 tmp，绝不碰生产库）。

覆盖：dry-run 零写入 / 只动清单内 A 类（非 A 一行不动）/ 复核拒执行（清单错配=一行不动）/
缺行拒执行 / 幂等重跑 / 清单外新残留一并失效化单列报告 / 备份快照 sha256 可回读 /
软删非硬删（行仍在、引擎不再消费）/ 真引擎决策语义（失效前 deny、失效后不再 deny）/
分类词表单一源（census.A_RULE_* 运行时真消费）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "deactivate_test_perm_rules.py"
_spec = importlib.util.spec_from_file_location("deactivate_test_perm_rules", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

from src.immune.permission_engine import (  # noqa: E402
    PermissionBehavior,
    PermissionService,
    PermissionStore,
)

census = mod.census  # 分类词表单一源（脚本同源 import，禁第二套词表）

# 行样本：A=确证测试残留（单一源 census.classify_rule），other=不动
A_ROWS = [
    ("a1", "test_dangerous_tool", "*", "deny", "user"),
    ("a2", "test_dangerous_tool", "*", "deny", "user"),
    ("a3", "write_file", "**/.ssh/**", "allow", "p09-probe"),
]
OTHER_ROWS = [
    ("n1", "read_file", "*", "ask", "builtin"),
    ("n2", "terminal", "*", "allow", "user"),
]


def _db(tmp_path: Path) -> Path:
    return tmp_path / "permission.db"


def _seed(tmp_path: Path, extra_rows=()) -> Path:
    """PermissionStore 建 schema（DDL 单一源）+ 直插受控 rule_id 行。"""
    db = _db(tmp_path)
    PermissionStore(str(db))  # _init_db
    conn = sqlite3.connect(str(db))
    try:
        seq = 0
        for rid, tool, content, behavior, source in [*A_ROWS, *OTHER_ROWS, *extra_rows]:
            seq += 1
            conn.execute(
                "INSERT INTO perm_rules (rule_id,tool_name,rule_content,behavior,source,"
                "hard,risk,seq,created_at) VALUES (?,?,?,?,?,0,'medium',?,?)",
                (rid, tool, content, behavior, source, seq, 1700000000.0 + seq),
            )
        conn.commit()
    finally:
        conn.close()
    return db


def _artifact(tmp_path: Path, ids: list[str]) -> Path:
    p = tmp_path / "artifact.json"
    p.write_text(json.dumps({"permission": {"rules_A": [{"rule_id": i} for i in ids]}}))
    return p


def _states(db: Path) -> dict:
    conn = sqlite3.connect(str(db))
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT rule_id,is_deleted FROM perm_rules")}
    finally:
        conn.close()


def test_dry_run_zero_changes(tmp_path):
    db = _seed(tmp_path)
    art = _artifact(tmp_path, ["a1", "a2", "a3"])
    before = _states(db)
    report = mod.run(db, art, apply=False)
    assert report["mode"] == "dry-run"
    assert report["deactivated"] == 0
    assert report["checklist_active"] == 3
    assert _states(db) == before


def test_apply_deactivates_only_targets(tmp_path):
    db = _seed(tmp_path)
    art = _artifact(tmp_path, ["a1", "a2", "a3"])
    report = mod.run(db, art, apply=True, backup_dir=tmp_path / "bak")
    assert report["deactivated"] == 3
    st = _states(db)
    assert st["a1"] == 1 and st["a2"] == 1 and st["a3"] == 1
    assert st["n1"] == 0 and st["n2"] == 0  # 非 A 一行不动
    assert report["readback"]["active_A_after"] == 0
    pre_non_a = sum(report["pre"]["non_A_active"].values())
    assert pre_non_a >= 2  # 至少我种的 2 条（store 播种 builtin 不定数）
    assert report["readback"]["non_A_pre_rows_still_active"] == pre_non_a


def test_refuse_contaminated_checklist_zero_changes(tmp_path):
    db = _seed(tmp_path)
    art = _artifact(tmp_path, ["a1", "n1"])  # n1=非 A 混入清单
    before = _states(db)
    with pytest.raises(mod.RefuseError, match="非 A"):
        mod.run(db, art, apply=True, backup_dir=tmp_path / "bak")
    assert _states(db) == before  # 一行不动


def test_refuse_missing_row(tmp_path):
    db = _seed(tmp_path)
    art = _artifact(tmp_path, ["a1", "ghost"])
    before = _states(db)
    with pytest.raises(mod.RefuseError, match="不存在"):
        mod.run(db, art, apply=True, backup_dir=tmp_path / "bak")
    assert _states(db) == before


def test_idempotent_rerun(tmp_path):
    db = _seed(tmp_path)
    art = _artifact(tmp_path, ["a1", "a2", "a3"])
    mod.run(db, art, apply=True, backup_dir=tmp_path / "bak")
    report2 = mod.run(db, art, apply=True, backup_dir=tmp_path / "bak")
    assert report2["already_inactive"] == 3
    assert report2["deactivated"] == 0
    assert report2["readback"]["active_A_after"] == 0


def test_beyond_checklist_extra_deactivated_and_reported(tmp_path):
    db = _seed(tmp_path, extra_rows=[("a4", "test_dangerous_tool", "*", "deny", "user")])
    art = _artifact(tmp_path, ["a1", "a2", "a3"])
    report = mod.run(db, art, apply=True, backup_dir=tmp_path / "bak")
    assert report["beyond_checklist"] == ["a4"]
    assert report["deactivated"] == 4
    assert _states(db)["a4"] == 1


def test_backup_snapshot_sha256(tmp_path):
    db = _seed(tmp_path)
    art = _artifact(tmp_path, ["a1"])
    report = mod.run(db, art, apply=True, backup_dir=tmp_path / "bak")
    bak = report["backup"]
    p = Path(bak["path"])
    assert p.exists()
    assert hashlib.sha256(p.read_bytes()).hexdigest() == bak["sha256"]
    conn = sqlite3.connect(str(p))
    try:
        # 快照=执行前全库（含 store 播种 builtin），行数与 pre 对账
        assert (
            conn.execute("SELECT COUNT(*) FROM perm_rules").fetchone()[0]
            == report["pre"]["total_rows"]
        )
    finally:
        conn.close()


def test_soft_delete_rows_preserved_engine_stops_consuming(tmp_path):
    db = _seed(tmp_path)
    art = _artifact(tmp_path, ["a1", "a2", "a3"])
    report = mod.run(db, art, apply=True, backup_dir=tmp_path / "bak")
    st = _states(db)
    # 软删非硬删：行数不变（含 store 播种 builtin），目标行仍在
    assert len(st) == report["pre"]["total_rows"]
    assert st["a1"] == 1 and st["n1"] == 0
    store = PermissionStore(str(db))
    active = store.list_rules()
    assert all(r["tool_name"] != "test_dangerous_tool" for r in active)
    assert all(r["source"] != "p09-probe" for r in active)
    deleted = store.list_rules(include_deleted=True)
    assert len(deleted) == report["pre"]["total_rows"]  # include_deleted 仍可回读（可回滚）


def test_engine_decision_liveness(tmp_path):
    """修生产行为真证据：残留规则在场→deny；失效化后→不再 deny（回落模式兜底 ask）。"""
    db = _seed(tmp_path)
    art = _artifact(tmp_path, ["a1", "a2", "a3"])
    svc = PermissionService(PermissionStore(str(db)))
    before = svc.check("test_dangerous_tool", {})
    assert before.behavior == PermissionBehavior.DENY
    mod.run(db, art, apply=True, backup_dir=tmp_path / "bak")
    after = svc.check("test_dangerous_tool", {})
    assert after.behavior != PermissionBehavior.DENY
    assert after.behavior == PermissionBehavior.ASK  # ACCEPT_EDITS 模式兜底


def test_classify_rule_vocabulary_single_source(monkeypatch):
    """分类判法=词表运行时真消费（禁手抄漂移）。"""
    assert census.classify_rule("test_dangerous_tool", "user") == ("A", "A_RULE_TOOLS")
    assert census.classify_rule("write_file", "p09-probe") == ("A", "A_RULE_SOURCES")
    assert census.classify_rule("read_file", "builtin") == ("other", "unmatched")
    monkeypatch.setattr(census, "A_RULE_TOOLS", {"zzz_tool"})
    assert census.classify_rule("zzz_tool", "user") == ("A", "A_RULE_TOOLS")
    assert census.classify_rule("test_dangerous_tool", "user") == ("other", "unmatched")
