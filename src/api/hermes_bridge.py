"""Hermes session bridge - lists sessions from ALL platforms (WeChat, Telegram, etc.)
and allows sending messages to specific sessions."""

import asyncio
import logging
import os
import re
import subprocess
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.user import get_current_user

router = APIRouter()

# Hermes 会话库单一路径源（SESS-⑥）：修前三 handler 内联 expanduser 三处
# 手抄，收口为模块常量（测试可注入隔离，绝不写生产库）。
_DB_PATH = os.path.expanduser("~/.hermes/state.db")


@router.get("/health")
async def hermes_bridge_health():
    """HermesBridge health check."""
    return {"status": "ok", "component": "HermesBridge"}


logger = logging.getLogger(__name__)


class SendMessageRequest(BaseModel):
    text: str
    session_id: str | None = None


def _list_sessions_sync(limit: int, source: str | None = None) -> dict:
    """Synchronous hermes sessions list (runs in thread executor)."""
    env = {**os.environ, "COLUMNS": "200"}
    cmd = ["hermes", "sessions", "list", "--limit", str(limit)]
    if source:
        cmd.extend(["--source", source])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        output = result.stdout.strip()

        sessions = []
        for line in output.split("\n"):
            # Skip header/separator lines
            if ("Preview" in line or "Title" in line) and "Workspace" in line:
                continue
            if line.startswith("─") or line.startswith("═") or not line.strip():
                continue

            # Find session ID at end of line
            m = re.search(r"(\d{8}_\d{6}_[a-f0-9]+|cron_\w+)$", line)
            if not m:
                continue

            sid = m.group(1)
            rest = line[: m.start()].rstrip()

            # Split rest by 2+ spaces (column separator)
            cols = re.split(r"\s{2,}", rest)

            if len(cols) >= 4:
                name_or_title, workspace, last_active, src = (
                    cols[0].strip(),
                    cols[1].strip(),
                    cols[2].strip(),
                    cols[3].strip(),
                )
            elif len(cols) >= 3:
                name_or_title, workspace, last_active = (
                    cols[0].strip(),
                    cols[1].strip(),
                    cols[2].strip(),
                )
                src = "cli"
            elif len(cols) >= 2:
                name_or_title, workspace = cols[0].strip(), cols[1].strip()
                last_active, src = "", "cli"
            else:
                name_or_title = cols[0].strip() if cols else ""
                workspace, last_active, src = "", "", "cli"

            # Map source to platform
            platform_map = {
                "cli": "hermes",
                "wx": "wechat",
                "wxentry": "wechat",
                "tg": "telegram",
                "dc": "discord",
            }
            platform = platform_map.get(src.lower(), src.lower() or "hermes")

            # Use session ID as fallback name when title is "—"
            display_name = name_or_title if name_or_title and name_or_title != "—" else sid

            sessions.append(
                {
                    "id": sid,
                    "name": display_name,
                    "platform": platform,
                    "chat_id": "",
                    "workspace": workspace,
                    "last_active": last_active,
                    "last_message": name_or_title[:80] if name_or_title else "",
                }
            )

        return {"sessions": sessions, "total": len(sessions)}
    except Exception as e:
        logger.error("list_sessions error: %s", e)
        return {"sessions": [], "total": 0, "error": str(e)}


@router.get("/sessions")
async def list_sessions(
    source: str | None = None,
    limit: int = 50,
    user_id: UUID = Depends(get_current_user),
):
    """列出Hermes所有平台的会话（微信、Telegram等）"""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _list_sessions_sync, limit, source)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, user_id: UUID = Depends(get_current_user)):
    """获取指定会话的历史消息 - 直接查SQLite数据库"""
    import sqlite3

    try:
        db = sqlite3.connect(_DB_PATH)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT id, role, content, tool_calls, tool_name, timestamp
               FROM messages
               WHERE session_id = ? AND active = 1 AND compacted = 0
               ORDER BY id""",
            (session_id,),
        ).fetchall()
        db.close()

        messages = []
        for r in rows:
            role = r["role"]
            content = r["content"] or ""
            # Skip tool messages and empty assistant messages
            if role == "tool":
                continue
            if role == "assistant" and not content.strip():
                continue
            messages.append(
                {
                    "id": str(r["id"]),
                    "role": role,
                    "content": content,
                    "timestamp": r["timestamp"] or "",
                    "source": "hermes-db",
                }
            )

        return {"messages": messages, "total": len(messages)}
    except sqlite3.DatabaseError as e:
        # SESS-⑥ 同族降级（SESS-④/⑤ 家族语义）：只认 sqlite3.DatabaseError
        # 家族不宽泛吞（自身 bug 不静默）；本面纯 hermes 读面无 agent 回落面
        # -> 空载荷+加性键 hermes_unreadable（不谎报"真无消息"，C2 失败带
        # error 字段禁空列表冒充）+日志带真因。
        logger.warning("get_session_messages: hermes store unreadable (%s)", e)
        return {
            "messages": [],
            "total": 0,
            "error": "hermes session store unreadable",
            "hermes_unreadable": True,
        }
    except Exception as e:
        logger.error("get_session_messages error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user_id: UUID = Depends(get_current_user)):
    """删除指定会话及其所有消息"""
    import sqlite3

    try:
        db = sqlite3.connect(_DB_PATH)
        # Delete messages first
        db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        # Delete session
        db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        # Try to delete from session_lineage too
        try:
            db.execute("DELETE FROM session_lineage WHERE session_id = ?", (session_id,))
        except Exception as exc:
            logging.getLogger(__name__).debug("probe skipped: %s", exc)
        db.commit()
        db.close()
        return {"success": True, "deleted_session": session_id}
    except sqlite3.DatabaseError as e:
        # SESS-⑥ 同族降级：200+success:False 既有失败契约字节不变，只换
        # 真因文案+加性键 hermes_unreadable（与 sessions_api delete 降级
        # 真因同文案同族）。
        logger.warning("delete_session: hermes store unreadable (%s)", e)
        return {
            "success": False,
            "error": "hermes session store unreadable",
            "hermes_unreadable": True,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/sessions/send")
async def send_message(
    req: SendMessageRequest,
    user_id: UUID = Depends(get_current_user),
):
    """向指定会话发送消息"""
    try:
        if req.session_id:
            cmd = ["hermes", "send", "--session", req.session_id, "--", req.text]
        else:
            cmd = ["hermes", "-z", req.text]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode("utf-8", errors="replace").strip()

        return {"ok": True, "content": output, "source": "hermes-cli"}
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Hermes response timeout")
    except Exception as e:
        logger.error("send_message error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/search")
async def search_sessions(q: str = "", user_id: UUID = Depends(get_current_user)):
    """搜索会话 - 同时搜索会话名称和消息内容"""
    import sqlite3

    if not q:
        return {"sessions": [], "query": q}
    try:
        db = sqlite3.connect(_DB_PATH)
        db.row_factory = sqlite3.Row

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

        db.close()

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
                        "created_at": r["started_at"],
                        "updated_at": r["last_activity_at"],
                        "source": r["source"],
                        "match_type": "title",
                        "snippet": None,
                    }
                )

        for r in msg_matches:
            if r["session_id"] not in seen:
                seen.add(r["session_id"])
                # Extract snippet around match
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
                        "created_at": r["started_at"],
                        "updated_at": r["last_activity_at"],
                        "source": r["source"],
                        "match_type": "content",
                        "snippet": snippet,
                    }
                )

        return {"sessions": results, "query": q, "total": len(results)}
    except sqlite3.DatabaseError as e:
        # SESS-⑥ 同族降级：error 键集（sessions+error）修前形状字节不变，
        # 只换真因文案+加性键 hermes_unreadable（禁空列表冒充"无搜索结果"）。
        logger.warning("search_sessions: hermes store unreadable (%s)", e)
        return {
            "sessions": [],
            "error": "hermes session store unreadable",
            "hermes_unreadable": True,
        }
    except Exception as e:
        return {"sessions": [], "error": str(e)}
