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

from src.config import settings

logger = logging.getLogger(__name__)

# 状态心跳 UPDATE SQL 单一源（async heartbeat 与同步 teardown 收尾共用禁手抄漂移，
# F4b-㉗ 纪律）。
# G1-⑤b 声明保留语义：current_task/current_files 用 COALESCE(?, 列)——绑定
# NULL=保留现值（看门狗/连接 keep-alive 节拍不再把已声明工作集洗成空，修前
# 每次心跳恒写 ''/'[]'），显式绑定值（含 ''/'[]'）=更新/清空声明（offline
# 收尾绑 ''/'[]' 清工作集）。async 心跳与同步收尾同一 SQL 照旧单一源。
_HEARTBEAT_SQL = """UPDATE agent_registry
            SET status = ?, current_task = COALESCE(?, current_task), current_files = COALESCE(?, current_files), last_heartbeat = ?
            WHERE tenant_id = ? AND agent_id = ?"""

# agent_registry DDL 单一源（_ensure_table 与同步收尾共用——表不在场时同步写
# 幂等补建，防 "no such table" 把收尾标 offline 变假成功）。
_AGENT_REGISTRY_DDL = """
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
            """


def db_url_for_pool(pool) -> str:
    """协调行真源文件路径单一解析（与 SQLitePool.connect 同一语义：显式 db_path
    优先、缺省 settings.database_url；同步收尾走同一文件禁第二真源）。"""
    raw = getattr(pool, "_db_path", None) or settings.database_url
    return raw.replace("sqlite:///", "").replace("sqlite://", "")


def heartbeat_sync(db_url: str, tenant_id: str, agent_id: str, status: str = "active") -> None:
    """同步状态写（teardown 取消面专用，G1-⑤）：DEC-⑱ 纪律「取消兑现于 finally
    首个 await」——连接收尾路径的 await 型写会被取消连同收尾一起吞掉（实测丢
    offline 标记），故断开标 offline 走 sqlite3 直写同一行同一 SQL（_HEARTBEAT_SQL
    单一源）。旁路纪律：故障 logger.warning 可见，绝不吃掉收尾主路径。"""
    import sqlite3

    conn = sqlite3.connect(db_url)
    try:
        conn.execute(_AGENT_REGISTRY_DDL)
        conn.execute(_HEARTBEAT_SQL, (status, "", "[]", time.time(), tenant_id, agent_id))
        conn.commit()
    finally:
        conn.close()


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
            await self.db.execute(_AGENT_REGISTRY_DDL)
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
            DO UPDATE SET agent_type = excluded.agent_type, status = 'idle', last_heartbeat = ?
        """,
            (self.tenant_id, agent_id, agent_type, time.time(), time.time(), time.time()),
        )

    async def heartbeat(
        self,
        agent_id: str,
        status: str = "active",
        current_task: str | None = None,
        current_files: list[str] | None = None,
    ):
        """心跳更新。

        G1-⑤b 声明语义收口：current_task/current_files 缺省 None=保留现值
        （_HEARTBEAT_SQL COALESCE——keep-alive 节拍不再洗掉已声明工作集），
        显式传值（含空列表）=更新声明。"""
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            _HEARTBEAT_SQL,
            (
                status,
                current_task,
                json.dumps(current_files) if current_files is not None else None,
                time.time(),
                self.tenant_id,
                agent_id,
            ),
        )

    async def unregister_agent(self, agent_id: str):
        """注销Agent（G1-⑤c）：真源行删除→派生协调行随之删除。

        管理面删除端点（api/agent.py DELETE /nodes/{id}）是首个生产消费点：
        派生观测面跟随真源——真源行没了，协调行留着就是幽灵行（5min 活跃窗内
        继续出现在协作视图/冲突检查里）。与 heartbeat 的 UPDATE-only 纪律互补：
        注销是显式删除动作（真源行确实被删了才走到这里），不是伪造。

        协调面只删自己命名空间的行（tenant 键过滤），他租户同名 agent_id
        的行一律不动（F3b-③b-3「禁跨租户破坏」纪律）。"""
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            "DELETE FROM agent_registry WHERE tenant_id = ? AND agent_id = ?",
            (self.tenant_id, agent_id),
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
                        # NULL/缺失统一 ""（register 未声明时列为 NULL，禁 None 混形）
                        "their_task": a.get("current_task") or "",
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

    async def mark_messages_read(self, agent_id: str) -> None:
        """G1-⑤b：协调收件箱送达标记（inbox 拉取/连接接取后清 unread——
        get_messages 的 unread_only 语义由此闭环，修前 read 列恒 0 零消费）。"""
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            "UPDATE agent_coord_messages SET read = 1 WHERE tenant_id = ? AND to_agent = ? AND read = 0",
            (self.tenant_id, agent_id),
        )

    async def get_stats(self) -> dict:
        await self._ensure_table()
        if not self.db:
            return {"agents": 0}
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM agent_registry WHERE tenant_id = ?",
            (self.tenant_id,),
        )
        return {"agents": count}
