"""Security audit logger."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import StrEnum

from src.immune.audit_persist import AuditPersistence

logger = logging.getLogger(__name__)


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
    """Thread-safe in-memory audit log with ring buffer.

    RV-①b-⑧：persist 在场时留痕随写落盘+首触懒回载（重启审计史不丢），
    缺省 persist=None=纯内存既有语义字节不变。
    """

    def __init__(self, max_entries: int = 10000, persist: AuditPersistence | None = None):
        self._log: deque[AuditEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        # RV-①b-⑧ 持久化接线（缺省 None=纯内存零变化）：写穿+首触懒回载。
        self._persist = persist
        self._loaded = False

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
        self._ensure_loaded()
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
        self._sync_entry(entry)

    def query(
        self,
        action: AuditAction | None = None,
        risk_level: str | None = None,
        client_ip: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
        since: float | None = None,
    ) -> list[dict]:
        self._ensure_loaded()
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
        保留最近一半审计历史。counters 是累计计数，不随裁剪回退（RV-①b-⑧：
        计数恒对账全表行聚合，cleanup 只裁内存 ring 不删行=审计史不可被
        cleanup 抹除（安全审计语义），恒全表聚合由此保真）。
        """
        self._ensure_loaded()
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
        self._ensure_loaded()
        with self._lock:
            return {
                "total_entries": len(self._log),
                "counters": self._counter_truth(),
                "oldest_entry": self._log[0].timestamp if self._log else None,
                "newest_entry": self._log[-1].timestamp if self._log else None,
            }

    def _sync_entry(self, entry: AuditEntry) -> None:
        """审计留痕追加写穿单一点（RV-①b-⑧，_sync_entry 纪律：禁散落写点漂移）。

        追加型历史表（echo_history/limb_history 同纪律）：每次留痕恰一行 INSERT
        禁覆盖=禁丢史。写失败=记账旁路纪律（RV-①b-①）：logger.error 可见不阻断
        审计本体（诚实边界①：该行只活内存、重启丢）。
        """
        if self._persist is None:
            return
        try:
            self._persist.insert(
                timestamp=entry.timestamp,
                action=entry.action.value,
                client_ip=entry.client_ip,
                user_id=entry.user_id,
                endpoint=entry.endpoint,
                detail=entry.detail,
                risk_level=entry.risk_level,
                metadata=entry.metadata,
            )
        except Exception as e:
            logger.error(f"immune 审计留痕落盘失败（留痕本体已在内存）: {e}")

    def _ensure_loaded(self) -> None:
        """审计留痕首触懒回载（RV-①b-⑧，⑦-2 _ensure_history_loaded 同款）：
        persist 在场才回载，只回载一次；首触=首次读（query/stats/cleanup）或
        首次写（log 先回载再追加——回载替换 ring，先载后录防在录条目被回载
        冲掉；首触先于任何追加=回载时 ring 恒空，生产不变量）。

        回载窗=最近 maxlen 行（内存 ring 既有裁剪语义不变，更旧行留在库不进
        内存——echo RV-①b-④ ③ 同语义）。计数不随窗裁剪：计数真源=全表行聚合
        （_counter_truth 恒对账 counters()，内存计数器降级为缓存视图 MEM-⑥
        纪律）。回载失败=空审计起步+logger.error 可见（诚实边界②）。
        """
        if self._persist is None or self._loaded:
            return
        self._loaded = True
        try:
            rows = self._persist.load_recent(self._log.maxlen or 0)
        except Exception as e:
            logger.error(f"immune 审计留痕回载失败（空审计起步）: {e}")
            return
        entries: list[AuditEntry] = []
        for row in rows:
            try:
                entries.append(
                    AuditEntry(
                        timestamp=float(row["timestamp"]),
                        action=AuditAction(row["action"]),
                        client_ip=str(row["client_ip"]),
                        user_id=str(row["user_id"]),
                        endpoint=str(row["endpoint"]),
                        detail=str(row["detail"]),
                        risk_level=str(row["risk_level"]),
                        metadata=row["metadata"] if isinstance(row["metadata"], dict) else {},
                    )
                )
            except Exception as e:
                # 坏行跳过必留日志真因（echo ④ 纪律：不因单行坏数据丢整段审计史）
                logger.error(f"immune 审计留痕坏行跳过: {e}")
        with self._lock:
            self._log.clear()
            self._log.extend(entries)

    def _counter_truth(self) -> dict:
        """计数真源（RV-①b-⑧「计数真源化」，⑦-2 _counter_truth 同款）：persist
        在场=恒对账审计留痕行聚合（MEM-⑥ 纪律：计数器=行缓存视图，值读面读真源
        禁信第二套聚合列）；缺省=纯内存计数器既有语义字节不变。对账失败=
        logger.error 可见+回落内存计数缓存（缓存本职，禁静默假 0）。
        """
        if self._persist is None:
            return dict(self._counters)
        try:
            return self._persist.counters()
        except Exception as e:
            logger.error(f"immune 审计计数对账失败（回落内存计数缓存）: {e}")
            return dict(self._counters)
