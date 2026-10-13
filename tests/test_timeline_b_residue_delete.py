"""TL-⑰ B 类可删三组按清单精确删语义（全 tmp，绝不碰生产库）。

覆盖：dry-run 零写入 / 三组精确删（storm·A·U 一行不动）/ storm 恒保留 /
缺行如实报幂等 / 删前逐行复核拒删（清单错配=一行不动）/ A·B 清单相交拒删 /
缺键拒删 / 备份快照可回读 / 回读对账 / 清单外同判据只报告不删 /
分组常量单一源（classify_timeline 返回值=B_RULES_ALL 逐值）。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_timeline_b_residue.py"
_spec = importlib.util.spec_from_file_location("delete_timeline_b_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

CENSUS = mod.census

DDL = """
CREATE TABLE events (
    event_id    TEXT PRIMARY KEY,
    organ       TEXT NOT NULL,
    emoji       TEXT DEFAULT '',
    event_type  TEXT NOT NULL,
    summary     TEXT NOT NULL,
    detail      TEXT DEFAULT '{}',
    timestamp   REAL NOT NULL,
    collected_at REAL NOT NULL,
    tenant_id TEXT DEFAULT 'default')
"""

# 行样本：分类判法单一源=census.classify_timeline（summary 命中各自 B 分组）
EXEC_ROWS = [
    ("evt_x1", "mirror", "sandbox_exec", "\U0001f9ea Sandbox exec: echo hi"),
    ("evt_x2", "mirror", "sandbox_fork", "\U0001f374 Sandbox forked: state"),
]
PIPE_ROWS = [
    ("evt_p1", "pipeline", "pipeline_completed", "[Pipeline] readme.md done"),
    ("evt_p2", "pipeline", "pipeline_completed", "[Pipeline] tagged.txt done"),
]
BRAIN_ROWS = [
    ("evt_g1", "brain", "think", "用一句话介绍一下K8s"),
    ("evt_g2", "brain", "think", "列出3个Linux发行版"),
]
STORM_ROWS = [
    ("evt_s1", "immune", "request_blocked", "rate limit POST /api/nerve/publish x1"),
    ("evt_s2", "immune", "request_blocked", "rate limit POST /api/nerve/publish x2"),
]
A_ROWS = [("evt_a1", "mirror", "sandbox_created", "Sandbox created: test_sandbox-1")]
U_ROWS = [
    ("evt_u1", "brain", "think", "总结一下今天的工作"),
    ("evt_u2", "healer", "diagnosis", "vital probe"),
]
ALL_ROWS = EXEC_ROWS + PIPE_ROWS + BRAIN_ROWS + STORM_ROWS + A_ROWS + U_ROWS
TS = 1791900000.0


def _mk_db(path: Path, rows=ALL_ROWS) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    for i, (eid, organ, etype, summary) in enumerate(rows):
        conn.execute(
            "INSERT INTO events(event_id,organ,event_type,summary,detail,timestamp,collected_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (eid, organ, etype, summary, "{}", TS + i, TS + i),
        )
    conn.commit()
    conn.close()


def _mk_artifact(path: Path, a_ids, b_ids, with_keys=True, overlap=False) -> None:
    tl = {}
    if with_keys:
        b = list(b_ids) + (list(a_ids) if overlap else [])
        tl = {"A_event_ids": list(a_ids), "B_event_ids": b}
    path.write_text(json.dumps({"timeline": tl}))


def _full_artifact(path: Path) -> None:
    b_ids = [r[0] for r in EXEC_ROWS + PIPE_ROWS + BRAIN_ROWS + STORM_ROWS]
    _mk_artifact(path, [r[0] for r in A_ROWS], b_ids)


def _ids(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT event_id FROM events")}
    finally:
        conn.close()


def _setup(tmp_path, artifact_fn=_full_artifact):
    db = tmp_path / "t.db"
    art = tmp_path / "art.json"
    _mk_db(db)
    artifact_fn(art)
    return db, art


TARGETS = {r[0] for r in EXEC_ROWS + PIPE_ROWS + BRAIN_ROWS}


def test_dry_run_zero_write(tmp_path):
    db, art = _setup(tmp_path)
    before = _ids(db)
    rep = mod.run(db, art, apply=False)
    assert rep["mode"] == "dry-run"
    assert rep["deleted"] == 0
    assert rep["targets"] == len(TARGETS) == 6
    assert rep["kept_storm"] == len(STORM_ROWS)
    assert _ids(db) == before  # 零写入


def test_apply_deletes_exactly_three_groups(tmp_path):
    db, art = _setup(tmp_path)
    rep = mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")
    assert rep["deleted"] == len(TARGETS)
    left = _ids(db)
    assert left & TARGETS == set()
    # storm/A/U 一行不动
    for eid in [r[0] for r in STORM_ROWS + A_ROWS + U_ROWS]:
        assert eid in left


def test_storm_kept_explicit(tmp_path):
    db, art = _setup(tmp_path)
    rep = mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")
    assert rep["storm_rows_before"] == rep["storm_rows_after"] == len(STORM_ROWS)
    assert rep["post_B_by_rule"].get(CENSUS.B_RULE_STORM) == len(STORM_ROWS)
    assert CENSUS.B_RULE_STORM not in rep["delete_rules"]


def test_missing_artifact_row_nonfatal_idempotent(tmp_path):
    def art_fn(p):
        b = [r[0] for r in EXEC_ROWS + PIPE_ROWS + BRAIN_ROWS + STORM_ROWS] + ["evt_gone"]
        _mk_artifact(p, [r[0] for r in A_ROWS], b)

    db, art = _setup(tmp_path, art_fn)
    rep = mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")
    assert rep["missing"] == 1
    assert rep["deleted"] == len(TARGETS)
    # 幂等重跑：目标已清零，缺行照旧如实报，不再删
    rep2 = mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")
    assert rep2["deleted"] == 0
    assert rep2["missing"] == 1 + len(TARGETS)


def test_drift_refuse_row_now_A(tmp_path):
    # 清单里一行现判 A（行内容被改写）=清单陈旧，拒删一行不动
    db, art = _setup(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE events SET summary='Sandbox created: test_sandbox-9' WHERE event_id='evt_x1'"
    )
    conn.commit()
    conn.close()
    before = _ids(db)
    with pytest.raises(mod.RefuseError, match="现判非 B"):
        mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")
    assert _ids(db) == before  # 一行不动


def test_drift_refuse_row_now_U(tmp_path):
    db, art = _setup(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE events SET summary='普通真实事件' WHERE event_id='evt_p1'")
    conn.commit()
    conn.close()
    with pytest.raises(mod.RefuseError):
        mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")


def test_artifact_missing_keys_refuse(tmp_path):
    db = tmp_path / "t.db"
    art = tmp_path / "art.json"
    _mk_db(db)
    _mk_artifact(art, [], [], with_keys=False)
    with pytest.raises(mod.RefuseError, match="不可信"):
        mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")


def test_ab_overlap_refuse(tmp_path):
    db, art = _setup(tmp_path)
    _mk_artifact(
        art,
        [r[0] for r in A_ROWS],
        [r[0] for r in EXEC_ROWS + PIPE_ROWS + BRAIN_ROWS + STORM_ROWS],
        overlap=True,
    )
    before = _ids(db)
    with pytest.raises(mod.RefuseError, match="相交"):
        mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")
    assert _ids(db) == before


def test_backup_snapshot_readable(tmp_path):
    db, art = _setup(tmp_path)
    rep = mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")
    bk = rep["backup"]
    assert Path(bk["path"]).exists()
    assert len(bk["sha256"]) == 64
    conn = sqlite3.connect(bk["path"])
    try:
        n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        ids = {r[0] for r in conn.execute("SELECT event_id FROM events")}
    finally:
        conn.close()
    assert n == len(ALL_ROWS)  # 删前全量快照
    assert TARGETS <= ids  # 待删行在备份里完好可回滚


def test_readback_accounting(tmp_path):
    db, art = _setup(tmp_path)
    rep = mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")
    assert rep["pre"]["total"] == len(ALL_ROWS)
    assert rep["post"]["total"] == len(ALL_ROWS) - len(TARGETS)
    assert rep["new_rows_during_run"] == 0
    assert rep["non_target_rows_before"] == len(ALL_ROWS) - len(TARGETS)
    # 新 B 残留信号（TL-④ post_A_ids_not_in_artifact 同款语义）：U 行不在清单属正常
    assert rep["post_B_ids_not_in_artifact"] == []


def test_beyond_checklist_reported_not_deleted(tmp_path):
    db, art = _setup(tmp_path)
    # 清单外同判据新残留（不删只报）
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO events(event_id,organ,event_type,summary,detail,timestamp,collected_at)"
        " VALUES(?,?,?,?,?,?,?)",
        ("evt_new_x", "mirror", "sandbox_exec", "\U0001f9ea Sandbox exec: new", "{}", TS, TS),
    )
    conn.commit()
    conn.close()
    rep = mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")
    assert rep["beyond_checklist"] == ["evt_new_x"]
    assert "evt_new_x" in _ids(db)  # 报告不动手


def test_row_survival_non_target(tmp_path):
    db, art = _setup(tmp_path)
    mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")
    left = _ids(db)
    non_target = {r[0] for r in ALL_ROWS} - TARGETS
    assert non_target <= left


def test_single_source_b_rule_constants():
    probes = {
        CENSUS.B_RULE_EXEC: EXEC_ROWS[0],
        CENSUS.B_RULE_PIPELINE: PIPE_ROWS[0],
        CENSUS.B_RULE_BRAIN: BRAIN_ROWS[0],
        CENSUS.B_RULE_STORM: STORM_ROWS[0],
    }
    for rule, (_eid, organ, etype, summary) in probes.items():
        cls, got = CENSUS.classify_timeline(organ, etype, summary, "{}")
        assert cls == "B"
        assert got == rule  # 返回值恰=常量，单一源非手抄
    assert set(CENSUS.B_RULES_ALL) == set(probes)
    # 工具分组并集=B 全集（无 rule 静默漏处置）
    assert (set(mod.DELETE_RULES) | mod.KEEP_RULES) == set(CENSUS.B_RULES_ALL)
    assert mod.DELETE_RULES & mod.KEEP_RULES == set()


def test_group_partition_gate_refuses_when_incomplete(tmp_path, monkeypatch):
    db, art = _setup(tmp_path)
    monkeypatch.setattr(mod, "DELETE_RULES", {CENSUS.B_RULE_EXEC})  # 分组不全
    with pytest.raises(mod.RefuseError, match="未覆盖"):
        mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")


def test_apply_zero_target_idempotent_empty_group(tmp_path):
    # 清单只含 storm：零目标零删，storm 全保留
    db = tmp_path / "t.db"
    art = tmp_path / "art.json"
    _mk_db(db)
    _mk_artifact(art, [r[0] for r in A_ROWS], [r[0] for r in STORM_ROWS])
    before = _ids(db)
    rep = mod.run(db, art, apply=True, backup_dir=tmp_path / "bk")
    assert rep["targets"] == 0 and rep["deleted"] == 0
    assert _ids(db) == before
