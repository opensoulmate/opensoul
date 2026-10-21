"""IP access control — blacklist, whitelist, geo-blocking."""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from dataclasses import dataclass, field

from src.immune.access_persist import AccessPersistence

logger = logging.getLogger(__name__)


@dataclass
class IPEntry:
    ip: str
    reason: str = ""
    added_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    hit_count: int = 0


class IPAccessControl:
    """IP-based access control with blacklist/whitelist."""

    def __init__(self, persist: AccessPersistence | None = None):
        """
        Args:
            persist: RV-①b-② 名单持久化真源（缺省 None=纯内存，既有语义零变化）。
                给定即增删/命中计数写穿 + 构造期回载（重启封禁不丢）。
        """
        self._blacklist: dict[str, IPEntry] = {}
        self._whitelist: dict[str, IPEntry] = {}
        self._lock = threading.Lock()
        self._persist = persist
        if persist is not None:
            self._reload_from_persist()

    def _sync_entry(self, list_kind: str, key: str, entry: IPEntry | None) -> None:
        """名单行写穿单一点（增删/命中计数/过期清理各变更共用，禁散落写点漂移）。

        entry=None=删除行。写失败不阻断名单动作本体（内存即生效），logger.error
        可见——故障窗口内该变更只活内存、重启丢/复活（如实记非谎报 durable）。
        """
        if self._persist is None:
            return
        try:
            if entry is None:
                self._persist.delete(list_kind, key)
            else:
                self._persist.upsert(
                    list_kind=list_kind,
                    entry_key=key,
                    ip=entry.ip,
                    reason=entry.reason,
                    added_at=entry.added_at,
                    expires_at=entry.expires_at,
                    hit_count=entry.hit_count,
                )
        except Exception as exc:  # noqa: BLE001 — 旁路纪律（失败可见不阻断）
            logger.error("immune access persist failed (in-memory state intact): %s", exc)

    def _reload_from_persist(self) -> None:
        """启动回载：名单行真源回载（hit_count 等行状态一并还原）。回载失败不阻断
        构造（旁路纪律同写穿口），logger.error 可见+空名单起步，行真源仍在库下次
        启动重试——安全面如实记：回载故障窗口白名单-only 模式不生效（诚实边界②）。"""
        if self._persist is None:
            return
        try:
            rows = self._persist.load_all()
        except Exception as exc:  # noqa: BLE001 — 旁路纪律（失败可见不阻断）
            logger.error("immune access reload failed (starting with empty lists): %s", exc)
            return
        for row in rows:
            entry = IPEntry(
                ip=row["ip"],
                reason=row["reason"],
                added_at=row["added_at"],
                expires_at=row["expires_at"],
                hit_count=row["hit_count"],
            )
            if row["list_kind"] == "blacklist":
                self._blacklist[row["entry_key"]] = entry
            else:
                self._whitelist[row["entry_key"]] = entry

    def is_allowed(self, ip: str) -> dict:
        """Check if an IP is allowed."""
        with self._lock:
            # Check whitelist first — if whitelist exists, only whitelisted IPs allowed
            if self._whitelist and not self._is_in_list(ip, self._whitelist):
                return {"allowed": False, "reason": "IP not in whitelist", "action": "block"}

            # Check blacklist
            if self._is_in_list(ip, self._blacklist):
                entry = self._blacklist.get(self._normalize(ip)) or self._blacklist.get(ip)
                if entry:
                    entry.hit_count += 1
                    if entry.expires_at and time.time() > entry.expires_at:
                        del self._blacklist[self._normalize(ip)]
                        self._sync_entry("blacklist", self._normalize(ip), None)
                        return {"allowed": True, "reason": "blacklist expired", "action": "allow"}
                    self._sync_entry("blacklist", self._normalize(ip), entry)
                return {
                    "allowed": False,
                    "reason": entry.reason if entry else "blacklisted",
                    "action": "block",
                }

            return {"allowed": True, "reason": "", "action": "allow"}

    def blacklist_add(self, ip: str, reason: str = "", ttl_seconds: int | None = None):
        with self._lock:
            expires = time.time() + ttl_seconds if ttl_seconds else None
            key = self._normalize(ip)
            self._blacklist[key] = IPEntry(ip=ip, reason=reason, expires_at=expires)
            self._sync_entry("blacklist", key, self._blacklist[key])

    def blacklist_remove(self, ip: str):
        with self._lock:
            self._blacklist.pop(self._normalize(ip), None)
            self._sync_entry("blacklist", self._normalize(ip), None)

    def whitelist_add(self, ip: str, reason: str = ""):
        with self._lock:
            key = self._normalize(ip)
            self._whitelist[key] = IPEntry(ip=ip, reason=reason)
            self._sync_entry("whitelist", key, self._whitelist[key])

    def whitelist_remove(self, ip: str):
        with self._lock:
            self._whitelist.pop(self._normalize(ip), None)
            self._sync_entry("whitelist", self._normalize(ip), None)

    def list_blacklist(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "ip": e.ip,
                    "reason": e.reason,
                    "added_at": e.added_at,
                    "expires_at": e.expires_at,
                    "hit_count": e.hit_count,
                }
                for e in self._blacklist.values()
            ]

    def list_whitelist(self) -> list[dict]:
        with self._lock:
            return [
                {"ip": e.ip, "reason": e.reason, "added_at": e.added_at}
                for e in self._whitelist.values()
            ]

    def stats(self) -> dict:
        with self._lock:
            return {
                "blacklist_count": len(self._blacklist),
                "whitelist_count": len(self._whitelist),
            }

    @staticmethod
    def _normalize(ip: str) -> str:
        try:
            return str(ipaddress.ip_address(ip))
        except ValueError:
            return ip

    def _is_in_list(self, ip: str, ip_dict: dict) -> bool:
        normalized = self._normalize(ip)
        if normalized in ip_dict:
            return True
        # Check if IP is in any CIDR range
        for key in ip_dict:
            if "/" in key:
                try:
                    if ipaddress.ip_address(normalized) in ipaddress.ip_network(key, strict=False):
                        return True
                except ValueError:
                    pass
        return False
