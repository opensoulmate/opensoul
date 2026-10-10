"""Proactive Engine — 主动行为引擎。

Agent不等指令，主动观察、判断、行动：
- 监听文件变化 → 自动备份/检查
- 监听错误日志 → 自动诊断
- 定期学习 → 提取模式
- 发现问题 → 主动报告
"""

import asyncio
import inspect
import json
import logging
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _default_events_db() -> str:
    """主动行为事件库默认落 data/proactive.db（与job_queue.db/ai_groups.db同款小库模式）。"""
    return str(Path(__file__).resolve().parent.parent.parent / "data" / "proactive.db")


class ProactiveEngine:
    """主动行为引擎 — 观察→判断→行动循环

    A3（2026-10-10）：从"只存在"补到"真调度"——每个watcher带独立interval（到期才跑，
    不是每tick全量跑），每次执行留痕proactive_events（ok/耗时/result/error，可观测性），
    run_watcher手动触发复用同一条执行路径（API面板与心跳共用，不产生第二份状态）。
    """

    def __init__(
        self,
        db_pool=None,
        tenant_id: str = "default",
        agent_id: str = "default",
        db_path: str | None = None,
    ):
        self.db = db_pool
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        # None=默认data/proactive.db；""=纯内存不留痕（测试隔离用）
        self.db_path = _default_events_db() if db_path is None else db_path
        self._watchers: dict[str, dict] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        self._heartbeat_s = 60.0
        self._events_memory: list[dict] = []  # 无DB时的内存环形留痕（get_status仍可读）
        self._db_ready = False

    def register_watcher(self, name: str, callback: Callable, interval_seconds: float = 60.0):
        """注册一个观察者（interval=执行间隔，心跳tick只跑到期的watcher）"""
        self._watchers[name] = {
            "callback": callback,
            "interval_seconds": float(interval_seconds),
            "last_run_at": 0.0,
            "last_ok": None,
            "last_error": "",
            "last_result": {},
            "last_duration_ms": 0.0,
            "run_count": 0,
            "error_count": 0,
        }

    async def start(self, interval_seconds: float = 60):
        """启动主动观察循环（心跳：每tick只执行到期watcher）"""
        self._running = True
        self._heartbeat_s = float(interval_seconds)
        self._task = asyncio.create_task(self._observe_loop(interval_seconds))
        logger.info(
            "主动行为引擎启动, heartbeat=%ds, watchers=%s",
            interval_seconds,
            {n: int(w["interval_seconds"]) for n, w in self._watchers.items()},
        )

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _observe_loop(self, interval: float):
        while self._running:
            try:
                now = time.time()
                for name, w in list(self._watchers.items()):
                    if now - w["last_run_at"] >= w["interval_seconds"]:
                        await self.run_watcher(name)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("观察循环异常: %s", e)
            await asyncio.sleep(interval)

    async def run_watcher(self, name: str) -> dict:
        """执行单个watcher并留痕（心跳到期调用与API手动触发共用此路径）。"""
        w = self._watchers.get(name)
        if w is None:
            raise KeyError(f"unknown watcher: {name}")
        started = time.time()
        started_at = datetime.now(UTC).isoformat()
        ok, error = True, ""
        try:
            res = w["callback"]()
            if inspect.isawaitable(res):
                res = await res
            result = res if isinstance(res, dict) else {"result": res}
        except Exception as e:  # 单watcher失败绝不掀翻心跳循环
            ok, error, result = False, f"{type(e).__name__}: {e}", {}
            logger.warning("watcher %s 执行失败: %s", name, error)
        duration_ms = (time.time() - started) * 1000
        w["last_run_at"] = time.time()
        w["last_ok"] = ok
        w["last_error"] = error
        w["last_result"] = result
        w["last_duration_ms"] = round(duration_ms, 1)
        w["run_count"] += 1
        if not ok:
            w["error_count"] += 1
        self._record_event(
            {
                "id": str(uuid.uuid4()),
                "watcher": name,
                "ok": ok,
                "started_at": started_at,
                "finished_at": datetime.now(UTC).isoformat(),
                "duration_ms": round(duration_ms, 1),
                "result": result,
                "error": error,
            }
        )
        logger.info(
            "[proactive] %s %s in %.0fms%s",
            name,
            "ok" if ok else "FAILED",
            duration_ms,
            f" error={error}" if error else "",
        )
        return {
            "watcher": name,
            "ok": ok,
            "duration_ms": round(duration_ms, 1),
            "result": result,
            "error": error,
        }

    # ── 事件留痕（可观测性：每次主动行为都可回读，不留黑箱） ──────────

    def _ensure_db(self):
        if self._db_ready or not self.db_path:
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS proactive_events (
                    id TEXT PRIMARY KEY, watcher TEXT NOT NULL,
                    ok INTEGER NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL, duration_ms REAL NOT NULL,
                    result TEXT DEFAULT '{}', error TEXT DEFAULT '')"""
            )
        self._db_ready = True

    def _record_event(self, event: dict):
        self._events_memory.append(event)
        self._events_memory = self._events_memory[-200:]
        if not self.db_path:
            return
        try:
            self._ensure_db()
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO proactive_events (id, watcher, ok, started_at, finished_at, duration_ms, result, error)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        str(event["id"]),
                        event["watcher"],
                        1 if event["ok"] else 0,
                        event["started_at"],
                        event["finished_at"],
                        event["duration_ms"],
                        json.dumps(event["result"], ensure_ascii=False),
                        event["error"],
                    ),
                )
        except Exception as e:  # 留痕失败绝不反噬行为执行
            logger.warning("proactive event persist failed: %s", e)

    def recent_events(self, limit: int = 50) -> list[dict]:
        if not self.db_path:
            return list(reversed(self._events_memory[-limit:]))
        try:
            self._ensure_db()
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM proactive_events ORDER BY started_at DESC LIMIT ?", (int(limit),)
                ).fetchall()
            events = []
            for r in rows:
                d = dict(r)
                try:
                    d["result"] = json.loads(d.get("result") or "{}")
                except Exception:
                    d["result"] = {}
                d["ok"] = bool(d["ok"])
                events.append(d)
            return events
        except Exception as e:
            logger.warning("proactive events read failed: %s", e)
            return list(reversed(self._events_memory[-limit:]))

    async def get_status(self) -> dict:
        return {
            "running": self._running,
            "heartbeat_seconds": self._heartbeat_s,
            "events_db": self.db_path or "(memory-only)",
            "watchers": [
                {
                    "name": name,
                    "interval_seconds": w["interval_seconds"],
                    "last_run_at": (
                        datetime.fromtimestamp(w["last_run_at"], UTC).isoformat()
                        if w["last_run_at"]
                        else None
                    ),
                    "last_ok": w["last_ok"],
                    "last_error": w["last_error"],
                    "last_duration_ms": w["last_duration_ms"],
                    "run_count": w["run_count"],
                    "error_count": w["error_count"],
                    "last_result": w["last_result"],
                }
                for name, w in self._watchers.items()
            ],
        }


# 进程内单例：main.py lifespan挂载启动，api/proactive.py面板读同一实例
proactive_engine = ProactiveEngine()
