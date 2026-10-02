"""Skill learner for OpenGene.

Automatically extracts reusable skills from successful executions,
forming a skill library for future use.

Borrowed from: Voyager skill library, CodeAct
"""

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("opensoul.gene.skill_learner")


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

    def __init__(self, db_path: str = ""):
        if not db_path:
            base = Path.home() / ".hermes" / "opensoul" / "gene"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "skills.db")
        self.db_path = db_path
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
                "usage_count": row["usage_count"],
                "success_count": row["success_count"],
            }
            conn.execute("DELETE FROM learned_skills WHERE skill_id = ?", (skill_id,))
            conn.commit()
        self._audit(skill_id, "DELETE", old=old, actor=actor, reason=reason)
        return True

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
            logger.warning(
                f"Recorded failed execution into failure memory (skill family {skill_id}): "
                f"{task_description[:50]}"
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
            rows = conn.execute(
                """SELECT * FROM learned_skills
                   ORDER BY usage_count DESC, success_count DESC
                   LIMIT ?""",
                (limit * 3,),
            ).fetchall()

        task_lower = task_description.lower()
        scored = []
        for row in rows:
            d = dict(row)
            d["tags"] = json.loads(d.get("tags", "[]"))
            d["parameters"] = json.loads(d.get("parameters", "[]"))
            score = d["success_count"] * 0.3 + d["usage_count"] * 0.1
            if any(kw in d["name"].lower() for kw in task_lower.split()):
                score += 0.5
            if any(kw in d["description"].lower() for kw in task_lower.split()):
                score += 0.3
            d["relevance_score"] = score
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

    def record_usage(self, skill_id: str, success: bool):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE learned_skills SET
                   usage_count = usage_count + 1,
                   success_count = success_count + ?,
                   last_used_at = ?
                   WHERE skill_id = ?""",
                (1 if success else 0, time.time(), skill_id),
            )
            conn.commit()

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
