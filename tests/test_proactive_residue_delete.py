"""PROACT-④ 删除执行件——proactive.db 测试噪音按清单精确删语义（全 tmp，绝不碰生产库）。

覆盖：dry-run 零写入 / 按清单精确删（A+orphan B/U 删、near_start·cadence B/U 留）/
缺行非致命 / 删前逐行复核拒删（类错配、层错配）/ 工件缺键·清单相交·orphan_reliable
False·journal 不可用四闸拒删 / 备份快照可回读 / 清单外同判据只报告不删 /
单一源运行时真消费 / 幂等重跑。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_proactive_test_residue.py"
_spec = importlib.util.spec_from_file_location("delete_proactive_test_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

DDL = """
CREATE TABLE proactive_events (
    id TEXT PRIMARY KEY, watcher TEXT NOT NULL,
    ok INTEGER NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL, duration_ms REAL NOT NULL,
    result TEXT DEFAULT '{}', error TEXT DEFAULT '', tenant_id TEXT DEFAULT 'default')
"""

# 时间线（ISO 串，census.proactive_row_verdicts 按 ts[:19] 分秒）：
# base=2026-01-01T00:00:00（journal start 假桩落在 base，±15s 窗口=near_start 层）
# 判定期望（memory_decay interval 21600/experience_collect 3600/heredity 1800/pattern_extract 7200）：
#   c1  00:00:00 heredity  → U + near_start  留
#   c2  00:00:02 pattern   → U + near_start  留
#   a3  00:00:05 memory ×2 → A + near_start/orphan 删（A 任意层）
#   b1  00:00:30 experience_collect + b2 heredity 同秒双行 → 各 B
#        b1=该 watcher 链首=B+cadence 留；b2 链槽被 c1 占=B+orphan 删
#   u_o 00:00:40 pattern（链槽被 c2 占）→ U + orphan 删
#   a1  01-02 00:00:00 memory ×2 → A 删（a1 cadence/a2 orphan）
#   u_k 01-02 12:00:00 memory 单行 → U + cadence 留（距 a1 43200s ≥ 21595）
ROWS = [
    ("c1", "heredity_triggers", "2026-01-01T00:00:00.000"),
    ("c2", "pattern_extract", "2026-01-01T00:00:02.000"),
    ("a3", "memory_decay", "2026-01-01T00:00:05.000"),
    ("a4", "memory_decay", "2026-01-01T00:00:05.100"),  # 同秒（ts[:19] 同）→ A
    ("b1", "experience_collect", "2026-01-01T00:00:30.000"),
    ("b2", "heredity_triggers", "2026-01-01T00:00:30.200"),  # 同秒异 watcher → 各 B
    ("u_o", "pattern_extract", "2026-01-01T00:00:40.000"),
    ("a1", "memory_decay", "2026-01-02T00:00:00.000"),
    ("a2", "memory_decay", "2026-01-02T00:00:00.500"),  # 同秒 → A
    ("u_k", "memory_decay", "2026-01-02T12:00:00.000"),
]
JOURNAL_STARTS = [datetime.fromisoformat("2026-01-01T00:00:00").timestamp()]
# 注：epoch 均按本地时区解释（datetime.fromisoformat(...).timestamp()），假桩同源构造
DELETE_ROWS = {"a1", "a2", "a3", "a4", "b2", "u_o"}
KEEP_ROWS = {"c1", "c2", "b1", "u_k"}


def _mk_db(path: Path, rows=ROWS) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    for rid, w, ts in rows:
        conn.execute(
            "INSERT INTO proactive_events(id,watcher,ok,started_at,finished_at,duration_ms)"
            " VALUES(?,?,1,?,?,1.0)",
            (rid, w, ts, ts),
        )
    conn.commit()
    conn.close()


def _rows(path: Path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT id FROM proactive_events")}
    finally:
        conn.close()


def _artifact_from_scan(db: Path, path: Path, orphan_reliable=True) -> dict:
    """清单=census 单一源真跑（composition 断言锁定期望判定）。"""
    conn = sqlite3.connect(str(db))
    try:
        s = mod.scan(conn, JOURNAL_STARTS)
    finally:
        conn.close()
    v = s["verdicts"]
    assert {i for i, c in v.items() if c[0] == "A"} == {"a1", "a2", "a3", "a4"}
    assert {i for i, c in v.items() if c[0] == "B"} == {"b1", "b2"}
    assert {i for i, c in v.items() if c[0] == "U"} == {"c1", "c2", "u_o", "u_k"}
    assert v["b2"][2] == "orphan" and v["b1"][2] == "cadence"
    assert v["u_o"][2] == "orphan" and v["u_k"][2] == "cadence"
    assert v["c1"][2] == "near_start" and v["c2"][2] == "near_start"

    def pick(cls, layer):
        return sorted(i for i, c in v.items() if c[0] == cls and c[2] == layer)

    p = {
        "A_event_ids": pick("A", "near_start") + pick("A", "cadence") + pick("A", "orphan"),
        "B_orphan_ids": pick("B", "orphan"),
        "U_orphan_ids": pick("U", "orphan"),
        "B_near_start_ids": pick("B", "near_start"),
        "B_cadence_ids": pick("B", "cadence"),
        "U_near_start_ids": pick("U", "near_start"),
        "U_cadence_ids": pick("U", "cadence"),
        "facts": {"orphan_reliable": orphan_reliable},
    }
    path.write_text(json.dumps({"proactive": p}))
    return p


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "proactive.db"
    _mk_db(db)
    art = tmp_path / "census.json"
    _artifact_from_scan(db, art)
    monkeypatch.setattr(mod.census, "load_journal_starts", lambda: list(JOURNAL_STARTS))
    return {"db": db, "art": art, "tmp": tmp_path}


def test_dry_run_zero_write(env):
    rep = mod.run(env["db"], env["art"], apply=False)
    assert rep["mode"] == "dry-run" and rep["deleted"] == 0
    assert _rows(env["db"]) == DELETE_ROWS | KEEP_ROWS
    assert rep["matched"] == len(DELETE_ROWS)
    assert sum(rep["missing"].values()) == 0


def test_apply_exact_delete_and_keep_untouched(env):
    rep = mod.run(env["db"], env["art"], apply=True, backup_dir=env["tmp"] / "backups")
    assert rep["deleted"] == len(DELETE_ROWS)
    assert _rows(env["db"]) == KEEP_ROWS  # near_start/cadence 留一行不动
    assert rep["keep_rows_before"] == len(KEEP_ROWS)
    assert rep["keep_rows_after"] == len(KEEP_ROWS)
    assert rep["post"]["total"] == len(KEEP_ROWS)
    assert rep["pre"]["total"] == len(ROWS)


def test_backup_snapshot_roundtrip_and_sha(env):
    rep = mod.run(env["db"], env["art"], apply=True, backup_dir=env["tmp"] / "backups")
    b = rep["backup"]
    assert hashlib.sha256(Path(b["path"]).read_bytes()).hexdigest() == b["sha256"]
    conn = sqlite3.connect(b["path"])
    try:
        ids = {r[0] for r in conn.execute("SELECT id FROM proactive_events")}
    finally:
        conn.close()
    assert ids == DELETE_ROWS | KEEP_ROWS  # 删前全量快照可整体回滚


def test_class_mismatch_refuses_whole_run(env, monkeypatch):
    # 单一源运行时真消费：override classify_proactive 翻转判定 → A 行现判非 A → 整轮拒删
    monkeypatch.setattr(mod.census, "classify_proactive", lambda wsc, src: ("U", "unmatched"))
    with pytest.raises(mod.RefuseError, match="现判错类错层"):
        mod.run(env["db"], env["art"], apply=True, backup_dir=env["tmp"] / "b")
    assert _rows(env["db"]) == DELETE_ROWS | KEEP_ROWS  # 一行不动


def test_layer_mismatch_refuses(env, monkeypatch):
    # journal 假桩覆盖全部行时间 → 全员 near_start → B/U orphan 清单行现判非 orphan
    base = JOURNAL_STARTS[0]
    monkeypatch.setattr(
        mod.census,
        "load_journal_starts",
        lambda: [base + off for off in (0.0, 2.0, 5.0, 30.0, 40.0, 86400.0, 129600.0)],
    )
    with pytest.raises(mod.RefuseError, match="现判错类错层"):
        mod.run(env["db"], env["art"], apply=True, backup_dir=env["tmp"] / "b")
    assert _rows(env["db"]) == DELETE_ROWS | KEEP_ROWS


def test_artifact_missing_key_refuses(env):
    p = json.loads(env["art"].read_text())
    del p["proactive"]["U_orphan_ids"]
    env["art"].write_text(json.dumps(p))
    with pytest.raises(mod.RefuseError, match="工件缺"):
        mod.run(env["db"], env["art"], apply=True, backup_dir=env["tmp"] / "b")
    assert _rows(env["db"]) == DELETE_ROWS | KEEP_ROWS


def test_lists_intersect_refuses(env):
    p = json.loads(env["art"].read_text())
    p["proactive"]["B_orphan_ids"] = p["proactive"]["B_orphan_ids"] + ["u_k"]  # u_k∈保留清单
    env["art"].write_text(json.dumps(p))
    with pytest.raises(mod.RefuseError, match="相交"):
        mod.run(env["db"], env["art"], apply=True, backup_dir=env["tmp"] / "b")
    assert _rows(env["db"]) == DELETE_ROWS | KEEP_ROWS


def test_orphan_reliable_false_refuses(env):
    _artifact_from_scan(env["db"], env["art"], orphan_reliable=False)
    with pytest.raises(mod.RefuseError, match="orphan_reliable"):
        mod.run(env["db"], env["art"], apply=True, backup_dir=env["tmp"] / "b")
    assert _rows(env["db"]) == DELETE_ROWS | KEEP_ROWS


def test_journal_unavailable_refuses(env, monkeypatch):
    monkeypatch.setattr(mod.census, "load_journal_starts", lambda: None)
    with pytest.raises(mod.RefuseError, match="journal 不可用"):
        mod.run(env["db"], env["art"], apply=True, backup_dir=env["tmp"] / "b")
    assert _rows(env["db"]) == DELETE_ROWS | KEEP_ROWS


def test_missing_id_nonfatal_and_idempotent_rerun(env):
    p = json.loads(env["art"].read_text())
    p["proactive"]["A_event_ids"].append("ghost-id-not-present")
    env["art"].write_text(json.dumps(p))
    rep = mod.run(env["db"], env["art"], apply=True, backup_dir=env["tmp"] / "b1")
    assert rep["deleted"] == len(DELETE_ROWS)  # 缺行非致命，其余照删
    assert rep["missing"]["A_event_ids"] == 1
    assert _rows(env["db"]) == KEEP_ROWS
    # 幂等重跑：目标已不在场，deleted=0 不炸
    rep2 = mod.run(env["db"], env["art"], apply=True, backup_dir=env["tmp"] / "b2")
    assert rep2["deleted"] == 0 and rep2["matched"] == 0
    assert _rows(env["db"]) == KEEP_ROWS


def test_beyond_checklist_report_only(env):
    # 清单外同判据新残留（新 A 同秒对）：只报告不删（生产服务持续写入宁窄勿滥）
    conn = sqlite3.connect(str(env["db"]))
    for rid in ("x1", "x2"):
        conn.execute(
            "INSERT INTO proactive_events(id,watcher,ok,started_at,finished_at,duration_ms)"
            " VALUES(?,?,1,'2026-01-05T00:00:00.000','2026-01-05T00:00:00.000',1.0)",
            (rid, "memory_decay"),
        )
    conn.commit()
    conn.close()
    rep = mod.run(env["db"], env["art"], apply=True, backup_dir=env["tmp"] / "b")
    assert rep["beyond_checklist_count"] == 2
    assert set(rep["beyond_checklist"]) == {"x1", "x2"}
    remaining = _rows(env["db"])
    assert {"x1", "x2"} <= remaining  # 未删
    assert remaining & DELETE_ROWS == set()


def test_report_keys_contract(env):
    rep = mod.run(env["db"], env["art"], apply=False)
    for key in (
        "tool",
        "mode",
        "db",
        "artifact",
        "artifact_delete",
        "artifact_keep",
        "matched",
        "missing",
        "pre",
        "keep_rows_before",
        "beyond_checklist",
        "beyond_checklist_count",
        "deleted",
    ):
        assert key in rep, key
    assert rep["tool"] == "delete_proactive_test_residue"
    assert rep["artifact_delete"] == {"A_event_ids": 4, "B_orphan_ids": 1, "U_orphan_ids": 1}
    assert rep["artifact_keep"] == {
        "B_near_start_ids": 0,
        "B_cadence_ids": 1,
        "U_near_start_ids": 2,
        "U_cadence_ids": 1,
    }


def test_single_source_static_wiring():
    """工具真消费 census 单一源（写了≠接线了）：符号引用非手抄判法。"""
    src = _SCRIPT.read_text()
    assert "census.proactive_row_verdicts" in src
    assert "census.load_journal_starts" in src
    assert "classify_proactive" not in src.replace("census_test_residue", "")
