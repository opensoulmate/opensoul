"""Metacognition — 元认知：思考自己的思考。

Agent能：
- 审视自己的决策过程（为什么选了这个策略？）
- 评估自己的信心程度（我对这个答案有多确定？）
- 识别自己的盲区（我不擅长什么？）
- 调整自己的策略（上次失败了，换个方法试试）
"""

import json
import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 成败轴规范串单一源（F4b-㉗）：写点（brain verify）与全部读面分类（
# compute_confidence_calibration/confidence_stats/get_blind_spots）共用，
# 禁各处字面抄写漂移。判法=精确串（F4b-㉕/㉖ 纪律：禁 LIKE 子串误计）。
SUCCESS_OUTCOME = "success"
FAILURE_OUTCOME = "failure"


def calibration_prior_correction(calibration: dict | None) -> float:
    """G1-④c 校准→先验纠偏量：预测偏差的实际方向量（actual_success_rate − avg_confidence）。

    只认 compute_confidence_calibration 真源形状（声明从实现派生，F4b-㉗ 同纪律）：
    - calibration == 'miscalibrated' 且 avg_confidence/actual_success_rate 双数值
      在场 → 返回有符号纠偏量（负=预测偏高应向下纠、正=预测偏低应向上纠）
    - 'good'/'no_data' → 0.0（无失准无纠偏可言）、缺键/值非数值（含 bool）→ 0.0
      如实不猜数字（G1-④a 字段缺失如实略纪律）。
    纠偏量锚定实际成功率（outcome 轴）——是"预测误差校正"非"自评生自评"自我循环
    （F4b-㉕ 纪律保留：自评轴仍不直接进派生，只以"预测−实际"的误差形式进）。
    返回值 ∈ [−1, 1]（两输入皆 ∈ [0, 1]）。
    """
    if not isinstance(calibration, dict) or calibration.get("calibration") != "miscalibrated":
        return 0.0
    avg = calibration.get("avg_confidence")
    actual = calibration.get("actual_success_rate")
    # bool 拒收（True/False 是 int 子类会静默当 1/0，G1-④a _numeric 同语义）；
    # isinstance 窄化后数值运算（Pyright 类型面收口）。
    if isinstance(avg, bool) or isinstance(actual, bool):
        return 0.0
    if not isinstance(avg, (int, float)) or not isinstance(actual, (int, float)):
        return 0.0
    return round(float(actual) - float(avg), 3)


def derive_agent_confidence(
    stats: dict,
    prior: float = 0.8,
    prior_strength: float = 2.0,
    calibration: dict | None = None,
) -> float:
    """agent_confidence 生产源派生（F4b-㉕ + G1-④c 校准纠偏）：从元认知账本真源派生未声明信心。

    真源=本 agent 的成败观测（verify 落账的 outcome 行），非字面推断非语义理解：
    - 无成败观测 → prior "信心充足"（F4b-㉒ 缺省行为契约零变化）
    - 有观测 → Laplace 经验收缩 (successes + prior_strength*prior) / (outcomes +
      prior_strength)——样本越多越信观测成功率（如实标注：经验收缩非统计推断，
      prior/prior_strength 是人为选定的先验与强度）；连续失败把信心压到 0.5 以下
      → F4b-㉒ 低信心收紧在生产真源上可达（修前调用方恒不声明=恒 0.8 恒不可达）。
    自评信心（decision 行）刻意不进派生：信心派生自评=自我循环（信心生信心）；
    自评只进校准对比（get_confidence_calibration 预测信心 vs 实际成功率）。
    min/max 是 0~1 契约归一（stats 来自 SQL 聚合 successes<=outcomes 结构上成立）。

    G1-④c 校准纠偏（calibration 参数）：get_confidence_calibration 的失准观测
    （'miscalibrated'）按预测误差方向修正 prior——修前派生的 prior 固定 0.8
    "信心充足"对本 agent 可能系统性偏高/偏低（校准真源实测预测 vs 实际的偏差），
    失准即先验错，纠先验=根因修。选 prior 修而非输出修的理由（如实记）：
    ①高样本区 outcome 项主导、派生收敛到实际成功率（构造上已校准），失准来源
    就是固定 prior——只纠 prior 不碰 outcome 项=不与已锚定实际结果的轴重复计数
    ②纠偏量经 k/(n+k) 衰减，高样本区自然淡出防过校正③纠偏量锚定实际成功率
    （outcome 轴）非自评均值本身，是误差校正不是自评生自评。
    calibration 缺省/None/'good'/'no_data' → 纠偏量 0.0，输出与 F4b-㉕ 字节一致
    （既有契约零变化，回归测试锁死）。
    """
    correction = calibration_prior_correction(calibration)
    effective_prior = min(max(prior + correction, 0.0), 1.0)
    outcomes = int(stats.get("outcomes") or 0)
    successes = int(stats.get("successes") or 0)
    if outcomes <= 0:
        return effective_prior
    derived = (successes + prior_strength * effective_prior) / (outcomes + prior_strength)
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
    successes = sum(1 for _, o in pairs if (o.get("outcome") or "") == SUCCESS_OUTCOME)
    success_rate = successes / len(pairs)
    high = [(d, o) for d, o in pairs if float(d["confidence"]) > high_threshold]
    high_success = sum(1 for _, o in high if (o.get("outcome") or "") == SUCCESS_OUTCOME)
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
        """识别盲区：经常失败的领域（F4b-㉗ 子串匹配收口）。

        修前 outcome LIKE '%fail%' 子串匹配与家族精确串纪律（F4b-㉕/㉖：
        'success'/'failure' 精确串、禁子串误计）相悖，三类漂移：①子串假阳——
        'failproof'/'no-fail'/'fail-safe' 等含 'fail' 的非失败串被误计失败
        ②LIKE 大小写不敏感（'FAILURE' 被计）与 'success' 轴 BINARY 精确不对称
        ③同一 outcome 轴三种判法（本函数子串 vs 其余两面精确串）。
        现按成败轴单一源常量 FAILURE_OUTCOME 精确串计失败（声明从实现派生：
        与写点 brain verify 同一真源常量）。
        诚实边界：非规范串（'failed'/'fail'/'error'/'timeout'/'FAILURE' 等）
        如实不计失败（宁窄勿滥，F4b-㉔ 闭集精确匹配纪律；生产写点只写
        'success'/'failure' 两规范串，生产数据分类语义零变化）。
        """
        await self._ensure_table()
        if not self.db:
            return []
        rows = await self.db.fetch(
            """SELECT decision, COUNT(*) as fail_count
               FROM metacognition_log
               WHERE tenant_id = ? AND agent_id = ? AND event_type = 'outcome' AND outcome = ?
               GROUP BY decision
               HAVING fail_count >= 2
               ORDER BY fail_count DESC
               LIMIT 5""",
            (self.tenant_id, self.agent_id, FAILURE_OUTCOME),
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
                 SUM(CASE WHEN event_type = 'outcome' AND outcome = ? THEN 1 ELSE 0 END) AS successes,
                 AVG(CASE WHEN event_type = 'decision' THEN confidence END) AS avg_confidence
               FROM metacognition_log
               WHERE tenant_id = ? AND agent_id = ?""",
            (SUCCESS_OUTCOME, self.tenant_id, self.agent_id),
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


# ── G1-④a 元认知纠错回流（blind_spots/calibration → 执行 prompt 纠偏块） ──
# G0 定性③：reflect/blind_spots/calibration 仅观测口展示（/api/brain/metacognition）
# 不驱动任何纠偏动作。本节把观测变成纠偏动作的第一条闭环：反复失败的决策领域
# + 信心校准偏差 → 执行 prompt 纠偏提醒块（软建议，自带免责不覆盖任务本体），
# 消费面=ai_groups 任务执行（G1-④a；chat/ws_chat 面留后续拍）。

_METACOGNITIVE_AREA_MAX_CHARS = 200


def _metacognitive_advisory_lines(blind_spots, calibration, top_k: int = 3) -> list[str]:
    """纠偏条目纯函数（渲染/计数单一源，禁两套判法）。

    字段缺失如实略禁硬造（G1-③b-2 alternatives_payload 同纪律）：blind spot 行
    非 dict/缺 area/fail_count 不可渲染即跳过；calibration 仅 'miscalibrated' 出
    校准行（good/no_data 如实无纠偏可言），缺 avg_confidence/actual_success_rate
    或值非数值如实略（不猜数字）。top_k 只限盲区行（校准行是全局观测不占名额）。
    """
    lines = []
    rendered = 0
    for item in blind_spots if isinstance(blind_spots, list) else []:
        if rendered >= top_k:
            break
        if not isinstance(item, dict):
            continue
        area = re.sub(r"\s+", " ", str(item.get("area") or "")).strip()
        if not area:
            continue
        try:
            fail_count = int(item.get("fail_count") or 0)
        except (TypeError, ValueError):
            continue
        if fail_count <= 0:
            continue
        if len(area) > _METACOGNITIVE_AREA_MAX_CHARS:
            area = area[:_METACOGNITIVE_AREA_MAX_CHARS] + "…"
        lines.append(
            f"决策领域「{area}」历史上反复失败 {fail_count} 次——"
            "执行同类动作时换方法或加强前置检查与结果验证"
        )
        rendered += 1

    calib = calibration if isinstance(calibration, dict) else {}
    if calib.get("calibration") == "miscalibrated":
        avg_conf = calib.get("avg_confidence")
        actual = calib.get("actual_success_rate")

        def _numeric(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        if _numeric(avg_conf) and _numeric(actual):
            if float(avg_conf) > float(actual):
                direction, advice = "偏高", "执行时加强结果验证，勿轻信自我判断"
            else:
                direction, advice = "偏低", "按实际成功率大胆推进，避免过度保守"
            lines.append(
                f"自评信心{direction}（平均自评 {float(avg_conf):.2f} vs 实际成功率 "
                f"{float(actual):.2f}）——{advice}"
            )
    return lines


def format_metacognitive_advisory_for_prompt(blind_spots, calibration, top_k: int = 3) -> str:
    """元认知观测 → 执行 prompt 纠偏块（纯函数；无可用观测返回空串=零注入）。

    软建议块语义沿用 F2b-② format_advisory_for_prompt / DEC-③-① episodic 块：
    免责块头（仅供参考、与任务要求冲突时以任务要求为准）+软上下文不覆盖任务本体。
    """
    lines = _metacognitive_advisory_lines(blind_spots, calibration, top_k=top_k)
    if not lines:
        return ""
    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))
    return (
        "【元认知纠偏提醒】来自本 agent 历史执行的元认知观测（反复失败领域/信心校准），"
        "仅供参考；与任务要求冲突时以任务要求为准：\n" + numbered
    )


async def metacognitive_advisory_for(
    agent_id: str, db_pool=None, tenant_id: str = "default", top_k: int = 3
) -> tuple[str, int]:
    """G1-④a 元认知纠错回流通用取数口：blind_spots+calibration → 纠偏块。

    与 learned_advisory_for（F2b-②b）/episodic_advisory_for（DEC-③-①）同款
    纪律：异常如实抛禁静默吞（降级可见性归消费方）。读真源=本模块 Metacognition
    账本（F4b-㉕/㉖/㉗ 同一真源：think log_decision/verify log_outcome 双写点），
    口径=写点同键 (tenant_id, agent_id)——"学习产出在哪写就去哪读"（exec 路径
    进程内直调 brain think/verify 不带 authorization=匿名 'default'，见
    ai_groups._metacognitive_advisory_for 接缝注释）。返回 (纠偏块, 条数)，
    无可用观测=("", 0)。
    """
    if db_pool is None:
        from src.database.postgres import db_pool as _db_pool

        db_pool = _db_pool
    meta = Metacognition(db_pool, tenant_id, agent_id)
    blind = await meta.get_blind_spots()
    calibration = await meta.get_confidence_calibration()
    lines = _metacognitive_advisory_lines(blind, calibration, top_k=top_k)
    if not lines:
        return "", 0
    return format_metacognitive_advisory_for_prompt(blind, calibration, top_k=top_k), len(lines)
