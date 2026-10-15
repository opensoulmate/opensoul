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
    # 行租户键（DEC-⑦-⑥ trajectory 子搜索存储层加轴）：自带键、缺失/空白→
    # 'default' 兜底（event_row_tenant 单一判法）；行归属=父 session 行继承
    # （add_event 单写点，③b-2 写点=父行继承同款），存量行随迁移回填 'default'。
    tenant_id: str = ""

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
    # 行租户键（DEC-⑦-⑥）：自带键、缺失/空白→'default' 兜底（session_row_tenant
    # 单一判法）；写点=create_session(tenant_id=) bind_identity 派生，存量行随
    # 迁移回填 'default'（与 F3 实测分布一致）。
    tenant_id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


DEFAULT_TENANT = "default"


class SessionTenantDenied(Exception):
    """跨租户写拒（行归属不可被跨租户改写）：API层映射为HTTP 403。

    DEC-⑦-③ TemplateTenantDenied / ④ FileTenantDenied / ⑤ AgentTenantDenied
    同款（劫持闸语义，F3b-③c-2②d 纪律）。
    """


def session_row_tenant(session: TrajectorySession) -> str:
    """session 行租户键单一判法（DEC-⑦-⑥）：自带键、缺失/空白→'default' 兜底。

    写读同源：create_session 落行、劫持闸/归属闸、list/count 过滤、API 加性
    tenant_id 键共用本函数，禁消费点另发明判法（F4b-㉗ 纪律）；DEC-⑦-①
    _event_row_tenant / ② message_row_tenant / ③ template_row_tenant / ④
    file_row_tenant / ⑤ agent_row_tenant 同款语义（系统/缺省写方→'default'，
    键派生故障绝不吃掉落库）。
    """
    return ((session.tenant_id or "").strip()) or DEFAULT_TENANT


def event_row_tenant(event: TrajectoryEvent) -> str:
    """事件行租户键单一判法（DEC-⑦-⑥）：自带键、缺失/空白→'default' 兜底。

    事件行归属=父 session 行继承（add_event 单写点，③b-2「写点租户键=父行
    继承」同款）；本函数=落行归一与加性 tenant_id 键共用判法（写读同源）。
    """
    return ((event.tenant_id or "").strip()) or DEFAULT_TENANT


def score_row_tenant(score: "TrajectoryScore") -> str:
    """score 行租户键单一判法（DEC-⑦-⑨）：自带键、缺失/空白→'default' 兜底。

    评分行归属=父 session 行继承（add_score 单写点，③b-2「写点租户键=父行
    继承」/add_event 同款）；本函数=落行归一与加性 tenant_id 键共用判法
    （写读同源，F4b-㉗ 纪律）。
    """
    return ((score.tenant_id or "").strip()) or DEFAULT_TENANT


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
    # 行租户键（DEC-⑦-⑨ trajectory_scores 加轴）：自带键、缺失/空白→
    # 'default' 兜底（score_row_tenant 单一判法）；行归属=父 session 行继承
    # （add_score 单写点，③b-2/add_event 写点=父行继承同款），存量行随迁移
    # 回填 'default'。
    tenant_id: str = ""

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
                ended_at TEXT,
                tenant_id TEXT NOT NULL DEFAULT 'default'
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
                created_at TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'default'
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
        # Migration: 行租户键（DEC-⑦-⑥，C1 幂等双轨 ⑦-③④⑤ 先例）：CREATE TABLE
        # IF NOT EXISTS 不补列，存量库按 try/ALTER 补 tenant_id 列（常量默认
        # ='default' 回填存量行，与 F3 实测分布一致）；租户索引必须在补列之后建
        # （③c-2① 坑延续）。
        for _ddl in (
            "ALTER TABLE trajectory_sessions ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'",
            "ALTER TABLE trajectory_events ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'",
        ):
            try:
                await db_pool.execute(_ddl)
            except Exception as exc:
                msg = str(exc).lower()
                if "duplicate column" not in msg and "already exists" not in msg:
                    raise
        await db_pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_traj_sessions_tenant
            ON trajectory_sessions(tenant_id)
        """)
        await db_pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_traj_events_tenant
            ON trajectory_events(tenant_id)
        """)
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
                created_at TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'default'
            )
        """)
        await db_pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_traj_scores_session
            ON trajectory_scores(session_id, name)
        """)
        # Migration: scores 行租户键（DEC-⑦-⑨，C1 幂等双轨）——必须在
        # CREATE TABLE 之后跑（新库 DDL 已带列→duplicate 安全吞；老库补列）
        # 且租户索引必须在补列之后建（③c-2① 坑延续）。
        try:
            await db_pool.execute(
                "ALTER TABLE trajectory_scores ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate column" not in msg and "already exists" not in msg:
                raise
        await db_pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_traj_scores_tenant
            ON trajectory_scores(tenant_id)
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
        tenant_id: str | None = None,
    ) -> TrajectorySession:
        """新建 session。

        tenant_id=写点派生租户键（DEC-⑦-⑥）；未提供/空白→'default' 兜底=系统
        写方语义（session_row_tenant 单一判法，键派生故障绝不吃掉落库）。
        劫持闸：显式 id 撞既有行且租户不同=SessionTenantDenied 行零改写
        （DEC-⑦-③④⑤ 同款，F3b-③c-2②d 纪律）。
        """
        await self.ensure_tables()
        s = TrajectorySession(
            id=id,
            agent_id=agent_id,
            task_description=task_description,
            forked_from=forked_from,
            fork_point_event_id=fork_point_event_id,
            tags=tags or [],
            tenant_id=tenant_id or "",
        )
        # 行归属=写点派生租户（写读同一判法：落行值即 session_row_tenant 归一值）
        row_tenant = session_row_tenant(s)
        s.tenant_id = row_tenant
        if id:
            existing = await db_pool.fetchrow("SELECT * FROM trajectory_sessions WHERE id = ?", id)
            if existing and session_row_tenant(self._row_to_session(existing)) != row_tenant:
                # 行归属不可被跨租户改写：跨租户按同 id 建/顶=拒绝且行零改写。
                raise SessionTenantDenied(f"session {id!r} belongs to another tenant")
        await db_pool.execute(
            """INSERT INTO trajectory_sessions
               (id, agent_id, task_description, status, forked_from, fork_point_event_id,
                total_events, total_tokens, total_duration_ms, tags_json, created_at, ended_at,
                tenant_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            s.tenant_id,
        )
        return s

    async def get_session(
        self, session_id: str, tenant_id: str | None = None
    ) -> TrajectorySession | None:
        """by-ID 读面归属闸（DEC-⑦-⑪）：tenant_id 显式给出=按行租户键判定
        可见（session_row_tenant 单一判法），跨租户回 None 与 not-found 同
        契约不差分泄露行存在性（⑦-⑩ get_file_meta 同款，③c-1 纪律）；None=
        系统视图字节不变（写闸/内部系统调用面沿用）。"""
        await self.ensure_tables()
        row = await db_pool.fetchrow("SELECT * FROM trajectory_sessions WHERE id = ?", session_id)
        if not row:
            return None
        session = self._row_to_session(row)
        if tenant_id is not None and session_row_tenant(session) != tenant_id:
            return None
        return session

    async def list_sessions(
        self,
        agent_id: str = "",
        status: str = "",
        limit: int = 50,
        offset: int = 0,
        tenant_id: str | None = None,
    ) -> list[TrajectorySession]:
        """列出 session（DEC-⑦-⑥ 租户过滤）。

        tenant_id 显式给出=恒按行租户键过滤（行键写点归一+迁移回填，SQL 等值
        =session_row_tenant 等值；过滤先于截断=limit 只计可见命中，跨租户行不占
        名额）；None=系统视图，既有调用面字节不变。
        """
        await self.ensure_tables()
        clauses = []
        params: list[Any] = []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend([limit, offset])
        rows = await db_pool.fetch(
            f"SELECT * FROM trajectory_sessions{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            *params,
        )
        return [self._row_to_session(r) for r in rows]

    async def end_session(
        self, session_id: str, status: str = "completed", tenant_id: str | None = None
    ):
        """收口session（04:20轮遗留#2销账：session.status不再恒running）。

        session.status = 活跃度生命周期：running（有在途/最近活动）↔ completed
        （已收口不再活跃）。首收口生效（ended_at只记第一次，重复收口no-op）；
        后续add_event自愈翻回running（见add_event）。span/event级status仍是
        结果语义（ok/error）——失败可见性不在此层。

        DEC-⑦-⑥ 劫持闸：tenant_id 显式给出且与行租户键不同=SessionTenantDenied
        行零改写（跨租户不得改写他人 session 状态，⑦-④ update 同款）；None=
        系统视图不闸（既有内部调用面字节不变）；tenant_id 不进 SET=粘性不洗键。
        """
        await self.ensure_tables()
        row = await db_pool.fetchrow("SELECT * FROM trajectory_sessions WHERE id = ?", session_id)
        if not row:
            return
        if tenant_id is not None and session_row_tenant(self._row_to_session(row)) != tenant_id:
            raise SessionTenantDenied(f"session {session_id!r} belongs to another tenant")
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
            # P1-15 user_idle 终止条件落 trace：闲置收口前给 session 留终止标记
            # 事件（metadata.termination=user_idle）——"为什么这个 session 结束了"
            # 在 trace 里可查。注意顺序：add_event 会把 session 自愈翻回 running，
            # 标记必须在 end_session 之前落，收口才是终态。
            try:
                await self.add_event(
                    TrajectoryEvent(
                        session_id=sid,
                        event_type=EventType.CUSTOM.value,
                        agent_id="system",
                        content="会话闲置收口（user_idle 终止条件）",
                        metadata_json=json.dumps(
                            {"termination": "user_idle", "kind": "termination"}, ensure_ascii=False
                        ),
                        status="ok",
                    )
                )
            except Exception:
                pass  # 标记失败不阻塞收口（收口是清扫的本职）
            await self.end_session(sid, status="completed")
            flipped += 1
        return flipped

    async def delete_session(self, session_id: str, tenant_id: str | None = None) -> bool:
        """删除 session 及其事件/score（DEC-⑦-⑥ 归属闸）。

        tenant_id 显式给出时跨租户删除=零副作用回 False（与 not-found 同一契约，
        不差分泄露他租户行存在性，⑦-④ delete 同款）；None=系统视图不闸（既有
        内部调用面字节不变）。返回 True=真删，False=缺行或跨租户拒。
        """
        await self.ensure_tables()
        row = await db_pool.fetchrow("SELECT * FROM trajectory_sessions WHERE id = ?", session_id)
        if not row:
            return False
        if tenant_id is not None and session_row_tenant(self._row_to_session(row)) != tenant_id:
            return False
        await db_pool.execute("DELETE FROM trajectory_scores WHERE session_id = ?", session_id)
        await db_pool.execute("DELETE FROM trajectory_events WHERE session_id = ?", session_id)
        await db_pool.execute("DELETE FROM trajectory_sessions WHERE id = ?", session_id)
        return True

    async def count_sessions(self, tenant_id: str | None = None) -> int:
        """session 总数（DEC-⑦-⑥：tenant_id 显式给出=恒按行租户键过滤——
        总数不得泄露全局分布，③c-2① stats 纪律；None=系统视图字节不变）。"""
        await self.ensure_tables()
        if tenant_id is not None:
            row = await db_pool.fetchrow(
                "SELECT COUNT(*) as cnt FROM trajectory_sessions WHERE tenant_id = ?",
                tenant_id,
            )
        else:
            row = await db_pool.fetchrow("SELECT COUNT(*) as cnt FROM trajectory_sessions")
        return row["cnt"] if row else 0

    # ── Event CRUD ───────────────────────────────────────────

    async def add_event(self, event: TrajectoryEvent) -> TrajectoryEvent:
        """写入事件（DEC-⑦-⑥：行归属=父 session 行继承）。

        写点租户键=父 session 行租户键（③b-2「写点租户键=父行继承」同款）：
        外部/系统写方经 HTTP 落事件同样随父行归属（会话拥有者可见自己会话的
        全部事件，③b-2 外部写方回帖语义）；父行缺失/键空白→'default' 兜底不
        掉落库（键派生故障绝不吃掉落库，③b-2 旁路纪律）。
        """
        await self.ensure_tables()
        if not event.session_id:
            raise ValueError("event.session_id is required")
        parent = await db_pool.fetchrow(
            "SELECT tenant_id FROM trajectory_sessions WHERE id = ?", event.session_id
        )
        event.tenant_id = (dict(parent).get("tenant_id") if parent else "") or ""
        event.tenant_id = event_row_tenant(event)
        await db_pool.execute(
            """INSERT INTO trajectory_events
               (id, session_id, parent_event_id, event_type, agent_id,
                content, metadata_json, token_usage, duration_ms, status, created_at, end_at,
                tenant_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            event.tenant_id,
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

    async def get_events(
        self, session_id: str, limit: int = 500, tenant_id: str | None = None
    ) -> list[TrajectoryEvent]:
        """by-ID 读面归属闸（DEC-⑦-⑪）：读取权威=父 session 行闸门（③b-2
        读取权威=父行同款；事件行键=add_event 父行继承派生键，两键生产恒等，
        会话拥有者可见自己会话的全部事件）。显式 tenant_id 下父行不可见=
        回 [] 与空事件同契约；None=系统视图字节不变。"""
        await self.ensure_tables()
        if (
            tenant_id is not None
            and await self.get_session(session_id, tenant_id=tenant_id) is None
        ):
            return []
        rows = await db_pool.fetch(
            """SELECT * FROM trajectory_events
               WHERE session_id = ?
               ORDER BY created_at ASC
               LIMIT ?""",
            session_id,
            limit,
        )
        return [self._row_to_event(r) for r in rows]

    async def get_event(
        self, event_id: str, tenant_id: str | None = None
    ) -> TrajectoryEvent | None:
        """by-ID 读面归属闸（DEC-⑦-⑪）：读取权威=父 session 行闸门（get_events
        同权威）；显式 tenant_id 下父行不可见=回 None 与 not-found 同契约；
        None=系统视图字节不变。"""
        await self.ensure_tables()
        row = await db_pool.fetchrow("SELECT * FROM trajectory_events WHERE id = ?", event_id)
        if not row:
            return None
        event = self._row_to_event(row)
        if (
            tenant_id is not None
            and await self.get_session(event.session_id, tenant_id=tenant_id) is None
        ):
            return None
        return event

    @staticmethod
    def _search_where(
        session_id: str = "",
        event_type: str = "",
        keyword: str = "",
        status: str = "",
        tenant_id: str | None = None,
    ) -> tuple[str, list[Any]]:
        """搜索过滤条件单一真源——search_events与search_events_by_session共用。

        两处各写WHERE会漂移出"分组计数与事件列表对不上"（指标不许说谎）。
        列全带e.限定：JOIN trajectory_sessions后status列两表都有，裸列名歧义。
        调用方须把轨迹事件表别名为e（两查询同款）。
        DEC-⑦-⑥：tenant_id 显式给出=恒按事件行租户键过滤（行键=父行继承落行
        归一+迁移回填，SQL 等值=event_row_tenant 等值；None=系统视图）。
        """
        clauses = []
        params: list[Any] = []
        if session_id:
            clauses.append("e.session_id = ?")
            params.append(session_id)
        if event_type:
            clauses.append("e.event_type = ?")
            params.append(event_type)
        if keyword:
            clauses.append("e.content LIKE ?")
            params.append(f"%{keyword}%")
        if status:
            clauses.append("e.status = ?")
            params.append(status)
        if tenant_id is not None:
            clauses.append("e.tenant_id = ?")
            params.append(tenant_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return where, params

    async def search_events(
        self,
        session_id: str = "",
        event_type: str = "",
        keyword: str = "",
        status: str = "",
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> list[TrajectoryEvent]:
        await self.ensure_tables()
        where, params = self._search_where(
            session_id, event_type, keyword, status, tenant_id=tenant_id
        )
        params.append(limit)
        rows = await db_pool.fetch(
            f"SELECT * FROM trajectory_events e{where} ORDER BY e.created_at DESC LIMIT ?",
            *params,
        )
        return [self._row_to_event(r) for r in rows]

    async def search_events_by_session(
        self,
        session_id: str = "",
        event_type: str = "",
        keyword: str = "",
        status: str = "",
        limit: int = 50,
        tenant_id: str | None = None,
    ) -> list[dict]:
        """跨会话命中分组——"哪个session有error的tool_call"观察面。

        调研来源：SUMMARY P0-4可观测性（"我都不知道他们在干嘛"）+树内搜索只搜
        当前session树的遗留（dev-reports "跨session搜索…会话列表级过滤"）。
        纪律：
        - match_count=SQL GROUP BY真值，不从截断的事件列表推算（"total推算说谎"同门）；
        - 每组样本=最近一条命中（窗口函数rn=1），snippet超500字符截断必须显式
          标记sample_truncated（AIHawk SHOWN/SENT：截断必须显式标记）；
        - 会话元数据LEFT JOIN——事件还在但会话行已删时task_description为None，
          不猜不冒充。
        """
        await self.ensure_tables()
        where, params = self._search_where(
            session_id, event_type, keyword, status, tenant_id=tenant_id
        )
        group_params = list(params)
        group_params.append(limit)
        rows = await db_pool.fetch(
            f"""SELECT e.session_id AS session_id,
                       COUNT(*) AS match_count,
                       MIN(e.created_at) AS first_match_at,
                       MAX(e.created_at) AS last_match_at,
                       s.task_description AS task_description,
                       s.agent_id AS agent_id,
                       s.status AS session_status
                FROM trajectory_events e
                LEFT JOIN trajectory_sessions s ON s.id = e.session_id
                {where}
                GROUP BY e.session_id
                ORDER BY MAX(e.created_at) DESC
                LIMIT ?""",
            *group_params,
        )
        if not rows:
            return []
        sample_where, sample_params = self._search_where(
            session_id, event_type, keyword, status, tenant_id=tenant_id
        )
        sample_rows = await db_pool.fetch(
            f"""SELECT id, session_id, event_type, status, content, created_at FROM (
                    SELECT e.*, ROW_NUMBER() OVER (
                        PARTITION BY e.session_id ORDER BY e.created_at DESC, e.id DESC
                    ) AS rn
                    FROM trajectory_events e{sample_where}
                ) WHERE rn = 1""",
            *sample_params,
        )
        samples = {dict(r)["session_id"]: dict(r) for r in sample_rows}
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            s = samples.get(d["session_id"])
            sample = None
            if s is not None:
                content = s.get("content") or ""
                sample = {
                    "event_id": s["id"],
                    "event_type": s["event_type"],
                    "status": s["status"],
                    "created_at": s["created_at"],
                    "snippet": content[:500],
                    "sample_truncated": len(content) > 500,
                }
            out.append(
                {
                    "session_id": d["session_id"],
                    "match_count": d["match_count"],
                    "first_match_at": d["first_match_at"],
                    "last_match_at": d["last_match_at"],
                    "task_description": d["task_description"],
                    "agent_id": d["agent_id"],
                    "session_status": d["session_status"],
                    "sample": sample,
                }
            )
        return out

    # ── Spans (langfuse Observation layer) ───────────────────

    async def end_event(
        self,
        event_id: str,
        status: str = "ok",
        token_usage: int | None = None,
        termination: str = "",
    ) -> TrajectoryEvent | None:
        """Close a span-style event: stamp ``end_at`` and derive duration.

        langfuse Observation semantics — an observation without an end is
        still "open". Duration is computed here from created_at→now rather
        than trusted from the caller, so every span's duration comes from
        one clock. ``token_usage`` folds back into the session total.

        ``termination``（P1-15 五终止条件词汇，agent/termination.py）合入事件
        metadata（metadata.termination）——回合被哪个终止条件收口落 trace；
        空串=正常完成，metadata 不动（向后兼容零变化）。
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
        new_meta_json = ev.metadata_json
        if termination:
            meta = ev.metadata
            meta["termination"] = str(termination)[:40]
            new_meta_json = json.dumps(meta, ensure_ascii=False)
        await db_pool.execute(
            """UPDATE trajectory_events
               SET end_at = ?, duration_ms = ?, status = ?, token_usage = ?, metadata_json = ?
               WHERE id = ?""",
            end_at,
            duration,
            status,
            new_tokens,
            new_meta_json,
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
        ev.metadata_json = new_meta_json
        return ev

    async def get_trace_tree(
        self, session_id: str, limit: int = 500, tenant_id: str | None = None
    ) -> dict:
        """Build the nested observation tree for a session.

        DEC-⑦-⑪ 归属闸：tenant_id 透传 get_events 同一父行权威闸（跨租户=
        空树与无事件同契约，API 层另有 get_session 404 前置同契约收口）。

        langfuse Trace→Observation model: ``parent_event_id`` links become
        parent/child edges, each node carries subtree rollups (events /
        tokens / duration — the Warp subtree-rollup pattern). Corrupt
        lineage must not hang the reader: dangling parents are promoted to
        roots and cycles are cut by treating the cycle member as a root.
        """
        await self.ensure_tables()
        events = await self.get_events(session_id, limit=limit, tenant_id=tenant_id)
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
        """Persist a quality score against a session (optionally one event).

        DEC-⑦-⑨：评分行归属=父 session 行继承（add_event「写点租户键=父行
        继承」同款，③b-2 外部写方回帖语义——eval_loop/llm_judge/human 等外部
        评分方经 HTTP/store 落分同样随父行归属，会话拥有者可见自己会话的全部
        评分）；父行缺失/键空白→'default' 兜底不掉落库（键派生故障绝不吃掉落
        库，③b-2 旁路纪律）。
        """
        await self.ensure_tables()
        if not score.session_id:
            raise ValueError("score.session_id is required")
        if not score.name:
            raise ValueError("score.name is required")
        if score.data_type not in {t.value for t in ScoreDataType}:
            raise ValueError(f"score.data_type must be one of {[t.value for t in ScoreDataType]}")
        if score.source not in SCORE_SOURCES:
            raise ValueError(f"score.source must be one of {list(SCORE_SOURCES)}")
        parent = await db_pool.fetchrow(
            "SELECT tenant_id FROM trajectory_sessions WHERE id = ?", score.session_id
        )
        score.tenant_id = (dict(parent).get("tenant_id") if parent else "") or ""
        score.tenant_id = score_row_tenant(score)
        await db_pool.execute(
            """INSERT INTO trajectory_scores
               (id, session_id, trace_id, name, value, string_value,
                data_type, comment, source, created_at, tenant_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
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
            score.tenant_id,
        )
        return score

    async def get_scores(
        self,
        session_id: str = "",
        name: str = "",
        source: str = "",
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[TrajectoryScore]:
        """读评分（DEC-⑦-⑨ 租户过滤）。

        tenant_id 显式给出=恒按行租户键过滤（行键=父行继承落行归一+迁移回填，
        SQL 等值=score_row_tenant 等值；过滤先于截断=limit 只计可见命中）；
        None=系统视图，既有调用面字节不变。
        """
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
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = await db_pool.fetch(
            f"SELECT * FROM trajectory_scores{where} ORDER BY created_at DESC LIMIT ?",
            *params,
        )
        return [self._row_to_score(r) for r in rows]

    async def get_score_summary(self, session_id: str = "", tenant_id: str | None = None) -> dict:
        """Aggregate scores per name — the "is it getting better" query.

        DEC-⑦-⑨：tenant_id 显式给出=恒按行租户键过滤（聚合不得泄露全局分布，
        ③c-2① stats 纪律）；None=系统视图字节不变。
        """
        await self.ensure_tables()
        clauses = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if tenant_id is not None:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
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
        self,
        source_session_id: str,
        fork_point_event_id: str,
        new_agent_id: str = "",
        tenant_id: str | None = None,
    ) -> TrajectorySession:
        """Fork a session at a specific event — copies events up to the fork point
        and creates a new session for branching execution.

        DEC-⑦-⑥：新 session 行归属=调用方派生租户（克隆归克隆者，⑦-③ clone
        同款）；复制事件经 add_event 随新 session 行继承落键（父行继承单一写点）。
        """
        # DEC-⑦-⑪ 源读归属闸：跨租户=源不可见走同一 ValueError 与 not-found
        # 同契约（克隆零副作用），闸在克隆/复制之前=禁拿他人内容跑克隆
        # （⑦-⑩ promote/auto-process 同型）；None=系统视图字节不变。
        source = await self.get_session(source_session_id, tenant_id=tenant_id)
        if not source:
            raise ValueError(f"Source session {source_session_id} not found")

        # Create new session marked as fork
        new_session = await self.create_session(
            agent_id=new_agent_id or source.agent_id,
            task_description=f"[Fork] {source.task_description}",
            forked_from=source_session_id,
            fork_point_event_id=fork_point_event_id,
            tags=source.tags + ["fork"],
            tenant_id=tenant_id,
        )

        # Copy events up to and including the fork point
        events = await self.get_events(source_session_id, tenant_id=tenant_id)
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
        s = TrajectorySession(
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
            tenant_id=d.get("tenant_id") or "",
        )
        # 加性 tenant_id 键归一（session_row_tenant 单一判法，⑤ _row_to_dict 同款）
        s.tenant_id = session_row_tenant(s)
        return s

    def _row_to_event(self, row) -> TrajectoryEvent:
        d = dict(row)
        ev = TrajectoryEvent(
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
            tenant_id=d.get("tenant_id") or "",
        )
        # 加性 tenant_id 键归一（event_row_tenant 单一判法，写读同源）
        ev.tenant_id = event_row_tenant(ev)
        return ev

    def _row_to_score(self, row) -> TrajectoryScore:
        d = dict(row)
        s = TrajectoryScore(
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
            tenant_id=d.get("tenant_id") or "",
        )
        # 加性 tenant_id 键归一（score_row_tenant 单一判法，写读同源）
        s.tenant_id = score_row_tenant(s)
        return s


# Singleton
trajectory_store = TrajectoryStore()
