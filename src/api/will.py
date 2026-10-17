"""OpenWill API — 意志系统：工作流编排、条件触发、多分支流程。"""

import asyncio
import hmac
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from src.config import settings
from src.services.tenant_binding import bind_identity
from src.will.checkpoint import CheckpointAccessDenied
from src.will.dag_planner import DAGPlanAccessDenied, DAGPlanner, StepStatus
from src.will.engine import WorkflowAccessDenied, WorkflowEngine
from src.will.models import (
    NodeType,
    TriggerType,
    WorkflowStatus,
)

router = APIRouter()
engine = WorkflowEngine()

# P3-③ 启动自动seed：首次启动（持久化文件为空）时注册真实系统编排
try:
    if not engine.list_workflows():
        from src.will.orchestration_seeder import seed_system_orchestrations

        _seed_result = seed_system_orchestrations(engine)
except Exception:
    pass  # seed失败不阻塞服务启动
dag_planner = DAGPlanner()


# ── Request Schemas ────────────────────────────────────────────


class WorkflowCreate(BaseModel):
    name: str
    description: str = ""
    trigger: TriggerType = TriggerType.MANUAL
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, Any] = Field(default_factory=dict)


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: WorkflowStatus | None = None
    variables: dict[str, Any] | None = None


class NodeCreate(BaseModel):
    node_type: NodeType
    label: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})


class EdgeCreate(BaseModel):
    source_node_id: str
    target_node_id: str
    condition: str | None = None
    label: str = ""


class ExecuteRequest(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)


# ── Workflow Endpoints ─────────────────────────────────────────


@router.post("/workflows")
async def create_workflow(req: WorkflowCreate, authorization: str | None = Header(None)):
    """Create a new workflow.

    F3b-⑦：定义面行归属租户从认证身份派生（bind_identity，匿名='default'），禁自报。
    """
    identity = bind_identity(authorization)
    wf = engine.create_workflow(
        name=req.name,
        description=req.description,
        trigger=req.trigger,
        trigger_config=req.trigger_config,
        variables=req.variables,
        tenant_id=identity.tenant_id,
    )
    return _workflow_dict(wf)


@router.get("/workflows")
async def list_workflows(
    status: str = Query(default=None), authorization: str | None = Header(None)
):
    """List all workflows（F3b-⑦：按派生租户过滤，None 系统视图仅引擎内部）。"""
    identity = bind_identity(authorization)
    ws = engine.list_workflows(tenant_id=identity.tenant_id)
    if status:
        try:
            s = WorkflowStatus(status)
            ws = engine.list_workflows(status=s, tenant_id=identity.tenant_id)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")
    return {"workflows": [_workflow_dict(w) for w in ws], "count": len(ws)}


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, authorization: str | None = Header(None)):
    """Get workflow details."""
    identity = bind_identity(authorization)
    try:
        wf = engine.get_workflow(workflow_id, tenant_id=identity.tenant_id)
    except WorkflowAccessDenied as e:
        raise HTTPException(403, f"workflow access denied: {e.reason}")
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return _workflow_dict(wf)


@router.put("/workflows/{workflow_id}")
async def update_workflow(
    workflow_id: str, req: WorkflowUpdate, authorization: str | None = Header(None)
):
    """Update workflow metadata."""
    identity = bind_identity(authorization)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        wf = engine.update_workflow(workflow_id, tenant_id=identity.tenant_id, **updates)
    except WorkflowAccessDenied as e:
        raise HTTPException(403, f"workflow access denied: {e.reason}")
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return _workflow_dict(wf)


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str, authorization: str | None = Header(None)):
    """Delete a workflow."""
    identity = bind_identity(authorization)
    try:
        ok = engine.delete_workflow(workflow_id, tenant_id=identity.tenant_id)
    except WorkflowAccessDenied as e:
        raise HTTPException(403, f"workflow access denied: {e.reason}")
    if not ok:
        raise HTTPException(404, "Workflow not found")
    return {"status": "ok", "workflow_id": workflow_id}


# ── Node Endpoints ─────────────────────────────────────────────


@router.post("/workflows/{workflow_id}/nodes")
async def add_node(workflow_id: str, req: NodeCreate, authorization: str | None = Header(None)):
    """Add a node to a workflow."""
    identity = bind_identity(authorization)
    try:
        node = engine.add_node(
            workflow_id,
            node_type=req.node_type,
            label=req.label,
            config=req.config,
            position=req.position,
            tenant_id=identity.tenant_id,
        )
    except WorkflowAccessDenied as e:
        raise HTTPException(403, f"workflow access denied: {e.reason}")
    if not node:
        raise HTTPException(404, "Workflow not found")
    return node.model_dump()


@router.delete("/workflows/{workflow_id}/nodes/{node_id}")
async def remove_node(workflow_id: str, node_id: str, authorization: str | None = Header(None)):
    """Remove a node from a workflow."""
    identity = bind_identity(authorization)
    try:
        ok = engine.remove_node(workflow_id, node_id, tenant_id=identity.tenant_id)
    except WorkflowAccessDenied as e:
        raise HTTPException(403, f"workflow access denied: {e.reason}")
    if not ok:
        raise HTTPException(404, "Node or workflow not found")
    return {"status": "ok"}


# ── Edge Endpoints ─────────────────────────────────────────────


@router.post("/workflows/{workflow_id}/edges")
async def add_edge(workflow_id: str, req: EdgeCreate, authorization: str | None = Header(None)):
    """Add an edge (connection) between two nodes."""
    identity = bind_identity(authorization)
    try:
        edge = engine.add_edge(
            workflow_id,
            source_node_id=req.source_node_id,
            target_node_id=req.target_node_id,
            condition=req.condition,
            label=req.label,
            tenant_id=identity.tenant_id,
        )
    except WorkflowAccessDenied as e:
        raise HTTPException(403, f"workflow access denied: {e.reason}")
    if not edge:
        raise HTTPException(404, "Workflow or nodes not found")
    return edge.model_dump()


@router.delete("/workflows/{workflow_id}/edges/{edge_id}")
async def remove_edge(workflow_id: str, edge_id: str, authorization: str | None = Header(None)):
    """Remove an edge from a workflow."""
    identity = bind_identity(authorization)
    try:
        ok = engine.remove_edge(workflow_id, edge_id, tenant_id=identity.tenant_id)
    except WorkflowAccessDenied as e:
        raise HTTPException(403, f"workflow access denied: {e.reason}")
    if not ok:
        raise HTTPException(404, "Edge or workflow not found")
    return {"status": "ok"}


# ── Validation ─────────────────────────────────────────────────


@router.get("/workflows/{workflow_id}/validate")
async def validate_workflow(workflow_id: str, authorization: str | None = Header(None)):
    """Validate a workflow DAG."""
    identity = bind_identity(authorization)
    try:
        wf = engine.get_workflow(workflow_id, tenant_id=identity.tenant_id)
    except WorkflowAccessDenied as e:
        raise HTTPException(403, f"workflow access denied: {e.reason}")
    if not wf:
        raise HTTPException(404, "Workflow not found")
    errors = wf.validate_dag()
    return {"valid": len(errors) == 0, "errors": errors}


# ── Execution Endpoints ────────────────────────────────────────


@router.post("/workflows/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: str,
    req: ExecuteRequest = ExecuteRequest(),
    authorization: str | None = Header(None),
):
    """Execute a workflow with real cross-component action execution.

    F3b-③c-1：执行归属租户从认证身份派生（bind_identity，匿名='default'），禁自报——
    checkpoint 表数据面隔离键由此落行。
    """
    identity = bind_identity(authorization)
    try:
        execution = await engine.execute_async(
            workflow_id, input_vars=req.variables, tenant_id=identity.tenant_id
        )
    except WorkflowAccessDenied as e:
        raise HTTPException(403, f"workflow access denied: {e.reason}")
    if not execution:
        raise HTTPException(404, "Workflow not found")
    return _execution_dict(execution)


@router.get("/executions")
async def list_executions(
    workflow_id: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    authorization: str | None = Header(None),
):
    """List workflow executions. F3b-③c-1：列表恒按派生租户过滤（跨租户行不可见）。"""
    identity = bind_identity(authorization)
    execs = engine.list_executions(
        workflow_id=workflow_id, limit=limit, tenant_id=identity.tenant_id
    )
    return {"executions": [_execution_dict(e) for e in execs], "count": len(execs)}


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str, authorization: str | None = Header(None)):
    """Get execution details. F3b-③c-1：by-ID 读走隔离闸门，跨租户 403。"""
    identity = bind_identity(authorization)
    try:
        exec = engine.get_execution(execution_id, tenant_id=identity.tenant_id)
    except CheckpointAccessDenied as e:
        raise HTTPException(403, f"checkpoint access denied: {e.reason}")
    if not exec:
        raise HTTPException(404, "Execution not found")
    return _execution_dict(exec)


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(execution_id: str, authorization: str | None = Header(None)):
    """Cancel a running execution. F3b-③c-1：写操作走隔离闸门，跨租户 403。"""
    identity = bind_identity(authorization)
    try:
        ok = engine.cancel_execution(execution_id, tenant_id=identity.tenant_id)
    except CheckpointAccessDenied as e:
        raise HTTPException(403, f"checkpoint access denied: {e.reason}")
    if not ok:
        raise HTTPException(400, "Execution cannot be cancelled")
    return {"status": "cancelled", "execution_id": execution_id}


# ── Checkpoint / Resume（STORM分阶段断点续跑 + agno /continue） ──


@router.post("/executions/{execution_id}/continue")
async def continue_execution(execution_id: str, authorization: str | None = Header(None)):
    """断点续跑：从checkpoint恢复已完成阶段产物，从失败/中断节点继续（agno /continue语义）。

    仅waiting/failed可续跑；success/cancelled拒绝（409，"绝不静默重跑"）。
    每次调用恰好续跑一遍，resume_count逐次+1。
    F3b-③c-1：续跑走隔离闸门（写操作），跨租户 403。
    """
    identity = bind_identity(authorization)
    try:
        execution, reason = await engine.resume_execution(
            execution_id, tenant_id=identity.tenant_id
        )
    except CheckpointAccessDenied as e:
        raise HTTPException(403, f"checkpoint access denied: {e.reason}")
    if execution is None:
        status = 404 if reason == "not_found" else 409
        raise HTTPException(status, f"Cannot resume: {reason}")
    return {**_execution_dict(execution), "resume": reason}


@router.get("/checkpoints")
async def list_checkpoints(
    workflow_id: str = Query(default=None),
    resumable_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    authorization: str | None = Header(None),
):
    """列出execution checkpoints（哪些执行可续跑/被打断——monitoring可观测）。

    F3b-③c-1：列表恒按派生租户过滤（跨租户行不可见）+bulk 引擎审计留痕。
    """
    identity = bind_identity(authorization)
    items = engine.list_checkpoints(
        workflow_id=workflow_id,
        resumable_only=resumable_only,
        limit=limit,
        tenant_id=identity.tenant_id,
    )
    return {"checkpoints": items, "count": len(items)}


@router.delete("/checkpoints/{execution_id}")
async def delete_checkpoint(execution_id: str, authorization: str | None = Header(None)):
    """删除checkpoint行+执行记录（清理通道）。F3b-③c-1：写操作走隔离闸门，跨租户 403。"""
    identity = bind_identity(authorization)
    try:
        ok = engine.delete_checkpoint(execution_id, tenant_id=identity.tenant_id)
    except CheckpointAccessDenied as e:
        raise HTTPException(403, f"checkpoint access denied: {e.reason}")
    if not ok:
        raise HTTPException(404, "Checkpoint not found")
    return {"status": "deleted", "execution_id": execution_id}


# ── Health / Stats ─────────────────────────────────────────────


@router.get("/health")
async def will_health():
    """OpenWill health check."""
    return {
        "status": "ok",
        "component": "OpenWill",
        "engine": engine.stats(),
    }


@router.get("/stats")
async def will_stats():
    """Get OpenWill statistics."""
    return engine.stats()


@router.get("/action-types")
async def list_action_types():
    """List available workflow action types with their config schemas."""
    return {
        "action_types": [
            {
                "type": "http",
                "label": "HTTP Request",
                "description": "Make a real HTTP request to any URL",
                "config_schema": {
                    "url": {
                        "type": "string",
                        "required": True,
                        "description": "Target URL (supports ${var} interpolation)",
                    },
                    "method": {
                        "type": "string",
                        "default": "GET",
                        "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    },
                    "headers": {"type": "object", "description": "Request headers"},
                    "body": {"type": "object", "description": "Request body (for POST/PUT/PATCH)"},
                    "timeout": {
                        "type": "integer",
                        "default": 30,
                        "description": "Timeout in seconds",
                    },
                },
            },
            {
                "type": "llm",
                "label": "LLM Call",
                "description": "Call an LLM via OpenGland for AI-powered processing",
                "config_schema": {
                    "prompt": {
                        "type": "string",
                        "required": True,
                        "description": "User prompt (supports ${var})",
                    },
                    "system": {"type": "string", "description": "System prompt"},
                    "model": {
                        "type": "string",
                        "description": "Model name (auto-selected if empty)",
                    },
                    "temperature": {"type": "number", "default": 0.7},
                    "max_tokens": {"type": "integer", "default": 2048},
                    "timeout": {"type": "integer", "default": 60},
                },
            },
            {
                "type": "notify",
                "label": "Send Notification",
                "description": "Send a notification via OpenEcho (webhook, email, etc.)",
                "config_schema": {
                    "title": {"type": "string", "description": "Notification title"},
                    "content": {
                        "type": "string",
                        "required": True,
                        "description": "Notification content",
                    },
                    "channel": {
                        "type": "string",
                        "default": "webhook",
                        "enum": ["webhook", "email", "sms", "dingtalk", "wecom"],
                    },
                    "target": {"type": "string", "description": "Target address/URL"},
                    "priority": {
                        "type": "string",
                        "default": "normal",
                        "enum": ["low", "normal", "high", "urgent"],
                    },
                },
            },
            {
                "type": "knowledge_search",
                "label": "Knowledge Search",
                "description": "Search the OpenSoul knowledge base",
                "config_schema": {
                    "query": {
                        "type": "string",
                        "required": True,
                        "description": "Search query (supports ${var})",
                    },
                    "limit": {"type": "integer", "default": 5},
                },
            },
            {
                "type": "script",
                "label": "Run Script",
                "description": "Execute a shell command with safety constraints",
                "config_schema": {
                    "command": {
                        "type": "string",
                        "required": True,
                        "description": "Shell command (supports ${var})",
                    },
                    "cwd": {"type": "string", "description": "Working directory"},
                    "timeout": {"type": "integer", "default": 30},
                },
            },
            {
                "type": "organ",
                "label": "Organ API Call",
                "description": "Call any OpenSoul organ's API endpoint directly",
                "config_schema": {
                    "endpoint": {
                        "type": "string",
                        "required": True,
                        "description": "API path (e.g. '/api/vein/stats')",
                    },
                    "method": {"type": "string", "default": "GET"},
                    "body": {"type": "object", "description": "Request body or params"},
                    "timeout": {"type": "integer", "default": 30},
                },
            },
        ],
    }


# ── Helpers ────────────────────────────────────────────────────


def _workflow_dict(wf: Any) -> dict:
    return {
        "id": wf.id,
        "name": wf.name,
        "description": wf.description,
        "status": wf.status.value,
        "trigger": wf.trigger.value,
        "trigger_config": wf.trigger_config,
        "variables": wf.variables,
        "nodes": [n.model_dump() for n in wf.nodes],
        "edges": [e.model_dump() for e in wf.edges],
        "created_at": wf.created_at,
        "updated_at": wf.updated_at,
        "run_count": wf.run_count,
        "last_run_at": wf.last_run_at,
        "tenant_id": getattr(wf, "tenant_id", "default") or "default",
    }


def _execution_dict(exec: Any) -> dict:
    return {
        "id": exec.id,
        "workflow_id": exec.workflow_id,
        "workflow_name": exec.workflow_name,
        "status": exec.status.value,
        "started_at": exec.started_at,
        "completed_at": exec.completed_at,
        "steps": [s.model_dump() for s in exec.steps],
        "variables": exec.variables,
        "error": exec.error,
        "trigger_type": exec.trigger_type,
        "resume_count": getattr(exec, "resume_count", 0),
        # F3b-③c-1：行归属租户随执行回显（可观测；派生自认证身份非自报）
        "tenant_id": getattr(exec, "tenant_id", "default") or "default",
    }


# ── DAG Planning ──────────────────────────────────────────────


class DAGPlanRequest(BaseModel):
    goal: str
    llm_response: str


class DAGStepUpdate(BaseModel):
    """步状态推进载荷（H1-②b-⑧）。status 非法值 422 如实拒禁静默当缺省。"""

    status: StepStatus
    result: Any = None
    error: str = ""


@router.post("/dag/plan")
async def create_dag_plan(req: DAGPlanRequest, authorization: str | None = Header(None)):
    """Create execution plan from LLM response.

    F3b-⑧b：行归属租户从认证身份派生（bind_identity，匿名='default'），禁自报。
    """
    identity = bind_identity(authorization)
    plan = dag_planner.create_from_llm_response(
        req.goal, req.llm_response, tenant_id=identity.tenant_id
    )
    if not plan:
        raise HTTPException(400, "Failed to parse LLM response into plan")
    return {
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "total_steps": len(plan.steps),
        "progress": plan.progress,
        "mermaid": dag_planner.to_mermaid(plan.plan_id),
        # F3b-⑧b：行归属租户随计划回显（加性契约键，可观测非自报）
        "tenant_id": plan.tenant_id,
    }


@router.get("/dag/{plan_id}/status")
async def dag_status(plan_id: str, authorization: str | None = Header(None)):
    """Get plan status.

    F3b-⑧b：by-ID 读走 IsolationEngine 闸门（跨租户→403 带真因，禁静默 not_found）。
    """
    identity = bind_identity(authorization)
    try:
        plan = dag_planner.get_plan(plan_id, tenant_id=identity.tenant_id)
    except DAGPlanAccessDenied as e:
        raise HTTPException(403, f"dag plan access denied: {e.reason}")
    if not plan:
        raise HTTPException(404, "Plan not found")
    return {
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "progress": plan.progress,
        "is_complete": plan.is_complete,
        "total_steps": len(plan.steps),
        # F3b-⑧b：行归属租户随计划回显（加性契约键）
        "tenant_id": plan.tenant_id or "default",
        "steps": [
            {
                "step_id": s.step_id,
                "description": s.description,
                "tool_name": s.tool_name,
                "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                "depends_on": s.depends_on,
                "error": s.error,
            }
            for s in plan.steps
        ],
    }


@router.get("/dag/{plan_id}/ready")
async def dag_ready_steps(plan_id: str, authorization: str | None = Header(None)):
    """Get steps whose dependencies are all satisfied（步进轴就绪查询，H1-②b-⑧）。

    get_ready_steps 首个真消费（修前全仓零消费=计划建完永不推进的读面缺口）。
    F3b-⑧b 语义沿用：by-ID 读走 IsolationEngine 闸门（跨租户→403 带真因，禁静默
    not_found）。就绪判法=引擎 get_ready_steps 原样出口（PENDING 且 depends_on 全
    COMPLETED；READY/RUNNING 态不进就绪集）。
    """
    identity = bind_identity(authorization)
    try:
        plan = dag_planner.get_plan(plan_id, tenant_id=identity.tenant_id)
    except DAGPlanAccessDenied as e:
        raise HTTPException(403, f"dag plan access denied: {e.reason}")
    if not plan:
        raise HTTPException(404, "Plan not found")
    ready = plan.get_ready_steps()
    return {
        "plan_id": plan.plan_id,
        "count": len(ready),
        "ready_steps": [
            {
                "step_id": s.step_id,
                "description": s.description,
                "tool_name": s.tool_name,
                "arguments": s.arguments,
                "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                "depends_on": s.depends_on,
                "estimated_duration": s.estimated_duration,
            }
            for s in ready
        ],
        "tenant_id": plan.tenant_id or "default",
    }


@router.post("/dag/{plan_id}/steps/{step_id}")
async def dag_update_step(
    plan_id: str,
    step_id: str,
    req: DAGStepUpdate,
    authorization: str | None = Header(None),
):
    """Advance a step's status（步进轴状态推进，H1-②b-⑧）。

    update_step 首个真消费（修前全仓零消费=计划建完永不推进的写面缺口）。
    F3b-⑧b 语义沿用：by-ID 写走 IsolationEngine 闸门（跨租户→403 行零改写）。
    缺计划/缺步如实 404 禁静默假成功（引擎 update_step 对缺步静默 no-op 是系统
    路径语义，API 面必须如实报缺）；非法 status 422。
    """
    identity = bind_identity(authorization)
    try:
        plan = dag_planner.get_plan(plan_id, tenant_id=identity.tenant_id)
    except DAGPlanAccessDenied as e:
        raise HTTPException(403, f"dag plan access denied: {e.reason}")
    if not plan:
        raise HTTPException(404, "Plan not found")
    step = next((s for s in plan.steps if s.step_id == step_id), None)
    if step is None:
        raise HTTPException(404, f"Step not found: {step_id}")
    try:
        dag_planner.update_step(
            plan_id,
            step_id,
            req.status,
            result=req.result,
            error=req.error,
            tenant_id=identity.tenant_id,
        )
    except DAGPlanAccessDenied as e:
        raise HTTPException(403, f"dag plan access denied: {e.reason}")
    return {
        "plan_id": plan.plan_id,
        "step_id": step.step_id,
        "status": step.status.value if hasattr(step.status, "value") else str(step.status),
        "result": step.result,
        "error": step.error,
        "progress": plan.progress,
        "is_complete": plan.is_complete,
        "plan_status": plan.status,
        "tenant_id": plan.tenant_id or "default",
    }


@router.get("/dag/stats")
async def dag_stats():
    """Get DAG planner statistics.

    F3b-⑧b 边界：聚合计数器全局不闸（同 goals-stats 全局边界，只含计数不含计划内容）。
    """
    return dag_planner.get_stats()


# ── P1 Goal自主目标循环（kilocode goal/runner.ts移植）──
from src.will.goal_loop import run_loop_iteration
from src.will.goal_runner import (
    GoalAccessDenied as _GoalAccessDenied,
)
from src.will.goal_runner import (
    GoalCreate as _GoalCreate,
)
from src.will.goal_runner import (
    GoalReport as _GoalReport,
)
from src.will.goal_runner import (
    GoalState as _GoalState,
)
from src.will.goal_runner import (
    get_goal_runner,
)


class GoalCreateRequest(BaseModel):
    session_id: str = ""
    description: str
    max_loops: int = 50


class GoalReportRequest(BaseModel):
    status: str  # "complete" | "blocked"
    reason: str = ""


class GoalEventRequest(BaseModel):
    tool_name: str
    result: dict[str, Any] = Field(default_factory=dict)
    detail: str = ""


@router.post("/goals")
async def create_goal(req: GoalCreateRequest, authorization: str | None = Header(None)):
    """Create a new autonomous goal. Admission check applied.

    F3b-⑧：行归属租户从认证身份派生（bind_identity，匿名='default'），禁自报。
    """
    identity = bind_identity(authorization)
    runner = get_goal_runner()
    goal = runner.create_goal(
        _GoalCreate(
            session_id=req.session_id,
            description=req.description,
            max_loops=req.max_loops,
        ),
        tenant_id=identity.tenant_id,
    )
    if not goal:
        raise HTTPException(409, "Admission denied — session already has active/blocked goal")
    return goal.model_dump()


@router.get("/goals")
async def list_goals(
    session_id: str = Query(default=None),
    state: str = Query(default=None),
    authorization: str | None = Header(None),
):
    """List goals, optionally filtered by session or state.

    F3b-⑧：按派生租户过滤（None=系统视图仅引擎内部）。
    """
    identity = bind_identity(authorization)
    runner = get_goal_runner()
    try:
        gs = runner.list_goals(
            session_id=session_id,
            state=_GoalState(state) if state else None,
            tenant_id=identity.tenant_id,
        )
    except _GoalAccessDenied as e:
        raise HTTPException(403, f"goal access denied: {e.reason}")
    return {"goals": [g.model_dump() for g in gs], "count": len(gs)}


@router.get("/goals/{goal_id}")
async def get_goal(goal_id: str, authorization: str | None = Header(None)):
    """Get goal details including event history."""
    identity = bind_identity(authorization)
    runner = get_goal_runner()
    try:
        goal = runner.get_goal(goal_id, tenant_id=identity.tenant_id)
    except _GoalAccessDenied as e:
        raise HTTPException(403, f"goal access denied: {e.reason}")
    if not goal:
        raise HTTPException(404, "Goal not found")
    return goal.model_dump()


@router.delete("/goals/{goal_id}")
async def delete_goal(goal_id: str, authorization: str | None = Header(None)):
    """Delete a goal."""
    identity = bind_identity(authorization)
    runner = get_goal_runner()
    try:
        if not runner.delete_goal(goal_id, tenant_id=identity.tenant_id):
            raise HTTPException(404, "Goal not found")
    except _GoalAccessDenied as e:
        raise HTTPException(403, f"goal access denied: {e.reason}")
    return {"status": "ok", "goal_id": goal_id}


@router.post("/goals/{goal_id}/report")
async def goal_report(
    goal_id: str, req: GoalReportRequest, authorization: str | None = Header(None)
):
    """Agent self-report via goal_report protocol.

    注意：这是agent自报，不是独立验证。事件驱动计数决定是否真正COMPLETED。
    """
    identity = bind_identity(authorization)
    runner = get_goal_runner()
    try:
        goal = runner.goal_report(
            _GoalReport(
                goal_id=goal_id,
                status=req.status,
                reason=req.reason,
            ),
            tenant_id=identity.tenant_id,
        )
    except _GoalAccessDenied as e:
        raise HTTPException(403, f"goal access denied: {e.reason}")
    if not goal:
        raise HTTPException(404, "Goal not found")
    return goal.model_dump()


@router.post("/goals/{goal_id}/events")
async def record_goal_event(
    goal_id: str, req: GoalEventRequest, authorization: str | None = Header(None)
):
    """Record a tool execution event for event-driven outcome counting."""
    identity = bind_identity(authorization)
    runner = get_goal_runner()
    try:
        event = runner.record_event(
            goal_id, req.tool_name, req.result, req.detail, tenant_id=identity.tenant_id
        )
    except _GoalAccessDenied as e:
        raise HTTPException(403, f"goal access denied: {e.reason}")
    if event is None:
        raise HTTPException(404, "Goal not found or already terminal")
    goal = runner.get_goal(goal_id)
    return {"event": event.model_dump(), "goal_state": goal.state.value if goal else "unknown"}


@router.post("/goals/{goal_id}/loop")
async def goal_loop_step(goal_id: str, authorization: str | None = Header(None)):
    """Run one self-driven loop iteration for the goal (H1-②b-⑪ 自驱循环).

    与 proactive goal_loop 行为共用 run_loop_iteration 同一代码路径：
    真 LLM 步产出朝目标的下一步动作/进展（事件留痕）＋record_loop 计次（
    max_loops 停机判定随之生效）。非 ACTIVE goal 如实 skipped 不触达执行器。
    F3b-⑧：行归属租户从认证身份派生（bind_identity，匿名=default）。
    """
    identity = bind_identity(authorization)
    runner = get_goal_runner()
    try:
        result = await asyncio.to_thread(
            run_loop_iteration, goal_id, runner=runner, tenant_id=identity.tenant_id
        )
    except _GoalAccessDenied as e:
        raise HTTPException(403, f"goal access denied: {e.reason}")
    if result is None:
        raise HTTPException(404, "Goal not found")
    return result


@router.post("/goals/{goal_id}/pause")
async def pause_goal(
    goal_id: str,
    reason: str = Query(default="User paused"),
    authorization: str | None = Header(None),
):
    """Pause an active goal (user preemption). Not terminal."""
    identity = bind_identity(authorization)
    runner = get_goal_runner()
    try:
        goal = runner.pause_goal(goal_id, reason, tenant_id=identity.tenant_id)
    except _GoalAccessDenied as e:
        raise HTTPException(403, f"goal access denied: {e.reason}")
    if not goal:
        raise HTTPException(404, "Goal not found")
    return goal.model_dump()


@router.post("/goals/{goal_id}/resume")
async def resume_goal(
    goal_id: str,
    reason: str = Query(default="User resumed"),
    authorization: str | None = Header(None),
):
    """Resume a paused goal. BLOCKED goals require goal_report first."""
    identity = bind_identity(authorization)
    runner = get_goal_runner()
    try:
        goal = runner.resume_goal(goal_id, reason, tenant_id=identity.tenant_id)
    except _GoalAccessDenied as e:
        raise HTTPException(403, f"goal access denied: {e.reason}")
    if not goal:
        raise HTTPException(400, "Cannot resume — goal not found, terminal, or BLOCKED")
    return goal.model_dump()


@router.post("/goals/{goal_id}/cancel")
async def cancel_goal(
    goal_id: str,
    reason: str = Query(default="User cancelled"),
    authorization: str | None = Header(None),
):
    """Cancel a goal → FAILED terminal state."""
    identity = bind_identity(authorization)
    runner = get_goal_runner()
    try:
        goal = runner.cancel_goal(goal_id, reason, tenant_id=identity.tenant_id)
    except _GoalAccessDenied as e:
        raise HTTPException(403, f"goal access denied: {e.reason}")
    if not goal:
        raise HTTPException(404, "Goal not found")
    return goal.model_dump()


@router.get("/goals-stats")
async def goal_stats():
    """Goal runner statistics for monitoring panel."""
    runner = get_goal_runner()
    return runner.get_stats()


# ── P0-8 后台作业队列（agno job_queue模式）──

# 运行时接线（2026-09-19 cron轮，"写了≠接线了"修复）：handler注册在模块
# 加载时完成（幂等，GET端点零副作用）；worker池懒启动——首次/jobs/submit
# 时start()（agno "executor上线才claim任务"）。此前c1feb676只落了队列基础
# 设施+API，register_handler/start调用点为0，提交的作业永远pending。
from src.will.job_handlers import register_default_handlers as _register_job_handlers
from src.will.job_queue import JobAccessDenied as _JobAccessDenied
from src.will.job_queue import get_job_queue as _get_job_queue

_register_job_handlers(_get_job_queue())


class JobSubmitRequest(BaseModel):
    name: str
    params: dict = {}
    timeout_s: int = 300
    max_retries: int = 2
    idempotency_key: str = ""  # agno §5.2：同key pending/running不重复执行
    source: str = "real"  # provenance：'test'=测试作业（经验/进化链跳过）


@router.post("/seed-system")
async def seed_system():
    """P3-③: 注册真实系统编排为可视化workflow（幂等）"""
    from src.will.orchestration_seeder import seed_system_orchestrations

    result = seed_system_orchestrations(engine)
    return {"ok": not result["errors"], **result}


@router.get("/jobs/health")
async def job_queue_health():
    """Job queue health check."""
    return {"status": "ok", "component": "JobQueue", **_get_job_queue().get_stats()}


@router.post("/jobs/submit")
async def job_submit(req: JobSubmitRequest, authorization: str | None = Header(None)):
    """Submit a background job. Returns job_id immediately.

    接线语义：首次提交懒启动worker池（start()幂等）；未注册handler的作业
    落FAILED("No handler for job type")——失败可见（mem0 §1.1），不静默吞。
    F3b-③a：作业归属租户从认证身份派生（bind_identity，匿名='default'），
    禁客户端自报——jobs 表数据面隔离键由此落行。
    """
    identity = bind_identity(authorization)
    jq = _get_job_queue()
    _register_job_handlers(jq)  # 幂等兜底（模块加载时已注册）
    await jq.start()
    existing = jq.find_by_idempotency_key(req.idempotency_key, tenant_id=identity.tenant_id)
    job_id = await jq.submit(
        req.name,
        req.params,
        req.timeout_s,
        req.max_retries,
        idempotency_key=req.idempotency_key,
        source=req.source,
        tenant_id=identity.tenant_id,
    )
    return {
        "job_id": job_id,
        "status": "submitted",
        "deduped": bool(existing) and existing == job_id,
        "workers": jq.get_stats()["workers"],
    }


@router.post("/jobs/purge")
async def job_purge(
    status: str = "",
    name_pattern: str = "",
    older_than_days: int = 0,
    source: str = "",
    authorization: str | None = Header(None),
):
    """清理作业历史 — 集成修复#4: test_job/stress_test等测试噪声积压清理。

    source='test'：provenance精确打击（只清标记为测试的行，不按名字猜）。
    零过滤拒绝：无任何过滤条件=DELETE全表——误清footgun，强制至少一个过滤。
    F3b-③a：清理范围恒限派生租户名下（跨租户历史行绝不动）。
    """
    if not (status or name_pattern or older_than_days or source):
        raise HTTPException(
            status_code=400,
            detail="purge requires at least one filter (status/name_pattern/older_than_days/source)",
        )
    identity = bind_identity(authorization)
    from src.will.job_queue import get_job_queue

    purged = get_job_queue().purge(
        status=status,
        name_pattern=name_pattern,
        older_than_days=older_than_days,
        source=source,
        tenant_id=identity.tenant_id,
    )
    return {"purged": purged}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, authorization: str | None = Header(None)):
    """Get job status and result. F3b-③a：by-ID 读走隔离闸门，跨租户 403。"""
    identity = bind_identity(authorization)
    from src.will.job_queue import get_job_queue

    try:
        result = get_job_queue().get(job_id, tenant_id=identity.tenant_id)
    except _JobAccessDenied as e:
        raise HTTPException(403, f"job access denied: {e.reason}")
    if not result:
        raise HTTPException(404, "Job not found")
    return result


@router.get("/jobs")
async def job_list(status: str = "", limit: int = 50, authorization: str | None = Header(None)):
    """List jobs, optionally filtered by status. F3b-③a：只列派生租户名下作业。"""
    identity = bind_identity(authorization)
    from src.will.job_queue import get_job_queue

    return {"jobs": get_job_queue().list_jobs(status, limit, tenant_id=identity.tenant_id)}


@router.post("/jobs/{job_id}/cancel")
async def job_cancel(job_id: str, authorization: str | None = Header(None)):
    """Cancel a pending job. F3b-③a：by-ID 写走隔离闸门，跨租户 403。"""
    identity = bind_identity(authorization)
    from src.will.job_queue import get_job_queue

    try:
        ok = get_job_queue().cancel(job_id, tenant_id=identity.tenant_id)
    except _JobAccessDenied as e:
        raise HTTPException(403, f"job access denied: {e.reason}")
    return {"cancelled": ok}


@router.post("/jobs/{job_id}/requeue")
async def job_requeue(job_id: str, authorization: str | None = Header(None)):
    """agno /queue/jobs/{id}/requeue：终态（failed/timeout/cancelled）作业人工续跑。

    budget grant=恰好一次（evolution-engine-patterns §5.2 "用户触发的一次续跑
    绝不静默重跑"）；pending/running返回not_terminal不重复排队。
    F3b-③a：by-ID 写走隔离闸门，跨租户 403。
    """
    identity = bind_identity(authorization)
    from src.will.job_queue import get_job_queue

    try:
        result = await get_job_queue().requeue(job_id, tenant_id=identity.tenant_id)
    except _JobAccessDenied as e:
        raise HTTPException(403, f"job access denied: {e.reason}")
    if result.get("reason") == "not_found":
        raise HTTPException(404, "Job not found")
    return result


# ── ScheduleWakeup（claude-code SDK：agent自报noop+streak折叠） ──
# 接线：agent工具 schedule_wakeup（acp-proxy soulmate_agent）→ POST /api/will/wakeup/schedule；
# 前端折叠UI → GET /api/will/wakeup/folded（连续noop折叠成 noop_streak 块）


class WakeupScheduleRequest(BaseModel):
    agent_id: str = "default"
    session_id: str = ""
    reason: str = Field(description="一句话：这次唤醒检查了什么（必填，展示给用户）")
    noop: bool = Field(description="true=这次检查什么都没发生；false=有进展")
    delay_seconds: float = Field(default=60.0, description="下次唤醒延迟（会被钳到[60,3600]秒）")
    stop: bool = Field(default=False, description="true=结束唤醒循环")
    source: str = Field(default="", description="调度来源标识（可观测用，可空）")


@router.post("/wakeup/schedule")
async def wakeup_schedule(req: WakeupScheduleRequest, authorization: str | None = Header(None)):
    """记录一次 ScheduleWakeup 自报：钳制延迟 + noop streak + 折叠状态。

    F3b-⑧c：行归属租户从认证身份派生（bind_identity，匿名='default'），禁自报。
    """
    from src.will.wakeup import get_wakeup_scheduler

    identity = bind_identity(authorization)
    try:
        event = get_wakeup_scheduler().record(
            reason=req.reason,
            noop=req.noop,
            delay_seconds=req.delay_seconds,
            stop=req.stop,
            agent_id=req.agent_id,
            session_id=req.session_id,
            source=req.source,
            tenant_id=identity.tenant_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return event


@router.get("/wakeup/status")
async def wakeup_status(
    agent_id: str = "default",
    session_id: str = "",
    authorization: str | None = Header(None),
):
    """唤醒循环状态：active / current_streak / next_wakeup_at / 倒计时。

    F3b-⑧c：按派生租户过滤（跨租户循环恒不可见）。
    """
    from src.will.wakeup import get_wakeup_scheduler

    identity = bind_identity(authorization)
    return get_wakeup_scheduler().status(
        agent_id=agent_id, session_id=session_id, tenant_id=identity.tenant_id
    )


@router.get("/wakeup/folded")
async def wakeup_folded(
    agent_id: str = "default",
    session_id: str = "",
    limit: int = Query(default=50),
    authorization: str | None = Header(None),
):
    """折叠显示：连续 noop 折叠成一个 noop_streak 块（count/streak_max/时间段/全部reason）。

    F3b-⑧c：按派生租户过滤（跨租户循环恒不可见）。
    """
    from src.will.wakeup import get_wakeup_scheduler

    identity = bind_identity(authorization)
    return get_wakeup_scheduler().folded_view(
        agent_id=agent_id, session_id=session_id, limit=limit, tenant_id=identity.tenant_id
    )


@router.get("/wakeup/overview")
async def wakeup_overview(
    include_test: bool = Query(default=False), authorization: str | None = Header(None)
):
    """全部唤醒循环状态聚合（monitoring面板数据源：不指定key一屏看全）。

    每个 (agent_id, session_id) 键给 status() 同语义摘要（active/streak/倒计时/
    fire执行侧状态），前端Wakeup面板按此渲染，逐key展开再查/wakeup/folded。
    include_test 默认False：source='test' 测试循环不进面板（真实唤醒循环一屏可见）。
    F3b-⑧c：按派生租户过滤（跨租户循环恒不进面板，reason 全文不跨租户泄露）。
    """
    from src.will.wakeup import get_wakeup_scheduler

    identity = bind_identity(authorization)
    return get_wakeup_scheduler().overview(include_test=include_test, tenant_id=identity.tenant_id)


# ── 执行侧闭环（claude-code runtime"到点重新唤醒agent"）──
# 接线：acp-proxy agent/wakeup_executor.py（a/b双实例）→ GET /wakeup/due 轮询 →
# POST /wakeup/claim CAS领取（at-most-once，双实例不双跑）→ 执行唤醒回合 →
# POST /wakeup/fire-result 回执（失败释放重试/上限EXHAUSTED留痕）。
# DEC-① ②：executor 凭据=X-Executor-Token 头（见下方 _executor_token_verdict）。


class WakeupClaimRequest(BaseModel):
    event_id: int = Field(description="wakeup事件id")


class WakeupFireResultRequest(BaseModel):
    event_id: int
    ok: bool = Field(description="唤醒回合是否成功执行")
    error: str = Field(default="", description="失败原因（ok=False时进last_fire_error留痕）")


# DEC-① ②（F3b-⑧c 边界①收口）：executor 认证轴——共享 wakeup_executor 是系统组件
# （与 proactive 心跳同族调度器，非用户身份非 agent 身份），凭据（X-Executor-Token
# 头 ↔ settings.wakeup_executor_token）过=系统视图 claim 各租户到点事件、点火落
# 行租户键（事件行自带 tenant_id，due 回包逐事件带键=点火动作按行租户归属，claim/
# ack 只写行本身=行租户键不漂移）。缺头=既有用户轴字节不变（bind_identity 契约零
# 变化）；带着头来而错/轴未配置=401 fail-closed——禁静默降级匿名 'default' 视图
# （静默降级会让非 default 租户到点事件永久无人点火=静默停滞，错误必须可见）。

_EXECUTOR_REJECTIONS = {
    "bad-config": "wakeup executor axis not configured (settings.wakeup_executor_token 空)",
    "bad-token": "wakeup executor token invalid",
}


def _executor_token_verdict(presented: str | None) -> str:
    """executor 凭据单一判定口（F3b-⑤ _agent_token_verdict 同族纪律，禁两套判定）。

    返回 "absent"（未带头→用户轴）/"ok"（系统视图）/"bad-config"/"bad-token"
    （→401 fail-closed，理由如实区分、不回显凭据）。显式空串头=带着头来=按坏凭据
    拒（禁静默降级用户轴）；hmac.compare_digest 恒时比较（F3b-④ 纪律）。
    """
    if presented is None:
        return "absent"
    if not presented:
        return "bad-token"
    expected = settings.wakeup_executor_token or ""
    if not expected:
        return "bad-config"
    if hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        return "ok"
    return "bad-token"


@router.get("/wakeup/due")
async def wakeup_due(
    authorization: str | None = Header(None),
    x_executor_token: str | None = Header(None),
):
    """到点应唤醒的事件（executor轮询入口；已领取/已终结的不再返回）。

    F3b-⑧c：按派生租户过滤（跨租户到点事件恒不返回，reason 全文不跨租户泄露）。
    DEC-① ②：executor 凭据过=系统视图——各租户到点事件全返回，逐事件 tenant_id
    键=点火动作落行租户键；缺头=用户轴契约零变化；带着头来而坏=401 零静默降级。
    """
    from src.will.wakeup import get_wakeup_scheduler

    verdict = _executor_token_verdict(x_executor_token)
    if verdict.startswith("bad"):
        raise HTTPException(401, _EXECUTOR_REJECTIONS[verdict])
    if verdict == "ok":
        due = get_wakeup_scheduler().due_wakeups(tenant_id=None)
    else:
        identity = bind_identity(authorization)
        due = get_wakeup_scheduler().due_wakeups(tenant_id=identity.tenant_id)
    return {"count": len(due), "due": due}


@router.post("/wakeup/claim")
async def wakeup_claim(
    req: WakeupClaimRequest,
    authorization: str | None = Header(None),
    x_executor_token: str | None = Header(None),
):
    """CAS领取执行权：并发领取只有一个赢家（409=已被领取/已终结/超重试上限）。

    F3b-⑧c：by-ID 写闸门（跨租户→403 带真因，闸门前置于状态检查）。
    DEC-① ②：executor 凭据过=系统视图跨租户领取（身份证明先于一切，401 零副作用）；
    领取只写事件行本身=行租户键不漂移（点火动作落行租户键）。
    """
    from src.will.wakeup import WakeupAccessDenied, get_wakeup_scheduler

    verdict = _executor_token_verdict(x_executor_token)
    if verdict.startswith("bad"):
        raise HTTPException(401, _EXECUTOR_REJECTIONS[verdict])
    tenant_id = None if verdict == "ok" else bind_identity(authorization).tenant_id
    try:
        event = get_wakeup_scheduler().claim_fire(req.event_id, tenant_id=tenant_id)
    except WakeupAccessDenied as e:
        raise HTTPException(403, f"wakeup access denied: {e.reason}")
    if event is None:
        raise HTTPException(
            409,
            f"wakeup事件{req.event_id}不可领取（已被领取/已终结/超过重试上限）",
        )
    return {"claimed": True, "event": event}


@router.post("/wakeup/fire-result")
async def wakeup_fire_result(
    req: WakeupFireResultRequest,
    authorization: str | None = Header(None),
    x_executor_token: str | None = Header(None),
):
    """执行结果回执：失败→释放重试或EXHAUSTED终态（mem0 §1.1失败必须可见）。

    F3b-⑧c：by-ID 写闸门（跨租户→403 行零改写）。
    DEC-① ②：executor 凭据过=系统视图跨租户回执（401 零副作用先于一切写）；
    回执只写事件行本身=行租户键不漂移。
    """
    from src.will.wakeup import WakeupAccessDenied, get_wakeup_scheduler

    verdict = _executor_token_verdict(x_executor_token)
    if verdict.startswith("bad"):
        raise HTTPException(401, _EXECUTOR_REJECTIONS[verdict])
    tenant_id = None if verdict == "ok" else bind_identity(authorization).tenant_id
    try:
        return get_wakeup_scheduler().ack_fire(
            req.event_id, ok=req.ok, error=req.error, tenant_id=tenant_id
        )
    except WakeupAccessDenied as e:
        raise HTTPException(403, f"wakeup access denied: {e.reason}")
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/wakeup/purge")
async def wakeup_purge(source: str = "", authorization: str | None = Header(None)):
    """清理唤醒事件 — source='test'：provenance精确打击（只清标记行，不按名字猜）。

    live测试收编约定：测试schedule声明source='test'，跑完自清本表
    （jobs/purge同款；零过滤拒绝——无source=DELETE全表，误清footgun）。
    F3b-⑧c：按派生租户清（"只给租户"的清理不得变相清他租户历史）。
    """
    from src.will.wakeup import get_wakeup_scheduler

    identity = bind_identity(authorization)
    try:
        return get_wakeup_scheduler().purge(source=source, tenant_id=identity.tenant_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
