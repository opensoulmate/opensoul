#!/usr/bin/env python3
"""全仓「增长域」（无界增长/无老化面）普查（GR-①）——只读静态枚举审计工具。

背景：RV-①c 观察件「dream_distiller._dream_history / vital / alert._history
append 无裁剪疑似无界（归增长域=RV-① 对偶新域另查）」；RV-①b 多件诚实边界
「表只增不减无老化面（retention 另拍）」同族。RV-①b「可续无需拍板」车道清空后
按 MEM-⑥「G0 先例」开新域定性。零行为改动：本工具只读源码、只写 JSON 工件。

普查口径（两轴，判定单一源=classify_memory/classify_db 纯函数，禁在别处手抄）：
  轴一 内存增长面（memory）—— 类方法体内 self.<attr>/模块级小写全局 的容器
       （dict/list/set/defaultdict/deque/OrderedDict，RV-① 同款形态学），按
       「增长算子 vs 裁剪算子」对账判增长形态：
         增长算子 GROW —— .append/.extend/.insert/.add/.update/.setdefault/
                          .union_update（attr 直调或 attr[下标] 后调）、
                          self.<attr>[k]=v（setitem）、self.<attr> += …（iadd）
         裁剪算子 TRIM —— .pop/.popleft/.remove/.clear/.popitem/.discard、
                          del self.<attr>[…]（delitem）、
                          self.<attr> = self.<attr>[切片]（reslice 保尾裁剪）、
                          self.<attr> = [… for … in self.<attr> if …]（filter 滤除滑动）
  轴二 DB 追加面（db）—— 全仓 SQL 字符串里的 INSERT INTO <表> 与
       DELETE FROM <表> 对账（表级聚合跨模块）。by-id DELETE ≠ 老化，
       判定只答「有没有删口」，retention 语义归报告人工定性。

verdict（classify_memory 单一源）：
  bounded_ring    —— deque(maxlen=…) 直配（环形有界，非增长缺陷）
  bounded_values  —— dict/defaultdict 值为 maxlen 有界容器（值有界；键仍可无界，
                      报告人工定性，禁当「已裁剪」洗白）
  grow_only       —— 有增长算子、零裁剪算子（疑似无界增长=观察件形态）
  grow_with_trim  —— 增长+裁剪同在（有裁剪面，窗口语义归报告）
  trim_only       —— 只见裁剪（证据面）
  no_growth       —— 容器在场但未见增长/裁剪算子（证据面）
verdict（classify_db 单一源）：
  db_append_only  —— 有 INSERT 无 DELETE（只增不减无删口）
  db_has_delete   —— INSERT+DELETE 同在（删口在场，是否 retention 归报告）
  db_delete_only  —— 只见 DELETE（证据面）
未归类/未知形态如实抛 ValueError，禁静默默认。

诚实边界（普查口径如实）：
1. 静态枚举不追跨模块委托——裁剪若发生在别的类/函数（委托链），本类只见增长
   算子=如实判 grow_only；报告定性时可推翻（形态学判法宁可多报不漏报）。
2. 局部变量容器不收（只收 self. 属性/模块级全局）；同名属性跨类独立计。
3. 「重置即裁剪」（self.<attr> = [] 赋空）不计裁剪算子——__init__ 初始赋值同形，
   计入会把全部容器误判 grow_with_trim（宁漏勿滥）；真重置裁剪在报告人工定性。
4. reslice 只认「右值=同名属性下标/切片取值」的赋值（self._h = self._h[-N:]），
   复杂表达式裁剪不认。
5. SQL 判定只认字符串常量里的 INSERT INTO/DELETE FROM 字面（动态拼 SQL 不认）；
   INSERT OR REPLACE/IGNORE 也计 INSERT（REPLACE 不是删口）。
6. 本拍=静态普查+人工定性（审计即切片本体），建设件（retention/裁剪策略）按
   候拍清单逐件另拍；增长语义（审计不可抹除 vs 可裁窗口）涉产品口味须拍板的
   在报告显式标出。

用法：
  python3 scripts/census_unbounded_growth.py [--root src] \
      [--json data/unbounded_growth_census_20261023.json] [--check]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent

SKIP_DIRS = {"__pycache__", "static", "tests", "node_modules"}

KIND_INSTANCE = "instance_registry"
KIND_MODULE = "module_global"

GROW_METHODS = {
    "append", "extend", "insert", "add", "update", "setdefault",
    "union_update", "set_item",
}
TRIM_METHODS = {"pop", "popleft", "remove", "clear", "popitem", "discard"}

MUTABLE_FACTORIES = {"dict", "list", "set", "frozenset", "defaultdict", "deque", "OrderedDict"}

SQL_INSERT_RE = re.compile(r"INSERT(?:\s+OR\s+(?:REPLACE|IGNORE))?\s+INTO\s+([A-Za-z_][\w]*)", re.IGNORECASE)
SQL_DELETE_RE = re.compile(r"DELETE\s+FROM\s+([A-Za-z_][\w]*)", re.IGNORECASE)


# ---------------------------------------------------------------- verdict 单一源

def classify_memory(container: str, maxlen_site: Optional[str],
                    growth_ops: list[str], trim_ops: list[str]) -> str:
    """内存增长形态判定单一源（纯函数）。maxlen_site: 'direct' | 'nested' | None。"""
    if maxlen_site == "direct":
        return "bounded_ring"
    if maxlen_site == "nested":
        return "bounded_values"
    if growth_ops and trim_ops:
        return "grow_with_trim"
    if growth_ops:
        return "grow_only"
    if trim_ops:
        return "trim_only"
    if container in MUTABLE_FACTORIES:
        return "no_growth"
    raise ValueError("未归类条目：container={}".format(container))


def classify_db(insert_sites: list[str], delete_sites: list[str]) -> str:
    """DB 增长形态判定单一源（纯函数）。"""
    if insert_sites and delete_sites:
        return "db_has_delete"
    if insert_sites:
        return "db_append_only"
    if delete_sites:
        return "db_delete_only"
    raise ValueError("未归类条目：无 INSERT/DELETE 信号")


# ---------------------------------------------------------------- AST 形态采集

def container_kind(node: ast.AST) -> Optional[str]:
    """赋值右值是否进程内容器形态；返回形态名，非容器 None（RV-① 同款）。"""
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


def _deque_maxlen_in(value: ast.AST) -> bool:
    """赋值右值里是否出现 deque(…, maxlen=…/有界实参) 形态（含嵌套 lambda/factory）。"""
    for node in ast.walk(value):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
        if name != "deque":
            continue
        if any(kw.arg == "maxlen" for kw in node.keywords):
            return True
        if len(node.args) >= 2:  # deque(iterable, maxlen) 位置形
            return True
    return False


def _maxlen_site(assign_value: ast.AST, container: str) -> Optional[str]:
    if not _deque_maxlen_in(assign_value):
        return None
    return "direct" if container == "deque" else "nested"


def _attr_name(node: ast.AST) -> Optional[tuple[str, str]]:
    """取 (target_kind, attr)：self.<attr> 或 模块级 <name>；否则 None。"""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
        return ("self_attr", node.attr)
    if isinstance(node, ast.Name):
        return ("module_name", node.id)
    return None


def _base_attr(node: ast.AST) -> Optional[tuple[str, str]]:
    """算子目标基座归因：self.<attr> / self.<attr>[…] / 模块名 / 模块名[…] 都归到该 attr。"""
    cur = node
    while isinstance(cur, (ast.Subscript, ast.Attribute)):
        if isinstance(cur, ast.Attribute):
            return _attr_name(cur)
        cur = cur.value  # Subscript → 下标基座继续上溯
    return _attr_name(cur)


def _call_attr_name(node: ast.Call) -> tuple[Optional[str], Optional[tuple[str, str]]]:
    """返回 (方法名, 基座归属)。支持 self.<attr>.m() 与 self.<attr>[…].m()。"""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr, _base_attr(func.value)
    return None, None


def collect_ops(body: "list[Any]", target_kind: str) -> tuple[dict, dict]:
    """扫一段体，按归属 attr 聚合（增长算子, 裁剪算子）证据行（name@line）。"""
    growth: dict[str, set] = {}
    trim: dict[str, set] = {}

    def _add(bucket: dict, attr: str, tag: str, line: int) -> None:
        bucket.setdefault(attr, set()).add("{}@{}".format(tag, line))

    for node in ast.walk(ast.Module(body=list(body), type_ignores=[])):
        if isinstance(node, ast.Call):
            method, owner = _call_attr_name(node)
            if method is not None and owner is not None and owner[0] == target_kind:
                attr = owner[1]
                if method in GROW_METHODS:
                    _add(growth, attr, method, getattr(node, "lineno", 0))
                elif method in TRIM_METHODS:
                    _add(trim, attr, method, getattr(node, "lineno", 0))
        elif isinstance(node, ast.Assign):
            line = getattr(node, "lineno", 0)
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript):
                    owner = _base_attr(tgt)
                    if owner is not None and owner[0] == target_kind:
                        _add(growth, owner[1], "setitem", line)
                elif isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) and tgt.value.id == "self" \
                        and target_kind == "self_attr":
                    if _is_trim_reassign(node.value, tgt.attr):
                        _add(trim, tgt.attr, _trim_reassign_tag(node.value), line)
                elif isinstance(tgt, ast.Name) and target_kind == "module_name":
                    if _is_trim_reassign(node.value, tgt.id):
                        _add(trim, tgt.id, _trim_reassign_tag(node.value), line)
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, (ast.Add, ast.BitOr)):
            owner = _base_attr(node.target)
            if owner is not None and owner[0] == target_kind:
                _add(growth, owner[1], "iadd", getattr(node, "lineno", 0))
        elif isinstance(node, ast.Delete):
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript):
                    owner = _base_attr(tgt)
                    if owner is not None and owner[0] == target_kind:
                        _add(trim, owner[1], "delitem", getattr(node, "lineno", 0))
    return (
        {attr: sorted(lines) for attr, lines in growth.items()},
        {attr: sorted(lines) for attr, lines in trim.items()},
    )


def _is_reslice(value: ast.AST, attr: str) -> bool:
    """右值是否「同名属性的下标/切片取值」（self._h = self._h[-N:] 保尾裁剪形态）。"""
    return isinstance(value, ast.Subscript) and _same_attr(value.value, attr)


def _is_filter_reassign(value: ast.AST, attr: str) -> bool:
    """右值是否「同名属性的带条件推导式滤除」（self._x = [t for t in self._x if …] 滑窗滑动形态）。"""
    if not isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return False
    for comp in value.generators:
        if not comp.ifs:
            continue  # 无条件推导式=复制非滤除（宁漏勿滥）
        if _same_attr(comp.iter, attr):
            return True
    return False


def _is_trim_reassign(value: ast.AST, attr: str) -> bool:
    """重赋值裁剪形态：保尾 reslice 或 滤除推导式（两形态单一入口）。"""
    return _is_reslice(value, attr) or _is_filter_reassign(value, attr)


def _trim_reassign_tag(value: ast.AST) -> str:
    return "filter" if isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)) else "reslice"


def _same_attr(node: ast.AST, attr: str) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == attr:
        return True
    if isinstance(node, ast.Name) and node.id == attr:
        return True
    return False


# ---------------------------------------------------------------- 扫描

def _assign_pairs(node: ast.AST) -> list[tuple[ast.AST, ast.AST]]:
    """Assign/AnnAssign 双形统一取 (target, value) 对（宁漏勿滥，RV-① 同款）。"""
    if isinstance(node, ast.Assign):
        return [(tgt, node.value) for tgt in node.targets]
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [(node.target, node.value)]
    return []


def scan_file(path: Path) -> tuple[list[dict], list[dict]]:
    """扫单文件：返回 (内存面发现, DB 面发现)。"""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return [], []
    try:
        rel = str(path.relative_to(REPO))
    except ValueError:
        rel = str(path)  # 合成源码树（测试 tmp）不属仓内，原样记路径

    mem: list[dict] = []
    # 模块级小写名可变全局（UPPER_CASE 视为常量不收，RV-① 同款）
    module_body = list(tree.body)
    mod_growth, mod_trim = collect_ops(module_body, "module_name")
    for node in module_body:
        for tgt, value in _assign_pairs(node):
            if not (isinstance(tgt, ast.Name) and tgt.id[:1].islower()):
                continue
            kind = container_kind(value)
            if kind is None:
                continue
            mem.append(_mem_entry(rel, None, tgt.id, kind, node.lineno,
                                  _maxlen_site(value, kind),
                                  mod_growth.get(tgt.id, []), mod_trim.get(tgt.id, [])))

    # 类内 self.<attr> 容器（全方法体扫，非仅 __init__，RV-① 同款）
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        cls_growth, cls_trim = collect_ops(list(cls.body), "self_attr")
        seen: set = set()
        for node in ast.walk(cls):
            for tgt, value in _assign_pairs(node):
                if not (isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "self"):
                    continue
                kind = container_kind(value)
                if kind is None or (cls.name, tgt.attr) in seen:
                    continue
                seen.add((cls.name, tgt.attr))
                mem.append(_mem_entry(rel, cls.name, tgt.attr, kind, getattr(node, "lineno", 0),
                                      _maxlen_site(value, kind),
                                      cls_growth.get(tgt.attr, []), cls_trim.get(tgt.attr, [])))

    # DB 面：SQL 字符串常量（INSERT INTO / DELETE FROM）
    inserts: dict[str, set] = {}
    deletes: dict[str, set] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            line = getattr(node, "lineno", 0)
            for m in SQL_INSERT_RE.finditer(text):
                inserts.setdefault(m.group(1), set()).add("INSERT@{}".format(line))
            for m in SQL_DELETE_RE.finditer(text):
                deletes.setdefault(m.group(1), set()).add("DELETE@{}".format(line))
    db = []
    for table in sorted(set(inserts) | set(deletes)):
        ins, dele = sorted(inserts.get(table, [])), sorted(deletes.get(table, []))
        db.append({
            "module": rel,
            "table": table,
            "insert_sites": ins,
            "delete_sites": dele,
            "verdict": classify_db(ins, dele),
        })
    return mem, db


def _mem_entry(rel: str, cls: Optional[str], attr: str, container: str,
               line: int, maxlen_site: Optional[str],
               growth_ops: list[str], trim_ops: list[str]) -> dict:
    return {
        "kind": KIND_INSTANCE if cls is not None else KIND_MODULE,
        "module": rel,
        "class": cls,
        "attr": attr,
        "container": container,
        "maxlen_site": maxlen_site,
        "line": line,
        "growth_ops": list(growth_ops),
        "trim_ops": list(trim_ops),
        "verdict": classify_memory(container, maxlen_site, list(growth_ops), list(trim_ops)),
    }


def scan(root: Path) -> dict:
    files = sorted(
        p for p in root.rglob("*.py")
        if not (set(p.relative_to(root).parts) & SKIP_DIRS)
    )
    mem: list[dict] = []
    db_raw: list[dict] = []
    for path in files:
        m, d = scan_file(path)
        mem.extend(m)
        db_raw.extend(d)

    # 内存面去重键 (module, class, attr)——同键多赋值取首行，算子跨处聚合已在 collect_ops 收齐
    dedup: dict[tuple, dict] = {}
    for f in mem:
        key = (f["module"], f["class"], f["attr"])
        if key not in dedup:
            dedup[key] = f
    mem_entries = sorted(dedup.values(), key=lambda f: (f["module"], f["class"] or "", f["attr"]))

    # DB 面表级聚合（跨模块同表合并=retention 语义看整表）
    tables: dict[str, dict] = {}
    for row in db_raw:
        agg = tables.setdefault(row["table"], {
            "table": row["table"], "modules": [], "insert_sites": [], "delete_sites": [],
        })
        if row["module"] not in agg["modules"]:
            agg["modules"].append(row["module"])
        agg["insert_sites"] = sorted(set(agg["insert_sites"]) | {"{} {}".format(row["module"], s) for s in row["insert_sites"]})
        agg["delete_sites"] = sorted(set(agg["delete_sites"]) | {"{} {}".format(row["module"], s) for s in row["delete_sites"]})
    db_entries = []
    for table in sorted(tables):
        agg = tables[table]
        agg["modules"] = sorted(agg["modules"])
        agg["verdict"] = classify_db(agg["insert_sites"], agg["delete_sites"])
        db_entries.append(agg)

    by_verdict: dict[str, int] = {}
    for e in mem_entries:
        by_verdict[e["verdict"]] = by_verdict.get(e["verdict"], 0) + 1
    db_by_verdict: dict[str, int] = {}
    for e in db_entries:
        db_by_verdict[e["verdict"]] = db_by_verdict.get(e["verdict"], 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "files_scanned": len(files),
        "stats": {
            "memory_findings": len(mem_entries),
            "db_findings": len(db_entries),
            "by_verdict": by_verdict,
            "db_by_verdict": db_by_verdict,
        },
        "memory_entries": mem_entries,
        "db_entries": db_entries,
    }


def _stable(result: dict) -> dict:
    """确定性对账形：去掉时间戳/根路径，只比事实面。"""
    return {
        "files_scanned": result["files_scanned"],
        "stats": result["stats"],
        "memory_entries": result["memory_entries"],
        "db_entries": result["db_entries"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="src")
    parser.add_argument("--json", default="data/unbounded_growth_census_20261023.json")
    parser.add_argument("--check", action="store_true", help="两跑确定性校验（逐键相等）")
    args = parser.parse_args()
    root = (REPO / args.root).resolve()
    result = scan(root)

    if args.check:
        again = scan(root)
        ok = _stable(result) == _stable(again)
        print(json.dumps({"check": "PASS" if ok else "FAIL"}, ensure_ascii=False))
        sys.exit(0 if ok else 1)

    out = REPO / args.json
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"json": str(out), "stats": result["stats"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
