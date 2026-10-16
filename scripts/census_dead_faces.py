#!/usr/bin/env python3
"""全仓 wired-but-not-functional 死面普查（H0）——只读静态调用图枚举审计工具。

目的：系统性找出「定义了但没有任何真实消费点」的函数/方法（D1 terminal_ws、
F4b-① gland、G1-① cross_domain_idea、G1-⑤ register_agent、F4b-㉒ adjust_decision
等历史死面均为逐案 grep 偶然发现），产出候选清单供后续建设件按最小件排序拍板。
零行为改动：本工具只读源码、只写 JSON 工件，不改任何 src。

判法（单一源=classify_refs 纯函数，禁在别处手抄）：
  dead         —— 除自身定义外，src 全仓（不含 tests/static）零名字引用
  export_only  —— 仅在包 __init__.py 重导出（模块接缝/死资产出口，无真实消费）
  wired        —— 有 src 内真实引用（调用点/注册表/依赖注入/装饰器消费等）

诚实边界（普查口径如实）：
1. 名字按全局裸名匹配（Name.id / Attribute.attr）——同名碰撞只做「保守合并」：
   别处同名引用会被误计为引用（把死面判成 wired=漏报方向），绝不把活面判成
   dead（dead 结论仍须人工 grep 复核，报告即含复核指引）。
2. 路由装饰器（router./app./websocket/mount）的 handler 不进候选（有 HTTP 出口=
   既有消费面，「API-only 不进决策面」是另一类问题，归建设件定性不在普查口径）。
3. 装饰器在场（router./app./websocket/mount、@server.tool() MCP 注册、pydantic
   校验器、property 等任何装饰器）不进候选——装饰器本身即消费函数对象的接线
   面（注册/包裹），静态无法判注册对象后续是否被吃，宁漏勿滥。
4. tests/ 引用一律不计（测试消费=非生产消费，正是本普查要区分的）。
5. 反射消费（getattr/字符串注册）不可静态判——候选经人工复核后才进结论。

用法：
  python3 scripts/census_dead_faces.py [--root src] [--json data/dead_face_census.json]
"""
from __future__ import annotations

import argparse
import ast
import json
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SKIP_DIRS = {"__pycache__", "static", "tests", "node_modules"}

TIER_DEAD = "dead"
TIER_EXPORT_ONLY = "export_only"
TIER_WIRED = "wired"


def is_public(name: str) -> bool:
    """公开面（单下划线/双下划线开头的私有面不进普查候选）。"""
    return not name.startswith("_")


def is_decorated(decorators: list[str]) -> bool:
    """装饰器在场=注册/包裹接线面（路由 handler、@server.tool()、pydantic 校验器、
    property 等），不进死面候选（宁漏勿滥）。"""
    return bool(decorators)


def classify_refs(non_export_refs: int, export_refs: int) -> str:
    """引用分级判法（纯函数单一源）。"""
    if non_export_refs <= 0 and export_refs <= 0:
        return TIER_DEAD
    if non_export_refs <= 0:
        return TIER_EXPORT_ONLY
    return TIER_WIRED


def _skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _decorator_texts(node) -> list[str]:
    out = []
    for d in getattr(node, "decorator_list", []):
        try:
            out.append(ast.unparse(d))
        except Exception:
            out.append("")
    return out


def collect(root: Path) -> tuple[list[dict], dict]:
    """收集 defs（定义面）与 refs（引用面）。refs: name -> [(file, line)]。"""
    defs: list[dict] = []
    refs: dict[str, list[tuple[str, int]]] = {}
    files = sorted(p for p in root.rglob("*.py") if not _skip(p))

    def walk_defs(node, qual: str, path: Path, in_function: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk_defs(child, f"{qual}.{child.name}" if qual else child.name, path, False)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = _decorator_texts(child)
                q = f"{qual}.{child.name}" if qual else child.name
                defs.append({
                    "qualname": q,
                    "name": child.name,
                    "file": str(path.relative_to(root.parent)) if root.name == "src" else str(path),
                    "line": child.lineno,
                    "kind": "method" if qual and "." in q else "function",
                    "decorators": decorators,
                    "nested": in_function,
                })
                walk_defs(child, q, path, True)
            else:
                walk_defs(child, qual, path, in_function)

    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        walk_defs(tree, "", path, False)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                refs.setdefault(node.id, []).append((str(path), node.lineno))
            elif isinstance(node, ast.Attribute):
                refs.setdefault(node.attr, []).append((str(path), node.lineno))
            elif isinstance(node, ast.ImportFrom):
                # from X import foo as bar —— foo 是对源符号的引用（alias 不是 Name，
                # 漏计会把被 import 的活面误判成 dead）
                for alias in node.names:
                    if alias.name != "*":
                        refs.setdefault(alias.name, []).append((str(path), node.lineno))
    return defs, refs


def scan(root: Path) -> dict:
    """全仓普查：返回逐候选分级 + 汇总。root 即被扫源码根（通常 <repo>/src）。"""
    defs, refs = collect(root)
    candidates = []
    counts = {TIER_DEAD: 0, TIER_EXPORT_ONLY: 0, TIER_WIRED: 0}
    skipped = {"private": 0, "nested": 0, "decorated": 0, "dunder": 0}

    for d in defs:
        if d["name"] in ("__init__", "__post_init__") or (d["name"].startswith("__") and d["name"].endswith("__")):
            skipped["dunder"] += 1
            continue
        if d["nested"]:
            skipped["nested"] += 1
            continue
        if not is_public(d["name"]):
            skipped["private"] += 1
            continue
        if is_decorated(d["decorators"]):
            skipped["decorated"] += 1
            continue

        export_refs, non_export_refs = [], []
        own_file = str(Path(d["file"]))
        for ref_file, ref_line in refs.get(d["name"], []):
            if ref_file.endswith(own_file) and ref_line == d["line"]:
                continue  # 自身定义
            if Path(ref_file).name == "__init__.py":
                export_refs.append((ref_file, ref_line))
            else:
                non_export_refs.append((ref_file, ref_line))
        tier = classify_refs(len(non_export_refs), len(export_refs))
        counts[tier] += 1
        if tier != TIER_WIRED:
            candidates.append({
                "tier": tier,
                "qualname": d["qualname"],
                "name": d["name"],
                "file": d["file"],
                "line": d["line"],
                "kind": d["kind"],
                "decorators": d["decorators"],
                "export_refs": [f"{f}:{ln}" for f, ln in export_refs[:5]],
            })

    candidates.sort(key=lambda c: (c["tier"], c["file"], c["line"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "totals": {
            "defs": len(defs),
            "eligible": sum(counts.values()),
            **counts,
            "skipped": skipped,
        },
        "candidates": candidates,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(REPO / "src"))
    ap.add_argument("--json", default=str(REPO / "data" / "dead_face_census.json"))
    args = ap.parse_args()
    report = scan(Path(args.root))
    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    t = report["totals"]
    print(f"defs={t['defs']} eligible={t['eligible']} "
          f"dead={t[TIER_DEAD]} export_only={t[TIER_EXPORT_ONLY]} wired={t[TIER_WIRED]} "
          f"skipped={t['skipped']}")
    for c in report["candidates"]:
        print(f"  [{c['tier']}] {c['file']}:{c['line']} {c['qualname']}")
    print(f"artifact -> {out}")


if __name__ == "__main__":
    main()
