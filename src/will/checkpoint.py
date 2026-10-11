"""Workflow execution checkpoint store — STORM分阶段落盘 + agno /continue断点续跑。

调研来源：
- SUMMARY.md §五 will差距表 P1："分阶段checkpoint断点续跑 | STORM分阶段落盘+agno /continue?continue_from"
- 40-storm-source.md #17（P0）："Runner分阶段+本地断点续跑（run_knowledge_curation/run_outline_generation/
  run_article_generation/run_article_polishing可分开跑；_load_information_table_from_local_fs等从磁盘
  恢复上一阶段产物）——长研究任务必须：跑到第3阶段崩了不用重跑前2阶段"
- 65-ag2-source.md #8（P0）："checkpoint()持久化resume state，resume_from=prior_task_id读回checkpoint
  断点续跑（HubBackedCheckpointStore）"
- evolution-engine-patterns.md §5.2 agno job_queue："continue的budget grant = max_attempts = attempt+1
  （用户触发的一次续跑绝不静默重跑）"；"paused tickets are retention-exempt—must outlive arbitrary
  human latency"（可续跑的执行豁免自动清理）
- 21-langflow-source.md #3："持久化图Checkpoint：暂停的图序列化为GraphCheckpoint按(job_id,'graph')
  一行落库，进程重启后按run_id恢复；跨用户扫描风险显式raise防泄漏"
- evolution-engine-patterns.md §1.1 mem0："失败必须可见禁止静默降级"——interrupted执行显式标记
  WAITING+error原因，绝不静默消失
- evolution-engine-patterns.md §4.5 LangGraph："关键写入sync、批量试验async"——每阶段checkpoint
  同步落盘

设计：
- 每个WorkflowExecution一行（execution_id主键），每个DAG节点执行完即save（分阶段落盘）
- 步骤输出（script output / LLM response / HTTP response）随checkpoint持久化——
  续跑时已完成阶段的产物从盘上恢复进variables，不重新执行（STORM语义）
- 进程重启后：RUNNING/PENDING的执行由engine恢复为WAITING（可续跑+原因可见），
  终态执行原样恢复（重启不丢失执行历史——可观测性）
- prune只清理success/cancelled终态；WAITING/FAILED可续跑执行豁免（agno retention-exempt）
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.nest.isolation import IsolationEngine, ResourceType
from src.nest.tenant import tenant_namespace

from .models import ExecutionStatus, StepExecution, WorkflowExecution

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "will_checkpoints.db"

# F3b-③c-1（2026-10-12）数据面租户缺省：匿名/内部执行（系统触发、启动恢复）
# 归 'default' 租户——与生产数据分布（tenant_id 全='default'）一致。
DEFAULT_TENANT = "default"


class CheckpointAccessDenied(Exception):
    """跨租户 checkpoint 访问被 IsolationEngine 拒绝（API 层映射 403，禁静默当 not_found）。

    verdict=check_access 原始回包（allowed/reason/namespaced_id），审计留痕在
    IsolationEngine 审计日志（data/nest.db），此处只携带原因供 API 如实报。
    """

    def __init__(self, reason: str, verdict: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.verdict = verdict or {}


# 可续跑状态（resume准入）：WAITING=被重启打断 / FAILED=节点失败待修复后重跑
RESUMABLE_STATES = frozenset({ExecutionStatus.WAITING, ExecutionStatus.FAILED})
# prune豁免状态（agno retention-exempt：可续跑的执行必须活过任意长的人工延迟）
_PRUNE_EXEMPT = RESUMABLE_STATES | {ExecutionStatus.RUNNING, ExecutionStatus.PENDING}


class CheckpointStore:
    """SQLite-backed execution checkpoint store（Langflow GraphCheckpoint按行落库语义）。

    单文件SQLite；写入为同步调用（LangGraph"关键写入sync"）——每次节点执行完落盘一次。
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # F3b-③c-1：数据面咨询 IsolationEngine（懒构造——首个 gate 调用才建
        # NestStore，收集期零生产库接触）。默认实例即真闸门，无"未接线"模式。
        self._isolation: IsolationEngine | None = None
        self._init_db()

    @property
    def isolation(self) -> IsolationEngine:
        """隔离引擎（check_access 真执行点；审计日志落 NestStore=data/nest.db）。"""
        if self._isolation is None:
            self._isolation = IsolationEngine()
        return self._isolation

    def gate(self, tenant_id: str, row_tenant: str, resource_id: str, action: str) -> None:
        """by-ID 访问闸门：IsolationEngine.check_access 真执行（F3b-③c-1）。

        资源 ID 用 namespaced_id(row_tenant ns, execution_id)——资源的 namespace
        前缀是行归属租户，比较基准是访问者派生租户；namespace 不匹配=跨租户冒读，
        引擎拒绝（allowed=False）→ CheckpointAccessDenied 上抛，禁静默当 not_found。
        允许/拒绝都进引擎审计日志（audit_access=True），total_access_checks>0。
        """
        verdict = self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.CHECKPOINT,
            resource_id=self.isolation.namespaced_id(tenant_namespace(row_tenant), resource_id),
            action=action,
        )
        if not verdict.get("allowed"):
            raise CheckpointAccessDenied(verdict.get("reason", "access denied"), verdict)

    def gate_bulk(self, tenant_id: str, action: str) -> None:
        """bulk 操作（list/prune/stats）审计留痕：执行隔离由 WHERE tenant_id=? 承担，
        引擎侧记一次 namespace 自匹配访问（audit_access=True「Log all access
        attempts」语义），使数据面访问在 /api/nest 隔离审计里可见。"""
        self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.CHECKPOINT,
            resource_id=self.isolation.namespaced_id(tenant_namespace(tenant_id), "*"),
            action=action,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS will_checkpoints (
                        execution_id TEXT PRIMARY KEY,
                        workflow_id TEXT NOT NULL,
                        workflow_name TEXT DEFAULT '',
                        status TEXT NOT NULL,
                        started_at TEXT DEFAULT '',
                        updated_at TEXT DEFAULT '',
                        completed_at TEXT,
                        variables TEXT DEFAULT '{}',
                        steps TEXT DEFAULT '[]',
                        error TEXT,
                        trigger_type TEXT DEFAULT 'manual',
                        resume_count INTEGER DEFAULT 0,
                        tenant_id TEXT DEFAULT 'default'
                    )
                    """
                )
                # F3b-③c-1 数据面隔离键迁移：既有库补列，存量行 DEFAULT 'default'
                # （与 F3 审计实测数据分布 tenant_id 全='default' 一致；DDL+ALTER 双轨幂等）
                try:
                    conn.execute(
                        "ALTER TABLE will_checkpoints ADD COLUMN tenant_id TEXT DEFAULT 'default'"
                    )
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ckpt_workflow "
                    "ON will_checkpoints(workflow_id, started_at)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ckpt_status ON will_checkpoints(status)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ckpt_tenant "
                    "ON will_checkpoints(tenant_id, started_at)"
                )
        except Exception as exc:
            logger.warning("Checkpoint store init failed (non-fatal): %s", exc)

    # ── Core CRUD ───────────────────────────────────────────────

    def save(self, execution: WorkflowExecution) -> None:
        """Upsert一次execution全量状态（每阶段落盘后调用）。

        resume_count从execution对象读取（models.WorkflowExecution.resume_count）。
        写盘失败只记日志不阻塞执行（与engine._save同哲学：持久化故障不阻塞运行），
        但绝不静默假装成功——logger.warning即失败可见。
        """
        try:
            data = execution.model_dump(mode="json")
            # F3b-③c-1 隔离键粘性（jobs._persist COALESCE 同语义）：字段置空=未声明，
            # 不把既有归属洗回 'default'；新行空值落 'default'（与生产分布一致）
            tenant_val = (getattr(execution, "tenant_id", "") or "").strip() or None
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO will_checkpoints (
                        execution_id, workflow_id, workflow_name, status,
                        started_at, updated_at, completed_at,
                        variables, steps, error, trigger_type, resume_count,
                        tenant_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 'default'))
                    ON CONFLICT(execution_id) DO UPDATE SET
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        completed_at=excluded.completed_at,
                        variables=excluded.variables,
                        steps=excluded.steps,
                        error=excluded.error,
                        resume_count=excluded.resume_count,
                        tenant_id=COALESCE(?, will_checkpoints.tenant_id)
                    """,
                    (
                        execution.id,
                        execution.workflow_id,
                        execution.workflow_name,
                        execution.status.value
                        if isinstance(execution.status, ExecutionStatus)
                        else str(execution.status),
                        execution.started_at,
                        datetime.now(UTC).isoformat(),
                        execution.completed_at,
                        json.dumps(data.get("variables", {}), ensure_ascii=False),
                        json.dumps(data.get("steps", []), ensure_ascii=False),
                        execution.error,
                        execution.trigger_type,
                        int(getattr(execution, "resume_count", 0) or 0),
                        tenant_val,
                        tenant_val,
                    ),
                )
        except Exception as exc:
            logger.warning("Checkpoint save failed for %s: %s", execution.id, exc)

    def load(self, execution_id: str, tenant_id: str | None = None) -> WorkflowExecution | None:
        """按execution_id恢复（STORM _load_*_from_local_fs语义 / ag2 resume_from读回）。

        坏行宽容跳过（单行损坏不炸整个恢复流程）但日志可见。
        F3b-③c-1：tenant_id=None=系统内部读（启动恢复等全租户路径，不闸不过滤）；
        显式租户=by-ID 闸门真执行（跨租户 CheckpointAccessDenied，禁静默 None）。
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM will_checkpoints WHERE execution_id = ?", (execution_id,)
                ).fetchone()
        except Exception as exc:
            logger.warning("Checkpoint load failed for %s: %s", execution_id, exc)
            return None
        if row is None:
            return None
        if tenant_id is not None:
            row_tenant = row["tenant_id"] if "tenant_id" in row.keys() else DEFAULT_TENANT
            self.gate(tenant_id, row_tenant or DEFAULT_TENANT, execution_id, "read")
        return self._row_to_execution(row)

    def list_checkpoints(
        self,
        workflow_id: str | None = None,
        resumable_only: bool = False,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出checkpoint行（dict形态，供API/monitoring直接消费）。

        resumable_only=True只返回可续跑执行（WAITING/FAILED）。
        F3b-③c-1：tenant_id=None=系统内部扫描（启动恢复全租户，不闸不过滤）；
        显式租户=bulk 恒 WHERE tenant_id=?（执行隔离真源）+引擎审计留痕。
        """
        try:
            with self._connect() as conn:
                if tenant_id is not None:
                    self.gate_bulk(tenant_id, "list")
                    if workflow_id:
                        rows = conn.execute(
                            "SELECT * FROM will_checkpoints WHERE tenant_id = ? AND workflow_id = ? "
                            "ORDER BY started_at DESC LIMIT ?",
                            (tenant_id, workflow_id, limit),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT * FROM will_checkpoints WHERE tenant_id = ? "
                            "ORDER BY started_at DESC LIMIT ?",
                            (tenant_id, limit),
                        ).fetchall()
                elif workflow_id:
                    rows = conn.execute(
                        "SELECT * FROM will_checkpoints WHERE workflow_id = ? "
                        "ORDER BY started_at DESC LIMIT ?",
                        (workflow_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM will_checkpoints ORDER BY started_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
        except CheckpointAccessDenied:
            raise  # 闸门拒绝必须可见（API 403），禁被宽泛 except 吞成空列表
        except Exception as exc:
            logger.warning("Checkpoint list failed: %s", exc)
            return []
        out = []
        for row in rows:
            item = self._row_to_dict(row)
            if resumable_only and item["status"] not in {s.value for s in RESUMABLE_STATES}:
                continue
            out.append(item)
        return out

    def delete(self, execution_id: str, tenant_id: str | None = None) -> bool:
        """删除checkpoint行。F3b-③c-1：显式租户=by-ID 闸门真执行+按行租户键收口删除
        （跨租户 CheckpointAccessDenied 禁删）；tenant_id=None=系统内部清理通道。"""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT tenant_id FROM will_checkpoints WHERE execution_id = ?", (execution_id,)
                ).fetchone()
                if row is None:
                    return False
                if tenant_id is not None:
                    row_tenant = row["tenant_id"] if "tenant_id" in row.keys() else DEFAULT_TENANT
                    self.gate(tenant_id, row_tenant or DEFAULT_TENANT, execution_id, "write")
                    cur = conn.execute(
                        "DELETE FROM will_checkpoints WHERE execution_id = ? AND tenant_id = ?",
                        (execution_id, tenant_id),
                    )
                else:
                    cur = conn.execute(
                        "DELETE FROM will_checkpoints WHERE execution_id = ?", (execution_id,)
                    )
                return cur.rowcount > 0
        except CheckpointAccessDenied:
            raise  # 闸门拒绝必须可见（API 403），禁被宽泛 except 吞成假 not_found
        except Exception as exc:
            logger.warning("Checkpoint delete failed for %s: %s", execution_id, exc)
            return False

    def prune(self, keep: int = 200, tenant_id: str | None = None) -> int:
        """保留最近keep条终态（success/cancelled）checkpoint，更旧的删除。

        可续跑/进行中状态豁免（agno："paused tickets are retention-exempt—
        must outlive arbitrary human latency"）。返回删除行数。
        F3b-③c-1：显式租户=清理范围恒限本租户名下（跨租户历史行绝不动）+bulk 留痕；
        tenant_id=None=系统内部维护通道（全租户）。
        """
        exempt = tuple(s.value for s in _PRUNE_EXEMPT)
        placeholders = ",".join("?" for _ in exempt)
        try:
            with self._connect() as conn:
                if tenant_id is not None:
                    self.gate_bulk(tenant_id, "prune")
                    cur = conn.execute(
                        f"""
                        DELETE FROM will_checkpoints WHERE execution_id IN (
                            SELECT execution_id FROM will_checkpoints
                            WHERE tenant_id = ? AND status NOT IN ({placeholders})
                            ORDER BY started_at DESC
                            LIMIT -1 OFFSET ?
                        )
                        """,
                        (tenant_id, *exempt, keep),
                    )
                else:
                    cur = conn.execute(
                        f"""
                        DELETE FROM will_checkpoints WHERE execution_id IN (
                            SELECT execution_id FROM will_checkpoints
                            WHERE status NOT IN ({placeholders})
                            ORDER BY started_at DESC
                            LIMIT -1 OFFSET ?
                        )
                        """,
                        (*exempt, keep),
                    )
                return cur.rowcount
        except CheckpointAccessDenied:
            raise  # 闸门拒绝必须可见（API 403），禁被宽泛 except 吞成假 0
        except Exception as exc:
            logger.warning("Checkpoint prune failed: %s", exc)
            return 0

    def stats(self, tenant_id: str | None = None) -> dict[str, Any]:
        """checkpoint统计（monitoring可观测：有多少可续跑/被打断的执行）。

        F3b-③c-1：显式租户=统计恒限本租户名下+bulk 留痕；None=系统全局（health）。
        """
        try:
            with self._connect() as conn:
                if tenant_id is not None:
                    self.gate_bulk(tenant_id, "stats")
                    total = conn.execute(
                        "SELECT COUNT(*) AS c FROM will_checkpoints WHERE tenant_id = ?",
                        (tenant_id,),
                    ).fetchone()["c"]
                    by_status_rows = conn.execute(
                        "SELECT status, COUNT(*) AS c FROM will_checkpoints "
                        "WHERE tenant_id = ? GROUP BY status",
                        (tenant_id,),
                    ).fetchall()
                else:
                    total = conn.execute("SELECT COUNT(*) AS c FROM will_checkpoints").fetchone()[
                        "c"
                    ]
                    by_status_rows = conn.execute(
                        "SELECT status, COUNT(*) AS c FROM will_checkpoints GROUP BY status"
                    ).fetchall()
        except CheckpointAccessDenied:
            raise  # 闸门拒绝必须可见，禁被宽泛 except 吞成假 0
        except Exception as exc:
            logger.warning("Checkpoint stats failed: %s", exc)
            return {"total": 0, "by_status": {}, "resumable": 0}
        by_status = {r["status"]: r["c"] for r in by_status_rows}
        resumable = sum(by_status.get(s.value, 0) for s in RESUMABLE_STATES)
        return {"total": total, "by_status": by_status, "resumable": resumable}

    # ── Deserialization helpers ─────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "execution_id": row["execution_id"],
            "workflow_id": row["workflow_id"],
            "workflow_name": row["workflow_name"],
            "status": row["status"],
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
            "error": row["error"],
            "trigger_type": row["trigger_type"],
            "resume_count": row["resume_count"],
            "tenant_id": (
                row["tenant_id"] if "tenant_id" in row.keys() else DEFAULT_TENANT
            ),  # F3b-③c-1 隔离键随行返回（行归属可观测）
            "steps_count": len(json.loads(row["steps"] or "[]")),
        }

    @staticmethod
    def _row_to_execution(row: sqlite3.Row) -> WorkflowExecution | None:
        try:
            steps_raw = json.loads(row["steps"] or "[]")
            variables = json.loads(row["variables"] or "{}")
            steps = [StepExecution(**s) for s in steps_raw]
            status_raw = row["status"]
            try:
                status = ExecutionStatus(status_raw)
            except ValueError:
                # 未知状态值fail-closed降级为WAITING（可续跑+人工可见），不静默当成功
                logger.warning(
                    "Unknown checkpoint status %r for %s — treating as waiting",
                    status_raw,
                    row["execution_id"],
                )
                status = ExecutionStatus.WAITING
            return WorkflowExecution(
                id=row["execution_id"],
                workflow_id=row["workflow_id"],
                workflow_name=row["workflow_name"] or "",
                status=status,
                started_at=row["started_at"] or datetime.now(UTC).isoformat(),
                completed_at=row["completed_at"],
                steps=steps,
                variables=variables,
                error=row["error"],
                trigger_type=row["trigger_type"] or "manual",
                resume_count=int(row["resume_count"] or 0),
                tenant_id=(row["tenant_id"] if "tenant_id" in row.keys() else DEFAULT_TENANT)
                or DEFAULT_TENANT,
            )
        except Exception as exc:
            logger.warning("Checkpoint row deserialize failed for %s: %s", row["execution_id"], exc)
            return None
