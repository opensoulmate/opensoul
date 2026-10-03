"""Skill learner for OpenGene.

Automatically extracts reusable skills from successful executions,
forming a skill library for future use.

Borrowed from: Voyager skill library, CodeAct
"""

import hashlib
import json
import logging
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("opensoul.gene.skill_learner")


def _is_wire_sentinel(tc: dict) -> bool:
    """wire元数据哨兵判定（gene_wire约定：__x__ dunder名=元数据条目非真实工具）。

    如 __TRUNCATED__（截断记账 dropped/total）——不进tool_sequence/参数/模板。
    """
    name = str((tc.get("function") or {}).get("name", "") or "")
    return name.startswith("__") and name.endswith("__") and len(name) > 4


# ── 检索分词（CJK bigram + 拉丁词token）──────────────────────────
# find_relevant旧公式的关键词命中是 `any(kw in name for kw in task.split())`
# ——whitespace分词对中文**整句一个token**（中文无空格），而技能名=提炼时刻的
# 任务描述（task_description[:50]）：换种说法复述同一任务（「清理下日志」vs
# 「帮我把日志文件清理一下」）子串匹配永不命中→相关性增益(+0.5/+0.3)从不触发
# →find_relevant退化成"只按success_rate排"=与任务无关的推荐（进化链检索失灵）。
# 反面：英文靠空格分词能命中，却被the/a等停用词刷分（'a' in 任意含a名字=+0.5）。
# 对齐DeerMem近重复门的CJK bigram先例：CJK连续段→字符bigram（单字段收单字），
# 拉丁/数字→小写单词（len>=2+停用词过滤）。
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_LATIN_STOPWORDS = frozenset(
    "the a an of to in on at for and or is are was be been do does did "
    "not no yes it its this that these those with from by as please".split()
)


def _relevance_tokens(text: str | None) -> set[str]:
    """任务/技能名/描述 → 检索token集（CJK bigram + 拉丁词，全小写）。"""
    t = (text or "").lower()
    tokens: set[str] = set()
    for run in _CJK_RUN_RE.findall(t):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.update(run[i : i + 2] for i in range(len(run) - 1))
    for w in _LATIN_TOKEN_RE.findall(t):
        if len(w) >= 2 and w not in _LATIN_STOPWORDS:
            tokens.add(w)
    return tokens


def _token_coverage(query_tokens: set[str], text: str | None, text_tokens: set[str]) -> float:
    """查询token被文本覆盖的比例（0.0~1.0）。单字token（CJK单字段）按字符出现
    判中——bigram集里没有单字，不能漏。空查询=0（不算全中，避免空任务乱抬分）。"""
    if not query_tokens:
        return 0.0
    hit = sum(
        1 for tok in query_tokens if tok in text_tokens or (len(tok) == 1 and tok in (text or ""))
    )
    return hit / len(query_tokens)


@dataclass
class Skill:
    skill_id: str
    name: str
    description: str
    category: str = "general"
    code_template: str = ""
    parameters: list[str] = field(default_factory=list)
    usage_count: int = 0
    success_count: int = 0
    avg_duration_ms: float = 0.0
    created_at: float = field(default_factory=time.time)
    last_used_at: float = 0.0
    tags: list[str] = field(default_factory=list)
    source_session: str = ""
    audit_event: str = ""  # 本次extract落的审计事件：ADD / UPDATE（mem0 §1.2记账）

    @property
    def success_rate(self) -> float:
        return self.success_count / self.usage_count if self.usage_count else 0.0


class SkillLearner:
    """Extracts and manages reusable skills from executions."""

    # 状态变更事件=回滚语义红线：每技能"最近一次"状态变更事件是revert缺省目标
    # (_pick_event)与删除恢复(old留档)的唯一依据，任何retention上限都不得清掉。
    AUDIT_STATE_EVENTS = ("ADD", "UPDATE", "DELETE", "REVERT")

    def __init__(self, db_path: str = ""):
        if not db_path:
            base = Path.home() / ".hermes" / "opensoul" / "gene"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "skills.db")
        self.db_path = db_path
        # 审计账本retention缺省（真实学习审计只增不减的膨胀治理，dev-reports挂账#4）。
        # 实例属性而非类常量：测试/运维可按需收紧或放宽。
        self.audit_retention = {
            "max_per_skill": 200,  # 每技能审计行上限（超出裁最旧）
            "max_total": 5000,  # 全局审计行上限（超出裁最旧）
            "fail_max_age_days": 30.0,  # FAIL事件过龄即清（无状态快照、非回滚依据）
        }
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learned_skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    category TEXT DEFAULT 'general',
                    code_template TEXT DEFAULT '',
                    parameters TEXT DEFAULT '[]',
                    usage_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    avg_duration_ms REAL DEFAULT 0,
                    created_at REAL NOT NULL,
                    last_used_at REAL DEFAULT 0,
                    tags TEXT DEFAULT '[]',
                    source_session TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_skills_category
                ON learned_skills(category, success_count)
            """)
            # 进化审计表（mem0 §1.2 可直接抄的schema：old/new/event，此处加FAIL分支）
            # "这条技能是被谁、因为哪次执行、从什么改成什么"全程可追溯
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    old_json TEXT DEFAULT '',
                    new_json TEXT DEFAULT '',
                    actor TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_skill_audit_skill
                ON skill_audit(skill_id, audit_id)
            """)
            conn.commit()

    def _audit(
        self,
        skill_id: str,
        event: str,
        old: dict | None = None,
        new: dict | None = None,
        actor: str = "",
        reason: str = "",
    ) -> None:
        """写一条进化审计记录（mem0 §1.2 schema: old/new/event）。

        记账失败绝不阻塞主流程（CowAgent记账哲学），但必须大声记日志——
        静默丢审计=记账体系失真（mem0 §1.1 失败可见）。
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO skill_audit
                       (skill_id, event, old_json, new_json, actor, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        skill_id,
                        event,
                        json.dumps(old, ensure_ascii=False) if old is not None else "",
                        json.dumps(new, ensure_ascii=False) if new is not None else "",
                        actor,
                        reason,
                        time.time(),
                    ),
                )
                conn.commit()
        except Exception:
            logger.exception(f"skill_audit write failed (skill_id={skill_id}, event={event})")
            return
        # 写路径自动retention（防账本只增不减的膨胀，dev-reports挂账#4）：
        # 门槛内只花一个COUNT，超限才真正裁剪。
        self._maybe_prune()

    def _maybe_prune(self) -> None:
        """_audit落账后触发的自动retention。裁剪失败绝不阻塞主流程（CowAgent记账哲学）。"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                total = conn.execute("SELECT COUNT(*) FROM skill_audit").fetchone()[0]
            if total > self.audit_retention["max_total"]:
                result = self.prune_audit()
                logger.info(f"skill_audit auto-pruned: {result}")
        except Exception:
            logger.exception("skill_audit auto-prune failed (non-blocking)")

    def prune_audit(
        self,
        max_per_skill: int | None = None,
        max_total: int | None = None,
        fail_max_age_days: float | None = None,
    ) -> dict:
        """审计账本retention三层裁剪（每层都守回滚语义红线）。

        1. 过龄FAIL：FAIL无状态快照、不是回滚依据，>fail_max_age_days天直接清；
        2. per-skill上限：每技能保最新max_per_skill行；
        3. 全局上限：总行数保最新max_total行。
        红线：每技能"最近一次状态变更事件"（ADD/UPDATE/DELETE/REVERT）任何层都不清——
        它是revert缺省目标(_pick_event)与删除恢复(old留档)的唯一依据；红线行数超过
        上限时账本可暂时高于上限（正确性>瘦身），下次裁剪再收敛。
        返回各层删除计数（可观测：调用方/日志立刻看到裁了多少）。
        """
        cfg = self.audit_retention
        max_per_skill = cfg["max_per_skill"] if max_per_skill is None else max_per_skill
        max_total = cfg["max_total"] if max_total is None else max_total
        fail_max_age_days = (
            cfg["fail_max_age_days"] if fail_max_age_days is None else fail_max_age_days
        )
        if max_per_skill < 1 or max_total < 1 or fail_max_age_days < 0:
            raise ValueError("invalid retention params (caps >= 1, age >= 0)")

        events = ",".join(f"'{e}'" for e in self.AUDIT_STATE_EVENTS)
        anchor_sql = (
            f"SELECT MAX(audit_id) FROM skill_audit WHERE event IN ({events}) GROUP BY skill_id"
        )
        with sqlite3.connect(self.db_path) as conn:
            before = conn.execute("SELECT COUNT(*) FROM skill_audit").fetchone()[0]
            # ① 过龄FAIL（非回滚依据，先清噪声）
            cutoff = time.time() - fail_max_age_days * 86400
            deleted_fail = conn.execute(
                "DELETE FROM skill_audit WHERE event = 'FAIL' AND created_at < ?",
                (cutoff,),
            ).rowcount
            # ② per-skill上限：每技能最新N行 ∪ 红线锚点行
            deleted_per_skill = conn.execute(
                f"""DELETE FROM skill_audit WHERE audit_id NOT IN (
                        SELECT audit_id FROM (
                            SELECT audit_id,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY skill_id ORDER BY audit_id DESC
                                   ) AS rn
                            FROM skill_audit
                        ) WHERE rn <= ?
                        UNION {anchor_sql}
                    )""",
                (max_per_skill,),
            ).rowcount
            # ③ 全局上限：最新M行 ∪ 红线锚点行（子查询包一层：ORDER BY/LIMIT只作用于自身）
            deleted_total = conn.execute(
                f"""DELETE FROM skill_audit WHERE audit_id NOT IN (
                        SELECT audit_id FROM (
                            SELECT audit_id FROM skill_audit
                            ORDER BY audit_id DESC LIMIT ?
                        )
                        UNION {anchor_sql}
                    )""",
                (max_total,),
            ).rowcount
            conn.commit()
            after = conn.execute("SELECT COUNT(*) FROM skill_audit").fetchone()[0]

        return {
            "before": before,
            "after": after,
            "deleted_aged_fail": deleted_fail,
            "deleted_per_skill_cap": deleted_per_skill,
            "deleted_total_cap": deleted_total,
        }

    @staticmethod
    def _audit_row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["old"] = json.loads(d["old_json"]) if d["old_json"] else None
        d["new"] = json.loads(d["new_json"]) if d["new_json"] else None
        del d["old_json"], d["new_json"]
        return d

    def history(self, skill_id: str, limit: int = 50) -> list[dict]:
        """审计历史（mem0 §1.2 history()查询接口）：这条技能被谁、因为哪次执行、从什么改成什么。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM skill_audit WHERE skill_id = ?
                   ORDER BY audit_id DESC LIMIT ?""",
                (skill_id, limit),
            ).fetchall()
        return [self._audit_row_to_dict(r) for r in rows]

    def audit_tail(self, limit: int = 50) -> list[dict]:
        """跨技能的最近审计事件（ADD/UPDATE/DELETE/FAIL），新→旧。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM skill_audit ORDER BY audit_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._audit_row_to_dict(r) for r in rows]

    def delete_skill(self, skill_id: str, actor: str = "api", reason: str = "") -> bool:
        """删除技能并记DELETE审计（old留档=回滚依据，mem0 §1.2 is_deleted语义）。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
            ).fetchone()
            if row is None:
                return False
            old = {
                "name": row["name"],
                "description": row["description"],
                "category": row["category"],
                "code_template": row["code_template"],
                "parameters": json.loads(row["parameters"] or "[]"),
                "tags": json.loads(row["tags"] or "[]"),
                "source_session": row["source_session"],
                "usage_count": row["usage_count"],
                "success_count": row["success_count"],
                # 全保真快照：回滚（revert_skill）需要无损恢复整行，
                # created_at/last_used_at/avg_duration_ms缺失会让undelete丢字段
                "avg_duration_ms": row["avg_duration_ms"],
                "created_at": row["created_at"],
                "last_used_at": row["last_used_at"],
            }
            conn.execute("DELETE FROM learned_skills WHERE skill_id = ?", (skill_id,))
            conn.commit()
        self._audit(skill_id, "DELETE", old=old, actor=actor, reason=reason)
        return True

    # ── 进化回滚（Agent Zero _time_travel travel/revert语义 + preview先行） ──
    # 状态字段全集：revert预览的diff面与_apply_state的恢复面共用同一真源
    STATE_FIELDS = (
        "name",
        "description",
        "category",
        "code_template",
        "parameters",
        "tags",
        "source_session",
        "usage_count",
        "success_count",
        "avg_duration_ms",
        "created_at",
        "last_used_at",
    )

    @staticmethod
    def _skill_row_state(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["parameters"] = json.loads(d["parameters"] or "[]")
        d["tags"] = json.loads(d["tags"] or "[]")
        return d

    def current_state(self, skill_id: str) -> dict | None:
        """技能当前全量状态（learned_skills整行），不存在=已删除态(None)。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,)
            ).fetchone()
        return self._skill_row_state(row) if row else None

    def _pick_event(self, skill_id: str, audit_id: int | None) -> dict | None:
        """选定回滚目标事件。

        - 显式audit_id：回滚到该事件发生之前（含ADD→删除技能、REVERT→再翻回去）。
        - 缺省：最近一次有状态变更的事件（UPDATE/DELETE/REVERT）=一键回滚上一次改动；
          只有ADD史的技能不自动选（防误删），必须显式传audit_id。
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if audit_id is None:
                row = conn.execute(
                    """SELECT * FROM skill_audit
                       WHERE skill_id = ? AND event IN ('UPDATE','DELETE','REVERT')
                       ORDER BY audit_id DESC LIMIT 1""",
                    (skill_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM skill_audit WHERE skill_id = ? AND audit_id = ?",
                    (skill_id, audit_id),
                ).fetchone()
        return self._audit_row_to_dict(row) if row else None

    def revert_preview(self, skill_id: str, audit_id: int | None = None) -> dict | None:
        """回滚预览（Agent Zero _time_travel：破坏性动作前先看清diff）——零写入。

        返回action（restore/update/delete）+逐字段changes{from,to}；
        无可回滚事件=None；FAIL事件无状态变更→ValueError。
        """
        event = self._pick_event(skill_id, audit_id)
        if event is None:
            return None
        if event["event"] == "FAIL":
            raise ValueError(f"audit event {event['audit_id']} is FAIL (no skill state to revert)")
        target = event["old"]  # 该事件发生之前的状态；None=之前不存在（回滚=删除）
        current = self.current_state(skill_id)
        changes = {}
        for f in self.STATE_FIELDS:
            cur_v = current.get(f) if current else None
            tgt_v = target.get(f) if target else None
            if cur_v != tgt_v:
                changes[f] = {"from": cur_v, "to": tgt_v}
        return {
            "skill_id": skill_id,
            "event": {k: event[k] for k in ("audit_id", "event", "actor", "reason", "created_at")},
            "action": "delete" if target is None else ("restore" if current is None else "update"),
            "changes": changes,
            "current": current,
            "target": target,
        }

    def _apply_state(self, skill_id: str, target: dict | None, current: dict | None) -> None:
        """把learned_skills行写成target状态（None=删除行）。target没记录的字段保留当前值。"""
        with sqlite3.connect(self.db_path) as conn:
            if target is None:
                conn.execute("DELETE FROM learned_skills WHERE skill_id = ?", (skill_id,))
                conn.commit()
                return
            base = dict(current or {})
            base.update(target)  # 快照记录的字段以旧快照为准，老审计行缺的字段不回退
            now = time.time()
            vals = (
                base.get("name", ""),
                base.get("description", ""),
                base.get("category", "general"),
                base.get("code_template", ""),
                json.dumps(base.get("parameters", [])),
                int(base.get("usage_count", 0) or 0),
                int(base.get("success_count", 0) or 0),
                float(base.get("avg_duration_ms", 0) or 0),
                float(base.get("created_at", now) or now),
                float(base.get("last_used_at", 0) or 0),
                json.dumps(base.get("tags", [])),
                base.get("source_session", ""),
            )
            if current is None:
                conn.execute(
                    """INSERT INTO learned_skills
                       (name, description, category, code_template, parameters,
                        usage_count, success_count, avg_duration_ms,
                        created_at, last_used_at, tags, source_session, skill_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    vals + (skill_id,),
                )
            else:
                conn.execute(
                    """UPDATE learned_skills SET
                       name=?, description=?, category=?, code_template=?, parameters=?,
                       usage_count=?, success_count=?, avg_duration_ms=?,
                       created_at=?, last_used_at=?, tags=?, source_session=?
                       WHERE skill_id=?""",
                    vals + (skill_id,),
                )
            conn.commit()

    def revert_skill(
        self,
        skill_id: str,
        audit_id: int | None = None,
        actor: str = "api",
        reason: str = "",
    ) -> dict | None:
        """一键回滚：技能状态恢复到目标审计事件发生之前（Agent Zero travel/revert）。

        - 缺省=回滚上一次改动；显式audit_id=回到任意事件之前（ADD→删除技能）。
        - 回滚本身记REVERT审计（old=回滚前、new=恢复后）——回滚可再被回滚（travel链不断）。
        - 审计表只追加不改写（进化引擎保护区：回滚点/审计本身不可被改写）。
        """
        preview = self.revert_preview(skill_id, audit_id)
        if preview is None:
            return None
        target, current = preview["target"], preview["current"]
        self._apply_state(skill_id, target, current)
        self._audit(
            skill_id,
            "REVERT",
            old=current,
            new=target if target is not None else {"deleted": True},
            actor=actor,
            reason=(f"{reason} revert_to_before_audit_id={preview['event']['audit_id']}".strip()),
        )
        logger.info(
            f"Reverted skill {skill_id} to before audit_id={preview['event']['audit_id']} "
            f"(action={preview['action']}, actor={actor})"
        )
        return {
            "skill_id": skill_id,
            "action": preview["action"],
            "reverted_to_before": preview["event"]["audit_id"],
            "restored": target,
            "changes": preview["changes"],
        }

    def extract_from_execution(
        self,
        session_id: str,
        task_description: str,
        tool_calls: list[dict],
        success: bool,
    ) -> Skill | None:
        """Extract a reusable skill from an execution.

        成功 → skill行（首见ADD；再见到同一skill_id=UPDATE且**累计计数不重置**）
        + 一条审计记录（mem0 §1.2：谁/何时/从什么改成什么）。
        失败且有tool_calls → FAIL审计记录（mem0 §1.1：失败必须可见，进失败记忆
        分支而非静默丢弃），返回None。
        """
        if not tool_calls:
            return None

        # wire哨兵过滤（gene_wire约定：__x__ dunder名=元数据条目非真实工具）：
        # __TRUNCATED__截断标记不进tool_sequence/参数/模板——否则同一任务不同
        # 截断点产生不同skill_id碎片。过滤后为空=本回合只有元数据，同样不提炼。
        tool_calls = [tc for tc in tool_calls if not _is_wire_sentinel(tc)]
        if not tool_calls:
            return None

        tools_used = [tc.get("function", {}).get("name", "") for tc in tool_calls]
        tool_sequence = " → ".join(t for t in tools_used if t)

        skill_id = f"skill_{hashlib.sha256(f'{task_description}:{tool_sequence}'.encode()).hexdigest()[:12]}"

        if not success:
            # 失败记忆分支：可见、可按skill_id检索到失败族谱（mem0 §1.1 静默吞错误=失败记忆失真）
            self._audit(
                skill_id,
                "FAIL",
                new={
                    "task_description": task_description,
                    "tool_sequence": tool_sequence,
                },
                actor=session_id,
                reason="execution failed",
            )
            # 使用遥测真实接线（02:15轮遗留"record_usage无调用方"销账）：失败执行也是
            # "使用"——usage_count+1、success_count不动，success_rate随失败下降，
            # find_relevant排序自动降权。技能族尚无成功提炼行时updated=False纯no-op
            # （没技能可归因，只留FAIL审计，不落learned_skills行）。
            usage = self.record_usage(skill_id, success=False)
            logger.warning(
                f"Recorded failed execution into failure memory (skill family {skill_id}): "
                f"{task_description[:50]}"
                + (
                    f" (usage now {usage['success_count']}/{usage['usage_count']}, "
                    f"success_rate={usage['success_rate']:.2f})"
                    if usage["updated"]
                    else " (no learned skill row yet, usage not attributable)"
                )
            )
            return None

        # Only extract if meaningful pattern detected
        meaningful_patterns = [
            ("read_file", "write_file"),
            ("terminal", "read_file"),
            ("web_search", "read_file"),
            ("browser_exec", "terminal"),
        ]
        is_pattern = any(all(t in tools_used for t in pattern) for pattern in meaningful_patterns)
        if len(tools_used) < 2 and not is_pattern:
            return None

        parameters = self._extract_parameters(tool_calls)
        code_template = self._generate_template(tool_calls)

        skill = Skill(
            skill_id=skill_id,
            name=task_description[:50],
            description=f"从执行中学习: {tool_sequence}",
            category=self._categorize(task_description),
            code_template=code_template,
            parameters=parameters,
            tags=list(set(t for t in tools_used if t)),
            source_session=session_id,
        )

        now = time.time()
        snapshot = {
            "name": skill.name,
            "description": skill.description,
            "category": skill.category,
            "code_template": skill.code_template,
            "parameters": skill.parameters,
            "tags": skill.tags,
            "source_session": session_id,
        }
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT * FROM learned_skills WHERE skill_id = ?", (skill.skill_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO learned_skills
                       (skill_id, name, description, category, code_template,
                        parameters, usage_count, success_count, avg_duration_ms,
                        created_at, last_used_at, tags, source_session)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        skill.skill_id,
                        skill.name,
                        skill.description,
                        skill.category,
                        skill.code_template,
                        json.dumps(skill.parameters),
                        1,
                        1,
                        0,
                        now,
                        now,
                        json.dumps(skill.tags),
                        session_id,
                    ),
                )
                event, old = "ADD", None
                skill.usage_count, skill.success_count = 1, 1
            else:
                # 再见到同一skill（同task+tool序列）= UPDATE：
                # 禁止INSERT OR REPLACE——那会把usage_count/success_count静默重置为1、
                # created_at被覆盖（进化数据丢失）。此处内容字段更新+计数累加+created_at保留，
                # old/new全量留档进审计（mem0 §1.2）。
                conn.execute(
                    """UPDATE learned_skills SET
                       name = ?, description = ?, category = ?, code_template = ?,
                       parameters = ?, tags = ?, source_session = ?,
                       usage_count = usage_count + 1,
                       success_count = success_count + 1,
                       last_used_at = ?
                       WHERE skill_id = ?""",
                    (
                        skill.name,
                        skill.description,
                        skill.category,
                        skill.code_template,
                        json.dumps(skill.parameters),
                        json.dumps(skill.tags),
                        session_id,
                        now,
                        skill.skill_id,
                    ),
                )
                event = "UPDATE"
                skill.usage_count = existing["usage_count"] + 1
                skill.success_count = existing["success_count"] + 1
                old = {
                    "name": existing["name"],
                    "description": existing["description"],
                    "category": existing["category"],
                    "code_template": existing["code_template"],
                    "parameters": json.loads(existing["parameters"] or "[]"),
                    "tags": json.loads(existing["tags"] or "[]"),
                    "source_session": existing["source_session"],
                    "usage_count": existing["usage_count"],
                    "success_count": existing["success_count"],
                    # 全保真快照（回滚依据，Agent Zero _time_travel"preview后可travel"）：
                    # 三个时间/时长字段缺失会让revert丢数据
                    "avg_duration_ms": existing["avg_duration_ms"],
                    "created_at": existing["created_at"],
                    "last_used_at": existing["last_used_at"],
                }
            conn.commit()

        self._audit(
            skill.skill_id,
            event,
            old=old,
            new=snapshot,
            actor=session_id,
            reason="extract_from_execution",
        )
        skill.audit_event = event
        logger.info(f"Extracted skill: {skill.name} ({skill.category}) [{event}]")
        return skill

    def _extract_parameters(self, tool_calls: list[dict]) -> list[str]:
        params = set()
        for tc in tool_calls:
            args = tc.get("function", {}).get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    continue
            for key in ("path", "command", "url", "query", "content"):
                if key in args:
                    params.add(key)
        return list(params)

    def _generate_template(self, tool_calls: list[dict]) -> str:
        lines = []
        for tc in tool_calls[:5]:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            param_args = {}
            for k, v in args.items():
                if isinstance(v, str) and len(v) > 10:
                    param_args[k] = f"{{{{{k}}}}}"
                else:
                    param_args[k] = v
            lines.append(f"{name}({json.dumps(param_args, ensure_ascii=False)})")
        return "\n".join(lines)

    def _categorize(self, description: str) -> str:
        desc = description.lower()
        if any(kw in desc for kw in ["file", "write", "read", "edit", "文件"]):
            return "file_operations"
        if any(kw in desc for kw in ["web", "search", "browser", "网页", "搜索"]):
            return "web_operations"
        if any(kw in desc for kw in ["terminal", "command", "shell", "命令"]):
            return "system_operations"
        if any(kw in desc for kw in ["code", "debug", "test", "代码", "测试"]):
            return "coding"
        return "general"

    def find_relevant(self, task_description: str, limit: int = 3) -> list[dict]:
        """Find skills relevant to a task description."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            # 候选全集而非usage序窗口（冷启动盲区修复）：先按usage开LIMIT*3窗口
            # 再做相关性过滤，会让新学技能（usage=1/2）永远挤不进高usage旧技能
            # 占满的窗口——再相关也查不到（live API实锤：135行库中12条(2,2)旧测试
            # 行把9窗口占满→刚提炼的技能recommend查无此技能）。相关性/评分对全集
            # 做，最后才按relevance_score截limit；表规模（学习技能数）远够全扫。
            rows = conn.execute("SELECT * FROM learned_skills").fetchall()

        # 三因子评分（generative-agents recency+importance+relevance范式的技能侧）：
        #   relevance  = 0.6*名字覆盖 + 0.4*描述覆盖（CJK bigram/词token覆盖度，见
        #                _relevance_tokens——旧whitespace子串匹配对中文整句一个token
        #                从不命中，"usage排序公式启发式"遗留销账）
        #   trust      = success_rate（失败只稀释不加分——失败越多不得排越前）
        #   evidence   = min(usage_count,10)/10（证据量封顶，防老技能靠堆量躺赢）
        #   recency    = exp(-age/30天)（last_used_at/created_at锚点，久未用的技能
        #                轻微降权——新鲜经验优先，权重小不翻盘相关性/成败）
        # score = 0.45*relevance + 0.35*trust + 0.10*evidence + 0.10*recency
        # 每行带relevance_breakdown（可观测：为什么这行排这——覆盖率/四因子裸值）。
        task_tokens = _relevance_tokens(task_description)
        now = time.time()
        scored = []
        for row in rows:
            d = dict(row)
            d["tags"] = json.loads(d.get("tags", "[]"))
            d["parameters"] = json.loads(d.get("parameters", "[]"))
            # success_rate如实回填（learned_skills行里没这列，此前recommend恒返回
            # get缺省0.0——指标说谎：真实成败率被0.0掩盖）
            d["success_rate"] = d["success_count"] / d["usage_count"] if d["usage_count"] else 0.0
            name_cov = _token_coverage(
                task_tokens, d.get("name") or "", _relevance_tokens(d.get("name"))
            )
            desc_cov = _token_coverage(
                task_tokens, d.get("description") or "", _relevance_tokens(d.get("description"))
            )
            relevance = 0.6 * name_cov + 0.4 * desc_cov
            trust = d["success_rate"]
            evidence = min(d["usage_count"], 10) / 10
            anchor = d.get("last_used_at") or d.get("created_at") or now
            recency = math.exp(-max(0.0, now - float(anchor)) / (30.0 * 86400))
            score = 0.45 * relevance + 0.35 * trust + 0.10 * evidence + 0.10 * recency
            d["relevance_score"] = score
            d["relevance_breakdown"] = {
                "name_coverage": round(name_cov, 4),
                "description_coverage": round(desc_cov, 4),
                "relevance": round(relevance, 4),
                "trust": round(trust, 4),
                "evidence": round(evidence, 4),
                "recency": round(recency, 4),
            }
            scored.append(d)

        scored.sort(key=lambda x: x["relevance_score"], reverse=True)
        return scored[:limit]

    def get_context_prompt(self, task_description: str) -> str:
        """Generate skill context for system prompt injection."""
        skills = self.find_relevant(task_description, limit=2)
        if not skills:
            return ""

        lines = ["## 可复用技能（从历史执行中学习）\n"]
        for skill in skills:
            lines.append(f"### {skill['name']}")
            lines.append(f"描述: {skill['description']}")
            if skill["code_template"]:
                lines.append(f"模板:\n```\n{skill['code_template']}\n```")
            lines.append(f"成功率: {skill['success_count']}/{skill['usage_count']}")
            lines.append("")

        return "\n".join(lines)

    def record_usage(self, skill_id: str, success: bool) -> dict:
        """使用遥测单一入口：记一次"技能被使用"（成功/失败）。

        usage_count恒+1；success_count仅成功时+1；last_used_at=使用时刻。
        skill_id无learned_skills行时纯no-op（updated=False，没技能可归因不落新行）。
        返回更新后计数（可观测：调用方/日志能立刻看到success_rate漂移）。
        刻意不写审计：USAGE遥测量大，skill_audit记"进化变更"（ADD/UPDATE/DELETE/
        FAIL/REVERT），计数变更的留痕由extract路径的UPDATE审计承担。
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                """UPDATE learned_skills SET
                   usage_count = usage_count + 1,
                   success_count = success_count + ?,
                   last_used_at = ?
                   WHERE skill_id = ?""",
                (1 if success else 0, time.time(), skill_id),
            )
            conn.commit()
            row = conn.execute(
                """SELECT usage_count, success_count FROM learned_skills
                   WHERE skill_id = ?""",
                (skill_id,),
            ).fetchone()
        if row is None:
            return {
                "updated": False,
                "usage_count": 0,
                "success_count": 0,
                "success_rate": 0.0,
            }
        usage_count = row["usage_count"]
        success_count = row["success_count"]
        return {
            "updated": True,
            "usage_count": usage_count,
            "success_count": success_count,
            "success_rate": success_count / usage_count if usage_count else 0.0,
        }

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM learned_skills").fetchone()[0]
            by_category = conn.execute(
                "SELECT category, COUNT(*) FROM learned_skills GROUP BY category"
            ).fetchall()
            top = conn.execute(
                """SELECT name, usage_count, success_count
                   FROM learned_skills ORDER BY usage_count DESC LIMIT 5"""
            ).fetchall()
            audit_total = conn.execute("SELECT COUNT(*) FROM skill_audit").fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM skill_audit WHERE event = 'FAIL'"
            ).fetchone()[0]

        return {
            "total_skills": total,
            "by_category": {c[0]: c[1] for c in by_category},
            "top_skills": [{"name": s[0], "uses": s[1], "successes": s[2]} for s in top],
            "audit_events": audit_total,
            "failed_extractions": failed,
        }
