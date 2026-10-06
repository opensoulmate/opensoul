"""OpenCapture API — Browser extension capture endpoints.

Receives page/selection captures from the OpenMate browser extension
and stores them in the knowledge base via OpenSoul.
"""

import hashlib
import json
import logging
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.database.postgres import db_pool

router = APIRouter()
logger = logging.getLogger(__name__)


# ─── Pydantic models ──────────────────────────────────────────────


class PageCapture(BaseModel):
    title: str
    url: str
    description: str = ""
    keywords: list[str] = []
    content: str = ""
    html: str = ""


class SelectionCapture(BaseModel):
    text: str
    url: str = ""
    title: str = ""
    context: str = ""


class CaptureResponse(BaseModel):
    id: str
    type: str
    title: str
    url: str
    created_at: float
    status: str


# ─── Storage helpers ──────────────────────────────────────────────

# 新schema权威DDL。旧版漂移表(id TEXT PRIMARY KEY/type/metadata)与本DDL
# 不兼容（id是TEXT主键无自增、缺capture_type等7列），ALTER补列救不了→
# 漂移时整表重建+旧行按列映射迁移。
_CAPTURES_DDL = """CREATE TABLE IF NOT EXISTS captures (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            capture_type  TEXT    NOT NULL,
            title         TEXT    NOT NULL DEFAULT '',
            url           TEXT    NOT NULL DEFAULT '',
            description   TEXT    NOT NULL DEFAULT '',
            keywords      TEXT    NOT NULL DEFAULT '[]',
            content       TEXT    NOT NULL DEFAULT '',
            content_hash  TEXT    NOT NULL DEFAULT '',
            status        TEXT    NOT NULL DEFAULT 'captured',
            created_at    REAL    NOT NULL,
            user_id       TEXT    NOT NULL DEFAULT 'browser-extension'
        )"""

# 漂移重建时的中间备份表名（崩溃残留由 _ensure_table 续跑清场）
_LEGACY_BACKUP_TABLE = "captures_legacy_backup"

# 新系缺列逐列补齐（与 src/database/postgres.py _SCHEMA_FIXES 同范式）
_COLUMN_FIXES = [
    ("title", "ALTER TABLE captures ADD COLUMN title TEXT NOT NULL DEFAULT ''"),
    ("url", "ALTER TABLE captures ADD COLUMN url TEXT NOT NULL DEFAULT ''"),
    ("description", "ALTER TABLE captures ADD COLUMN description TEXT NOT NULL DEFAULT ''"),
    ("keywords", "ALTER TABLE captures ADD COLUMN keywords TEXT NOT NULL DEFAULT '[]'"),
    ("content", "ALTER TABLE captures ADD COLUMN content TEXT NOT NULL DEFAULT ''"),
    ("content_hash", "ALTER TABLE captures ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"),
    ("status", "ALTER TABLE captures ADD COLUMN status TEXT NOT NULL DEFAULT 'captured'"),
    ("created_at", "ALTER TABLE captures ADD COLUMN created_at REAL NOT NULL DEFAULT 0"),
    (
        "user_id",
        "ALTER TABLE captures ADD COLUMN user_id TEXT NOT NULL DEFAULT 'browser-extension'",
    ),
]


async def _table_columns(table: str) -> list[str]:
    """PRAGMA table_info探测列集（_ensure_schema同款探测方式，幂等）。"""
    rows = await db_pool.fetch(f"PRAGMA table_info({table})")
    return [r["name"] for r in rows]


def _migration_select_sql(old: set[str]) -> str:
    """旧漂移表→新schema的行映射SQL（只映射旧表实际存在的列，不丢数据）。

    type→capture_type（空值标'legacy'）、metadata→description原文保留、
    TEXT时间→epoch秒（新列REAL，typeof守卫：已是数值原样保留）。
    id不迁移（新表自增重排）。
    """

    def col(name: str, fallback: str = "''") -> str:
        return name if name in old else fallback

    exprs = [
        "COALESCE(NULLIF({}, ''), 'legacy')".format(col("type")),
        col("title"),
        col("url"),
        col("description", col("metadata")),
        "'[]'",
        col("content"),
        col("content_hash"),
        "'migrated'",
        (
            "CASE WHEN typeof(created_at) IN ('integer','real') THEN created_at "
            "ELSE COALESCE(strftime('%s', created_at), strftime('%s','now')) END"
            if "created_at" in old
            else "strftime('%s','now')"
        ),
        col("user_id", "'browser-extension'"),
    ]
    return ", ".join(exprs)


async def _migrate_legacy_captures(old: list[str]) -> None:
    """旧漂移表整表重建：备份→建新表→行映射回收→清备份（数据不丢）。"""
    n_rows = 0
    await db_pool.execute(f"DROP TABLE IF EXISTS {_LEGACY_BACKUP_TABLE}")
    await db_pool.execute(f"ALTER TABLE captures RENAME TO {_LEGACY_BACKUP_TABLE}")
    await db_pool.execute(_CAPTURES_DDL)
    sel = _migration_select_sql(set(old))
    result = await db_pool.execute(
        "INSERT INTO captures (capture_type, title, url, description, keywords, content, "
        f"content_hash, status, created_at, user_id) SELECT {sel} FROM {_LEGACY_BACKUP_TABLE}"
    )
    try:
        n_rows = int(str(result).rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        n_rows = -1
    await db_pool.execute(f"DROP TABLE {_LEGACY_BACKUP_TABLE}")
    # 失败/状态必须可见：迁移是不可逆结构操作，journal留痕（INFO已可达journal）
    logger.info(
        "captures schema migrated from legacy drift (old columns=%s, rows preserved=%s)",
        old,
        n_rows,
    )


async def _ensure_table():
    """幂等schema guard + 漂移自愈。

    2026-10-06实证：生产data/opensoul.db的captures是旧版漂移schema
    (id TEXT PRIMARY KEY/type/metadata/created_at TEXT)，CREATE TABLE IF
    NOT EXISTS对已存在的旧表是no-op→每个引用capture_type的查询都报
    "no such column: capture_type"，而健康检查把异常吞成status:"ok"
    +note:"table not yet initialized"——浏览器扩展采集写入/列表过滤/统计
    整体不可用且对外谎报健康（"写了≠接线了"的健康面形态）。

    模式沿用src/database/postgres.py SQLitePool._ensure_schema的
    "PRAGMA table_info探测→缺失才补，幂等可重复执行"；旧表id是TEXT主键
    与新schema自增不兼容，ALTER补列救不了→整表重建（_migrate_legacy_captures，
    旧行按列映射迁移不丢数据）；新系缺列走_COLUMN_FIXES逐列ALTER。
    """
    await db_pool.execute(_CAPTURES_DDL)
    cols = await _table_columns("captures")
    if cols and "capture_type" not in cols:
        await _migrate_legacy_captures(cols)
    elif cols:
        for column, ddl in _COLUMN_FIXES:
            if column not in cols:
                await db_pool.execute(ddl)
    # 崩溃续跑：重建中途断电/重启残留备份表→把行补回收再清场
    # （仅当新表为空才补，避免二次复制重复行）
    backup_cols = await _table_columns(_LEGACY_BACKUP_TABLE)
    if backup_cols:
        n = await db_pool.fetchval("SELECT COUNT(*) FROM captures") or 0
        if n == 0:
            sel = _migration_select_sql(set(backup_cols))
            await db_pool.execute(
                "INSERT INTO captures (capture_type, title, url, description, keywords, content, "
                f"content_hash, status, created_at, user_id) SELECT {sel} FROM {_LEGACY_BACKUP_TABLE}"
            )
        await db_pool.execute(f"DROP TABLE IF EXISTS {_LEGACY_BACKUP_TABLE}")
        logger.warning("captures migration leftover backup recovered (old columns=%s)", backup_cols)


def _row_to_dict(row) -> dict:
    d = dict(row)
    if "keywords" in d:
        import json

        try:
            d["keywords"] = json.loads(d["keywords"])
        except (json.JSONDecodeError, TypeError):
            d["keywords"] = []
    return d


# ─── Endpoints ─────────────────────────────────────────────────────


@router.get("/health")
async def capture_health():
    """OpenCapture health check."""
    try:
        await _ensure_table()
        rows = await db_pool.fetch("SELECT COUNT(*) as cnt FROM captures")
        total = (rows[0]["cnt"] if rows else 0) if rows else 0
        pages = await db_pool.fetch(
            "SELECT COUNT(*) as cnt FROM captures WHERE capture_type = 'page'"
        )
        selections = await db_pool.fetch(
            "SELECT COUNT(*) as cnt FROM captures WHERE capture_type = 'selection'"
        )
        return {
            "status": "ok",
            "component": "OpenCapture",
            "total_captures": total,
            "page_captures": (pages[0]["cnt"] if pages else 0) if pages else 0,
            "selection_captures": (selections[0]["cnt"] if selections else 0) if selections else 0,
        }
    except Exception as e:
        # 失败必须可见（mem0 §1.1/AIHawk"显式标记"）：此前吞异常返回
        # status:"ok"+note:"table not yet initialized"——schema漂移期间
        # 对外谎报健康，dashboard /api/health/all 也把它当ok。
        # 检查失败=真实不健康→503，让health_all/前端如实显示error。
        logger.warning("Capture health check failed: %s", e)
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "component": "OpenCapture",
                "total_captures": 0,
                "page_captures": 0,
                "selection_captures": 0,
                "error": str(e),
            },
        )


@router.get("/stats")
async def capture_stats():
    """Get capture statistics."""
    try:
        # _ensure_table纳入try：schema守卫失败也走"失败可见"（503+error），
        # 与capture_health同契约（此前守卫在try外，异常直接炸成未处理500）
        await _ensure_table()
        total = await db_pool.fetchval("SELECT COUNT(*) FROM captures") or 0
        pages = (
            await db_pool.fetchval("SELECT COUNT(*) FROM captures WHERE capture_type = 'page'") or 0
        )
        selections = (
            await db_pool.fetchval("SELECT COUNT(*) FROM captures WHERE capture_type = 'selection'")
            or 0
        )
        recent = (
            await db_pool.fetchval(
                # 修复PG方言残留：EXTRACT(EPOCH FROM NOW() - INTERVAL ...)是
                # PostgreSQL语法，SQLite必炸→此前整个/stats被except吞成全0
                # （total/pages/selections算完也一起丢）。改参数绑定epoch截止
                # 时间，方言无关（适配器$1→?照常转换）。
                "SELECT COUNT(*) FROM captures WHERE created_at > $1",
                time.time() - 86400,
            )
            or 0
        )
        return {
            "status": "ok",
            "component": "OpenCapture",
            "total_captures": total,
            "page_captures": pages,
            "selection_captures": selections,
            "recent_24h": recent,
        }
    except Exception as e:
        # 失败必须可见：此前吞异常返回全0+status:"ok"（统计长期静默失真）
        logger.warning("Capture stats failed: %s", e)
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "component": "OpenCapture",
                "total_captures": 0,
                "page_captures": 0,
                "selection_captures": 0,
                "recent_24h": 0,
                "error": str(e),
            },
        )


@router.post("/page", response_model=CaptureResponse)
async def capture_page(req: PageCapture):
    """Capture a full page from the browser extension."""
    await _ensure_table()

    content = req.content or req.description or req.title
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    now = time.time()

    # Check for duplicate
    existing = await db_pool.fetchrow(
        "SELECT id FROM captures WHERE url = $1 AND content_hash = $2",
        req.url,
        content_hash,
    )
    if existing:
        logger.info(f"Duplicate page capture for {req.url}, returning existing")
        return CaptureResponse(
            id=str(existing["id"]),
            type="page",
            title=req.title,
            url=req.url,
            created_at=now,
            status="duplicate",
        )

    import json

    row = await db_pool.fetchrow(
        """INSERT INTO captures (capture_type, title, url, description, keywords, content, content_hash, created_at)
           VALUES ('page', $1, $2, $3, $4, $5, $6, $7) RETURNING *""",
        req.title,
        req.url,
        req.description,
        json.dumps(req.keywords),
        content,
        content_hash,
        now,
    )

    logger.info(f"Captured page: {req.title} ({req.url})")
    return CaptureResponse(
        id=str(row["id"]),
        type="page",
        title=req.title,
        url=req.url,
        created_at=now,
        status="captured",
    )


@router.post("/selection", response_model=CaptureResponse)
async def capture_selection(req: SelectionCapture):
    """Capture selected text from the browser extension."""
    await _ensure_table()

    content_hash = hashlib.sha256(req.text.encode()).hexdigest()[:16]
    now = time.time()

    # Check for duplicate
    existing = await db_pool.fetchrow(
        "SELECT id FROM captures WHERE url = $1 AND content_hash = $2",
        req.url,
        content_hash,
    )
    if existing:
        logger.info(f"Duplicate selection capture for {req.url}")
        return CaptureResponse(
            id=str(existing["id"]),
            type="selection",
            title=req.title or "Selected Text",
            url=req.url,
            created_at=now,
            status="duplicate",
        )

    row = await db_pool.fetchrow(
        """INSERT INTO captures (capture_type, title, url, content, content_hash, created_at)
           VALUES ('selection', $1, $2, $3, $4, $5) RETURNING *""",
        req.title or "Selected Text",
        req.url,
        req.text,
        content_hash,
        now,
    )

    logger.info(f"Captured selection: {len(req.text)} chars from {req.url}")
    return CaptureResponse(
        id=str(row["id"]),
        type="selection",
        title=req.title or "Selected Text",
        url=req.url,
        created_at=now,
        status="captured",
    )


@router.get("/list")
async def list_captures(
    limit: int = 50,
    offset: int = 0,
    capture_type: str = "",
):
    """List all captures with pagination."""
    await _ensure_table()

    if capture_type:
        rows = await db_pool.fetch(
            "SELECT * FROM captures WHERE capture_type = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            capture_type,
            limit,
            offset,
        )
    else:
        rows = await db_pool.fetch(
            "SELECT * FROM captures ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )

    count_rows = await db_pool.fetch("SELECT COUNT(*) as cnt FROM captures")
    total = count_rows[0]["cnt"] if count_rows else 0

    return {
        "captures": [_row_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{capture_id}")
async def get_capture(capture_id: int):
    """Get a single capture by ID."""
    await _ensure_table()
    row = await db_pool.fetchrow("SELECT * FROM captures WHERE id = $1", capture_id)
    if not row:
        raise HTTPException(status_code=404, detail="Capture not found")
    return _row_to_dict(row)


@router.delete("/{capture_id}")
async def delete_capture(capture_id: int):
    """Delete a capture."""
    await _ensure_table()
    result = await db_pool.execute("DELETE FROM captures WHERE id = $1", capture_id)
    if "DELETE 0" in result:
        raise HTTPException(status_code=404, detail="Capture not found")
    return {"deleted": True, "id": capture_id}


@router.post("/{capture_id}/promote")
async def promote_to_knowledge(capture_id: int, user_id: str = "default"):
    """Promote a capture to the knowledge base."""
    await _ensure_table()
    row = await db_pool.fetchrow("SELECT * FROM captures WHERE id = $1", capture_id)
    if not row:
        raise HTTPException(status_code=404, detail="Capture not found")

    capture = dict(row)

    # Insert into knowledge base
    try:
        import uuid as _uuid

        knowledge_id = str(_uuid.uuid4())
        tags = ["capture", capture["capture_type"], "browser"]
        await db_pool.execute(
            """INSERT INTO knowledge (id, user_id, title, content, source, content_type, metadata, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            knowledge_id,
            user_id,
            capture["title"] or f"Captured from {capture['url']}",
            capture["content"],
            f"capture://{capture_id}",
            "text/html",
            json.dumps(
                {"tags": tags, "url": capture["url"], "capture_type": capture["capture_type"]}
            ),
            time.time(),
            time.time(),
        )

        # fire-and-forget: meilisearch增量索引（失败不阻塞主流程）
        try:
            from src.services.meili_indexer import schedule_index

            schedule_index(knowledge_id)
        except Exception:
            pass
        # Add tags to knowledge_tags table
        for tag_name in tags:
            tag_id = str(_uuid.uuid4())
            await db_pool.execute(
                "INSERT INTO tags (id, name, user_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                tag_id,
                tag_name,
                user_id,
            )
            tag_row = await db_pool.fetchrow(
                "SELECT id FROM tags WHERE name = $1 AND user_id = $2", tag_name, user_id
            )
            if tag_row:
                await db_pool.execute(
                    "INSERT INTO knowledge_tags (knowledge_id, tag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    knowledge_id,
                    tag_row["id"],
                )
    except Exception as e:
        # Fallback: table might not exist or schema differs
        logger.warning(f"Could not promote to knowledge: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to promote: {e}")

    # Update capture status
    await db_pool.execute("UPDATE captures SET status = 'promoted' WHERE id = $1", capture_id)

    return {"promoted": True, "id": capture_id, "user_id": user_id}
