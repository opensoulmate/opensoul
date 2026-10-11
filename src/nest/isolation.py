"""Isolation engine — enforce namespace isolation across shared resources.

Ensures tenants cannot access each other's:
- Knowledge bases and documents
- Vector collections (logical partitioning)
- API keys and secrets
- Agents and workflows
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from enum import StrEnum

from src.nest.store import NestStore


class ResourceType(StrEnum):
    KNOWLEDGE_BASE = "knowledge_base"
    DOCUMENT = "document"
    VECTOR_COLLECTION = "vector_collection"
    AGENT = "agent"
    WORKFLOW = "workflow"
    API_KEY = "api_key"
    FILE = "file"
    SESSION = "session"
    JOB = "job"  # F3b-③a（2026-10-12）：will 执行面后台作业（jobs 表）——数据面首个真消费轴
    AI_GROUP_TASK = "ai_group_task"  # F3b-③b-1（2026-10-12）：AI 群组任务面（ai_group_tasks 表）
    AI_GROUP_MESSAGE = (
        "ai_group_message"  # F3b-③b-2（2026-10-12）：AI 群组消息面（discussion_messages 表）
    )
    AI_GROUP_EVENT = (
        "ai_group_event"  # F3b-③b-2（2026-10-12）：AI 群组事件面（task_execution_events 表）
    )
    AI_GROUP = (
        "ai_group"  # F3b-③b-3（2026-10-12）：AI 群组组面（ai_groups 表）+ 成员/能力/评分行归属
    )
    CHECKPOINT = "checkpoint"  # F3b-③c-1（2026-10-12）：will 执行 checkpoint（will_checkpoints 表）
    TIMELINE_EVENT = (
        "timeline_event"  # F3b-③c-2①（2026-10-12）：时间线事件面（timeline.db events 表）
    )
    PROACTIVE_EVENT = "proactive_event"  # F3b-③c-2②a（2026-10-12）：主动行为事件面（proactive.db proactive_events 表）


@dataclass
class IsolationPolicy:
    """Defines isolation rules for a resource type."""

    resource_type: ResourceType
    namespace_scoped: bool = True  # Prefix all IDs with namespace
    cross_tenant_allowed: bool = False  # Can tenants share this resource?
    encryption_enabled: bool = False  # Encrypt at rest per tenant
    audit_access: bool = True  # Log all access attempts


@dataclass
class AccessLog:
    """Audit log entry for cross-boundary access attempts."""

    timestamp: float
    tenant_id: str
    resource_type: str
    resource_id: str
    action: str  # "read", "write", "delete", "list"
    allowed: bool
    reason: str = ""
    source_ip: str = ""


class IsolationEngine:
    """Enforce tenant isolation across all shared resources."""

    # Default policies per resource type
    DEFAULT_POLICIES = {
        ResourceType.KNOWLEDGE_BASE: IsolationPolicy(
            ResourceType.KNOWLEDGE_BASE, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.DOCUMENT: IsolationPolicy(
            ResourceType.DOCUMENT, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.VECTOR_COLLECTION: IsolationPolicy(
            ResourceType.VECTOR_COLLECTION, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.AGENT: IsolationPolicy(
            ResourceType.AGENT, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.WORKFLOW: IsolationPolicy(
            ResourceType.WORKFLOW, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.API_KEY: IsolationPolicy(
            ResourceType.API_KEY,
            namespace_scoped=True,
            cross_tenant_allowed=False,
            encryption_enabled=True,
        ),
        ResourceType.FILE: IsolationPolicy(
            ResourceType.FILE, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.SESSION: IsolationPolicy(
            ResourceType.SESSION,
            namespace_scoped=True,
            cross_tenant_allowed=True,  # Shared sessions allowed
        ),
        ResourceType.JOB: IsolationPolicy(
            ResourceType.JOB, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.AI_GROUP_TASK: IsolationPolicy(
            ResourceType.AI_GROUP_TASK, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.AI_GROUP_MESSAGE: IsolationPolicy(
            ResourceType.AI_GROUP_MESSAGE, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.AI_GROUP_EVENT: IsolationPolicy(
            ResourceType.AI_GROUP_EVENT, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.AI_GROUP: IsolationPolicy(
            ResourceType.AI_GROUP, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.CHECKPOINT: IsolationPolicy(
            ResourceType.CHECKPOINT, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.TIMELINE_EVENT: IsolationPolicy(
            ResourceType.TIMELINE_EVENT, namespace_scoped=True, cross_tenant_allowed=False
        ),
        ResourceType.PROACTIVE_EVENT: IsolationPolicy(
            ResourceType.PROACTIVE_EVENT, namespace_scoped=True, cross_tenant_allowed=False
        ),
    }

    def __init__(self, db_path: str | None = None):
        self._policies: dict[ResourceType, IsolationPolicy] = dict(self.DEFAULT_POLICIES)
        self._lock = threading.Lock()
        self._max_log = 10000
        # F3b-①（2026-10-12）：审计日志真源持久化到 SQLite（NestStore）——修前
        # _access_log 内存 list 重启即丢，现随写随落盘、读走库（内存 list 移除，单一真源）。
        self._store = NestStore(db_path)

    def namespaced_id(self, namespace: str, resource_id: str) -> str:
        """Create a namespaced resource ID."""
        return f"{namespace}:{resource_id}"

    def extract_namespace(self, namespaced_id: str) -> tuple[str, str]:
        """Extract namespace and original ID from a namespaced ID."""
        parts = namespaced_id.split(":", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return "", namespaced_id

    def check_access(
        self,
        tenant_namespace: str,
        resource_type: ResourceType,
        resource_id: str,
        action: str = "read",
        source_ip: str = "",
    ) -> dict:
        """Check if a tenant can access a resource.

        Returns:
            {"allowed": bool, "reason": str, "namespaced_id": str}
        """
        policy = self._policies.get(resource_type)
        if not policy:
            self._log_access(
                tenant_namespace, resource_type, resource_id, action, True, "no_policy"
            )
            return {"allowed": True, "reason": "no_policy", "namespaced_id": resource_id}

        # If resource has a namespace prefix, check it matches
        if policy.namespace_scoped and ":" in resource_id:
            resource_ns, _ = self.extract_namespace(resource_id)
            if resource_ns and resource_ns != tenant_namespace:
                self._log_access(
                    tenant_namespace,
                    resource_type,
                    resource_id,
                    action,
                    False,
                    f"namespace mismatch: {resource_ns} != {tenant_namespace}",
                    source_ip,
                )
                return {
                    "allowed": False,
                    "reason": f"Resource belongs to different tenant (namespace: {resource_ns})",
                    "namespaced_id": resource_id,
                }

        # Build namespaced ID if needed
        namespaced_id = resource_id
        if policy.namespace_scoped and ":" not in resource_id:
            namespaced_id = self.namespaced_id(tenant_namespace, resource_id)

        self._log_access(
            tenant_namespace, resource_type, resource_id, action, True, "ok", source_ip
        )
        return {"allowed": True, "reason": "ok", "namespaced_id": namespaced_id}

    def set_policy(self, resource_type: str, **kwargs) -> bool:
        """Update isolation policy for a resource type."""
        try:
            rt = ResourceType(resource_type)
        except ValueError:
            return False

        with self._lock:
            policy = self._policies.get(rt)
            if not policy:
                policy = IsolationPolicy(rt)
                self._policies[rt] = policy
            for key in (
                "namespace_scoped",
                "cross_tenant_allowed",
                "encryption_enabled",
                "audit_access",
            ):
                if key in kwargs:
                    setattr(policy, key, kwargs[key])
        return True

    def get_policy(self, resource_type: str) -> dict | None:
        try:
            rt = ResourceType(resource_type)
        except ValueError:
            return None
        policy = self._policies.get(rt)
        if not policy:
            return None
        return {
            "resource_type": policy.resource_type.value,
            "namespace_scoped": policy.namespace_scoped,
            "cross_tenant_allowed": policy.cross_tenant_allowed,
            "encryption_enabled": policy.encryption_enabled,
            "audit_access": policy.audit_access,
        }

    def list_policies(self) -> list[dict]:
        return [
            {
                "resource_type": p.resource_type.value,
                "namespace_scoped": p.namespace_scoped,
                "cross_tenant_allowed": p.cross_tenant_allowed,
                "encryption_enabled": p.encryption_enabled,
                "audit_access": p.audit_access,
            }
            for p in self._policies.values()
        ]

    def get_access_log(
        self,
        tenant_id: str | None = None,
        action: str | None = None,
        allowed: bool | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """倒序读审计日志（真源 SQLite，重启后历史照读；过滤语义与原内存版一致）。"""
        return self._store.list_access_log(
            tenant_id=tenant_id, action=action, allowed=allowed, limit=limit
        )

    def stats(self) -> dict:
        s = self._store.access_stats()
        total = s["total_access_checks"]
        blocked = s["blocked_attempts"]
        return {
            "total_access_checks": total,
            "blocked_attempts": blocked,
            "block_rate": round(blocked / total * 100, 2) if total else 0,
            "by_resource_type": s["by_resource_type"],
            "policies_count": len(self._policies),
        }

    def _log_access(
        self,
        tenant_id: str,
        resource_type: ResourceType,
        resource_id: str,
        action: str,
        allowed: bool,
        reason: str = "",
        source_ip: str = "",
    ):
        entry = AccessLog(
            timestamp=time.time(),
            tenant_id=tenant_id,
            resource_type=resource_type.value,
            resource_id=resource_id,
            action=action,
            allowed=allowed,
            reason=reason,
            source_ip=source_ip,
        )
        self._store.insert_access_log(asdict(entry), max_log=self._max_log)
