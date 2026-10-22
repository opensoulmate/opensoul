"""RPA Executor — manages task execution, queue, and history.

Handles:
- Task lifecycle (create, queue, execute, complete/fail)
- Execution queue with priority
- Result tracking and screenshots
- Template instantiation with variable substitution
"""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger(__name__)


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


def task_row_tenant(raw: object) -> str:
    """任务行租户键单一判法（DEC-⑦-㉔；DEC-⑦-② message_row_tenant /
    ⑦-㉒ template_row_tenant / ⑦-⑳ v2_event_row_tenant 同款语义：自带键
    缺失/空白→'default' 兜底，键派生故障绝不吃掉落库）。raw=RPATask 行对象
    （读侧过滤）/历史 dict 行/原始租户值（写侧落行）三形同收，写读同一判法
    禁双源漂移（F4b-㉗ 纪律）。"""
    if isinstance(raw, dict):
        raw = raw.get("tenant_id")
    elif hasattr(raw, "tenant_id"):
        raw = getattr(raw, "tenant_id")
    return str(raw or "").strip() or "default"


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
    tenant_id: str = "default"

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
            # 加性键（DEC-⑦-㉔）：行归属可观测（归一值）
            "tenant_id": task_row_tenant(self),
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

    def to_state(self) -> dict:
        """任务行持久化状态（RV-①b-⑦-1）：完整往返形。to_dict 是 API 展示形
        （results 输出截断 500 丢 screenshot_path、不带 variables），落盘/回载需
        逐字段保真——刻意独立单一源，禁拿展示形当落盘形（回载缺字段=fabricate）。"""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "status": self.status.value,
            "priority": int(self.priority),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_step": int(self.current_step),
            "error": self.error,
            "tags": list(self.tags),
            "variables": dict(self.variables),
            "tenant_id": task_row_tenant(self),
            "actions": [
                {**a.to_dict(), "condition": a.condition, "metadata": dict(a.metadata)}
                for a in self.actions
            ],
            "results": [
                {
                    "action_type": r.action_type,
                    "success": bool(r.success),
                    "action_index": int(r.action_index),
                    "output": r.output,
                    "error": r.error,
                    "duration_ms": int(r.duration_ms),
                    "screenshot_path": r.screenshot_path,
                }
                for r in self.results
            ],
        }

    @classmethod
    def from_state(cls, data: dict) -> RPATask:
        """从落盘状态重建任务行（to_state 唯一逆）。字段缺失/非法如实抛，
        行取舍归回载方（跳过+可见）。"""
        actions = [Action.from_dict(a) for a in data.get("actions", [])]
        results = [StepResult(**r) for r in data.get("results", [])]
        return cls(
            task_id=str(data["task_id"]),
            name=str(data.get("name", "")),
            actions=actions,
            status=TaskStatus(data.get("status", "queued")),
            priority=int(data.get("priority", 5)),
            created_at=float(data.get("created_at") or time.time()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            current_step=int(data.get("current_step", 0)),
            results=results,
            error=str(data.get("error", "")),
            tags=list(data.get("tags", [])),
            variables=dict(data.get("variables", {})),
            tenant_id=task_row_tenant(data.get("tenant_id")),
        )


class RPAExecutor:
    """Execute RPA tasks with queue management."""

    def __init__(
        self,
        max_concurrent: int = 3,
        max_history: int = 1000,
        persist=None,
        history_persist=None,
        template_persist=None,
    ):
        self._tasks: dict[str, RPATask] = {}
        self._queue: deque[str] = deque()
        self._history: list[dict] = []
        self._templates: dict[str, TaskTemplate] = {}
        self._lock = threading.Lock()
        self._max_concurrent = max_concurrent
        self._max_history = max_history
        self._running_count = 0
        self._tenant_running: dict[str, int] = {}
        self._tenant_counters: dict[str, dict[str, int]] = {}
        self._total_executed = 0
        self._total_succeeded = 0
        self._total_failed = 0
        # 任务行持久化可选参（RV-①b-⑦-1，⑥-2 同款）：缺省 None=纯内存既有语义
        # 零变化；在场=状态变更写穿+首触懒回载，重启任务不丢。
        self._persist = persist
        self._loaded = False
        # 执行历史持久化可选参（RV-①b-⑦-2）：缺省 None=纯内存既有语义零变化；
        # 在场=历史行追加写穿+首触懒回载+计数读面对账行真源，重启执行史/计数不丢。
        self._history_persist = history_persist
        self._history_loaded = False
        # 用户模板持久化可选参（RV-①b-⑦-3）：缺省 None=纯内存既有语义零变化；
        # 在场=模板变更写穿+首触懒回载，重启用户模板不丢（builtin 种子=代码真源
        # 不进表，回载只补缺不覆盖）。
        self._template_persist = template_persist
        self._templates_loaded = False

        # Load built-in templates
        for tpl in BUILTIN_TEMPLATES:
            self._templates[tpl.template_id] = tpl

    # ── Persistence（RV-①b-⑦-1）──────────────────────────

    def _sync_task(self, task: RPATask) -> None:
        """任务状态写穿单一点（RV-①b-② _sync_entry 纪律：禁散落写点漂移）。

        全部任务变更点（建任务/开始执行/终态/取消）共用本口落盘。写失败=记账
        旁路纪律（RV-①b-①）：logger.error 可见不阻断任务本体。
        """
        if self._persist is None:
            return
        try:
            self._persist.upsert(
                task_id=str(task.task_id),
                status=str(task.status.value),
                payload=json.dumps(task.to_state(), ensure_ascii=False),
            )
        except Exception as e:
            logger.error(f"limb task 落盘失败（任务本体不受阻）: {e}")

    def _drop_task(self, task_id: str) -> None:
        """删行单一点（delete_task 真消费；幂等缺行不抛）。"""
        if self._persist is None:
            return
        try:
            self._persist.delete(task_id)
        except Exception as e:
            logger.error(f"limb task 删行失败（任务本体不受阻）: {e}")

    def _sync_history(self, entry: dict) -> None:
        """执行历史追加写穿单一点（RV-①b-⑦-2，_sync_task 纪律：禁散落写点漂移）。

        追加型历史表（echo_history 同纪律）：每次执行恰一行 INSERT 禁覆盖=禁丢
        史（同任务多次执行多行如实各计）。写失败=记账旁路纪律（RV-①b-①）：
        logger.error 可见不阻断执行本体。
        """
        if self._history_persist is None:
            return
        try:
            self._history_persist.insert(**entry)
        except Exception as e:
            logger.error(f"limb 执行历史落盘失败（执行本体不受阻）: {e}")

    def _ensure_history_loaded(self) -> None:
        """执行历史首触懒回载（RV-①b-⑦-2，⑦-1 _ensure_tasks_loaded 同款）：
        persist 在场才回载，只回载一次；首触=首次读（get_history/stats）或首次写
        （_record_history 先回载再追加——回载替换内存窗，先载后录防在录条目被
        回载冲掉；首触先于任何追加=回载时内存窗恒空，生产不变量）。

        回载窗=最近 max_history 行（内存 _history 既有 1000 裁剪语义不变，更旧行
        留在库不进内存——echo RV-①b-④ ③ 同语义）。计数不随窗裁剪：计数真源=
        全表行聚合（_counter_truth 恒对账 counters()，内存计数器降级为缓存视图
        MEM-⑥ 纪律）。回载失败=空历史起步+logger.error 可见（诚实边界②）。
        """
        if self._history_persist is None or self._history_loaded:
            return
        self._history_loaded = True
        try:
            rows = self._history_persist.load_recent(self._max_history)
        except Exception as e:
            logger.error(f"limb 执行历史回载失败（空历史起步）: {e}")
            return
        self._history = rows[-self._max_history :]

    def _counter_truth(self) -> dict:
        """计数真源（RV-①b-⑦-2「计数真源化」）：persist 在场=恒对账执行历史行
        聚合（MEM-⑥ 纪律：计数器=行缓存视图，值读面读真源禁信第二套聚合列）；
        缺省=纯内存计数器既有语义字节不变。对账失败=logger.error 可见+回落内存
        计数缓存（缓存本职，禁静默假 0）。

        返回形状：{"executed","succeeded","failed","by_tenant":{key:{...}}}
        与 counters() 逐键一致（单一形状禁两套）。
        """
        if self._history_persist is None:
            return {
                "executed": self._total_executed,
                "succeeded": self._total_succeeded,
                "failed": self._total_failed,
                "by_tenant": {k: dict(v) for k, v in self._tenant_counters.items()},
            }
        try:
            return self._history_persist.counters()
        except Exception as e:
            logger.error(f"limb 执行历史计数对账失败（回落内存计数缓存）: {e}")
            return {
                "executed": self._total_executed,
                "succeeded": self._total_succeeded,
                "failed": self._total_failed,
                "by_tenant": {k: dict(v) for k, v in self._tenant_counters.items()},
            }

    def _ensure_tasks_loaded(self) -> None:
        """首触懒回载（RV-①b-⑥-2 同款）：persist 在场才回载，只回载一次。

        重启后 worker 不在：在途态（running）执行已断——如实降 failed 带真因
        （「执行被服务重启打断」），禁留 running 空壳=永远等不到 worker 的僵死
        任务（⑥-2 在途态降 paused 同纪律，本执行器无续传口故终态化可见）。回载
        只读不写（转换只改内存，状态回写随下一次变更点写穿）。

        _queue 重建=行状态非 cancelled 的任务 id 按回载序（忠实还原既有内存语义：
        创建即入队、执行不退队、取消才退队——既有怪癖本拍不改语义，留档另拍；
        已删任务的 _queue 孤儿 id 无法从行重建=回载面如实少掉不 fabricate）。
        """
        if self._persist is None or self._loaded:
            return
        self._loaded = True
        try:
            rows = self._persist.load_all()
        except Exception as e:
            logger.error(f"limb task 回载失败（空任务起步）: {e}")
            return
        for data in rows:
            try:
                task = RPATask.from_state(data)
            except Exception as e:
                logger.error(f"limb task 行不可解跳过: {e}")
                continue
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.FAILED
                task.error = task.error or "interrupted by service restart"
                if task.completed_at is None:
                    task.completed_at = time.time()
            self._tasks.setdefault(task.task_id, task)
            if task.status != TaskStatus.CANCELLED and task.task_id not in self._queue:
                self._queue.append(task.task_id)

    def _sync_template(self, template: TaskTemplate) -> None:
        """用户模板写穿单一点（RV-①b-⑦-3，_sync_task 纪律：禁散落写点漂移）。

        create_template 建/覆盖模板共用本口落盘。写失败=记账旁路纪律
        （RV-①b-①）：logger.error 可见不阻断模板本体。
        """
        if self._template_persist is None:
            return
        try:
            self._template_persist.upsert(
                template_id=str(template.template_id),
                tenant_id=template_row_tenant(template),
                category=str(template.category),
                payload=json.dumps(template.to_state(), ensure_ascii=False),
            )
        except Exception as e:
            logger.error(f"limb template 落盘失败（模板本体不受阻）: {e}")

    def _drop_template(self, template_id: str) -> None:
        """删行单一点（delete_template 真消费；幂等缺行不抛）。"""
        if self._template_persist is None:
            return
        try:
            self._template_persist.delete(template_id)
        except Exception as e:
            logger.error(f"limb template 删行失败（模板本体不受阻）: {e}")

    def _ensure_templates_loaded(self) -> None:
        """用户模板首触懒回载（RV-①b-⑦-3，⑦-1 _ensure_tasks_loaded 同款）：
        persist 在场才回载，只回载一次；首触=首次读（list/get/stats）或首次写
        （create/delete 先回载再变更——不回载则覆盖劫持闸看不见既有行、删行后
        旧行随重启复活）。

        回载只补缺不覆盖（⑦-1 setdefault 同纪律）：内存在册更真禁回载洗内存
        新态；builtin 种子（构造期已入册）恒不被库行洗掉=代码=真源（诚实边界④，
        即使库里出现 builtin id 行也不覆盖种子）。回载失败=空模板起步+
        logger.error 可见（诚实边界②）。
        """
        if self._template_persist is None or self._templates_loaded:
            return
        self._templates_loaded = True
        try:
            rows = self._template_persist.load_all()
        except Exception as e:
            logger.error(f"limb template 回载失败（空模板起步）: {e}")
            return
        for data in rows:
            try:
                template = TaskTemplate.from_state(data)
            except Exception as e:
                logger.error(f"limb template 行不可解跳过: {e}")
                continue
            self._templates.setdefault(template.template_id, template)

    # ── Task Management ────────────────────────────────────

    def create_task(
        self,
        name: str,
        actions: list[dict],
        priority: int = TaskPriority.NORMAL,
        tags: list[str] | None = None,
        variables: dict | None = None,
        tenant_id: str | None = None,
    ) -> RPATask:
        """Create a new RPA task.

        写点行归属（DEC-⑦-㉔）：tenant_id 落行（缺失/空白→'default' 兜底，
        键派生故障绝不吃掉落库）。
        """
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        parsed_actions = [Action.from_dict(a) for a in actions]
        task = RPATask(
            task_id=task_id,
            name=name,
            actions=parsed_actions,
            priority=priority,
            tags=tags or [],
            variables=variables or {},
            tenant_id=task_row_tenant(tenant_id),
        )
        self._ensure_tasks_loaded()
        with self._lock:
            self._tasks[task_id] = task
            self._queue.append(task_id)
        self._sync_task(task)
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

        双职（DEC-⑦-㉔，⑦-⑫ clone 双职同款）：同一 tenant_id 同时=源读闸
        +产物任务行归属（闭 ⑦-㉒ 留档「instantiate 产物任务无行键」）。
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
            tenant_id=tenant_id,
        )

    def _gate_task(self, task_id: str, tenant_id: str | None = None) -> RPATask | None:
        """by-ID 归属闸单点（DEC-⑦-㉔；⑦-⑫ get_template / ⑦-㉒
        by-ID 闸同款）：显式租户=跨租户回 None 与 not-found 同契约不差分泄露
        行存在性（③c-1 纪律），闸前置（跨租户恒 None 禁状态差分泄露）；
        None=系统视图字节不变。get/execute/cancel/delete 四 by-ID 面全经
        本口=未来消费方无法绕闸。回载懒触点（RV-①b-⑦-1）：本口是 by-ID 读写
        唯一入口，首触在此回载=重启后 by-ID 取回真源。"""
        self._ensure_tasks_loaded()
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if tenant_id is not None and task_row_tenant(task) != task_row_tenant(tenant_id):
            return None
        return task

    def get_task(self, task_id: str, tenant_id: str | None = None) -> RPATask | None:
        return self._gate_task(task_id, tenant_id)

    def list_tasks(
        self,
        status: str | None = None,
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> list[dict]:
        """tenant_id=显式租户=恒按 task_row_tenant 过滤（过滤先于截断防跨租户
        行占名额，⑦-⑬ 同款）；None=系统视图字节不变。
        """
        self._ensure_tasks_loaded()
        with self._lock:
            tasks = list(self._tasks.values())
        if tenant_id is not None:
            key = task_row_tenant(tenant_id)
            tasks = [t for t in tasks if task_row_tenant(t) == key]
        if status:
            tasks = [t for t in tasks if t.status.value == status]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tasks[:limit]]

    def cancel_task(self, task_id: str, tenant_id: str | None = None) -> bool:
        """by-ID 写面归属闸（DEC-⑦-㉔）：跨租户=与缺行同一 False 同契约
        不差分泄露行存在性（③c-1），闸前置禁状态差分；None=系统视图字节不变。
        """
        with self._lock:
            task = self._gate_task(task_id, tenant_id)
            if not task or task.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
                return False
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
            if task_id in self._queue:
                self._queue.remove(task_id)
        self._sync_task(task)
        return True

    def delete_task(self, task_id: str, tenant_id: str | None = None) -> bool:
        """by-ID 写面归属闸（DEC-⑦-㉔）：跨租户=与缺行同一 False 同契约
        （同响应）不差分泄露行存在性+行零破坏（③b-3）；闸前置禁状态差分；
        None=系统视图字节不变。
        """
        with self._lock:
            task = self._gate_task(task_id, tenant_id)
            if not task:
                return False
            if task.status == TaskStatus.RUNNING:
                return False
            self._tasks.pop(task_id, None)
        self._drop_task(task_id)
        return True

    # ── Execution ──────────────────────────────────────────

    def execute_task(self, task_id: str, tenant_id: str | None = None) -> RPATask | None:
        """Execute a task synchronously (for demo/testing).

        by-ID 写面归属闸（DEC-⑦-㉔）：跨租户=与缺行同一 None 同契约
        （API 404），零副作用禁拿他人任务跑执行；None=系统视图字节不变。
        """
        task = self._gate_task(task_id, tenant_id)
        if not task:
            return None
        if task.status != TaskStatus.QUEUED:
            return task

        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self._sync_task(task)
        self._running_count += 1
        run_key = task_row_tenant(task)
        self._tenant_running[run_key] = self._tenant_running.get(run_key, 0) + 1

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
                    self._sync_task(task)
                    self._record_history(task)
                    return task

            task.status = TaskStatus.COMPLETED
            task.current_step = len(task.actions)
            task.completed_at = time.time()
            self._total_succeeded += 1
            self._sync_task(task)
            self._record_history(task)

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
            self._total_failed += 1
            self._sync_task(task)
            self._record_history(task)

        finally:
            self._running_count -= 1
            self._tenant_running[run_key] = self._tenant_running.get(run_key, 0) - 1

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
        self._ensure_templates_loaded()
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
        self._ensure_templates_loaded()
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
        self._ensure_templates_loaded()
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
        self._sync_template(template)
        return template

    def delete_template(self, template_id: str, tenant_id: str | None = None) -> bool:
        # Don't allow deleting built-in templates（builtin 判法单一源 is_builtin
        # 禁第二套名单手抄 F4b-㉗；builtin 恒拒=共享≠可写 DEC-⑦-㉑ 同款）
        if is_builtin(template_id):
            return False
        self._ensure_templates_loaded()
        existing = self._templates.get(template_id)
        if existing is None:
            return False
        # 写面恒按行归属（template_visible_to 不适用——共享≠可写）；跨租户=与
        # not-found 同契约（同回 False→同响应）不差分泄露行存在性（③c-1 纪律）
        if tenant_id is not None and template_row_tenant(existing) != template_row_tenant(
            tenant_id
        ):
            return False
        removed = self._templates.pop(template_id, None) is not None
        if removed:
            self._drop_template(template_id)
        return removed

    # ── Stats ──────────────────────────────────────────────

    def stats(self, tenant_id: str | None = None) -> dict:
        """tenant_id=显式租户=任务面计数键恒按行租户键聚合作用域（DEC-⑦-㉔
        收口 ⑦-㉒ 留档「stats 任务计数键不随租户」：total_tasks/by_status/
        queue_length 按行键过滤、running/total_* 按分租户桶计数，禁泄露全局
        分布 ⑦-⑭⑰ 同款）+templates 计数恒按可见集聚合（⑦-㉒）；
        None=系统视图字节不变（/health 监控面）。
        计数真源化（RV-①b-⑦-2）：total_executed/succeeded/failed 恒对账执行
        历史行聚合（_counter_truth），修前内存计数器重启归零漂移收口。
        """
        self._ensure_tasks_loaded()
        self._ensure_history_loaded()
        self._ensure_templates_loaded()
        counts = self._counter_truth()
        with self._lock:
            if tenant_id is not None:
                key = task_row_tenant(tenant_id)
                visible = [t for t in self._tasks.values() if task_row_tenant(t) == key]
                by_status = {}
                for t in visible:
                    by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
                bucket = counts["by_tenant"].get(key, {"executed": 0, "succeeded": 0, "failed": 0})
                executed = bucket["executed"]
                return {
                    "total_tasks": len(visible),
                    "by_status": by_status,
                    "queue_length": sum(
                        1
                        for tid in self._queue
                        if (t := self._tasks.get(tid)) is not None and task_row_tenant(t) == key
                    ),
                    "running": self._tenant_running.get(key, 0),
                    "total_executed": executed,
                    "total_succeeded": bucket["succeeded"],
                    "total_failed": bucket["failed"],
                    "success_rate": round(bucket["succeeded"] / executed * 100, 1)
                    if executed
                    else 0,
                    "templates": sum(
                        1 for t in self._templates.values() if template_visible_to(t, key)
                    ),
                }
            by_status = {}
            for t in self._tasks.values():
                by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
            return {
                "total_tasks": len(self._tasks),
                "by_status": by_status,
                "queue_length": len(self._queue),
                "running": self._running_count,
                "total_executed": counts["executed"],
                "total_succeeded": counts["succeeded"],
                "total_failed": counts["failed"],
                "success_rate": round(counts["succeeded"] / counts["executed"] * 100, 1)
                if counts["executed"]
                else 0,
                "templates": sum(
                    1
                    for t in self._templates.values()
                    if tenant_id is None or template_visible_to(t, tenant_id)
                ),
            }

    def get_history(self, limit: int = 50, tenant_id: str | None = None) -> list[dict]:
        """tenant_id=显式租户=恒按行租户键过滤（过滤先于截断防跨租户行占
        名额，⑦-⑬ 同款；行键=任务行继承 ③b-2）；None=系统视图字节不变。
        回载懒触点（RV-①b-⑦-2）：首触在此回载=重启后历史取回真源。
        """
        self._ensure_history_loaded()
        rows = self._history
        if tenant_id is not None:
            key = task_row_tenant(tenant_id)
            rows = [h for h in rows if task_row_tenant(h) == key]
        return rows[-limit:]

    # ── Helpers ────────────────────────────────────────────

    def _record_history(self, task: RPATask):
        # 首触懒回载先于追加（RV-①b-⑦-2）：回载替换内存窗，先载后录防在录条目
        # 被回载冲掉（_ensure_history_loaded 生产不变量：首触先于任何追加）。
        self._ensure_history_loaded()
        self._total_executed += 1
        key = task_row_tenant(task)
        bucket = self._tenant_counters.setdefault(key, {"executed": 0, "succeeded": 0, "failed": 0})
        bucket["executed"] += 1
        if task.status == TaskStatus.COMPLETED:
            bucket["succeeded"] += 1
        elif task.status == TaskStatus.FAILED:
            bucket["failed"] += 1
        entry = {
            "task_id": task.task_id,
            "name": task.name,
            "status": task.status.value,
            "steps": len(task.results),
            "elapsed_seconds": task.elapsed_seconds,
            "error": task.error,
            "completed_at": task.completed_at,
            # 行键=任务行继承（DEC-⑦-㉔，③b-2 父行继承）
            "tenant_id": key,
        }
        self._sync_history(entry)
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

    @staticmethod
    def _substitute(text: str, variables: dict) -> str:
        """Replace {{var}} placeholders with variable values."""

        def replace(match):
            key = match.group(1).strip()
            return str(variables.get(key, match.group(0)))

        return re.sub(r"\{\{(\w+)\}\}", replace, text)
