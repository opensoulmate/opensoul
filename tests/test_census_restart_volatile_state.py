"""RV-① 重启即丢状态面普查工具——分类矩阵/信号旁证/去重口径/确定性（全 tmp 合成源码，绝不碰 src）。

覆盖：容器形态矩阵/Assign+AnnAssign 双形/模块级小写全局收·UPPER 常量不收/self 注册表
检出（plain+annotated 双形）/self 非容器不收/落盘·回载信号旁证与 auto_verdict 矩阵/
去重键 (module,class,attr)/确定性/纯函数判法单一源。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "census_restart_volatile_state.py"
_spec = importlib.util.spec_from_file_location("census_restart_volatile_rv1", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _write(root: Path, name: str, src: str) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src, encoding="utf-8")


SYNTH = """\
registry = {}
CONST_TABLE = {}
counter = 0


class Eng:
    def __init__(self):
        self.tasks = {}
        self.items: list = []
        self.name = "x"
        self.cache = defaultdict(dict)

    def add(self):
        self.later = set()

    def persist(self):
        self.conn.execute("INSERT INTO t VALUES (1)")

    def boot(self):
        self._ensure_loaded()


class Loader:
    def __init__(self):
        self.rows = {}

    def restore(self):
        self.rows = self._load()
"""


@pytest.fixture()
def synth(tmp_path: Path) -> Path:
    root = tmp_path / "pkg"
    _write(root, "mod_a.py", SYNTH)
    return root


def test_container_kind_matrix() -> None:
    import ast

    src = "a = {}\nb = []\nc = set()\nd = defaultdict(dict)\ne = deque()\nf = OrderedDict()\ng = 1\nh = 'x'\ni = dict()"
    tree = ast.parse(src)
    nodes = [n.value for n in tree.body if isinstance(n, ast.Assign)]
    kinds = [mod.container_kind(n) for n in nodes]
    assert kinds == [
        "dict",
        "list",
        "set",
        "defaultdict",
        "deque",
        "OrderedDict",
        None,
        None,
        "dict",
    ]


def test_assign_pairs_dual_form() -> None:
    import ast

    tree = ast.parse("self.a = {}\nself.b: dict = []\nself.c += 1\nreturn 1")
    pairs = [mod._assign_pairs(n) for n in tree.body]
    assert len(pairs[0]) == 1  # Assign
    assert len(pairs[1]) == 1  # AnnAssign
    assert pairs[2] == []  # AugAssign 不收
    assert pairs[3] == []  # Return 不收


def test_classify_finding_single_source() -> None:
    assert mod.classify_finding("self_attr") == "instance_registry"
    assert mod.classify_finding("module_name") == "module_global"


def test_auto_verdict_matrix() -> None:
    assert mod.auto_verdict([], []) == "volatile"
    assert mod.auto_verdict(["save@1"], []) == "write_only"
    assert mod.auto_verdict([], ["SELECT@1"]) == "load_only"
    assert mod.auto_verdict(["save@1"], ["SELECT@1"]) == "persisted_evidence"


def test_scan_synthetic(synth: Path) -> None:
    result = mod.scan(synth)
    entries = {(e["class"], e["attr"]): e for e in result["entries"]}
    # self 注册表检出（plain+annotated+方法内 set 双形）
    assert ("Eng", "tasks") in entries
    assert ("Eng", "items") in entries
    assert ("Eng", "later") in entries
    assert ("Eng", "cache") in entries
    # self 非容器不收
    assert ("Eng", "name") not in entries
    # 模块级小写全局收、UPPER 常量不收
    keys = {(e["class"], e["attr"]) for e in result["entries"]}
    assert (None, "registry") in keys
    assert (None, "CONST_TABLE") not in keys
    assert (None, "counter") not in keys


def test_signal_verdicts(synth: Path) -> None:
    result = mod.scan(synth)
    entries = {(e["class"], e["attr"]): e for e in result["entries"]}
    assert entries[("Eng", "tasks")]["auto_verdict"] == "persisted_evidence"  # execute@ 落盘信号
    assert entries[("Eng", "tasks")]["write_signals"]
    assert entries[("Loader", "rows")]["auto_verdict"] == "load_only"  # _load 回载信号
    assert entries[("Loader", "rows")]["load_signals"]


def test_dedup_key_and_determinism(synth: Path) -> None:
    a = mod.scan(synth)
    b = mod.scan(synth)
    assert a["entries"] == b["entries"]  # 确定性（排序+去重稳定）
    keys = [(e["module"], e["class"], e["attr"]) for e in a["entries"]]
    assert len(keys) == len(set(keys))  # 去重键 (module,class,attr)


def test_stats_shape(synth: Path) -> None:
    result = mod.scan(synth)
    stats = result["stats"]
    assert stats["findings"] == len(result["entries"])
    assert stats["instance_registry"] + stats["module_global"] == stats["findings"]
    assert sum(stats["by_auto_verdict"].values()) == stats["findings"]
    json.dumps(result)  # 工件可序列化
