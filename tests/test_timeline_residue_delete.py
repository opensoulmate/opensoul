"""TL-④ 删除执行件①——timeline A 类按清单精确删语义（全 tmp，绝不碰生产库）。

覆盖：dry-run 零写入 / 按清单精确删（B/U 一行不动）/ 缺行如实报 /
删前逐行复核拒删（清单错配=一行不动）/ A·B 清单相交拒删 / 备份快照可回读。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_timeline_test_residue.py"
_spec = importlib.util.spec_from_file_location("delete_timeline_test_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

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

# 行样本：分类判法单一源=census.classify_timeline
# A：Sandbox created: test_sandbox*（sandbox_fixture_names）
A_ROWS = [
    ("evt_a1", "mirror", "sandbox_created", "Sandbox created: test_sandbox-1"),
    ("evt_a2", "mirror", "sandbox_created", "Sandbox created: var_test-2"),
    ("evt_a3", "brain", "verify", "verify ok"),  # detail 带 stub-agent → A
]
B_ROWS = [
    ("evt_b1", "immune", "request_blocked", "rate limit POST /api/nerve/publish"),
    ("evt_b2", "pipeline", "pipeline_completed", "[Pipeline] readme.md done"),
]
U_ROWS = [
    ("evt_u1", "brain", "think", "总结一下今天的工作"),
    ("evt_u2", "healer", "diagnosis", "vital probe"),
]
TS = 1791900000.0


def _mk_db(path: Path, rows) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    for i, (eid, organ, etype, summary) in enumerate(rows):
        detail = '{"source":"stub-agent"}' if eid == "evt_a3" else "{}"
        conn.execute(
            "INSERT INTO events(event_id,organ,event_type,summary,detail,timestamp,collected_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (eid, organ, etype, summary, detail, TS + i, TS + i),
        )
    conn.commit()
    conn.close()


def _mk_artifact(path: Path, a_ids, b_ids) -> None:
    path.write_text(
        json.dumps({"timeline": {"A_event_ids": list(a_ids), "B_event_ids": list(b_ids)}})
    )


def _rows(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT event_id FROM events")}
    finally:
        conn.close()


@pytest.fixture()
def scene(tmp_path):
    db = tmp_path / "timeline.db"
    artifact = tmp_path / "census.json"
    _mk_db(db, A_ROWS + B_ROWS + U_ROWS)
    _mk_artifact(artifact, ["evt_a1", "evt_a2", "evt_a3", "evt_gone"], ["evt_b1", "evt_b2"])
    return {
        "db": db,
        "artifact": artifact,
        "backup_dir": tmp_path / "bk",
        "report": tmp_path / "report.json",
    }


def test_load_artifact_ids(scene):
    a_ids, b_ids = mod.load_artifact_ids(scene["artifact"])
    assert a_ids == ["evt_a1", "evt_a2", "evt_a3", "evt_gone"]
    assert b_ids == ["evt_b1", "evt_b2"]


def test_dry_run_zero_writes(scene):
    rep = mod.run(
        scene["db"],
        scene["artifact"],
        apply=False,
        backup_dir=scene["backup_dir"],
        report_path=scene["report"],
    )
    assert rep["mode"] == "dry-run"
    assert rep["deleted"] == 0
    assert rep["matched"] == 3 and rep["missing"] == 1
    assert _rows(scene["db"]) == {r[0] for r in A_ROWS + B_ROWS + U_ROWS}
    assert not scene["backup_dir"].exists()


def test_apply_deletes_exactly_listed(scene):
    rep = mod.run(
        scene["db"],
        scene["artifact"],
        apply=True,
        backup_dir=scene["backup_dir"],
        report_path=scene["report"],
    )
    assert rep["deleted"] == 3
    assert rep["missing"] == 1  # evt_gone 缺行如实报，非致命
    # A 清单行全清，B/U 一行不动
    assert _rows(scene["db"]) == {"evt_b1", "evt_b2", "evt_u1", "evt_u2"}
    assert rep["post"] == {"total": 4, "A": 0, "B": 2, "U": 2}
    assert rep["b_list_rows_before"] == 2 and rep["b_list_rows_after"] == 2
    assert rep["post_A_ids_not_in_artifact"] == []


def test_refuse_when_listed_row_now_not_A(scene):
    # 清单错配：把 U 类行塞进 A 清单 → 拒删，一行不动
    _mk_artifact(scene["artifact"], ["evt_a1", "evt_u1"], ["evt_b1", "evt_b2"])
    with pytest.raises(mod.RefuseError, match="现判非 A"):
        mod.run(
            scene["db"],
            scene["artifact"],
            apply=True,
            backup_dir=scene["backup_dir"],
            report_path=scene["report"],
        )
    assert _rows(scene["db"]) == {r[0] for r in A_ROWS + B_ROWS + U_ROWS}


def test_refuse_when_ab_lists_overlap(scene):
    _mk_artifact(scene["artifact"], ["evt_a1", "evt_b1"], ["evt_b1", "evt_b2"])
    with pytest.raises(mod.RefuseError, match="相交"):
        mod.run(
            scene["db"],
            scene["artifact"],
            apply=True,
            backup_dir=scene["backup_dir"],
            report_path=scene["report"],
        )
    assert _rows(scene["db"]) == {r[0] for r in A_ROWS + B_ROWS + U_ROWS}


def test_backup_snapshot_contains_pre_rows(scene):
    rep = mod.run(
        scene["db"],
        scene["artifact"],
        apply=True,
        backup_dir=scene["backup_dir"],
        report_path=scene["report"],
    )
    backup = Path(rep["backup"]["path"])
    assert backup.exists()
    assert len(rep["backup"]["sha256"]) == 64
    # 备份=删前全库快照（可整体回滚）
    assert _rows(backup) == {r[0] for r in A_ROWS + B_ROWS + U_ROWS}
    assert _rows(scene["db"]) != _rows(backup)


def test_report_artifact_written(scene):
    rep = mod.run(
        scene["db"],
        scene["artifact"],
        apply=True,
        backup_dir=scene["backup_dir"],
        report_path=scene["report"],
    )
    on_disk = json.loads(scene["report"].read_text())
    assert on_disk["deleted"] == rep["deleted"] == 3
    assert on_disk["pre"] == {"total": 7, "A": 3, "B": 2, "U": 2}
    assert on_disk["backup"]["path"] == rep["backup"]["path"]
