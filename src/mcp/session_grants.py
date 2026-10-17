"""MCP session-level tool isolation — consumer allowlist + publish-side auth.

P1 gap (SUMMARY.md §五 mcp行: "会话级工具隔离endpoint + 发布侧auth + 消费侧白名单"，
参照 Composio toolkits auth + swarms MCPDeployer 发布侧鉴权 + ODR MCPConfig 按agent白名单)。

三个交付面（一条scope规则服务两个消费面）：
1. **会话级工具隔离**：一个会话只能看到/调用被显式授权（grant）的 server/tool，
   fail-closed（Letta memory-confinement"无sandbox直接报错不降级"）——零 grant = 零可见。
2. **发布侧auth**：被发布的工具面（registry tools + src/mcp/server.py stdio工具）
   必须持 consumer token 才能消费；token 只在签发时明文返回一次，落库 sha256 摘要
   （GitHub PAT 语义——泄库不泄token）；revoke 后立即失效。
3. **消费侧白名单**：consumer scopes 为 "*" 或精确 scope 串；
   registry 消费请求 scope = server_id；stdio 发布面 scope = "tool:<name>"。

scopes 统一匹配规则（evolution-engine-patterns §4.3"调用时拒绝"而非藏起来）：
    "*" in scopes → 放行；否则要求精确命中请求 scope 串；其余一律拒绝。

审计语义（mem0 §1.2"失败必须可见"）：每次拒绝都返回明确 reason，
不静默返回空列表假装"没有工具"。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time

# 与 McpServerRegistry 共用同一 SQLite 真源（server_registry.DEFAULT_DB_PATH）
from src.mcp.server_registry import DEFAULT_DB_PATH

# 请求 scope 的前缀约定：stdio 发布面按工具粒度鉴权
PUBLISHED_TOOL_SCOPE_PREFIX = "tool:"


def published_tool_scope(tool_name: str) -> str:
    """stdio 发布面的 scope 串：tool:<name>。"""
    return f"{PUBLISHED_TOOL_SCOPE_PREFIX}{tool_name}"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionToolGate:
    """会话级工具隔离 + consumer token 签发/校验（同一 mcp.db 真源）。"""

    def __init__(self, db_path: str | None = None):
        db = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(db), exist_ok=True)
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._init_db()

    def _init_db(self):
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS mcp_session_grants (
                session_id  TEXT NOT NULL,
                server_id   TEXT NOT NULL,
                tool_name   TEXT NOT NULL DEFAULT '*',
                granted_by  TEXT DEFAULT '',
                created_at  REAL NOT NULL,
                PRIMARY KEY (session_id, server_id, tool_name)
            );
            CREATE INDEX IF NOT EXISTS idx_grants_session
                ON mcp_session_grants(session_id);
            CREATE TABLE IF NOT EXISTS mcp_consumers (
                consumer_id TEXT PRIMARY KEY,
                token_hash  TEXT NOT NULL,
                scopes_json TEXT NOT NULL DEFAULT '["*"]',
                created_at  REAL NOT NULL,
                revoked     INTEGER DEFAULT 0
            );
            """
        )
        self._db.commit()

    # ── 会话级工具隔离（消费侧白名单） ─────────────────────────

    def grant(
        self,
        session_id: str,
        server_id: str,
        tool_name: str = "*",
        granted_by: str = "",
    ) -> dict:
        """显式授权：session → server/tool（tool_name='*' = 整个 server）。"""
        if not session_id or not server_id or not tool_name:
            raise ValueError("session_id / server_id / tool_name must be non-empty")
        self._db.execute(
            """INSERT OR REPLACE INTO mcp_session_grants
               (session_id, server_id, tool_name, granted_by, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, server_id, tool_name, granted_by, time.time()),
        )
        self._db.commit()
        return {
            "session_id": session_id,
            "server_id": server_id,
            "tool_name": tool_name,
            "granted_by": granted_by,
        }

    def revoke(self, session_id: str, server_id: str, tool_name: str = "*") -> bool:
        result = self._db.execute(
            "DELETE FROM mcp_session_grants WHERE session_id = ? AND server_id = ? AND tool_name = ?",
            (session_id, server_id, tool_name),
        )
        self._db.commit()
        return result.rowcount > 0

    def clear_session(self, session_id: str) -> int:
        result = self._db.execute(
            "DELETE FROM mcp_session_grants WHERE session_id = ?", (session_id,)
        )
        self._db.commit()
        return result.rowcount

    def list_grants(self, session_id: str | None = None) -> list[dict]:
        if session_id:
            rows = self._db.execute(
                "SELECT * FROM mcp_session_grants WHERE session_id = ? ORDER BY server_id, tool_name",
                (session_id,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM mcp_session_grants ORDER BY session_id, server_id, tool_name"
            ).fetchall()
        return [dict(r) for r in rows]

    def is_tool_allowed(self, session_id: str, server_id: str, tool_name: str) -> bool:
        """fail-closed：无显式 grant 一律 False（零信任默认拒绝）。"""
        row = self._db.execute(
            """SELECT 1 FROM mcp_session_grants
               WHERE session_id = ? AND server_id = ?
                 AND (tool_name = '*' OR tool_name = ?)
               LIMIT 1""",
            (session_id, server_id, tool_name),
        ).fetchone()
        return row is not None

    def allowed_server_ids(self, session_id: str) -> set[str]:
        rows = self._db.execute(
            "SELECT DISTINCT server_id FROM mcp_session_grants WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return {r["server_id"] for r in rows}

    # ── 发布侧auth：consumer token 签发/校验 ─────────────────────

    def issue_consumer(self, consumer_id: str, scopes: list[str] | None = None) -> tuple[str, dict]:
        """签发 consumer token。明文只返回这一次，落库仅 sha256 摘要。"""
        if not consumer_id:
            raise ValueError("consumer_id must be non-empty")
        token = "mcp_" + secrets.token_urlsafe(32)
        scope_list = scopes or ["*"]
        if not isinstance(scope_list, list) or not all(isinstance(s, str) for s in scope_list):
            raise ValueError("scopes must be a list of strings")

        self._db.execute(
            """INSERT OR REPLACE INTO mcp_consumers
               (consumer_id, token_hash, scopes_json, created_at, revoked)
               VALUES (?, ?, ?, ?, 0)""",
            (consumer_id, _hash_token(token), json.dumps(scope_list), time.time()),
        )
        self._db.commit()
        return token, {
            "consumer_id": consumer_id,
            "scopes": scope_list,
            "created_at": time.time(),
            "revoked": False,
        }

    def authenticate(self, token: str | None) -> dict | None:
        """token → consumer 记录；缺失/未知/已吊销 → None（fail-closed）。"""
        if not token:
            return None
        row = self._db.execute(
            "SELECT * FROM mcp_consumers WHERE token_hash = ? AND revoked = 0",
            (_hash_token(token),),
        ).fetchone()
        if row is None:
            return None

        return {
            "consumer_id": row["consumer_id"],
            "scopes": json.loads(row["scopes_json"] or "[]"),
            "created_at": row["created_at"],
            "revoked": bool(row["revoked"]),
        }

    def list_consumers(self) -> list[dict]:
        """列表绝不回显 token / token_hash（泄库不泄token）。"""
        rows = self._db.execute(
            "SELECT consumer_id, scopes_json, created_at, revoked FROM mcp_consumers ORDER BY consumer_id"
        ).fetchall()

        return [
            {
                "consumer_id": r["consumer_id"],
                "scopes": json.loads(r["scopes_json"] or "[]"),
                "created_at": r["created_at"],
                "revoked": bool(r["revoked"]),
            }
            for r in rows
        ]

    def revoke_consumer(self, consumer_id: str) -> bool:
        result = self._db.execute(
            "UPDATE mcp_consumers SET revoked = 1 WHERE consumer_id = ? AND revoked = 0",
            (consumer_id,),
        )
        self._db.commit()
        return result.rowcount > 0

    @staticmethod
    def consumer_may_use(consumer: dict, scope: str) -> bool:
        """scopes 统一匹配：'*' 全放行，否则精确命中；其余拒绝。"""
        scopes = consumer.get("scopes") or []
        return "*" in scopes or scope in scopes

    def stats(self) -> dict:
        grants = self._db.execute("SELECT COUNT(*) AS c FROM mcp_session_grants").fetchone()
        sessions = self._db.execute(
            "SELECT COUNT(DISTINCT session_id) AS c FROM mcp_session_grants"
        ).fetchone()
        consumers = self._db.execute(
            "SELECT COUNT(*) AS c FROM mcp_consumers WHERE revoked = 0"
        ).fetchone()
        return {
            "session_grants": grants["c"],
            "isolated_sessions": sessions["c"],
            "active_consumers": consumers["c"],
        }


_default_gate: SessionToolGate | None = None


def default_gate() -> SessionToolGate:
    """懒加载单例（与 api/mcp.py 的 registry 同一 mcp.db 真源）。"""
    global _default_gate
    if _default_gate is None:
        _default_gate = SessionToolGate()
    return _default_gate


def check_tool_scope(
    token: str | None, scope: str, gate: SessionToolGate | None = None
) -> tuple[bool, str]:
    """发布面统一鉴权入口（src/mcp/server.py 每个工具调用）。

    返回 (allowed, reason)；拒绝必须带 reason（mem0 §1.1 失败可见）。
    """
    if not token:
        return False, "unauthorized: missing token"
    consumer = (gate or default_gate()).authenticate(token)
    if consumer is None:
        return False, "unauthorized: invalid or revoked token"
    if not SessionToolGate.consumer_may_use(consumer, scope):
        return False, f"forbidden: consumer scope does not include '{scope}'"
    return True, "ok"
