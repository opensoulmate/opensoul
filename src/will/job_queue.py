"""P0-8: 后台作业队列 — 参照agno job_queue模式

功能：
- 提交后台作业（异步执行，立即返回job_id）
- 查询作业状态（pending/running/completed/failed）
- 获取作业结果
- 作业列表（按状态过滤）
- 超时控制 + 失败重试

参照: agno job_queue/store.py 300行 + langfuse BullMQ + kilocode BackgroundJob

运行时接线（2026-09-19 cron轮，"写了≠接线了"修复）：
- handler注册表：src/will/job_handlers.py（api/will.py模块加载时注册）
- worker池懒启动：首次submit时start()（agno "executor上线才claim任务"）
- idempotency_key去重：同key的pending/running作业不重复创建（agno §5.2幂等键）
- stale回收：进程重启后DB里的pending/running作业由新进程重排队
  （agno原版靠heartbeat lease回收；本部署单进程，重启即全部回收，
    差异在此注明，不假装有分布式租约）
- list_jobs/get/get_stats以SQLite为真源：重启后monitoring面板仍可见历史

retry_or_fail（agno §5.2退避 + §1.4错误风暴熔断，2026-09-21 cron轮，上轮遗留#5销账）：
- 指数退避：失败重试不立即回队，delay=base×2^(n-1) capped+jitter（cortex/llm_retry.py
  同款2s×2^n惯例），next_retry_at落盘monitoring可见；退避窗口跨重启保留
- 错误风暴熔断：同job名连续同错误≥storm_window→抑制重试显式终态（error带
  [breaker_open]前缀），cooldown过期半开、同名成功自动复位——防系统性故障
  （provider宕机/密钥失效/handler bug）烧完每个作业的重试预算（agno"连续同错即停"）
- requeue：POST /api/will/jobs/{id}/requeue 人工续跑终态作业——budget grant=
  恰好一次（"用户触发的一次续跑绝不静默重跑"），人工意图不受熔断拦截
- stale回收遵守重试预算：RUNNING崩溃且retries≥max_retries→显式失败不静默重跑
  （agno at-most-once默认：副作用可能已发生），error指引requeue
"""

import asyncio
import json
import logging
import random
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

from src.nest.isolation import IsolationEngine, ResourceType
from src.nest.tenant import tenant_namespace

logger = logging.getLogger("opensoul.job_queue")

# F3b-③a（2026-10-12）数据面租户缺省：匿名/内部提交（proactive、dream_producer
# 等系统作业）归 'default' 租户——与生产数据分布（tenant_id 全='default'）一致。
DEFAULT_TENANT = "default"


class JobAccessDenied(Exception):
    """跨租户作业访问被 IsolationEngine 拒绝（API 层映射 403，禁静默当 not_found）。

    verdict=check_access 原始回包（allowed/reason/namespaced_id），审计留痕在
    IsolationEngine 审计日志（data/nest.db），此处只携带原因供 API 如实报。
    """

    def __init__(self, reason: str, verdict: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.verdict = verdict or {}


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    name: str
    status: JobStatus = JobStatus.PENDING
    params: dict = field(default_factory=dict)
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0
    finished_at: float = 0
    timeout_s: int = 300
    retries: int = 0
    max_retries: int = 2
    next_retry_at: float = 0  # agno retry_or_fail退避：下一次重试执行时间（0=无退避等待）
    source: str = "real"  # provenance（learned_skills.source同款哲学）：real=生产作业 /
    # test=测试作业——测试垃圾不进经验/进化链（systemic_test往生产API提交test_job/
    # test_cancel/stress_test曾污染job_queue.db 1092行→experiences 1092行→
    # evolution_log 279行假"高频失败进化"）
    tenant_id: str = DEFAULT_TENANT  # F3b-③a 数据面隔离键：归属租户（提交时落，
    # 后续读/改/删按此过滤；IsolationEngine check_access 的资源 namespace 来源）

    @property
    def duration_s(self) -> float:
        if self.started_at and self.finished_at:
            return round(self.finished_at - self.started_at, 2)
        return 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": str(self.status),
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "retries": self.retries,
            "timeout_s": self.timeout_s,
            "next_retry_at": self.next_retry_at,
            "source": self.source,
            "tenant_id": self.tenant_id,
        }


class JobQueue:
    """后台作业队列 — SQLite持久化 + asyncio worker池"""

    def __init__(
        self,
        db_path: str = "",
        max_workers: int = 3,
        retry_base_delay_s: float = 2.0,
        retry_max_delay_s: float = 60.0,
        retry_jitter: bool = True,
        storm_window: int = 3,
        storm_cooldown_s: float = 300.0,
    ):
        self._db_path = db_path or str(Path.home() / "opensoul" / "data" / "job_queue.db")
        self._jobs: dict[str, Job] = {}
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._max_workers = max_workers
        self._running = False
        # agno retry_or_fail退避（cortex/llm_retry.py同款2s×2^n capped+jitter惯例）
        self._retry_base_delay_s = retry_base_delay_s
        self._retry_max_delay_s = retry_max_delay_s
        self._retry_jitter = retry_jitter
        # agno §1.4错误风暴熔断：同job名连续同错误≥window→抑制重试
        self._storm_window = max(1, storm_window)
        self._storm_cooldown_s = storm_cooldown_s
        self._retry_tasks: dict[str, asyncio.Task] = {}
        self._breakers: dict[str, dict] = {}
        # F3b-③a：数据面咨询 IsolationEngine（懒构造——首个 gate 调用才建
        # NestStore，收集期零生产库接触）。默认实例即真闸门，无"未接线"模式。
        self._isolation: IsolationEngine | None = None
        self._init_db()

    @property
    def isolation(self) -> IsolationEngine:
        """隔离引擎（check_access 真执行点；审计日志落 NestStore=data/nest.db）。"""
        if self._isolation is None:
            self._isolation = IsolationEngine()
        return self._isolation

    def _gate(self, tenant_id: str, row_tenant: str, resource_id: str, action: str) -> None:
        """by-ID 访问闸门：IsolationEngine.check_access 真执行（F3b-③a）。

        资源 ID 用 namespaced_id(row_tenant ns, job_id)——资源的 namespace 前缀
        是行归属租户，比较基准是访问者派生租户；namespace 不匹配=跨租户冒读，
        引擎拒绝（allowed=False）→ JobAccessDenied 上抛，禁静默当 not_found。
        允许/拒绝都进引擎审计日志（audit_access=True），total_access_checks>0。
        """
        verdict = self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.JOB,
            resource_id=self.isolation.namespaced_id(tenant_namespace(row_tenant), resource_id),
            action=action,
        )
        if not verdict.get("allowed"):
            raise JobAccessDenied(verdict.get("reason", "access denied"), verdict)

    def _gate_bulk(self, tenant_id: str, action: str) -> None:
        """bulk 操作（list/purge）审计留痕：执行隔离由 WHERE tenant_id=? 承担，
        引擎侧记一次 namespace 自匹配访问（audit_access=True「Log all access
        attempts」语义），使数据面访问在 /api/nest 隔离审计里可见。"""
        self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.JOB,
            resource_id=self.isolation.namespaced_id(tenant_namespace(tenant_id), "*"),
            action=action,
        )

    def _conn(self) -> sqlite3.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    params TEXT DEFAULT '{}',
                    result TEXT,
                    error TEXT DEFAULT '',
                    created_at REAL,
                    started_at REAL DEFAULT 0,
                    finished_at REAL DEFAULT 0,
                    timeout_s INTEGER DEFAULT 300,
                    retries INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 2,
                    idempotency_key TEXT DEFAULT '',
                    next_retry_at REAL DEFAULT 0,
                    source TEXT DEFAULT 'real',
                    tenant_id TEXT DEFAULT 'default'
                )
            """)
            # 幂等键列迁移（既有库补列；重复列错误按消息匹配安全吞掉）
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN idempotency_key TEXT DEFAULT ''")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
            # 退避列迁移（agno retry_or_fail：next_retry_at跨重启保留退避窗口）
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN next_retry_at REAL DEFAULT 0")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
            # provenance列迁移（learned_skills.source同款：既有库补列，存量行
            # DEFAULT 'real'——存量测试垃圾另行purge/清理，不靠列默认值区分）
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN source TEXT DEFAULT 'real'")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
            # F3b-③a 数据面隔离键迁移：既有库补列，存量行 DEFAULT 'default'
            # （与 F3 审计实测数据分布 tenant_id 全='default' 一致）
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN tenant_id TEXT DEFAULT 'default'")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_idem ON jobs(idempotency_key, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_tenant ON jobs(tenant_id, created_at)"
            )

    def register_handler(self, name: str, handler: Callable[..., Awaitable[Any]]):
        """注册作业处理器"""
        self._handlers[name] = handler

    def find_by_idempotency_key(
        self, idempotency_key: str, tenant_id: str = DEFAULT_TENANT
    ) -> str | None:
        """按幂等键查pending/running作业id（API层duplicate回执用，agno §5.2）。

        F3b-③a：幂等键按租户隔离（同key不同租户=两个独立作业，互不去重——
        幂等键是调用方命名空间内的去重承诺，不是跨租户全局锁）。
        """
        if not idempotency_key:
            return None
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT id FROM jobs WHERE idempotency_key = ? AND tenant_id = ? AND status IN ('pending','running') LIMIT 1",
                    (idempotency_key, tenant_id or DEFAULT_TENANT),
                ).fetchone()
            return row["id"] if row else None
        except Exception as e:
            logger.error(f"find_by_idempotency_key failed: {e}")
            return None

    async def start(self):
        """启动worker池"""
        if self._running:
            return
        self._running = True
        # stale回收：上一个进程遗留的pending/running作业重新排队
        await self._recover_from_db()
        for i in range(self._max_workers):
            task = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            self._workers.append(task)
        logger.info(f"JobQueue started: {self._max_workers} workers")

    async def _recover_from_db(self):
        """重启恢复：DB为真源——pending重新排队，running视为死进程遗留（agno stale回收简化版）。

        同时把DB历史行载入内存索引，get()/worker取作业在重启后不再失明。
        """
        try:
            with self._conn() as conn:
                rows = conn.execute("SELECT * FROM jobs").fetchall()
        except Exception as e:
            logger.error(f"job recovery read failed: {e}")
            return
        recovered = 0
        for row in rows:
            job_id = row["id"]
            if job_id in self._jobs:
                continue
            job = self._row_to_job(row)
            self._jobs[job_id] = job
            if job.status == JobStatus.RUNNING:
                if job.retries >= job.max_retries:
                    # agno at-most-once默认：崩溃作业显式失败不静默重跑
                    # （副作用可能已发生）；人工续跑走requeue（grant恰好一次）
                    job.status = JobStatus.FAILED
                    job.error = (
                        "stale recovery: crashed mid-run, retry budget exhausted "
                        f"({job.retries}/{job.max_retries}) — requeue via "
                        f"POST /api/will/jobs/{job.id}/requeue"
                    )
                    job.finished_at = time.time()
                    self._persist(job)
                else:
                    # 死进程遗留：回pending重跑（retries保留，不静默重置计数）
                    job.status = JobStatus.PENDING
                    job.error = "stale recovery: previous process exited mid-run"
                    self._persist(job)
            if job.status == JobStatus.PENDING:
                if job.next_retry_at > time.time():
                    # agno retry_or_fail退避：窗口跨重启保留，到点才回队
                    self._schedule_retry(job_id, job.next_retry_at - time.time())
                else:
                    await self._queue.put(job_id)
                recovered += 1
        if recovered:
            logger.info(f"JobQueue recovery: re-queued {recovered} stale jobs")

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        """DB行 → Job对象（params/result宽容反序列化，坏JSON不炸恢复流程）"""
        try:
            params = json.loads(row["params"] or "{}")
        except Exception:
            params = {}
        result: Any = row["result"]
        if result:
            try:
                result = json.loads(result)
            except Exception:
                pass  # 保留原文，绝不因坏JSON丢结果
        try:
            status = JobStatus(str(row["status"]))
        except Exception:
            status = JobStatus.FAILED
        return Job(
            id=row["id"],
            name=row["name"],
            status=status,
            params=params if isinstance(params, dict) else {},
            result=result,
            error=row["error"] or "",
            created_at=row["created_at"] or 0,
            started_at=row["started_at"] or 0,
            finished_at=row["finished_at"] or 0,
            timeout_s=row["timeout_s"] or 300,
            retries=row["retries"] or 0,
            max_retries=row["max_retries"] if row["max_retries"] is not None else 2,
            next_retry_at=(row["next_retry_at"] or 0) if "next_retry_at" in row.keys() else 0,
            source=(row["source"] or "real") if "source" in row.keys() else "real",
            tenant_id=(row["tenant_id"] or DEFAULT_TENANT)
            if "tenant_id" in row.keys()
            else DEFAULT_TENANT,
        )

    async def stop(self):
        """停止worker池"""
        self._running = False
        for task in self._workers:
            task.cancel()
        self._workers.clear()
        for task in self._retry_tasks.values():
            task.cancel()
        self._retry_tasks.clear()
        logger.info("JobQueue stopped")

    async def submit(
        self,
        name: str,
        params: dict | None = None,
        timeout_s: int = 300,
        max_retries: int = 2,
        idempotency_key: str = "",
        source: str = "real",
        tenant_id: str = DEFAULT_TENANT,
    ) -> str:
        """提交后台作业，立即返回job_id。

        idempotency_key（agno §5.2）：同key已有pending/running作业时返回既有job_id，
        不静默重复执行；已完成/失败的同key作业不拦新提交（重试是合法意图）。
        source（provenance）：'test'=测试作业（经验采集侧跳过，learned_skills.source
        同款）；其他值原样保留（未知不猜不归一）。
        tenant_id（F3b-③a）：作业归属租户（API 层来自 bind_identity 派生，禁自报；
        内部/系统作业缺省 'default'）——后续读/改/删按此过滤+IsolationEngine 闸门。
        """
        tenant_id = (tenant_id or "").strip() or DEFAULT_TENANT
        if idempotency_key:
            with self._conn() as conn:
                existing = conn.execute(
                    "SELECT id FROM jobs WHERE idempotency_key = ? AND tenant_id = ? AND status IN ('pending','running') LIMIT 1",
                    (idempotency_key, tenant_id),
                ).fetchone()
            if existing:
                logger.info(
                    f"Job submit deduped by idempotency_key={idempotency_key} -> {existing['id']}"
                )
                return existing["id"]
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        job = Job(
            id=job_id,
            name=name,
            params=params or {},
            timeout_s=timeout_s,
            max_retries=max_retries,
            source=(source or "real").strip() or "real",
            tenant_id=tenant_id,
        )
        self._jobs[job_id] = job
        self._persist(job, idempotency_key=idempotency_key)
        await self._queue.put(job_id)
        logger.info(f"Job submitted: {job_id} ({name})")
        return job_id

    def get(self, job_id: str, tenant_id: str = DEFAULT_TENANT) -> dict | None:
        """查询作业状态（F3b-③a：by-ID 读走 IsolationEngine 闸门——跨租户冒读
        抛 JobAccessDenied，不静默当 not_found）。"""
        job = self._jobs.get(job_id)
        if not job:
            # Try loading from DB
            with self._conn() as conn:
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if row:
                    job = self._row_to_job(row)
                    self._jobs[job_id] = job
        if not job:
            return None
        self._gate(tenant_id, job.tenant_id, job_id, "read")
        return job.to_dict()

    def list_jobs(
        self, status: str = "", limit: int = 50, tenant_id: str = DEFAULT_TENANT
    ) -> list[dict]:
        """列出作业（SQLite为真源——重启后历史仍可见，monitoring面板依赖）。

        F3b-③a：bulk 读按租户过滤（WHERE tenant_id=? 为执行真源）+引擎审计留痕。
        """
        tenant_id = (tenant_id or "").strip() or DEFAULT_TENANT
        self._gate_bulk(tenant_id, "list")
        query = "SELECT * FROM jobs WHERE tenant_id = ?"
        args: list[Any] = [tenant_id]
        if status:
            query += " AND status = ?"
            args.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        try:
            with self._conn() as conn:
                rows = conn.execute(query, args).fetchall()
        except Exception as e:
            logger.error(f"list_jobs db read failed: {e}")
            return []
        jobs = []
        for row in rows:
            job = self._jobs.get(row["id"])
            jobs.append(job.to_dict() if job else self._row_to_job(row).to_dict())
        return jobs

    def cancel(self, job_id: str, tenant_id: str = DEFAULT_TENANT) -> bool:
        """取消作业（F3b-③a：by-ID 写走 IsolationEngine 闸门）"""
        job = self._jobs.get(job_id)
        if not job:
            with self._conn() as conn:
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if row:
                    job = self._row_to_job(row)
                    self._jobs[job_id] = job
        if not job:
            return False
        self._gate(tenant_id, job.tenant_id, job_id, "write")
        if job.status == JobStatus.PENDING:
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
            job.next_retry_at = 0
            self._persist(job)
            task = self._retry_tasks.pop(job_id, None)
            if task and not task.done():
                task.cancel()
            return True
        return False

    def purge(
        self,
        status: str = "",
        name_pattern: str = "",
        older_than_days: int = 0,
        source: str = "",
        tenant_id: str = DEFAULT_TENANT,
    ) -> int:
        """清理作业历史 — 集成修复#4: 测试噪声积压(test_job failed×20等)。

        source过滤（provenance精确打击）：source='test'只清测试作业行——
        不按名字猜（LIKE误伤含'test'的真实作业名），按schema标记清。
        零过滤=DELETE全表footgun：调用方（API层）必须给至少一个过滤条件。
        F3b-③a：恒按租户过滤（tenant 不算过滤条件——防"只给租户"变相清全史），
        跨租户历史行绝不动。
        """
        if not (status or name_pattern or older_than_days or source):
            logger.warning("JobQueue.purge拒绝零过滤调用（防误清全表）")
            return 0
        tenant_id = (tenant_id or "").strip() or DEFAULT_TENANT
        self._gate_bulk(tenant_id, "delete")
        where: list[str] = ["tenant_id = ?"]
        params: list = [tenant_id]
        if status:
            where.append("status = ?")
            params.append(status)
        if name_pattern:
            where.append("name LIKE ?")
            params.append(f"%{name_pattern}%")
        if source:
            where.append("source = ?")
            params.append(source)
        if older_than_days > 0:
            where.append("created_at < ?")
            params.append(time.time() - older_than_days * 86400)
        sql = "DELETE FROM jobs" + (f" WHERE {' AND '.join(where)}" if where else "")
        with self._conn() as conn:
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM jobs" + (f" WHERE {' AND '.join(where)}" if where else ""),
                    params,
                ).fetchall()
            ]
            cur = conn.execute(sql, params)
        purged = cur.rowcount or 0
        # 内存注册表同步清理：否则purge后get()仍返回已删行的内存副本（指标说谎）
        for jid in ids:
            self._jobs.pop(jid, None)
        logger.info(
            "JobQueue.purge: %d条 (status=%s pattern=%s source=%s)",
            purged,
            status,
            name_pattern,
            source,
        )
        return purged

    def get_stats(self) -> dict:
        """队列统计（SQLite为真源 + 进程内实时字段）"""
        counts: dict[str, int] = {}
        total = 0
        try:
            with self._conn() as conn:
                for row in conn.execute("SELECT status, COUNT(*) AS c FROM jobs GROUP BY status"):
                    counts[str(row["status"])] = int(row["c"])
                    total += int(row["c"])
        except Exception as e:
            logger.error(f"get_stats db read failed: {e}")
        now = time.time()
        breakers = {
            name: {
                "open": bool(e.get("open")),
                "count": int(e.get("count", 0)),
                "blocked": int(e.get("blocked", 0)),
                "cooldown_remaining_s": (
                    round(max(0.0, self._storm_cooldown_s - (now - e.get("opened_at", 0))), 1)
                    if e.get("open")
                    else 0.0
                ),
            }
            for name, e in self._breakers.items()
        }
        return {
            "total": total,
            "by_status": counts,
            "queue_size": self._queue.qsize(),
            "workers": len(self._workers),
            "handlers": sorted(self._handlers.keys()),
            "running": self._running,
            "retry_scheduled": len(self._retry_tasks),
            "breakers": breakers,
        }

    async def _worker_loop(self, worker_name: str):
        """Worker循环：从队列取作业并执行"""
        while self._running:
            try:
                job_id = await asyncio.wait_for(self._queue.get(), timeout=5)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            job = self._jobs.get(job_id)
            if not job or job.status == JobStatus.CANCELLED:
                continue

            handler = self._handlers.get(job.name)
            if not handler:
                job.status = JobStatus.FAILED
                job.error = f"No handler for job type '{job.name}'"
                job.finished_at = time.time()
                self._persist(job)
                continue

            # Execute
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            job.next_retry_at = 0
            self._persist(job)

            try:
                result = await asyncio.wait_for(handler(**job.params), timeout=job.timeout_s)
                job.status = JobStatus.COMPLETED
                job.result = result
                self._breaker_on_success(job.name)
            except TimeoutError:
                if await self._handle_failure(
                    job, f"Job timed out after {job.timeout_s}s", is_timeout=True
                ):
                    continue  # 已按退避调度延迟重试
            except Exception as e:
                if await self._handle_failure(job, str(e)[:500], is_timeout=False):
                    continue  # 已按退避调度延迟重试

            job.finished_at = time.time()
            self._persist(job)
            logger.info(
                f"[{worker_name}] Job {job_id} ({job.name}): {job.status} in {job.duration_s}s"
            )

    # ── agno retry_or_fail退避 + §1.4错误风暴熔断 + requeue（2026-09-21遗留#5）──

    def _backoff_delay(self, attempt: int) -> float:
        """指数退避秒数（agno retry_or_fail退避）。attempt=1→首次重试。
        惯例对齐cortex/llm_retry.py（kilocode 2s×2^n capped，half-jitter防惊群）。"""
        delay = min(self._retry_base_delay_s * (2 ** max(0, attempt - 1)), self._retry_max_delay_s)
        if self._retry_jitter:
            delay *= 0.5 + random.random() * 0.5
        return delay

    def _breaker_record_failure(self, name: str, error: str) -> bool:
        """错误风暴熔断（agno environments §1.4：连续同错即停）。

        同job名连续相同错误（signature=error前200字符）≥storm_window→熔断打开，
        重试被抑制直至cooldown过期（半开：本次失败按新风暴重新计数）或同名作业
        成功（_breaker_on_success复位）。返回breaker是否允许本次重试。
        防系统性故障（provider宕机/密钥失效/handler bug）烧完每个作业的重试预算，
        也防把系统性故障记成N条独立失败（evolution-engine-patterns §1.4）。
        """
        sig = (error or "")[:200]
        now = time.time()
        entry = self._breakers.get(name)
        if entry and entry.get("open"):
            if now - entry.get("opened_at", 0) < self._storm_cooldown_s:
                entry["blocked"] = int(entry.get("blocked", 0)) + 1
                return False
            entry = None  # cooldown过期→半开：本次失败按新风暴重新计数
        if entry and entry.get("sig") == sig:
            entry["count"] = int(entry.get("count", 0)) + 1
        else:
            entry = {"sig": sig, "count": 1, "open": False, "opened_at": 0, "blocked": 0}
        self._breakers[name] = entry
        if entry["count"] >= self._storm_window:
            entry["open"] = True
            entry["opened_at"] = now
            logger.warning(
                f"JobQueue breaker OPEN for '{name}': {entry['count']} consecutive "
                f"identical failures — retry suppressed {self._storm_cooldown_s}s"
            )
            return False
        return True

    def _breaker_on_success(self, name: str):
        """同名作业成功→熔断复位（半开探测成功的语义）"""
        if self._breakers.pop(name, None):
            logger.info(f"JobQueue breaker RESET for '{name}': job succeeded")

    async def _handle_failure(self, job: Job, error: str, is_timeout: bool) -> bool:
        """失败处理（agno retry_or_fail退避 + §1.4熔断 + mem0 §1.1失败可见）。

        返回True=已调度延迟重试（调用方continue worker循环，不占槽位睡眠）；
        返回False=终态（调用方落finished_at）。
        - budget未尽且熔断允许 → 指数退避后重排，next_retry_at落盘（monitoring可见）
        - 熔断打开 → 立即终态，error带[breaker_open]前缀（不烧重试预算）
        - budget耗尽 → 终态（error原文，重试计数保留可见）
        """
        job.status = JobStatus.TIMEOUT if is_timeout else JobStatus.FAILED
        job.error = error
        breaker_ok = self._breaker_record_failure(job.name, error)
        if breaker_ok and job.retries < job.max_retries:
            job.retries += 1
            delay = self._backoff_delay(job.retries)
            job.status = JobStatus.PENDING
            job.next_retry_at = time.time() + delay
            self._persist(job)
            self._schedule_retry(job.id, delay)
            logger.warning(
                f"Job {job.id} retry {job.retries}/{job.max_retries} in {delay:.1f}s: {error[:120]}"
            )
            return True
        if not breaker_ok:
            job.error = f"[breaker_open] {error}"
        return False

    def _schedule_retry(self, job_id: str, delay: float):
        """延迟重排：worker不占槽位睡眠，到点由事件循环回队（agno退避语义）。
        同job旧调度先取消防重复回队；stop()/cancel()时调度任务被取消。
        """
        old = self._retry_tasks.pop(job_id, None)
        if old and not old.done():
            old.cancel()

        async def _delayed():
            try:
                await asyncio.sleep(delay)
                job = self._jobs.get(job_id)
                if self._running and job and job.status == JobStatus.PENDING:
                    await self._queue.put(job_id)
            except asyncio.CancelledError:
                pass
            finally:
                self._retry_tasks.pop(job_id, None)

        try:
            self._retry_tasks[job_id] = asyncio.create_task(_delayed())
        except RuntimeError:
            # 无运行中事件循环（同步上下文防御）：退避降级为立即回队，不静默丢作业
            logger.warning(f"No event loop for delayed retry of {job_id}; enqueue immediately")
            self._queue.put_nowait(job_id)

    async def requeue(self, job_id: str, tenant_id: str = DEFAULT_TENANT) -> dict:
        """agno /queue/jobs/{id}/requeue：终态作业人工续跑。

        budget grant = max_retries = retries（evolution-engine-patterns §5.2：
        "用户触发的一次续跑绝不静默重跑"）——grant恰好一次执行，成功/失败都显式
        可见，不因剩余budget静默追加重试。人工意图直跑不受熔断拦截；若作业成功，
        同名熔断器自动复位。pending/running不requeue（not_terminal）。
        """
        job = self._jobs.get(job_id)
        if not job:
            try:
                with self._conn() as conn:
                    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            except Exception as e:
                logger.error(f"requeue db read failed: {e}")
                row = None
            if not row:
                return {"requeued": False, "reason": "not_found"}
            job = self._row_to_job(row)
            self._jobs[job_id] = job
        self._gate(tenant_id, job.tenant_id, job_id, "write")
        if job.status not in (JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.CANCELLED):
            return {"requeued": False, "reason": f"not_terminal:{job.status}", "job": job.to_dict()}
        old = self._retry_tasks.pop(job_id, None)
        if old and not old.done():
            old.cancel()
        job.max_retries = job.retries  # agno budget grant：恰好一次，绝不静默重跑
        job.status = JobStatus.PENDING
        job.next_retry_at = 0
        job.finished_at = 0
        self._persist(job)
        await self._queue.put(job_id)
        logger.info(f"JobQueue.requeue: {job_id} ({job.name}) granted one more attempt")
        return {"requeued": True, "job": job.to_dict()}

    def _persist(self, job: Job, idempotency_key: str = ""):
        """持久化作业状态到SQLite"""
        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO jobs
                    (id, name, status, params, result, error, created_at, started_at, finished_at, timeout_s, retries, max_retries, idempotency_key, next_retry_at, source, tenant_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, (SELECT idempotency_key FROM jobs WHERE id = ?), ''), ?, COALESCE(?, (SELECT source FROM jobs WHERE id = ?), 'real'), COALESCE(?, (SELECT tenant_id FROM jobs WHERE id = ?), 'default'))
                """,
                    (
                        job.id,
                        job.name,
                        str(job.status),
                        json.dumps(job.params, ensure_ascii=False),
                        json.dumps(job.result, ensure_ascii=False, default=str)
                        if job.result is not None
                        else None,
                        job.error,
                        job.created_at,
                        job.started_at,
                        job.finished_at,
                        job.timeout_s,
                        job.retries,
                        job.max_retries,
                        idempotency_key or None,
                        job.id,
                        job.next_retry_at,
                        # source粘性（idempotency_key同款COALESCE）：状态更新轮的
                        # REPLACE不把既有provenance冲回默认值——'test'永不被洗白
                        (job.source or "").strip() or None,
                        job.id,
                        # tenant粘性（同款COALESCE）：归属租户不被状态更新轮洗回
                        # 'default'（F3b-③a）
                        (job.tenant_id or "").strip() or None,
                        job.id,
                    ),
                )
        except Exception as e:
            logger.error(f"Failed to persist job {job.id}: {e}")


# ── 全局单例 ──
_job_queue: JobQueue | None = None


def get_job_queue() -> JobQueue:
    global _job_queue
    if _job_queue is None:
        _job_queue = JobQueue()
    return _job_queue
