"""Integration connector — manage bidirectional connections to external systems."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ConnectorType(StrEnum):
    WEBHOOK_IN = "webhook_in"  # Receive webhooks from external
    WEBHOOK_OUT = "webhook_out"  # Send webhooks to external
    REST_API = "rest_api"  # REST API integration
    OA_SYSTEM = "oa_system"  # OA/ERP/etc
    CUSTOM = "custom"


class ConnectorStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class Connector:
    connector_id: str
    name: str
    type: ConnectorType
    endpoint: str = ""
    secret: str = ""
    # register 签发回调凭据（DEC-⑲，F3b-④/⑤ 同族"claim 注册身份必须持
    # register 签发 token"）：create_connector 恒签发 uuid4().hex，明文只回
    # 注册响应一次；receive_webhook 凭据证明消费（X-Callback-Token 或
    # X-Signature HMAC 等效证明）。
    callback_token: str = ""
    status: ConnectorStatus = ConnectorStatus.ACTIVE
    headers: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_triggered: float = 0
    trigger_count: int = 0
    error_count: int = 0
    last_error: str = ""


@dataclass
class WebhookEvent:
    event_id: str
    connector_id: str
    timestamp: float
    method: str = "POST"
    headers: dict = field(default_factory=dict)
    payload: Any = None
    source_ip: str = ""


class IntegrationManager:
    """Manage bidirectional integrations and webhook connections."""

    def __init__(self):
        self._connectors: dict[str, Connector] = {}
        self._events: list[WebhookEvent] = []
        self._lock = threading.Lock()
        self._max_events = 5000

    def create_connector(
        self,
        name: str,
        type: str,
        endpoint: str = "",
        secret: str = "",
        headers: dict | None = None,
        config: dict | None = None,
        description: str = "",
        tags: list[str] | None = None,
    ) -> Connector:
        connector_id = f"conn-{uuid.uuid4().hex[:8]}"
        connector = Connector(
            connector_id=connector_id,
            name=name,
            type=ConnectorType(type),
            endpoint=endpoint,
            secret=secret,
            # register 签发回调凭据（DEC-⑲）：恒签发=生产不可达"无证"行
            # （fail-closed 判定口的缺省态永不裸奔），明文只回注册响应一次。
            callback_token=uuid.uuid4().hex,
            headers=headers or {},
            config=config or {},
            description=description,
            tags=tags or [],
        )
        with self._lock:
            self._connectors[connector_id] = connector
        return connector

    def get_connector(self, connector_id: str) -> Connector | None:
        with self._lock:
            return self._connectors.get(connector_id)

    def list_connectors(self, type: str | None = None, status: str | None = None) -> list[dict]:
        with self._lock:
            connectors = list(self._connectors.values())
        if type:
            connectors = [c for c in connectors if c.type.value == type]
        if status:
            connectors = [c for c in connectors if c.status.value == status]
        return [
            {
                "connector_id": c.connector_id,
                "name": c.name,
                "type": c.type.value,
                "status": c.status.value,
                "endpoint": c.endpoint,
                "has_secret": bool(c.secret),
                "has_callback_token": bool(c.callback_token),
                "description": c.description,
                "tags": c.tags,
                "trigger_count": c.trigger_count,
                "error_count": c.error_count,
                "last_triggered": c.last_triggered,
                "created_at": c.created_at,
            }
            for c in sorted(connectors, key=lambda x: x.created_at, reverse=True)
        ]

    def update_connector(self, connector_id: str, **kwargs) -> bool:
        with self._lock:
            connector = self._connectors.get(connector_id)
        if not connector:
            return False
        for key, value in kwargs.items():
            if hasattr(connector, key):
                if key == "type":
                    setattr(connector, key, ConnectorType(value))
                elif key == "status":
                    setattr(connector, key, ConnectorStatus(value))
                else:
                    setattr(connector, key, value)
        return True

    def delete_connector(self, connector_id: str) -> bool:
        with self._lock:
            return self._connectors.pop(connector_id, None) is not None

    def rotate_callback_token(self, connector_id: str) -> str | None:
        """签发新回调凭据并即刻作废旧凭据（DEC-⑲-②）。

        行 token 被替换=旧明文立即失效（receive_webhook 判定口 fail-closed），
        新明文只返回给调用方一次（register 同语义，get/list 面不回吐）。
        撤销后经本口可重新签发（凭据丢失恢复路径，免重注册 connector）。
        """
        with self._lock:
            connector = self._connectors.get(connector_id)
            if not connector:
                return None
            token = uuid.uuid4().hex
            connector.callback_token = token
            return token

    def revoke_callback_token(self, connector_id: str) -> bool:
        """撤销回调凭据（DEC-⑲-②）：置空行 token=凭据证明 fail-closed 拒。

        只清 X-Callback-Token 证明面；X-Signature HMAC=secret 等效证明是
        独立轴不动（secret 同时用于出站签名，撤销/更换属 PATCH secret 既有面）。
        """
        with self._lock:
            connector = self._connectors.get(connector_id)
            if not connector:
                return False
            connector.callback_token = ""
            return True

    def record_event(
        self,
        connector_id: str,
        method: str = "POST",
        headers: dict | None = None,
        payload: Any = None,
        source_ip: str = "",
    ) -> WebhookEvent:
        """Record an incoming webhook event."""
        event = WebhookEvent(
            event_id=f"evt-{uuid.uuid4().hex[:8]}",
            connector_id=connector_id,
            timestamp=time.time(),
            method=method,
            headers=headers or {},
            payload=payload,
            source_ip=source_ip,
        )

        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events :]

            connector = self._connectors.get(connector_id)
            if connector:
                connector.last_triggered = time.time()
                connector.trigger_count += 1

        return event

    def send_webhook(self, connector_id: str, payload: dict) -> dict:
        """Send a webhook to a connector's endpoint."""
        with self._lock:
            connector = self._connectors.get(connector_id)
        if not connector:
            return {"success": False, "error": "Connector not found"}
        if connector.status != ConnectorStatus.ACTIVE:
            return {"success": False, "error": f"Connector is {connector.status.value}"}
        if not connector.endpoint:
            return {"success": False, "error": "No endpoint configured"}

        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json", **connector.headers}

            # Sign if secret exists
            if connector.secret:
                signature = hmac.new(
                    connector.secret.encode("utf-8"),
                    body,
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Signature"] = f"sha256={signature}"

            req = urllib.request.Request(
                connector.endpoint, data=body, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                connector.trigger_count += 1
                connector.last_triggered = time.time()
                return {"success": True, "status_code": resp.status, "connector_id": connector_id}

        except Exception as e:
            connector.error_count += 1
            connector.last_error = str(e)
            return {"success": False, "error": str(e), "connector_id": connector_id}

    def get_events(self, connector_id: str | None = None, limit: int = 50) -> list[dict]:
        with self._lock:
            events = self._events
        if connector_id:
            events = [e for e in events if e.connector_id == connector_id]
        return [
            {
                "event_id": e.event_id,
                "connector_id": e.connector_id,
                "timestamp": e.timestamp,
                "method": e.method,
                "source_ip": e.source_ip,
                "payload_type": type(e.payload).__name__,
            }
            for e in events[-limit:]
        ]

    def stats(self) -> dict:
        with self._lock:
            return {
                "total_connectors": len(self._connectors),
                "active": sum(
                    1 for c in self._connectors.values() if c.status == ConnectorStatus.ACTIVE
                ),
                "total_events": len(self._events),
                "by_type": {
                    t.value: sum(1 for c in self._connectors.values() if c.type == t)
                    for t in ConnectorType
                },
            }
