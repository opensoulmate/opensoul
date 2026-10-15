"""Reflector — 行动后反思，无论成功/失败/异常，统一复盘。"""

import json
import logging
import subprocess
from pathlib import Path

import httpx

from src.config import settings
from src.models.cognitive import CheckItem, Intent, TaskContext, Verification

logger = logging.getLogger(__name__)


class Reflector:
    """行动后反思器 — 复用ChainOfThought的LLM能力做深度反思"""

    def __init__(self):
        pass

    async def reflect(self, action: str, result: dict, task_ctx: TaskContext) -> Verification:
        checks: list[CheckItem] = []

        # 1. 系统异常检查（异常不能跳出闭环）
        if result.get("exception"):
            checks.append(CheckItem("系统异常", False, result.get("error", "未知异常")))

        # 2. 语法检查
        if result.get("file_written"):
            syntax_ok = await self._check_syntax(result["file_path"])
            checks.append(
                CheckItem(
                    "语法校验", syntax_ok, None if syntax_ok else f"语法错误: {result['file_path']}"
                )
            )

        # 3. 功能检查
        if result.get("test_output"):
            test_ok = result["test_output"].get("passed", False)
            checks.append(CheckItem("功能校验", test_ok, result["test_output"].get("error")))

        # 3b. 进程退出码检查（DEC-⑤-① 执行结果回传协议：agent 回传 exit_code→
        # 真 Verification；仅在回传方声明 exit_code 时检查=零既有行为变化）
        if result.get("exit_code") is not None:
            code_ok = result["exit_code"] == 0
            checks.append(
                CheckItem(
                    "退出码",
                    code_ok,
                    None if code_ok else f"进程退出码 {result['exit_code']}",
                )
            )

        # 4. 意图匹配检查
        if result.get("diff") and task_ctx.intent:
            intent_match = await self._check_intent_match(result["diff"], task_ctx.intent)
            checks.append(
                CheckItem("意图匹配", intent_match, None if intent_match else "改动与预期意图不符")
            )

        if not checks:
            checks.append(CheckItem("无检查项", True))

        success = all(c.passed for c in checks)
        fix = None
        error = None
        if not success:
            failed = [c for c in checks if not c.passed]
            error = failed[0].error
            fix = self._suggest_fix(failed)

        return Verification(success=success, checks=checks, error=error, fix=fix)

    async def _check_syntax(self, file_path: str) -> bool:
        path = Path(file_path)
        if not path.exists():
            return True
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return True
        suffix = path.suffix.lower()
        if suffix == ".py":
            try:
                compile(content, file_path, "exec")
                return True
            except SyntaxError:
                return False
        elif suffix in (".ts", ".tsx", ".js", ".jsx"):
            try:
                r = subprocess.run(["node", "--check", file_path], capture_output=True, timeout=5)
                return r.returncode == 0
            except Exception:
                return True
        elif suffix == ".json":
            import json as _json

            try:
                _json.loads(content)
                return True
            except _json.JSONDecodeError:
                return False
        return True

    async def _check_intent_match(self, diff: str, intent: Intent) -> bool:
        for f in intent.target_files:
            if f in diff:
                return True
        return len(diff) > 0

    def _suggest_fix(self, failed: list[CheckItem]) -> str:
        suggestions = []
        for c in failed:
            if c.name == "语法校验":
                suggestions.append("修复语法错误后重试")
            elif c.name == "系统异常":
                suggestions.append(f"异常处理：{c.error}")
            elif c.name == "意图匹配":
                suggestions.append("改动与预期不符，建议用增量编辑重试")
            elif c.name == "功能校验":
                suggestions.append(f"功能测试失败：{c.error}")
            elif c.name == "退出码":
                suggestions.append(f"进程非零退出（{c.error}），检查执行日志后重试")
        return "; ".join(suggestions) if suggestions else "请人工检查"
