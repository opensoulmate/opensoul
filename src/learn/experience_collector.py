"""ExperienceCollector — P0-7进化数据层：真实执行产物 → experiences表。

背景（2026-09-21 live取证）：POST /api/brain/evolve 返回
analysis_error="no such table: experiences"——SelfEvolution.analyze_and_evolve()
与LongTermLearning.extract_patterns()的进化分析查询无输入数据，
进化管线结构性空转（用户痛点"但是一直没有进化啊"的数据层根因）。

调研来源：
- TradingAgents决策延迟回填（SUMMARY.md P0-6："pending→update_with_outcome，
  带真实反馈信号的记忆——'不进化'痛点独有解"）：经验必须来自真实执行结果，
  本模块只搬运已发生的事实（聊天回复/作业结果/评估结论），绝不伪造outcome
- agno learning_zone（evolution-engine-patterns.md §2.1）："有成有败"的任务集
  才有进化价值 → success与failure都如实采集；
  agno idempotency_key（§5.2）→ source_ref确定性去重，重复采集零重复写入
- mem0 §1.1/§1.2（同上）："失败必须可见禁止静默降级"→失败原文入error列
  （SelfEvolution._analyze_failure_patterns按error分组，原文即分组键）；
  metadata携带来源provenance（哪条消息/哪个作业/哪次实验）可审计
- SelfEvolution现有查询契约（src/heredity/self_evolution.py，本模块是其数据生产者）：
  experiences(tenant_id, agent_id, outcome, error, created_at) +
  user_feedback(tenant_id, user_id, rating, created_at)——后者当前无任何写入方，
  本模块只建空表（无真实评分数据不伪造，_analyze_feedback对空表返回诚实默认值）

三个真实数据源（均已在生产库实证存在）：
1. agent_messages（opensoul.db，756行）：assistant回合含显式失败签名（生产实证
   20条"推理错误: ..."前缀）→ failure；无签名 → success（可观测行为=无错误）
2. jobs（~/opensoul/data/job_queue.db，83 completed/204 failed）：作业真实执行结果
3. eval_experiments（~/.hermes/opensoul/benchmark/eval_loop.db）：评估闭环逐case
   pass/fail（agno"超时≠答错"：unscored case跳过不计outcome）

确定性失败签名（_message_failure）：只认显式标记，宁可漏报不可误报——
误报的failure会污染进化提案（错误分组），漏报只是样本少一点。
"""

import json
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("opensoul.experience_collector")

# 显式失败签名（生产数据+各runtime契约；判定逻辑见_message_failure，组合规则在那里）
FAILURE_SIGNATURES = (
    "推理错误",  # 生产实证：soulmate推理异常前缀（20条/4种错误文本）
    "[被拦截]",  # P0-3权限引擎deny的合成工具结果
    "[BLOCKED]",  # immune输出侧护栏/agent-zero终止标记
    "Traceback (most recent call last)",  # Python异常原文（code mode/terminal输出）
    "error=1",  # 导入会话契约：[tool_result id=.. error=1]（须与[tool_result]同现）
)


def _message_failure(content: str) -> tuple[str, str]:
    """assistant消息 → (outcome, error_text)。

    只认显式失败签名；"[tool_result"单独出现=成功工具结果，须与"error=1"
    同现才算失败（导入契约）。error_text取首个签名所在行（SelfEvolution按
    error精确文本分组计数，同源错误行首一致→正确聚合）。
    """
    text = content or ""
    lines = text.splitlines()
    first = lines[0].strip() if lines else ""
    if text.startswith("推理错误"):
        return "failure", first[:200]
    if "[被拦截]" in text:
        return "failure", next((ln.strip() for ln in lines if "[被拦截]" in ln), "[被拦截]")[:200]
    if "[BLOCKED]" in text:
        return "failure", next((ln.strip() for ln in lines if "[BLOCKED]" in ln), "[BLOCKED]")[:200]
    if "error=1" in text and "[tool_result" in text:
        return "failure", next(
            (ln.strip() for ln in lines if "error=1" in ln), "tool_result error=1"
        )[:200]
    if "Traceback (most recent call last)" in text:
        idx = text.find("Traceback")
        return "failure", text[idx : idx + 200]
    return "success", ""


# ── 错误分组键归一化（20:36轮遗留#3销账）────────────────────────────
# SelfEvolution._analyze_failure_patterns 按 error 精确文本 GROUP BY 计数，
# job_queue 的运行时终态标记会让同根因错误分裂成多个组（单组计数达不到
# 阈值→高频失败漏报/提案碎片化）。只动采集/分析侧的分组键语义，
# job_queue 自身的失败可见原文不改（报告口径：不动 job_queue 语义）。

BREAKER_PREFIX = "[breaker_open] "
STALE_PREFIX = "stale recovery:"

# ── 测试作业provenance（learned_skills.source同款哲学，jobs.source列配套）──
# 主门=jobs.source列（'test'精确标记）——采集侧不做名字猜测（既有测试
# test_job_outcomes钉住"作业名不参与判定"语义：名字叫test_job的作业照常采集）。
# 测试垃圾的清理责任在产生侧：systemic_test提交时标source='test'并在跑完按
# provenance+自有保留名自清（见acp-proxy/systemic_test.py S3/S6自清理块）。


def normalize_error_key(error: str) -> str:
    """失败error → 稳定分组键。

    归一化对象（均已在生产job_queue.db实证存在）：
    - "[breaker_open] {原文}"（job_queue.py:553 错误风暴熔断终态前缀）
      → 剥前缀，根因原文即分组键
    - "stale recovery: crashed mid-run, retry budget exhausted (n/m) —
      requeue via POST /api/will/jobs/{id}/requeue"（job_queue.py:226）
      → 每个作业的 (n/m)/job_id 都不同 → 每条error唯一 → 分组键碎片化；
      坍缩为稳定键
    - "stale recovery: previous process exited mid-run"（job_queue.py:235）
      → 本身稳定，保留原文
    - 其余error原样（宁可分组保守，绝不误合并不同根因；mem0 §1.1：
      归一化只剥已知运行时标记，不猜测语义）
    """
    key = (error or "").strip()
    while key.startswith(BREAKER_PREFIX):
        key = key[len(BREAKER_PREFIX) :].strip()
    if key.startswith(STALE_PREFIX):
        rest = key[len(STALE_PREFIX) :].strip()
        low = rest.lower()
        if "retry budget exhausted" in low:
            return "stale recovery: retry budget exhausted"
        if "previous process exited" in low:
            return "stale recovery: previous process exited mid-run"
        return f"stale recovery: {rest}"[:200]
    return key[:200]


def _default_opensoul_db() -> str:
    """与src.database.postgres.SQLitePool同款解析（sqlite:///相对路径按cwd解析，
    两者同进程同cwd→必然指向同一文件；非sqlite部署返回空串→collect显式skip）。"""
    try:
        from src.config import settings

        url = settings.database_url
    except Exception:  # 配置不可用时fail-safe：无默认库，collect显式skip
        return ""
    if not url.startswith("sqlite"):
        return ""
    path = url.replace("sqlite:///", "").replace("sqlite://", "")
    return str(Path(path).resolve())


class ExperienceCollector:
    """真实执行产物 → experiences表的采集器（同步sqlite3，调用方见：
    api/brain.py /evolve 每次分析前采集；will/job_handlers.py learn.collect_experiences
    后台作业）。source_ref确定性去重（msg:{id}/job:{id}/eval:{exp}:{case}），
    重复采集零副作用（agno idempotency_key语义）。"""

    def __init__(
        self,
        db_path: str = "",
        job_db_path: str = "",
        eval_db_path: str = "",
        tenant_id: str = "default",
        agent_id: str = "default",
    ):
        self.db_path = db_path or _default_opensoul_db()
        self.job_db_path = job_db_path or str(Path.home() / "opensoul" / "data" / "job_queue.db")
        self.eval_db_path = eval_db_path or str(
            Path.home() / ".hermes" / "opensoul" / "benchmark" / "eval_loop.db"
        )
        self.tenant_id = tenant_id
        self.agent_id = agent_id

    # ------------------------------------------------------------------
    # schema
    # ------------------------------------------------------------------
    def ensure_schema(self, conn: sqlite3.Connection):
        """experiences + user_feedback幂等建表。

        experiences为experience.py同款schema + source_ref列（provenance+去重键）。
        老库兼容：experience.py可能先建表（无source_ref）→ PRAGMA探测+ALTER
        （schema_doctor/_SCHEMA_FIXES同款幂等模式，重复执行安全）。
        user_feedback与mind/user_memory.py同款schema——SelfEvolution._analyze_feedback
        的查询目标，空表=诚实默认（无评分数据不伪造）。
        """
        conn.execute(
            """
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
                source_ref TEXT
            )
        """
        )
        cols = [row[1] for row in conn.execute("PRAGMA table_info(experiences)").fetchall()]
        if cols and "source_ref" not in cols:
            conn.execute("ALTER TABLE experiences ADD COLUMN source_ref TEXT")
        # partial UNIQUE：source_ref非空唯一；NULL允许多行（experience.py写入路径无source_ref）
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_source_ref "
            "ON experiences(source_ref) WHERE source_ref IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exp_tenant_agent ON experiences(tenant_id, agent_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exp_outcome ON experiences(tenant_id, agent_id, outcome)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL,
                feedback TEXT,
                rating INTEGER,
                created_at REAL NOT NULL
            )
        """
        )
        conn.commit()

    # ------------------------------------------------------------------
    # 写入helper
    # ------------------------------------------------------------------
    def _insert_exp(
        self,
        conn: sqlite3.Connection,
        *,
        action: str,
        intent_summary: str,
        outcome: str,
        error: str,
        created_at: float,
        source_ref: str,
        metadata: dict,
    ) -> bool:
        """写一条经验；source_ref已存在→跳过返回False（agno幂等键）。

        error入列前经normalize_error_key归一化（稳定分组键）；归一化改变了
        原文时metadata["error_raw"]保留原文（mem0审计：分组键稳定≠丢原文，
        provenance可追溯到作业/消息的原始错误）。
        """
        if source_ref:
            row = conn.execute(
                "SELECT 1 FROM experiences WHERE source_ref = ?", (source_ref,)
            ).fetchone()
            if row:
                return False
        error_key = normalize_error_key(error)
        meta = dict(metadata or {})
        if error and error_key != error.strip()[:200]:
            meta["error_raw"] = error[:200]
        conn.execute(
            """INSERT INTO experiences
               (tenant_id, agent_id, action, intent_summary, outcome, error, fix,
                relevance_score, created_at, metadata, source_ref)
               VALUES (?, ?, ?, ?, ?, ?, NULL, 1.0, ?, ?, ?)""",
            (
                self.tenant_id,
                self.agent_id,
                action[:500],
                intent_summary[:200],
                outcome,
                error_key or None,
                float(created_at or time.time()),
                json.dumps(meta, ensure_ascii=False),
                source_ref,
            ),
        )
        return True

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # 数据源
    # ------------------------------------------------------------------
    def _collect_from_messages(self, conn: sqlite3.Connection) -> dict:
        """agent_messages → 经验：assistant回合按显式失败签名判定outcome
        （TradingAgents outcome回填——真实回复即真实反馈信号）。
        intent_summary=同会话最近一条用户消息（回合的真实任务语境）。"""
        stats: dict = {"scanned": 0, "written": 0, "failures": 0, "successes": 0, "deduped": 0}
        if not self._table_exists(conn, "agent_messages"):
            stats["note"] = "agent_messages table missing"
            return stats
        rows = conn.execute(
            "SELECT id, session_id, role, content, timestamp FROM agent_messages "
            "ORDER BY session_id, id"
        ).fetchall()
        last_user: dict[str, str] = {}
        for row in rows:
            session_id = row["session_id"] or ""
            role = row["role"] or ""
            content = row["content"] or ""
            if role == "user":
                first_line = content.splitlines()[0].strip() if content.splitlines() else ""
                if first_line:
                    last_user[session_id] = first_line[:200]
                continue
            if role != "assistant":
                continue
            stats["scanned"] += 1
            outcome, error = _message_failure(content)
            if outcome == "failure":
                stats["failures"] += 1
            else:
                stats["successes"] += 1
            written = self._insert_exp(
                conn,
                action=f"chat_turn:{session_id}",
                intent_summary=last_user.get(session_id, "unknown"),
                outcome=outcome,
                error=error,
                created_at=row["timestamp"] or time.time(),
                source_ref=f"msg:{row['id']}",
                metadata={
                    "source": "agent_message",
                    "message_id": row["id"],
                    "session_id": session_id,
                },
            )
            if written:
                stats["written"] += 1
            else:
                stats["deduped"] += 1
        return stats

    def _collect_from_jobs(self) -> dict:
        """jobs（job_queue.db）→ 经验：作业真实执行结果（completed=success；
        failed/timeout/orphaned=failure带error原文；pending/running未有结果跳过
        ——TradingAgents"pending不冒充outcome"）。"""
        stats: dict = {
            "scanned": 0,
            "written": 0,
            "failures": 0,
            "successes": 0,
            "deduped": 0,
            "skipped_test": 0,
        }
        path = self.job_db_path
        if not path or not Path(path).exists():
            stats["note"] = "job_queue.db missing"
            return stats
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            if not self._table_exists(conn, "jobs"):
                stats["note"] = "jobs table missing"
                return stats
            try:
                rows = conn.execute(
                    "SELECT id, name, status, error, created_at, source FROM jobs"
                ).fetchall()
            except sqlite3.OperationalError:
                # 老库无source列（迁移前）：按real处理（fail-safe不误杀存量真实作业）
                rows = conn.execute(
                    "SELECT id, name, status, error, created_at FROM jobs"
                ).fetchall()
        finally:
            conn.close()
        main = sqlite3.connect(self.db_path)
        main.row_factory = sqlite3.Row
        try:
            self.ensure_schema(main)
            for row in rows:
                status = (row["status"] or "").lower()
                if status not in ("completed", "failed", "timeout", "orphaned"):
                    continue  # pending/running：无执行结果，不产经验
                # provenance门（learned_skills.source同款哲学）：source='test'的
                # 测试作业不产经验——systemic_test往生产API提交test_job/test_cancel/
                # stress_test曾污染job_queue.db 1092行→experiences 1092行→evolution_log
                # 279行假"高频失败"进化（"没有进化啊"的污染源之一）。
                # 只认schema标记，不做名字猜测（test_job_outcomes钉住的语义：
                # 名字不参与判定——清理责任在产生侧的source标记+自清）。
                src = (row["source"] or "real") if "source" in row.keys() else "real"
                if src == "test":
                    stats["skipped_test"] += 1
                    continue
                stats["scanned"] += 1
                outcome = "success" if status == "completed" else "failure"
                error = ""
                if outcome == "failure":
                    stats["failures"] += 1
                    error = (row["error"] or "").strip()[:200] or f"job_{status}"
                else:
                    stats["successes"] += 1
                written = self._insert_exp(
                    main,
                    action=f"job:{row['name']}",
                    intent_summary=f"background_job:{row['name']}",
                    outcome=outcome,
                    error=error,
                    created_at=row["created_at"] or time.time(),
                    source_ref=f"job:{row['id']}",
                    metadata={"source": "job_queue", "job_id": row["id"], "status": status},
                )
                if written:
                    stats["written"] += 1
                else:
                    stats["deduped"] += 1
            main.commit()
        finally:
            main.close()
        return stats

    def _collect_from_eval(self) -> dict:
        """eval_experiments（eval_loop.db）→ 经验：评估闭环逐case结论。
        agno语义：unscored case（超时/错误未评分）跳过不计outcome；
        case passed=全部scored attempt都passed。error带attempt错误原文
        （无原文时用确定性eval_case_failed:{dataset}:{case}分组键）。"""
        stats: dict = {"scanned": 0, "written": 0, "failures": 0, "successes": 0, "deduped": 0}
        path = self.eval_db_path
        if not path or not Path(path).exists():
            stats["note"] = "eval_loop.db missing"
            return stats
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            if not self._table_exists(conn, "eval_experiments"):
                stats["note"] = "eval_experiments table missing"
                return stats
            rows = conn.execute(
                "SELECT experiment_id, dataset_id, results, created_at FROM eval_experiments"
            ).fetchall()
        finally:
            conn.close()
        main = sqlite3.connect(self.db_path)
        main.row_factory = sqlite3.Row
        try:
            self.ensure_schema(main)
            for row in rows:
                try:
                    results = json.loads(row["results"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue  # 坏JSON跳过但不中断（mem0：其余照常采集）
                cases = results.get("cases") or {}
                for case_id, case_data in cases.items():
                    if not isinstance(case_data, dict):
                        continue
                    scored = int(case_data.get("scored_count") or 0)
                    if scored <= 0:
                        continue  # agno"超时≠答错"：unscored不产经验
                    stats["scanned"] += 1
                    passed = int(case_data.get("pass_count") or 0)
                    dataset_id = row["dataset_id"] or ""
                    if passed >= scored:
                        outcome, stats["successes"] = "success", stats["successes"] + 1
                        error = ""
                    else:
                        outcome = "failure"
                        stats["failures"] += 1
                        error = ""
                        for attempt in case_data.get("attempts") or []:
                            if isinstance(attempt, dict) and attempt.get("error"):
                                error = str(attempt["error"])[:200]
                                break
                        if not error:
                            error = f"eval_case_failed:{dataset_id}:{case_id}"
                    written = self._insert_exp(
                        main,
                        action=f"eval:{dataset_id}:{case_id}",
                        intent_summary=f"eval_case:{dataset_id}:{case_id}",
                        outcome=outcome,
                        error=error,
                        created_at=row["created_at"] or time.time(),
                        source_ref=f"eval:{row['experiment_id']}:{case_id}",
                        metadata={
                            "source": "eval_experiment",
                            "experiment_id": row["experiment_id"],
                            "case_id": case_id,
                        },
                    )
                    if written:
                        stats["written"] += 1
                    else:
                        stats["deduped"] += 1
            main.commit()
        finally:
            main.close()
        return stats

    # ------------------------------------------------------------------
    # 编排
    # ------------------------------------------------------------------
    def collect(self) -> dict:
        """采集全部数据源→experiences表，返回统计（可观测：每个源的
        scanned/written/failures/successes/deduped）。幂等：重复调用零重复写入。"""
        if not self.db_path:
            return {
                "status": "skipped",
                "reason": "database_url is not sqlite — experience collection requires sqlite",
                "written": 0,
            }
        stats: dict = {"status": "ok", "sources": {}, "written": 0, "deduped": 0}
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            self.ensure_schema(conn)
            msg_stats = self._collect_from_messages(conn)
            conn.commit()
            stats["sources"]["messages"] = msg_stats
        finally:
            conn.close()
        stats["sources"]["jobs"] = self._collect_from_jobs()
        stats["sources"]["eval_experiments"] = self._collect_from_eval()
        for src in stats["sources"].values():
            stats["written"] += int(src.get("written") or 0)
            stats["deduped"] += int(src.get("deduped") or 0)
        # 采集后总量（SelfEvolution分析输入规模，一条SQL可观测）
        try:
            check = sqlite3.connect(self.db_path)
            try:
                stats["experiences_total"] = check.execute(
                    "SELECT COUNT(*) FROM experiences WHERE tenant_id=? AND agent_id=?",
                    (self.tenant_id, self.agent_id),
                ).fetchone()[0]
            finally:
                check.close()
        except sqlite3.Error:
            pass
        return stats

    def get_stats(self) -> dict:
        """experiences表统计（monitoring/evolve响应可观测性）。"""
        if not self.db_path or not Path(self.db_path).exists():
            return {"total": 0, "note": "db missing"}
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            self.ensure_schema(conn)
            rows = conn.execute(
                "SELECT outcome, COUNT(*) AS cnt FROM experiences "
                "WHERE tenant_id=? AND agent_id=? GROUP BY outcome",
                (self.tenant_id, self.agent_id),
            ).fetchall()
            by_outcome = {r["outcome"]: r["cnt"] for r in rows}
            recent = conn.execute(
                "SELECT action, outcome, substr(COALESCE(error,''),1,80) AS error, created_at "
                "FROM experiences WHERE tenant_id=? AND agent_id=? "
                "ORDER BY created_at DESC LIMIT 5",
                (self.tenant_id, self.agent_id),
            ).fetchall()
            fb_count = conn.execute(
                "SELECT COUNT(*) FROM user_feedback WHERE tenant_id=? AND user_id=?",
                (self.tenant_id, self.agent_id),
            ).fetchone()[0]
            return {
                "total": sum(by_outcome.values()),
                "success_count": by_outcome.get("success", 0),
                "failure_count": by_outcome.get("failure", 0),
                "user_feedback_count": fb_count,
                "recent": [dict(r) for r in recent],
            }
        finally:
            conn.close()
