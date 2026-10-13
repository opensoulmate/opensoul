"""Security audit logger."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import StrEnum


class AuditAction(StrEnum):
    LOGIN = "login"
    LOGOUT = "logout"
    ACCESS_DENIED = "access_denied"
    RATE_LIMITED = "rate_limited"
    CONTENT_BLOCKED = "content_blocked"
    IP_BLOCKED = "ip_blocked"
    DATA_EXPORT = "data_export"
    CONFIG_CHANGE = "config_change"
    SUSPICIOUS = "suspicious"
    API_CALL = "api_call"


@dataclass
class AuditEntry:
    timestamp: float
    action: AuditAction
    client_ip: str = ""
    user_id: str = ""
    endpoint: str = ""
    detail: str = ""
    risk_level: str = "low"
    metadata: dict = field(default_factory=dict)


class AuditLogger:
    """Thread-safe in-memory audit log with ring buffer."""

    def __init__(self, max_entries: int = 10000):
        self._log: deque[AuditEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}

    def log(
        self,
        action: AuditAction,
        client_ip: str = "",
        user_id: str = "",
        endpoint: str = "",
        detail: str = "",
        risk_level: str = "low",
        metadata: dict | None = None,
    ):
        entry = AuditEntry(
            timestamp=time.time(),
            action=action,
            client_ip=client_ip,
            user_id=user_id,
            endpoint=endpoint,
            detail=detail,
            risk_level=risk_level,
            metadata=metadata or {},
        )
        with self._lock:
            self._log.append(entry)
            key = f"{action.value}:{risk_level}"
            self._counters[key] = self._counters.get(key, 0) + 1

    def query(
        self,
        action: AuditAction | None = None,
        risk_level: str | None = None,
        client_ip: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
        since: float | None = None,
    ) -> list[dict]:
        with self._lock:
            entries = list(self._log)

        results = []
        for e in reversed(entries):
            if action and e.action != action:
                continue
            if risk_level and e.risk_level != risk_level:
                continue
            if client_ip and e.client_ip != client_ip:
                continue
            if user_id and e.user_id != user_id:
                continue
            if since and e.timestamp < since:
                continue
            results.append(asdict(e))
            if len(results) >= limit:
                break

        return results

    def cleanup(self, keep_last: int | None = None) -> dict:
        """Drop oldest entries, keeping the newest keep_last.

        缺省 keep_last=容量之半（策略选择可另拍）：为降级器官腾出内存头寸，
        保留最近一半审计历史。counters 是累计计数，不随裁剪回退。
        """
        if keep_last is None:
            keep_last = (self._log.maxlen or 0) // 2
        if isinstance(keep_last, bool) or not isinstance(keep_last, int):
            raise ValueError("keep_last must be an integer")
        if keep_last < 0:
            raise ValueError("keep_last must be >= 0")
        with self._lock:
            dropped = max(0, len(self._log) - keep_last)
            for _ in range(dropped):
                self._log.popleft()
            return {"dropped": dropped, "remaining": len(self._log), "keep_last": keep_last}

    def stats(self) -> dict:
        with self._lock:
            return {
                "total_entries": len(self._log),
                "counters": dict(self._counters),
                "oldest_entry": self._log[0].timestamp if self._log else None,
                "newest_entry": self._log[-1].timestamp if self._log else None,
            }
