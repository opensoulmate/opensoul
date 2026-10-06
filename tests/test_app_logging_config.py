"""configure_app_logging 行为钉 + 接线钉（2026-10-06 日志可观测性轮）。

背景：app级INFO日志被无handler的root logger整体丢弃（journal实测
"Auto-bootstrap result"从未出现，WARNING经lastResort只剩裸消息）。
本族钉住：①函数行为（handler/级别/降噪/幂等）②main.py模块级接线
③干净解释器下import src.main真实触发配置（运行时接线非死代码）。
"""

import ast
import io
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from src.main import configure_app_logging

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def restore_root_logger():
    """保存/恢复root logger全局状态——配置函数动的是进程级单例。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_noisy = {
        name: logging.getLogger(name).level for name in ("httpx", "httpcore", "urllib3", "openai")
    }
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for name, level in saved_noisy.items():
        logging.getLogger(name).setLevel(level)


class TestConfigureAppLogging:
    def test_adds_handler_and_enables_info(self, restore_root_logger):
        root = logging.getLogger()
        root.handlers[:] = []
        root.setLevel(logging.WARNING)
        configure_app_logging()
        assert len(root.handlers) == 1
        assert root.level <= logging.INFO

    def test_idempotent_single_handler(self, restore_root_logger):
        root = logging.getLogger()
        root.handlers[:] = []
        root.setLevel(logging.WARNING)
        configure_app_logging()
        configure_app_logging()
        assert len(root.handlers) == 1

    def test_existing_handler_not_replaced_but_level_fixed(self, restore_root_logger):
        """已有handler时幂等不重复挂，但root level>INFO仍要放行INFO。"""
        root = logging.getLogger()
        sentinel = logging.StreamHandler(io.StringIO())
        root.handlers[:] = [sentinel]
        root.setLevel(logging.WARNING)
        configure_app_logging()
        assert root.handlers == [sentinel]
        assert root.level <= logging.INFO

    def test_info_record_reaches_handler(self, restore_root_logger):
        """行为钉：INFO记录经root handler真实输出（此前被整体丢弃）。"""
        root = logging.getLogger()
        root.handlers[:] = []
        root.setLevel(logging.WARNING)
        configure_app_logging()
        stream = io.StringIO()
        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        handler.setStream(stream)
        logging.getLogger("opensoul.test.app_logging").info("wired-visible-%s", "42")
        out = stream.getvalue()
        assert "wired-visible-42" in out
        assert "INFO" in out and "opensoul.test.app_logging" in out

    def test_noisy_third_party_loggers_damped(self, restore_root_logger):
        """httpx等每请求一条INFO会淹没journal（探针每分钟20+请求）→压WARNING。"""
        root = logging.getLogger()
        root.handlers[:] = []
        root.setLevel(logging.WARNING)
        configure_app_logging()
        for name in ("httpx", "httpcore", "urllib3", "openai"):
            assert logging.getLogger(name).level >= logging.WARNING, name


class TestRuntimeWiring:
    def test_main_module_level_call_site(self):
        """静态接线钉：src/main.py模块级真实调用configure_app_logging()。"""
        tree = ast.parse((_REPO_ROOT / "src" / "main.py").read_text(encoding="utf-8"))
        calls = []
        for node in tree.body:  # 模块级语句
            targets = []
            if isinstance(node, ast.If):
                targets = node.body
            elif isinstance(node, ast.Expr):
                targets = [node]
            for stmt in targets:
                for sub in ast.walk(stmt):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Name)
                        and sub.func.id == "configure_app_logging"
                    ):
                        calls.append(sub)
        assert calls, "src/main.py 模块级缺少 configure_app_logging() 调用（脱线）"

    def test_clean_interpreter_import_configures_root(self):
        """运行时接线钉（最强证据）：干净解释器import src.main后root handler就位、
        INFO放行、httpx降噪——证明模块级调用在真实启动路径（uvicorn src.main:app）
        会触发，而非死代码。"""
        code = (
            "import logging\n"
            "import src.main\n"
            "root = logging.getLogger()\n"
            "assert root.handlers, 'root has no handler after import src.main'\n"
            "assert root.level <= logging.INFO, root.level\n"
            "assert logging.getLogger('httpx').level >= logging.WARNING\n"
            "print('CLEAN_IMPORT_OK')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert "CLEAN_IMPORT_OK" in proc.stdout
