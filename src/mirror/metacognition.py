"""Metacognition — 元认知：思考自己的思考。

Agent能：
- 审视自己的决策过程（为什么选了这个策略？）
- 评估自己的信心程度（我对这个答案有多确定？）
- 识别自己的盲区（我不擅长什么？）
- 调整自己的策略（上次失败了，换个方法试试）
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


def derive_agent_confidence(stats: dict, prior: float = 0.8, prior_strength: float = 2.0) -> float:
    """agent_confidence 生产源派生（F4b-㉕）：从元认知账本真源派生未声明信心。

    真源=本 agent 的成败观测（verify 落账的 outcome 行），非字面推断非语义理解：
    - 无成败观测 → prior 0.8"信心充足"（F4b-㉒ 缺省行为契约零变化）
    - 有观测 → Laplace 经验收缩 (successes + prior_strength*prior) / (outcomes +
      prior_strength)——样本越多越信观测成功率（如实标注：经验收缩非统计推断，
      prior/prior_strength 是人为选定的先验与强度）；连续失败把信心压到 0.5 以下
      → F4b-㉒ 低信心收紧在生产真源上可达（修前调用方恒不声明=恒 0.8 恒不可达）。
    自评信心（decision 行）刻意不进派生：信心派生自评=自我循环（信心生信心）；
    自评只进校准对比（get_confidence_calibration 预测信心 vs 实际成功率）。
    min/max 是 0~1 契约归一（stats 来自 SQL 聚合 successes<=outcomes 结构上成立）。
    """
    outcomes = int(stats.get("outcomes") or 0)
    successes = int(stats.get("successes") or 0)
    if outcomes <= 0:
        return prior
    derived = (successes + prior_strength * prior) / (outcomes + prior_strength)
    return round(min(max(derived, 0.0), 1.0), 3)


class Metacognition:
    """元认知引擎 — 自我审视、自我纠错"""

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
                CREATE TABLE IF NOT EXISTS metacognition_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    decision TEXT,
                    reasoning TEXT,
                    confidence REAL,
                    outcome TEXT,
                    lesson TEXT,
                    created_at REAL NOT NULL
                )
            """)
        self._initialized = True

    async def log_decision(self, decision: str, reasoning: str, confidence: float):
        """记录一个决策及其推理过程"""
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            """INSERT INTO metacognition_log
               (tenant_id, agent_id, event_type, decision, reasoning, confidence, created_at)
               VALUES (?, ?, 'decision', ?, ?, ?, ?)""",
            (
                self.tenant_id,
                self.agent_id,
                decision[:500],
                reasoning[:1000],
                confidence,
                time.time(),
            ),
        )

    async def log_outcome(self, decision: str, outcome: str, lesson: str = ""):
        """记录决策的结果和教训"""
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            """INSERT INTO metacognition_log
               (tenant_id, agent_id, event_type, decision, outcome, lesson, created_at)
               VALUES (?, ?, 'outcome', ?, ?, ?, ?)""",
            (self.tenant_id, self.agent_id, decision[:500], outcome, lesson[:500], time.time()),
        )

    async def reflect_recent(self, limit: int = 10) -> list[dict]:
        """审视最近的决策"""
        await self._ensure_table()
        if not self.db:
            return []
        rows = await self.db.fetch(
            """SELECT event_type, decision, reasoning, confidence, outcome, lesson, created_at
               FROM metacognition_log
               WHERE tenant_id = ? AND agent_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (self.tenant_id, self.agent_id, limit),
        )
        return [dict(row) for row in rows]

    async def get_blind_spots(self) -> list[dict]:
        """识别盲区：经常失败的领域"""
        await self._ensure_table()
        if not self.db:
            return []
        rows = await self.db.fetch(
            """SELECT decision, COUNT(*) as fail_count
               FROM metacognition_log
               WHERE tenant_id = ? AND agent_id = ? AND event_type = 'outcome' AND outcome LIKE '%fail%'
               GROUP BY decision
               HAVING fail_count >= 2
               ORDER BY fail_count DESC
               LIMIT 5""",
            (self.tenant_id, self.agent_id),
        )
        return [{"area": row["decision"], "fail_count": row["fail_count"]} for row in rows]

    async def get_confidence_calibration(self) -> dict:
        """校准信心：预测信心 vs 实际成功率"""
        await self._ensure_table()
        if not self.db:
            return {"avg_confidence": 0, "calibration": "no_data"}
        rows = await self.db.fetch(
            """SELECT confidence, outcome
               FROM metacognition_log
               WHERE tenant_id = ? AND agent_id = ? AND confidence IS NOT NULL
               ORDER BY created_at DESC LIMIT 50""",
            (self.tenant_id, self.agent_id),
        )
        if not rows:
            return {"avg_confidence": 0, "calibration": "no_data"}

        confidences = [r["confidence"] for r in rows]
        avg_conf = sum(confidences) / len(confidences)

        # 简单校准：高信心决策的成功率
        high_conf = [r for r in rows if r["confidence"] and r["confidence"] > 0.7]
        high_conf_success = sum(
            1 for r in high_conf if r["outcome"] and "success" in str(r["outcome"])
        )
        success_rate = high_conf_success / len(high_conf) if high_conf else 0

        return {
            "avg_confidence": round(avg_conf, 2),
            "high_confidence_count": len(high_conf),
            "high_confidence_success_rate": round(success_rate, 2),
            "calibration": "good" if abs(avg_conf - success_rate) < 0.2 else "miscalibrated",
        }

    async def confidence_stats(self) -> dict:
        """agent_confidence 生产源真源统计（F4b-㉕）。

        账本两轴：decision 行=think 落账的自评信心（confidence 列）、outcome 行=
        verify 落账的实际成败（outcome='success'/'failure'）——派生只消费成败轴
        （derive_agent_confidence docstring），自评轴供校准对比。
        """
        await self._ensure_table()
        empty = {"decisions": 0, "outcomes": 0, "successes": 0, "avg_confidence": None}
        if not self.db:
            return empty
        row = await self.db.fetchrow(
            """SELECT
                 SUM(CASE WHEN event_type = 'decision' THEN 1 ELSE 0 END) AS decisions,
                 SUM(CASE WHEN event_type = 'outcome' THEN 1 ELSE 0 END) AS outcomes,
                 SUM(CASE WHEN event_type = 'outcome' AND outcome = 'success' THEN 1 ELSE 0 END) AS successes,
                 AVG(CASE WHEN event_type = 'decision' THEN confidence END) AS avg_confidence
               FROM metacognition_log
               WHERE tenant_id = ? AND agent_id = ?""",
            (self.tenant_id, self.agent_id),
        )
        if row is None:
            return empty
        avg_conf = row["avg_confidence"]
        return {
            "decisions": int(row["decisions"] or 0),
            "outcomes": int(row["outcomes"] or 0),
            "successes": int(row["successes"] or 0),
            "avg_confidence": round(float(avg_conf), 3) if avg_conf is not None else None,
        }

    async def get_stats(self) -> dict:
        await self._ensure_table()
        if not self.db:
            return {"decisions": 0}
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM metacognition_log WHERE tenant_id = ? AND agent_id = ?",
            (self.tenant_id, self.agent_id),
        )
        return {"decisions": count}
