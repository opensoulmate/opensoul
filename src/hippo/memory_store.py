"""Short-term memory store with lifecycle management.

In-memory store for session-scoped memories with TTL, decay tracking,
and automatic archival/forgetting based on the configured decay strategy.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.hippo.decay import DecayEngine, DecayStrategy
from src.hippo.memory_redact import redact_for_memory


@dataclass
class Memory:
    """A single memory entry in the hippocampus."""

    memory_id: str
    session_id: str
    content: str
    importance: float = 0.5  # 0.0 to 1.0
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    access_count: int = 0
    archived: bool = False
    retention: float = 1.0
    # MEM-① 语义检索面：查询期懒计算的向量缓存（None=未算/已失效）。
    embedding: list[float] | None = None


class MemoryStore:
    """Thread-safe short-term memory store with decay-based lifecycle."""

    def __init__(
        self,
        max_entries: int = 10000,
        strategy: DecayStrategy = DecayStrategy.ACCESS_REINFORCED,
        half_life_hours: float = 24.0,
        archive_threshold: float = 0.3,
        forget_threshold: float = 0.05,
        persist: Any | None = None,
    ):
        self._memories: dict[str, Memory] = {}
        self._lock = threading.Lock()
        self.max_entries = max_entries
        # MEM-④ 持久化（可选）：persist=MemoryPersistence 时写穿落盘+懒回载，
        # 缺省 None=纯内存（既有语义字节零变化）。duck-typed 依赖 upsert/
        # upsert_many/delete/load_rows 四口（src/hippo/memory_persist.py）。
        self._persist = persist
        self._loaded = False
        self.decay_engine = DecayEngine(
            strategy=strategy,
            half_life_hours=half_life_hours,
            archive_threshold=archive_threshold,
            forget_threshold=forget_threshold,
        )

    def _ensure_loaded(self) -> None:
        """持久化回载（仅 persist 实例）：首次访问把库中已有行并入内存工作集。

        只补缺不覆盖（同 id 真源=内存中较新版本）；embedding 缓存刻意不回载
        （查询期懒算，memory_persist 诚实边界①）。无 persist 实例零开销零语义
        变化。懒回载（非构造期）：模块级单例 import 即构造，测试面每用例
        BRAIN_MEMORY_DB tmp 真源（conftest _isolate_brain_memory_db）。
        """
        if self._persist is None or self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            for row in self._persist.load_rows():
                mid = row["memory_id"]
                if mid in self._memories:
                    continue
                self._memories[mid] = Memory(**row)
            self._loaded = True

    def _sync(self, mem: Memory) -> None:
        """写穿单行（内存真源已变，落盘失败如实抛禁谎报 durable）。"""
        if self._persist is not None:
            self._persist.upsert(mem)

    def _sync_delete(self, memory_id: str) -> None:
        """写穿删行。"""
        if self._persist is not None:
            self._persist.delete(memory_id)

    def add(
        self,
        session_id: str,
        content: str,
        importance: float = 0.5,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        memory_id: str | None = None,
    ) -> Memory:
        """Add a new memory."""
        self._ensure_loaded()
        # ── kilocode MemoryRedact 采集前脱敏：短期记忆写入口同样不落凭据 ──
        content, redact_findings = redact_for_memory(content or "")
        if redact_findings:
            metadata = dict(metadata or {})
            metadata["memory_redact"] = {
                "count": len(redact_findings),
                "types": [f["type"] for f in redact_findings],
            }
        mem = Memory(
            memory_id=memory_id or str(uuid.uuid4()),
            session_id=session_id,
            content=content,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
        )
        with self._lock:
            # Evict if at capacity
            if len(self._memories) >= self.max_entries:
                self._evict_lowest_retention()
            self._memories[mem.memory_id] = mem
            self._sync(mem)
        return mem

    def get(self, memory_id: str) -> Memory | None:
        """Get a memory by ID, refreshing its access timestamp."""
        self._ensure_loaded()
        with self._lock:
            mem = self._memories.get(memory_id)
            if mem:
                mem.last_accessed_at = time.time()
                mem.access_count += 1
                self._sync(mem)
            return mem

    def peek(self, memory_id: str):
        """Read-only lookup（零副作用）：不刷 last_accessed_at 不增 access_count。

        归属闸专用（F3b-⑳）：闸门读取绝不可改写行（被拒请求"行零改写"），
        与 get() 的"读取即活动"语义刻意分开——闸读走 peek，过闸后才 get()。
        （SessionManager.peek F3b-⑱ 同款纪律。）
        """
        self._ensure_loaded()
        with self._lock:
            return self._memories.get(memory_id)

    def search(
        self,
        session_id: str | None = None,
        tags: list[str] | None = None,
        min_retention: float | None = None,
        include_archived: bool = False,
        limit: int = 50,
        query: str | None = None,
    ) -> list[Memory]:
        """Search memories with filters.

        query=关键词过滤（字面子串匹配 content，大小写不敏感；None/空=零过滤
        契约零变化）。只匹配 content——tags 已有独立过滤轴，两轴刻意分开。
        """
        self._ensure_loaded()
        needle = query.lower() if query else None
        results = []
        with self._lock:
            for mem in self._memories.values():
                if session_id and mem.session_id != session_id:
                    continue
                if not include_archived and mem.archived:
                    continue
                if tags and not any(t in mem.tags for t in tags):
                    continue
                if needle is not None and needle not in mem.content.lower():
                    continue
                if min_retention is not None:
                    decay = self.decay_engine.calculate(
                        mem.created_at, mem.last_accessed_at, mem.access_count, mem.importance
                    )
                    mem.retention = decay.retention
                    if mem.retention < min_retention:
                        continue
                results.append(mem)
        # Sort by retention descending
        results.sort(key=lambda m: m.retention, reverse=True)
        return results[:limit]

    def update(self, memory_id: str, **kwargs) -> Memory | None:
        """Update memory fields."""
        self._ensure_loaded()
        # ── kilocode MemoryRedact：更新路径的内容同样先脱敏 ──
        if isinstance(kwargs.get("content"), str):
            kwargs["content"], _findings = redact_for_memory(kwargs["content"])
        with self._lock:
            mem = self._memories.get(memory_id)
            if not mem:
                return None
            for key in ("content", "importance", "tags", "metadata"):
                if key in kwargs:
                    setattr(mem, key, kwargs[key])
            if "content" in kwargs:
                # MEM-①：内容变更→旧向量陈旧，缓存失效（禁拿旧文向量排名）
                mem.embedding = None
            mem.last_accessed_at = time.time()
            self._sync(mem)
            return mem

    def apply_embeddings(self, embeddings: dict[str, list[float]]) -> int:
        """批量回填行向量缓存（MEM-① 语义检索面查询期懒计算，锁内落行）。

        返回真落行数：行已消失/空向量不算（空向量=embedding 失败禁缓存）。
        """
        self._ensure_loaded()
        applied = 0
        with self._lock:
            for mid, vec in embeddings.items():
                mem = self._memories.get(mid)
                if mem is None or not vec:
                    continue
                mem.embedding = list(vec)
                applied += 1
        return applied

    def delete(self, memory_id: str) -> bool:
        """Delete a memory."""
        self._ensure_loaded()
        with self._lock:
            removed = self._memories.pop(memory_id, None) is not None
            if removed:
                self._sync_delete(memory_id)
            return removed

    def run_decay_cycle(self) -> dict:
        """Run a full decay cycle: update retention, archive and forget as needed.

        Returns stats about what happened.
        """
        to_archive = []
        to_forget = []
        scanned = []
        updated = 0

        self._ensure_loaded()
        with self._lock:
            for mem in self._memories.values():
                if mem.archived:
                    continue
                decay = self.decay_engine.calculate(
                    mem.created_at, mem.last_accessed_at, mem.access_count, mem.importance
                )
                mem.retention = decay.retention
                scanned.append(mem)
                updated += 1

                if decay.should_forget:
                    to_forget.append(mem.memory_id)
                elif decay.should_archive:
                    to_archive.append(mem.memory_id)

            # Archive
            for mid in to_archive:
                if mid in self._memories:
                    self._memories[mid].archived = True

            # Forget
            for mid in to_forget:
                self._memories.pop(mid, None)

            # MEM-④ 写穿：retention 刷新/归档批量单事务落盘、遗忘行删行（与
            # 内存变更同锁原子；无 persist 实例零开销零语义变化）。
            if self._persist is not None:
                forget_set = set(to_forget)
                self._persist.upsert_many([m for m in scanned if m.memory_id not in forget_set])
                for mid in to_forget:
                    self._sync_delete(mid)

        return {
            "updated": updated,
            "archived": len(to_archive),
            "forgotten": len(to_forget),
            "total_active": sum(1 for m in self._memories.values() if not m.archived),
            "total_archived": sum(1 for m in self._memories.values() if m.archived),
        }

    def get_stats(self) -> dict:
        """Get store statistics."""
        self._ensure_loaded()
        with self._lock:
            total = len(self._memories)
            active = sum(1 for m in self._memories.values() if not m.archived)
            archived = total - active
            sessions = set(m.session_id for m in self._memories.values())
            total_accesses = sum(m.access_count for m in self._memories.values())

        return {
            "total_memories": total,
            "active": active,
            "archived": archived,
            "sessions": len(sessions),
            "total_accesses": total_accesses,
            "max_entries": self.max_entries,
            "usage_percent": round(total / self.max_entries * 100, 1) if self.max_entries else 0,
            "strategy": self.decay_engine.strategy.value,
            "half_life_hours": self.decay_engine.half_life_hours,
            "archive_threshold": self.decay_engine.archive_threshold,
            "forget_threshold": self.decay_engine.forget_threshold,
        }

    def _evict_lowest_retention(self):
        """Evict the memory with lowest retention (must hold lock)."""
        lowest_id = None
        lowest_ret = 2.0
        for mid, mem in self._memories.items():
            if mem.archived:
                lowest_id = mid
                lowest_ret = -1
                break
            decay = self.decay_engine.calculate(
                mem.created_at, mem.last_accessed_at, mem.access_count, mem.importance
            )
            if decay.retention < lowest_ret:
                lowest_ret = decay.retention
                lowest_id = mid
        if lowest_id:
            self._memories.pop(lowest_id, None)
            self._sync_delete(lowest_id)
