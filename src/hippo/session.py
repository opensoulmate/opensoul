"""Session lifecycle management for OpenHippo.

Tracks conversation sessions, their metadata, and manages
session-scoped memory grouping.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum


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
    ):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self.idle_timeout = idle_timeout_seconds
        self.expire_timeout = expire_timeout_seconds
        self.max_sessions = max_sessions

    def create(self, user_id: str = "", title: str = "", metadata: dict | None = None) -> Session:
        """Create a new session."""
        session = Session(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            title=title or f"Session {int(time.time())}",
            metadata=metadata or {},
        )
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                self._evict_oldest()
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """Get a session, refreshing its last_active_at if active."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.status == SessionStatus.ACTIVE:
                session.last_active_at = time.time()
            return session

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
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                self._note_activity_locked(session)
                return True
            return False

    def increment_memory_count(self, session_id: str, count: int = 1):
        """Increment the memory count for a session.

        A memory write is session activity (H1-②b-②): the timestamp/status
        refresh goes through the same _note_activity_locked primitive as
        touch(), so a long session that keeps receiving memories no longer
        goes stale/idle/expired behind the writer's back.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.memory_count += count
                self._note_activity_locked(session)

    def list_sessions(
        self,
        user_id: str | None = None,
        status: SessionStatus | None = None,
        limit: int = 50,
    ) -> list[Session]:
        """List sessions with optional filters."""
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
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.status = SessionStatus.ARCHIVED
                return True
            return False

    def delete(self, session_id: str) -> bool:
        """Delete a session."""
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def run_lifecycle_check(self) -> dict:
        """Check all sessions and update status based on timeouts.

        Returns stats about status changes.
        """
        now = time.time()
        idle_count = 0
        expired_count = 0

        with self._lock:
            for session in self._sessions.values():
                if session.status == SessionStatus.ARCHIVED:
                    continue

                inactive = now - session.last_active_at

                if inactive > self.expire_timeout:
                    session.status = SessionStatus.EXPIRED
                    expired_count += 1
                elif inactive > self.idle_timeout:
                    if session.status == SessionStatus.ACTIVE:
                        session.status = SessionStatus.IDLE
                        idle_count += 1

        return {
            "checked": len(self._sessions),
            "newly_idle": idle_count,
            "newly_expired": expired_count,
        }

    def get_stats(self) -> dict:
        """Get session statistics."""
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
