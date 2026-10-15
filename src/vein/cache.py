"""In-memory LRU cache with TTL for file access hot path.

Future: swap backend to Redis for distributed caching.
"""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass


def cache_row_tenant(raw: object) -> str:
    """缓存行租户键单一判法（DEC-⑦-㉕b；⑦-② message_row_tenant / ⑦-㉔
    task_row_tenant / ⑦-㉕ upload_session_row_tenant 同款语义：自带键缺失/
    空白→'default' 兜底，键派生故障绝不吃掉落库）。raw=CacheEntry 行对象
    （读侧过滤）/dict 行/原始租户值（写侧落行）三形同收，写读同一判法禁
    双源漂移（F4b-㉗ 纪律）。"""
    if isinstance(raw, dict):
        raw = raw.get("tenant_id")
    elif hasattr(raw, "tenant_id"):
        raw = getattr(raw, "tenant_id")
    return str(raw or "").strip() or "default"


def _entry_index_key(entry: "CacheEntry") -> tuple[str, str]:
    """行→内建复合索引键（单一派生口，禁手抄）。"""
    return (cache_row_tenant(entry), entry.key)


@dataclass
class CacheEntry:
    key: str
    data: bytes
    size: int
    created_at: float
    ttl: float  # seconds
    hits: int = 0
    tenant_id: str = "default"

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > self.ttl


class CacheManager:
    """LRU cache with TTL eviction and size limits.

    DEC-⑦-㉕b 行租户键加轴：内建复合索引键 (tenant, key)——同 key 不同
    租户=两个独立缓存行互不覆盖/互不清（③a 幂等键按租户隔离同款语义，
    修前全局单键=跨租户互覆盖互删）；行归属由 cache_row_tenant 单一判法
    落行（缺失/空白→'default' 兜底）。方法显式 tenant_id=按行租户键闸：
    跨租户查=结构性不可见（与 not-found 同返回不差分泄露 ③c-1）、跨租户
    删=零破坏（③b-3）；None=系统视图（内部 file: 热缓存维护面）。
    """

    def __init__(self, max_size_mb: int = 256, default_ttl: int = 3600):
        self._max_size = max_size_mb * 1024 * 1024  # bytes
        self._default_ttl = default_ttl
        self._cache: OrderedDict[tuple[str, str], CacheEntry] = OrderedDict()
        self._current_size = 0
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str, tenant_id: str | None = None) -> bytes | None:
        entry = self.get_entry(key, tenant_id=tenant_id)
        return entry.data if entry is not None else None

    def get_entry(self, key: str, tenant_id: str | None = None) -> "CacheEntry | None":
        with self._lock:
            entry = self._lookup(key, tenant_id)
            if entry is None:
                self._misses += 1
                return None
            if entry.expired:
                self._remove(_entry_index_key(entry))
                self._misses += 1
                return None
            # Move to end (most recently used)
            self._cache.move_to_end(_entry_index_key(entry))
            entry.hits += 1
            self._hits += 1
            return entry

    def _lookup(self, key: str, tenant_id: str | None) -> "CacheEntry | None":
        """归属闸查表口：显式租户=复合键精确查（跨租户结构性不可见=与
        not-found 同返回）；None=系统视图（按 key 扫取 LRU 序最近使用行，
        既有内部调用 file:{uuid} 键全局唯一）。"""
        if tenant_id is not None:
            return self._cache.get((cache_row_tenant(tenant_id), key))
        for _, entry in self._cache.items():
            if entry.key == key:
                return entry
        return None

    def put(
        self,
        key: str,
        data: bytes,
        ttl: float | None = None,
        tenant_id: str | None = None,
    ) -> CacheEntry:
        row_tenant = cache_row_tenant(tenant_id)
        with self._lock:
            index_key = (row_tenant, key)
            # Remove existing if present（同租户同 key=覆盖既有语义；异租户
            # 同 key=另一独立行互不覆盖）
            if index_key in self._cache:
                self._remove(index_key)

            entry_size = len(data)

            # Evict LRU entries until we have space
            while self._current_size + entry_size > self._max_size and self._cache:
                self._evict_lru()

            entry = CacheEntry(
                key=key,
                data=data,
                size=entry_size,
                created_at=time.time(),
                ttl=ttl or self._default_ttl,
                tenant_id=row_tenant,
            )
            self._cache[index_key] = entry
            self._current_size += entry_size
            return entry

    def invalidate(self, key: str, tenant_id: str | None = None) -> bool:
        with self._lock:
            entry = self._lookup(key, tenant_id)
            if entry is None:
                return False
            return self._remove(_entry_index_key(entry))

    def clear(self, tenant_id: str | None = None) -> int:
        """显式租户=恒按行租户键清（防"只给租户"变相清全史，③a purge /
        ③c-2① clear 同款纪律）；None=系统视图清全部。"""
        with self._lock:
            if tenant_id is None:
                count = len(self._cache)
                self._cache.clear()
                self._current_size = 0
                return count
            row_tenant = cache_row_tenant(tenant_id)
            keys = [ik for ik in self._cache if ik[0] == row_tenant]
            for ik in keys:
                self._remove(ik)
            return len(keys)

    def cleanup_expired(self, tenant_id: str | None = None) -> int:
        """Remove expired entries. Returns count removed.

        显式租户=只清本租户的过期行（bulk 清理恒按行租户键，clear 同款
        纪律）；None=系统视图清全部过期行。"""
        with self._lock:
            row_tenant = cache_row_tenant(tenant_id) if tenant_id is not None else None
            expired_keys = [
                ik
                for ik, v in self._cache.items()
                if v.expired and (row_tenant is None or ik[0] == row_tenant)
            ]
            for k in expired_keys:
                self._remove(k)
            return len(expired_keys)

    @property
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._cache),
                "current_size_bytes": self._current_size,
                "max_size_bytes": self._max_size,
                "usage_percent": round(self._current_size / self._max_size * 100, 2)
                if self._max_size
                else 0,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total * 100, 2) if total else 0,
                "default_ttl_seconds": self._default_ttl,
            }

    def _remove(self, index_key: tuple[str, str]) -> bool:
        entry = self._cache.pop(index_key, None)
        if entry:
            self._current_size -= entry.size
            return True
        return False

    def _evict_lru(self):
        """Evict the least recently used entry."""
        if self._cache:
            _, entry = self._cache.popitem(last=False)
            self._current_size -= entry.size
