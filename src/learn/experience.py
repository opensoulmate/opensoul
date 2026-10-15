"""Experience Memory — SQLite存储版本，多租户隔离。"""

import hashlib
import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

from src.learn.long_term import LITERAL_OVERLAP_THRESHOLD, literal_overlap
from src.models.cognitive import Experience, Intent, TaskContext, Verification

logger = logging.getLogger(__name__)

# F4b-㉜ 扫描上界单一源（get_relevant_experience/get_similar_failures 共用，
# 禁两处字面 LIMIT 漂移——F4b-㉗ 纪律）：按 outcome 类各扫描至多 100 行再评分。
_QUERY_SCAN_LIMIT = 100

# DEC-⑤-①b 幂等键"身份字段"单一源——哪些字段定义"这一份执行结果"（显式
# result_id 在场时整份证据不参与定键，客户端声明即身份；未给才走证据指纹）。
_RESULT_KEY_FIELDS = (
    "task_id",
    "action",
    "goal",
    "target_files",
    "exit_code",
    "exception",
    "error",
    "file_written",
    "file_path",
    "test_output",
    "diff",
)


def execution_result_key(result_id: str, report: dict) -> str:
    """执行结果回传幂等键单一派生口（DEC-⑤-①b；key 语义自决见 pending-decisions ⑪）。

    - 显式 result_id 非空白 → 客户端声明身份（"explicit:"+result_id）：同 id 重复
      回传即同一份报告，证据差异不拆行（客户端要区分两次上报就给不同 id）。
    - 未给 → 证据指纹派生（"derived:"+sha256(规范 JSON)）：同份证据的网络重试/
      重复回传同键（at-most-once 不重复落库），证据变化（如 exit_code 改变=另一次
      观测）异键各记一行。
    键刻意不含租户/agent：作用域=行键 (tenant_id, agent_id)，跨租户同键互不去重
    （防去重面变跨租户信息通道）。
    """
    rid = (result_id or "").strip()
    if rid:
        return "explicit:" + rid
    scope = {k: report.get(k) for k in _RESULT_KEY_FIELDS}
    canonical = json.dumps(scope, ensure_ascii=False, sort_keys=True, default=str)
    return "derived:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
                    metadata TEXT DEFAULT '{}',
                    result_key TEXT
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
            # C1 幂等自愈：老库补列（CREATE TABLE IF NOT EXISTS 不给既有表加列）
            cols = await self.db.fetch("PRAGMA table_info(experiences)")
            if not any(c["name"] == "result_key" for c in cols):
                await self.db.execute("ALTER TABLE experiences ADD COLUMN result_key TEXT")
            # 幂等键唯一索引=at-most-once 的结构保证（非查重守卫）；必须在补列之后
            # 建（③c-2① 坑：老库先建索引=no such column）。NULL 键在 SQLite 唯一
            # 索引下互不撞（既有无键行/空键行为语义零变化）。
            await self.db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_result_key
                ON experiences(tenant_id, agent_id, result_key)
            """)
        self._initialized = True

    async def record(
        self,
        action: str,
        result: dict,
        verification: Verification,
        task_ctx: TaskContext,
        result_key: str = "",
    ) -> dict:
        """记录一次行动结果。

        DEC-⑤-①b 重复回传幂等：result_key 非空时按 (tenant_id, agent_id, result_key)
        at-most-once——同键重复回传不重复落库，返回首行 outcome/error/fix
        （duplicate=True, recorded=False，禁谎报 recorded=True）；空键=既有语义
        逐字不变（每次都记一行）。键唯一索引兜底并发撞键（撞键=回读首行如实报
        duplicate，非吞错）。返回 dict 为加性契约（既有调用方不读返回值零影响）。
        """
        await self._ensure_table()
        outcome = "success" if verification.success else "failure"
        db = self.db
        if not db:
            return {"recorded": False, "duplicate": False, "outcome": outcome}

        async def _find_existing():
            return await db.fetchrow(
                """SELECT outcome, error, fix FROM experiences
                   WHERE tenant_id = ? AND agent_id = ? AND result_key = ? LIMIT 1""",
                (self.tenant_id, self.agent_id, result_key),
            )

        if result_key:
            existing = await _find_existing()
            if existing is not None:
                return {
                    "recorded": False,
                    "duplicate": True,
                    "outcome": existing["outcome"],
                    "error": existing["error"],
                    "fix": existing["fix"],
                }

        try:
            await db.execute(
                """INSERT INTO experiences
                   (tenant_id, agent_id, action, intent_summary, outcome, error, fix, relevance_score, created_at, metadata, result_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    # DEC-⑤-① 回传协议溯源：task_id 进 metadata（铁律 json.dumps；
                    # 无 task_id=空对象，既有形状零变化）
                    json.dumps({"task_id": task_ctx.task_id} if task_ctx.task_id else {}),
                    result_key or None,
                ),
            )
        except Exception:
            # 并发同键撞唯一索引：回读首行如实报 duplicate（at-most-once 语义收口）；
            # 非撞键故障照常上抛（禁吞错假 duplicate）
            if not result_key:
                raise
            existing = await _find_existing()
            if existing is None:
                raise
            return {
                "recorded": False,
                "duplicate": True,
                "outcome": existing["outcome"],
                "error": existing["error"],
                "fix": existing["fix"],
            }
        # 老化淘汰
        await self._aging_prune()
        return {
            "recorded": True,
            "duplicate": False,
            "outcome": outcome,
            "error": verification.error,
            "fix": verification.fix,
        }

    async def get_relevant_experience(self, intent: Intent) -> list[Experience]:
        """获取和当前意图相关的历史经验。

        F4b-㉜ 修前=单条混合查询 top-100 近期窗口（F4b-㉚ 边界⑥ 留档件）——与
        get_similar_failures 修前同型的**跨类截断**：成功行量大（生产 958:22）
        把失败行挤出混合窗口（生产实证：窗口=最近约 2 天，22 条 failure 全在
        24 天前，混合窗口内 failure=0）——只有失败补贴才可能过门槛的老失败行
        结构性查不到=静默丢。现按 outcome 类分窗直查（idx_exp_outcome 真索引）
        再合并评分：失败类/非失败类各自独立窗口，跨类不再互相挤占（与
        get_similar_failures 的 outcome 直查同修法同源）。扫描上界如实标注：
        单类超 _QUERY_SCAN_LIMIT 行时类内更旧的相关行仍会被截断（与
        get_similar_failures 同一边界，非静默语义——文档化边界）。
        """
        await self._ensure_table()
        if not self.db:
            return []

        # 按意图类型匹配 + 时间衰减（评分语义零变化，只修扫描面）
        rows: list = []
        for outcome_filter in ("outcome = 'failure'", "outcome <> 'failure'"):
            rows.extend(
                await self.db.fetch(
                    f"""SELECT action, intent_summary, outcome, error, fix, created_at, relevance_score
                        FROM experiences
                        WHERE tenant_id = ? AND agent_id = ? AND {outcome_filter}
                        ORDER BY created_at DESC
                        LIMIT {_QUERY_SCAN_LIMIT}""",
                    (self.tenant_id, self.agent_id),
                )
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
            f"""SELECT action, intent_summary, outcome, error, fix, created_at, relevance_score
               FROM experiences
               WHERE tenant_id = ? AND agent_id = ? AND outcome = 'failure'
               ORDER BY created_at DESC
               LIMIT {_QUERY_SCAN_LIMIT}""",
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


# ── DEC-③-① episodic 失败经验软注入（pending-decisions ③ 试水形态 B） ──────
# 与 F2b-② learned 块（learned_patterns 语义层）并列的第二条回流轴：episodic
# 原始经验（experiences）失败行 error 字段 → 执行 prompt 软建议块。C 形态
# （fix 真数据注入）解锁条件=DEC-⑤-① 仓外回传方产生真数据，届时另拍。

_EPISODIC_ERROR_MAX_CHARS = 200


def _episodic_error_text(exp) -> str:
    """failure 行 error 字段归一文本（非 failure/空 error 返回空串=不入块）。

    试水形态 B 纪律：仅 error 字段进 prompt（intent_summary/fix 等一律不注入，
    测试锁死）；换行折叠为单行、超长截断如实带省略号。
    """
    if getattr(exp, "outcome", None) != "failure":
        return ""
    err = (getattr(exp, "error", None) or "").strip()
    if not err:
        return ""
    err = re.sub(r"\s+", " ", err)
    if len(err) > _EPISODIC_ERROR_MAX_CHARS:
        err = err[:_EPISODIC_ERROR_MAX_CHARS] + "…"
    return err


def format_episodic_advisory_for_prompt(items, top_k: int = 3) -> str:
    """失败经验 → 执行 prompt 软建议块（纯函数；无可用项返回空串=零注入）。

    软建议块语义沿用 F2b-② format_advisory_for_prompt：免责块头（仅供参考、
    与任务要求冲突时以任务要求为准）+ 软上下文不覆盖任务本体。
    """
    errs = [t for t in (_episodic_error_text(e) for e in (items or [])) if t][:top_k]
    if not errs:
        return ""
    lines = [f"{i}. {err}" for i, err in enumerate(errs, 1)]
    return (
        "【历史失败提醒】来自本系统执行类似任务的历史失败教训，仅供参考；"
        "与任务要求冲突时以任务要求为准：\n" + "\n".join(lines)
    )


async def episodic_advisory_for(goal: str, db_pool=None, top_k: int = 3) -> tuple[str, int]:
    """episodic 回流通用取数口（DEC-③-①）：相似失败经验 error → 建议块。

    与 learned_advisory_for（F2b-②b）同款纪律：口径 ADVISORY_SCOPE 单一源
    （学习产出在哪写就去哪读）、异常如实抛禁静默吞（降级可见性归消费方）。
    检索走 ExperienceMemory.get_similar_failures（F4b-㉚ 相关度真源：字面相关
    门槛 0.2，无关零注入宁缺勿滥）。返回 (建议块, 条数)，无命中=("", 0)。
    """
    if db_pool is None:
        from src.database.postgres import db_pool as _db_pool

        db_pool = _db_pool
    from src.learn.long_term import ADVISORY_SCOPE

    memory = ExperienceMemory(db_pool, *ADVISORY_SCOPE)
    intent = Intent(
        user_prompt=goal,
        target_files=[],
        modify_scope="single_file",
        goal="modify",
        change_size="small",
    )
    failures = await memory.get_similar_failures(intent)
    items = [f for f in failures if _episodic_error_text(f)][:top_k]
    return format_episodic_advisory_for_prompt(items, top_k=top_k), len(items)
