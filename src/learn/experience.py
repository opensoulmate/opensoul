"""Experience Memory — SQLite存储版本，多租户隔离。"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

from src.learn.long_term import LITERAL_OVERLAP_THRESHOLD, literal_overlap
from src.models.cognitive import Experience, Intent, TaskContext, Verification

logger = logging.getLogger(__name__)


class ExperienceMemory:
    """经验记忆：SQLite存储，多租户隔离，支持语义检索、老化衰减。"""

    def __init__(self, db_pool=None, tenant_id: str = "default", agent_id: str = "default"):
        self.db = db_pool
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self._initialized = False

    async def _ensure_table(self):
        if self._initialized:
            return
        if self.db:
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS experiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    intent_summary TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    error TEXT,
                    fix TEXT,
                    relevance_score REAL DEFAULT 1.0,
                    created_at REAL NOT NULL,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            await self.db.execute("""
                CREATE INDEX IF NOT EXISTS idx_exp_tenant_agent
                ON experiences(tenant_id, agent_id)
            """)
            await self.db.execute("""
                CREATE INDEX IF NOT EXISTS idx_exp_outcome
                ON experiences(tenant_id, agent_id, outcome)
            """)
        self._initialized = True

    async def record(
        self, action: str, result: dict, verification: Verification, task_ctx: TaskContext
    ):
        """记录一次行动结果"""
        await self._ensure_table()
        if not self.db:
            return

        outcome = "success" if verification.success else "failure"
        await self.db.execute(
            """INSERT INTO experiences
               (tenant_id, agent_id, action, intent_summary, outcome, error, fix, relevance_score, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.tenant_id,
                self.agent_id,
                action[:500],
                task_ctx.intent.goal if task_ctx.intent else "unknown",
                outcome,
                verification.error,
                verification.fix,
                1.0,
                time.time(),
            ),
        )
        # 老化淘汰
        await self._aging_prune()

    async def get_relevant_experience(self, intent: Intent) -> list[Experience]:
        """获取和当前意图相关的历史经验"""
        await self._ensure_table()
        if not self.db:
            return []

        # 按意图类型匹配 + 时间衰减
        rows = await self.db.fetch(
            """SELECT action, intent_summary, outcome, error, fix, created_at, relevance_score
               FROM experiences
               WHERE tenant_id = ? AND agent_id = ?
               ORDER BY created_at DESC
               LIMIT 100""",
            (self.tenant_id, self.agent_id),
        )

        experiences = []
        for row in rows:
            exp = Experience(
                action=row["action"],
                intent_summary=row["intent_summary"],
                outcome=row["outcome"],
                error=row["error"],
                fix=row["fix"],
                timestamp=datetime.fromtimestamp(row["created_at"]),
                relevance_score=row["relevance_score"],
            )
            score = self._compute_relevance(exp, intent)
            if score > 0.2:
                exp.relevance_score = score
                experiences.append(exp)

        experiences.sort(key=lambda e: e.relevance_score, reverse=True)
        return experiences[:10]

    async def get_similar_failures(self, intent: Intent) -> list[Experience]:
        """获取类似操作的失败经验。

        F4b-㉚ 修前=get_relevant_experience 再过滤 failure——其 100 行近期窗口
        把窗口外的失败行静默挡掉（生产实证：963 行里 22 条 failure 全在 24 天前，
        top-100 窗口内 failure=0，风险轴接线后仍恒空集=接线了≠跑通了）。本查询
        按 outcome 直查（idx_exp_outcome 真索引），失败经验不被近期窗口截断；
        相关度/衰减/门槛仍走 _compute_relevance 同一真源（强匹配老失败仍算数、
        弱相关随时间淡出，宁缺勿滥语义不变）。
        """
        await self._ensure_table()
        if not self.db:
            return []
        rows = await self.db.fetch(
            """SELECT action, intent_summary, outcome, error, fix, created_at, relevance_score
               FROM experiences
               WHERE tenant_id = ? AND agent_id = ? AND outcome = 'failure'
               ORDER BY created_at DESC
               LIMIT 100""",
            [self.tenant_id, self.agent_id],
        )
        failures = []
        for row in rows:
            exp = Experience(
                action=row["action"],
                intent_summary=row["intent_summary"],
                outcome=row["outcome"],
                error=row["error"],
                fix=row["fix"],
                timestamp=datetime.fromtimestamp(row["created_at"]),
                relevance_score=row["relevance_score"],
            )
            score = self._compute_relevance(exp, intent)
            if score > 0.2:
                exp.relevance_score = score
                failures.append(exp)
        failures.sort(key=lambda e: e.relevance_score, reverse=True)
        return failures

    def _compute_relevance(self, exp: Experience, intent: Intent) -> float:
        """相关度（0~1）：匹配分 × 时间衰减 + 失败补贴。

        F4b-㉚ 写读语义对齐（接线前定形——修前本类全仓零实例、无任何消费方）：
        写方（ExperienceCollector 生产实证 963 行）intent_summary=任务原文自由
        文本、action=chat_turn:*/job 动作串，goal 枚举精确轴对生产数据恒 0 命中
        ——补字面相关度轴（literal_overlap 单一源，达 LITERAL_OVERLAP_THRESHOLD
        才算相关：宁缺勿滥，无关恒 0 分）；文件轴同时查 intent_summary（生产
        action 常是不透明 ID，文件路径在任务原文里）。失败补贴在衰减之后（失败
        教训保留权重：强匹配的老失败仍过 get_relevant_experience 的门槛，弱相关
        随时间淡出）。相关度是字面匹配非语义 embedding（如实标注）。
        """
        match = 0.0
        if exp.intent_summary == intent.goal:
            match += 0.5
        for f in intent.target_files:
            if f in exp.action or f in exp.intent_summary:
                match += 0.3
                break
        overlap = literal_overlap(intent.user_prompt, exp.intent_summary or exp.action)
        if overlap >= LITERAL_OVERLAP_THRESHOLD:
            match += overlap
        if match <= 0.0:
            return 0.0
        age_hours = (datetime.now() - exp.timestamp).total_seconds() / 3600
        time_decay = max(0.1, 1.0 - age_hours / (24 * 30))
        score = match * time_decay
        if exp.outcome == "failure":
            score += 0.15
        return min(1.0, score)

    async def _aging_prune(self):
        """老化淘汰：移除90天前的经验"""
        if not self.db:
            return
        cutoff = time.time() - 90 * 86400
        await self.db.execute(
            "DELETE FROM experiences WHERE tenant_id = ? AND agent_id = ? AND created_at < ?",
            (self.tenant_id, self.agent_id, cutoff),
        )

    async def get_stats(self) -> dict:
        await self._ensure_table()
        if not self.db:
            return {"total": 0, "success_count": 0, "failure_count": 0}

        total = await self.db.fetchval(
            "SELECT COUNT(*) FROM experiences WHERE tenant_id = ? AND agent_id = ?",
            (self.tenant_id, self.agent_id),
        )
        success = await self.db.fetchval(
            "SELECT COUNT(*) FROM experiences WHERE tenant_id = ? AND agent_id = ? AND outcome = 'success'",
            (self.tenant_id, self.agent_id),
        )
        return {
            "total": total,
            "success_count": success,
            "failure_count": total - success,
        }
