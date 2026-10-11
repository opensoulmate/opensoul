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

from src.nest.isolation import IsolationEngine, ResourceType
from src.nest.tenant import tenant_namespace

logger = logging.getLogger(__name__)

# F3b-③c-2②a（2026-10-12）数据面租户缺省：心跳调度（系统行为无身份上下文）与
# 匿名调用归 'default' 租户——与生产数据分布（proactive.db 存量行）迁移回填一致。
DEFAULT_TENANT = "default"


class ProactiveEventAccessDenied(Exception):
    """跨租户 proactive 事件访问被 IsolationEngine 拒绝（API 层映射 403）。

    verdict=check_access 原始回包（allowed/reason/namespaced_id），审计留痕在
    IsolationEngine 审计日志（data/nest.db），此处只携带原因供 API 如实报。
    """

    def __init__(self, reason: str, verdict: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.verdict = verdict or {}


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
        # F3b-③c-2②a：数据面咨询 IsolationEngine（懒构造——首个 gate 调用才建
        # NestStore，收集期零生产库接触）。默认实例即真审计，无"未接线"模式。
        self._isolation: IsolationEngine | None = None

    @property
    def isolation(self) -> IsolationEngine:
        """隔离引擎（check_access 真执行点；审计日志落 NestStore=data/nest.db）。"""
        if self._isolation is None:
            self._isolation = IsolationEngine()
        return self._isolation

    def gate_bulk(self, tenant_id: str, action: str) -> None:
        """bulk 操作审计留痕（F3b-③c-2②a）：执行隔离由 WHERE tenant_id=? 承担，
        引擎侧记 list 审计（audit_access="Log all access attempts"）。

        本表无 by-ID 读写面（api/proactive.py 仅 status/events/run 三口），故无
        by-ID 闸门；跨租户拒绝面=查询过滤（B 恒读不到 A 的行）。
        """
        self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.PROACTIVE_EVENT,
            resource_id=self.isolation.namespaced_id(tenant_namespace(tenant_id), "*"),
            action=action,
        )

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

    async def run_watcher(self, name: str, tenant_id: str | None = None) -> dict:
        """执行单个watcher并留痕（心跳到期调用与API手动触发共用此路径）。

        F3b-③c-2②a：事件行归属租户=显式 tenant_id（API 手动触发=调用方
        bind_identity 派生租户）或引擎租户（心跳调度=系统行为），缺失/空白→
        'default' 兜底（键派生故障绝不吃掉落库，③b-2 同款旁路纪律）。watcher
        执行本体是全局系统行为（回调无租户参数），本拍隔离的是事件留痕面。
        """
        w = self._watchers.get(name)
        if w is None:
            raise KeyError(f"unknown watcher: {name}")
        row_tenant = (
            str(tenant_id or "").strip() or str(self.tenant_id or "").strip() or DEFAULT_TENANT
        )
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
                "tenant_id": row_tenant,
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
                    result TEXT DEFAULT '{}', error TEXT DEFAULT '',
                    tenant_id TEXT DEFAULT 'default')"""
            )
            # F3b-③c-2②a：DDL+ALTER 双轨幂等——老库（迁移前形状无 tenant_id）补列+
            # 存量行回填 'default'（与生产数据分布一致），缺列才 ALTER（幂等）。
            # 租户索引必须在补列之后建（老库形态下先建索引会 no such column，③c-2① 坑）。
            cols = {r[1] for r in conn.execute("PRAGMA table_info(proactive_events)").fetchall()}
            if "tenant_id" not in cols:
                conn.execute(
                    "ALTER TABLE proactive_events ADD COLUMN tenant_id TEXT DEFAULT 'default'"
                )
                conn.execute(
                    "UPDATE proactive_events SET tenant_id = 'default' WHERE tenant_id IS NULL"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pe_tenant ON proactive_events(tenant_id, started_at DESC)"
            )
        self._db_ready = True

    def _record_event(self, event: dict):
        # F3b-③c-2②a：行归属租户=事件自带键，缺失/空白→引擎租户→'default' 兜底
        # （键派生故障绝不吃掉落库，③b-2/③c-2① 同款旁路纪律）。
        tenant_val = (
            str(event.get("tenant_id") or "").strip()
            or str(self.tenant_id or "").strip()
            or DEFAULT_TENANT
        )
        event = {**event, "tenant_id": tenant_val}
        self._events_memory.append(event)
        self._events_memory = self._events_memory[-200:]
        if not self.db_path:
            return
        try:
            self._ensure_db()
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO proactive_events (id, watcher, ok, started_at, finished_at, duration_ms, result, error, tenant_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        str(event["id"]),
                        event["watcher"],
                        1 if event["ok"] else 0,
                        event["started_at"],
                        event["finished_at"],
                        event["duration_ms"],
                        json.dumps(event["result"], ensure_ascii=False),
                        event["error"],
                        tenant_val,
                    ),
                )
        except Exception as e:  # 留痕失败绝不反噬行为执行
            logger.warning("proactive event persist failed: %s", e)

    def recent_events(self, limit: int = 50, tenant_id: str | None = None) -> list[dict]:
        """回读事件留痕。

        F3b-③c-2②a：tenant_id=None=系统视图（全租户，不闸不过滤——心跳/维护/health）；
        显式租户=bulk 恒 WHERE tenant_id=?（执行隔离真源）+引擎审计留痕。
        """
        if not self.db_path:
            mem = self._events_memory
            if tenant_id is not None:
                self.gate_bulk(tenant_id, "list")
                mem = [e for e in mem if e.get("tenant_id", DEFAULT_TENANT) == tenant_id]
            return list(reversed(mem[-limit:]))
        try:
            self._ensure_db()
            if tenant_id is not None:
                self.gate_bulk(tenant_id, "list")
            sql = "SELECT * FROM proactive_events"
            params: list = []
            if tenant_id is not None:
                sql += " WHERE tenant_id = ?"
                params.append(tenant_id)
            sql += " ORDER BY started_at DESC LIMIT ?"
            params.append(int(limit))
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
            events = []
            for r in rows:
                d = dict(r)
                try:
                    d["result"] = json.loads(d.get("result") or "{}")
                except Exception:
                    d["result"] = {}
                d["ok"] = bool(d["ok"])
                d["tenant_id"] = d.get("tenant_id") or DEFAULT_TENANT
                events.append(d)
            return events
        except Exception as e:
            logger.warning("proactive events read failed: %s", e)
            # 降级内存回读同样恒带租户键（禁 DB 故障降级变成跨租户泄露）
            mem = self._events_memory
            if tenant_id is not None:
                mem = [x for x in mem if x.get("tenant_id", DEFAULT_TENANT) == tenant_id]
            return list(reversed(mem[-limit:]))

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
