"""MARROW-① 模块级 scheduler.start() 收口——调度线程生命周期归服务 lifespan。

修前（fresh import 硬证）：import src.api.marrow 即拉起 daemon 线程
（scheduler._running=True、Thread(_loop) alive），而生产 schedules.json 有真
排程（Daily Data Backup）——import 副作用=生产写面（真建备份 tar），测试面
被迫逐 fixture 手工 stop（conftest marrow_client）。

本文件锁死边界：
1. fresh import（真子进程）零线程副作用（修前 running=True/Thread=控制组反证）
2. BackupScheduler.start/stop 原语：起停+幂等（线程生命周期的真执行面）
3. run_due_backups 不依赖后台线程（手动 /schedules/run 语义零变化）
4. src/main.py lifespan 接线普查（AST：yield 前 start、yield 后 stop——
   调度线程随服务起停，修前模块级 start 不在 lifespan=断言反证）
"""

import ast
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fresh_import_probe() -> str:
    """真子进程 fresh import src.api.marrow，回读 scheduler 线程状态。"""
    code = "import src.api.marrow as m\ns = m.scheduler\nprint(s._running, s._thread)\n"
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _calls_in(stmts, obj: str, attr: str) -> list:
    """收集 stmts 内 obj.attr(...) 调用节点（递归，含嵌套函数体）。"""
    hits = []
    for st in stmts:
        for node in ast.walk(st):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == obj
            ):
                hits.append(node)
    return hits


class TestImportPurity:
    def test_fresh_import_starts_no_scheduler_thread(self):
        # 修前：fresh import 输出 "True <Thread(... started daemon ...)>"=控制组反证
        out = _fresh_import_probe()
        assert out == "False None", f"import 即起线程未收口: {out!r}"

    def test_module_source_has_no_import_time_start(self):
        # 结构面：模块顶层（import 路径）禁 scheduler.start() 调用
        tree = ast.parse((REPO_ROOT / "src" / "api" / "marrow.py").read_text())
        offenders = []
        for st in tree.body:
            for node in ast.walk(st):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "start"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "scheduler"
                ):
                    offenders.append(node)
        assert not offenders, "模块 import 路径上存在 scheduler.start() 调用"


class TestSchedulerPrimitive:
    def test_start_stop_and_idempotent_start(self, tmp_path):
        from src.marrow.backup import BackupManager, BackupScheduler

        mgr = BackupManager(backup_dir=tmp_path / "backups")
        sch = BackupScheduler(mgr)
        assert sch._running is False
        assert sch._thread is None

        sch.start()
        assert sch._running is True
        t1 = sch._thread
        assert t1 is not None and t1.is_alive()

        sch.start()  # 幂等：不换线程不双跑
        assert sch._thread is t1

        sch.stop()
        assert sch._running is False

    def test_run_due_backups_without_background_thread(self, tmp_path):
        # 手动触发语义零变化：/api/marrow/schedules/run 直调 run_due_backups，
        # 与后台线程无关——收口（不自动起线程）不许把手动通道一起收死
        from src.marrow.backup import BackupManager, BackupScheduler

        backup_dir = tmp_path / "backups"
        src_dir = tmp_path / "data"
        src_dir.mkdir()
        (src_dir / "f.txt").write_text("x")

        mgr = BackupManager(backup_dir=backup_dir)
        sch = BackupScheduler(mgr)
        assert sch._running is False  # 无后台线程

        sb = sch.create_schedule(
            name="manual-t",
            source_dirs=[str(src_dir)],
            interval="daily",
            description="",
            tags=["test"],
        )
        sch._schedules[sb.schedule_id].next_run_at = time.time() - 1  # 置为到期
        results = sch.run_due_backups()

        assert len(results) == 1
        assert results[0]["success"] is True
        assert results[0]["schedule_id"] == sb.schedule_id
        assert (backup_dir / f"{results[0]['backup_id']}.tar.gz").exists() or any(
            backup_dir.glob(f"{results[0]['backup_id']}*")
        ), results


class TestLifespanWiring:
    def test_lifespan_starts_and_stops_scheduler(self):
        # 接线普查（AST 真源，禁只信 grep）：yield 前 scheduler.start()、
        # yield 后 scheduler.stop()——修前两者都不在 lifespan=断言反证
        tree = ast.parse((REPO_ROOT / "src" / "main.py").read_text())
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"
        )
        yield_idx = next(
            i
            for i, st in enumerate(fn.body)
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Yield)
        )
        before = fn.body[:yield_idx]
        after = fn.body[yield_idx + 1 :]

        assert _calls_in(before, "scheduler", "start"), "lifespan 启动段缺 scheduler.start()"
        assert not _calls_in(after, "scheduler", "start"), (
            "scheduler.start() 不许出现在 shutdown 段"
        )
        assert _calls_in(after, "scheduler", "stop"), "lifespan 关闭段缺 scheduler.stop()"
        assert not _calls_in(before, "scheduler", "stop"), "scheduler.stop() 不许出现在启动段"
