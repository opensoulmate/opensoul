"""工具级权限引擎 — Claude-Code式5模式 × 规则匹配 × 人工审批

调研来源（100-agent研究 SUMMARY.md P0-3，10+方互证）：
- AgentScope PermissionEngine 848行（~/agent-research-src/agentscope/src/agentscope/permission/）：
  5模式(DEFAULT/ACCEPT_EDITS/EXPLORE/BYPASS/DONT_ASK) × 逐模式评估顺序；
  规则匹配按tool类型分流（Bash子串/前缀通配、文件glob、其他工具通用）；
  EXPLORE的只读保证不可被allow规则授予；bypass-immune safety ASK。
- kilocode三层叠加（feature-matrix PROGRESS六轮补验）：base×approved×session
  findLast语义（session来源规则优先于builtin）、hardRuleset硬否决（任何模式不可覆盖）、
  敏感权限必须真人交互回复（机器审批静默拒绝）、权限provenance写回"为什么允许/拒绝"。
- mem0审计表（evolution-engine-patterns §1.2）：每次决策/规则变更留
  old/new/event 审计轨迹。
- Roo-Code RooProtectedController：引擎自身规则文件不可被agent改写（hard规则
  只能由人工经API删除，删除动作本身写审计）。

OpenSoul现状（SUMMARY.md原文）："casbin用户级RBAC——无工具/路径/参数粒度"。
本模块补齐工具/路径/参数粒度，casbin继续负责用户级RBAC，两者互补。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("opensoul.immune.permission_engine")


# ── 基础类型（AgentScope _types.py 移植） ─────────────────────────


class PermissionMode(StrEnum):
    """权限模式 — 每种模式的评估顺序独立封装（AgentScope语义）"""

    DEFAULT = "default"  # 未匹配任何allow规则的操作都要问
    ACCEPT_EDITS = "accept_edits"  # 工作目录内的编辑自动放行，其余走规则
    EXPLORE = "explore"  # 只读模式，一切修改被否决（allow规则不可覆盖）
    BYPASS = "bypass"  # 跳过安全询问，仅deny/hard规则兜底
    DONT_ASK = "dont_ask"  # 无人值守：一切ASK转换为DENY（绝不返回ASK）


class PermissionBehavior(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    PASSTHROUGH = "passthrough"


# 工具分类（决定匹配策略与只读快路径）
READ_ONLY_TOOLS = {
    "read_file",
    "read_file_segment",
    "list_files",
    "search_files",
    "web_search",
    "web_extract",
    "vision_analyze",
    "read_image",
    "check_evolution_status",
    "todo",
    "clarify",
}
FILE_TOOLS = {
    "read_file",
    "read_file_segment",
    "write_file",
    "patch",
    "read_image",
    "vision_analyze",
}
SHELL_TOOLS = {"terminal", "execute_code"}
MUTATING_TOOLS = {"write_file", "patch", "terminal", "execute_code", "request_evolution"}

# shell只读命令（DEFAULT/EXPLORE下也放行 — AgentScope Bash check_read_only）
_SHELL_READONLY_PREFIXES = (
    "ls",
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "wc",
    "file",
    "stat",
    "du",
    "df",
    "pwd",
    "which",
    "whoami",
    "id",
    "date",
    "echo",
    "grep",
    "rg",
    "find",
    "tree",
    "git status",
    "git log",
    "git diff",
    "git show",
    "git branch",
    "git remote",
    "ps",
    "top -",
    "free",
    "uname",
    "hostname",
    "env",
    "printenv",
    "md5sum",
    "sha256sum",
)

# ── 内置基线策略（source="builtin"，可经API删除/覆盖，hard除外） ──

# hard deny：任何模式、任何来源规则都不可覆盖（kilocode hardRuleset硬否决）
BUILTIN_HARD_DENY = [
    # 灾难性shell命令 — 直接毁机/毁盘
    ("terminal", "rm -rf /"),
    ("terminal", "rm -rf /*"),
    ("terminal", "mkfs"),
    ("terminal", "dd if="),
    ("terminal", ":(){ :|:& };:"),
    ("terminal", "> /dev/sd"),
    ("terminal", "chmod -R 777 /"),
    ("terminal", "chown -R /"),
]

# 普通ask：敏感操作需人工确认（session来源allow规则可覆盖 — kilocode findLast）
BUILTIN_ASK = [
    # shell提权/系统管理/出网（供应链攻击面 — SUMMARY.md行业信号7）
    ("terminal", "sudo ", "high"),
    ("terminal", "rm -rf", "high"),
    ("terminal", "systemctl", "medium"),
    ("terminal", "shutdown", "high"),
    ("terminal", "reboot", "high"),
    ("terminal", "mkfs", "high"),
    ("terminal", "iptables", "high"),
    ("terminal", "curl ", "medium"),
    ("terminal", "wget ", "medium"),
    ("terminal", "chmod -R", "medium"),
    ("terminal", "chown -R", "medium"),
    ("terminal", "kill -9", "low"),
    # 机密文件读取（防凭证经LLM外泄 — Warp secret_redaction同源风险）
    ("read_file", ".ssh", "high"),
    ("read_file", "id_rsa", "high"),
    ("read_file", "id_ed25519", "high"),
    ("read_file", ".pem", "high"),
    ("read_file", ".key", "high"),
    ("read_file", ".env", "medium"),
    ("read_file", "/etc/shadow", "high"),
    ("read_file", ".aws/credentials", "high"),
    ("read_file", ".gnupg", "high"),
    ("search_files", ".ssh", "high"),
    ("search_files", "id_rsa", "high"),
    # 写入系统目录
    ("write_file", "/etc/", "high"),
    ("write_file", "/boot/", "high"),
    ("write_file", "/usr/", "medium"),
    ("write_file", ".ssh", "high"),
    ("patch", "/etc/", "high"),
    ("patch", ".ssh", "high"),
]


# ── 规则与决策 ─────────────────────────────────────────────────


@dataclass
class PermissionRule:
    """一条权限规则。

    rule_content 语义随 tool 类型分流（AgentScope match_rule 移植）：
    - shell工具：子串匹配；"git:*" 形式 = 前缀通配（首token匹配）
    - 文件工具：glob匹配路径（"src/**"、"**/.ssh/**"）
    - 其他工具：对参数JSON做子串匹配；空content = 匹配该工具全部调用
    """

    tool_name: str  # "terminal" / "read_file" / "*"（全部工具）
    rule_content: str | None  # None/"" = 整工具级规则
    behavior: PermissionBehavior
    source: str = "user"  # builtin / user / session（kilocode三层）
    hard: bool = False  # 硬规则：任何模式不可覆盖
    risk: str = "medium"  # low/medium/high — ASK时传导给审批UI
    rule_id: str = ""
    seq: int = 0  # 后添加者seq大（findLast）
    created_at: float = 0.0
    is_deleted: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["behavior"] = self.behavior.value
        return d


@dataclass
class PermissionDecision:
    """一次权限判定的结果 — 带完整provenance（kilocode"为什么允许/拒绝"）"""

    behavior: PermissionBehavior
    message: str
    decision_reason: str = ""
    rule_id: str = ""
    rule_source: str = ""  # 命中规则的来源层
    rule_content: str = ""  # 命中规则的内容
    requires_human: bool = False  # ASK且必须真人交互（机器不可代答）
    risk: str = "medium"
    suggested_rules: list = field(default_factory=list)
    decision_id: str = ""  # ASK决策的审计ID（人工结果回写用）
    mode: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["behavior"] = self.behavior.value
        return d


# 来源层优先级：session（本会话人工批准产生）> user（API添加）> builtin（内置基线）
_SOURCE_PRIORITY = {"session": 3, "user": 2, "builtin": 1}


def _shell_command_of(tool_input: dict) -> str:
    # acp-proxy execute_code工具的代码载荷在"code"键（非"command"/"cmd"）
    return str(tool_input.get("command") or tool_input.get("cmd") or tool_input.get("code") or "")


def _path_of(tool_input: dict) -> str:
    return str(tool_input.get("path") or "")


def match_rule(rule_content: str | None, tool_name: str, tool_input: dict) -> bool:
    """规则匹配 — 按工具类型分流（AgentScope各tool.match_rule的Python复刻）"""
    if not rule_content or rule_content == "*":
        return True  # 空content或"*" = 工具级规则，匹配该工具一切调用

    if tool_name in SHELL_TOOLS or rule_content.startswith(("sudo ", "rm ", "curl ", "wget ")):
        cmd = _shell_command_of(tool_input)
        if not cmd:
            return False
        if rule_content.endswith(":*") or rule_content.endswith(" *"):
            # 前缀通配："git:*" 匹配 "git push origin main"
            prefix = rule_content.rstrip("*").rstrip(":").rstrip()
            return cmd.strip().startswith(prefix)
        return rule_content in cmd

    if tool_name in FILE_TOOLS:
        path = _path_of(tool_input)
        if not path:
            return False
        # glob匹配完整路径 + 尾段basename模式
        if fnmatch.fnmatch(path, rule_content):
            return True
        if fnmatch.fnmatch(os.path.basename(path), rule_content):
            return True
        # "**/x/**" 风格：路径中出现该段即命中（"/home/u/.ssh/id_rsa" ⊨ "**/.ssh/**"）
        stripped = rule_content.strip("*").strip("/")
        if stripped and stripped in path:
            return True
        return False

    # 其他工具（含MCP工具）：参数JSON子串匹配
    try:
        payload = json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = str(tool_input)
    return rule_content in payload


# ── P0-3 参数粒度数据外泄检测（credential exfiltration） ──────────────
# 缺口来源：SUMMARY.md P0-3「路径/参数粒度」+ 上轮遗留「工具参数级命令注入
# （curl外发/凭据窃取类终端命令）」。参照 Warp secret_redaction（凭据识别）+
# agent-zero _infection_check（外发通道）。
# 语义：网络出口工具（curl/wget/nc/scp/ssh/ftp…）把**机密文件**当载荷上传、
# 管道喂入或重定向，= 凭据外泄 → hard deny（任何模式/规则不可覆盖）。
# 普通网络请求/下载、非机密文件传输不在此列（负控制零误伤）。

# 网络出口（数据能离机的通道）
_NETWORK_SINK_RE = re.compile(
    r"(?:^|[|;&\s])(?:curl|wget|nc|ncat|netcat|ftp|sftp|scp|ssh|telnet|socat|certutil)\b"
    r"|/dev/(?:tcp|udp)/",
    re.IGNORECASE,
)
# 反弹shell / 交互式远程执行通道（本身即外泄通道，不需再判网络出口）
_REVERSE_SHELL_RE = re.compile(
    r"/dev/(?:tcp|udp)/|\bbash\s+-i\b|\bsh\s+-i\b|\bnc\s+(?:-\w*\s+)*-e\b|\bncat\b[^\n]*\s-e\b|>&\s*/dev/tcp",
    re.IGNORECASE,
)
# 强凭据文件路径（scp位置操作数判定用，降低误伤）
_SENSITIVE_FILE_RE = re.compile(
    r"\.ssh/|id_rsa|id_ed25519|id_ecdsa|id_dsa|\.pem\b|\.key\b|\.env\b|/etc/passwd|/etc/shadow"
    r"|\.aws/credentials|\.aws/config|\.gnupg|\.netrc|\.git-credentials|\.npmrc|\.pypirc"
    r"|\.kube/config|\.docker/config|\.bash_history|\.zsh_history",
    re.IGNORECASE,
)
# 弱机密命名词（@/管道语境明确，允许更宽）
_SENSITIVE_WORD_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|secret|password|passwd|credential|private[_-]?key|token)",
    re.IGNORECASE,
)
# 上传位置 @path（curl -d @file / --data-binary @file / -F name=@file / --form ...@file）
_AT_FILE_RE = re.compile(r"@(?:'[^']*'|\"[^\"]*\"|[^\s'\"]+)")
# --post-file=PATH / --upload-file=PATH
_UPLOAD_FLAG_RE = re.compile(r"--(?:post-file|upload-file)[= ]([^\s;]+)", re.IGNORECASE)
# cat PATH | / < PATH（喂入网络出口）
_PIPE_SRC_RE = re.compile(r"(?:cat|head|tail|less|more|grep)\s+([^\s|;]+)\s*\|", re.IGNORECASE)
_REDIR_SRC_RE = re.compile(r"<\s*([^\s;]+)")
# scp/sftp/ftp 位置操作数（本地源，含@的远端目标跳过）
_TRANSFER_CMD_RE = re.compile(r"(?:^|[|;&\s])(?:scp|sftp|ftp)\b", re.IGNORECASE)


def _is_sensitive_ref(text: str) -> bool:
    return bool(_SENSITIVE_FILE_RE.search(text or "") or _SENSITIVE_WORD_RE.search(text or ""))


def detect_exfiltration(tool_input: dict | None) -> dict | None:
    """参数粒度数据外泄检测。命中返回 finding dict，否则 None。

    只在 shell/execute_code 命令载荷上判定；finding 形状：
    ``{"kind", "risk", "hard", "reason", "matched"}``。
    """
    cmd = _shell_command_of(tool_input or {})
    if not cmd:
        return None

    # 1) 反弹shell / 交互式远程执行通道 —— 本身就是外泄通道
    m = _REVERSE_SHELL_RE.search(cmd)
    if m:
        return {
            "kind": "reverse_shell",
            "risk": "high",
            "hard": True,
            "reason": "interactive/reverse shell channel (/dev/tcp, bash -i, nc -e)",
            "matched": m.group(0).strip(),
        }

    # 无网络出口则谈不上数据外泄
    if not _NETWORK_SINK_RE.search(cmd):
        return None

    # 2) 上传位置引用机密：@file / --post-file / --upload-file
    for am in _AT_FILE_RE.finditer(cmd):
        path = am.group(0)[1:].strip("'\"")
        if _is_sensitive_ref(path):
            return {
                "kind": "upload",
                "risk": "high",
                "hard": True,
                "reason": "sensitive file referenced as network upload payload",
                "matched": path,
            }
    for fm in _UPLOAD_FLAG_RE.finditer(cmd):
        if _is_sensitive_ref(fm.group(1)):
            return {
                "kind": "upload",
                "risk": "high",
                "hard": True,
                "reason": "sensitive file referenced as network upload payload",
                "matched": fm.group(1),
            }

    # 3) 管道/重定向喂入网络出口
    for pm in _PIPE_SRC_RE.finditer(cmd):
        if _is_sensitive_ref(pm.group(1)):
            return {
                "kind": "pipe",
                "risk": "high",
                "hard": True,
                "reason": "sensitive file piped into a network sink",
                "matched": pm.group(1),
            }
    for rm in _REDIR_SRC_RE.finditer(cmd):
        if _is_sensitive_ref(rm.group(1)):
            return {
                "kind": "redirect",
                "risk": "high",
                "hard": True,
                "reason": "sensitive file redirected into a network sink",
                "matched": rm.group(1),
            }

    # 4) scp/sftp/ftp 本地源操作数（强凭据文件路径，避免远端路径误伤）
    if _TRANSFER_CMD_RE.search(cmd):
        for tok in re.findall(r"[\w~./\-]+", cmd):
            if "@" in tok:
                continue  # 远端目标 user@host:/path
            if _SENSITIVE_FILE_RE.search(tok):
                return {
                    "kind": "transfer",
                    "risk": "high",
                    "hard": True,
                    "reason": "sensitive local file sent to a remote host",
                    "matched": tok,
                }
    return None


def _is_shell_read_only(cmd: str) -> bool:
    c = cmd.strip()
    return any(c == p or c.startswith(p + " ") for p in _SHELL_READONLY_PREFIXES)


def _path_in_working_dirs(path: str, working_dirs: list[str]) -> bool:
    if not path or not working_dirs:
        return False
    try:
        real = os.path.realpath(path)
    except OSError:
        real = path
    for wd in working_dirs:
        try:
            real_wd = os.path.realpath(wd)
        except OSError:
            real_wd = wd
        if real == real_wd or real.startswith(real_wd.rstrip("/") + "/"):
            return True
    return False


def generate_suggestions(tool_name: str, tool_input: dict) -> list[dict]:
    """从被拦调用生成放行建议（AgentScope _generate_suggestions移植）

    - shell："npm run build" → "npm run:*"（首两token前缀通配）
    - 文件："/a/b/c.py" → "/a/b/**"（目录级glob）
    - 其他：工具级空content规则
    """
    if tool_name in SHELL_TOOLS:
        cmd = _shell_command_of(tool_input).strip()
        tokens = cmd.split()
        if len(tokens) >= 2:
            content = f"{' '.join(tokens[:2])}:*"
        elif tokens:
            content = f"{tokens[0]}:*"
        else:
            content = ""
        return [
            {
                "tool_name": tool_name,
                "rule_content": content,
                "behavior": "allow",
                "source": "session",
            }
        ]
    if tool_name in FILE_TOOLS:
        path = _path_of(tool_input)
        parent = os.path.dirname(path) if path else ""
        content = f"{parent}/**" if parent else ""
        return [
            {
                "tool_name": tool_name,
                "rule_content": content,
                "behavior": "allow",
                "source": "session",
            }
        ]
    return [
        {
            "tool_name": tool_name,
            "rule_content": "",
            "behavior": "allow",
            "source": "session",
        }
    ]


# ── 引擎 ───────────────────────────────────────────────────────


def _builtin_rules() -> list[PermissionRule]:
    """由BUILTIN_*常量构造内置基线规则（引擎脱离store单独使用时也fail-closed）"""
    rules: list[PermissionRule] = []
    seq = 0
    for tool, content in BUILTIN_HARD_DENY:
        seq += 1
        rules.append(
            PermissionRule(
                tool_name=tool,
                rule_content=content,
                behavior=PermissionBehavior.DENY,
                source="builtin",
                hard=True,
                risk="high",
                rule_id=f"blt-{seq:03d}",
                seq=seq,
            )
        )
    for tool, content, risk in BUILTIN_ASK:
        seq += 1
        rules.append(
            PermissionRule(
                tool_name=tool,
                rule_content=content,
                behavior=PermissionBehavior.ASK,
                source="builtin",
                hard=False,
                risk=risk,
                rule_id=f"blt-{seq:03d}",
                seq=seq,
            )
        )
    return rules


_BEHAVIOR_WORD = {
    PermissionBehavior.ALLOW: "granted",
    PermissionBehavior.DENY: "denied",
    PermissionBehavior.ASK: "required",
}


class PermissionEngine:
    """纯规则评估引擎 — 无状态，context每次评估前由store构建

    评估顺序（kilocode findLast统一扫描 + AgentScope逐模式尾部）：
      0. hard deny — 任何模式不可覆盖（BYPASS也不行，kilocode hardRuleset）
      1. findLast统一扫描 — 跨deny/ask/allow行为类，按(session>user>builtin, seq降序)
         找第一条命中的非hard规则，其行为即判定（session层可覆盖builtin层ask）；
         EXPLORE下allow命中不放行（只读保证不可被规则授予）
      2. 只读快路径 → ALLOW（所有模式共用，AgentScope _check_read_only_fast_path）
      3. EXPLORE兜底 → DENY（非只读操作）
      4. ACCEPT_EDITS工作目录内文件编辑 → ALLOW
      5. 模式兜底（DEFAULT/ACCEPT_EDITS→ASK；BYPASS→ALLOW；DONT_ASK→DENY，均携带建议）
    """

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.ACCEPT_EDITS,
        rules: list[PermissionRule] | None = None,
        working_directories: list[str] | None = None,
    ):
        self.mode = mode
        self.rules = [r for r in (rules or []) if not r.is_deleted]
        # fail-closed：未提供builtin基线时自动补上（store已播种则不重复）
        if not any(r.source == "builtin" for r in self.rules):
            self.rules = self.rules + _builtin_rules()
        self.working_directories = working_directories or []

    # ── 内部：按行为类收集命中规则（findLast语义） ──
    def _first_matching(
        self,
        behavior: PermissionBehavior,
        tool_name: str,
        tool_input: dict,
        hard: bool | None = None,
    ) -> PermissionRule | None:
        candidates = [
            r
            for r in self.rules
            if r.behavior == behavior
            and (r.tool_name == tool_name or r.tool_name == "*")
            and (hard is None or r.hard == hard)
        ]
        # session > user > builtin；同层后添加者优先（findLast）
        candidates.sort(key=lambda r: (_SOURCE_PRIORITY.get(r.source, 0), r.seq), reverse=True)
        for rule in candidates:
            if match_rule(rule.rule_content, tool_name, tool_input):
                return rule
        return None

    def _find_last_matching(
        self,
        tool_name: str,
        tool_input: dict,
        behaviors: tuple = (
            PermissionBehavior.DENY,
            PermissionBehavior.ASK,
            PermissionBehavior.ALLOW,
        ),
    ) -> PermissionRule | None:
        """kilocode三层findLast：跨行为类统一排序，最后添加/最高层的匹配规则决定行为"""
        candidates = [
            r
            for r in self.rules
            if not r.hard
            and r.behavior in behaviors
            and (r.tool_name == tool_name or r.tool_name == "*")
        ]
        candidates.sort(key=lambda r: (_SOURCE_PRIORITY.get(r.source, 0), r.seq), reverse=True)
        for rule in candidates:
            if match_rule(rule.rule_content, tool_name, tool_input):
                return rule
        return None

    @staticmethod
    def _decision_from_rule(
        rule: PermissionRule, tool_name: str, default_message: str = ""
    ) -> PermissionDecision:
        word = _BEHAVIOR_WORD.get(rule.behavior, rule.behavior.value)
        msg = default_message or (
            f"Permission {word} for {tool_name} "
            f"(rule: {rule.rule_content or '*'}, source={rule.source})"
        )
        return PermissionDecision(
            behavior=rule.behavior,
            message=msg,
            decision_reason=f"Rule[{rule.source}]: {rule.rule_content or '(tool-level)'}",
            rule_id=rule.rule_id,
            rule_source=rule.source,
            rule_content=rule.rule_content or "",
            requires_human=rule.behavior == PermissionBehavior.ASK,
            risk=rule.risk,
        )

    def _read_only_fast_path(self, tool_name: str, tool_input: dict) -> PermissionDecision | None:
        if tool_name in READ_ONLY_TOOLS and tool_name not in MUTATING_TOOLS:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Permission granted for {tool_name} (read-only tool)",
                decision_reason="Read-only tools are auto-allowed in all modes",
                rule_source="engine",
            )
        if tool_name in SHELL_TOOLS and _is_shell_read_only(_shell_command_of(tool_input)):
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Permission granted for {tool_name} (read-only command)",
                decision_reason="Read-only shell command auto-allowed",
                rule_source="engine",
            )
        return None

    def check(self, tool_name: str, tool_input: dict | None = None) -> PermissionDecision:
        tool_input = tool_input or {}

        # Step 0a: 参数粒度数据外泄检测 —— 凭据外泄 hard deny（任何模式/规则不可覆盖）
        # SUMMARY.md P0-3 缺口：curl外发/凭据窃取类终端命令此前不归任何门禁。
        exfil = detect_exfiltration(tool_input)
        if exfil is not None:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=(
                    f"[HARD DENY] {tool_name} blocked: credential exfiltration "
                    f"detected ({exfil['kind']})"
                ),
                decision_reason=(
                    f"Exfiltration guard ({exfil['kind']}): {exfil['reason']} "
                    f"(matched: {exfil.get('matched', '')})"
                ),
                rule_id="guard-exfiltration",
                rule_source="engine",
                rule_content=exfil.get("matched", ""),
                requires_human=False,
                risk="high",
                mode=self.mode.value,
            )

        # Step 0: hard deny — 硬否决，任何模式不可覆盖（kilocode hardRuleset）
        hard = self._first_matching(PermissionBehavior.DENY, tool_name, tool_input, hard=True)
        if hard:
            d = self._decision_from_rule(hard, tool_name)
            d.message = f"[HARD DENY] {tool_name} blocked by protected rule: {hard.rule_content}"
            d.decision_reason = (
                f"Hard rule (bypass-immune, source={hard.source}): {hard.rule_content}"
            )
            d.mode = self.mode.value
            return d

        # Step 1: kilocode findLast统一扫描 — 跨行为类最高层/最后添加的匹配规则决定行为
        matched = self._find_last_matching(tool_name, tool_input)
        if matched is not None:
            if matched.behavior == PermissionBehavior.DENY:
                d = self._decision_from_rule(matched, tool_name)
                d.mode = self.mode.value
                return d
            if matched.behavior == PermissionBehavior.ASK:
                if self.mode == PermissionMode.DONT_ASK:
                    # DONT_ASK不变式：绝不返回ASK — 转DENY并保留provenance与建议
                    d = PermissionDecision(
                        behavior=PermissionBehavior.DENY,
                        message=f"Permission denied for {tool_name} (dont_ask: ASK→DENY, user unavailable)",
                        decision_reason=(
                            f"DONT_ASK converted ASK to DENY. "
                            f"Original rule[{matched.source}]: {matched.rule_content}"
                        ),
                        rule_id=matched.rule_id,
                        rule_source=matched.source,
                        rule_content=matched.rule_content or "",
                        risk=matched.risk,
                        mode=self.mode.value,
                    )
                    d.suggested_rules = generate_suggestions(tool_name, tool_input)
                    return d
                d = self._decision_from_rule(matched, tool_name)
                d.decision_id = uuid.uuid4().hex[:16]
                d.mode = self.mode.value
                d.suggested_rules = generate_suggestions(tool_name, tool_input)
                return d
            # matched.behavior == ALLOW
            # EXPLORE只读保证不可被allow规则授予（AgentScope规格）→ 继续往下走
            if self.mode != PermissionMode.EXPLORE:
                d = self._decision_from_rule(matched, tool_name)
                d.mode = self.mode.value
                return d

        # Step 2: 只读快路径（所有模式共用，AgentScope _check_read_only_fast_path）
        ro = self._read_only_fast_path(tool_name, tool_input)
        if ro:
            ro.mode = self.mode.value
            return ro

        # Step 3: EXPLORE — 非只读操作一律DENY
        if self.mode == PermissionMode.EXPLORE:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Permission denied for {tool_name} (explore mode is read-only)",
                decision_reason="Explore mode does not allow modifications; allow rules cannot grant it",
                mode=self.mode.value,
            )

        # Step 4: ACCEPT_EDITS — 工作目录内的文件编辑自动放行
        if self.mode == PermissionMode.ACCEPT_EDITS and tool_name in ("write_file", "patch"):
            path = _path_of(tool_input)
            if _path_in_working_dirs(path, self.working_directories):
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=f"Permission granted for {tool_name} (edit within working directory)",
                    decision_reason=f"ACCEPT_EDITS auto-allow: {path} in working dirs",
                    rule_source="engine",
                    mode=self.mode.value,
                )

        # Step 5: 模式兜底
        mode = self.mode
        if mode in (PermissionMode.DEFAULT, PermissionMode.ACCEPT_EDITS):
            d = PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Permission required for {tool_name}",
                decision_reason=f"Mode {mode.value}: no rule matched, human confirmation required",
                requires_human=True,
                risk="high" if tool_name in MUTATING_TOOLS else "medium",
                mode=mode.value,
            )
            d.decision_id = uuid.uuid4().hex[:16]
            d.suggested_rules = generate_suggestions(tool_name, tool_input)
            return d
        if mode == PermissionMode.BYPASS:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Permission granted for {tool_name} (bypass mode)",
                decision_reason="Bypass mode allows all operations not covered by deny/hard rules",
                mode=mode.value,
            )
        # DONT_ASK兜底 — 同样携带建议（告诉用户加什么规则能放行）
        d = PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message=f"Permission denied for {tool_name} (dont_ask: user not available)",
            decision_reason="Mode dont_ask: unmatched operations are denied when no user can answer",
            mode=mode.value,
        )
        d.suggested_rules = generate_suggestions(tool_name, tool_input)
        return d


# ── 持久化（mem0审计表模式） ─────────────────────────────────────


class PermissionStore:
    """SQLite持久化：规则表 + 决策审计表 + 模式表

    - rules：rule_id/工具/内容/行为/来源/hard/risk/seq/is_deleted
    - decision_audit：每次check落一条（behavior/reason/rule provenance），
      ASK决策被人工approve/deny后回写outcome（TradingAgents"决策延迟回填"同型）
    - meta：当前模式
    """

    def __init__(self, db_path: str = ""):
        if not db_path:
            base = Path.home() / ".hermes" / "opensoul" / "immune"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "permission.db")
        self.db_path = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS perm_rules (
                    rule_id TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    rule_content TEXT,
                    behavior TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'user',
                    hard INTEGER NOT NULL DEFAULT 0,
                    risk TEXT NOT NULL DEFAULT 'medium',
                    seq INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    is_deleted INTEGER NOT NULL DEFAULT 0
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS perm_decision_audit (
                    decision_id TEXT PRIMARY KEY,
                    session_id TEXT DEFAULT '',
                    tool_name TEXT NOT NULL,
                    tool_input_digest TEXT DEFAULT '',
                    tool_input_preview TEXT DEFAULT '',
                    behavior TEXT NOT NULL,
                    decision_reason TEXT DEFAULT '',
                    message TEXT DEFAULT '',
                    rule_id TEXT DEFAULT '',
                    rule_source TEXT DEFAULT '',
                    rule_content TEXT DEFAULT '',
                    requires_human INTEGER DEFAULT 0,
                    risk TEXT DEFAULT 'medium',
                    mode TEXT DEFAULT '',
                    outcome TEXT DEFAULT '',
                    outcome_comment TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    outcome_at REAL
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS perm_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_perm_audit_tool ON perm_decision_audit(tool_name, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_perm_audit_behavior ON perm_decision_audit(behavior, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_perm_rules_tool ON perm_rules(tool_name, is_deleted)"
            )
        self._seed_builtin_rules()

    def _seed_builtin_rules(self):
        """首次启动播种内置基线策略（幂等：已有builtin规则则跳过）"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM perm_rules WHERE source='builtin'"
            ).fetchone()
            if row["n"] > 0:
                return
            now = time.time()
            seq = 0
            for tool, content in BUILTIN_HARD_DENY:
                seq += 1
                conn.execute(
                    "INSERT INTO perm_rules (rule_id, tool_name, rule_content, behavior, source, hard, risk, seq, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex[:12], tool, content, "deny", "builtin", 1, "high", seq, now),
                )
            for item in BUILTIN_ASK:
                tool, content, risk = item
                seq += 1
                conn.execute(
                    "INSERT INTO perm_rules (rule_id, tool_name, rule_content, behavior, source, hard, risk, seq, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex[:12], tool, content, "ask", "builtin", 0, risk, seq, now),
                )
            conn.execute(
                "INSERT OR IGNORE INTO perm_meta (key, value) VALUES ('mode', ?)",
                (PermissionMode.ACCEPT_EDITS.value,),
            )
        logger.info("permission engine: seeded %d builtin rules", seq)

    # ── 规则CRUD ──
    def add_rule(
        self,
        tool_name: str,
        rule_content: str | None,
        behavior: str,
        source: str = "user",
        hard: bool = False,
        risk: str = "medium",
    ) -> dict:
        rule_id = uuid.uuid4().hex[:12]
        with self._conn() as conn:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM perm_rules").fetchone()
            seq = row["m"] + 1
            conn.execute(
                "INSERT INTO perm_rules (rule_id, tool_name, rule_content, behavior, source, hard, risk, seq, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    rule_id,
                    tool_name,
                    rule_content,
                    behavior,
                    source,
                    1 if hard else 0,
                    risk,
                    seq,
                    time.time(),
                ),
            )
        return {"rule_id": rule_id, "seq": seq}

    def delete_rule(self, rule_id: str) -> bool:
        """软删（保留审计轨迹；hard规则同样可由人工删除 — 但删除动作被审计）"""
        with self._conn() as conn:
            cur = conn.execute("UPDATE perm_rules SET is_deleted=1 WHERE rule_id=?", (rule_id,))
            return cur.rowcount > 0

    def list_rules(
        self, include_deleted: bool = False, behavior: str = "", tool_name: str = ""
    ) -> list[dict]:
        q = "SELECT * FROM perm_rules WHERE 1=1"
        args: list = []
        if not include_deleted:
            q += " AND is_deleted=0"
        if behavior:
            q += " AND behavior=?"
            args.append(behavior)
        if tool_name:
            q += " AND tool_name=?"
            args.append(tool_name)
        q += " ORDER BY seq"
        with self._conn() as conn:
            rows = conn.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["hard"] = bool(d["hard"])
            d["is_deleted"] = bool(d["is_deleted"])
            out.append(d)
        return out

    def get_rules(self) -> list[PermissionRule]:
        rules = []
        for d in self.list_rules():
            rules.append(
                PermissionRule(
                    tool_name=d["tool_name"],
                    rule_content=d["rule_content"],
                    behavior=PermissionBehavior(d["behavior"]),
                    source=d["source"],
                    hard=d["hard"],
                    risk=d["risk"],
                    rule_id=d["rule_id"],
                    seq=d["seq"],
                    created_at=d["created_at"],
                )
            )
        return rules

    # ── 模式 ──
    def get_mode(self) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM perm_meta WHERE key='mode'").fetchone()
        return row["value"] if row else PermissionMode.ACCEPT_EDITS.value

    def set_mode(self, mode: str) -> None:
        PermissionMode(mode)  # 校验
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO perm_meta (key, value) VALUES ('mode', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (mode,),
            )

    # ── 决策审计 ──
    def log_decision(
        self,
        decision: PermissionDecision,
        session_id: str = "",
        tool_name: str = "",
        tool_input: dict | None = None,
    ) -> None:
        tool_input = tool_input or {}
        try:
            preview = json.dumps(tool_input, ensure_ascii=False)[:500]
            digest = hashlib.sha256(
                json.dumps(tool_input, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()[:16]
        except (TypeError, ValueError):
            preview, digest = str(tool_input)[:500], ""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO perm_decision_audit "
                "(decision_id, session_id, tool_name, tool_input_digest, tool_input_preview,"
                " behavior, decision_reason, message, rule_id, rule_source, rule_content,"
                " requires_human, risk, mode, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision.decision_id or uuid.uuid4().hex[:16],
                    session_id,
                    tool_name,
                    digest,
                    preview,
                    decision.behavior.value,
                    decision.decision_reason,
                    decision.message,
                    decision.rule_id,
                    decision.rule_source,
                    decision.rule_content,
                    1 if decision.requires_human else 0,
                    decision.risk,
                    decision.mode,
                    time.time(),
                ),
            )

    def record_outcome(self, decision_id: str, outcome: str, comment: str = "") -> bool:
        """人工审批结果回写（approved/denied/timeout）"""
        if outcome not in ("approved", "denied", "timeout"):
            return False
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE perm_decision_audit SET outcome=?, outcome_comment=?, outcome_at=? "
                "WHERE decision_id=?",
                (outcome, comment, time.time(), decision_id),
            )
            return cur.rowcount > 0

    def audit_query(
        self, limit: int = 50, behavior: str = "", tool_name: str = "", outcome: str = ""
    ) -> list[dict]:
        q = "SELECT * FROM perm_decision_audit WHERE 1=1"
        args: list = []
        if behavior:
            q += " AND behavior=?"
            args.append(behavior)
        if tool_name:
            q += " AND tool_name=?"
            args.append(tool_name)
        if outcome:
            q += " AND outcome=?"
            args.append(outcome)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 1000)))
        with self._conn() as conn:
            rows = conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM perm_decision_audit").fetchone()["n"]
            by_b = {}
            for r in conn.execute(
                "SELECT behavior, COUNT(*) AS n FROM perm_decision_audit GROUP BY behavior"
            ):
                by_b[r["behavior"]] = r["n"]
            denied = conn.execute(
                "SELECT COUNT(*) AS n FROM perm_decision_audit WHERE behavior='deny'"
            ).fetchone()["n"]
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM perm_decision_audit WHERE behavior='ask' AND outcome=''"
            ).fetchone()["n"]
            rules_n = conn.execute(
                "SELECT COUNT(*) AS n FROM perm_rules WHERE is_deleted=0"
            ).fetchone()["n"]
            hard_n = conn.execute(
                "SELECT COUNT(*) AS n FROM perm_rules WHERE is_deleted=0 AND hard=1"
            ).fetchone()["n"]
        return {
            "decisions_total": total,
            "by_behavior": by_b,
            "denied": denied,
            "pending_approvals": pending,
            "active_rules": rules_n,
            "hard_rules": hard_n,
            "mode": self.get_mode(),
        }


# ── 服务门面（API与调用方的唯一入口） ─────────────────────────────


class PermissionService:
    """store + engine 门面：check() = 构建context → 评估 → 落审计"""

    def __init__(self, store: PermissionStore | None = None, db_path: str = ""):
        self.store = store or PermissionStore(db_path)

    def build_engine(self, working_dir: str = "") -> PermissionEngine:
        mode = PermissionMode(self.store.get_mode())
        wds = [working_dir] if working_dir else []
        return PermissionEngine(mode=mode, rules=self.store.get_rules(), working_directories=wds)

    def check(
        self,
        tool_name: str,
        tool_input: dict | None = None,
        session_id: str = "",
        working_dir: str = "",
    ) -> PermissionDecision:
        engine = self.build_engine(working_dir=working_dir)
        decision = engine.check(tool_name, tool_input or {})
        try:
            self.store.log_decision(
                decision, session_id=session_id, tool_name=tool_name, tool_input=tool_input or {}
            )
        except Exception as e:  # 审计失败不阻塞判定
            logger.warning("permission audit log failed: %s", e)
        return decision
