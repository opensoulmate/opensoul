"""PROACT-② proactive.db 历史测试噪音清点——分类矩阵/证据层/闸门/只读纪律（全 tmp，绝不碰生产库）。

覆盖：classify_proactive 机制事实矩阵（同秒同 watcher 重复→A、同秒多行秒单行→B、
孤立→U）/ A 组 excess=测试行数硬下界 / 证据层（journal 启动窗 near_start 优先于
心跳节奏链 cadence，其余 orphan）/ journal 不可用如实降级（orphan_reliable=False）
/ PROACT-① 收口闸门 / 只读纪律 / 分类词表单一源运行时真消费 / DEFAULT_INTERVALS
真源防漂移锁死 / main() 接线普查。
"""

from __future__ import annotations

import ast
import importlib.util
import sqlite3
from datetime import UTC, datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "census_test_residue.py"
_spec = importlib.util.spec_from_file_location("census_test_residue_proact2", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

DDL = """
CREATE TABLE proactive_events (
    id TEXT PRIMARY KEY, watcher TEXT NOT NULL, ok INTEGER NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT NOT NULL, duration_ms REAL NOT NULL,
    result TEXT DEFAULT '{}', error TEXT DEFAULT '', tenant_id TEXT DEFAULT 'default')
"""

BASE = 1791000000.0  # 早于 PROACT-① 收口闸门（PROACTIVE_CUTOFF）
_REAL_JOURNAL = object()  # 哨兵=不打桩走真 load_journal_starts


def iso(epoch):
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _mk_db(path, rows):
    """rows: (id, watcher, epoch) —— 同一秒=epoch 取整到秒后相同。"""
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    for rid, w, epoch in rows:
        ts = iso(epoch)
        conn.execute(
            "INSERT INTO proactive_events(id,watcher,ok,started_at,finished_at,duration_ms)"
            " VALUES(?,?,1,?,?,1.0)",
            (rid, w, ts, ts),
        )
    conn.commit()
    return conn


def _run(tmp_path, monkeypatch, rows, starts=_REAL_JOURNAL):
    """rows: (id, watcher, epoch)；starts=journal 启动时刻桩（list/None），哨兵=真取数。"""
    db = tmp_path / "proactive.db"
    _mk_db(db, rows).close()
    monkeypatch.setenv("PROACTIVE_DB", str(db))
    if starts is not _REAL_JOURNAL:
        monkeypatch.setattr(mod, "load_journal_starts", lambda: starts)
    out = {}
    mod.census_proactive(out)
    return out["proactive"]


# ---- 分类矩阵（纯函数单一源） ------------------------------------------------
@pytest.mark.parametrize(
    "watcher_sec,sec_total,cls,rule",
    [
        (2, 2, "A", mod.A_RULE_PROACTIVE_DUP),
        (2, 5, "A", mod.A_RULE_PROACTIVE_DUP),
        (3, 3, "A", mod.A_RULE_PROACTIVE_DUP),
        (1, 2, "B", mod.B_RULE_PROACTIVE_SEC_SINGLE),
        (1, 3, "B", mod.B_RULE_PROACTIVE_SEC_SINGLE),
        (1, 1, "U", "unmatched"),
    ],
)
def test_classify_matrix(watcher_sec, sec_total, cls, rule):
    assert mod.classify_proactive(watcher_sec, sec_total) == (cls, rule)


def test_rule_names_contract_locked():
    """规则名契约锁死（防判法漂移改名洗账）。"""
    assert mod.A_RULE_PROACTIVE_DUP == "same_second_watcher_duplicate"
    assert mod.B_RULE_PROACTIVE_SEC_SINGLE == "multi_row_second_singleton"


def _eval_num(node: ast.expr) -> float:
    """AST 数值求值（literal_eval 不吃 6 * 3600 这类常量算式，测试侧小求值器）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval_num(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp):
        l, r = _eval_num(node.left), _eval_num(node.right)
        if isinstance(node.op, ast.Mult):
            return l * r
        if isinstance(node.op, ast.Add):
            return l + r
        if isinstance(node.op, ast.Sub):
            return l - r
        if isinstance(node.op, ast.Div):
            return l / r
    raise ValueError(f"unsupported expr: {node!r}")


def test_default_intervals_matches_source():
    """心跳节奏证据层间隔=真源 DEFAULT_INTERVALS（防手抄漂移，改真源即炸）。"""
    tree = ast.parse((REPO / "src" / "will" / "proactive_behaviors.py").read_text())
    found = None
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and getattr(node.target, "id", "") == "DEFAULT_INTERVALS"
        ):
            assert isinstance(node.value, ast.Dict)
            found = {}
            for k, v in zip(node.value.keys, node.value.values):
                assert isinstance(k, ast.Constant) and isinstance(k.value, str)
                assert isinstance(v, ast.expr)
                found[k.value] = _eval_num(v)
    assert found is not None, "DEFAULT_INTERVALS 不在模块顶层"
    assert mod.PROACTIVE_INTERVALS == {k: float(v) for k, v in found.items()}


# ---- 桶与 excess 下界 --------------------------------------------------------
def test_census_buckets_and_excess(tmp_path, monkeypatch):
    rows = [
        ("md-1", "memory_decay", BASE + 0.1),
        ("md-2", "memory_decay", BASE + 0.2),
        ("md-3", "memory_decay", BASE + 0.3),
        ("ht-1", "heredity_triggers", BASE + 60.1),
        ("pe-1", "pattern_extract", BASE + 60.2),
        ("ec-1", "experience_collect", BASE + 120.0),
    ]
    res = _run(tmp_path, monkeypatch, rows, starts=[])
    assert res["total"] == 6
    assert res["A"] == 3 and res["B"] == 2 and res["U"] == 1
    assert res["A_event_ids"] == ["md-1", "md-2", "md-3"]
    # excess=组内除最早一行外（按 started_at,id 序）=测试行数硬下界
    assert res["A_excess_event_ids"] == ["md-2", "md-3"]
    assert res["facts"]["A_excess_lower_bound"] == 2
    assert res["B_event_ids"] == ["ht-1", "pe-1"]
    assert res["U_event_ids"] == ["ec-1"]
    assert res["facts"]["dup_groups"] == 1
    assert res["facts"]["dup_group_peak_per_sec"] == 3
    assert res["facts"]["by_watcher"] == {
        "memory_decay": 3,
        "heredity_triggers": 1,
        "pattern_extract": 1,
        "experience_collect": 1,
    }
    assert res["A_by_rule"] == {f"memory_decay:{mod.A_RULE_PROACTIVE_DUP}": 3}
    assert res["B_by_rule"] == {
        f"heredity_triggers:{mod.B_RULE_PROACTIVE_SEC_SINGLE}": 1,
        f"pattern_extract:{mod.B_RULE_PROACTIVE_SEC_SINGLE}": 1,
    }


def test_gate_after_proact1_cutoff(tmp_path, monkeypatch):
    late = mod.PROACTIVE_CUTOFF + 100.0
    rows = [
        ("old-a1", "memory_decay", BASE + 0.1),
        ("old-a2", "memory_decay", BASE + 0.2),
        ("new-a1", "memory_decay", late + 0.1),
        ("new-a2", "memory_decay", late + 0.2),
        ("new-u", "heredity_triggers", late + 60.0),
    ]
    res = _run(tmp_path, monkeypatch, rows, starts=[])
    assert res["facts"]["after_proact1_cutoff"] == {"A": 2, "B": 0, "U": 1}


# ---- 证据层（只作决策材料不改判） --------------------------------------------
def test_layers_near_start_cadence_orphan(tmp_path, monkeypatch):
    """near_start 优先于 cadence；cadence 贪心链（间隔 >= interval-5）；其余 orphan。"""
    t0 = BASE + 3600.0
    rows = [
        ("ht-start", "heredity_triggers", t0 + 3.0),  # journal 启动窗内→near_start
        ("ht-early", "heredity_triggers", t0 + 100.0),  # 距链首 100s < 1800-5→orphan
        ("ht-next", "heredity_triggers", t0 + 3.0 + 1800.0),  # 间隔≥interval-5→cadence
        ("md-lone", "memory_decay", t0 + 500.0),  # 该 watcher 首行=链首→cadence
    ]
    starts = [t0 + 2.0, t0 + 2.0 - 86400.0]
    res = _run(tmp_path, monkeypatch, rows, starts=sorted(starts))
    assert res["U_near_start_ids"] == ["ht-start"]
    assert res["U_cadence_ids"] == ["ht-next", "md-lone"]
    assert res["U_orphan_ids"] == ["ht-early"]
    assert res["facts"]["layers_total"] == {
        "near_start": 1,
        "cadence": 2,
        "orphan": 1,
    }
    assert res["facts"]["journal"]["available"] is True
    assert res["facts"]["journal"]["service_starts"] == 2
    assert res["facts"]["orphan_reliable"] is True


def test_layer_precedence_near_start_beats_cadence(tmp_path, monkeypatch):
    t0 = BASE + 3600.0
    rows = [
        ("ht-a", "heredity_triggers", t0 + 3.0),  # 链首且启动窗内→near_start
        ("ht-b", "heredity_triggers", t0 + 3.0 + 1800.0),  # cadence
    ]
    res = _run(tmp_path, monkeypatch, rows, starts=[t0])
    assert res["U_near_start_ids"] == ["ht-a"]
    assert res["U_cadence_ids"] == ["ht-b"]
    assert res["U_orphan_ids"] == []


def test_journal_unavailable_degrades_honestly(tmp_path, monkeypatch):
    t0 = BASE + 3600.0
    rows = [
        ("ht-a", "heredity_triggers", t0 + 3.0),
        ("ht-b", "heredity_triggers", t0 + 200.0),
    ]
    res = _run(tmp_path, monkeypatch, rows, starts=None)
    assert res["facts"]["journal"] == {
        "available": False,
        "service_starts": 0,
        "near_window_s": 15.0,
    }
    assert res["U_near_start_ids"] == []
    assert res["U_cadence_ids"] == ["ht-a"]
    assert res["U_orphan_ids"] == ["ht-b"]
    assert res["facts"]["orphan_reliable"] is False  # 禁当删除清单用


def test_a_rows_layered_transparently(tmp_path, monkeypatch):
    """A 组近启动窗（服务启动 burst 与测试 burst 撞秒的混合风险）如实计数。"""
    t0 = BASE + 3600.0
    rows = [
        ("md-1", "memory_decay", t0 + 3.1),
        ("md-2", "memory_decay", t0 + 3.2),
        ("md-3", "memory_decay", t0 + 3.3),
    ]
    res = _run(tmp_path, monkeypatch, rows, starts=[t0 + 2.0])
    assert res["A"] == 3
    assert res["facts"]["A_near_start_rows"] == 3
    assert res["facts"]["layers_total"]["near_start"] == 3


# ---- 只读纪律 / 单一源真消费 / 接线普查 --------------------------------------
def test_census_read_only(tmp_path, monkeypatch):
    rows = [("md-1", "memory_decay", BASE + 0.1), ("md-2", "memory_decay", BASE + 0.2)]
    db = tmp_path / "proactive.db"
    conn = _mk_db(db, rows)
    before = conn.execute("SELECT COUNT(*) FROM proactive_events").fetchone()[0]
    conn.close()
    monkeypatch.setenv("PROACTIVE_DB", str(db))
    monkeypatch.setattr(mod, "load_journal_starts", lambda: [])
    mod.census_proactive({})
    after = sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM proactive_events").fetchone()[0]
    assert before == after == 2
    # ro 连接拒写（mode=ro 真生效）
    ro = sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute(
            "INSERT INTO proactive_events(id,watcher,ok,started_at,finished_at,"
            "duration_ms) VALUES('x','y',1,'t','t',1.0)"
        )
    ro.close()


def test_classify_single_source_runtime_consumed(tmp_path, monkeypatch):
    """词表单一源运行时真消费：override 分类器→全表改判（禁 census 内手抄判法）。"""
    rows = [
        ("r1", "memory_decay", BASE + 0.1),
        ("r2", "heredity_triggers", BASE + 0.2),
        ("r3", "pattern_extract", BASE + 0.3),
    ]
    monkeypatch.setattr(mod, "classify_proactive", lambda a, b: ("A", "forced"))
    res = _run(tmp_path, monkeypatch, rows, starts=[])
    assert res["A"] == 3 and res["B"] == 0 and res["U"] == 0
    assert res["A_by_rule"] == {
        "memory_decay:forced": 1,
        "heredity_triggers:forced": 1,
        "pattern_extract:forced": 1,
    }


def test_main_wires_census_proactive():
    """接线普查：main() 真调用 census_proactive（写了≠接线了）。"""
    tree = ast.parse(_SCRIPT.read_text())
    main_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [
        n.func.id
        for n in ast.walk(main_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert "census_proactive" in calls


def test_proactive_db_env_first(tmp_path, monkeypatch):
    """路径单一源：PROACTIVE_DB env 优先（与 src/will/proactive.py _default_events_db 同款）。"""
    monkeypatch.setenv("PROACTIVE_DB", str(tmp_path / "x.db"))
    assert mod._proactive_db() == tmp_path / "x.db"
    monkeypatch.delenv("PROACTIVE_DB")
    assert mod._proactive_db() == REPO / "data" / "proactive.db"
