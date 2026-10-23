"""GR-① 增长域（无界增长/无老化面）普查——判定表契约测试（审计即切片本体，零行为改动）。

锁四件事：
1. 判定单一源：classify_memory/classify_db 形态矩阵逐值锁死（含判法盲区反例）；
2. 未归类如实抛：禁静默默认；
3. 确定性：同输入两跑逐键相等（--check 同判据）；
4. 前提标记锁：人工定性依赖的源码形态（观察件/判法收口面）真在源码里——
   前提变化（如建设件落地裁剪）须按契约随更新复核判定（F4b-⑯f 先例）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_TOOL = ROOT / "scripts" / "census_unbounded_growth.py"
spec = importlib.util.spec_from_file_location("gr1_tool", _TOOL)
assert spec is not None and spec.loader is not None
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

ARTIFACT = ROOT / "data" / "unbounded_growth_census_20261023.json"


# ---------------------------------------------------------------- 1. 判定单一源


def test_classify_memory_有界环形优先():
    assert tool.classify_memory("deque", "direct", ["append@1"], []) == "bounded_ring"
    # 有增长算子也不改判（环形有界非增长缺陷）
    assert tool.classify_memory("deque", "direct", ["append@1"], ["pop@2"]) == "bounded_ring"


def test_classify_memory_值有界键面独立():
    assert tool.classify_memory("defaultdict", "nested", ["append@1"], []) == "bounded_values"


def test_classify_memory_增长裁剪对账():
    assert tool.classify_memory("list", None, ["append@1"], []) == "grow_only"
    assert tool.classify_memory("list", None, ["append@1"], ["reslice@2"]) == "grow_with_trim"
    assert tool.classify_memory("list", None, [], ["clear@1"]) == "trim_only"
    assert tool.classify_memory("dict", None, [], []) == "no_growth"


def test_classify_memory_未归类如实抛():
    with pytest.raises(ValueError, match="未归类条目"):
        tool.classify_memory("tuple", None, [], [])


def test_classify_db_矩阵():
    assert tool.classify_db(["INSERT@1"], []) == "db_append_only"
    assert tool.classify_db(["INSERT@1"], ["DELETE@2"]) == "db_has_delete"
    assert tool.classify_db([], ["DELETE@1"]) == "db_delete_only"
    with pytest.raises(ValueError, match="未归类条目"):
        tool.classify_db([], [])


# ---------------------------------------------------------------- 2. 形态采集（合成源码树）


def _scan_source(tmp_path: Path, source: str) -> tuple[list[dict], list[dict]]:
    f = tmp_path / "synth.py"
    f.write_text(source, encoding="utf-8")
    return tool.scan_file(f)


def test_合成树_形态矩阵逐值(tmp_path):
    src = (
        "from collections import deque, defaultdict\n"
        "class A:\n"
        "    def __init__(self):\n"
        "        self.ring = deque(maxlen=10)\n"
        "        self.grow_list = []\n"
        "        self.window = []\n"
        "        self.sliced = []\n"
        "        self.keyed = defaultdict(lambda: deque(maxlen=5))\n"
        "        self.table = {}\n"
        "    def tick(self):\n"
        "        self.grow_list.append(1)\n"
        "        self.window.append(1)\n"
        "        self.window = [t for t in self.window if t > 0]\n"  # filter 滑动
        "        self.sliced.append(1)\n"
        "        self.sliced = self.sliced[-5:]\n"  # reslice 保尾
        "        self.keyed['k'].append(1)\n"
        "        self.table['k'] = 1\n"
        "        self.table.pop('k', None)\n"
        "        del self.table['k2']\n"
        "        self.grow_list += [2]\n"
        "module_ring = deque(maxlen=3)\n"
        "module_list = []\n"
        "module_list.append(1)\n"
    )
    mem, _ = _scan_source(tmp_path, src)
    by = {(e["class"], e["attr"]): e for e in mem}
    assert by[("A", "ring")]["verdict"] == "bounded_ring"
    assert by[("A", "grow_list")]["verdict"] == "grow_only"
    assert set(by[("A", "grow_list")]["growth_ops"]) == {"append@11", "iadd@20"}
    assert by[("A", "window")]["verdict"] == "grow_with_trim"
    assert "filter@13" in by[("A", "window")]["trim_ops"]
    assert by[("A", "sliced")]["verdict"] == "grow_with_trim"
    assert "reslice@15" in by[("A", "sliced")]["trim_ops"]
    assert by[("A", "keyed")]["verdict"] == "bounded_values"
    assert by[("A", "table")]["verdict"] == "grow_with_trim"
    assert by[(None, "module_ring")]["verdict"] == "bounded_ring"
    assert by[(None, "module_list")]["verdict"] == "grow_only"
    assert by[(None, "module_list")]["kind"] == "module_global"


def test_合成树_无条件推导式复制不算裁剪(tmp_path):
    src = (
        "class A:\n"
        "    def __init__(self):\n"
        "        self.items = []\n"
        "    def copy(self):\n"
        "        self.items = [x for x in self.items]\n"  # 无 if=复制非滤除（宁漏勿滥）
    )
    mem, _ = _scan_source(tmp_path, src)
    # 复制重赋值不计裁剪（宁漏勿滥）；该容器也无增长算子→no_growth 证据面
    assert mem[0]["verdict"] == "no_growth"
    assert mem[0]["trim_ops"] == []


def test_合成树_DB面表级聚合(tmp_path):
    src = (
        "Q1 = 'INSERT INTO t_events (a) VALUES (?)'\n"
        "Q2 = 'INSERT OR REPLACE INTO t_events (a) VALUES (?)'\n"
        "Q3 = 'DELETE FROM t_events WHERE id=?'\n"
        "Q4 = 'insert into t_grow values (1)'\n"
    )
    _, db = _scan_source(tmp_path, src)
    by = {e["table"]: e for e in db}
    assert by["t_events"]["verdict"] == "db_has_delete"
    assert by["t_grow"]["verdict"] == "db_append_only"
    # INSERT OR REPLACE 计 INSERT 但不是删口（REPLACE 不算 DELETE）
    assert len(by["t_events"]["insert_sites"]) == 2


def test_合成树_切片外attr不误归因(tmp_path):
    src = (
        "class A:\n"
        "    def __init__(self):\n"
        "        self.data = {}\n"
        "    def f(self):\n"
        "        other = {}\n"
        "        other['k'] = 1\n"  # 局部变量不收
        "        self.data2 = {}\n"  # 非容器赋值外的增长算子不误归 data
    )
    mem, _ = _scan_source(tmp_path, src)
    assert {(e["class"], e["attr"]) for e in mem} == {("A", "data"), ("A", "data2")}
    by = {(e["class"], e["attr"]): e for e in mem}
    assert by[("A", "data")]["growth_ops"] == []


# ---------------------------------------------------------------- 3. 确定性+工件回读


def test_确定性_两跑逐键相等():
    r1 = tool.scan(ROOT / "src")
    r2 = tool.scan(ROOT / "src")
    assert tool._stable(r1) == tool._stable(r2)


def test_工件回读对账_stats与逐条重算一致():
    art = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    mem, db = art["memory_entries"], art["db_entries"]
    assert art["stats"]["memory_findings"] == len(mem)
    assert art["stats"]["db_findings"] == len(db)
    by = {}
    for e in mem:
        by[e["verdict"]] = by.get(e["verdict"], 0) + 1
    assert art["stats"]["by_verdict"] == by
    db_by = {}
    for e in db:
        db_by[e["verdict"]] = db_by.get(e["verdict"], 0) + 1
    assert art["stats"]["db_by_verdict"] == db_by
    # 去重键零重复
    keys = [(e["module"], e["class"], e["attr"]) for e in mem]
    assert len(keys) == len(set(keys))
    # 任一条目必有 verdict（禁静默默认）
    assert all(e["verdict"] for e in mem + db)


# ---------------------------------------------------------------- 4. 前提标记锁


def _mem_entry(module: str, cls: str | None, attr: str) -> dict:
    art = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    for e in art["memory_entries"]:
        if (e["module"], e["class"], e["attr"]) == (module, cls, attr):
            return e
    raise AssertionError(f"工件缺条目 {module}/{cls}:{attr}")


def test_前提_观察件真无界形态在场():
    """RV-①c 观察件（dream_distiller/vital alert）判 grow_only=增长域开域依据；
    契约随更新（GR-①b-④，F4b-⑯f 先例）：_dream_history 已由 GR-①b-④ 落内存窗
    裁剪——工件快照判 grow_only（历史定性不改写），本锁改钉**现行源码**判
    grow_with_trim（delitem 裁剪真在）=收口不许静默回退无界；alert._history
    未裁剪照旧 grow_only（GR-①b-⑤ 待拍板件）。"""
    assert (
        _mem_entry("src/hippo/dream_distiller.py", "DreamDistiller", "_dream_history")["verdict"]
        == "grow_only"
    )
    assert _mem_entry("src/vital/alert.py", "AlertManager", "_history")["verdict"] == "grow_only"
    # 现行源码面：内存窗真裁（判法单一源 scan_file 实扫，非工件快照）
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    mem, _db = tool.scan_file(root / "src/hippo/dream_distiller.py")
    entry = next(e for e in mem if e["class"] == "DreamDistiller" and e["attr"] == "_dream_history")
    assert entry["verdict"] == "grow_with_trim"
    assert any(op.startswith("delitem@") for op in entry["trim_ops"])


def test_前提_判法收口面_filter滑动认得():
    """SlidingWindowCounter._timestamps=filter 滑动形态（首版误判 grow_only，
    同拍收口 filter 算子）；本锁防判法回退。"""
    e = _mem_entry("src/immune/rate_limiter.py", "SlidingWindowCounter", "_timestamps")
    assert e["verdict"] == "grow_with_trim"
    assert any(op.startswith("filter@") for op in e["trim_ops"])


def test_前提_重置不计裁剪口径锁死():
    """LongTermMemoryStore.last_context_memory_ids=per-call 重置界（口径#3
    「重置即裁剪不计」判法盲区反例，人工定性非缺陷）；本锁死该口径。"""
    e = _mem_entry(
        "src/hippo/long_term_memory.py", "LongTermMemoryStore", "last_context_memory_ids"
    )
    assert e["verdict"] == "grow_only"  # 重置不计裁剪→形态学如实 grow_only
    assert e["trim_ops"] == []


def test_前提_事件流型候选在场():
    """GR-①b 候拍清单依赖的事件流型真无界面真在工件里（工件=2026-10-23 快照）。

    契约随更新（GR-① 诚实边界⑥，F4b-⑯f 先例）：TokenMeter._records 已由
    GR-①b-① 落内存窗裁剪、push_delivery_log 已由 GR-①b-② 落内存窗裁剪——
    工件快照判 grow_only（历史定性不改写），本锁改钉**现行源码**判 grow_with_trim
    （delitem 裁剪真在）=收口不许静默回退无界。"""
    assert _mem_entry("src/a2a/task_manager.py", "TaskManager", "tasks")["verdict"] == "grow_only"
    # 现行源码面：内存窗真裁（判法单一源 scan_file 实扫，非工件快照）
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    mem, _db = tool.scan_file(root / "src/gland/token_meter.py")
    entry = next(e for e in mem if e["class"] == "TokenMeter" and e["attr"] == "_records")
    assert entry["verdict"] == "grow_with_trim"
    assert any(op.startswith("delitem@") for op in entry["trim_ops"])
    mem2, _db2 = tool.scan_file(root / "src/a2a/task_manager.py")
    entry2 = next(
        e for e in mem2 if e["class"] == "TaskManager" and e["attr"] == "push_delivery_log"
    )
    assert entry2["verdict"] == "grow_with_trim"
    assert any(op.startswith("delitem@") for op in entry2["trim_ops"])


def test_前提_DB无删口面在场():
    art = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    by = {e["table"]: e for e in art["db_entries"]}
    assert by["gland_usage"]["verdict"] == "db_append_only"
    assert by["trajectory_v2_events"]["verdict"] == "db_append_only"
    assert by["evolution_log"]["verdict"] == "db_append_only"
