"""Persistent timeline event store using SQLite.

Stores all organ events persistently with rich querying capabilities.
Events are automatically captured from the event_bridge and stored here.
"""

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from src.nest.isolation import IsolationEngine, ResourceType
from src.nest.tenant import tenant_namespace

# F3b-③c-2①（2026-10-12）数据面租户缺省：器官内生事件（event_bridge emit/
# push_event 无身份上下文）与匿名调用归 'default' 租户——与生产数据分布
# （timeline.db 30860 行存量）迁移回填 'default' 一致。
DEFAULT_TENANT = "default"


class TimelineEventAccessDenied(Exception):
    """跨租户 timeline 事件访问被 IsolationEngine 拒绝（API 层映射 403，禁静默当 not_found）。

    verdict=check_access 原始回包（allowed/reason/namespaced_id），审计留痕在
    IsolationEngine 审计日志（data/nest.db），此处只携带原因供 API 如实报。
    """

    def __init__(self, reason: str, verdict: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.verdict = verdict or {}


@dataclass
class TimelineEvent:
    event_id: str
    organ: str
    emoji: str
    event_type: str
    summary: str
    detail: str  # JSON string
    timestamp: float
    collected_at: float
    tenant_id: str = DEFAULT_TENANT  # F3b-③c-2①：行归属租户（缺省 'default' 兼容旧调用）

    @property
    def detail_dict(self) -> dict:
        try:
            return json.loads(self.detail)
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "organ": self.organ,
            "emoji": self.emoji,
            "event_type": self.event_type,
            "summary": self.summary,
            "detail": self.detail_dict,
            "timestamp": self.timestamp,
            "collected_at": self.collected_at,
            "tenant_id": self.tenant_id,
            "time_ago": _time_ago(self.timestamp),
        }


def _time_ago(ts: float) -> str:
    """Human-readable time ago string."""
    diff = time.time() - ts
    if diff < 60:
        return f"{int(diff)}s ago"
    elif diff < 3600:
        return f"{int(diff / 60)}m ago"
    elif diff < 86400:
        return f"{int(diff / 3600)}h ago"
    else:
        return f"{int(diff / 86400)}d ago"


def default_db_path() -> str:
    """库路径：TIMELINE_DB 环境变量优先（测试隔离，NEST_DB/AGENT_REGISTRY_DB 同款
    先例），缺省 <repo>/data/timeline.db。"""
    env = os.environ.get("TIMELINE_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "timeline.db")


class TimelineStore:
    """Persistent event timeline with SQLite backend."""

    def __init__(self, db_path: str | None = None):
        db = db_path or default_db_path()
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        # F3b-③c-2①：数据面咨询 IsolationEngine（懒构造——首个 gate 调用才建
        # NestStore，收集期零生产库接触）。默认实例即真闸门，无"未接线"模式。
        self._isolation: IsolationEngine | None = None
        self._init_db()

    @property
    def isolation(self) -> IsolationEngine:
        """隔离引擎（check_access 真执行点；审计日志落 NestStore=data/nest.db）。"""
        if self._isolation is None:
            self._isolation = IsolationEngine()
        return self._isolation

    def gate(self, tenant_id: str, row_tenant: str, resource_id: str, action: str) -> None:
        """by-ID 访问闸门：IsolationEngine.check_access 真执行（F3b-③c-2①）。

        资源 ID 用 namespaced_id(row_tenant ns, event_id)——资源的 namespace
        前缀是行归属租户，比较基准是访问者派生租户；namespace 不匹配=跨租户冒读，
        引擎拒绝（allowed=False）→ TimelineEventAccessDenied 上抛，禁静默当 not_found。
        允许/拒绝都进引擎审计日志（audit_access=True），total_access_checks>0。
        """
        verdict = self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.TIMELINE_EVENT,
            resource_id=self.isolation.namespaced_id(tenant_namespace(row_tenant), resource_id),
            action=action,
        )
        if not verdict.get("allowed", False):
            raise TimelineEventAccessDenied(verdict.get("reason", "access denied"), verdict)

    def gate_bulk(self, tenant_id: str, action: str) -> None:
        """bulk 操作（list/clear/stats）审计留痕：执行隔离由 WHERE tenant_id=? 承担，
        引擎侧记 list/delete 审计（audit_access=\"Log all access attempts\"）。"""
        self.isolation.check_access(
            tenant_namespace=tenant_namespace(tenant_id),
            resource_type=ResourceType.TIMELINE_EVENT,
            resource_id=self.isolation.namespaced_id(tenant_namespace(tenant_id), "*"),
            action=action,
        )

    def _init_db(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                event_id    TEXT PRIMARY KEY,
                organ       TEXT NOT NULL,
                emoji       TEXT DEFAULT '',
                event_type  TEXT NOT NULL,
                summary     TEXT NOT NULL,
                detail      TEXT DEFAULT '{}',
                timestamp   REAL NOT NULL,
                collected_at REAL NOT NULL,
                tenant_id   TEXT DEFAULT 'default'
            );
            CREATE INDEX IF NOT EXISTS idx_events_organ ON events(organ);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_events_organ_ts ON events(organ, timestamp DESC);
        """)
        self._db.commit()
        # F3b-③c-2①：DDL+ALTER 双轨幂等——老库（迁移前形状无 tenant_id）补列+
        # 存量行回填 'default'（与生产数据分布一致），缺列才 ALTER（幂等）。
        # 租户索引必须在补列之后建（老库形态下先建索引会 no such column）。
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(events)").fetchall()}
        if "tenant_id" not in cols:
            self._db.execute("ALTER TABLE events ADD COLUMN tenant_id TEXT DEFAULT 'default'")
            self._db.execute("UPDATE events SET tenant_id = 'default' WHERE tenant_id IS NULL")
            self._db.commit()
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_tenant ON events(tenant_id, timestamp DESC)"
        )
        self._db.commit()

    def record(self, event: dict) -> bool:
        """Record an event to the timeline. Returns True if inserted (deduped by event_id)."""
        event_id = event.get("id", "")
        if not event_id:
            return False

        # F3b-③c-2①：行归属租户=事件自带键，缺失/空白→'default' 兜底
        # （键派生故障绝不吃掉落库，③b-2 同款旁路纪律）。
        tenant_val = str(event.get("tenant_id") or "").strip() or DEFAULT_TENANT

        try:
            self._db.execute(
                """INSERT OR IGNORE INTO events
                   (event_id, organ, emoji, event_type, summary, detail, timestamp, collected_at, tenant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    event.get("organ", "unknown"),
                    event.get("emoji", ""),
                    event.get("type", "unknown"),
                    event.get("summary", ""),
                    json.dumps(event.get("detail", {}), ensure_ascii=False),
                    event.get("timestamp", event.get("collected_at", time.time())),
                    event.get("collected_at", time.time()),
                    tenant_val,
                ),
            )
            self._db.commit()
            return True
        except Exception:
            return False

    def query(
        self,
        organ: str | None = None,
        event_type: str | None = None,
        search: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
        offset: int = 0,
        tenant_id: str | None = None,
    ) -> list[TimelineEvent]:
        """Query events with filters.

        F3b-③c-2①：tenant_id=None=系统内部扫描（全租户视图，不闸不过滤）；
        显式租户=bulk 恒 WHERE tenant_id=?（执行隔离真源）+引擎审计留痕。
        """
        clauses = ["1=1"]
        params: list = []

        if tenant_id is not None:
            self.gate_bulk(tenant_id, "list")
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if organ:
            clauses.append("organ = ?")
            params.append(organ)
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if search:
            clauses.append("(summary LIKE ? OR detail LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until:
            clauses.append("timestamp <= ?")
            params.append(until)

        where = " AND ".join(clauses)
        query = f"SELECT * FROM events WHERE {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._db.execute(query, params).fetchall()
        return [
            TimelineEvent(
                event_id=r["event_id"],
                organ=r["organ"],
                emoji=r["emoji"],
                event_type=r["event_type"],
                summary=r["summary"],
                detail=r["detail"],
                timestamp=r["timestamp"],
                collected_at=r["collected_at"],
                tenant_id=r["tenant_id"] if "tenant_id" in r.keys() else DEFAULT_TENANT,
            )
            for r in rows
        ]

    def get_event(self, event_id: str, tenant_id: str | None = None) -> TimelineEvent | None:
        """Get a single event by ID.

        F3b-③c-2①：tenant_id=None=系统内部读（不过滤不闸）；显式租户=by-ID 闸门
        真执行（跨租户 TimelineEventAccessDenied，禁静默 None）；缺行 None 契约不变。
        """
        row = self._db.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        if not row:
            return None
        row_tenant = row["tenant_id"] if "tenant_id" in row.keys() else DEFAULT_TENANT
        if tenant_id is not None:
            self.gate(tenant_id, row_tenant or DEFAULT_TENANT, event_id, "read")
        return TimelineEvent(
            event_id=row["event_id"],
            organ=row["organ"],
            emoji=row["emoji"],
            event_type=row["event_type"],
            summary=row["summary"],
            detail=row["detail"],
            timestamp=row["timestamp"],
            collected_at=row["collected_at"],
            tenant_id=row_tenant or DEFAULT_TENANT,
        )

    def delete_event(self, event_id: str, tenant_id: str | None = None) -> bool:
        """Delete a single event.

        F3b-③c-2①：显式租户=闸门前置（跨租户 TimelineEventAccessDenied 禁删、
        行存活）；tenant_id=None=系统内部清理通道。缺行 False（404 契约不变）。
        """
        row = self._db.execute(
            "SELECT tenant_id FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if not row:
            return False
        if tenant_id is not None:
            row_tenant = row["tenant_id"] if "tenant_id" in row.keys() else DEFAULT_TENANT
            self.gate(tenant_id, row_tenant or DEFAULT_TENANT, event_id, "delete")
        cursor = self._db.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
        self._db.commit()
        return cursor.rowcount > 0

    def clear(self, older_than_days: int | None = None, tenant_id: str | None = None) -> int:
        """Clear events. If older_than_days is set, only clear old events.

        显式租户=恒按行租户键清（防"只给租户"变相清全史，③a purge
        同款语义）+bulk 审计留痕；tenant_id=None=系统内部全清通道。
        """
        if tenant_id is not None:
            self.gate_bulk(tenant_id, "delete")
        if older_than_days:
            cutoff = time.time() - (older_than_days * 86400)
            if tenant_id is not None:
                cursor = self._db.execute(
                    "DELETE FROM events WHERE tenant_id = ? AND timestamp < ?",
                    (tenant_id, cutoff),
                )
            else:
                cursor = self._db.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
        else:
            if tenant_id is not None:
                cursor = self._db.execute("DELETE FROM events WHERE tenant_id = ?", (tenant_id,))
            else:
                cursor = self._db.execute("DELETE FROM events")
        self._db.commit()
        return cursor.rowcount

    def stats(self, tenant_id: str | None = None) -> dict:
        """Get timeline statistics.

        F3b-③c-2①：显式租户=全部统计恒 WHERE tenant_id=?（禁他租户行进计数，
        "只给租户"stats 不得泄露全局分布）+bulk 审计；None=系统全局（health 用）。
        """
        if tenant_id is not None:
            self.gate_bulk(tenant_id, "stats")
        scope = " WHERE tenant_id = ?" if tenant_id is not None else ""
        scope_params: list = [tenant_id] if tenant_id is not None else []

        total = self._db.execute(f"SELECT COUNT(*) FROM events{scope}", scope_params).fetchone()[0]

        # Events per organ
        organ_counts = {}
        for row in self._db.execute(
            f"SELECT organ, COUNT(*) as cnt FROM events{scope} GROUP BY organ ORDER BY cnt DESC",
            scope_params,
        ).fetchall():
            organ_counts[row["organ"]] = row["cnt"]

        # Events per type
        type_counts = {}
        for row in self._db.execute(
            f"SELECT event_type, COUNT(*) as cnt FROM events{scope} GROUP BY event_type ORDER BY cnt DESC LIMIT 20",
            scope_params,
        ).fetchall():
            type_counts[row["event_type"]] = row["cnt"]

        # Time range
        time_range = self._db.execute(
            f"SELECT MIN(timestamp) as earliest, MAX(timestamp) as latest FROM events{scope}",
            scope_params,
        ).fetchone()

        # Recent activity (last 24h)
        day_ago = time.time() - 86400
        recent_where = (
            " WHERE tenant_id = ? AND timestamp > ?"
            if tenant_id is not None
            else " WHERE timestamp > ?"
        )
        recent_params = [tenant_id, day_ago] if tenant_id is not None else [day_ago]
        recent = self._db.execute(
            f"SELECT COUNT(*) FROM events{recent_where}", recent_params
        ).fetchone()[0]

        # Hourly distribution (last 24h)
        hourly = []
        for i in range(24):
            hour_start = time.time() - (i + 1) * 3600
            hour_end = time.time() - i * 3600
            hour_where = (
                " WHERE tenant_id = ? AND timestamp > ? AND timestamp <= ?"
                if tenant_id is not None
                else " WHERE timestamp > ? AND timestamp <= ?"
            )
            hour_params = (
                [tenant_id, hour_start, hour_end]
                if tenant_id is not None
                else [hour_start, hour_end]
            )
            count = self._db.execute(
                f"SELECT COUNT(*) FROM events{hour_where}", hour_params
            ).fetchone()[0]
            hourly.append({"hours_ago": i + 1, "count": count})

        return {
            "total_events": total,
            "recent_24h": recent,
            "by_organ": organ_counts,
            "by_type": type_counts,
            "time_range": {
                "earliest": time_range["earliest"] if time_range else None,
                "latest": time_range["latest"] if time_range else None,
            },
            "hourly_distribution": hourly,
        }

    def organs(self, tenant_id: str | None = None) -> list[dict]:
        """List all organs that have events, with counts.

        F3b-③c-2①：显式租户=恒 WHERE tenant_id=?；None=系统视图。
        """
        if tenant_id is not None:
            self.gate_bulk(tenant_id, "list")
        scope = " WHERE tenant_id = ?" if tenant_id is not None else ""
        scope_params: list = [tenant_id] if tenant_id is not None else []
        rows = self._db.execute(
            f"""SELECT organ, emoji, COUNT(*) as count,
                      MAX(timestamp) as last_event
               FROM events{scope} GROUP BY organ ORDER BY last_event DESC""",
            scope_params,
        ).fetchall()
        return [
            {
                "organ": r["organ"],
                "emoji": r["emoji"],
                "count": r["count"],
                "last_event": r["last_event"],
                "last_event_ago": _time_ago(r["last_event"]),
            }
            for r in rows
        ]

    def event_types(self, tenant_id: str | None = None) -> list[dict]:
        """List all event types with counts.

        F3b-③c-2①：显式租户=恒 WHERE tenant_id=?；None=系统视图。
        """
        if tenant_id is not None:
            self.gate_bulk(tenant_id, "list")
        scope = " WHERE tenant_id = ?" if tenant_id is not None else ""
        scope_params: list = [tenant_id] if tenant_id is not None else []
        rows = self._db.execute(
            f"""SELECT event_type, COUNT(*) as count,
                      MAX(timestamp) as last_seen
               FROM events{scope} GROUP BY event_type ORDER BY count DESC""",
            scope_params,
        ).fetchall()
        return [
            {
                "event_type": r["event_type"],
                "count": r["count"],
                "last_seen": r["last_seen"],
            }
            for r in rows
        ]
