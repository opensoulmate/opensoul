"""H0 死面普查工具——分类矩阵/引用计数口径/边界纪律（全 tmp 合成源码，绝不碰 src）。

覆盖：dead 检出/真引用判 wired/ImportFrom 别名引用计数（普查真 bug 回归锁死：
from X import foo as bar 是对 foo 的引用，漏计=把活面误判 dead）/装饰器在场排除
（router handler、@server.tool() 型注册面）/私有·dunder·嵌套跳过/export_only 分级/
同名碰撞保守方向（宁漏报勿误杀）/方法面检出/确定性/纯函数判法/工件逐键。
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "census_dead_faces.py"
_spec = importlib.util.spec_from_file_location("census_dead_faces_h0", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _write(root: Path, name: str, src: str) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src, encoding="utf-8")


@pytest.fixture()
def synth(tmp_path: Path) -> Path:
    """合成源码树：死面/活面/注册面/出口面/碰撞面全覆盖。"""
    root = tmp_path / "pkg"
    _write(
        root,
        "mod_a.py",
        """\
class Eng:
    def dead_meth(self):
        return 1

    def used_meth(self):
        return 2


def dead_fn():
    return 3


def used_fn():
    return 4


def decorated_fn():
    return 5


def _private_fn():
    return 6


class Holder:
    def __init__(self):
        self.x = 1

    def __repr__(self):
        return "Holder"


def outer():
    def nested_fn():
        return 7
    return nested_fn
""",
    )
    _write(
        root,
        "mod_b.py",
        """\
from pkg.mod_a import used_fn as aliased
from pkg.mod_a import decorated_fn


def call_them():
    e = None
    return aliased() + decorated_fn() + (e.used_meth() if e else 0)
""",
    )
    _write(
        root,
        "mod_c.py",
        """\
def colliding_name():
    return 8


def user_of_collision():
    return colliding_name()
""",
    )
    _write(
        root,
        "mod_d.py",
        """\
def exported_only():
    return 9


def dead_but_collides():
    return 10
""",
    )
    _write(
        root,
        "mod_e.py",
        """\
x = colliding_name
""",
    )
    _write(
        root,
        "routed.py",
        """\
router = object()


@router.get("/x")
def route_handler():
    return 11


@router.tool()
def tool_registered():
    return 12
""",
    )
    _write(root, "__init__.py", "from pkg.mod_d import exported_only\n")
    return root


def _by_name(report: dict) -> dict:
    return {c["qualname"]: c for c in report["candidates"]}


def test_dead_fn_detected_and_used_fn_wired(synth: Path):
    report = mod.scan(synth)
    cands = _by_name(report)
    assert "dead_fn" in cands and cands["dead_fn"]["tier"] == mod.TIER_DEAD
    assert "used_fn" not in cands  # 真引用=wired 不进候选


def test_import_alias_reference_counts_as_wiring(synth: Path):
    """回归锁死普查真 bug：from X import foo as bar 漏计会把活面误判 dead。"""
    cands = _by_name(mod.scan(synth))
    assert "used_fn" not in cands  # 经 import alias 被消费
    assert "decorated_fn" not in cands  # 同为 import 引用（走 ImportFrom 计数）


def test_decorated_excluded_route_and_tool_registration(synth: Path):
    report = mod.scan(synth)
    names = {c["qualname"] for c in report["candidates"]}
    assert "route_handler" not in names
    assert "tool_registered" not in names
    assert report["totals"]["skipped"]["decorated"] >= 2


def test_private_dunder_nested_skipped(synth: Path):
    report = mod.scan(synth)
    names = {c["qualname"] for c in report["candidates"]}
    assert "_private_fn" not in names
    assert "Holder.__init__" not in names
    assert "Holder.__repr__" not in names
    assert "outer.nested_fn" not in names
    assert report["totals"]["skipped"]["private"] >= 1
    assert report["totals"]["skipped"]["dunder"] >= 2
    assert report["totals"]["skipped"]["nested"] >= 1


def test_export_only_tier(synth: Path):
    cands = _by_name(mod.scan(synth))
    assert cands["exported_only"]["tier"] == mod.TIER_EXPORT_ONLY
    assert cands["exported_only"]["export_refs"]


def test_method_dead_face_detected(synth: Path):
    cands = _by_name(mod.scan(synth))
    assert "Eng.dead_meth" in cands and cands["Eng.dead_meth"]["tier"] == mod.TIER_DEAD
    assert "Eng.used_meth" not in cands
    assert cands["Eng.dead_meth"]["kind"] == "method"


def test_name_collision_conservative_direction(synth: Path):
    """同名碰撞只许漏报（dead→wired）方向，绝不把活面判 dead：
    dead_but_collides 与活名 colliding_name 无关但 mod_e 引用 colliding_name
    不影响它；而 colliding_name 本体=wired。"""
    cands = _by_name(mod.scan(synth))
    assert cands["dead_but_collides"]["tier"] == mod.TIER_DEAD  # 无碰撞仍检出
    assert "colliding_name" not in cands  # 真引用


def test_scan_deterministic(synth: Path):
    a, b = mod.scan(synth), mod.scan(synth)
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b


def test_classify_refs_pure():
    assert mod.classify_refs(0, 0) == mod.TIER_DEAD
    assert mod.classify_refs(0, 3) == mod.TIER_EXPORT_ONLY
    assert mod.classify_refs(1, 0) == mod.TIER_WIRED
    assert mod.classify_refs(2, 5) == mod.TIER_WIRED


def test_artifact_shape_and_totals(synth: Path, tmp_path: Path):
    report = mod.scan(synth)
    out = tmp_path / "census.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    back = json.loads(out.read_text(encoding="utf-8"))
    for key in ("generated_at", "root", "totals", "candidates"):
        assert key in back
    t = back["totals"]
    for key in ("defs", "eligible", "dead", "export_only", "wired", "skipped"):
        assert key in t
    assert t["eligible"] == t["dead"] + t["export_only"] + t["wired"]
    for c in back["candidates"]:
        assert c["tier"] in (mod.TIER_DEAD, mod.TIER_EXPORT_ONLY)
        for key in ("qualname", "name", "file", "line", "kind", "decorators"):
            assert key in c


def test_importfrom_alias_counting_locked_in_source():
    """静态契约：ImportFrom alias 引用计数必须在工具源码在场（防回归删掉）。"""
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    has_importfrom_dispatch = any(
        isinstance(n, ast.Attribute) and n.attr == "ImportFrom" for n in ast.walk(tree)
    )
    has_alias_iteration = any(
        isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "alias"
        for n in ast.walk(tree)
    )
    assert has_importfrom_dispatch and has_alias_iteration, (
        "ImportFrom alias 引用计数被移除=普查 bug 回归"
    )
