"""Session lifecycle management for OpenHippo.

Tracks conversation sessions, their metadata, and manages
session-scoped memory grouping.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SessionStatus(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    EXPIRED = "expired"
    ARCHIVED = "archived"


@dataclass
class Session:
    """A conversation session."""

    session_id: str
    user_id: str = ""
    title: str = ""
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    memory_count: int = 0
    total_tokens: int = 0
    metadata: dict = field(default_factory=dict)


class SessionManager:
    """Manages session lifecycle with idle detection and auto-expiry."""

    def __init__(
        self,
        idle_timeout_seconds: float = 1800,  # 30 min
        expire_timeout_seconds: float = 86400,  # 24 hours
        max_sessions: int = 500,
        persist: Any | None = None,
    ):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self.idle_timeout = idle_timeout_seconds
        self.expire_timeout = expire_timeout_seconds
        self.max_sessions = max_sessions
        # MEM-④ 持久化（可选）：persist=SessionPersistence 时写穿落盘+懒回载，
        # 缺省 None=纯内存（既有语义字节零变化）。duck-typed 依赖 upsert/
        # upsert_many/delete/load_rows 四口（src/hippo/memory_persist.py）。
        self._persist = persist
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """持久化回载（仅 persist 实例）：首次访问把库中已有会话行并入内存真源。

        只补缺不覆盖；status 原样串归一 SessionStatus 枚举（持久化层不 import
        本模块防循环依赖）。懒回载理由同 MemoryStore._ensure_loaded。
        """
        if self._persist is None or self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            for row in self._persist.load_rows():
                sid = row["session_id"]
                if sid in self._sessions:
                    continue
                row = dict(row)
                row["status"] = SessionStatus(row["status"])
                self._sessions[sid] = Session(**row)
            self._loaded = True

    def _sync(self, session: Session) -> None:
        """写穿单行（内存真源已变，落盘失败如实抛禁谎报 durable）。"""
        if self._persist is not None:
            self._persist.upsert(session)

    def _sync_delete(self, session_id: str) -> None:
        """写穿删行。"""
        if self._persist is not None:
            self._persist.delete(session_id)

    def create(
        self,
        user_id: str = "",
        title: str = "",
        metadata: dict | None = None,
        session_id: str | None = None,
    ) -> Session:
        """Create a new session.

        注册键=行身份单一源（F3b-⑲）：session_id 显式给定时同时作为 Session
        身份与 _sessions 注册键；缺省生成 uuid4（键==身份恒同源）。修前调用方
        create() 后改 .session_id 不改 dict 键=键/身份错位孤儿行（同 id 逐条
        记忆各建一孤儿会话、increment_memory_count/touch 按 id 恒查不到）。
        注册键已存在=如实 ValueError，禁静默覆盖既有会话。
        """
        self._ensure_loaded()
        session = Session(
            session_id=session_id if session_id is not None else str(uuid.uuid4()),
            user_id=user_id,
            title=title or f"Session {int(time.time())}",
            metadata=metadata or {},
        )
        with self._lock:
            if session.session_id in self._sessions:
                raise ValueError(f"session already exists: {session.session_id}")
            if len(self._sessions) >= self.max_sessions:
                self._evict_oldest()
            self._sessions[session.session_id] = session
            self._sync(session)
        return session

    def get(self, session_id: str) -> Session | None:
        """Get a session, refreshing its last_active_at if active."""
        self._ensure_loaded()
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.status == SessionStatus.ACTIVE:
                session.last_active_at = time.time()
                self._sync(session)
            return session

    def peek(self, session_id: str) -> Session | None:
        """Read-only lookup（零副作用）：不刷新 last_active_at 不改状态。

        归属闸专用（F3b-⑱）：闸门读取绝不可改写行（被拒请求"行零改写"），
        与 get() 的"读取即活动"语义刻意分开——闸读走 peek，过闸后才 get()。
        """
        self._ensure_loaded()
        with self._lock:
            return self._sessions.get(session_id)

    def _note_activity_locked(self, session: Session) -> None:
        """Record session activity (must hold self._lock).

        Single source for "this session is being used" semantics (H1-②b-②):
        refreshes last_active_at and revives IDLE/EXPIRED -> ACTIVE.  ARCHIVED
        keeps its status — archiving is explicit user intent that incidental
        activity must not silently undo (the timestamp still refreshes so the
        record stays truthful).
        """
        session.last_active_at = time.time()
        if session.status in (SessionStatus.IDLE, SessionStatus.EXPIRED):
            session.status = SessionStatus.ACTIVE

    def touch(self, session_id: str) -> bool:
        """Record session activity (refresh timestamp, revive from idle/expiry).

        Returns True if the session existed and the activity was recorded.
        """
        self._ensure_loaded()
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                self._note_activity_locked(session)
                self._sync(session)
                return True
            return False

    def increment_memory_count(self, session_id: str, count: int = 1):
        """Adjust the memory count for a session (default +1, delete path passes -1).

        计数对称（MEM-③）：_memory_write 写入 +1、brain 删除口删除成功 -1，
        count 为有符号增量；会话行缺席如实 no-op（孤儿记忆无计数可退）。

        A memory write is session activity (H1-②b-②): the timestamp/status
        refresh goes through the same _note_activity_locked primitive as
        touch(), so a long session that keeps receiving memories no longer
        goes stale/idle/expired behind the writer's back.
        """
        self._ensure_loaded()
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.memory_count += count
                self._note_activity_locked(session)
                self._sync(session)

    def list_sessions(
        self,
        user_id: str | None = None,
        status: SessionStatus | None = None,
        limit: int = 50,
    ) -> list[Session]:
        """List sessions with optional filters."""
        self._ensure_loaded()
        results = []
        with self._lock:
            for s in self._sessions.values():
                if user_id and s.user_id != user_id:
                    continue
                if status and s.status != status:
                    continue
                results.append(s)
        results.sort(key=lambda s: s.last_active_at, reverse=True)
        return results[:limit]

    def archive(self, session_id: str) -> bool:
        """Archive a session."""
        self._ensure_loaded()
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.status = SessionStatus.ARCHIVED
                self._sync(session)
                return True
            return False

    def delete(self, session_id: str) -> bool:
        """Delete a session."""
        self._ensure_loaded()
        with self._lock:
            removed = self._sessions.pop(session_id, None) is not None
            if removed:
                self._sync_delete(session_id)
            return removed

    def run_lifecycle_check(self) -> dict:
        """Check all sessions and update status based on timeouts.

        Returns stats about status changes.
        """
        now = time.time()
        idle_count = 0
        expired_count = 0
        changed = []

        self._ensure_loaded()
        with self._lock:
            for session in self._sessions.values():
                if session.status == SessionStatus.ARCHIVED:
                    continue

                inactive = now - session.last_active_at

                if inactive > self.expire_timeout:
                    session.status = SessionStatus.EXPIRED
                    expired_count += 1
                    changed.append(session)
                elif inactive > self.idle_timeout:
                    if session.status == SessionStatus.ACTIVE:
                        session.status = SessionStatus.IDLE
                        idle_count += 1
                        changed.append(session)

            # MEM-④ 写穿：状态迁移批量单事务落盘（与内存变更同锁原子）
            if self._persist is not None:
                self._persist.upsert_many(changed)

        return {
            "checked": len(self._sessions),
            "newly_idle": idle_count,
            "newly_expired": expired_count,
        }

    def get_stats(self) -> dict:
        """Get session statistics."""
        self._ensure_loaded()
        with self._lock:
            total = len(self._sessions)
            by_status = {}
            for s in self._sessions.values():
                by_status[s.status.value] = by_status.get(s.status.value, 0) + 1
            total_memories = sum(s.memory_count for s in self._sessions.values())

        return {
            "total_sessions": total,
            "by_status": by_status,
            "total_memories_tracked": total_memories,
            "idle_timeout_seconds": self.idle_timeout,
            "expire_timeout_seconds": self.expire_timeout,
            "max_sessions": self.max_sessions,
        }

    def _evict_oldest(self):
        """Evict the oldest expired or archived session (must hold lock)."""
        # Priority: expired first, then archived, then oldest idle
        candidates = []
        for sid, s in self._sessions.items():
            if s.status == SessionStatus.EXPIRED:
                candidates.append((0, s.last_active_at, sid))
            elif s.status == SessionStatus.ARCHIVED:
                candidates.append((1, s.last_active_at, sid))
            elif s.status == SessionStatus.IDLE:
                candidates.append((2, s.last_active_at, sid))
        if candidates:
            candidates.sort()
            self._sessions.pop(candidates[0][2], None)
            self._sync_delete(candidates[0][2])
