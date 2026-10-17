"""Sessions API — unified session management.

Provides session search, delete, and message retrieval at /api/sessions.
Reads from both Hermes state SQLite database and OpenSoul agent_sessions.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.user import get_current_user

router = APIRouter()


@router.get("/health")
async def sessions_api_health():
    """SessionsAPI health check."""
    return {"status": "ok", "component": "SessionsAPI"}


logger = logging.getLogger(__name__)

_DB_PATH = os.path.expanduser("~/.hermes/state.db")
_OPENSOUL_DB = os.environ.get(
    "OPENSOUL_DB",
    os.path.join(os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul"), "data", "opensoul.db"),
)


def _get_db():
    """Get a connection to the Hermes state database."""
    if not os.path.exists(_DB_PATH):
        return None
    db = sqlite3.connect(_DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def _get_agent_db():
    """Get a connection to the OpenSoul database for agent sessions."""
    if not os.path.exists(_OPENSOUL_DB):
        return None
    db = sqlite3.connect(_OPENSOUL_DB)
    db.row_factory = sqlite3.Row
    return db


def _ensure_session_tags_table(db: sqlite3.Connection):
    """Create session_tags table if it doesn't exist."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS session_tags (
            session_id TEXT NOT NULL,
            tag_name TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (session_id, tag_name)
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_session_tags_name ON session_tags(tag_name)")
    db.commit()


def _get_session_tags_map(db: sqlite3.Connection, session_ids: list[str]) -> dict[str, list[str]]:
    """Get tags for multiple sessions in one query. Returns {session_id: [tag_names]}."""
    if not session_ids:
        return {}
    _ensure_session_tags_table(db)
    placeholders = ",".join("?" for _ in session_ids)
    rows = db.execute(
        f"SELECT session_id, tag_name FROM session_tags WHERE session_id IN ({placeholders}) ORDER BY tag_name",
        session_ids,
    ).fetchall()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["session_id"], []).append(r["tag_name"])
    return result


def _ts_to_iso(ts):
    """Convert a unix timestamp (REAL) to ISO string."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()
    except (ValueError, OSError):
        return str(ts)


def _get_agent_sessions(limit: int = 100, offset: int = 0) -> list[dict]:
    """Get agent proxy sessions from OpenSoul DB."""
    adb = _get_agent_db()
    if not adb:
        return []
    try:
        rows = adb.execute(
            """
            SELECT id, agent_id, title, created_at, last_activity_at, message_count
            FROM agent_sessions
            WHERE archived = 0
            ORDER BY last_activity_at DESC
            LIMIT ? OFFSET ?
        """,
            (limit, offset),
        ).fetchall()
        sessions = []
        for r in rows:
            sessions.append(
                {
                    "id": r["id"],
                    "title": r["title"] or "Untitled",
                    "created_at": _ts_to_iso(r["created_at"]),
                    "updated_at": _ts_to_iso(r["last_activity_at"]),
                    "source": r["agent_id"],  # agent_id as source for frontend grouping
                    "message_count": r["message_count"] or 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            )
        return sessions
    except Exception as e:
        logger.error("get_agent_sessions error: %s", e)
        return []
    finally:
        adb.close()


@router.post("")
async def create_session(
    body: dict,
    user_id: UUID = Depends(get_current_user),
):
    """Create a new session in OpenSoul agent_sessions."""
    adb = _get_agent_db()
    if not adb:
        raise HTTPException(status_code=500, detail="Database not available")
    try:
        session_id = body.get("id") or f"om-{int(datetime.now(UTC).timestamp())}"
        agent_id = body.get("agent_id", "soulmate")
        title = body.get("name", "New Chat")
        now = datetime.now(UTC).timestamp()
        adb.execute(
            "INSERT OR IGNORE INTO agent_sessions (id, agent_id, title, created_at, last_activity_at, message_count) VALUES (?, ?, ?, ?, ?, 0)",
            (session_id, agent_id, title, now, now),
        )
        adb.commit()
        # 保存tags
        tags = body.get("tags", [])
        if tags:
            _ensure_session_tags_table(adb)
            for tag in tags:
                adb.execute(
                    "INSERT OR IGNORE INTO session_tags (session_id, tag_name) VALUES (?, ?)",
                    (session_id, tag),
                )
            adb.commit()
        return {"ok": True, "session_id": session_id}
    except Exception as e:
        logger.error("create_session error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        adb.close()


@router.get("")
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    tag: str = Query(default=None, description="Filter sessions by tag name"),
):
    """List recent sessions — merges Hermes sessions and agent proxy sessions."""
    all_sessions = []

    # 1. Hermes sessions
    db = _get_db()
    if db:
        try:
            rows = db.execute(
                """
                SELECT id, title, started_at, last_activity_at, source,
                       message_count, input_tokens, output_tokens
                FROM sessions
                WHERE archived = 0
                ORDER BY last_activity_at DESC
                LIMIT ? OFFSET ?
            """,
                (limit, offset),
            ).fetchall()
            for r in rows:
                all_sessions.append(
                    {
                        "id": r["id"],
                        "title": r["title"] or "Untitled",
                        "created_at": _ts_to_iso(r["started_at"]),
                        "updated_at": _ts_to_iso(r["last_activity_at"]),
                        "source": r["source"],
                        "message_count": r["message_count"] or 0,
                        "input_tokens": r["input_tokens"] or 0,
                        "output_tokens": r["output_tokens"] or 0,
                    }
                )
        except Exception as e:
            logger.error("list_sessions error: %s", e)
        finally:
            db.close()

    # 2. Agent proxy sessions
    agent_sessions = _get_agent_sessions(limit, offset)
    all_sessions.extend(agent_sessions)

    # 3. Sort by last_activity_at descending
    all_sessions.sort(key=lambda s: s.get("updated_at") or "", reverse=True)

    # 4. Attach tags to each session
    tag_db = _get_agent_db()
    if tag_db:
        try:
            session_ids = [s["id"] for s in all_sessions if s.get("id")]
            tags_map = _get_session_tags_map(tag_db, session_ids)
            for s in all_sessions:
                s["tags"] = tags_map.get(s["id"], [])

            # Filter by tag if requested
            if tag:
                all_sessions = [
                    s for s in all_sessions if tag.lower() in [t.lower() for t in s.get("tags", [])]
                ]
        finally:
            tag_db.close()

    return {
        "sessions": all_sessions[:limit],
        "total": len(all_sessions),
        "limit": limit,
        "offset": offset,
    }


@router.get("/search")
async def search_sessions(
    q: str = "",
    user_id: UUID = Depends(get_current_user),
):
    """Search sessions by title and message content."""
    if not q:
        return {"sessions": [], "query": q}

    db = _get_db()
    if not db:
        return {"sessions": [], "query": q, "error": "No session database found"}

    try:
        # Search session titles
        title_matches = db.execute(
            """
            SELECT DISTINCT s.id, s.title, s.started_at, s.last_activity_at, s.source
            FROM sessions s
            WHERE s.archived = 0 AND s.title LIKE ?
            ORDER BY s.started_at DESC
            LIMIT 20
        """,
            (f"%{q}%",),
        ).fetchall()

        # Search message content
        msg_matches = db.execute(
            """
            SELECT DISTINCT m.session_id, s.title, s.started_at, s.last_activity_at, s.source,
                   m.content as matched_content
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE s.archived = 0 AND m.active = 1 AND m.content LIKE ?
            ORDER BY s.started_at DESC
            LIMIT 20
        """,
            (f"%{q}%",),
        ).fetchall()

        # Merge results, dedup by session_id
        seen = set()
        results = []

        for r in title_matches:
            if r["id"] not in seen:
                seen.add(r["id"])
                results.append(
                    {
                        "id": r["id"],
                        "title": r["title"],
                        "created_at": _ts_to_iso(r["started_at"]),
                        "updated_at": _ts_to_iso(r["last_activity_at"]),
                        "source": r["source"],
                        "match_type": "title",
                        "snippet": None,
                    }
                )

        for r in msg_matches:
            if r["session_id"] not in seen:
                seen.add(r["session_id"])
                content = r["matched_content"] or ""
                idx = content.lower().find(q.lower())
                start = max(0, idx - 40)
                end = min(len(content), idx + len(q) + 40)
                snippet = (
                    ("..." if start > 0 else "")
                    + content[start:end]
                    + ("..." if end < len(content) else "")
                )

                results.append(
                    {
                        "id": r["session_id"],
                        "title": r["title"],
                        "created_at": _ts_to_iso(r["started_at"]),
                        "updated_at": _ts_to_iso(r["last_activity_at"]),
                        "source": r["source"],
                        "match_type": "content",
                        "snippet": snippet,
                    }
                )

        return {"sessions": results, "query": q, "total": len(results)}
    except Exception as e:
        logger.error("search_sessions error: %s", e)
        return {"sessions": [], "error": str(e)}
    finally:
        db.close()


# ── External Session Import API（P0-10 会话资产化：goose import_formats移植）──


class SessionImportRequest(BaseModel):
    file_path: str | None = None
    content: str | None = None
    agent_id: str = "imported"


@router.post("/import")
async def import_external_session(
    body: SessionImportRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Import an external agent transcript (Claude Code / Codex / Pi .jsonl)。

    嗅探格式→canonical转换→写入agent_sessions/agent_messages（前端现有
    /api/sessions 读路径直接可见）。确定性主键去重：同文件重复导入status=duplicate。
    未知格式/缺失文件显式报错，不静默。
    """
    from src.trajectory.import_formats import (
        MAX_IMPORT_BYTES,
        ImportFormatError,
        convert,
        import_to_db,
    )

    if body.content is None and not body.file_path:
        raise HTTPException(status_code=400, detail="file_path or content required")
    if body.content is not None:
        content = body.content
    else:
        file_path = body.file_path or ""
        fp = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.isfile(fp):
            raise HTTPException(status_code=404, detail=f"file not found: {fp}")
        if os.path.getsize(fp) > MAX_IMPORT_BYTES:
            raise HTTPException(status_code=413, detail="transcript too large")
        with open(fp, encoding="utf-8", errors="replace") as f:
            content = f.read()
    try:
        conv = convert(content)
        stats = import_to_db(conv, db_path=_OPENSOUL_DB, agent_id=body.agent_id or "imported")
    except ImportFormatError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    logger.info(
        "session import: %s fmt=%s status=%s messages=%s",
        stats["session_id"],
        stats["source_format"],
        stats["status"],
        stats["messages_written"],
    )
    return {"ok": True, **stats}


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Get session details — checks both Hermes and agent sessions."""
    # Try Hermes first
    db = _get_db()
    if db:
        try:
            row = db.execute(
                """
                SELECT id, title, started_at, last_activity_at, source,
                       message_count, input_tokens, output_tokens, estimated_cost_usd
                FROM sessions WHERE id = ? AND archived = 0
            """,
                (session_id,),
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "title": row["title"],
                    "created_at": _ts_to_iso(row["started_at"]),
                    "updated_at": _ts_to_iso(row["last_activity_at"]),
                    "source": row["source"],
                    "message_count": row["message_count"] or 0,
                    "input_tokens": row["input_tokens"] or 0,
                    "output_tokens": row["output_tokens"] or 0,
                    "estimated_cost_usd": row["estimated_cost_usd"] or 0,
                }
        finally:
            db.close()

    # Try agent sessions
    adb = _get_agent_db()
    if adb:
        try:
            row = adb.execute(
                """
                SELECT id, agent_id, title, created_at, last_activity_at, message_count
                FROM agent_sessions WHERE id = ? AND archived = 0
            """,
                (session_id,),
            ).fetchone()
            if row:
                return {
                    "id": row["id"],
                    "title": row["title"],
                    "created_at": _ts_to_iso(row["created_at"]),
                    "updated_at": _ts_to_iso(row["last_activity_at"]),
                    "source": row["agent_id"],
                    "message_count": row["message_count"] or 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost_usd": 0,
                }
        finally:
            adb.close()

    raise HTTPException(status_code=404, detail="Session not found")


@router.patch("/{session_id}")
async def rename_session(
    session_id: str,
    body: dict,
    user_id: UUID = Depends(get_current_user),
):
    """Rename a session (update title) — works for both Hermes and agent sessions.

    身份轴（SESS-① 留档缺口收口）：与同文件 delete/get/search 等鉴权家族同款
    get_current_user 门——修前本端点是家族唯一无鉴权写口，未认证调用方可改任意
    会话标题。user_id 仅作鉴权轴（会话行无归属列，行级过滤不存在=同 delete 语义）。
    """
    new_title = body.get("title", "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Title is required")

    # Try Hermes first
    db = _get_db()
    if db:
        try:
            row = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                db.execute("UPDATE sessions SET title = ? WHERE id = ?", (new_title, session_id))
                db.commit()
                return {"success": True, "id": session_id, "title": new_title}
        finally:
            db.close()

    # Try agent sessions
    adb = _get_agent_db()
    if adb:
        try:
            row = adb.execute(
                "SELECT id FROM agent_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row:
                adb.execute(
                    "UPDATE agent_sessions SET title = ? WHERE id = ?", (new_title, session_id)
                )
                adb.commit()
                return {"success": True, "id": session_id, "title": new_title}
        finally:
            adb.close()

    raise HTTPException(status_code=404, detail="Session not found")


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Delete a session and all its messages — works for both Hermes and agent sessions."""
    # Try Hermes first
    db = _get_db()
    if db:
        try:
            row = db.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                try:
                    db.execute("DELETE FROM session_lineage WHERE session_id = ?", (session_id,))
                except Exception as exc:
                    logging.getLogger(__name__).debug("probe skipped: %s", exc)
                db.commit()
                return {"success": True, "deleted_session": session_id}
        finally:
            db.close()

    # Try agent sessions
    adb = _get_agent_db()
    if adb:
        try:
            row = adb.execute(
                "SELECT id FROM agent_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row:
                adb.execute("DELETE FROM agent_messages WHERE session_id = ?", (session_id,))
                adb.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))
                # pi branch-summary配套清理：防agent_branch_summaries孤儿行
                try:
                    from src.trajectory.branch_summary import delete_branch_summaries

                    delete_branch_summaries(adb, session_id)
                except Exception as exc:
                    logging.getLogger(__name__).debug("branch summary cleanup skipped: %s", exc)
                adb.commit()
                return {"success": True, "deleted_session": session_id}
        finally:
            adb.close()

    return {"success": False, "error": "Session not found"}


def _decode_memory_marker(raw) -> dict | None:
    """kilocode #9记忆marker读侧解码（marker-meta.ts fromParts语义）。

    agent_messages.metadata JSON的kiloMemory字段（acp-proxy
    agent/memory_marker.py写入）→ {type,tokens,count,files,items}。files缺失时
    回退sources键（kilocode：兼容dropped之前的老格式）。损坏/缺失JSON→None：
    读路径绝不因marker解析失败丢整条消息（deepseek"溢写失败绝不能把成功调用
    变错"语义）。
    """
    if not raw:
        return None
    try:
        meta = json.loads(raw)
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    km = meta.get("kiloMemory")
    if not isinstance(km, dict):
        return None
    files = km.get("files")
    if not isinstance(files, list):
        sources = km.get("sources")
        files = sources if isinstance(sources, list) else []
    files = [f for f in files if isinstance(f, str)]
    items = km.get("items")
    items = [i for i in items if isinstance(i, str)] if isinstance(items, list) else []
    return {
        "type": "startup" if km.get("type") == "startup" else "recall",
        "tokens": km.get("tokens") if isinstance(km.get("tokens"), (int, float)) else 0,
        "count": km.get("count") if isinstance(km.get("count"), (int, float)) else len(files),
        "files": files,
        "items": items,
    }


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Get messages for a session — works for both Hermes and agent sessions."""
    # Try Hermes first
    db = _get_db()
    if db:
        try:
            rows = db.execute(
                """
                SELECT id, role, content, tool_calls, tool_name, timestamp
                FROM messages
                WHERE session_id = ? AND active = 1 AND compacted = 0
                ORDER BY id
            """,
                (session_id,),
            ).fetchall()
            if rows:
                messages = []
                for r in rows:
                    role = r["role"]
                    content = r["content"] or ""
                    if role == "tool":
                        continue
                    if role == "assistant" and not content.strip():
                        continue
                    messages.append(
                        {
                            "id": str(r["id"]),
                            "role": role,
                            "content": content,
                            "timestamp": _ts_to_iso(r["timestamp"]),
                            "source": "hermes-db",
                        }
                    )
                # Hermes路径无消息树/分支摘要概念，契约一致携带空列表
                return {"messages": messages, "total": len(messages), "branch_summaries": []}
        finally:
            db.close()

    # Try agent sessions
    adb = _get_agent_db()
    if adb:
        try:
            try:
                adb.execute("SELECT attachments FROM agent_messages LIMIT 1")
            except Exception:
                adb.execute("ALTER TABLE agent_messages ADD COLUMN attachments TEXT")
            # kilocode #9记忆marker：metadata列probe+ALTER幂等迁移（attachments同款）
            try:
                adb.execute("SELECT metadata FROM agent_messages LIMIT 1")
            except Exception:
                adb.execute("ALTER TABLE agent_messages ADD COLUMN metadata TEXT")
            # P0-10消息树：读路径同样触发parentId列迁移（幂等，首个触达者完成全库迁移）
            from src.trajectory.message_tree import ensure_parent_column

            ensure_parent_column(adb)
            rows = adb.execute(
                """
                SELECT id, role, content, timestamp, attachments, parent_message_id, metadata
                FROM agent_messages
                WHERE session_id = ?
                ORDER BY id
            """,
                (session_id,),
            ).fetchall()
            messages = []
            for r in rows:
                attachments_out = []
                if r["attachments"]:
                    try:
                        att_list = json.loads(r["attachments"])
                        for att in att_list:
                            att_data = None
                            att_path = att.get("path")
                            if att_path and os.path.exists(att_path):
                                with open(att_path, "rb") as af:
                                    att_data = base64.b64encode(af.read()).decode()
                            attachments_out.append(
                                {
                                    "type": att.get("type", "file"),
                                    "name": att.get("name", "file"),
                                    "mime_type": att.get("mime_type", "application/octet-stream"),
                                    "data": att_data,
                                }
                            )
                    except Exception as _att_e:
                        logger.warning("parse attachments error: %s", _att_e)
                messages.append(
                    {
                        "id": str(r["id"]),
                        "role": r["role"],
                        "content": r["content"] or "",
                        "timestamp": _ts_to_iso(r["timestamp"]),
                        "source": "agent-db",
                        "attachments": attachments_out if attachments_out else None,
                        # P0-10消息树parentId（open-webui/pi）：NULL=会话根节点
                        "parent_id": (
                            str(r["parent_message_id"])
                            if r["parent_message_id"] is not None
                            else None
                        ),
                        # kilocode #9记忆marker（"本回复用了记忆"badge数据源，
                        # marker-meta.ts synthetic+ignored part的读侧暴露）
                        "memory_marker": _decode_memory_marker(r["metadata"]),
                    }
                )
            # pi branch-summarization读侧：会话的切分支摘要随消息读路径下发
            # （BranchSummaryEntry参与上下文的OpenSoul映射；表不存在=空列表自愈）
            from src.trajectory.branch_summary import (
                ensure_branch_summary_table,
                get_branch_summaries,
            )

            ensure_branch_summary_table(adb)
            summaries = get_branch_summaries(adb, session_id)
            return {
                "messages": messages,
                "total": len(messages),
                "branch_summaries": summaries,
            }
        finally:
            adb.close()

    return {"messages": [], "total": 0}


# ── 消息树 Fork API（P0-10：open-webui build_fork_history + pi parentId追加树）──


class SessionForkRequest(BaseModel):
    message_id: int | str | None = None


@router.post("/{session_id}/fork")
async def fork_session_at_message(
    session_id: str,
    body: SessionForkRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Fork会话消息树分支——从message_id沿parent_message_id回溯到根，分支复制为新会话。

    open-webui chat_fork.py build_fork_history语义 + pi追加树数据模型（P0-10四方定案）。
    确定性主键 fork:{session}:{message}（agno幂等键）：同分支点重复fork返回
    status=duplicate零重复写入。环/消息不存在/空会话/源会话不存在显式报错不静默。
    仅agent_sessions路径有消息树；Hermes state.db的sessions无parentId概念不支持fork。
    """
    from src.trajectory.message_tree import (
        CycleError,
        EmptyChatError,
        MessageNotFoundError,
        MessageTreeError,
        SessionNotFoundError,
        ensure_parent_column,
    )
    from src.trajectory.message_tree import (
        fork_session as fork_session_tree,
    )

    if body.message_id is None or str(body.message_id).strip() == "":
        raise HTTPException(status_code=400, detail="message_id required")
    if not os.path.exists(_OPENSOUL_DB):
        raise HTTPException(status_code=500, detail="Database not available")
    # 读写两侧都先过幂等迁移：历史会话（迁移前创建）同样可fork
    adb = _get_agent_db()
    if adb:
        try:
            ensure_parent_column(adb)
        finally:
            adb.close()
    try:
        stats = fork_session_tree(_OPENSOUL_DB, session_id, body.message_id)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except MessageNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except CycleError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except EmptyChatError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except MessageTreeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # pi navigateTree {summarize:true}的fork映射：fork=从源会话叶"导航"到fork点，
    # 被离开的分支=源会话fork点之后的尾部——自动摘要挂fork产物会话名下。
    # 摘要失败不回滚fork（fork已commit）：branch_summary显式携带error状态（失败可见）。
    if stats.get("status") == "forked":
        try:
            from src.trajectory.branch_summary import (
                resolve_summarizer,
                summarize_fork_context,
            )

            stats["branch_summary"] = summarize_fork_context(
                _OPENSOUL_DB,
                session_id,
                body.message_id,
                stats["fork_session_id"],
                summarizer=resolve_summarizer(None),  # auto：provider可用→LLM
            )
        except Exception as bs_err:
            logger.warning("branch summary after fork failed: %s", bs_err)
            stats["branch_summary"] = {"status": "error", "error": str(bs_err)}
    logger.info(
        "session fork: src=%s point=%s status=%s copied=%s",
        session_id,
        body.message_id,
        stats.get("status"),
        stats.get("messages_copied"),
    )
    return {"ok": True, **stats}


# ── 切分支自动摘要 API（pi branch-summarization.ts，P0-10遗留#1销账）──


class BranchSummaryRequest(BaseModel):
    """pi navigateTree(targetId, {summarize:true})的HTTP映射。"""

    from_message_id: int | str | None = None  # old leaf（被离开分支的叶）
    target_message_id: int | str | None = None  # 导航目标
    summarizer: str | None = None  # llm|extractive|auto（默认auto，env可覆盖）


@router.post("/{session_id}/branch-summaries")
async def create_branch_summary(
    session_id: str,
    body: BranchSummaryRequest,
    user_id: UUID = Depends(get_current_user),
):
    """同会话切分支摘要：收集from→公共祖先的被离开分支条目，生成结构化摘要落库。

    pi branch-summarization.ts collectEntriesForBranchSummary+generateBranchSummary
    语义。summarizer模式经resolve_summarizer解析（默认auto：provider已配置→
    LLM summarizer走BRANCH_SUMMARY_PROMPT原文，失败可见降级extractive；env
    BRANCH_SUMMARY_SUMMARIZER=extractive可强制离线路径）。异常映射与fork端点
    同款（MessageNotFound→404，Cycle→409）。
    """
    from src.trajectory.branch_summary import resolve_summarizer, summarize_branch
    from src.trajectory.message_tree import (
        CycleError,
        EmptyChatError,
        MessageNotFoundError,
        MessageTreeError,
        SessionNotFoundError,
    )

    if body.from_message_id is None or str(body.from_message_id).strip() == "":
        raise HTTPException(status_code=400, detail="from_message_id required")
    if body.target_message_id is None or str(body.target_message_id).strip() == "":
        raise HTTPException(status_code=400, detail="target_message_id required")
    if not os.path.exists(_OPENSOUL_DB):
        raise HTTPException(status_code=500, detail="Database not available")
    try:
        summarizer = resolve_summarizer(body.summarizer)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        stats = summarize_branch(
            _OPENSOUL_DB,
            session_id,
            body.from_message_id,
            body.target_message_id,
            summarizer=summarizer,
        )
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except MessageNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except CycleError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except EmptyChatError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except MessageTreeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    logger.info(
        "branch summary: session=%s from=%s target=%s status=%s",
        session_id,
        body.from_message_id,
        body.target_message_id,
        stats.get("status"),
    )
    return {"ok": True, **stats}


@router.get("/{session_id}/branches")
async def get_session_branches(
    session_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """会话消息树可观测：分支点（parent有≥2子消息的节点）+ 全部切分支摘要。"""
    from src.trajectory.branch_summary import (
        get_branch_summaries,
        list_branch_points,
    )

    if not os.path.exists(_OPENSOUL_DB):
        raise HTTPException(status_code=500, detail="Database not available")
    adb = _get_agent_db()
    if not adb:
        return {"session_id": session_id, "branch_points": [], "branch_summaries": []}
    try:
        points = list_branch_points(adb, session_id)
        summaries = get_branch_summaries(adb, session_id)
        return {
            "session_id": session_id,
            "branch_points": points,
            "branch_summaries": summaries,
        }
    finally:
        adb.close()


# ── Session Tags API ──────────────────────────────────────────


class TagRequest(BaseModel):
    tag_name: str


@router.get("/tags/all")
async def list_all_tags():
    """List all unique tag names across all sessions."""
    db = _get_agent_db()
    if not db:
        return {"tags": []}
    try:
        _ensure_session_tags_table(db)
        rows = db.execute(
            """SELECT tag_name, COUNT(*) as count
               FROM session_tags GROUP BY tag_name ORDER BY tag_name"""
        ).fetchall()
        return {"tags": [{"name": r["tag_name"], "count": r["count"]} for r in rows]}
    except Exception as e:
        logger.error("list_all_tags error: %s", e)
        # C2统一错误响应：失败带error字段，禁空列表冒充"没标签"
        return {"tags": [], "error": str(e)}
    finally:
        db.close()


@router.post("/{session_id}/tags")
async def add_session_tag(
    session_id: str,
    body: TagRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Add a tag to a session.

    身份轴（SESS-② 勘误留档缺口收口）：与同文件 rename/delete 等鉴权家族同款
    get_current_user 门——修前 tags 写端点（add/remove）与 PATCH 同缺鉴权，
    未认证调用方可给任意会话打/删标签。user_id 仅作鉴权轴（会话行无归属列，
    行级过滤不存在=同 delete/rename 语义）。读面（GET tags）保留既有浏览语义。
    """
    tag_name = body.tag_name.strip().lower()
    if not tag_name or len(tag_name) > 50:
        raise HTTPException(400, "Tag name must be 1-50 characters")
    db = _get_agent_db()
    if not db:
        raise HTTPException(500, "Database not available")
    try:
        _ensure_session_tags_table(db)
        db.execute(
            "INSERT OR IGNORE INTO session_tags (session_id, tag_name) VALUES (?, ?)",
            (session_id, tag_name),
        )
        db.commit()
        return {"session_id": session_id, "tag": tag_name, "status": "added"}
    except Exception as e:
        logger.error("add_session_tag error: %s", e)
        raise HTTPException(500, str(e))
    finally:
        db.close()


@router.delete("/{session_id}/tags/{tag_name}")
async def remove_session_tag(
    session_id: str,
    tag_name: str,
    user_id: UUID = Depends(get_current_user),
):
    """Remove a tag from a session.

    身份轴（SESS-② 勘误留档缺口收口）：同 add_session_tag 的 get_current_user 门
    （鉴权轴非归属轴，同 delete/rename 语义）。
    """
    db = _get_agent_db()
    if not db:
        raise HTTPException(500, "Database not available")
    try:
        _ensure_session_tags_table(db)
        db.execute(
            "DELETE FROM session_tags WHERE session_id = ? AND tag_name = ?",
            (session_id, tag_name.strip().lower()),
        )
        db.commit()
        return {"session_id": session_id, "tag": tag_name, "status": "removed"}
    except Exception as e:
        logger.error("remove_session_tag error: %s", e)
        raise HTTPException(500, str(e))
    finally:
        db.close()


@router.get("/{session_id}/tags")
async def get_session_tags(session_id: str):
    """Get all tags for a specific session."""
    db = _get_agent_db()
    if not db:
        return {"tags": []}
    try:
        _ensure_session_tags_table(db)
        rows = db.execute(
            "SELECT tag_name, created_at FROM session_tags WHERE session_id = ? ORDER BY tag_name",
            (session_id,),
        ).fetchall()
        return {"tags": [{"name": r["tag_name"], "created_at": r["created_at"]} for r in rows]}
    finally:
        db.close()
