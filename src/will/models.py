"""OpenWill workflow data models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class NodeType(StrEnum):
    TRIGGER = "trigger"
    ACTION = "action"
    CONDITION = "condition"
    DELAY = "delay"
    PARALLEL = "parallel"
    MERGE = "merge"
    END = "end"


class TriggerType(StrEnum):
    MANUAL = "manual"
    CRON = "cron"
    EVENT = "event"
    WEBHOOK = "webhook"


class ActionType(StrEnum):
    HTTP = "http"
    LLM = "llm"
    KNOWLEDGE_SEARCH = "knowledge_search"
    NOTIFY = "notify"
    SCRIPT = "script"
    AGENT = "agent"
    ORGAN = "organ"


class WorkflowStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING = "waiting"


class WorkflowNode(BaseModel):
    id: str
    node_type: NodeType
    label: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0})

    @property
    def display_label(self) -> str:
        return self.label or f"{self.node_type.value}:{self.id[:8]}"


class WorkflowEdge(BaseModel):
    id: str
    source_node_id: str
    target_node_id: str
    condition: str | None = None  # Python expression for conditional edges
    label: str = ""


class Workflow(BaseModel):
    id: str
    name: str
    description: str = ""
    status: WorkflowStatus = WorkflowStatus.DRAFT
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    trigger: TriggerType = TriggerType.MANUAL
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    run_count: int = 0
    last_run_at: str | None = None

    def get_node(self, node_id: str) -> WorkflowNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def get_outgoing_edges(self, node_id: str) -> list[WorkflowEdge]:
        return [e for e in self.edges if e.source_node_id == node_id]

    def get_trigger_nodes(self) -> list[WorkflowNode]:
        return [n for n in self.nodes if n.node_type == NodeType.TRIGGER]

    def validate_dag(self) -> list[str]:
        """Validate workflow is a valid DAG. Returns list of errors."""
        errors = []
        if not self.nodes:
            errors.append("Workflow has no nodes")
            return errors
        triggers = self.get_trigger_nodes()
        if not triggers:
            errors.append("Workflow has no trigger node")
        # Check all edge references exist
        node_ids = {n.id for n in self.nodes}
        for edge in self.edges:
            if edge.source_node_id not in node_ids:
                errors.append(f"Edge source '{edge.source_node_id}' not found")
            if edge.target_node_id not in node_ids:
                errors.append(f"Edge target '{edge.target_node_id}' not found")
        # Simple cycle detection
        visited = set()
        path = set()
        adj: dict[str, list[str]] = {}
        for n in self.nodes:
            adj[n.id] = [e.target_node_id for e in self.get_outgoing_edges(n.id)]

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            path.add(node_id)
            for neighbor in adj.get(node_id, []):
                if neighbor in path:
                    errors.append(f"Cycle detected involving node '{node_id}'")
                    return True
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
            path.discard(node_id)
            return False

        for n in self.nodes:
            if n.id not in visited:
                dfs(n.id)
        return errors


class StepExecution(BaseModel):
    node_id: str
    node_label: str = ""
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: str | None = None
    completed_at: str | None = None
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0
    # 节点级错误恢复可观测：实际执行次数（重试成功>1，config.retry=0 恒为 1）
    attempts: int = 1


class WorkflowExecution(BaseModel):
    id: str
    workflow_id: str
    workflow_name: str = ""
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None
    steps: list[StepExecution] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    trigger_type: str = "manual"
    # agno budget grant语义：每次人工resume恰好+1，"用户触发的一次续跑绝不静默重跑"
    resume_count: int = 0

    @property
    def duration_ms(self) -> float:
        if not self.completed_at:
            return 0
        start = datetime.fromisoformat(self.started_at)
        end = datetime.fromisoformat(self.completed_at)
        return (end - start).total_seconds() * 1000

    @property
    def current_node_id(self) -> str | None:
        for step in reversed(self.steps):
            if step.status == ExecutionStatus.RUNNING:
                return step.node_id
        return None
