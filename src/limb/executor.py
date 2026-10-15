"""RPA Executor — manages task execution, queue, and history.

Handles:
- Task lifecycle (create, queue, execute, complete/fail)
- Execution queue with priority
- Result tracking and screenshots
- Template instantiation with variable substitution
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, StrEnum

from src.limb.tasks import (
    BUILTIN_TEMPLATES,
    Action,
    ActionType,
    StepResult,
    TaskTemplate,
    TemplateTenantDenied,
    is_builtin,
    template_row_tenant,
    template_visible_to,
)


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(int, Enum):
    LOW = 0
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


@dataclass
class RPATask:
    """A single RPA task with ordered steps."""

    task_id: str
    name: str
    actions: list[Action]
    status: TaskStatus = TaskStatus.QUEUED
    priority: int = TaskPriority.NORMAL
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    current_step: int = 0
    results: list[StepResult] = field(default_factory=list)
    error: str = ""
    tags: list[str] = field(default_factory=list)
    variables: dict = field(default_factory=dict)

    @property
    def progress(self) -> float:
        if not self.actions:
            return 100.0
        return round(self.current_step / len(self.actions) * 100, 1)

    @property
    def elapsed_seconds(self) -> float:
        if not self.started_at:
            return 0
        end = self.completed_at or time.time()
        return round(end - self.started_at, 2)

    def to_dict(self, include_results: bool = False) -> dict:
        d = {
            "task_id": self.task_id,
            "name": self.name,
            "status": self.status.value,
            "priority": self.priority,
            "progress": self.progress,
            "current_step": self.current_step,
            "total_steps": len(self.actions),
            "elapsed_seconds": self.elapsed_seconds,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "tags": self.tags,
        }
        if include_results:
            d["results"] = [
                {
                    "action_index": r.action_index,
                    "action_type": r.action_type,
                    "success": r.success,
                    "output": r.output[:500] if r.output else "",
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                }
                for r in self.results
            ]
            d["actions"] = [a.to_dict() for a in self.actions]
        return d


class RPAExecutor:
    """Execute RPA tasks with queue management."""

    def __init__(self, max_concurrent: int = 3, max_history: int = 1000):
        self._tasks: dict[str, RPATask] = {}
        self._queue: deque[str] = deque()
        self._history: list[dict] = []
        self._templates: dict[str, TaskTemplate] = {}
        self._lock = threading.Lock()
        self._max_concurrent = max_concurrent
        self._max_history = max_history
        self._running_count = 0
        self._total_executed = 0
        self._total_succeeded = 0
        self._total_failed = 0

        # Load built-in templates
        for tpl in BUILTIN_TEMPLATES:
            self._templates[tpl.template_id] = tpl

    # ── Task Management ────────────────────────────────────

    def create_task(
        self,
        name: str,
        actions: list[dict],
        priority: int = TaskPriority.NORMAL,
        tags: list[str] | None = None,
        variables: dict | None = None,
    ) -> RPATask:
        """Create a new RPA task."""
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        parsed_actions = [Action.from_dict(a) for a in actions]
        task = RPATask(
            task_id=task_id,
            name=name,
            actions=parsed_actions,
            priority=priority,
            tags=tags or [],
            variables=variables or {},
        )
        with self._lock:
            self._tasks[task_id] = task
            self._queue.append(task_id)
        return task

    def create_from_template(
        self,
        template_id: str,
        variables: dict | None = None,
        name: str = "",
        tenant_id: str | None = None,
    ) -> RPATask | None:
        """Create a task from a template with variable substitution.

        tenant_id=显式租户=实例化源读归属闸（DEC-⑦-㉒，get_template 单点闸覆盖：
        跨租户回 None 与 not-found 同契约，零副作用禁拿他人模板内容跑实例化，
        ⑦-⑫ instantiate 源读闸同款）；None=系统视图字节不变。
        """
        template = self.get_template(template_id, tenant_id=tenant_id)
        if not template:
            return None

        vars = variables or {}
        actions = []
        for action in template.actions:
            # Substitute variables in target and value
            target = self._substitute(action.target, vars)
            value = self._substitute(action.value, vars)
            actions.append(
                Action(
                    action_type=action.action_type,
                    target=target,
                    value=value,
                    description=action.description,
                    timeout=action.timeout,
                    retry=action.retry,
                    optional=action.optional,
                )
            )

        return self.create_task(
            name=name or template.name,
            actions=[a.to_dict() for a in actions],
            tags=template.tags,
            variables=vars,
        )

    def get_task(self, task_id: str) -> RPATask | None:
        return self._tasks.get(task_id)

    def list_tasks(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        with self._lock:
            tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tasks[:limit]]

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
                return False
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
            if task_id in self._queue:
                self._queue.remove(task_id)
        return True

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status == TaskStatus.RUNNING:
                return False
            self._tasks.pop(task_id, None)
        return True

    # ── Execution ──────────────────────────────────────────

    def execute_task(self, task_id: str) -> RPATask | None:
        """Execute a task synchronously (for demo/testing)."""
        task = self._tasks.get(task_id)
        if not task:
            return None
        if task.status != TaskStatus.QUEUED:
            return task

        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self._running_count += 1

        try:
            for i, action in enumerate(task.actions):
                task.current_step = i
                start = time.time()
                result = self._execute_action(action, task.variables)
                duration = int((time.time() - start) * 1000)
                result.action_index = i
                result.duration_ms = duration
                task.results.append(result)

                if not result.success and not action.optional:
                    task.status = TaskStatus.FAILED
                    task.error = result.error
                    task.completed_at = time.time()
                    self._total_failed += 1
                    self._record_history(task)
                    return task

            task.status = TaskStatus.COMPLETED
            task.current_step = len(task.actions)
            task.completed_at = time.time()
            self._total_succeeded += 1
            self._record_history(task)

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
            self._total_failed += 1
            self._record_history(task)

        finally:
            self._running_count -= 1

        return task

    def _execute_action(self, action: Action, variables: dict) -> StepResult:
        """Execute a single action (simulated for demo)."""
        # In production, this would use Playwright/Selenium
        # For now, simulate execution
        action_type = action.action_type

        if action_type == ActionType.NAVIGATE:
            return StepResult(
                action_type=action_type.value,
                success=True,
                output=f"Navigated to {action.target}",
            )
        elif action_type == ActionType.CLICK:
            return StepResult(
                action_type=action_type.value,
                success=True,
                output=f"Clicked {action.target}",
            )
        elif action_type == ActionType.TYPE:
            return StepResult(
                action_type=action_type.value,
                success=True,
                output=f"Typed '{action.value[:50]}...' into {action.target}",
            )
        elif action_type == ActionType.WAIT:
            wait_time = min(action.timeout, 2)  # Cap wait for demo
            time.sleep(wait_time)
            return StepResult(
                action_type=action_type.value,
                success=True,
                output=f"Waited {wait_time}s for {action.target or 'page load'}",
            )
        elif action_type == ActionType.SCREENSHOT:
            return StepResult(
                action_type=action_type.value,
                success=True,
                output=f"Screenshot captured of {action.target or 'full page'}",
                screenshot_path=f"/tmp/rpa_screenshot_{int(time.time())}.png",
            )
        elif action_type == ActionType.EXTRACT:
            return StepResult(
                action_type=action_type.value,
                success=True,
                output=f"Extracted data from {action.target}",
            )
        elif action_type == ActionType.SELECT:
            return StepResult(
                action_type=action_type.value,
                success=True,
                output=f"Selected '{action.value}' in {action.target}",
            )
        elif action_type == ActionType.SCROLL:
            return StepResult(
                action_type=action_type.value,
                success=True,
                output=f"Scrolled {action.value or 'down'}",
            )
        elif action_type == ActionType.KEY_PRESS:
            return StepResult(
                action_type=action_type.value,
                success=True,
                output=f"Pressed key: {action.value}",
            )
        elif action_type == ActionType.SUBMIT:
            return StepResult(
                action_type=action_type.value,
                success=True,
                output=f"Submitted form {action.target}",
            )
        else:
            return StepResult(
                action_type=action_type.value,
                success=True,
                output=f"Executed custom action: {action.description}",
            )

    # ── Templates ──────────────────────────────────────────

    def list_templates(
        self, category: str | None = None, tenant_id: str | None = None
    ) -> list[dict]:
        """tenant_id=显式租户=恒按 template_visible_to 过滤（builtin 共享目录全
        租户可见+其余行按行归属，DEC-⑩-①）；None=系统视图字节不变。"""
        templates = list(self._templates.values())
        if tenant_id is not None:
            templates = [t for t in templates if template_visible_to(t, tenant_id)]
        if category:
            templates = [t for t in templates if t.category == category]
        return [t.to_dict() for t in templates]

    def get_template(self, template_id: str, tenant_id: str | None = None) -> TaskTemplate | None:
        """by-ID 读唯一入口=归属闸单点（DEC-⑦-㉒，⑦-⑩ get_meta / ⑦-⑫
        get_template 单点闸同款）：显式租户=跨租户回 None 与 not-found 同契约
        不差分泄露行存在性（③c-1 纪律），builtin=系统共享目录全租户可见
        （DEC-⑩-①）；None=系统视图字节不变。实例化源读（create_from_template）
        全经本口=未来消费方无法绕闸。"""
        template = self._templates.get(template_id)
        if template is None:
            return None
        if tenant_id is not None and not template_visible_to(template, tenant_id):
            return None
        return template

    def create_template(self, data: dict, tenant_id: str | None = None) -> TaskTemplate:
        """写点行归属（DEC-⑦-㉒）：tenant_id 落行（缺失/空白→'default' 兜底=
        系统写方，键派生故障绝不吃掉落库）。写面语义：builtin=系统共享只读目录
        （共享≠可写，⑦-㉑ builtin 改/删恒拒同款）；行归属不可被跨租户改写
        （⑦-③ 创建劫持闸同款）——显式租户覆盖他租户行=TemplateTenantDenied
        行零改写；None=系统视图字节不变。"""
        template_id = data.get("template_id") or f"tpl-{uuid.uuid4().hex[:8]}"
        existing = self._templates.get(template_id)
        if existing is not None:
            if is_builtin(template_id):
                raise TemplateTenantDenied(f"built-in template '{template_id}' is shared read-only")
            if tenant_id is not None and template_row_tenant(existing) != template_row_tenant(
                tenant_id
            ):
                raise TemplateTenantDenied(f"template '{template_id}' belongs to another tenant")
        actions = [Action.from_dict(a) for a in data.get("actions", [])]
        template = TaskTemplate(
            template_id=template_id,
            name=data["name"],
            description=data.get("description", ""),
            category=data.get("category", "custom"),
            actions=actions,
            variables=data.get("variables", []),
            tags=data.get("tags", []),
            tenant_id=template_row_tenant(tenant_id),
        )
        self._templates[template_id] = template
        return template

    def delete_template(self, template_id: str, tenant_id: str | None = None) -> bool:
        # Don't allow deleting built-in templates（builtin 判法单一源 is_builtin
        # 禁第二套名单手抄 F4b-㉗；builtin 恒拒=共享≠可写 DEC-⑦-㉑ 同款）
        if is_builtin(template_id):
            return False
        existing = self._templates.get(template_id)
        if existing is None:
            return False
        # 写面恒按行归属（template_visible_to 不适用——共享≠可写）；跨租户=与
        # not-found 同契约（同回 False→同响应）不差分泄露行存在性（③c-1 纪律）
        if tenant_id is not None and template_row_tenant(existing) != template_row_tenant(
            tenant_id
        ):
            return False
        return self._templates.pop(template_id, None) is not None

    # ── Stats ──────────────────────────────────────────────

    def stats(self, tenant_id: str | None = None) -> dict:
        """tenant_id=显式租户=templates 计数恒按可见集聚合（禁泄露全局分布，
        ⑦-⑭⑰ 同款）；其余键为任务面计数（任务面无行租户轴不在本拍）；
        None=系统视图字节不变（/health 监控面）。"""
        with self._lock:
            by_status = {}
            for t in self._tasks.values():
                by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
            return {
                "total_tasks": len(self._tasks),
                "by_status": by_status,
                "queue_length": len(self._queue),
                "running": self._running_count,
                "total_executed": self._total_executed,
                "total_succeeded": self._total_succeeded,
                "total_failed": self._total_failed,
                "success_rate": round(self._total_succeeded / self._total_executed * 100, 1)
                if self._total_executed
                else 0,
                "templates": sum(
                    1
                    for t in self._templates.values()
                    if tenant_id is None or template_visible_to(t, tenant_id)
                ),
            }

    def get_history(self, limit: int = 50) -> list[dict]:
        return self._history[-limit:]

    # ── Helpers ────────────────────────────────────────────

    def _record_history(self, task: RPATask):
        self._total_executed += 1
        self._history.append(
            {
                "task_id": task.task_id,
                "name": task.name,
                "status": task.status.value,
                "steps": len(task.results),
                "elapsed_seconds": task.elapsed_seconds,
                "error": task.error,
                "completed_at": task.completed_at,
            }
        )
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

    @staticmethod
    def _substitute(text: str, variables: dict) -> str:
        """Replace {{var}} placeholders with variable values."""

        def replace(match):
            key = match.group(1).strip()
            return str(variables.get(key, match.group(0)))

        return re.sub(r"\{\{(\w+)\}\}", replace, text)
