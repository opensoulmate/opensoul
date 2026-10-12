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


def compute_confidence_calibration(
    decision_rows: list[dict],
    outcome_rows: list[dict],
    high_threshold: float = 0.7,
    tolerance: float = 0.2,
) -> dict:
    """F4b-㉖ 配对校准纯函数：把决策行（预测信心）与成败行（实际结果）按 ref 配成
    校准观测，计算"预测信心 vs 实际成功率"。

    配对语义（因果如实，禁虚构配对）：
    - 每条成败行配「同 ref 且 (created_at, id) 不晚于它」的最近一条决策行——
      结果归属于产生它之前最近的决策（多轮 think/verify 各自配对不乱串）
    - 一决策可配多成败（同决策下多次 verify=同一预测的多次观测，如实各计）
    - 空 ref（无配对键）/无前置决策的成败行 → 不配对如实计数，绝不硬凑
    - 成败精确串 'success' 才计成功（F4b-㉕ 禁 '%success%' 子串误计纪律延续），
      其他 outcome 串只进分母
    返回契约：既有键（avg_confidence=配对观测的平均预测信心、high_confidence_count/
    high_confidence_success_rate=高信心配对观测的计数/成功率（修前恒 0）、
    calibration）+加性键（actual_success_rate、paired、unpaired_decisions、
    unpaired_outcomes）。配不出任何对=calibration 'no_data'（校准无从谈起，
    禁拿无配对数据硬算）。
    """

    def _key(row: dict) -> tuple[float, int]:
        return (float(row.get("created_at") or 0), int(row.get("id") or 0))

    def _ref(row: dict) -> str:
        return str(row.get("ref") or "").strip()

    decs = sorted(decision_rows, key=_key)
    outs = sorted(outcome_rows, key=_key)
    pairs: list[tuple[dict, dict]] = []
    used_decision_ids: set[tuple[float, int]] = set()
    unpaired_outcomes = 0
    for o in outs:
        if not _ref(o):
            unpaired_outcomes += 1
            continue
        match = None
        for d in decs:
            if _ref(d) != _ref(o):
                continue
            if _key(d) <= _key(o):
                match = d
            else:
                break  # decs 已按时间升序，后续只会更晚
        if match is None:
            unpaired_outcomes += 1
            continue
        used_decision_ids.add(_key(match))
        pairs.append((match, o))

    result = {
        "avg_confidence": 0,
        "high_confidence_count": 0,
        "high_confidence_success_rate": 0,
        "calibration": "no_data",
        "actual_success_rate": 0,
        "paired": len(pairs),
        "unpaired_decisions": len(decs) - len(used_decision_ids),
        "unpaired_outcomes": unpaired_outcomes,
    }
    if not pairs:
        return result

    confidences = [float(d["confidence"]) for d, _ in pairs]
    avg_conf = sum(confidences) / len(confidences)
    successes = sum(1 for _, o in pairs if (o.get("outcome") or "") == "success")
    success_rate = successes / len(pairs)
    high = [(d, o) for d, o in pairs if float(d["confidence"]) > high_threshold]
    high_success = sum(1 for _, o in high if (o.get("outcome") or "") == "success")
    result.update(
        {
            "avg_confidence": round(avg_conf, 2),
            "high_confidence_count": len(high),
            "high_confidence_success_rate": round(high_success / len(high), 2) if high else 0,
            "calibration": "good" if abs(avg_conf - success_rate) < tolerance else "miscalibrated",
            "actual_success_rate": round(success_rate, 2),
        }
    )
    return result


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
                    ref TEXT,
                    created_at REAL NOT NULL
                )
            """)
            # F4b-㉖ schema 自愈（C1 幂等模式）：F4b-㉕ 前形态的老库缺 ref 配对键列，
            # CREATE TABLE IF NOT EXISTS 不会给已存在表补列 → 幂等补列。
            cols = await self.db.fetch("PRAGMA table_info(metacognition_log)")
            if cols and "ref" not in {row["name"] for row in cols}:
                await self.db.execute("ALTER TABLE metacognition_log ADD COLUMN ref TEXT")
        self._initialized = True

    async def log_decision(self, decision: str, reasoning: str, confidence: float, ref: str = ""):
        """记录一个决策及其推理过程。

        ref=配对键（F4b-㉖）：think.session_id ↔ verify.task_id 同键双写点
        （ai_groups 同一 task_id）→ get_confidence_calibration 按 ref 把决策行的
        预测信心与成败行的实际结果配成校准观测；空 ref=无配对键，如实不配对。
        """
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            """INSERT INTO metacognition_log
               (tenant_id, agent_id, event_type, decision, reasoning, confidence, ref, created_at)
               VALUES (?, ?, 'decision', ?, ?, ?, ?, ?)""",
            (
                self.tenant_id,
                self.agent_id,
                decision[:500],
                reasoning[:1000],
                confidence,
                ref or None,
                time.time(),
            ),
        )

    async def log_outcome(self, decision: str, outcome: str, lesson: str = "", ref: str = ""):
        """记录决策的结果和教训。ref=配对键（F4b-㉖，见 log_decision docstring）。"""
        await self._ensure_table()
        if not self.db:
            return
        await self.db.execute(
            """INSERT INTO metacognition_log
               (tenant_id, agent_id, event_type, decision, outcome, lesson, ref, created_at)
               VALUES (?, ?, 'outcome', ?, ?, ?, ?, ?)""",
            (
                self.tenant_id,
                self.agent_id,
                decision[:500],
                outcome,
                lesson[:500],
                ref or None,
                time.time(),
            ),
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
        """校准信心：预测信心 vs 实际成功率（F4b-㉖ 配对缺陷收口）。

        修前结构缺陷（F4b-㉕ 留档）：decision 行（confidence 有值/outcome NULL）与
        outcome 行（outcome 有值/confidence NULL）两行无配对键，而旧实现按单行同时读
        confidence+outcome（WHERE confidence IS NOT NULL 选出的行 outcome 恒 NULL）
        → high_confidence_success_rate 恒 0 → calibration 恒 'miscalibrated'。
        现按 ref 配对键把两轴配成校准观测（compute_confidence_calibration 纯函数）。
        窗口如实：最近各 50 行内配对，窗口外不虚构；配不出对如实报 no_data+计数。
        """
        await self._ensure_table()
        if not self.db:
            return compute_confidence_calibration([], [])
        dec_rows = await self.db.fetch(
            """SELECT id, ref, confidence, created_at
               FROM metacognition_log
               WHERE tenant_id = ? AND agent_id = ? AND event_type = 'decision'
                 AND confidence IS NOT NULL
               ORDER BY created_at DESC, id DESC LIMIT 50""",
            (self.tenant_id, self.agent_id),
        )
        out_rows = await self.db.fetch(
            """SELECT id, ref, outcome, created_at
               FROM metacognition_log
               WHERE tenant_id = ? AND agent_id = ? AND event_type = 'outcome'
               ORDER BY created_at DESC, id DESC LIMIT 50""",
            (self.tenant_id, self.agent_id),
        )
        return compute_confidence_calibration(
            [dict(row) for row in dec_rows], [dict(row) for row in out_rows]
        )

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
