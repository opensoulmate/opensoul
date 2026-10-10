"""Multi-Agent Coordination — 多Agent协作。

多个OpenMate实例共享同一个OpenSoul大脑：
- 共享经验记忆（一个Agent学到的，其他Agent也能用）
- 协作感知（知道其他Agent在做什么）
- 冲突避免（多个Agent不重复修改同一文件）
- 分工协调（复杂任务拆分给不同Agent）
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class MultiAgentCoordinator:
    """多Agent协作协调器"""

    def __init__(self, db_pool=None, tenant_id: str = "default"):
        self.db = db_pool
        self.tenant_id = tenant_id
        self._initialized = False

    async def _ensure_table(self):
        if self._initialized:
            return
        if self.db:
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS agent_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    agent_type TEXT DEFAULT 'openmate',
                    status TEXT DEFAULT 'idle',
                    current_task TEXT,
                    current_files TEXT DEFAULT '[]',
                    last_heartbeat REAL,
                    created_at REAL NOT NULL,
                    UNIQUE(tenant_id, agent_id)
                )
            """)
            # C1（2026-10-11）拆表：原表名agent_messages与消息树（acp-proxy/
            # ws_chat同库的session_id/role/content/timestamp形状）同名不同形，
            # 共享一张表必互相"no such column/NOT NULL冲突"。各归各表：协作
            # 收件箱落自己的表，消息树及其存量数据零改动（防假行被
            # experience_collector按消息树语义采集）。
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS agent_coord_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    from_agent TEXT NOT NULL,
                    to_agent TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    read INTEGER DEFAULT 0,
                    created_at REAL NOT NULL
                )
            """)
        self._initialized = True

    async def register_agent(self, agent_id: str, agent_type: str = "openmate"):
        """注册Agent"""
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            """
            INSERT INTO agent_registry (tenant_id, agent_id, agent_type, status, last_heartbeat, created_at)
            VALUES (?, ?, ?, 'idle', ?, ?)
            ON CONFLICT(tenant_id, agent_id)
            DO UPDATE SET status = 'idle', last_heartbeat = ?
        """,
            (self.tenant_id, agent_id, agent_type, time.time(), time.time(), time.time()),
        )

    async def heartbeat(
        self,
        agent_id: str,
        status: str = "active",
        current_task: str = "",
        current_files: list[str] = None,
    ):
        """心跳更新"""
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            """
            UPDATE agent_registry
            SET status = ?, current_task = ?, current_files = ?, last_heartbeat = ?
            WHERE tenant_id = ? AND agent_id = ?
        """,
            (
                status,
                current_task,
                json.dumps(current_files or []),
                time.time(),
                self.tenant_id,
                agent_id,
            ),
        )

    async def get_active_agents(self) -> list[dict]:
        """获取活跃Agent列表"""
        await self._ensure_table()
        if not self.db:
            return []
        cutoff = time.time() - 300  # 5分钟内心跳
        rows = await self.db.fetch(
            """
            SELECT agent_id, agent_type, status, current_task, current_files, last_heartbeat
            FROM agent_registry
            WHERE tenant_id = ? AND last_heartbeat > ?
            ORDER BY last_heartbeat DESC
        """,
            (self.tenant_id, cutoff),
        )
        return [dict(row) for row in rows]

    async def check_file_conflict(self, agent_id: str, target_files: list[str]) -> list[dict]:
        """检查文件冲突：其他Agent是否正在修改这些文件"""
        await self._ensure_table()
        if not self.db:
            return []
        active = await self.get_active_agents()
        conflicts = []
        for a in active:
            if a["agent_id"] == agent_id:
                continue
            try:
                their_files = json.loads(a.get("current_files", "[]"))
            except (json.JSONDecodeError, TypeError):
                their_files = []
            overlap = set(target_files) & set(their_files)
            if overlap:
                conflicts.append(
                    {
                        "agent_id": a["agent_id"],
                        "conflicting_files": list(overlap),
                        "their_task": a.get("current_task", ""),
                    }
                )
        return conflicts

    async def send_message(self, from_agent: str, to_agent: str, message_type: str, content: str):
        """Agent间发消息"""
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            "INSERT INTO agent_coord_messages (tenant_id, from_agent, to_agent, message_type, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self.tenant_id, from_agent, to_agent, message_type, content, time.time()),
        )

    async def get_messages(self, agent_id: str, unread_only: bool = True) -> list[dict]:
        """获取消息"""
        await self._ensure_table()
        if not self.db:
            return []
        condition = "AND read = 0" if unread_only else ""
        rows = await self.db.fetch(
            f"SELECT * FROM agent_coord_messages WHERE tenant_id = ? AND to_agent = ? {condition} ORDER BY created_at DESC LIMIT 20",
            (self.tenant_id, agent_id),
        )
        return [dict(row) for row in rows]

    async def get_stats(self) -> dict:
        await self._ensure_table()
        if not self.db:
            return {"agents": 0}
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM agent_registry WHERE tenant_id = ?",
            (self.tenant_id,),
        )
        return {"agents": count}
