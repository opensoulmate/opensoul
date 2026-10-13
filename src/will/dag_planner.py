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


class DAGPlanner:
    """Creates and manages DAG-based execution plans."""

    def __init__(self):
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
        """
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

        if plan.is_complete:
            plan.completed_at = time.time()
            failed = sum(1 for s in plan.steps if s.status == StepStatus.FAILED)
            if failed == 0:
                plan.status = "completed"
                self._stats["completed_plans"] += 1
            else:
                plan.status = "failed"
                self._stats["failed_plans"] += 1

    def visualize_ascii(self, plan_id: str) -> str:
        """Render plan as ASCII DAG tree."""
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
        return {
            **self._stats,
            "active_plans": sum(
                1 for p in self._plans.values() if p.status in ("pending", "running")
            ),
        }
