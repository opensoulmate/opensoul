#!/usr/bin/env python3
"""全仓「重启即丢状态面」普查（RV-①）——只读静态枚举审计工具。

目的：MEM-④⑤⑥ 把记忆面持久化后，系统性盘点全仓还有哪些状态只活在进程内存
（service 重启即丢），逐条 file:line 落事实，产出候选清单供建设件按最小件排序拍板。
零行为改动：本工具只读源码、只写 JSON 工件，不改任何 src。

普查口径（两类发现，单一源=classify_finding 纯函数，禁在别处手抄）：
  instance_registry —— 类方法体内 self.<attr> = {}/[]/set()/defaultdict/deque/OrderedDict
                        （进程内注册表/缓冲/缓存的典型形态）
  module_global     —— 模块级小写名可变全局（{}/[]/set()/defaultdict/deque）

持久化旁证（证据列事实不判罪，判定归报告人工定性）：
  write_signals —— 本类/本模块内出现的落盘形态调用（sqlite execute/INSERT/UPDATE、
                    json.dump、write_text、_persist/save/store 调用点等）逐条 file:line
  load_signals  —— 本类/本模块内出现的回载形态调用（_load/_ensure_loaded/load_cache/
                    SELECT/restore 等）逐条 file:line
  auto_verdict  —— 粗判（persisted_evidence/write_only/volatile/no_signal），仅供
                    报告分诊排序；「重启真丢不丢」以报告人工定性为准，禁直接引用。

诚实边界（普查口径如实）：
1. 静态枚举不追跨模块委托——状态真源若在独立 Store 类（如 NestStore/ExperienceMemory），
   本体类只看到 self._store.save(...) 形态的调用点（归 write_signals），委托链深度不追。
2. 派生/可重建状态（统计计数、缓存、LRU）与「真事实状态」（注册表、任务、消息）在
   形态上不可静态区分——auto_verdict 只看有无落盘信号，不判状态语义，判定归报告。
3. 装饰器/描述符/property 内的赋值不进候选（宁漏勿滥）；类体非 __init__ 方法内的
   注册表赋值也收（很多注册表在 register_/add_ 方法里建），但 per-call 临时局部容器
   不算——只收 self. 属性赋值。
4. tests/static/node_modules 不扫；同名属性跨类独立计（按 (class, attr) 键）。
5. 重启行为本身不实测（live 重启探针留建设件逐件拍），本拍=静态普查+数据面旁证。

用法：
  python3 scripts/census_restart_volatile_state.py [--root src] \
      [--json data/restart_volatile_census_20261021.json]
"""
from __future__ import annotations

import argparse
import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent

SKIP_DIRS = {"__pycache__", "static", "tests", "node_modules"}

KIND_INSTANCE = "instance_registry"
KIND_MODULE = "module_global"

# 落盘形态信号（名字/属性名精确集，宁窄勿滥；命中即记证据行不判罪）
WRITE_CALL_NAMES = {
    "execute", "executemany", "execute_many",
    "json_dump", "dump",
    "write_text", "write_bytes", "writelines",
    "_persist", "persist", "save", "save_cache", "store", "record", "_record",
    "commit", "backup",
}
WRITE_NAME_TOKENS = {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"}
# 回载形态信号
LOAD_CALL_NAMES = {
    "_load", "load", "load_cache", "restore", "_ensure_loaded", "_reload",
    "fetchall", "fetchone", "read_text", "SELECT",
}
LOAD_NAME_TOKENS = {"SELECT"}

MUTABLE_FACTORIES = {"dict", "list", "set", "frozenset", "defaultdict", "deque", "OrderedDict"}


def container_kind(node: ast.AST) -> Optional[str]:
    """赋值右值是否进程内容器形态；返回形态名（dict/list/set/...），非容器 None。"""
    if isinstance(node, ast.Dict):
        return "dict"
    if isinstance(node, ast.List):
        return "list"
    if isinstance(node, ast.Set):
        return "set"
    if isinstance(node, ast.Call):
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in MUTABLE_FACTORIES:
            return name
    return None


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def collect_signals(body: "list[Any]", include_classes: bool = True) -> tuple[list[str], list[str]]:
    """扫一段 AST 体，收集（落盘形态, 回载形态）证据行（name@line）。

    include_classes=False 时不下探类体（类方法跑在实例上，与模块级全局的写读
    证据不同源——宁缺勿滥，模块级全局只采模块级与模块函数内的调用）。
    """
    writes: list[str] = []
    loads: list[str] = []
    for node in _walk_all(body, include_classes=include_classes):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        line = getattr(node, "lineno", 0)
        if name in WRITE_CALL_NAMES:
            writes.append("{}@{}".format(name, line))
        elif name in LOAD_CALL_NAMES:
            loads.append("{}@{}".format(name, line))
        else:
            for tok in WRITE_NAME_TOKENS:
                if tok in name:
                    writes.append("{}@{}".format(name, line))
                    break
    return sorted(set(writes)), sorted(set(loads))


def _assign_pairs(node: ast.AST) -> list[tuple[ast.AST, ast.AST]]:
    """Assign/AnnAssign 双形统一取 (target, value) 对（宁漏勿滥，其余语句不收）。"""
    if isinstance(node, ast.Assign):
        return [(tgt, node.value) for tgt in node.targets]
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [(node.target, node.value)]
    return []


def _walk_all(body: "list[Any]", include_classes: bool = True) -> list[ast.AST]:
    out: list[ast.AST] = []
    stack = list(body)
    while stack:
        node = stack.pop()
        out.append(node)
        children = list(ast.iter_child_nodes(node))
        if not include_classes and isinstance(node, ast.ClassDef):
            children = []  # 类体不下探（模块级信号与类内信号分开采）
        stack.extend(children)
    return out


def auto_verdict(writes: list[str], loads: list[str]) -> str:
    """粗判单一源（只看信号在场，不判状态语义；报告人工定性为准）。"""
    if writes and loads:
        return "persisted_evidence"
    if writes:
        return "write_only"
    if loads:
        return "load_only"
    return "volatile"


def classify_finding(target: str) -> str:
    """发现分类单一源（纯函数）。target: 'self_attr' | 'module_name'。"""
    return KIND_INSTANCE if target == "self_attr" else KIND_MODULE


def scan_file(path: Path) -> list[dict]:
    """扫单文件，产出发现行（事实列，零判定）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    try:
        rel = str(path.relative_to(REPO))
    except ValueError:
        rel = str(path)  # 合成源码树（测试 tmp）不属仓内，原样记路径
    findings: list[dict] = []

    # 模块级可变全局（小写名，UPPER_CASE 视为常量不收；Assign+AnnAssign 双形）
    module_body = list(tree.body)
    module_writes, module_loads = collect_signals(module_body, include_classes=False)
    for node in module_body:
        for tgt, value in _assign_pairs(node):
            if not (isinstance(tgt, ast.Name) and tgt.id[:1].islower()):
                continue
            kind = container_kind(value)
            if kind is None:
                continue
            findings.append({
                "kind": classify_finding("module_name"),
                "module": rel,
                "class": None,
                "attr": tgt.id,
                "container": kind,
                "line": node.lineno,
                "write_signals": module_writes,
                "load_signals": module_loads,
            })

    # 类内 self.<attr> 容器注册表（全方法体扫，非仅 __init__）
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        cls_writes, cls_loads = collect_signals(list(cls.body))
        for node in _walk_all(list(cls.body)):
            for tgt, value in _assign_pairs(node):
                if not (isinstance(tgt, ast.Attribute)
                        and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "self"):
                    continue
                kind = container_kind(value)
                if kind is None:
                    continue
                findings.append({
                    "kind": classify_finding("self_attr"),
                    "module": rel,
                    "class": cls.name,
                    "attr": tgt.attr,
                    "container": kind,
                    "line": getattr(node, "lineno", 0),
                    "write_signals": cls_writes,
                    "load_signals": cls_loads,
                })

    for f in findings:
        f["auto_verdict"] = auto_verdict(f["write_signals"], f["load_signals"])
    return findings


def scan(root: Path) -> dict:
    files = sorted(
        p for p in root.rglob("*.py")
        if not (set(p.relative_to(root).parts) & SKIP_DIRS)
    )
    findings: list[dict] = []
    for path in files:
        findings.extend(scan_file(path))
    # 去重键 (module, class, attr)——同类属性多处赋值取首行，形态冲突如实全记
    dedup: dict[tuple, dict] = {}
    for f in findings:
        key = (f["module"], f["class"], f["attr"])
        if key not in dedup:
            dedup[key] = f
    entries = sorted(dedup.values(), key=lambda f: (f["module"], f["class"] or "", f["attr"]))
    by_verdict: dict[str, int] = {}
    for e in entries:
        by_verdict[e["auto_verdict"]] = by_verdict.get(e["auto_verdict"], 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "files_scanned": len(files),
        "stats": {
            "findings": len(entries),
            "instance_registry": sum(1 for e in entries if e["kind"] == KIND_INSTANCE),
            "module_global": sum(1 for e in entries if e["kind"] == KIND_MODULE),
            "by_auto_verdict": by_verdict,
        },
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="src")
    parser.add_argument("--json", default="data/restart_volatile_census_20261021.json")
    args = parser.parse_args()
    root = (REPO / args.root).resolve()
    result = scan(root)
    out = REPO / args.json
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"json": str(out), "stats": result["stats"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
