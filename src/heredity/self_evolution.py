"""Self-Evolution — 自我进化引擎。

Agent自动优化自己的策略：
- 分析成功/失败模式 → 调整prompt策略
- 识别低效行为 → 替换为更优方案
- 从用户反馈中学习 → 调整交互风格
- 定期自评 → 发现改进空间
"""

import json
import logging
import re
import time
from typing import Optional

from src.learn.long_term import classify_error_type, learned_failure_fixes_for

logger = logging.getLogger(__name__)

# 提案生命周期（2026-10-05轮，LobeChat declareSelfFeedbackIntent去重语义）：
# 同一发现不重复立案。生产实证：evolution_log曾积压190行同reason复读
# （同一对已修复bug各95行"高频失败: ...(5次)"）——此前analyze_and_evolve每次
# 运行都无条件INSERT。治理三件套：签名去重（无新证据不复读）+ 新鲜度窗口
# （陈旧已修复模式不再进入分析）+ 存量归档（compact_duplicate_logs）。
FRESH_PATTERN_WINDOW_S = 7 * 86400  # 失败模式活跃度窗口：last_seen超窗=不再提案

# 既有规则文案（无学习推荐时的回落，字面保留=契约零变化）：syntax/timeout
# 有专属规则，其余类别（permission/not_found/other）一律"增加前置检查"——
# 修前内联判断链语义原样收编（判断键=classify_error_type 单一源）。
_RULE_RECOMMENDATIONS = {
    "syntax_error": "执行前做语法检查",
    "timeout": "增加超时时间或分步执行",
}
_DEFAULT_RULE_RECOMMENDATION = "增加前置检查"


class SelfEvolution:
    """自我进化：自动优化Agent策略"""

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
                CREATE TABLE IF NOT EXISTS evolution_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    evolution_type TEXT NOT NULL,
                    before_state TEXT,
                    after_state TEXT,
                    reason TEXT,
                    impact_score REAL DEFAULT 0.0,
                    created_at REAL NOT NULL,
                    meta TEXT,
                    status TEXT DEFAULT 'active'
                )
            """)
            await self._migrate_columns()
        self._initialized = True

    async def _migrate_columns(self):
        """老库自愈：meta/status列PRAGMA探测+ALTER（schema_doctor同款幂等模式）。

        meta=结构化提案签名JSON（去重匹配+证据计数），status=active/superseded
        （重复立案归档不删行——历史可审计，mem0 §1.1）。
        """
        try:
            cols = {r["name"] for r in await self.db.fetch("PRAGMA table_info(evolution_log)")}
        except Exception as e:
            logger.warning(f"[self-evolution] 列探测失败(跳过迁移): {e}")
            return
        if "meta" not in cols:
            await self.db.execute("ALTER TABLE evolution_log ADD COLUMN meta TEXT")
        if "status" not in cols:
            await self.db.execute(
                "ALTER TABLE evolution_log ADD COLUMN status TEXT DEFAULT 'active'"
            )

    async def analyze_and_evolve(self) -> list[dict]:
        """分析当前状态，自动进化"""
        await self._ensure_table()
        if not self.db:
            return []
        # 存量重复立案归档（幂等，清账与增量防复发一体）
        await self.compact_duplicate_logs()

        evolutions = []

        # 1. 分析成功率趋势
        success_trend = await self._analyze_success_trend()
        if success_trend["declining"]:
            evolution = {
                "type": "strategy_adjustment",
                "reason": f"成功率下降: {success_trend['current']:.0%} vs {success_trend['previous']:.0%}",
                "action": "切换到更保守的策略",
            }
            evolution["signature"] = self._row_signature(
                {"evolution_type": evolution["type"], "reason": evolution["reason"], "meta": None}
            )
            if await self._last_proposal(evolution) is None:
                await self._log_evolution(evolution)
                evolutions.append(evolution)

        # 2. 分析高频失败模式（提案生命周期：无新证据不重复立案）
        failure_patterns = await self._analyze_failure_patterns()
        for pattern in failure_patterns:
            evolution = {
                "type": "failure_avoidance",
                "reason": f"高频失败: {pattern['error_type']} ({pattern['count']}次)",
                "action": f"自动规避: {pattern['recommendation']}",
                "meta": {
                    "pattern_key": pattern.get("pattern_key", ""),
                    "count": pattern["count"],
                    # F2b-②c-1 推荐回流可观测：建议来源/错误类别/学习读取故障真因
                    # （meta 加性键——_row_signature 不读 meta，去重语义零变化）
                    "error_class": pattern.get("error_class", ""),
                    "recommendation_source": pattern.get("recommendation_source", ""),
                    "learned_error": pattern.get("learned_error", ""),
                },
            }
            evolution["signature"] = self._row_signature(
                {"evolution_type": evolution["type"], "reason": evolution["reason"], "meta": None}
            )
            prior = await self._last_proposal(evolution)
            if prior is not None:
                # 有案在先：仅当立案后该签名又有新失败发生才再立新案（新证据驱动），
                # 否则同发现复读（190行同reason刷屏的根因）
                if not await self._new_failures_since(
                    pattern.get("pattern_key", ""), prior["created_at"]
                ):
                    continue
            await self._log_evolution(evolution)
            evolutions.append(evolution)

        # 3. 分析用户反馈
        feedback_trend = await self._analyze_feedback()
        if feedback_trend["negative_trend"]:
            evolution = {
                "type": "style_adjustment",
                "reason": f"用户满意度下降: {feedback_trend['avg_rating']:.1f}/5",
                "action": "调整交互风格",
            }
            evolution["signature"] = self._row_signature(
                {"evolution_type": evolution["type"], "reason": evolution["reason"], "meta": None}
            )
            if await self._last_proposal(evolution) is None:
                await self._log_evolution(evolution)
                evolutions.append(evolution)

        return evolutions

    async def _analyze_success_trend(self) -> dict:
        """分析成功率趋势"""
        recent = await self.db.fetch(
            """
            SELECT outcome, COUNT(*) as cnt FROM experiences
            WHERE tenant_id = ? AND agent_id = ?
            AND created_at > ?
            GROUP BY outcome
        """,
            (self.tenant_id, self.agent_id, time.time() - 7 * 86400),
        )

        previous = await self.db.fetch(
            """
            SELECT outcome, COUNT(*) as cnt FROM experiences
            WHERE tenant_id = ? AND agent_id = ?
            AND created_at BETWEEN ? AND ?
            GROUP BY outcome
        """,
            (self.tenant_id, self.agent_id, time.time() - 14 * 86400, time.time() - 7 * 86400),
        )

        def calc_rate(rows):
            total = sum(r["cnt"] for r in rows)
            success = sum(r["cnt"] for r in rows if r["outcome"] == "success")
            return success / total if total > 0 else 0.5

        current_rate = calc_rate(recent)
        previous_rate = calc_rate(previous)

        return {
            "current": current_rate,
            "previous": previous_rate,
            "declining": current_rate < previous_rate - 0.1,
        }

    async def _analyze_failure_patterns(self) -> list[dict]:
        """分析高频失败模式。

        20:36轮遗留#3修复：SQL的GROUP BY error是精确文本分组——运行时终态
        标记（[breaker_open]熔断前缀 / stale recovery动态(n/m)+job_id）会让
        同根因错误分裂成多个组，单组计数达不到阈值→高频失败漏报、进化提案
        碎片化。改SQL取原始分组后在Python侧按normalize_error_key合并计数
        （历史存量行无需数据迁移即正确聚合；采集侧对新行同样写归一化键），
        阈值cnt>=3作用于合并后计数。
        """
        from src.learn.experience_collector import normalize_error_key

        rows = await self.db.fetch(
            """
            SELECT error, COUNT(*) as cnt, MAX(created_at) as last_seen FROM experiences
            WHERE tenant_id = ? AND agent_id = ? AND outcome = 'failure'
            AND created_at > ? AND error IS NOT NULL
            GROUP BY error
            ORDER BY cnt DESC
        """,
            (self.tenant_id, self.agent_id, time.time() - 30 * 86400),
        )

        merged: dict[str, dict] = {}
        for row in rows:
            key = normalize_error_key(row["error"] or "")
            if not key:
                continue
            entry = merged.setdefault(key, {"count": 0, "last_seen": 0.0})
            entry["count"] += int(row["cnt"])
            entry["last_seen"] = max(entry["last_seen"], float(row["last_seen"] or 0))

        patterns = []
        cutoff = time.time() - FRESH_PATTERN_WINDOW_S
        for error, agg in sorted(merged.items(), key=lambda kv: -kv[1]["count"]):
            cnt = agg["count"]
            if cnt < 3:
                break  # 合并后按count降序，首个低于阈值后全部低于
            # 新鲜度门：陈旧模式（7天窗口内无新失败=已修复/已消失）不再提案——
            # 190行复读的两个bug都是3周前已修复的历史错误
            if agg["last_seen"] < cutoff:
                continue
            error_class = classify_error_type(error)
            advice = await self._recommendation_for(error, error_class)
            patterns.append(
                {
                    "error_type": error[:100],
                    "pattern_key": error,
                    "count": cnt,
                    "last_seen": agg["last_seen"],
                    "recommendation": advice["recommendation"],
                    # F2b-②c-1 推荐回流可观测：建议来源/错误类别/学习读取故障真因
                    "error_class": error_class,
                    "recommendation_source": advice["source"],
                    "learned_error": advice.get("learned_error", ""),
                }
            )
            if len(patterns) >= 5:
                break
        return patterns

    async def _recommendation_for(self, error: str, error_class: str) -> dict:
        """失败规避建议（F2b-②c-1 推荐回流 evolve 面）——learned_patterns 已学习
        修复优先，无学习推荐/学习读取故障→回落既有规则文案（无学习数据时契约
        零变化）。

        学习层是增强面：读取故障绝不阻断进化分析，但必须可见（learned_error
        带真因进 meta，F2b-②"故障不阻断但必须可见"纪律，禁静默吞）。
        """
        try:
            fixes = await learned_failure_fixes_for(error, self.db)
        except Exception as e:
            logger.warning(f"[self-evolution] learned failure fixes read failed: {e}")
            return {
                "recommendation": _RULE_RECOMMENDATIONS.get(
                    error_class, _DEFAULT_RULE_RECOMMENDATION
                ),
                "source": "rule",
                "learned_error": f"{type(e).__name__}: {e}",
            }
        if fixes:
            # 已学习修复建议真回流进化提案（与 format_advisory_for_prompt 的
            # failure 渲染同款取前2条；条数少于2如实全给）
            return {"recommendation": "；".join(fixes[:2]), "source": "learned_patterns"}
        return {
            "recommendation": _RULE_RECOMMENDATIONS.get(error_class, _DEFAULT_RULE_RECOMMENDATION),
            "source": "rule",
        }

    async def _analyze_feedback(self) -> dict:
        """分析用户反馈趋势"""
        rows = await self.db.fetch(
            """
            SELECT rating FROM user_feedback
            WHERE tenant_id = ? AND user_id = ?
            AND created_at > ?
            ORDER BY created_at DESC
        """,
            (self.tenant_id, self.agent_id, time.time() - 30 * 86400),
        )

        if not rows:
            return {"avg_rating": 3.0, "negative_trend": False}

        ratings = [r["rating"] for r in rows if r["rating"] is not None]
        avg = sum(ratings) / len(ratings) if ratings else 3.0

        return {
            "avg_rating": avg,
            "negative_trend": avg < 3.0,
        }

    async def feedback_stats(self) -> dict:
        """用户反馈统计（22:05轮遗留#3配套：/api/brain/feedback与进化分析
        共用同一语义源——API展示的"进化引擎看到什么"即analyze_and_evolve
        真实所见，非独立实现的镜像数字）。

        注意：SelfEvolution构造时agent_id槽位即user_feedback表的user_id
        （analyze_and_evolve→_analyze_feedback同款映射），调用方传user_id。
        """
        stats = await self._analyze_feedback()
        count = 0
        if self.db:
            count = await self.db.fetchval(
                "SELECT COUNT(*) FROM user_feedback WHERE tenant_id = ? AND user_id = ?",
                (self.tenant_id, self.agent_id),
            )
        stats["feedback_count"] = count or 0
        return stats

    # ------------------------------------------------------------------
    # 提案生命周期：签名去重 + 新证据门槛 + 存量归档
    # ------------------------------------------------------------------
    @staticmethod
    def _row_signature(row) -> str:
        """行/候选提案 → 稳定签名（同签名=同一发现）。

        新行用meta.signature；存量行（meta空）回退推导——
        failure_avoidance剥reason的"(N次)"计数后缀与"高频失败: "前缀
        （不同时间的计数快照归并为同一签），其余类型=type+reason全等。
        """
        meta = {}
        try:
            meta = (
                json.loads(row.get("meta") or "{}")
                if isinstance(row, dict)
                else json.loads(row["meta"] or "{}")
            )
        except Exception:
            meta = {}
        sig = meta.get("signature")
        if sig:
            return str(sig)
        ev_type = (
            row["evolution_type"] if not isinstance(row, dict) else row.get("evolution_type", "")
        )
        reason = (row["reason"] if not isinstance(row, dict) else row.get("reason", "")) or ""
        if ev_type == "failure_avoidance":
            body = re.sub(r"\s*\(\d+次\)\s*$", "", reason).strip()
            if body.startswith("高频失败:"):
                body = body[len("高频失败:") :].strip()
            return f"failure_avoidance:{body[:100]}"
        return f"{ev_type}:{reason}"

    async def _last_proposal(self, evolution: dict) -> dict | None:
        """找同签名的最近一次立案（含存量行兼容匹配）。

        不按status过滤——superseded行同样是"立过案"的证据，去重不看归档与否。
        """
        sig = evolution.get("signature", "")
        if not sig:
            return None
        rows = await self.db.fetch(
            """SELECT id, evolution_type, reason, meta, created_at FROM evolution_log
               WHERE tenant_id = ? AND agent_id = ? AND evolution_type = ?
               ORDER BY created_at DESC, id DESC LIMIT 300""",
            (self.tenant_id, self.agent_id, evolution.get("type", "")),
        )
        for row in rows:
            if self._row_signature(row) == sig:
                return {"id": row["id"], "created_at": float(row["created_at"] or 0)}
        return None

    async def _new_failures_since(self, pattern_key: str, since: float) -> bool:
        """立案后该签名是否有新失败发生（新证据驱动再立案的唯一依据）。"""
        if not pattern_key:
            return False
        from src.learn.experience_collector import normalize_error_key

        rows = await self.db.fetch(
            """SELECT error FROM experiences
               WHERE tenant_id = ? AND agent_id = ? AND outcome = 'failure'
               AND created_at > ? AND error IS NOT NULL""",
            (self.tenant_id, self.agent_id, since),
        )
        return any(normalize_error_key(r["error"] or "") == pattern_key for r in rows)

    async def compact_duplicate_logs(self) -> dict:
        """重复立案归档（幂等）：同签名保留最新1条active，更早的标superseded。

        不删行（历史可审计，mem0 §1.1）；get_stats只数active——
        "进化数"指标不再被复读刷屏灌水（生产190行同reason复读的存量清账）。
        """
        rows = await self.db.fetch(
            """SELECT id, evolution_type, reason, meta FROM evolution_log
               WHERE tenant_id = ? AND agent_id = ? AND (status IS NULL OR status = 'active')
               ORDER BY created_at ASC, id ASC""",
            (self.tenant_id, self.agent_id),
        )
        keep: dict[str, int] = {}
        for row in rows:
            keep[self._row_signature(row)] = int(row["id"])  # 升序→后者覆盖=保最新
        drop = [int(row["id"]) for row in rows if keep[self._row_signature(row)] != int(row["id"])]
        for i in range(0, len(drop), 100):
            chunk = drop[i : i + 100]
            placeholders = ",".join("?" for _ in chunk)
            await self.db.execute(
                f"UPDATE evolution_log SET status = 'superseded' WHERE id IN ({placeholders})",
                *chunk,
            )
        return {"superseded": len(drop)}

    async def _log_evolution(self, evolution: dict):
        meta = dict(evolution.get("meta") or {})
        if evolution.get("signature"):
            meta["signature"] = evolution["signature"]
        await self.db.execute(
            """INSERT INTO evolution_log
               (tenant_id, agent_id, evolution_type, reason, meta, impact_score, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active')""",
            (
                self.tenant_id,
                self.agent_id,
                evolution["type"],
                evolution["reason"],
                json.dumps(meta, ensure_ascii=False) if meta else None,
                0.5,
                time.time(),
            ),
        )

    async def get_stats(self) -> dict:
        await self._ensure_table()
        if not self.db:
            return {"evolutions": 0}
        # 只数active（superseded=重复立案归档，不代表独立发现）
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM evolution_log WHERE tenant_id = ? AND agent_id = ? "
            "AND (status IS NULL OR status = 'active')",
            (self.tenant_id, self.agent_id),
        )
        return {"evolutions": count}


# ── 进化周期编排（G1-② 自我进化自动调度：单一代码路径）────────────────
# POST /api/brain/evolve 与 proactive self_evolve 行为共用 run_evolution_cycle
# ——采集→分析→提案声明（审批管线）一条龙，禁两套编排逻辑漂移（F2b-①
# pattern_extract 共用代码路径同纪律：周期化=同一代码路径的定期调用）。

EVOLUTION_KIND_MAP = {
    "failure_avoidance": "failure_avoidance",
    "strategy_adjustment": "policy_adjustment",
    "style_adjustment": "prompt_strategy",
}


async def run_evolution_cycle(tenant_id="default", agent_id="default", db_pool=None) -> dict:
    """自我进化周期：采集真实执行产物 → 分析 → 声明式提案进入审批管线。

    语义原样收编自 POST /api/brain/evolve 端点本体（2026-10-06 P0-7 接线）：
    - 先采集（ExperienceCollector：agent_messages/jobs/eval → experiences）再
      分析——进化永远基于最新真实数据；采集异常不阻断分析（表可能已有历史
      数据可分析），错误显式进 experience_collection.error（mem0 1.1 失败必须
      可见）。
    - SelfEvolution.analyze_and_evolve() 的每条发现作为声明式提案进入
      /api/heredity/evolution/* 审批管线（LobeChat 范式：声明不等于执行，
      reviewer 审批人不等于发起人）。
    - 分析失败不阻断管线观测（analysis_error 如实带真因）。

    db_pool 缺省取全局池（端点语义不变）；proactive 行为注入 env/tmp 池
    （PROACT-③ 数据面隔离）。
    """
    if db_pool is None:
        from src.database.postgres import db_pool as _global_pool

        db_pool = _global_pool
    from src.learn.experience_collector import ExperienceCollector

    collection = {}
    try:
        collection = ExperienceCollector(tenant_id=tenant_id, agent_id=agent_id).collect()
    except Exception as e:  # 采集失败可见但不阻断进化分析
        collection = {"status": "failed", "error": str(e), "written": 0}

    evolutions = []
    analysis_error = ""
    try:
        evolutions = await SelfEvolution(db_pool, tenant_id, agent_id).analyze_and_evolve()
    except Exception as e:  # 分析失败不阻断管线观测
        analysis_error = str(e)

    # 单例接线（2026-10-06 heredity 测试隔离轮）：共享 heredity 单例=单一管线
    # （测试可换数据面），禁每次新建引擎与 /api/heredity/evolution/* 双引擎并存。
    from src.api.heredity import evolution_engine as engine

    declared = []
    for evo in evolutions:
        kind = EVOLUTION_KIND_MAP.get(evo.get("type", ""), "policy_adjustment")
        # 去重键=_digest(kind, title)（evolution_loop.declare_intent）。title 携带
        # reason 后 digest 按发现内容去重：同一发现重复 declare 即 duplicate，
        # 不同发现各建单（只用 action 做 title 会把不同失败模式互相误判
        # duplicate，4 条不同发现被折叠成 1 条=证据丢失）。
        _action = str(evo.get("action") or "").strip()
        _reason = str(evo.get("reason") or "").strip()
        _title = (
            f"{_action}: {_reason}"
            if _action and _reason
            else (_action or _reason or "auto evolution")
        )
        result = engine.declare_intent(
            kind=kind,
            title=_title[:200],
            rationale=str(evo.get("reason", "")),
            confidence=0.4,
            evidence_refs=[f"evolution_log:{evo.get('type', 'unknown')}"],
            proposer=f"self_evolution:{agent_id}",
        )
        declared.append(
            {
                "proposal_id": result.get("proposal_id"),
                "status": result.get("status"),
                "reject_reason": result.get("reject_reason", ""),
                "duplicate": result.get("duplicate", False),
            }
        )
    return {
        "evolutions": evolutions,
        "analysis_error": analysis_error,
        "experience_collection": collection,
        "declared_proposals": declared,
        "pipeline": engine.get_stats(),
    }
