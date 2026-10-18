#!/usr/bin/env python3
"""契约漂移普查（CD-①）——admin-ui 消费键 vs API 真源键 只读静态普查。

GLAND-⑤ trajectory 页 UI↔API 契约漂移（UI 读 session_id/event_count/updated_at
而 API 真源只出 id/total_events/created_at/ended_at）的同型面普查。零行为改动，
只读源码不打 live 服务、不写任何库。

判定口径（诚实分级，禁把推断当实证）：
- produced      消费键在 API 真源键集内（字典字面量/SQL 别名/模型字段/显式 helper 键）
- drift_hard    消费键不在键集内 且 消费面提取完整、handler 形状全解析（零动态操作）
                → 高置信漂移候选（仍需人工复核消费面提取是否误收）
- drift_suspect 消费键不在键集内 但 handler 形状含动态操作（dict(row)/model_dump/
                ** 展开/跨模块调用等）→ 待确认（禁直接定罪）
- unresolved    端点未映射（路径模板归一失败/多路由冲突）或形状提取失败 → 待确认

消费面提取为静态启发式（赋值切段+别名追踪），漏收=漏报（如实边界），误收=
进候选层由人工复核过滤；本工具只产候选与证据（file:line），不改任何代码。
提取只看真代码：注释/字符串字面文本遮蔽后不参与提取（字面文本非消费）；
typed 赋值（const x: T = apiFetch…）、?. 可选链、setX(data) 整体直传、
模板串字面量感知调用截取均在提取面（CD-⑨ 提取精度收口）。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ─────────────────────────── UI 消费面提取 ───────────────────────────

_ASSIGN_RE = re.compile(
    r"const\s+([A-Za-z_$][\w$]*)\s*(?::[^;\n]*?)?=\s*(?:await\s+)?apiFetch\s*(?:<[^>]*>)?\s*\("
)
_METHOD_RE = re.compile(r"method\s*:\s*['\"]([A-Za-z]+)['\"]")
_DESTRUCT_RE = re.compile(r"const\s*\{([^}]*)\}\s*=\s*([A-Za-z_$][\w$]*)\s*[;\n]")
_ACC = r"(?:\?\.|\.)"  # 访问符：`?.` 可选链与 `.` 同消费语义（?. 整类漏报收口）
_ALIAS_RE = re.compile(
    r"const\s+([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\s*" + _ACC + r"\s*([A-Za-z_$][\w$]*)"
    r"(?:\s*[|]{2}\s*[A-Za-z_$][\w$]*)?"
)
_PROP_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*" + _ACC + r"\s*([A-Za-z_$][\w$]*)")
_ITEM_CHAIN_RE = re.compile(
    r"\b([A-Za-z_$][\w$]*)((?:\s*" + _ACC + r"\s*[A-Za-z_$][\w$]*|\s*\([^()]*\))*)"
    r"\s*" + _ACC + r"\s*(?:map|forEach|filter|find|some|every)\s*\(\s*"
    r"(?:\(\s*([A-Za-z_$][\w$]*)[^)]*\)|([A-Za-z_$][\w$]*))"
)
_FOROF_RE = re.compile(r"for\s*\(\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s+of\s+([A-Za-z_$][\w$]*)")
# JS 内建成员（数组/字符串/对象方法与属性）——不是 API 响应键，禁误收
_BUILTIN_MEMBERS = {
    "length", "map", "filter", "find", "findIndex", "forEach", "some", "every", "slice",
    "splice", "push", "pop", "shift", "unshift", "concat", "join", "split", "includes",
    "indexOf", "lastIndexOf", "keys", "values", "entries", "sort", "reverse", "reduce",
    "flat", "flatMap", "trim", "toLowerCase", "toUpperCase", "toString", "replace",
    "match", "startsWith", "endsWith", "padStart", "padEnd", "charAt", "at", "then",
    "catch", "finally", "hasOwnProperty", "toFixed",
}


_REGEX_PREV_CHARS = set("([,=:[!&|?;+-*%~^<>")


def _literal_end(s: str, start: int) -> int:
    """字面量（'…'/"…"/`…`）闭合定界符下标；转义与模板 ${...} 内嵌字面量不误判。
    未闭合=-1。"""
    quote = s[start]
    i = start + 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i
        if c == "\n" and quote != "`":
            return -1
        if quote == "`" and c == "$" and i + 1 < n and s[i + 1] == "{":
            j = _template_expr_end(s, i + 2)
            if j < 0:
                return -1
            i = j
            continue
        i += 1
    return -1


def _template_expr_end(s: str, i: int) -> int:
    """${ 后表达式结尾 '}' 的下一位置（花括号深度+内嵌字面量感知）；未闭合=-1。"""
    depth = 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"`":
            j = _literal_end(s, i)
            if j < 0:
                return -1
            i = j + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _mask_code(src: str, blank_strings: bool = True) -> str:
    """注释/字符串/模板字面量内部遮蔽为等长空白（换行保留=偏移与行号不变）。

    消费键/调用点提取只看真代码：注释与字符串里的「data.键」是字面文本不是消费
    （CD-⑦ 注释盲区+字符串同型误收根因修）。模板 ${...} 表达式内是真代码不遮蔽
    （内嵌字面量递归遮蔽）；正则字面量按前一显著字符启发式识别（边界：return /x/
    形漏判按代码处理）。blank_strings=False 只剥注释（method: 'POST' 取值在字符
    串里，须见原文）。
    """
    out = list(src)
    n = len(src)

    def blank(pos: int) -> None:
        if 0 <= pos < n and out[pos] != "\n":
            out[pos] = " "

    def mask_string(i: int, quote: str) -> int:
        i += 1
        while i < n:
            c = src[i]
            if c == "\\":
                if blank_strings:
                    blank(i)
                    blank(i + 1)
                i += 2
                continue
            if c == quote:
                return i + 1
            if c == "\n" and quote != "`":
                return i
            if quote == "`" and c == "$" and i + 1 < n and src[i + 1] == "{":
                i = code(i + 2, True)
                continue
            if blank_strings:
                blank(i)
            i += 1
        return i

    def mask_regex(i: int) -> int:
        blank(i)
        i += 1
        in_class = False
        while i < n:
            c = src[i]
            if c == "\\":
                blank(i)
                blank(i + 1)
                i += 2
                continue
            if c == "\n":
                return i
            if c == "[":
                in_class = True
            elif c == "]":
                in_class = False
            elif c == "/" and not in_class:
                blank(i)
                i += 1
                while i < n and src[i].isalpha():
                    blank(i)
                    i += 1
                return i
            blank(i)
            i += 1
        return i

    def code(i: int, stop_at_brace: bool) -> int:
        depth = 0
        prev_sig = ""
        while i < n:
            c = src[i]
            if c == "/" and i + 1 < n and src[i + 1] == "/":
                while i < n and src[i] != "\n":
                    blank(i)
                    i += 1
                continue
            if c == "/" and i + 1 < n and src[i + 1] == "*":
                blank(i)
                blank(i + 1)
                i += 2
                while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                    blank(i)
                    i += 1
                if i < n:
                    blank(i)
                    blank(i + 1)
                    i += 2
                continue
            if c == "/" and prev_sig in _REGEX_PREV_CHARS:
                i = mask_regex(i)
                continue
            if c in "'\"`":
                i = mask_string(i, c)
                prev_sig = "'"
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if stop_at_brace and depth == 0:
                    return i + 1
                depth = max(depth - 1, 0)
            if not c.isspace():
                prev_sig = c
            i += 1
        return i

    code(0, False)
    return "".join(out)


def _chain_keys(chain_src: str) -> tuple:
    """元素链中间键（dedupResult.details.slice(0,5) → ("details",)）：方法调用
    token（后随 '('）与 JS 内建成员非响应键。"""
    method_at = {m.start() for m in re.finditer(r"[A-Za-z_$][\w$]*\s*\(", chain_src)}
    out = []
    for m in re.finditer(r"[A-Za-z_$][\w$]*", chain_src):
        tok = m.group(0)
        if m.start() in method_at or tok in _BUILTIN_MEMBERS:
            continue
        out.append(tok)
    return tuple(out)


@dataclass
class CallSite:
    method: str
    path: str
    raw_path: str
    line: int
    var: str | None
    path_dynamic: bool = False
    keys: set[tuple[str, ...]] = field(default_factory=set)
    key_lines: dict = field(default_factory=dict)  # keypath tuple -> line


def _normalize_path(raw: str) -> str:
    """路径模板归一：${...} 前有 /=路径参数→{}；无/（query 尾参数 ${qs}）=路径到此为止；
    其后截 query、去尾斜杠。"""
    p = raw.strip()
    out: list[str] = []
    i = 0
    while i < len(p):
        if p.startswith("${", i):
            j = _template_expr_end(p, i + 2)
            if j < 0:
                break
            if (out[-1] if out else "") == "/":
                out.append("{}")
                i = j
            else:
                break  # query 尾参数拼接（${qs}）——路径到此为止
        else:
            out.append(p[i])
            i += 1
    p = "".join(out)
    cut = len(p)
    for sep in ("?", "#"):
        k = p.find(sep)
        if k >= 0:
            cut = min(cut, k)
    p = p[:cut]
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def _extract_first_arg(src: str, open_idx: int) -> tuple[str, int, int]:
    """取 apiFetch( 后第一个实参字面量（字符串/模板串）与整个调用的右括号位置。"""
    depth = 0
    i = open_idx
    n = len(src)
    arg_start = -1
    while i < n:
        c = src[i]
        if c in "([{":
            depth += 1
            if depth == 1 and arg_start < 0:
                arg_start = i + 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return src, arg_start, i
        i += 1
    return src, arg_start, n


def extract_ui_callsites(source: str) -> list[CallSite]:
    """提取 apiFetch 调用点（方法/路径/响应变量/消费键）。

    消费键=响应变量赋值切段（本次赋值 → 同名变量下次赋值）内的属性访问、
    解构、别名（const x = var.key）与元素级访问（x.map(it => it.k)）。
    """
    src_raw = source
    source = _mask_code(source)  # 提取只看真代码（注释/字符串字面文本非消费）
    opts_text = _mask_code(src_raw, blank_strings=False)  # method 取值在字符串里
    sites: list[CallSite] = []
    assigns = []  # (line_no, var, match_start, open_idx)
    for m in _ASSIGN_RE.finditer(source):
        line_no = source[: m.start()].count("\n") + 1
        assigns.append((line_no, m.group(1), m.start(), m.end() - 1))

    for line_no, var, start, open_idx in assigns:
        _, arg_start, call_end = _extract_first_arg(source, open_idx)
        arg_src = src_raw[arg_start:call_end].strip() if arg_start >= 0 else ""
        raw_path = ""
        path_dynamic = False
        if arg_src and arg_src[0] in "'\"`":
            quote = arg_src[0]
            endq = _literal_end(arg_src, 0)
            raw_path = arg_src[1:endq if endq > 0 else len(arg_src)]
        else:
            path_dynamic = True  # 路径非字面量（变量/拼接）——禁按空串匹配
        opts_src = opts_text[open_idx:call_end]
        mm = _METHOD_RE.search(opts_src)
        method = (mm.group(1) if mm else "GET").upper()
        path = _normalize_path(raw_path)
        site = CallSite(method=method, path=path, raw_path=raw_path, line=line_no,
                        var=var, path_dynamic=path_dynamic)

        # 赋值切段（字符域）：本次调用右括号 → 同名变量下次赋值起点
        seg_end_char = len(source)
        for l2, v2, s2, _o2 in assigns:
            if v2 == var and s2 > start:
                seg_end_char = s2
                break
        # 同名变量任何 keyword 再赋值都断段（含非 apiFetch 形 const data: T =
        # await res.json()——不断段则后续消费错挂到上一 callsite）
        for rm in re.finditer(
            r"(?<![\w$.])(?:const|let|var)\s+%s\b\s*(?::[^=;\n]*?)?=(?!=)(?!>)" % re.escape(var),
            source[start:],
        ):
            pos = start + rm.start()
            if pos > start and pos < seg_end_char:
                seg_end_char = pos
                break
        segment = source[call_end:seg_end_char]
        seg_base = call_end

        def seg_line(pos: int) -> int:
            return source[: seg_base + pos].count("\n") + 1

        # 别名表：alias -> (源变量, keypath)；含 state setter 形（setSessions(data.sessions)
        # → sessions 别名——GLAND-⑤ trajectory 原始消费形：响应先入 state 再 map 消费）
        aliases: dict[str, tuple[str, tuple[str, ...]]] = {}
        for am in _ALIAS_RE.finditer(segment):
            alias, src_var, key = am.group(1), am.group(2), am.group(3)
            if src_var == var:
                aliases[alias] = (var, (key,))
        for am in re.finditer(
            r"set([A-Z][\w$]*)\s*\(\s*([A-Za-z_$][\w$]*)\s*" + _ACC + r"\s*([A-Za-z_$][\w$]*)",
            segment,
        ):
            if segment[: am.start()].rstrip().endswith("=>"):
                continue  # 箭头形参遮蔽（(data) => setX(data)）——非本 callsite 变量
            if am.group(2) == var:
                x = am.group(1)
                aliases[x] = (var, (am.group(3),))
                aliases[x[0].lower() + x[1:]] = (var, (am.group(3),))
        # 整体直传形 setX(var)（CD-⑧ setDedupResult(data)）→ 状态变量=整个响应对象
        for am in re.finditer(
            r"set([A-Z][\w$]*)\s*\(\s*([A-Za-z_$][\w$]*)\s*\)",
            segment,
        ):
            if segment[: am.start()].rstrip().endswith("=>"):
                continue  # 箭头形参遮蔽（(data) => setX(data)）——非本 callsite 变量
            if am.group(2) == var:
                x = am.group(1)
                aliases[x] = (var, ())
                aliases[x[0].lower() + x[1:]] = (var, ())
        # 变量本身也是元素容器别名（data.sessions ?? data 形）
        for am in re.finditer(
            r"const\s+([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\s*" + _ACC + r"\s*([A-Za-z_$][\w$]*)\s*"
            r"[|]{2}\s*([A-Za-z_$][\w$]*)",
            segment,
        ):
            if am.group(4) == var:
                aliases.setdefault(am.group(1), (var, (am.group(3),)))

        def add_key(kp: tuple[str, ...], ln: int) -> None:
            if kp[-1] in _BUILTIN_MEMBERS:  # JS 内建成员非 API 响应键
                return
            site.keys.add(kp)
            site.key_lines.setdefault(kp, ln)

        # ① 切段内：var 直接访问 + 解构
        seg_lines = segment.splitlines()
        for off, seg in enumerate(seg_lines):
            ln = seg_line(segment.find(seg)) if seg else line_no
            dm = _DESTRUCT_RE.search(seg)
            if dm and dm.group(2) == var:
                for part in dm.group(1).split(","):
                    name = part.split(":")[0].strip()
                    if re.fullmatch(r"[A-Za-z_$][\w$]*", name or ""):
                        add_key((name,), ln)
            for pm in _PROP_RE.finditer(seg):
                if pm.group(1) == var:
                    add_key((pm.group(2),), ln)  # _PROP_RE 已含 ?. 可选链

        # ② 全文件：别名访问/元素级访问（state 别名链的消费点在组件 JSX 域，切段外）
        for base, (src_var, prefix) in aliases.items():
            if base == var:
                continue  # 自身别名（setData(data)）走 ①③ 切段域，禁全文件扩面
            for pm in re.finditer(
                r"\b%s\s*" % re.escape(base) + _ACC + r"\s*([A-Za-z_$][\w$]*)", source
            ):
                add_key(prefix + (pm.group(1),), source[: pm.start()].count("\n") + 1)
            for dm in _DESTRUCT_RE.finditer(source):
                if dm.group(2) != base:
                    continue
                for part in dm.group(1).split(","):
                    name = part.split(":")[0].strip()
                    if re.fullmatch(r"[A-Za-z_$][\w$]*", name or ""):
                        add_key(prefix + (name,), source[: dm.start()].count("\n") + 1)
            for em in _ITEM_CHAIN_RE.finditer(source):
                if em.group(1) != base:
                    continue
                chain = _chain_keys(em.group(2))
                item = em.group(3) or em.group(4)
                rest = source[em.end(): em.end() + 400]
                for ip in re.finditer(
                    r"\b%s\s*" % re.escape(item) + _ACC + r"\s*([A-Za-z_$][\w$]*)", rest
                ):
                    add_key(prefix + chain + (ip.group(1),), source[: em.start()].count("\n") + 1)
            for fm in _FOROF_RE.finditer(source):
                if fm.group(2) != base:
                    continue
                item = fm.group(1)
                rest = source[fm.end(): fm.end() + 800]
                for ip in re.finditer(
                    r"\b%s\s*" % re.escape(item) + _ACC + r"\s*([A-Za-z_$][\w$]*)", rest
                ):
                    add_key(prefix + (ip.group(1),), source[: fm.start()].count("\n") + 1)

        # ③ 切段内 var 元素级直写形（data.sessions.map((s) => s.k) → ("sessions","k")；
        #    链式容器 dedupResult.details.slice(0,5).map((d) => d.k) → ("details","k")）
        for em in _ITEM_CHAIN_RE.finditer(segment):
            if em.group(1) != var:
                continue
            chain = _chain_keys(em.group(2))
            item = em.group(3) or em.group(4)
            rest = segment[em.end(): em.end() + 400]
            for ip in re.finditer(
                r"\b%s\s*" % re.escape(item) + _ACC + r"\s*([A-Za-z_$][\w$]*)", rest
            ):
                add_key(chain + (ip.group(1),), seg_line(em.start()))
        sites.append(site)
    return sites


# ─────────────────────────── API 真源面提取 ───────────────────────────

_IMPORT_RE = re.compile(r"from\s+src\.api\.([\w.]+)\s+import\s+[^;\n]*?\b(?:router|app)\s+as\s+(\w+)")
_INCLUDE_RE = re.compile(r"app\.include_router\s*\(", re.M)
_ROUTE_RE = re.compile(r"@router\.(get|post|put|patch|delete)\s*\(\s*(['\"`])([^'\"`]*)\2([^\n]*)")
_DEF_RE = re.compile(r"^(?:async\s+)?def\s+(\w+)\s*\(", re.M)
_DICT_KEY_RE = re.compile(r"['\"]([A-Za-z_][\w\-]*)['\"]\s*:")
_SELECT_RE = re.compile(r"SELECT\s+(.*?)\s+FROM\s+", re.I | re.S)
_MODEL_DUMP_RE = re.compile(r"\b(model_dump|asdict)\s*\(|\bdict\s*\(\s*\w+\s*\)|\*\*\w+|json\.loads\s*\(")
_TO_DICT_RE = re.compile(r"\b([A-Za-z_][\w]*)\s*[?.]\s*(to_dict|dict|model_dump)\s*\(")
_CLASS_NEW_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\s*\(")
_RESP_MODEL_RE = re.compile(r"response_model\s*=\s*(?:list\[)?([A-Za-z_][\w]*)")
_APIROUTER_PREFIX_RE = re.compile(r"router\s*=\s*APIRouter\s*\(\s*[^)]*prefix\s*=\s*(['\"])([^'\"]*)\1")


@dataclass
class Endpoint:
    method: str
    path: str
    module: str
    func: str
    line: int
    body: str
    decorator: str = ""
    resp_model: str | None = None


@dataclass
class Shape:
    produced: set[str] = field(default_factory=set)
    item_keys: dict[str, set[str]] = field(default_factory=dict)  # 顶层数组键 -> 元素键
    dynamic: bool = False
    resolved: bool = False
    evidence: list[str] = field(default_factory=list)


def _statement_blocks(src: str, start: int) -> str:
    """从 def 行截取函数体（到下一个顶层 def/class/@ 装饰器）。"""
    rest = src[start:]
    m = re.search(r"\n(?:@router\.|(?:async\s+)?def\s+\w+\s*\(|class\s+\w+)", rest[1:])
    return rest[: m.start() + 1] if m else rest


def build_endpoint_index(src_root: Path) -> dict[tuple[str, str], list[Endpoint]]:
    """main.py include_router 挂载面 → (method, path) 端点索引（禁手抄 prefix）。"""
    main_src = (src_root / "main.py").read_text(encoding="utf-8")
    var_to_module = {m.group(2): m.group(1) for m in _IMPORT_RE.finditer(main_src)}
    # include_router(<var>, prefix="...") 多行感知
    prefix_of: dict[str, str] = {}
    for m in _INCLUDE_RE.finditer(main_src):
        depth = 0
        i = m.end() - 1
        while i < len(main_src):
            if main_src[i] == "(":
                depth += 1
            elif main_src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        stmt = main_src[m.start(): i + 1]
        vm = re.search(r"\(\s*([A-Za-z_][\w]*)", stmt)
        pm = re.search(r"prefix\s*=\s*(['\"])([^'\"]*)\1", stmt)
        if vm:
            prefix_of[vm.group(1)] = pm.group(2) if pm else ""
    index: dict[tuple[str, str], list[Endpoint]] = {}
    for var, prefix in prefix_of.items():
        module = var_to_module.get(var)
        if not module:
            continue
        mod_path = src_root / "api" / f"{module.replace('.', '/')}.py"
        if not mod_path.exists():
            continue
        mod_src = mod_path.read_text(encoding="utf-8")
        apm = _APIROUTER_PREFIX_RE.search(mod_src)
        mod_prefix = apm.group(2) if apm else ""  # APIRouter(prefix=...) 模块内嵌前缀
        for rm in _ROUTE_RE.finditer(mod_src):
            method, path = rm.group(1).upper(), rm.group(3)
            decorator = rm.group(0)
            line_no = mod_src[: rm.start()].count("\n") + 1
            def_m = _DEF_RE.search(mod_src, rm.end())
            if not def_m:
                continue
            body = _statement_blocks(mod_src, def_m.start())
            full = _normalize_path(mod_prefix + prefix + path)
            full = re.sub(r"\{[^}]*\}", "{}", full)
            rmm = _RESP_MODEL_RE.search(decorator)
            index.setdefault((method, full), []).append(
                Endpoint(method, full, module, def_m.group(1), line_no, body,
                         decorator, rmm.group(1) if rmm else None)
            )
    return index


def _select_aliases(sql: str) -> set[str]:
    m = _SELECT_RE.search(sql)
    if not m:
        return set()
    cols: set[str] = set()
    depth = 0
    cur = ""
    for c in m.group(1):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == "," and depth == 0:
            cols.add(cur)
            cur = ""
        else:
            cur += c
    cols.add(cur)
    out: set[str] = set()
    for col in cols:
        col = col.strip()
        if not col or col == "*":
            if col == "*":
                out.add("*")
            continue
        am = re.search(r"\bAS\s+([A-Za-z_][\w]*)\s*$", col, re.I)
        if am:
            out.add(am.group(1))
        else:
            tail = re.split(r"\s+", col.strip())[-1]
            tail = tail.split(".")[-1]
            if re.fullmatch(r"[A-Za-z_][\w]*", tail):
                out.add(tail)
    return out


def _class_shape(class_name: str, src_root: Path, _seen: set[str] | None = None) -> set[str] | None:
    """模型真源键：类的 to_dict()/dict() 字面量键 或 字段注解名（dataclass/BaseModel），
    基类字段并入（EntityResponse(EntityBase) 一类继承面，禁漏真源键）。"""
    _seen = _seen if _seen is not None else set()
    if class_name in _seen:
        return None
    _seen.add(class_name)
    for py in sorted((src_root).rglob("*.py")):
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        m = re.search(r"^class\s+%s\b([^:]*):" % re.escape(class_name), text, re.M)
        if not m:
            continue
        body = _statement_blocks(text, m.start())
        keys: set[str] = set()
        for dm in re.finditer(
            r"def\s+(?:to_dict|dict|model_dump)\s*\([^)]*\)[^:]*:\s*(?:\"\"\".*?\"\"\"\s*)?(?:[^{]*?)return\s*(\{)",
            body,
            re.S,
        ):
            keys |= _dict_literal_keys(body, dm.start(1))
        for fm in re.finditer(r"^\s{4}([A-Za-z_][\w]*)\s*:\s*[^=\n]+(?:=.*)?$", body, re.M):
            keys.add(fm.group(1))
        for base in re.findall(r"[A-Za-z_][\w]*", m.group(1)):
            if base in {"BaseModel", "Base", "object"} or base == class_name:
                continue
            base_keys = _class_shape(base, src_root, _seen)
            if base_keys:
                keys |= base_keys
        if keys:
            return keys
    return None


def _dict_literal_keys(src: str, brace_idx: int) -> set[str]:
    depth = 0
    i = brace_idx
    buf = ""
    while i < len(src):
        c = src[i]
        if c == "{":
            depth += 1
            if depth == 1:
                i += 1
                continue
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        if depth == 1:
            buf += c
        i += 1
    return {m.group(1) for m in _DICT_KEY_RE.finditer(buf)}


def extract_shape(body: str, module_src: str, src_root: Path) -> Shape:
    """handler 返回形状：字典字面量键 ∪ SQL 别名 ∪ 模型字段；动态操作置 dynamic。"""
    shape = Shape()
    dynamic = False
    if _MODEL_DUMP_RE.search(body):
        dynamic = True
    # SQL 别名（fetch/fetchrow/exec 字符串字面量）
    for sm in re.finditer(r"(?:fetch|fetchrow|execute|executemany)\s*\(\s*(['\"]{3}|['\"])(.*?)\1", body, re.S):
        sql = sm.group(2)
        if re.search(r"\bSELECT\b", sql, re.I):
            aliases = _select_aliases(sql)
            if "*" in aliases:
                dynamic = True
                aliases.discard("*")
            shape.produced |= aliases
            shape.evidence.append("sql-alias")
    # return 字典字面量（含 return {"k": [...]} 顶层数组键的元素形状）
    for rm in re.finditer(r"\breturn\s+(\{)", body):
        keys = _dict_literal_keys(body, rm.start(1))
        shape.produced |= keys
        shape.resolved = True
        shape.evidence.append("dict-literal")
        # 顶层数组值元素键：  "k": [x.to_dict() for ...] / [{"a":1}] / [_row(x) ...]
        seg = body[rm.start(1):]
        for km in re.finditer(r"['\"]([A-Za-z_][\w\-]*)['\"]\s*:\s*(\[[^\]]*?\])", seg, re.S):
            arr = km.group(2)
            item_keys: set[str] = set()
            if re.search(r"\{", arr):
                for dm in re.finditer(r"\{", arr):
                    item_keys |= _dict_literal_keys(arr, dm.start())
                if item_keys:
                    shape.item_keys.setdefault(km.group(1), set()).update(item_keys)
            for tm in _TO_DICT_RE.finditer(arr):
                cls_keys = _class_shape(tm.group(1), src_root)
                if cls_keys:
                    shape.item_keys.setdefault(km.group(1), set()).update(cls_keys)
                    shape.evidence.append(f"model:{tm.group(1)}")
                else:
                    dynamic = True
            for cm in _CLASS_NEW_RE.finditer(arr):
                cls_keys = _class_shape(cm.group(1), src_root)
                if cls_keys:
                    shape.item_keys.setdefault(km.group(1), set()).update(cls_keys)
                elif cm.group(1)[0].isupper() and cm.group(1) not in {"List", "Dict", "Set", "Optional"}:
                    dynamic = True
        # 顶层数组键若元素形状未解析=动态
        for km in re.finditer(r"['\"]([A-Za-z_][\w\-]*)['\"]\s*:\s*(\[[^\]]*?\])", seg, re.S):
            if km.group(1) not in shape.item_keys:
                dynamic = True
    # return 单变量形：局部字典字面量赋值=真源键；跨模块调用结果=动态（禁当全解析）
    for rm in re.finditer(r"\breturn\s+([A-Za-z_][\w]*)\s*$", body, re.M):
        name = rm.group(1)
        am = re.search(r"^\s*%s\s*=\s*(\{)" % re.escape(name), body, re.M)
        if am:
            shape.produced |= _dict_literal_keys(body, am.start(1))
            shape.resolved = True
        else:
            dynamic = True
    for tm in _TO_DICT_RE.finditer(body):
        cls_keys = _class_shape(tm.group(1), src_root)
        if cls_keys:
            shape.produced |= cls_keys
            shape.evidence.append(f"model:{tm.group(1)}")
        else:
            dynamic = True
    # 显式 helper 键（_row_to_dict 形：d["k"] = … / return {"k": …}）
    for hm in re.finditer(r"(\w+)\s*\([^)]*\)", body):
        helper = hm.group(1)
        if helper.startswith("_"):
            hm2 = re.search(r"^def\s+%s\s*\(" % re.escape(helper), module_src, re.M)
            if hm2:
                hbody = _statement_blocks(module_src, hm2.start())
                for km in re.finditer(r"\[\s*['\"]([A-Za-z_][\w\-]*)['\"]\s*\]\s*=", hbody):
                    shape.produced.add(km.group(1))
                    shape.evidence.append(f"helper:{helper}")
                for hm3 in re.finditer(r"\breturn\s+(\{)", hbody):
                    shape.produced |= _dict_literal_keys(hbody, hm3.start(1))
                    shape.evidence.append(f"helper:{helper}")
                if _MODEL_DUMP_RE.search(hbody):
                    dynamic = True
    shape.dynamic = dynamic
    return shape


# ─────────────────────────── 对账 ───────────────────────────

VERDICTS = ("produced", "drift_hard", "drift_suspect", "unresolved_endpoint",
            "unresolved_shape", "unresolved_path")


@dataclass
class Finding:
    page: str
    line: int
    method: str
    path: str
    key: list[str]
    verdict: str
    handler: str | None
    produced: list[str]
    dynamic: bool
    note: str = ""
    path_status: str = ""


def _openapi_paths(spec: dict) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for p, ops in spec.get("paths", {}).items():
        norm = re.sub(r"\{[^}]*\}", "{}", p)
        for m in ops:
            if m.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                out.add((m.upper(), norm))
    return out


def compare(ui_root: Path, src_root: Path, openapi: dict | None = None) -> dict:
    index = build_endpoint_index(src_root)
    oapi = _openapi_paths(openapi) if openapi else None
    findings: list[Finding] = []
    page_files = sorted(ui_root.rglob("page.tsx")) + sorted(ui_root.rglob("*.ts"))
    page_files = [p for p in page_files if "node_modules" not in str(p) and ".next" not in str(p)]
    callsites = 0
    for pf in page_files:
        source = pf.read_text(encoding="utf-8")
        for site in extract_ui_callsites(source):
            callsites += 1
            rel = str(pf.relative_to(ui_root))

            def path_status(method: str, path: str) -> str:
                if oapi is None or site.path_dynamic:
                    return ""
                norm = re.sub(r"\{[^}]*\}", "{}", path)
                for v in (norm, norm + "/", norm.rstrip("/")):
                    if (method, v) in oapi:
                        return "in_openapi"
                return "missing_in_openapi"

            if site.path_dynamic or not site.path:
                for kp in sorted(site.keys):
                    findings.append(Finding(
                        rel, site.key_lines.get(kp, site.line), site.method, site.raw_path,
                        list(kp), "unresolved_path", None, [], False,
                        "路径非字面量（变量/拼接）——端点无法静态映射", ""))
                continue

            eps = index.get((site.method, site.path))
            pstatus = path_status(site.method, site.path)
            matched_note = ""
            if not eps:
                for kp in sorted(site.keys):
                    findings.append(Finding(
                        rel, site.key_lines.get(kp, site.line), site.method, site.path,
                        list(kp), "unresolved_endpoint", None, [], False,
                        "端点未映射（多路由/模板）", pstatus))
                continue
            ep = eps[0]
            shape = extract_shape(
                ep.body,
                (src_root / "api" / f"{ep.module}.py").read_text(encoding="utf-8"),
                src_root,
            )
            if ep.resp_model:
                rm_keys = _class_shape(ep.resp_model, src_root)
                if rm_keys:
                    # response_model=wire 契约权威（FastAPI 按模型序列化/过滤）——
                    # 键面以此为准，handler 内部动态操作不再降级判定
                    shape.produced = set(rm_keys)
                    shape.dynamic = False
                    shape.resolved = True
                    shape.evidence.append(f"response_model:{ep.resp_model}")
                else:
                    shape.dynamic = True
            handler = f"{ep.module}.{ep.func}:{ep.line}"
            if not shape.resolved and not shape.produced:
                for kp in sorted(site.keys):
                    findings.append(Finding(
                        rel, site.key_lines.get(kp, site.line), site.method, site.path,
                        list(kp), "unresolved_shape", handler, [], shape.dynamic,
                        "形状提取失败" + matched_note, pstatus))
                continue
            for kp in sorted(site.keys):
                top, rest = kp[0], kp[1:]
                if rest:
                    item_keys = shape.item_keys.get(top)
                    if item_keys is None:
                        verdict = "drift_suspect" if shape.dynamic else "unresolved_shape"
                        note = "元素形状未解析"
                    elif rest[0] in item_keys:
                        verdict, note = "produced", ""
                    elif shape.dynamic:
                        verdict, note = "drift_suspect", "元素键缺席（形状含动态操作）"
                    else:
                        verdict, note = "drift_hard", "元素键缺席（形状全解析）"
                else:
                    if top in shape.produced:
                        verdict, note = "produced", ""
                    elif shape.dynamic or not shape.produced:
                        verdict, note = "drift_suspect", "键缺席（形状含动态操作）"
                    else:
                        verdict, note = "drift_hard", "键缺席（形状全解析）"
                findings.append(Finding(
                    rel, site.key_lines.get(kp, site.line), site.method, site.path,
                    list(kp), verdict, handler, sorted(shape.produced), shape.dynamic,
                    note + matched_note, pstatus))
    counts: dict[str, int] = {v: 0 for v in VERDICTS}
    path_counts: dict[str, int] = {}
    for f in findings:
        counts[f.verdict] = counts.get(f.verdict, 0) + 1
        if f.path_status:
            path_counts[f.path_status] = path_counts.get(f.path_status, 0) + 1
    return {
        "findings": [f.__dict__ for f in findings],
        "counts": counts,
        "path_counts": path_counts,
        "endpoints_indexed": len(index),
        "pages_scanned": len(page_files),
        "callsites": callsites,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui-root", default=str(REPO / "admin-ui" / "app"))
    ap.add_argument("--src-root", default=str(REPO / "src"))
    ap.add_argument("--out", default="")
    ap.add_argument("--openapi", default="", help="openapi.json（live 挂载路由表真源）路径")
    ap.add_argument("--hard-only", action="store_true")
    args = ap.parse_args()
    openapi = json.loads(Path(args.openapi).read_text(encoding="utf-8")) if args.openapi else None
    report = compare(Path(args.ui_root), Path(args.src_root), openapi)
    if args.hard_only:
        report["findings"] = [f for f in report["findings"] if f["verdict"] == "drift_hard"]
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"written: {args.out}")
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
