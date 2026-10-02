"""Trajectory store — records Agent execution events to SQLite, supports replay and fork."""

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from src.database.postgres import db_pool

logger = logging.getLogger(__name__)


class EventType(StrEnum):
    """Types of trajectory events."""

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    USER_INPUT = "user_input"
    LLM_CALL = "llm_call"
    LLM_RESPONSE = "llm_response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    AGENT_DISPATCH = "agent_dispatch"
    AGENT_RESULT = "agent_result"
    SPAN = "span"
    ERROR = "error"
    CHECKPOINT = "checkpoint"
    BRANCH = "branch"
    CUSTOM = "custom"


@dataclass
class TrajectoryEvent:
    """A single event in an agent execution trajectory."""

    id: str = ""
    session_id: str = ""
    parent_event_id: str | None = None
    event_type: str = ""
    agent_id: str = ""
    content: str = ""
    metadata_json: str = "{}"
    token_usage: int = 0
    duration_ms: float = 0.0
    status: str = "ok"
    created_at: str = ""
    end_at: str | None = None

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    @property
    def metadata(self) -> dict:
        try:
            return json.loads(self.metadata_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["metadata"] = self.metadata
        return d


@dataclass
class TrajectorySession:
    """A complete agent execution session with its events."""

    id: str = ""
    agent_id: str = ""
    task_description: str = ""
    status: str = "running"
    forked_from: str | None = None
    fork_point_event_id: str | None = None
    total_events: int = 0
    total_tokens: int = 0
    total_duration_ms: float = 0.0
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    ended_at: str | None = None

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class ScoreDataType(StrEnum):
    """langfuse Score data types."""

    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"


# langfuse Score sources: API (programmatic) / EVAL (LLM judge or code eval) /
# ANNOTATION (human). Normalized to lowercase snake_case here.
SCORE_SOURCES = ("api", "llm_judge", "code_eval", "human")


@dataclass
class TrajectoryScore:
    """A quality score attached to a session or a single trace event.

    Mirrors the langfuse Score entity — the third layer of the
    Trace → Observation → Score observability model. Scores are how
    "did this run get better" becomes a queryable metric instead of a
    feeling (SUMMARY.md P0-5 评估闭环的存储前提).
    """

    id: str = ""
    session_id: str = ""
    trace_id: str | None = None
    name: str = ""
    value: float = 0.0
    string_value: str | None = None
    data_type: str = ScoreDataType.NUMERIC.value
    comment: str = ""
    source: str = "api"
    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


def gen_ai_attributes(
    system: str,
    model: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    finish_reason: str | None = None,
) -> dict:
    """Build OpenTelemetry GenAI semantic-convention attributes.

    SUMMARY.md §2 signal 5: OTel GenAI semconv was officially adopted —
    trajectory metadata should speak `gen_ai.*` so it can be exported to
    any OTel backend without translation.
    """
    attrs: dict = {"gen_ai.system": system, "gen_ai.request.model": model}
    if input_tokens is not None:
        attrs["gen_ai.usage.input_tokens"] = input_tokens
    if output_tokens is not None:
        attrs["gen_ai.usage.output_tokens"] = output_tokens
    if finish_reason is not None:
        attrs["gen_ai.response.finish_reasons"] = [finish_reason]
    return attrs


class TrajectoryStore:
    """Persistent storage for trajectory sessions and events."""

    async def ensure_tables(self):
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS trajectory_sessions (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL DEFAULT '',
                task_description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                forked_from TEXT,
                fork_point_event_id TEXT,
                total_events INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                total_duration_ms REAL NOT NULL DEFAULT 0,
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                ended_at TEXT
            )
        """)
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS trajectory_events (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_event_id TEXT,
                event_type TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                token_usage INTEGER NOT NULL DEFAULT 0,
                duration_ms REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok',
                created_at TEXT NOT NULL
            )
        """)
        await db_pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_traj_events_session
            ON trajectory_events(session_id, created_at)
        """)
        # Migration: span-level end timestamp (langfuse Observation layer).
        # SQLite has no ADD COLUMN IF NOT EXISTS — a duplicate-column error on
        # re-run means the migration already applied and is safe to swallow.
        try:
            await db_pool.execute("ALTER TABLE trajectory_events ADD COLUMN end_at TEXT")
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate column" not in msg and "already exists" not in msg:
                raise
        await db_pool.execute("""
            CREATE TABLE IF NOT EXISTS trajectory_scores (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                trace_id TEXT,
                name TEXT NOT NULL,
                value REAL NOT NULL DEFAULT 0,
                string_value TEXT,
                data_type TEXT NOT NULL DEFAULT 'numeric',
                comment TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'api',
                created_at TEXT NOT NULL
            )
        """)
        await db_pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_traj_scores_session
            ON trajectory_scores(session_id, name)
        """)

    # ── Session CRUD ─────────────────────────────────────────

    async def create_session(
        self,
        id: str = "",
        agent_id: str = "",
        task_description: str = "",
        forked_from: str | None = None,
        fork_point_event_id: str | None = None,
        tags: list[str] | None = None,
    ) -> TrajectorySession:
        await self.ensure_tables()
        s = TrajectorySession(
            id=id,
            agent_id=agent_id,
            task_description=task_description,
            forked_from=forked_from,
            fork_point_event_id=fork_point_event_id,
            tags=tags or [],
        )
        await db_pool.execute(
            """INSERT INTO trajectory_sessions
               (id, agent_id, task_description, status, forked_from, fork_point_event_id,
                total_events, total_tokens, total_duration_ms, tags_json, created_at, ended_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            s.id,
            s.agent_id,
            s.task_description,
            s.status,
            s.forked_from,
            s.fork_point_event_id,
            s.total_events,
            s.total_tokens,
            s.total_duration_ms,
            json.dumps(s.tags),
            s.created_at,
            s.ended_at,
        )
        return s

    async def get_session(self, session_id: str) -> TrajectorySession | None:
        await self.ensure_tables()
        row = await db_pool.fetchrow("SELECT * FROM trajectory_sessions WHERE id = ?", session_id)
        if not row:
            return None
        return self._row_to_session(row)

    async def list_sessions(
        self, agent_id: str = "", status: str = "", limit: int = 50, offset: int = 0
    ) -> list[TrajectorySession]:
        await self.ensure_tables()
        clauses = []
        params: list[Any] = []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend([limit, offset])
        rows = await db_pool.fetch(
            f"SELECT * FROM trajectory_sessions{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            *params,
        )
        return [self._row_to_session(r) for r in rows]

    async def end_session(self, session_id: str, status: str = "completed"):
        """收口session（04:20轮遗留#2销账：session.status不再恒running）。

        session.status = 活跃度生命周期：running（有在途/最近活动）↔ completed
        （已收口不再活跃）。首收口生效（ended_at只记第一次，重复收口no-op）；
        后续add_event自愈翻回running（见add_event）。span/event级status仍是
        结果语义（ok/error）——失败可见性不在此层。
        """
        await self.ensure_tables()
        await db_pool.execute(
            """UPDATE trajectory_sessions
               SET status = ?, ended_at = ?
               WHERE id = ? AND (status = 'running' OR ended_at IS NULL)""",
            status,
            datetime.now(UTC).isoformat(),
            session_id,
        )

    async def sweep_idle_sessions(self, idle_seconds: float = 1800.0) -> int:
        """闲置收口兜底：running但超过idle_seconds无事件的session翻completed。

        显式收口点（acp-proxy ws_acp连接teardown调end_session）覆盖不到的面
        （进程崩溃没走到收尾/opensoul自身写入方/HTTP工具入口）由本清扫兜底，
        保证session列表不会永远一片running。最后活跃时间=该session最新事件的
        created_at（无事件回退session.created_at；ISO同格式字符串字典序=时间序）。
        返回本次翻转的session数。清扫只翻running行——已收口/其他status不动。
        """
        await self.ensure_tables()
        cutoff = (datetime.now(UTC) - timedelta(seconds=max(float(idle_seconds), 0.0))).isoformat()
        rows = await db_pool.fetch(
            """SELECT s.id FROM trajectory_sessions s
               WHERE s.status = 'running' AND COALESCE(
                   (SELECT MAX(e.created_at) FROM trajectory_events e
                    WHERE e.session_id = s.id),
                   s.created_at) <= ?""",
            cutoff,
        )
        flipped = 0
        for row in rows:
            sid = row["id"]
            await self.end_session(sid, status="completed")
            flipped += 1
        return flipped

    async def delete_session(self, session_id: str):
        await self.ensure_tables()
        await db_pool.execute("DELETE FROM trajectory_scores WHERE session_id = ?", session_id)
        await db_pool.execute("DELETE FROM trajectory_events WHERE session_id = ?", session_id)
        await db_pool.execute("DELETE FROM trajectory_sessions WHERE id = ?", session_id)

    async def count_sessions(self) -> int:
        await self.ensure_tables()
        row = await db_pool.fetchrow("SELECT COUNT(*) as cnt FROM trajectory_sessions")
        return row["cnt"] if row else 0

    # ── Event CRUD ───────────────────────────────────────────

    async def add_event(self, event: TrajectoryEvent) -> TrajectoryEvent:
        await self.ensure_tables()
        if not event.session_id:
            raise ValueError("event.session_id is required")
        await db_pool.execute(
            """INSERT INTO trajectory_events
               (id, session_id, parent_event_id, event_type, agent_id,
                content, metadata_json, token_usage, duration_ms, status, created_at, end_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            event.id,
            event.session_id,
            event.parent_event_id,
            event.event_type,
            event.agent_id,
            event.content,
            event.metadata_json,
            event.token_usage,
            event.duration_ms,
            event.status,
            event.created_at,
            event.end_at,
        )
        # Update session counters + 活跃度自愈重开（session收口语义，04:20轮遗留#2）：
        # 事件=活动——已收口（completed）的session被新事件翻回running并清ended_at，
        # 长生命周期chat session跨连接/跨天继续时不卡死在completed。
        await db_pool.execute(
            """UPDATE trajectory_sessions
               SET total_events = total_events + 1,
                   total_tokens = total_tokens + ?,
                   status = 'running',
                   ended_at = NULL
               WHERE id = ?""",
            event.token_usage,
            event.session_id,
        )
        return event

    async def get_events(self, session_id: str, limit: int = 500) -> list[TrajectoryEvent]:
        await self.ensure_tables()
        rows = await db_pool.fetch(
            """SELECT * FROM trajectory_events
               WHERE session_id = ?
               ORDER BY created_at ASC
               LIMIT ?""",
            session_id,
            limit,
        )
        return [self._row_to_event(r) for r in rows]

    async def get_event(self, event_id: str) -> TrajectoryEvent | None:
        await self.ensure_tables()
        row = await db_pool.fetchrow("SELECT * FROM trajectory_events WHERE id = ?", event_id)
        return self._row_to_event(row) if row else None

    async def search_events(
        self, session_id: str = "", event_type: str = "", keyword: str = "", limit: int = 50
    ) -> list[TrajectoryEvent]:
        await self.ensure_tables()
        clauses = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if keyword:
            clauses.append("content LIKE ?")
            params.append(f"%{keyword}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = await db_pool.fetch(
            f"SELECT * FROM trajectory_events{where} ORDER BY created_at DESC LIMIT ?",
            *params,
        )
        return [self._row_to_event(r) for r in rows]

    # ── Spans (langfuse Observation layer) ───────────────────

    async def end_event(
        self,
        event_id: str,
        status: str = "ok",
        token_usage: int | None = None,
    ) -> TrajectoryEvent | None:
        """Close a span-style event: stamp ``end_at`` and derive duration.

        langfuse Observation semantics — an observation without an end is
        still "open". Duration is computed here from created_at→now rather
        than trusted from the caller, so every span's duration comes from
        one clock. ``token_usage`` folds back into the session total.
        """
        await self.ensure_tables()
        row = await db_pool.fetchrow("SELECT * FROM trajectory_events WHERE id = ?", event_id)
        if not row:
            return None
        ev = self._row_to_event(row)
        now = datetime.now(UTC)
        end_at = now.isoformat()
        duration = 0.0
        try:
            started = datetime.fromisoformat(ev.created_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            duration = max((now - started).total_seconds() * 1000.0, 0.0)
        except ValueError:
            # Unparseable created_at — leave duration at 0 rather than fail
            # the close (mirrors deepseek "spill failure must not turn a
            # successful call into an error").
            pass
        new_tokens = ev.token_usage if token_usage is None else token_usage
        await db_pool.execute(
            """UPDATE trajectory_events
               SET end_at = ?, duration_ms = ?, status = ?, token_usage = ?
               WHERE id = ?""",
            end_at,
            duration,
            status,
            new_tokens,
            event_id,
        )
        if token_usage is not None and token_usage != ev.token_usage:
            await db_pool.execute(
                """UPDATE trajectory_sessions
                   SET total_tokens = total_tokens + ?
                   WHERE id = ?""",
                token_usage - ev.token_usage,
                ev.session_id,
            )
        ev.end_at = end_at
        ev.duration_ms = duration
        ev.status = status
        ev.token_usage = new_tokens
        return ev

    async def get_trace_tree(self, session_id: str, limit: int = 500) -> dict:
        """Build the nested observation tree for a session.

        langfuse Trace→Observation model: ``parent_event_id`` links become
        parent/child edges, each node carries subtree rollups (events /
        tokens / duration — the Warp subtree-rollup pattern). Corrupt
        lineage must not hang the reader: dangling parents are promoted to
        roots and cycles are cut by treating the cycle member as a root.
        """
        await self.ensure_tables()
        events = await self.get_events(session_id, limit=limit)
        nodes: dict[str, dict] = {}
        order: list[str] = []
        for ev in events:
            node = ev.to_dict()
            node["children"] = []
            nodes[ev.id] = node
            order.append(ev.id)

        roots: list[dict] = []
        for eid in order:
            node = nodes[eid]
            parent_id = node.get("parent_event_id")
            if not parent_id or parent_id == eid or parent_id not in nodes:
                roots.append(node)
                continue
            # Cycle guard: walk the ancestor chain with a seen-set. If we
            # revisit a node the lineage is corrupt — promote to root.
            seen = {eid}
            cur: str | None = parent_id
            cyclic = False
            while cur:
                if cur in seen:
                    cyclic = True
                    break
                seen.add(cur)
                parent_node = nodes.get(cur)
                cur = parent_node.get("parent_event_id") if parent_node else None
            if cyclic:
                roots.append(node)
            else:
                nodes[parent_id]["children"].append(node)

        max_depth = 0

        def rollup(node: dict, depth: int) -> None:
            nonlocal max_depth
            max_depth = max(max_depth, depth)
            node["depth"] = depth
            subtree_tokens = node["token_usage"]
            subtree_duration = node["duration_ms"]
            subtree_events = 1
            for child in node["children"]:
                rollup(child, depth + 1)
                subtree_tokens += child["subtree_tokens"]
                subtree_duration += child["subtree_duration_ms"]
                subtree_events += child["subtree_events"]
            node["subtree_tokens"] = subtree_tokens
            node["subtree_duration_ms"] = round(subtree_duration, 1)
            node["subtree_events"] = subtree_events

        for root in roots:
            rollup(root, 0)

        return {
            "session_id": session_id,
            "roots": roots,
            "total_events": len(events),
            "max_depth": max_depth,
        }

    # ── Scores (langfuse Score layer) ────────────────────────

    async def add_score(self, score: TrajectoryScore) -> TrajectoryScore:
        """Persist a quality score against a session (optionally one event)."""
        await self.ensure_tables()
        if not score.session_id:
            raise ValueError("score.session_id is required")
        if not score.name:
            raise ValueError("score.name is required")
        if score.data_type not in {t.value for t in ScoreDataType}:
            raise ValueError(f"score.data_type must be one of {[t.value for t in ScoreDataType]}")
        if score.source not in SCORE_SOURCES:
            raise ValueError(f"score.source must be one of {list(SCORE_SOURCES)}")
        await db_pool.execute(
            """INSERT INTO trajectory_scores
               (id, session_id, trace_id, name, value, string_value,
                data_type, comment, source, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            score.id,
            score.session_id,
            score.trace_id,
            score.name,
            score.value,
            score.string_value,
            score.data_type,
            score.comment,
            score.source,
            score.created_at,
        )
        return score

    async def get_scores(
        self,
        session_id: str = "",
        name: str = "",
        source: str = "",
        limit: int = 100,
    ) -> list[TrajectoryScore]:
        await self.ensure_tables()
        clauses = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if name:
            clauses.append("name = ?")
            params.append(name)
        if source:
            clauses.append("source = ?")
            params.append(source)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = await db_pool.fetch(
            f"SELECT * FROM trajectory_scores{where} ORDER BY created_at DESC LIMIT ?",
            *params,
        )
        return [self._row_to_score(r) for r in rows]

    async def get_score_summary(self, session_id: str = "") -> dict:
        """Aggregate scores per name — the "is it getting better" query."""
        await self.ensure_tables()
        where = " WHERE session_id = ?" if session_id else ""
        params: tuple = (session_id,) if session_id else ()
        rows = await db_pool.fetch(
            f"""SELECT name,
                       COUNT(*) as cnt,
                       AVG(value) as avg_value,
                       MIN(value) as min_value,
                       MAX(value) as max_value,
                       SUM(CASE WHEN source = 'llm_judge' THEN 1 ELSE 0 END) as judge_count,
                       SUM(CASE WHEN source = 'human' THEN 1 ELSE 0 END) as human_count
                FROM trajectory_scores{where}
                GROUP BY name
                ORDER BY name""",
            *params,
        )
        scores = []
        for row in rows:
            scores.append(
                {
                    "name": row["name"],
                    "count": row["cnt"] or 0,
                    "avg_value": round(row["avg_value"] or 0, 3),
                    "min_value": row["min_value"] or 0,
                    "max_value": row["max_value"] or 0,
                    "llm_judge_count": row["judge_count"] or 0,
                    "human_count": row["human_count"] or 0,
                }
            )
        return {"scores": scores, "total_score_names": len(scores)}

    # ── Fork (Branch) ────────────────────────────────────────

    async def fork_session(
        self, source_session_id: str, fork_point_event_id: str, new_agent_id: str = ""
    ) -> TrajectorySession:
        """Fork a session at a specific event — copies events up to the fork point
        and creates a new session for branching execution."""
        source = await self.get_session(source_session_id)
        if not source:
            raise ValueError(f"Source session {source_session_id} not found")

        # Create new session marked as fork
        new_session = await self.create_session(
            agent_id=new_agent_id or source.agent_id,
            task_description=f"[Fork] {source.task_description}",
            forked_from=source_session_id,
            fork_point_event_id=fork_point_event_id,
            tags=source.tags + ["fork"],
        )

        # Copy events up to and including the fork point
        events = await self.get_events(source_session_id)
        copying = True
        for ev in events:
            if not copying:
                break
            new_ev = TrajectoryEvent(
                session_id=new_session.id,
                parent_event_id=ev.parent_event_id,
                event_type=ev.event_type,
                agent_id=ev.agent_id,
                content=ev.content,
                metadata_json=ev.metadata_json,
                token_usage=ev.token_usage,
                duration_ms=ev.duration_ms,
                status=ev.status,
            )
            await self.add_event(new_ev)
            if ev.id == fork_point_event_id:
                copying = False

        # Add a branch marker event
        await self.add_event(
            TrajectoryEvent(
                session_id=new_session.id,
                event_type=EventType.BRANCH.value,
                content=f"Branched from session {source_session_id} at event {fork_point_event_id}",
                metadata_json=json.dumps(
                    {
                        "source_session": source_session_id,
                        "fork_event": fork_point_event_id,
                    }
                ),
            )
        )

        return new_session

    # ── Stats ────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        await self.ensure_tables()
        total = await db_pool.fetchrow("SELECT COUNT(*) as cnt FROM trajectory_sessions")
        running = await db_pool.fetchrow(
            "SELECT COUNT(*) as cnt FROM trajectory_sessions WHERE status = 'running'"
        )
        events = await db_pool.fetchrow("SELECT COUNT(*) as cnt FROM trajectory_events")
        tokens = await db_pool.fetchrow(
            "SELECT COALESCE(SUM(total_tokens), 0) as total FROM trajectory_sessions"
        )
        return {
            "total_sessions": total["cnt"] if total else 0,
            "running_sessions": running["cnt"] if running else 0,
            "total_events": events["cnt"] if events else 0,
            "total_tokens": tokens["total"] if tokens else 0,
        }

    # ── Analytics ──────────────────────────────────────────────

    async def get_tool_analytics(self, limit: int = 50) -> dict:
        """Get tool usage frequency and success rate analytics."""
        await self.ensure_tables()

        # Tool usage frequency
        tool_rows = await db_pool.fetch(
            """
            SELECT
                COALESCE(json_extract(metadata_json, '$.tool_name'), 'unknown') as tool_name,
                COUNT(*) as usage_count,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count,
                AVG(duration_ms) as avg_duration_ms,
                SUM(token_usage) as total_tokens
            FROM trajectory_events
            WHERE event_type IN ('tool_call', 'tool_result')
            GROUP BY tool_name
            ORDER BY usage_count DESC
            LIMIT ?
        """,
            limit,
        )

        tools = []
        for row in tool_rows:
            usage = row["usage_count"] or 0
            success = row["success_count"] or 0
            tools.append(
                {
                    "tool_name": row["tool_name"],
                    "usage_count": usage,
                    "success_count": success,
                    "error_count": row["error_count"] or 0,
                    "success_rate": round(success / usage * 100, 1) if usage > 0 else 0,
                    "avg_duration_ms": round(row["avg_duration_ms"] or 0, 1),
                    "total_tokens": row["total_tokens"] or 0,
                }
            )

        return {"tools": tools, "total_tools": len(tools)}

    async def get_agent_analytics(self, limit: int = 50) -> dict:
        """Get per-agent performance analytics."""
        await self.ensure_tables()

        agent_rows = await db_pool.fetch(
            """
            SELECT
                agent_id,
                COUNT(*) as event_count,
                SUM(token_usage) as total_tokens,
                SUM(duration_ms) as total_duration_ms,
                AVG(duration_ms) as avg_duration_ms,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count
            FROM trajectory_events
            WHERE agent_id != ''
            GROUP BY agent_id
            ORDER BY event_count DESC
            LIMIT ?
        """,
            limit,
        )

        agents = []
        for row in agent_rows:
            events = row["event_count"] or 0
            success = row["success_count"] or 0
            agents.append(
                {
                    "agent_id": row["agent_id"],
                    "event_count": events,
                    "total_tokens": row["total_tokens"] or 0,
                    "total_duration_ms": round(row["total_duration_ms"] or 0, 1),
                    "avg_duration_ms": round(row["avg_duration_ms"] or 0, 1),
                    "success_count": success,
                    "error_count": row["error_count"] or 0,
                    "success_rate": round(success / events * 100, 1) if events > 0 else 0,
                }
            )

        return {"agents": agents, "total_agents": len(agents)}

    async def get_event_type_analytics(self) -> dict:
        """Get event type distribution analytics."""
        await self.ensure_tables()

        type_rows = await db_pool.fetch("""
            SELECT
                event_type,
                COUNT(*) as count,
                SUM(token_usage) as total_tokens,
                AVG(duration_ms) as avg_duration_ms
            FROM trajectory_events
            GROUP BY event_type
            ORDER BY count DESC
        """)

        types = []
        for row in type_rows:
            types.append(
                {
                    "event_type": row["event_type"],
                    "count": row["count"] or 0,
                    "total_tokens": row["total_tokens"] or 0,
                    "avg_duration_ms": round(row["avg_duration_ms"] or 0, 1),
                }
            )

        return {"event_types": types}

    async def get_token_analytics(self, days: int = 30) -> dict:
        """Get token usage over time (daily breakdown)."""
        await self.ensure_tables()

        daily_rows = await db_pool.fetch(
            """
            SELECT
                DATE(created_at) as day,
                SUM(token_usage) as tokens,
                COUNT(*) as events
            FROM trajectory_events
            WHERE created_at >= datetime('now', ?)
            GROUP BY DATE(created_at)
            ORDER BY day DESC
            LIMIT ?
        """,
            f"-{days} days",
            days,
        )

        daily = []
        for row in daily_rows:
            daily.append(
                {
                    "day": row["day"],
                    "tokens": row["tokens"] or 0,
                    "events": row["events"] or 0,
                }
            )

        # Summary
        total_tokens = sum(d["tokens"] for d in daily)
        total_events = sum(d["events"] for d in daily)
        avg_daily = round(total_tokens / len(daily), 0) if daily else 0

        return {
            "daily": daily,
            "summary": {
                "total_tokens": total_tokens,
                "total_events": total_events,
                "avg_daily_tokens": avg_daily,
                "days_tracked": len(daily),
            },
        }

    # ── Internal helpers ─────────────────────────────────────

    def _row_to_session(self, row) -> TrajectorySession:
        d = dict(row)
        tags = d.get("tags_json", "[]")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        return TrajectorySession(
            id=d["id"],
            agent_id=d.get("agent_id", ""),
            task_description=d.get("task_description", ""),
            status=d.get("status", "running"),
            forked_from=d.get("forked_from"),
            fork_point_event_id=d.get("fork_point_event_id"),
            total_events=d.get("total_events", 0),
            total_tokens=d.get("total_tokens", 0),
            total_duration_ms=d.get("total_duration_ms", 0),
            tags=tags,
            created_at=d.get("created_at", ""),
            ended_at=d.get("ended_at"),
        )

    def _row_to_event(self, row) -> TrajectoryEvent:
        d = dict(row)
        return TrajectoryEvent(
            id=d["id"],
            session_id=d.get("session_id", ""),
            parent_event_id=d.get("parent_event_id"),
            event_type=d.get("event_type", ""),
            agent_id=d.get("agent_id", ""),
            content=d.get("content", ""),
            metadata_json=d.get("metadata_json", "{}"),
            token_usage=d.get("token_usage", 0),
            duration_ms=d.get("duration_ms", 0),
            status=d.get("status", "ok"),
            created_at=d.get("created_at", ""),
            end_at=d.get("end_at"),
        )

    def _row_to_score(self, row) -> TrajectoryScore:
        d = dict(row)
        return TrajectoryScore(
            id=d["id"],
            session_id=d.get("session_id", ""),
            trace_id=d.get("trace_id"),
            name=d.get("name", ""),
            value=d.get("value", 0.0),
            string_value=d.get("string_value"),
            data_type=d.get("data_type", "numeric"),
            comment=d.get("comment", ""),
            source=d.get("source", "api"),
            created_at=d.get("created_at", ""),
        )


# Singleton
trajectory_store = TrajectoryStore()
