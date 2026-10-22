"""DAG task planner for OpenWill.

Decomposes complex tasks into directed acyclic graphs (DAGs) for
parallel execution with dependency management.

Borrowed from: LLM Compiler, Plan-and-Execute, Tree of Thoughts
"""

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional

from src.nest.isolation import IsolationEngine, ResourceType
from src.nest.tenant import tenant_namespace

from .checkpoint import DEFAULT_TENANT
from .plan_persist import PlanPersistence

logger = logging.getLogger("opensoul.will.planner")


class DAGPlanAccessDenied(Exception):
    """跨租户 DAG 计划面访问被 IsolationEngine 拒绝（API 层映射 403，禁静默当 not_found）。

    F3b-⑧b（2026-10-13）：与 GoalAccessDenied/WorkflowAccessDenied/CheckpointAccessDenied
    同族——verdict=check_access 原始回包（allowed/reason/namespaced_id），审计留痕在
    IsolationEngine 审计日志（data/nest.db）。
    """

    def __init__(self, reason: str, verdict: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.verdict = verdict or {}


class StepStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    step_id: str
    description: str
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: str = ""
    estimated_duration: float = 0.0
    actual_duration: float = 0.0
    retry_count: int = 0

    def to_state(self) -> dict:
        """完整往返形（RV-①b-⑦-1 纪律：落盘形独立单一源）——逐字段全量，
        result Any 原值进 payload（persist 层 json.dumps default=str 诚实边界④）。"""
        return {
            "step_id": self.step_id,
            "description": self.description,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "depends_on": list(self.depends_on),
            "status": str(self.status),
            "result": self.result,
            "error": self.error,
            "estimated_duration": self.estimated_duration,
            "actual_duration": self.actual_duration,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_state(cls, state: dict) -> "PlanStep":
        """to_state 逆（缺字段如实 KeyError 禁 fabricate——坏行由回载侧跳过可见）。"""
        return cls(
            step_id=str(state["step_id"]),
            description=str(state["description"]),
            tool_name=str(state["tool_name"]),
            arguments=state["arguments"] if isinstance(state["arguments"], dict) else {},
            depends_on=list(state["depends_on"]),
            status=StepStatus(state["status"]),
            result=state.get("result"),
            error=str(state.get("error") or ""),
            estimated_duration=float(state.get("estimated_duration") or 0.0),
            actual_duration=float(state.get("actual_duration") or 0.0),
            retry_count=int(state.get("retry_count") or 0),
        )


@dataclass
class ExecutionPlan:
    plan_id: str
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    status: str = "pending"
    metadata: dict = field(default_factory=dict)
    # F3b-⑧b：行归属租户（create=bind_identity 派生禁自报；缺失/空白→'default'）
    tenant_id: str = DEFAULT_TENANT

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        return completed / len(self.steps)

    @property
    def is_complete(self) -> bool:
        return all(
            s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED, StepStatus.FAILED)
            for s in self.steps
        )

    def get_ready_steps(self) -> list[PlanStep]:
        """Get steps whose dependencies are all satisfied."""
        completed_ids = {s.step_id for s in self.steps if s.status == StepStatus.COMPLETED}
        return [
            s
            for s in self.steps
            if s.status == StepStatus.PENDING and all(dep in completed_ids for dep in s.depends_on)
        ]

    def to_state(self) -> dict:
        """完整往返形（RV-①b-⑦-1 纪律：落盘形独立单一源）——steps/metadata 随
        payload 整体 json.dumps（dict/list 列禁裸存），tenant_id 行归属键随行保真。"""
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "steps": [s.to_state() for s in self.steps],
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "metadata": dict(self.metadata),
            "tenant_id": self.tenant_id,
        }

    @classmethod
    def from_state(cls, state: dict) -> "ExecutionPlan":
        """to_state 逆（缺字段如实 KeyError 禁 fabricate——坏行由回载侧跳过可见）。"""
        return cls(
            plan_id=str(state["plan_id"]),
            goal=str(state["goal"]),
            steps=[PlanStep.from_state(s) for s in state["steps"]],
            created_at=float(state["created_at"]),
            started_at=float(state.get("started_at") or 0.0),
            completed_at=float(state.get("completed_at") or 0.0),
            status=str(state["status"]),
            metadata=dict(state.get("metadata") or {}),
            tenant_id=str(state.get("tenant_id") or DEFAULT_TENANT),
        )


class DAGPlanner:
    """Creates and manages DAG-based execution plans."""

    def __init__(self, persist: PlanPersistence | None = None):
        self._plans: dict[str, ExecutionPlan] = {}
        self._stats = {
            "total_plans": 0,
            "completed_plans": 0,
            "failed_plans": 0,
            "total_steps": 0,
        }
        # F3b-⑧b：隔离引擎按 NEST_DB env 值缓存（conftest 全测试隔离自动换引擎，
        # ai_groups._iso 同款纪律）；懒构造=首个 gate 调用才建 NestStore，
        # 系统路径（tenant_id=None）零生产库接触。
        self._iso_cache: tuple[str, IsolationEngine] | None = None
        # RV-①b-⑫ 持久化接线（缺省 None=纯内存零变化）：写穿+首触懒回载。
        self._persist = persist
        self._plans_loaded = False

    # ── RV-①b-⑫ 计划持久化 ──────────────────────────────────────────
    def _ensure_loaded(self) -> None:
        """首触懒回载（RV-①b-⑤-1/⑨ 先例）：只补缺不覆盖（内存在册更真禁回载洗
        内存新态）；回载失败不置位下次触碰重试（诚实边界②）。每变更点/读点
        先载后录=回载时注册表恒空生产不变量。坏行（payload/枚举值不可解）跳过
        +logger.error 可见（persist 层 payload 坏行+本层 from_state 坏行双防线）。"""
        if self._persist is None or self._plans_loaded:
            return
        try:
            rows = self._persist.load_all()
        except Exception as e:
            logger.error(f"will 计划回载失败（空注册表起步，下次触碰重试）: {e}")
            return
        for row in rows:
            try:
                plan = ExecutionPlan.from_state(row["payload"] | {"plan_id": row["plan_id"]})
            except Exception as e:
                logger.error(f"will 计划坏行跳过（plan_id={row.get('plan_id')}，状态不可解）: {e}")
                continue
            if plan.plan_id not in self._plans:
                self._plans[plan.plan_id] = plan
        self._plans_loaded = True

    def _plan_to_row(self, plan: ExecutionPlan) -> dict:
        """落盘行形（列=行形、payload=完整往返形）——单一源禁两套序列化。"""
        state = plan.to_state()
        return {
            "plan_id": plan.plan_id,
            "goal": plan.goal,
            "status": plan.status,
            "tenant_id": plan.tenant_id,
            "created_at": plan.created_at,
            "started_at": plan.started_at,
            "completed_at": plan.completed_at,
            "steps_count": len(plan.steps),
            "payload": state,
        }

    def _sync_plan(self, plan: ExecutionPlan) -> None:
        """写穿单一点（全变更点共用，RV-①b-② _sync_entry 纪律禁散落写点）：
        记账旁路——写失败 logger.error 可见不阻断计划动作本体（诚实边界①）。"""
        if self._persist is None:
            return
        try:
            self._persist.upsert(self._plan_to_row(plan))
        except Exception as e:
            logger.error(f"will 计划落盘失败（该行只活内存重启丢，非谎报 durable）: {e}")

    def _counter_truth(self) -> dict:
        """计数单一判法（MEM-⑥）：persist 在场恒对账行聚合=内存 _stats 降级缓存
        视图（含 active_plans 同谓词派生）；对账失败回落缓存禁假 0。"""
        if self._persist is None:
            return {
                **self._stats,
                "active_plans": sum(
                    1 for p in self._plans.values() if p.status in ("pending", "running")
                ),
            }
        try:
            return self._persist.counters()
        except Exception as e:
            logger.error(f"will 计数对账失败（回落内存缓存，禁假 0）: {e}")
            return {
                **self._stats,
                "active_plans": sum(
                    1 for p in self._plans.values() if p.status in ("pending", "running")
                ),
            }

    # ── F3b-⑧b 租户闸门 ─────────────────────────────────────────────
    @property
    def isolation(self) -> IsolationEngine:
        """隔离引擎（check_access 真执行点；审计日志落 NestStore=data/nest.db）。"""
        key = os.environ.get("NEST_DB") or ""
        if self._iso_cache is None or self._iso_cache[0] != key:
            self._iso_cache = (key, IsolationEngine())
        return self._iso_cache[1]

    def gate(self, tenant_id: str, row_tenant: str, plan_id: str, action: str) -> None:
        """by-ID 访问闸门：IsolationEngine.check_access 真执行（F3b-⑧b）。

        资源 ID=namespaced_id(row_tenant ns, plan_id)——namespace 前缀是行归属
        租户的 ns（审计语义与 F3b-⑧ GoalGate 同构）；跨租户（allowed=False）
        → DAGPlanAccessDenied 上抛，禁静默当 not_found/假成功。
        """
        verdict = self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.DAG_PLAN,
            resource_id=self.isolation.namespaced_id(tenant_namespace(row_tenant), plan_id),
            action=action,
        )
        if not verdict.get("allowed", False):
            raise DAGPlanAccessDenied(verdict.get("reason", "access denied"), verdict)

    @staticmethod
    def _tenant_val(tenant_id: str | None) -> str:
        """行归属键归一：缺失/空白→'default'（与生产分布一致，键派生故障绝不吃掉落库）。"""
        return (tenant_id or "").strip() or DEFAULT_TENANT

    def create_plan(
        self, goal: str, steps_spec: list[dict], tenant_id: str | None = None
    ) -> ExecutionPlan:
        """Create an execution plan from step specifications.

        F3b-⑧b：行归属租户=调用方身份派生（bind_identity），禁自报；
        缺失/空白→'default'（与生产分布一致）。
        RV-①b-⑫：先载后录（_ensure_loaded 先于入册=回载不冲在录新计划）+写穿。
        """
        self._ensure_loaded()
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"

        steps = []
        for i, spec in enumerate(steps_spec):
            step = PlanStep(
                step_id=spec.get("id", f"step_{i}"),
                description=spec.get("description", ""),
                tool_name=spec.get("tool", ""),
                arguments=spec.get("arguments", {}),
                depends_on=spec.get("depends_on", []),
                estimated_duration=spec.get("estimated_seconds", 0),
            )
            steps.append(step)

        plan = ExecutionPlan(
            plan_id=plan_id, goal=goal, steps=steps, tenant_id=self._tenant_val(tenant_id)
        )
        self._plans[plan_id] = plan
        self._stats["total_plans"] += 1
        self._stats["total_steps"] += len(steps)
        self._sync_plan(plan)

        logger.info(f"Created plan {plan_id}: {len(steps)} steps for: {goal[:50]}")
        return plan

    def create_from_llm_response(
        self, goal: str, llm_response: str, tenant_id: str | None = None
    ) -> ExecutionPlan | None:
        """Parse LLM response into an execution plan.（F3b-⑧b：tenant_id 透传行归属）"""
        # Try JSON format
        try:
            data = json.loads(llm_response)
            if isinstance(data, dict) and "steps" in data:
                return self.create_plan(goal, data["steps"], tenant_id=tenant_id)
            elif isinstance(data, list):
                return self.create_plan(goal, data, tenant_id=tenant_id)
        except json.JSONDecodeError:
            pass

        # Parse text format
        steps_spec = []
        current: dict = {}

        for line in llm_response.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith(("Step ", "步骤 ", "- Step", "1.", "2.", "3.", "4.", "5.")):
                if current:
                    steps_spec.append(current)
                current = {"id": f"step_{len(steps_spec)}", "description": line}
            elif current and ("depends" in line.lower() or "依赖" in line):
                import re

                deps = re.findall(r"step[_\s]*(\d+)", line, re.IGNORECASE)
                current["depends_on"] = [f"step_{d}" for d in deps]
            elif current:
                current["description"] += f" {line}"

        if current:
            steps_spec.append(current)

        if steps_spec:
            return self.create_plan(goal, steps_spec, tenant_id=tenant_id)

        logger.warning(f"Could not parse LLM response as plan: {llm_response[:200]}")
        return None

    def get_plan(self, plan_id: str, tenant_id: str | None = None) -> ExecutionPlan | None:
        """读计划（F3b-⑧b）：tenant_id=None=系统视图（引擎内部/维护路径）不过滤不闸；
        显式租户=by-ID 闸门真执行（跨租户→DAGPlanAccessDenied，禁静默当 not_found）。
        缺行=返回 None（404 契约不变）。
        """
        self._ensure_loaded()
        plan = self._plans.get(plan_id)
        if plan is None:
            return None
        if tenant_id is not None:
            self.gate(tenant_id, plan.tenant_id, plan_id, "read")
        return plan

    def update_step(
        self,
        plan_id: str,
        step_id: str,
        status: StepStatus,
        result: Any = None,
        error: str = "",
        tenant_id: str | None = None,
    ):
        """Update a step's status and check plan completion.

        F3b-⑧b：显式租户=by-ID 写闸门真执行（跨租户→DAGPlanAccessDenied 行零改写）；
        tenant_id=None=系统路径语义不变。
        """
        self._ensure_loaded()
        plan = self._plans.get(plan_id)
        if not plan:
            return
        if tenant_id is not None:
            self.gate(tenant_id, plan.tenant_id, plan_id, "write")

        for step in plan.steps:
            if step.step_id == step_id:
                step.status = status
                step.result = result
                step.error = error
                break

        # 终态只定一次（H1-②b-⑧ 接线前定形——修前 update_step 零消费方=无行为影响面）：
        # 修前每次 update 到全终态都重复置终态/刷 completed_at/累加计数器——步进面
        # 接线后同一终态步的幂等重放（网络重试/步进循环重复推进）会虚增
        # completed_plans/failed_plans，与 total_plans 对不上账。
        if plan.is_complete and plan.status not in ("completed", "failed"):
            plan.completed_at = time.time()
            failed = sum(1 for s in plan.steps if s.status == StepStatus.FAILED)
            if failed == 0:
                plan.status = "completed"
                self._stats["completed_plans"] += 1
            else:
                plan.status = "failed"
                self._stats["failed_plans"] += 1
        # RV-①b-⑫：步进/终态迁移写穿（写穿单一点 _sync_plan，行=当前态快照）。
        self._sync_plan(plan)

    def visualize_ascii(self, plan_id: str) -> str:
        """Render plan as ASCII DAG tree."""
        self._ensure_loaded()
        plan = self._plans.get(plan_id)
        if not plan:
            return "Plan not found"

        lines = [f"📋 Plan: {plan.goal}"]
        lines.append(f"   Status: {plan.status} | Progress: {plan.progress:.0%}")
        lines.append("")

        for step in plan.steps:
            icon = {
                StepStatus.PENDING: "⏳",
                StepStatus.READY: "🔵",
                StepStatus.RUNNING: "🔄",
                StepStatus.COMPLETED: "✅",
                StepStatus.FAILED: "❌",
                StepStatus.SKIPPED: "⏭️",
            }.get(step.status, "❓")
            deps = f" (deps: {', '.join(step.depends_on)})" if step.depends_on else ""
            lines.append(f"  {icon} {step.step_id}: {step.description[:60]}{deps}")
            if step.error:
                lines.append(f"     ⚠️ {step.error[:80]}")

        return "\n".join(lines)

    def to_mermaid(self, plan_id: str) -> str:
        """Export plan as Mermaid diagram."""
        self._ensure_loaded()
        plan = self._plans.get(plan_id)
        if not plan:
            return "graph TD\n    empty[No data]"

        lines = ["graph TD"]
        for step in plan.steps:
            icon = {
                StepStatus.PENDING: "⏳",
                StepStatus.RUNNING: "🔄",
                StepStatus.COMPLETED: "✅",
                StepStatus.FAILED: "❌",
            }.get(step.status, "")
            safe = step.description.replace('"', "'")[:40]
            lines.append(f'    {step.step_id}["{icon} {safe}"]')

        for step in plan.steps:
            for dep in step.depends_on:
                lines.append(f"    {dep} --> {step.step_id}")

        return "\n".join(lines)

    def get_stats(self) -> dict:
        # F3b-⑧b 边界：聚合计数器全局不闸（同 F3b-⑧ goals-stats 全局边界——
        # 只含计数不含计划内容；by-ID 内容面由 get_plan 闸门承担）
        # RV-①b-⑫：persist 在场恒对账行聚合（MEM-⑥ 计数真源），内存 _stats 降级
        # 缓存视图；纯内存路径键形/语义逐字不变。
        self._ensure_loaded()
        return self._counter_truth()
