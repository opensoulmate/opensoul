"""Goal Autonomous Runner — 自主目标循环（kilocode goal/runner.ts移植）。

参照：kilocode-source-supplement2.md 核心发现A（goal/runner.ts 492行）。
五状态机 + 事件驱动结果判定 + 持久化 + 失败即停 + 用户抢占不打断 + 准入检查。

设计原则：
- "完成"不靠模型说，靠动作序列判定（事件驱动outcome）
- goal_report是自报协议，不是独立验证
- 失败即停：无进展/报错→自动paused，需人工审查后恢复
- 用户消息抢占不打断goal：paused不是终态，可自动续跑
- 带着未决审批开自主循环=事故 → 准入前置检查
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from src.nest.isolation import IsolationEngine, ResourceType
from src.nest.tenant import tenant_namespace

from .checkpoint import DEFAULT_TENANT

logger = logging.getLogger(__name__)


class GoalAccessDenied(Exception):
    """跨租户 goal 面访问被 IsolationEngine 拒绝（API 层映射 403，禁静默当 not_found）。

    F3b-⑧（2026-10-13）：与 WorkflowAccessDenied/CheckpointAccessDenied 同族——
    verdict=check_access 原始回包（allowed/reason/namespaced_id），审计留痕在
    IsolationEngine 审计日志（data/nest.db）。
    """

    def __init__(self, reason: str, verdict: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.verdict = verdict or {}


class GoalState(StrEnum):
    """五状态机（kilocode goal/state.ts）"""

    ACTIVE = "active"  # 正在自主循环
    PAUSED = "paused"  # 暂停（用户抢占/失败即停/手动暂停）— 非终态
    BLOCKED = "blocked"  # 被阻塞（agent自报blocked，需人工介入）
    COMPLETED = "completed"  # 已完成（事件驱动判定或goal_report自报）
    FAILED = "failed"  # 已失败（超时/重试耗尽）


class GoalOutcome(StrEnum):
    """事件驱动结果四态（kilocode outcome() 100行）"""

    SUCCESS = "success"  # 工具执行成功
    FAILED = "failed"  # 工具执行失败（bash exit≠0等）
    BLOCKED = "blocked"  # 被阻塞（plan_exit、权限拒绝等）
    NONE = "none"  # 不计分（question/suggest/todo等）


class GoalEvent(BaseModel):
    """Goal循环中的一次事件记录"""

    event_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    tool_name: str = ""
    outcome: GoalOutcome = GoalOutcome.NONE
    detail: str = ""
    raw_data: dict[str, Any] = Field(default_factory=dict)


class Goal(BaseModel):
    """一个自主目标"""

    goal_id: str = Field(default_factory=lambda: f"goal_{uuid4().hex[:12]}")
    session_id: str = ""  # 所属会话
    tenant_id: str = DEFAULT_TENANT  # F3b-⑧：行归属租户（create=bind_identity 派生禁自报）
    description: str = ""  # 目标描述
    state: GoalState = GoalState.ACTIVE
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    # 事件驱动计数
    events: list[GoalEvent] = Field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    blocked_count: int = 0
    none_count: int = 0
    # goal_report自报
    report_status: str | None = None  # "complete" | "blocked" | None
    report_reason: str | None = None
    # 状态变更原因（审计）
    state_history: list[dict[str, str]] = Field(default_factory=list)
    # 用户抢占标记
    superseded_count: int = 0
    # 轮次计数
    loop_count: int = 0
    max_loops: int = 50  # 安全上限

    @property
    def outcome_summary(self) -> dict[str, int]:
        return {
            "success": self.success_count,
            "failed": self.failure_count,
            "blocked": self.blocked_count,
            "none": self.none_count,
            "total": len(self.events),
        }

    @property
    def is_terminal(self) -> bool:
        return self.state in (GoalState.COMPLETED, GoalState.FAILED)

    @property
    def is_resumable(self) -> bool:
        return self.state in (GoalState.PAUSED, GoalState.BLOCKED)


class GoalCreate(BaseModel):
    session_id: str = ""
    description: str
    max_loops: int = 50


class GoalReport(BaseModel):
    """goal_report自报协议 — "这是你的报告，不是独立验证"（kilocode tool.ts）"""

    goal_id: str
    status: str  # "complete" | "blocked"
    reason: str = ""


class GoalRunner:
    """Goal自主目标循环引擎。

    核心循环：启动goal → agent执行动作 → 事件分类计数 → 判定是否继续
    判定规则（kilocode outcome()）：
      - completed = success > failure AND 全部finish=="stop"
      - bash exit≠0 = failed
      - question/suggest/todo = none不计分
      - plan_exit = blocked
    失败即停：无成功动作/显式报错 → auto-paused
    """

    _PERSIST_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "will_goals.json"

    # F3b-⑧：数据面咨询 IsolationEngine（懒构造同 WorkflowEngine 纪律——首个 gate
    # 调用才建 NestStore，系统路径（tenant_id=None）零生产库接触）。类级缺省保证
    # __new__ 旁路构造的实例（既有测试面）不炸。
    _isolation: IsolationEngine | None = None

    def __init__(self) -> None:
        self._goals: dict[str, Goal] = {}
        self._load()

    # ── F3b-⑧ 租户闸门 ─────────────────────────────────────────────
    @property
    def isolation(self) -> IsolationEngine:
        """隔离引擎（check_access 真执行点；审计日志落 NestStore=data/nest.db）。"""
        if self._isolation is None:
            self._isolation = IsolationEngine()
        return self._isolation

    def gate(self, tenant_id: str, row_tenant: str, resource_id: str, action: str) -> None:
        """by-ID 访问闸门：IsolationEngine.check_access 真执行（F3b-⑧）。

        资源 ID=namespaced_id(row_tenant ns, goal_id)——namespace 前缀是行归属
        租户的 ns（审计语义与 F3b-⑦ WorkflowGate 同构）；跨租户（allowed=False）
        → GoalAccessDenied 上抛，禁静默当 not_found/假成功。
        """
        verdict = self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.GOAL,
            resource_id=self.isolation.namespaced_id(tenant_namespace(row_tenant), resource_id),
            action=action,
        )
        if not verdict.get("allowed", False):
            raise GoalAccessDenied(verdict.get("reason", "access denied"), verdict)

    def gate_bulk(self, tenant_id: str, action: str) -> None:
        """bulk 操作（list）审计留痕：执行隔离由查询过滤承担，
        引擎侧记一次 namespace 自匹配访问（audit_access="Log all access attempts"）。"""
        self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.GOAL,
            resource_id=self.isolation.namespaced_id(tenant_namespace(tenant_id), "*"),
            action=action,
        )

    @staticmethod
    def _tenant_val(tenant_id: str | None) -> str:
        """行归属键归一：缺失/空白→'default'（与生产分布一致，键派生故障绝不吃掉落库）。"""
        return (tenant_id or "").strip() or DEFAULT_TENANT

    # ── Persistence ─────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if self._PERSIST_PATH.exists():
                data = json.loads(self._PERSIST_PATH.read_text(encoding="utf-8"))
                for gdata in data.get("goals", []):
                    try:
                        goal = Goal(**gdata)
                        # F3b-⑧：旧持久化行缺 tenant_id/空白→'default'（与生产分布一致）
                        goal.tenant_id = self._tenant_val(getattr(goal, "tenant_id", None))
                        self._goals[goal.goal_id] = goal
                    except Exception:
                        continue
        except Exception:
            pass

    def _save(self) -> None:
        try:
            self._PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {"goals": [g.model_dump(mode="json") for g in self._goals.values()]}
            self._PERSIST_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception:
            pass

    # ── Admission Check（准入前置检查） ──────────────────────────

    def admit(self, session_id: str, tenant_id: str | None = None) -> tuple[bool, str]:
        """启动goal前的准入检查（kilocode admit()）。

        检查项：
        1. 该session没有其他ACTIVE状态的goal（同一时间只跑一个）
        2. 该session没有BLOCKED状态的goal（需先处理阻塞）

        F3b-⑧ 语义收口：tenant_id 给定时只看同租户的 goal（session 键按租户隔离，
        与 F3b-③a 幂等键按租户隔离同款）——防同名 session 被他租户的 goal 误拦；
        tenant_id=None=既有全局语义不变（既有测试/系统路径契约）。
        """
        tenant = self._tenant_val(tenant_id) if tenant_id is not None else None
        for goal in self._goals.values():
            if tenant is not None and goal.tenant_id != tenant:
                continue
            if goal.session_id == session_id:
                if goal.state == GoalState.ACTIVE:
                    return False, f"Session {session_id} already has an active goal: {goal.goal_id}"
                if goal.state == GoalState.BLOCKED:
                    return (
                        False,
                        f"Session {session_id} has a blocked goal: {goal.goal_id}. Resolve it first.",
                    )
        return True, "ok"

    # ── CRUD ────────────────────────────────────────────────────

    def create_goal(self, req: GoalCreate, tenant_id: str | None = None) -> Goal | None:
        """创建新目标，先过准入检查。F3b-⑧：行归属=identity 派生租户（禁自报）。"""
        tenant = self._tenant_val(tenant_id)
        ok, reason = self.admit(req.session_id, tenant_id=tenant)
        if not ok:
            logger.warning("Goal admission denied: %s", reason)
            return None

        goal = Goal(
            session_id=req.session_id,
            tenant_id=tenant,
            description=req.description,
            max_loops=req.max_loops,
            started_at=datetime.now(UTC).isoformat(),
        )
        goal.state_history.append(
            {
                "state": GoalState.ACTIVE.value,
                "reason": "Goal created and started",
                "timestamp": goal.created_at,
            }
        )
        self._goals[goal.goal_id] = goal
        self._save()
        logger.info("Goal %s created: %s", goal.goal_id, goal.description)
        return goal

    def get_goal(self, goal_id: str, tenant_id: str | None = None) -> Goal | None:
        goal = self._goals.get(goal_id)
        if goal is not None and tenant_id is not None:
            self.gate(self._tenant_val(tenant_id), goal.tenant_id, goal_id, "read")
        return goal

    def list_goals(
        self,
        session_id: str | None = None,
        state: GoalState | None = None,
        tenant_id: str | None = None,
    ) -> list[Goal]:
        goals = list(self._goals.values())
        if tenant_id is not None:
            tenant = self._tenant_val(tenant_id)
            self.gate_bulk(tenant, "list")
            goals = [g for g in goals if g.tenant_id == tenant]
        if session_id:
            goals = [g for g in goals if g.session_id == session_id]
        if state:
            goals = [g for g in goals if g.state == state]
        return sorted(goals, key=lambda g: g.updated_at, reverse=True)

    def delete_goal(self, goal_id: str, tenant_id: str | None = None) -> bool:
        goal = self._goals.get(goal_id)
        if goal is None:
            return False
        if tenant_id is not None:
            self.gate(self._tenant_val(tenant_id), goal.tenant_id, goal_id, "delete")
        del self._goals[goal_id]
        self._save()
        return True

    # ── Event Recording（事件驱动结果判定） ──────────────────────

    def classify_tool_outcome(self, tool_name: str, result: dict[str, Any]) -> GoalOutcome:
        """对工具执行结果分类（kilocode outcome() ~100行的Python移植）。

        分类规则：
        - question/suggest/todo/board_post → NONE（不计分）
        - plan_exit → BLOCKED
        - bash/script类：exit_code != 0 → FAILED
        - 一般工具：有error字段 → FAILED，否则SUCCESS
        """
        # 不计分工具
        none_tools = {"question", "suggest", "todo", "board_post", "board_read", "notice"}
        if tool_name in none_tools:
            return GoalOutcome.NONE

        # 阻塞类
        if tool_name in ("plan_exit", "permission_denied"):
            return GoalOutcome.BLOCKED

        # bash/script类：exit_code判定
        if tool_name in ("bash", "script", "execute", "run_command"):
            exit_code = result.get("exit_code", result.get("returncode", 0))
            if exit_code != 0:
                return GoalOutcome.FAILED
            return GoalOutcome.SUCCESS

        # 通用工具：error字段判定
        if result.get("error") or result.get("success") is False:
            return GoalOutcome.FAILED

        return GoalOutcome.SUCCESS

    def record_event(
        self,
        goal_id: str,
        tool_name: str,
        result: dict[str, Any],
        detail: str = "",
        tenant_id: str | None = None,
    ) -> GoalEvent | None:
        """记录一次工具执行事件，更新计数，检查是否触发状态转换。"""
        goal = self._goals.get(goal_id)
        if not goal:
            return None
        if tenant_id is not None:
            # F3b-⑧：闸门前置于状态检查（跨租户恒拒，禁终态差分泄露他租户 goal 状态）
            self.gate(self._tenant_val(tenant_id), goal.tenant_id, goal_id, "write")
        if goal.is_terminal:
            logger.debug("Goal %s is terminal, ignoring event", goal_id)
            return None

        outcome = self.classify_tool_outcome(tool_name, result)
        event = GoalEvent(
            tool_name=tool_name,
            outcome=outcome,
            detail=detail,
            raw_data={k: str(v)[:200] for k, v in result.items()},
        )
        goal.events.append(event)

        # 更新计数
        if outcome == GoalOutcome.SUCCESS:
            goal.success_count += 1
        elif outcome == GoalOutcome.FAILED:
            goal.failure_count += 1
        elif outcome == GoalOutcome.BLOCKED:
            goal.blocked_count += 1
        else:
            goal.none_count += 1

        goal.updated_at = datetime.now(UTC).isoformat()

        # 事件驱动结果判定
        self._evaluate_state(goal)

        self._save()
        return event

    def _evaluate_state(self, goal: Goal) -> None:
        """事件驱动状态评估（kilocode outcome()判定逻辑）。

        规则：
        1. blocked事件出现 → BLOCKED（需人工介入）
        2. 连续失败（最近3个计分事件都是failed）→ PAUSED（失败即停）
        3. loop_count超限 → FAILED
        4. success > failure 且有success事件 → 保持ACTIVE（等待goal_report或继续）
        """
        if goal.state != GoalState.ACTIVE:
            return

        # Rule 1: blocked事件 → BLOCKED
        if goal.blocked_count > 0:
            self._transition(goal, GoalState.BLOCKED, "Blocked event detected — needs human review")
            return

        # Rule 2: 连续失败即停（最近3个计分事件全是failed）
        scored_events = [e for e in goal.events if e.outcome != GoalOutcome.NONE]
        if len(scored_events) >= 3:
            recent = scored_events[-3:]
            if all(e.outcome == GoalOutcome.FAILED for e in recent):
                self._transition(
                    goal,
                    GoalState.PAUSED,
                    "Fail-stop: 3 consecutive failures. Review before resuming.",
                )
                return

        # Rule 3: loop上限
        if goal.loop_count >= goal.max_loops:
            self._transition(goal, GoalState.FAILED, f"Max loops ({goal.max_loops}) exceeded")
            return

        # Rule 4: 零进展检查 — 20个事件后无任何success → PAUSED
        if len(scored_events) >= 20 and goal.success_count == 0:
            self._transition(
                goal,
                GoalState.PAUSED,
                "No progress: 20+ events with zero success. Review before resuming.",
            )
            return

    # ── goal_report自报协议 ─────────────────────────────────────

    def goal_report(self, req: GoalReport, tenant_id: str | None = None) -> Goal | None:
        """处理goal_report自报（kilocode tool.ts）。

        注意：这是agent的自报，不是独立验证。
        但自报complete且事件驱动也支持时，才真正COMPLETED。
        自报blocked → BLOCKED状态。
        """
        goal = self._goals.get(req.goal_id)
        if not goal:
            return None
        if tenant_id is not None:
            self.gate(self._tenant_val(tenant_id), goal.tenant_id, req.goal_id, "write")
        if goal.is_terminal:
            return goal

        goal.report_status = req.status
        goal.report_reason = req.reason
        goal.updated_at = datetime.now(UTC).isoformat()

        if req.status == "complete":
            # 事件驱动验证：success > failure 或者至少有success事件
            if goal.success_count > goal.failure_count or goal.success_count > 0:
                self._transition(goal, GoalState.COMPLETED, f"Goal report: complete — {req.reason}")
            else:
                # 自报完成但事件不支持 → 降级为PAUSED
                self._transition(
                    goal,
                    GoalState.PAUSED,
                    f"Self-reported complete but no success evidence. Review: {req.reason}",
                )
        elif req.status == "blocked":
            self._transition(goal, GoalState.BLOCKED, f"Goal report: blocked — {req.reason}")

        self._save()
        return goal

    # ── State Transitions ───────────────────────────────────────

    def pause_goal(
        self, goal_id: str, reason: str = "User paused", tenant_id: str | None = None
    ) -> Goal | None:
        """暂停goal（用户抢占/手动暂停）。PAUSED不是终态。"""
        goal = self._goals.get(goal_id)
        if not goal:
            return None
        if tenant_id is not None:
            self.gate(self._tenant_val(tenant_id), goal.tenant_id, goal_id, "write")
        if goal.is_terminal:
            return goal
        if goal.state == GoalState.ACTIVE:
            goal.superseded_count += 1
            self._transition(goal, GoalState.PAUSED, reason)
        self._save()
        return goal

    def resume_goal(
        self, goal_id: str, reason: str = "User resumed", tenant_id: str | None = None
    ) -> Goal | None:
        """恢复goal（从PAUSED/BLOCKED回到ACTIVE）。

        准入检查：只有PAUSED可直接恢复，BLOCKED需先clear blocked原因。
        """
        goal = self._goals.get(goal_id)
        if not goal:
            return None
        if tenant_id is not None:
            self.gate(self._tenant_val(tenant_id), goal.tenant_id, goal_id, "write")
        if goal.is_terminal:
            return goal
        if goal.state == GoalState.PAUSED:
            self._transition(goal, GoalState.ACTIVE, reason)
        elif goal.state == GoalState.BLOCKED:
            # BLOCKED需要goal_report明确clear后才能恢复
            return None
        self._save()
        return goal

    def cancel_goal(
        self, goal_id: str, reason: str = "User cancelled", tenant_id: str | None = None
    ) -> Goal | None:
        """取消goal → FAILED终态。"""
        goal = self._goals.get(goal_id)
        if not goal:
            return None
        if tenant_id is not None:
            # 闸门前置（③c-1 cancel 纪律）：跨租户恒拒，禁终态差分泄露
            self.gate(self._tenant_val(tenant_id), goal.tenant_id, goal_id, "delete")
        if not goal.is_terminal:
            self._transition(goal, GoalState.FAILED, reason)
        self._save()
        return goal

    def record_loop(self, goal_id: str) -> Goal | None:
        """记录一次自主循环迭代，自动检查是否该停止。"""
        goal = self._goals.get(goal_id)
        if not goal:
            return None
        if goal.state != GoalState.ACTIVE:
            return goal
        goal.loop_count += 1
        goal.updated_at = datetime.now(UTC).isoformat()
        self._evaluate_state(goal)
        self._save()
        return goal

    def _transition(self, goal: Goal, new_state: GoalState, reason: str) -> None:
        """状态转换 + 审计记录。"""
        old_state = goal.state
        goal.state = new_state
        goal.updated_at = datetime.now(UTC).isoformat()
        if new_state in (GoalState.COMPLETED, GoalState.FAILED, GoalState.BLOCKED):
            goal.completed_at = goal.updated_at
        goal.state_history.append(
            {
                "from": old_state.value,
                "state": new_state.value,
                "reason": reason,
                "timestamp": goal.updated_at,
            }
        )
        logger.info("Goal %s: %s → %s (%s)", goal.goal_id, old_state.value, new_state.value, reason)

    # ── Stats ───────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Goal runner统计信息（用于monitoring面板）。"""
        states: dict[str, int] = {}
        for goal in self._goals.values():
            s = goal.state.value
            states[s] = states.get(s, 0) + 1
        active_goals = [
            {
                "goal_id": g.goal_id,
                "session_id": g.session_id,
                "description": g.description[:100],
                "state": g.state.value,
                "loop_count": g.loop_count,
                "outcome": g.outcome_summary,
                "created_at": g.created_at,
            }
            for g in self._goals.values()
            if g.state == GoalState.ACTIVE
        ]
        return {
            "total_goals": len(self._goals),
            "by_state": states,
            "active_goals": active_goals,
        }


# ── Module-level singleton ──────────────────────────────────────

_runner: GoalRunner | None = None


def get_goal_runner() -> GoalRunner:
    global _runner
    if _runner is None:
        _runner = GoalRunner()
    return _runner
