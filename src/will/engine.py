"""OpenWill workflow execution engine — real cross-component execution.

Actions now make real calls:
- http: actual HTTP requests via httpx
- llm: calls OpenGland /api/gland/chat for real LLM responses
- notify: calls OpenEcho /api/echo/send for real notifications
- knowledge_search: calls OpenSoul /api/search
- script: executes shell commands with safety constraints
- organ: calls any organ's API endpoint
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .checkpoint import DEFAULT_TENANT, RESUMABLE_STATES, CheckpointStore
from .models import (
    ExecutionStatus,
    NodeType,
    StepExecution,
    TriggerType,
    Workflow,
    WorkflowEdge,
    WorkflowExecution,
    WorkflowNode,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)

_BASE = "http://127.0.0.1:8090"

# Safety: blocked shell commands
_BLOCKED_COMMANDS = frozenset(
    {
        "rm -rf /",
        "mkfs",
        "dd if=",
        ":(){ :|:& };:",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "chmod 777 /",
        "chown root",
    }
)


class WorkflowEngine:
    """Workflow execution engine with DAG traversal and real action execution.

    P3-③: workflows持久化到JSON（data/will_workflows.json），重启不丢失。
    """

    _PERSIST_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "will_workflows.json"

    def __init__(self, checkpoint_db: Path | None = None) -> None:
        self._workflows: dict[str, Workflow] = {}
        self._executions: dict[str, WorkflowExecution] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._max_execution_history = 1000
        # P1 checkpoint断点续跑（STORM分阶段落盘+agno /continue）：
        # 每个DAG节点执行完即落盘；进程重启后执行历史/可续跑状态从盘上恢复
        self._checkpoints = CheckpointStore(checkpoint_db)
        self._load()
        self._recover_executions()

    def _recover_executions(self) -> None:
        """启动时从checkpoint store恢复执行历史（Langflow"进程重启后按run_id恢复"）。

        - 终态执行（success/failed/cancelled）原样恢复——重启不丢失执行历史（可观测性）
        - RUNNING/PENDING = 上一进程死在执行中途 → 显式标记WAITING+原因（mem0失败可见），
          可通过resume_execution断点续跑（STORM"跑到第3阶段崩了不用重跑前2阶段"）
        """
        try:
            for item in self._checkpoints.list_checkpoints(limit=1000):
                exec_id = item["execution_id"]
                if exec_id in self._executions:
                    continue
                execution = self._checkpoints.load(exec_id)
                if execution is None:
                    continue
                if execution.status in (ExecutionStatus.RUNNING, ExecutionStatus.PENDING):
                    done = sum(1 for s in execution.steps if s.status == ExecutionStatus.SUCCESS)
                    execution.status = ExecutionStatus.WAITING
                    execution.error = (
                        f"interrupted: process restarted mid-run "
                        f"({done}/{len(execution.steps)} steps checkpointed) — "
                        f"resume via POST /api/will/executions/{execution.id}/continue"
                    )
                    self._checkpoints.save(execution)
                    logger.warning(
                        "Execution %s recovered as WAITING (interrupted mid-run)", exec_id
                    )
                self._executions[exec_id] = execution
        except Exception as exc:
            logger.warning("Execution recovery failed (non-fatal): %s", exc)

    # ── Persistence (P3-③) ─────────────────────────────────────

    def _load(self) -> None:
        """启动时从JSON加载workflows"""
        try:
            if self._PERSIST_PATH.exists():
                data = json.loads(self._PERSIST_PATH.read_text(encoding="utf-8"))
                for wdata in data.get("workflows", []):
                    try:
                        wf = Workflow(**wdata)
                        self._workflows[wf.id] = wf
                    except Exception:
                        continue
        except Exception:
            pass  # 持久化文件损坏不阻塞启动

    def _save(self) -> None:
        """变更后写盘"""
        try:
            self._PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {"workflows": [w.model_dump(mode="json") for w in self._workflows.values()]}
            self._PERSIST_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception:
            pass  # 写盘失败不阻塞运行

    # ── Workflow CRUD ───────────────────────────────────────────

    def create_workflow(
        self,
        name: str,
        description: str = "",
        trigger: TriggerType = TriggerType.MANUAL,
        trigger_config: dict | None = None,
        variables: dict | None = None,
    ) -> Workflow:
        wf_id = f"wf_{uuid4().hex[:12]}"
        wf = Workflow(
            id=wf_id,
            name=name,
            description=description,
            trigger=trigger,
            trigger_config=trigger_config or {},
            variables=variables or {},
        )
        self._workflows[wf_id] = wf
        self._save()
        return wf

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        return self._workflows.get(workflow_id)

    def list_workflows(self, status: WorkflowStatus | None = None) -> list[Workflow]:
        wfs = list(self._workflows.values())
        if status:
            wfs = [w for w in wfs if w.status == status]
        return sorted(wfs, key=lambda w: w.updated_at, reverse=True)

    def update_workflow(self, workflow_id: str, **kwargs: Any) -> Workflow | None:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None
        for key, value in kwargs.items():
            if hasattr(wf, key) and key not in ("id", "created_at"):
                setattr(wf, key, value)
        wf.updated_at = datetime.now(UTC).isoformat()
        self._save()
        return wf

    def delete_workflow(self, workflow_id: str) -> bool:
        removed = self._workflows.pop(workflow_id, None) is not None
        if removed:
            self._save()
        return removed

    # ── Node/Edge Management ────────────────────────────────────

    def add_node(
        self,
        workflow_id: str,
        node_type: NodeType,
        label: str = "",
        config: dict | None = None,
        position: dict | None = None,
    ) -> WorkflowNode | None:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None
        node = WorkflowNode(
            id=f"node_{uuid4().hex[:8]}",
            node_type=node_type,
            label=label,
            config=config or {},
            position=position or {"x": 0, "y": 0},
        )
        wf.nodes.append(node)
        wf.updated_at = datetime.now(UTC).isoformat()
        self._save()
        return node

    def remove_node(self, workflow_id: str, node_id: str) -> bool:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return False
        before = len(wf.nodes)
        wf.nodes = [n for n in wf.nodes if n.id != node_id]
        wf.edges = [
            e for e in wf.edges if e.source_node_id != node_id and e.target_node_id != node_id
        ]
        wf.updated_at = datetime.now(UTC).isoformat()
        self._save()
        return len(wf.nodes) < before

    def add_edge(
        self,
        workflow_id: str,
        source_node_id: str,
        target_node_id: str,
        condition: str | None = None,
        label: str = "",
    ) -> WorkflowEdge | None:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None
        if not wf.get_node(source_node_id) or not wf.get_node(target_node_id):
            return None
        edge = WorkflowEdge(
            id=f"edge_{uuid4().hex[:8]}",
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            condition=condition,
            label=label,
        )
        wf.edges.append(edge)
        wf.updated_at = datetime.now(UTC).isoformat()
        self._save()
        return edge

    def remove_edge(self, workflow_id: str, edge_id: str) -> bool:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return False
        before = len(wf.edges)
        wf.edges = [e for e in wf.edges if e.id != edge_id]
        wf.updated_at = datetime.now(UTC).isoformat()
        return len(wf.edges) < before

    # ── Execution ───────────────────────────────────────────────

    async def execute_async(
        self, workflow_id: str, input_vars: dict | None = None, tenant_id: str = ""
    ) -> WorkflowExecution | None:
        """Execute workflow asynchronously with real action execution.

        F3b-③c-1：tenant_id=执行归属租户（API 层来自 bind_identity 派生，禁自报；
        空/缺省='default'，与匿名/内部执行契约一致）。
        """
        tenant = (tenant_id or "").strip() or DEFAULT_TENANT
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None

        errors = wf.validate_dag()
        if errors:
            exec_id = f"exec_{uuid4().hex[:12]}"
            return WorkflowExecution(
                id=exec_id,
                workflow_id=workflow_id,
                workflow_name=wf.name,
                status=ExecutionStatus.FAILED,
                error=f"Validation failed: {'; '.join(errors)}",
                completed_at=datetime.now(UTC).isoformat(),
                tenant_id=tenant,
            )

        exec_id = f"exec_{uuid4().hex[:12]}"
        variables = {**wf.variables, **(input_vars or {})}
        execution = WorkflowExecution(
            id=exec_id,
            workflow_id=workflow_id,
            workflow_name=wf.name,
            variables=variables,
            trigger_type="manual",
            tenant_id=tenant,
        )

        self._executions[exec_id] = execution
        wf.run_count += 1
        wf.last_run_at = datetime.now(UTC).isoformat()

        # 起始checkpoint：即使第一阶段中途崩溃也有行可恢复（Langflow按run_id落库）
        self._checkpoints.save(execution)

        # Run asynchronously with real I/O
        await self._run_execution_async(wf, execution)
        return execution

    def execute(
        self, workflow_id: str, input_vars: dict | None = None, tenant_id: str = ""
    ) -> WorkflowExecution | None:
        """Synchronous wrapper — creates an event loop task. F3b-③c-1：tenant 同 execute_async。"""
        tenant = (tenant_id or "").strip() or DEFAULT_TENANT
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None

        errors = wf.validate_dag()
        if errors:
            exec_id = f"exec_{uuid4().hex[:12]}"
            return WorkflowExecution(
                id=exec_id,
                workflow_id=workflow_id,
                workflow_name=wf.name,
                status=ExecutionStatus.FAILED,
                error=f"Validation failed: {'; '.join(errors)}",
                completed_at=datetime.now(UTC).isoformat(),
                tenant_id=tenant,
            )

        exec_id = f"exec_{uuid4().hex[:12]}"
        variables = {**wf.variables, **(input_vars or {})}
        execution = WorkflowExecution(
            id=exec_id,
            workflow_id=workflow_id,
            workflow_name=wf.name,
            variables=variables,
            trigger_type="manual",
            tenant_id=tenant,
        )

        self._executions[exec_id] = execution
        wf.run_count += 1
        wf.last_run_at = datetime.now(UTC).isoformat()

        # 起始checkpoint：即使第一阶段中途崩溃也有行可恢复（Langflow按run_id落库）
        self._checkpoints.save(execution)

        # Try to run in existing loop, fallback to sync
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._run_execution_async(wf, execution))
        except RuntimeError:
            asyncio.run(self._run_execution_async(wf, execution))
        return execution

    async def _run_execution_async(
        self, wf: Workflow, execution: WorkflowExecution, resume: bool = False
    ) -> None:
        """Execute the workflow DAG from trigger nodes with real async I/O.

        resume=True（断点续跑，STORM语义）：
        - 已完成节点不重新执行——其产物已随checkpoint持久化在execution.variables中
          （STORM "_load_information_table_from_local_fs从磁盘恢复上一阶段产物"）
        - 失败/被打断的部分步骤从该节点从头重放（LangGraph §4.5"节点从头重放"）
        - frontier=已完成节点的出边目标，条件边用checkpoint变量重新求值
        """
        execution.status = ExecutionStatus.RUNNING
        triggers = wf.get_trigger_nodes()
        if not triggers and not resume:
            execution.status = ExecutionStatus.FAILED
            execution.error = "No trigger node found"
            execution.completed_at = datetime.now(UTC).isoformat()
            return

        async with httpx.AsyncClient(timeout=60.0) as client:
            queue: list[str] = [t.id for t in triggers]
            visited: set[str] = set()
            if resume:
                # 失败补偿回滚语义：被成功补偿（compensate:* 步骤 SUCCESS）的节点效果
                # 已撤销，续跑必须重放——从"已完成"剔除（补偿失败=效果仍在，不重放防双执行）
                compensated_nodes = {
                    s.node_id[len("compensate:") :]
                    for s in execution.steps
                    if s.node_id.startswith("compensate:") and s.status == ExecutionStatus.SUCCESS
                }
                completed_nodes = {
                    s.node_id
                    for s in execution.steps
                    if s.status == ExecutionStatus.SUCCESS
                    and not s.node_id.startswith("compensate:")
                    and s.node_id not in compensated_nodes
                }
                # 丢弃失败/中断的部分步骤——重跑时从头重放（LangGraph节点幂等重放语义）
                execution.steps = [
                    s
                    for s in execution.steps
                    if s.status == ExecutionStatus.SUCCESS
                    and not s.node_id.startswith("compensate:")
                    and s.node_id not in compensated_nodes
                ]
                if completed_nodes:
                    frontier: list[str] = []
                    for nid in completed_nodes:
                        for edge in wf.get_outgoing_edges(nid):
                            if edge.target_node_id in completed_nodes:
                                continue
                            if edge.condition:
                                try:
                                    if not self._eval_condition(
                                        edge.condition, execution.variables
                                    ):
                                        continue
                                except Exception:
                                    continue
                            frontier.append(edge.target_node_id)
                    if frontier:
                        queue = frontier
                    visited = set(completed_nodes)
            max_steps = 200
            step_count = 0

            while queue and step_count < max_steps:
                node_id = queue.pop(0)
                if node_id in visited:
                    continue
                visited.add(node_id)
                step_count += 1

                node = wf.get_node(node_id)
                if not node:
                    continue

                step = StepExecution(
                    node_id=node_id,
                    node_label=node.display_label,
                    status=ExecutionStatus.RUNNING,
                    started_at=datetime.now(UTC).isoformat(),
                    input_data=dict(execution.variables),
                )
                execution.steps.append(step)

                # 节点级错误恢复：config.retry 次内重试（默认 0=原语义不变），
                # 退避 retry_delay 秒（上限 5s）；attempts 全程落 step 可观测
                max_retries = max(0, min(int(node.config.get("retry", 0) or 0), 5))
                retry_delay = min(float(node.config.get("retry_delay", 0.5) or 0), 5.0)
                result: dict[str, Any] = {}
                node_error: Exception | None = None
                for attempt in range(max_retries + 1):
                    step.attempts = attempt + 1
                    try:
                        result = await self._execute_node_async(node, execution.variables, client)
                        # 动作层显式声明失败（success=False：脚本非零退出/HTTP 4xx·5xx）=
                        # 节点失败，与异常同路径——可重试、耗尽后补偿回滚。禁把声明的失败
                        # 静默当成功继续跑（失败必须可见，否则"错误恢复/回滚"对最常见的
                        # 失败形态永不触发=接线了≠跑通了）
                        if isinstance(result, dict) and result.get("success") is False:
                            raise ValueError(
                                "action reported failure: "
                                + str(result.get("output") or result.get("response") or result)[
                                    :200
                                ]
                            )
                        node_error = None
                        break
                    except Exception as e:
                        node_error = e
                        if attempt < max_retries:
                            logger.warning(
                                "Workflow node '%s' attempt %d/%d failed: %s — retrying",
                                node.display_label,
                                attempt + 1,
                                max_retries + 1,
                                e,
                            )
                            if retry_delay:
                                await asyncio.sleep(retry_delay)
                if node_error is None:
                    step.output_data = result
                    step.status = ExecutionStatus.SUCCESS
                    execution.variables.update(result)
                else:
                    step.status = ExecutionStatus.FAILED
                    step.error = (
                        str(node_error)
                        if step.attempts <= 1
                        else f"{node_error} (after {step.attempts} attempts)"
                    )
                    execution.status = ExecutionStatus.FAILED
                    execution.error = f"Node '{node.display_label}' failed: {step.error}"
                    execution.completed_at = datetime.now(UTC).isoformat()
                    logger.error("Workflow node '%s' failed: %s", node.display_label, node_error)
                    # 失败补偿回滚（saga）：已成功节点逆序执行 compensate 动作
                    await self._compensate(execution, wf, client)
                    # 分阶段落盘：失败现场（已完成阶段产物+失败步骤+补偿记录）checkpoint持久化
                    self._checkpoints.save(execution)
                    return

                step.completed_at = datetime.now(UTC).isoformat()
                if step.started_at and step.completed_at:
                    step.duration_ms = self._calc_duration_ms(step.started_at, step.completed_at)

                # STORM分阶段落盘：每完成一个节点同步checkpoint一次（LangGraph关键写入sync）
                self._checkpoints.save(execution)

                # Find next nodes
                outgoing = wf.get_outgoing_edges(node_id)
                for edge in outgoing:
                    if edge.condition:
                        try:
                            if self._eval_condition(edge.condition, execution.variables):
                                queue.append(edge.target_node_id)
                        except Exception as exc:
                            logging.getLogger(__name__).debug("probe skipped: %s", exc)
                    else:
                        queue.append(edge.target_node_id)

        if execution.status != ExecutionStatus.FAILED:
            execution.status = ExecutionStatus.SUCCESS
        execution.completed_at = datetime.now(UTC).isoformat()
        self._checkpoints.save(execution)
        self._trim_history()

    async def _execute_node_async(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient
    ) -> dict[str, Any]:
        """Execute a single node with real cross-component calls."""
        if node.node_type == NodeType.TRIGGER:
            return {"triggered": True}

        elif node.node_type == NodeType.DELAY:
            seconds = node.config.get("seconds", 0)
            if seconds > 0:
                await asyncio.sleep(min(seconds, 300))  # Cap at 5 min
            return {"delayed_seconds": seconds}

        elif node.node_type == NodeType.CONDITION:
            expr = node.config.get("expression", "True")
            result = self._eval_condition(expr, variables)
            return {"condition_result": result}

        elif node.node_type == NodeType.ACTION:
            return await self._execute_action(node, variables, client)

        elif node.node_type in (NodeType.PARALLEL, NodeType.MERGE):
            return {"parallel": True}

        elif node.node_type == NodeType.END:
            return {"ended": True}

        return {}

    async def _execute_action(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient
    ) -> dict[str, Any]:
        """Execute an action node — real cross-component calls."""
        action_type = node.config.get("type", "script")
        timeout = node.config.get("timeout", 30)

        if action_type == "http":
            return await self._action_http(node, variables, client, timeout)
        elif action_type == "llm":
            return await self._action_llm(node, variables, client, timeout)
        elif action_type == "notify":
            return await self._action_notify(node, variables, client)
        elif action_type == "knowledge_search":
            return await self._action_knowledge_search(node, variables, client)
        elif action_type == "script":
            return await self._action_script(node, variables, timeout)
        elif action_type == "organ":
            return await self._action_organ(node, variables, client, timeout)
        else:
            return {
                "action": action_type,
                "executed": True,
                "warning": f"Unknown action type: {action_type}",
            }

    async def _compensate(
        self,
        execution: WorkflowExecution,
        wf: Workflow,
        client: httpx.AsyncClient,
    ) -> None:
        """失败补偿回滚（saga）：已成功节点按逆序执行 config.compensate 动作。

        - 只补偿声明了 compensate（动作 spec，形状同 config：type/url/command/…）的节点；
          未声明的不硬造回滚——无法自动撤销的副作用留档在原步骤，不假装已撤销
        - 每条补偿落独立步骤（node_id 前缀 compensate:，随 steps checkpoint 持久化，
          断点续跑按该前缀识别"已回滚节点需重放"）
        - 补偿失败只记录不中断其余补偿（best-effort 全跑完，禁静默），汇总追加进
          execution.error
        """
        rolled_back = 0
        failed = 0
        for step in reversed(execution.steps):
            if step.status != ExecutionStatus.SUCCESS:
                continue
            if step.node_id.startswith("compensate:"):
                continue
            node = wf.get_node(step.node_id)
            if not node:
                continue
            spec = node.config.get("compensate")
            if not isinstance(spec, dict) or not spec:
                continue
            comp_node = WorkflowNode(
                id=f"compensate:{node.id}",
                node_type=NodeType.ACTION,
                label=f"compensate:{node.display_label}",
                config=dict(spec),
            )
            comp_step = StepExecution(
                node_id=comp_node.id,
                node_label=comp_node.label,
                status=ExecutionStatus.RUNNING,
                started_at=datetime.now(UTC).isoformat(),
                input_data=dict(execution.variables),
            )
            execution.steps.append(comp_step)
            try:
                result = await self._execute_node_async(comp_node, execution.variables, client)
                # 补偿动作显式 success:False（如回滚脚本非零退出）=补偿失败，
                # 如实记 FAILED 不谎报已回滚
                if isinstance(result, dict) and result.get("success") is False:
                    raise ValueError(
                        "compensation action reported failure: "
                        + str(result.get("output") or result.get("response") or result)[:200]
                    )
                comp_step.output_data = result
                comp_step.status = ExecutionStatus.SUCCESS
                rolled_back += 1
            except Exception as e:
                comp_step.status = ExecutionStatus.FAILED
                comp_step.error = str(e)
                failed += 1
                logger.error("Compensation for '%s' failed: %s", step.node_label, e)
            comp_step.completed_at = datetime.now(UTC).isoformat()
            # 每条补偿同步落盘（关键写入 sync：中途崩溃补偿现场不丢）
            self._checkpoints.save(execution)

        summary = f"compensation: {rolled_back} rolled back"
        if failed:
            summary += f", {failed} failed"
        if rolled_back or failed:
            execution.error = f"{execution.error} | {summary}" if execution.error else summary

    # ── Real Action Implementations ─────────────────────────────

    async def _action_http(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient, timeout: int
    ) -> dict[str, Any]:
        """Make a real HTTP request."""
        url = self._interpolate(node.config.get("url", ""), variables)
        method = node.config.get("method", "GET").upper()
        headers = node.config.get("headers", {})
        body = node.config.get("body")

        if body and isinstance(body, str):
            body = self._interpolate(body, variables)

        if not url:
            raise ValueError("HTTP action requires 'url' in config")

        try:
            resp = await client.request(
                method=method,
                url=url,
                headers=headers,
                json=body if body else None,
                timeout=timeout,
            )
            # Try to parse JSON response
            try:
                resp_data = resp.json()
            except Exception:
                resp_data = resp.text[:2000]

            return {
                "action": "http",
                "url": url,
                "method": method,
                "status_code": resp.status_code,
                "response": resp_data,
                "success": 200 <= resp.status_code < 400,
            }
        except httpx.TimeoutException:
            raise ValueError(f"HTTP request to {url} timed out after {timeout}s")
        except Exception as e:
            raise ValueError(f"HTTP request failed: {e}")

    async def _action_llm(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient, timeout: int
    ) -> dict[str, Any]:
        """Call OpenGland for a real LLM response."""
        prompt = self._interpolate(node.config.get("prompt", ""), variables)
        model = node.config.get("model")
        system = self._interpolate(node.config.get("system", ""), variables)
        temperature = node.config.get("temperature", 0.7)
        max_tokens = node.config.get("max_tokens", 2048)

        if not prompt:
            raise ValueError("LLM action requires 'prompt' in config")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if model:
            payload["model"] = model

        try:
            resp = await client.post(
                f"{_BASE}/api/gland/chat",
                json=payload,
                timeout=timeout,
            )
            if resp.status_code != 200:
                raise ValueError(f"Gland returned {resp.status_code}: {resp.text[:500]}")

            data = resp.json()
            # Extract the response content
            content = ""
            if isinstance(data, dict):
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                elif "content" in data:
                    content = data["content"]
                elif "response" in data:
                    content = data["response"]

            return {
                "action": "llm",
                "prompt": prompt[:200],
                "response": content[:5000],
                "model": data.get("model", model or "unknown"),
                "usage": data.get("usage", {}),
            }
        except httpx.TimeoutException:
            raise ValueError(f"LLM call timed out after {timeout}s")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"LLM call failed: {e}")

    async def _action_notify(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient
    ) -> dict[str, Any]:
        """Send a real notification via OpenEcho."""
        title = self._interpolate(node.config.get("title", "Workflow Notification"), variables)
        content = self._interpolate(node.config.get("content", ""), variables)
        channel = node.config.get("channel", "webhook")
        target = node.config.get("target", "")
        priority = node.config.get("priority", "normal")

        if not content:
            raise ValueError("Notify action requires 'content' in config")

        try:
            resp = await client.post(
                f"{_BASE}/api/echo/send",
                json={
                    "title": title,
                    "content": content,
                    "channel": channel,
                    "target": target,
                    "priority": priority,
                },
                timeout=10.0,
            )
            data = resp.json() if resp.status_code == 200 else {"error": resp.text[:500]}
            return {
                "action": "notify",
                "title": title,
                "channel": channel,
                "success": data.get("success", False),
                "msg_id": data.get("msg_id"),
                "error": data.get("error"),
            }
        except Exception as e:
            raise ValueError(f"Notification failed: {e}")

    async def _action_knowledge_search(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient
    ) -> dict[str, Any]:
        """Search the knowledge base via OpenSoul."""
        query = self._interpolate(node.config.get("query", ""), variables)
        limit = node.config.get("limit", 5)

        if not query:
            raise ValueError("Knowledge search requires 'query' in config")

        try:
            resp = await client.get(
                f"{_BASE}/api/search",
                params={"q": query, "limit": limit},
                timeout=15.0,
            )
            data = resp.json() if resp.status_code == 200 else {"results": []}
            results = data.get("results", data.get("hits", []))
            return {
                "action": "knowledge_search",
                "query": query,
                "results": results[:limit],
                "count": len(results),
            }
        except Exception as e:
            raise ValueError(f"Knowledge search failed: {e}")

    async def _action_script(
        self, node: WorkflowNode, variables: dict[str, Any], timeout: int
    ) -> dict[str, Any]:
        """Execute a shell command with safety constraints."""
        cmd = self._interpolate(node.config.get("command", ""), variables)
        cwd = node.config.get("cwd")

        if not cmd:
            raise ValueError("Script action requires 'command' in config")

        # Safety check
        for blocked in _BLOCKED_COMMANDS:
            if blocked in cmd:
                raise ValueError(f"Blocked dangerous command: {blocked}")

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode(errors="replace") if stdout else ""

            return {
                "action": "script",
                "command": cmd[:200],
                "exit_code": proc.returncode,
                "output": output[:5000],
                "success": proc.returncode == 0,
            }
        except TimeoutError:
            raise ValueError(f"Script timed out after {timeout}s")
        except Exception as e:
            raise ValueError(f"Script execution failed: {e}")

    async def _action_organ(
        self, node: WorkflowNode, variables: dict[str, Any], client: httpx.AsyncClient, timeout: int
    ) -> dict[str, Any]:
        """Call any organ's API endpoint directly."""
        endpoint = self._interpolate(node.config.get("endpoint", ""), variables)
        method = node.config.get("method", "GET").upper()
        body = node.config.get("body")

        if not endpoint:
            raise ValueError("Organ action requires 'endpoint' in config (e.g. '/api/vein/stats')")

        url = f"{_BASE}{endpoint}"
        if body and isinstance(body, dict):
            # Interpolate variables in body
            body = {k: self._interpolate(str(v), variables) for k, v in body.items()}

        try:
            resp = await client.request(
                method=method,
                url=url,
                json=body if body and method in ("POST", "PUT", "PATCH") else None,
                params=body if body and method == "GET" else None,
                timeout=timeout,
            )
            try:
                data = resp.json()
            except Exception:
                data = resp.text[:2000]

            return {
                "action": "organ",
                "endpoint": endpoint,
                "method": method,
                "status_code": resp.status_code,
                "response": data,
                "success": resp.status_code == 200,
            }
        except httpx.TimeoutException:
            raise ValueError(f"Organ call to {endpoint} timed out after {timeout}s")
        except Exception as e:
            raise ValueError(f"Organ call failed: {e}")

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _eval_condition(expr: str, variables: dict[str, Any]) -> bool:
        """Safely evaluate a condition expression."""
        safe_globals = {"__builtins__": {}}
        try:
            return bool(eval(expr, safe_globals, variables))
        except Exception:
            return False

    @staticmethod
    def _interpolate(template: str, variables: dict[str, Any]) -> str:
        """Interpolate ${var} placeholders in a string."""
        if not template:
            return template
        for key, value in variables.items():
            template = template.replace(f"${{{key}}}", str(value))
        return template

    @staticmethod
    def _calc_duration_ms(start: str, end: str) -> float:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        return (e - s).total_seconds() * 1000

    def _trim_history(self) -> None:
        if len(self._executions) > self._max_execution_history:
            sorted_execs = sorted(
                self._executions.items(),
                key=lambda x: x[1].started_at,
                reverse=True,
            )
            self._executions = dict(sorted_execs[: self._max_execution_history])

    # ── Execution History ───────────────────────────────────────

    def get_execution(
        self, execution_id: str, tenant_id: str | None = None
    ) -> WorkflowExecution | None:
        # 内存miss时回落checkpoint store（_trim_history裁掉的旧执行仍可按id读回，
        # Langflow"重启后按run_id恢复"读路径语义）
        # F3b-③c-1：显式租户=by-ID 闸门真执行（内存行与checkpoint行同一资源同一闸门，
        # 跨租户 CheckpointAccessDenied 禁静默 None）；None=系统内部读。
        execution = self._executions.get(execution_id) or self._checkpoints.load(execution_id)
        if execution is not None and tenant_id is not None:
            self._checkpoints.gate(
                tenant_id,
                getattr(execution, "tenant_id", "") or DEFAULT_TENANT,
                execution_id,
                "read",
            )
        return execution

    # ── Checkpoint / Resume（STORM分阶段断点续跑 + agno /continue） ──

    async def resume_execution(
        self, execution_id: str, tenant_id: str | None = None
    ) -> tuple[WorkflowExecution | None, str]:
        """断点续跑：从checkpoint恢复已完成阶段产物，从失败/中断节点继续执行。

        agno语义（evolution-engine-patterns §5.2）：
        - 仅WAITING/FAILED可续跑——SUCCESS/CANCELLED/RUNNING拒绝（"用户触发的一次续跑
          绝不静默重跑"：已完成的执行绝不自动重放）
        - 每次resume恰好执行一遍（budget grant），resume_count逐次+1可观测
        - 失败再次可见：续跑后再失败error原文随执行状态返回，不静默

        返回(execution | None, reason)：None时reason说明为什么不可续跑（API映射404/409）。
        F3b-③c-1：显式租户=by-ID 闸门真执行（action=write，续跑会写状态），跨租户
        CheckpointAccessDenied 上抛（API 403，禁静默当 not_found）；None=系统内部续跑。
        """
        execution = self._executions.get(execution_id) or self._checkpoints.load(execution_id)
        if execution is None:
            return None, "not_found"
        if tenant_id is not None:
            self._checkpoints.gate(
                tenant_id,
                getattr(execution, "tenant_id", "") or DEFAULT_TENANT,
                execution_id,
                "write",
            )
        if execution.status not in RESUMABLE_STATES:
            return (
                None,
                f"not_resumable: status={execution.status.value} "
                f"(only waiting/failed executions can be continued)",
            )
        wf = self._workflows.get(execution.workflow_id)
        if wf is None:
            reason = (
                f"workflow_missing: workflow {execution.workflow_id} no longer exists — "
                "cannot resume"
            )
            execution.error = reason
            self._checkpoints.save(execution)
            return None, reason

        errors = wf.validate_dag()
        if errors:
            reason = f"workflow_invalid: {'; '.join(errors)}"
            execution.error = reason
            self._checkpoints.save(execution)
            return None, reason

        done = sum(1 for s in execution.steps if s.status == ExecutionStatus.SUCCESS)
        execution.resume_count = int(getattr(execution, "resume_count", 0) or 0) + 1
        execution.status = ExecutionStatus.RUNNING
        execution.error = None
        execution.completed_at = None
        self._executions[execution_id] = execution
        self._checkpoints.save(execution)
        logger.info(
            "Resuming execution %s (resume #%d, %d completed steps replayed from checkpoint)",
            execution_id,
            execution.resume_count,
            done,
        )
        await self._run_execution_async(wf, execution, resume=True)
        return execution, (
            f"resumed: {done} completed step(s) replayed from checkpoint, "
            f"resume_count={execution.resume_count}"
        )

    def list_checkpoints(
        self,
        workflow_id: str | None = None,
        resumable_only: bool = False,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """checkpoint列表（API/monitoring消费：哪些执行可续跑/被打断）。

        F3b-③c-1：显式租户=bulk 过滤+引擎留痕；None=系统内部扫描（启动恢复）。
        """
        return self._checkpoints.list_checkpoints(
            workflow_id=workflow_id,
            resumable_only=resumable_only,
            limit=limit,
            tenant_id=tenant_id,
        )

    def delete_checkpoint(self, execution_id: str, tenant_id: str | None = None) -> bool:
        """删除checkpoint行+内存执行记录（清理通道）。

        F3b-③c-1：显式租户=内存行先行闸门（禁跨租户清他租户执行记录）+store 行闸门。
        """
        if tenant_id is not None:
            exec = self._executions.get(execution_id)
            if exec is not None:
                self._checkpoints.gate(
                    tenant_id,
                    getattr(exec, "tenant_id", "") or DEFAULT_TENANT,
                    execution_id,
                    "write",
                )
        self._executions.pop(execution_id, None)
        return self._checkpoints.delete(execution_id, tenant_id=tenant_id)

    def list_executions(
        self, workflow_id: str | None = None, limit: int = 50, tenant_id: str | None = None
    ) -> list[WorkflowExecution]:
        execs = list(self._executions.values())
        if tenant_id is not None:
            # F3b-③c-1：内存执行面按租户过滤（与 checkpoint 表查询过滤同语义）
            execs = [
                e for e in execs if (getattr(e, "tenant_id", "") or DEFAULT_TENANT) == tenant_id
            ]
        if workflow_id:
            execs = [e for e in execs if e.workflow_id == workflow_id]
        return sorted(execs, key=lambda e: e.started_at, reverse=True)[:limit]

    def cancel_execution(self, execution_id: str, tenant_id: str | None = None) -> bool:
        exec = self._executions.get(execution_id)
        if not exec:
            return False
        # F3b-③c-1：闸门先于状态检查——跨租户恒 403（禁用 400/403 差分泄露他租户执行状态）
        if tenant_id is not None:
            self._checkpoints.gate(
                tenant_id,
                getattr(exec, "tenant_id", "") or DEFAULT_TENANT,
                execution_id,
                "write",
            )
        if exec.status not in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING):
            return False
        exec.status = ExecutionStatus.CANCELLED
        exec.completed_at = datetime.now(UTC).isoformat()
        # 取消状态同步落盘（重启后历史一致可见）
        self._checkpoints.save(exec)
        return True

    # ── Stats ───────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        total_execs = len(self._executions)
        success = sum(1 for e in self._executions.values() if e.status == ExecutionStatus.SUCCESS)
        failed = sum(1 for e in self._executions.values() if e.status == ExecutionStatus.FAILED)
        running = sum(1 for e in self._executions.values() if e.status == ExecutionStatus.RUNNING)
        waiting = sum(1 for e in self._executions.values() if e.status == ExecutionStatus.WAITING)
        return {
            "total_workflows": len(self._workflows),
            "active_workflows": sum(
                1 for w in self._workflows.values() if w.status == WorkflowStatus.ACTIVE
            ),
            "total_executions": total_execs,
            "successful": success,
            "failed": failed,
            "running": running,
            # WAITING=被进程重启打断、可断点续跑的执行（mem0失败可见）
            "waiting_resume": waiting,
            "success_rate": round(success / total_execs * 100, 1) if total_execs else 0,
            # checkpoint store统计（resumable/by_status——monitoring可观测）
            "checkpoints": self._checkpoints.stats(),
        }
