"""AI群管理API — 基于Graph层四角色模型(advisor/executor/verifier/human)"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api.ws_chat import run_agent_proxy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-groups", tags=["ai-groups"])


@router.get("/health")
async def ai_groups_health():
    """AIGroups health check."""
    return {"status": "ok", "component": "AIGroups"}


DB_PATH = Path.home() / "opensoul" / "data" / "ai_groups.db"


def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ai_groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_group_agents (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            model TEXT DEFAULT '',
            status TEXT DEFAULT 'online',
            FOREIGN KEY (group_id) REFERENCES ai_groups(id)
        );
        CREATE TABLE IF NOT EXISTS ai_group_tasks (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            parent_task_id TEXT,
            goal TEXT NOT NULL,
            constraints TEXT DEFAULT '[]',
            completion_criteria TEXT DEFAULT '[]',
            status TEXT DEFAULT 'planning',
            assigned_agent_id TEXT,
            result TEXT DEFAULT '',
            quality_score REAL DEFAULT 0,
            iteration INTEGER DEFAULT 0,
            max_iterations INTEGER DEFAULT 2,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES ai_groups(id)
        );
    """)
    conn.commit()
    # 为已有表添加 temperature 列（如果不存在）
    try:
        conn.execute("ALTER TABLE ai_group_agents ADD COLUMN temperature REAL DEFAULT 0.7")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 列已存在
    # 为已有表添加 capabilities 列（如果不存在）
    try:
        conn.execute("ALTER TABLE ai_group_agents ADD COLUMN capabilities TEXT DEFAULT '[]'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 列已存在

    # 讨论与评分相关表
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_capabilities (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            avg_score REAL DEFAULT 0,
            task_count INTEGER DEFAULT 0,
            trend TEXT DEFAULT 'stable',
            updated_at TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES ai_groups(id),
            UNIQUE(group_id, agent_id, capability)
        );
        CREATE TABLE IF NOT EXISTS agent_scores (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            scored_agent_id TEXT NOT NULL,
            scorer_agent_id TEXT NOT NULL,
            score INTEGER NOT NULL,
            reason TEXT DEFAULT '',
            capability TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES ai_groups(id)
        );
        CREATE TABLE IF NOT EXISTS discussion_messages (
            id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            agent_name TEXT DEFAULT '',
            intent TEXT DEFAULT 'comment',
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            round_num INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES ai_groups(id)
        );
    """)
    conn.commit()
    conn.close()


init_db()


# ── Pydantic Models ──────────────────────────────────────
class AgentRole(BaseModel):
    agent_id: str
    name: str
    role: str  # advisor / executor / verifier / human
    model: str = ""


class CreateGroupRequest(BaseModel):
    name: str
    description: str = ""
    agents: list[AgentRole] = []


class SubmitTaskRequest(BaseModel):
    goal: str
    constraints: list[str] = []
    completion_criteria: list[str] = []


class AssignTaskRequest(BaseModel):
    agent_id: str


class CompleteTaskRequest(BaseModel):
    result: str = ""
    quality_score: float = 0


class VerifyTaskRequest(BaseModel):
    passed: bool
    reason: str = ""


class UpdateGroupRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class UpdateAgentRequest(BaseModel):
    model: str | None = None
    temperature: float | None = None
    role: str | None = None


class StartDiscussionRequest(BaseModel):
    goal: str
    constraints: list[str] = []
    completion_criteria: list[str] = []


class DiscussionResponseRequest(BaseModel):
    agent_id: str
    agent_name: str = ""
    intent: str = "comment"  # claim/suggest/refer/comment
    content: str


class DiscussionAssignment(BaseModel):
    agent_id: str
    subgoal: str


class DecideDiscussionRequest(BaseModel):
    assignments: list[DiscussionAssignment]


class ReviewTaskRequest(BaseModel):
    result: str = ""


class ScoreTaskRequest(BaseModel):
    scorer_agent_id: str
    score: int
    reason: str = ""
    capability: str = ""


class AutoEvaluateRequest(BaseModel):
    """自动评估请求 — 由前端在任务执行完成后调用"""

    capability: str = ""  # 能力维度，空则自动推断
    success: bool = True  # 执行是否成功
    source: str = ""  # 执行来源 (local/remote)


# ── Helper ───────────────────────────────────────────────
def row_to_dict(row):
    return dict(row) if row else None


def rows_to_list(rows):
    return [dict(r) for r in rows]


def auto_decompose_task(goal: str) -> list[dict]:
    """自动分解任务为子任务（基于目标关键词分析）"""
    subtasks = []
    keywords_map = {
        "分析": {"goal": "需求分析与信息收集", "role": "advisor"},
        "设计": {"goal": "架构设计与方案制定", "role": "advisor"},
        "方案": {"goal": "方案编写与内容组织", "role": "executor"},
        "实现": {"goal": "代码实现与功能开发", "role": "executor"},
        "测试": {"goal": "质量验证与测试检查", "role": "verifier"},
        "审查": {"goal": "最终审查与质量把关", "role": "advisor"},
        "文档": {"goal": "文档编写与整理", "role": "executor"},
        "部署": {"goal": "部署上线与配置", "role": "executor"},
    }
    matched_roles = set()
    for kw, info in keywords_map.items():
        if kw in goal and info["role"] not in matched_roles:
            subtasks.append(info)
            matched_roles.add(info["role"])

    # 默认分解：规划→执行→验证→审查
    if len(subtasks) < 2:
        subtasks = [
            {"goal": f"规划：分析'{goal}'的需求和步骤", "role": "advisor"},
            {"goal": f"执行：按计划完成'{goal}'", "role": "executor"},
            {"goal": f"验证：检查'{goal}'的完成质量", "role": "verifier"},
            {"goal": f"审查：最终确认'{goal}'的结果", "role": "advisor"},
        ]
    return subtasks


def select_agent_for_role(conn, group_id: str, role: str) -> str | None:
    """为指定角色选择可用Agent"""
    row = conn.execute(
        "SELECT agent_id FROM ai_group_agents WHERE group_id=? AND role=? AND status='online' LIMIT 1",
        (group_id, role),
    ).fetchone()
    if row:
        return row["agent_id"]
    # 回退：选任意在线Agent
    row = conn.execute(
        "SELECT agent_id FROM ai_group_agents WHERE group_id=? AND status='online' LIMIT 1",
        (group_id,),
    ).fetchone()
    return row["agent_id"] if row else None


def update_agent_capability(group_id: str, agent_id: str, capability: str, score: int):
    """更新Agent能力画像（运行平均值 + 趋势计算）"""
    conn = get_db()
    try:
        now = datetime.now().isoformat()
        row = conn.execute(
            "SELECT * FROM agent_capabilities WHERE group_id=? AND agent_id=? AND capability=?",
            (group_id, agent_id, capability),
        ).fetchone()

        if row:
            old_avg = row["avg_score"]
            old_count = row["task_count"]
            new_count = old_count + 1
            new_avg = (old_avg * old_count + score) / new_count
            # 趋势：最近分数 vs 历史均值
            if score > old_avg + 0.5:
                trend = "up"
            elif score < old_avg - 0.5:
                trend = "down"
            else:
                trend = "stable"
            conn.execute(
                "UPDATE agent_capabilities SET avg_score=?, task_count=?, trend=?, updated_at=? WHERE id=?",
                (round(new_avg, 2), new_count, trend, now, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO agent_capabilities (id, group_id, agent_id, capability, avg_score, task_count, trend, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4())[:8], group_id, agent_id, capability, score, 1, "stable", now),
            )
        conn.commit()
    finally:
        conn.close()


def calculate_task_avg_score(task_id: str) -> float | None:
    """计算任务平均分（去掉最高最低分）"""
    conn = get_db()
    try:
        rows = conn.execute("SELECT score FROM agent_scores WHERE task_id=?", (task_id,)).fetchall()
        scores = [r["score"] for r in rows]
        if len(scores) == 0:
            return None
        if len(scores) <= 2:
            return round(sum(scores) / len(scores), 2)
        scores.sort()
        trimmed = scores[1:-1]
        return round(sum(trimmed) / len(trimmed), 2)
    finally:
        conn.close()


# ── Endpoints ────────────────────────────────────────────
@router.post("")
def create_group(req: CreateGroupRequest):
    conn = get_db()
    try:
        group_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO ai_groups (id, name, description, status, created_at) VALUES (?,?,?,?,?)",
            (group_id, req.name, req.description, "active", now),
        )
        for agent in req.agents:
            conn.execute(
                "INSERT INTO ai_group_agents (id, group_id, agent_id, name, role, model, status) VALUES (?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4())[:8],
                    group_id,
                    agent.agent_id,
                    agent.name,
                    agent.role,
                    agent.model,
                    "online",
                ),
            )
        conn.commit()
        return {"id": group_id, "name": req.name, "status": "active"}
    finally:
        conn.close()


@router.get("")
def list_groups():
    conn = get_db()
    try:
        groups = rows_to_list(
            conn.execute("SELECT * FROM ai_groups ORDER BY created_at DESC").fetchall()
        )
        for g in groups:
            g["agents"] = rows_to_list(
                conn.execute(
                    "SELECT * FROM ai_group_agents WHERE group_id=?", (g["id"],)
                ).fetchall()
            )
            g["task_count"] = conn.execute(
                "SELECT COUNT(*) as c FROM ai_group_tasks WHERE group_id=?", (g["id"],)
            ).fetchone()["c"]
        return groups
    finally:
        conn.close()


@router.get("/{group_id}")
def get_group(group_id: str):
    conn = get_db()
    try:
        group = row_to_dict(
            conn.execute("SELECT * FROM ai_groups WHERE id=?", (group_id,)).fetchone()
        )
        if not group:
            raise HTTPException(404, "AI群不存在")
        group["agents"] = rows_to_list(
            conn.execute("SELECT * FROM ai_group_agents WHERE group_id=?", (group_id,)).fetchall()
        )
        group["tasks"] = rows_to_list(
            conn.execute(
                "SELECT * FROM ai_group_tasks WHERE group_id=? ORDER BY created_at DESC",
                (group_id,),
            ).fetchall()
        )
        return group
    finally:
        conn.close()


@router.post("/{group_id}/tasks")
def submit_task(group_id: str, req: SubmitTaskRequest):
    conn = get_db()
    try:
        group = conn.execute("SELECT id FROM ai_groups WHERE id=?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "AI群不存在")

        now = datetime.now().isoformat()
        task_id = str(uuid.uuid4())[:8]

        # 创建主任务
        conn.execute(
            "INSERT INTO ai_group_tasks (id, group_id, goal, constraints, completion_criteria, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                task_id,
                group_id,
                req.goal,
                json.dumps(req.constraints, ensure_ascii=False),
                json.dumps(req.completion_criteria, ensure_ascii=False),
                "planning",
                now,
                now,
            ),
        )

        # 自动分解子任务
        subtasks = auto_decompose_task(req.goal)
        for st in subtasks:
            agent_id = select_agent_for_role(conn, group_id, st["role"])
            conn.execute(
                "INSERT INTO ai_group_tasks (id, group_id, parent_task_id, goal, status, assigned_agent_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4())[:8],
                    group_id,
                    task_id,
                    st["goal"],
                    "pending",
                    agent_id,
                    now,
                    now,
                ),
            )

        conn.commit()
        return {"task_id": task_id, "subtasks_count": len(subtasks), "status": "planning"}
    finally:
        conn.close()


@router.get("/{group_id}/tasks")
def list_tasks(group_id: str):
    conn = get_db()
    try:
        tasks = rows_to_list(
            conn.execute(
                "SELECT * FROM ai_group_tasks WHERE group_id=? AND parent_task_id IS NULL ORDER BY created_at DESC",
                (group_id,),
            ).fetchall()
        )
        for t in tasks:
            t["subtasks"] = rows_to_list(
                conn.execute(
                    "SELECT * FROM ai_group_tasks WHERE parent_task_id=? ORDER BY created_at",
                    (t["id"],),
                ).fetchall()
            )
            t["constraints"] = json.loads(t.get("constraints", "[]"))
            t["completion_criteria"] = json.loads(t.get("completion_criteria", "[]"))
        return tasks
    finally:
        conn.close()


@router.post("/{group_id}/tasks/{task_id}/assign")
def assign_task(group_id: str, task_id: str, req: AssignTaskRequest):
    conn = get_db()
    try:
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE ai_group_tasks SET assigned_agent_id=?, status='executing', updated_at=? WHERE id=? AND group_id=?",
            (req.agent_id, now, task_id, group_id),
        )
        conn.commit()
        return {"status": "executing", "assigned_to": req.agent_id}
    finally:
        conn.close()


class ExecuteTaskRequest(BaseModel):
    agent_id: str
    goal: str


def _resolve_agent_binary(agent_id: str, model: str = "") -> str:
    """将AI群组agent_id映射到实际CLI agent二进制名。

    群组里的agent_id如 executor-1/advisor-1 不是真实binary名，
    需要根据model字段或agent_id关键词推断应该调用哪个CLI agent。
    """
    import shutil

    # 直接匹配 — agent_id本身就是binary名
    if shutil.which(agent_id):
        return agent_id

    # 根据model字段推断
    model_lower = model.lower()
    model_to_agent = {
        "claude": "claude",
        "opus": "claude",
        "sonnet": "claude",
        "haiku": "claude",
        "gpt": "codex",
        "o4": "codex",
        "o3": "codex",
        "o1": "codex",
        "mimo": "mimo",
        "deepseek": "deepseek",
        "qwen": "qwen",
        "gemini": "gemini",
        "llama": "ollama",
        "qwen2": "ollama",
    }
    for keyword, binary in model_to_agent.items():
        if keyword in model_lower and shutil.which(binary):
            return binary

    # 根据agent_id关键词推断
    id_lower = agent_id.lower()
    id_to_agent = {
        "advisor": "hermes",
        "executor": "hermes",
        "verifier": "hermes",
        "claude": "claude",
        "gpt": "codex",
        "mimo": "mimo",
        "codex": "codex",
    }
    for keyword, binary in id_to_agent.items():
        if keyword in id_lower and shutil.which(binary):
            return binary

    # 默认用hermes
    if shutil.which("hermes"):
        return "hermes"

    return agent_id  # 最后回退


@router.post("/{group_id}/tasks/{task_id}/execute")
async def execute_task(group_id: str, task_id: str, req: ExecuteTaskRequest):
    """真正执行Agent任务 — 调用run_agent_proxy获取实际结果"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT * FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        # 查找agent的model信息用于resolve
        agent_row = conn.execute(
            "SELECT * FROM ai_group_agents WHERE group_id=? AND agent_id=?",
            (group_id, req.agent_id),
        ).fetchone()
        agent_model = agent_row["model"] if agent_row else ""

        now = datetime.now().isoformat()

        # 更新状态为executing
        conn.execute(
            "UPDATE ai_group_tasks SET assigned_agent_id=?, status='executing', updated_at=? WHERE id=?",
            (req.agent_id, now, task_id),
        )
        conn.commit()

        # 解析真实binary名并执行
        real_binary = _resolve_agent_binary(req.agent_id, agent_model)
        logger.info(f"Agent resolve: {req.agent_id} (model={agent_model}) -> {real_binary}")
        response_text, source, success = await run_agent_proxy(real_binary, req.goal)

        # 保存结果
        result_now = datetime.now().isoformat()
        conn.execute(
            "UPDATE ai_group_tasks SET result=?, status='reviewing', updated_at=? WHERE id=?",
            (response_text, result_now, task_id),
        )

        # 记录执行消息到讨论
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, metadata, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                req.agent_id,
                req.agent_id,
                "result",
                response_text[:2000],  # 截断过长内容
                json.dumps(
                    {"source": source, "success": success, "full_length": len(response_text)},
                    ensure_ascii=False,
                ),
                result_now,
            ),
        )

        # ── 自动评分：基于结果质量启发式打分 + 更新能力画像 ──
        goal_text = task["goal"] if "goal" in task.keys() else req.goal
        auto_score, auto_reason, capability = _auto_score_from_result(
            response_text, success, goal_text
        )
        conn.execute(
            "UPDATE ai_group_tasks SET quality_score=?, status='scored', updated_at=? WHERE id=?",
            (auto_score, result_now, task_id),
        )
        # 记录分数到agent_scores表
        conn.execute(
            "INSERT INTO agent_scores (id, task_id, group_id, scored_agent_id, scorer_agent_id, score, reason, capability, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                task_id,
                group_id,
                req.agent_id,
                "auto-scorer",
                auto_score,
                auto_reason,
                capability,
                result_now,
            ),
        )
        # 更新Agent能力画像
        # 必须先提交外层事务再调用（update_agent_capability开独立连接写库，
        # 外层未提交持有写锁时内层必死锁'sqlite3.OperationalError: database is locked'）
        conn.commit()
        update_agent_capability(group_id, req.agent_id, capability, auto_score)

        conn.commit()

        return {
            "status": "scored",
            "task_id": task_id,
            "agent_id": req.agent_id,
            "response": response_text,
            "source": source,
            "success": success,
            "auto_score": auto_score,
            "reason": auto_reason,
            "capability": capability,
        }
    finally:
        conn.close()


@router.post("/{group_id}/tasks/{task_id}/complete")
def complete_task(group_id: str, task_id: str, req: CompleteTaskRequest):
    conn = get_db()
    try:
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE ai_group_tasks SET result=?, quality_score=?, status='verifying', updated_at=? WHERE id=? AND group_id=?",
            (req.result, req.quality_score, now, task_id, group_id),
        )
        conn.commit()
        return {"status": "verifying", "quality_score": req.quality_score}
    finally:
        conn.close()


@router.post("/{group_id}/tasks/{task_id}/verify")
def verify_task(group_id: str, task_id: str, req: VerifyTaskRequest):
    conn = get_db()
    try:
        now = datetime.now().isoformat()
        task = conn.execute(
            "SELECT * FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        if req.passed:
            conn.execute(
                "UPDATE ai_group_tasks SET status='completed', updated_at=? WHERE id=?",
                (now, task_id),
            )
            # 检查所有子任务是否完成
            if task["parent_task_id"]:
                pending = conn.execute(
                    "SELECT COUNT(*) as c FROM ai_group_tasks WHERE parent_task_id=? AND status!='completed'",
                    (task["parent_task_id"],),
                ).fetchone()["c"]
                if pending == 0:
                    conn.execute(
                        "UPDATE ai_group_tasks SET status='completed', updated_at=? WHERE id=?",
                        (now, task["parent_task_id"]),
                    )
            conn.commit()
            return {"status": "completed"}
        else:
            iteration = task["iteration"] + 1
            if iteration >= task["max_iterations"]:
                conn.execute(
                    "UPDATE ai_group_tasks SET status='failed', iteration=?, updated_at=? WHERE id=?",
                    (iteration, now, task_id),
                )
                conn.commit()
                return {
                    "status": "failed",
                    "reason": req.reason,
                    "iteration": iteration,
                    "message": "已达最大迭代次数，升级到人工决策",
                }
            else:
                conn.execute(
                    "UPDATE ai_group_tasks SET status='executing', iteration=?, result='', updated_at=? WHERE id=?",
                    (iteration, now, task_id),
                )
                conn.commit()
                return {"status": "retry", "iteration": iteration, "reason": req.reason}
    finally:
        conn.close()


@router.delete("/{group_id}")
def delete_group(group_id: str):
    conn = get_db()
    try:
        conn.execute("DELETE FROM ai_group_tasks WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM ai_group_agents WHERE group_id=?", (group_id,))
        conn.execute("DELETE FROM ai_groups WHERE id=?", (group_id,))
        conn.commit()
        return {"status": "deleted"}
    finally:
        conn.close()


@router.post("/{group_id}/agents")
def add_agent(group_id: str, agent: AgentRole):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO ai_group_agents (id, group_id, agent_id, name, role, model, status) VALUES (?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                agent.agent_id,
                agent.name,
                agent.role,
                agent.model,
                "online",
            ),
        )
        conn.commit()
        return {"status": "added"}
    finally:
        conn.close()


@router.patch("/{group_id}")
def update_group(group_id: str, req: UpdateGroupRequest):
    conn = get_db()
    try:
        group = conn.execute("SELECT id FROM ai_groups WHERE id=?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "AI群不存在")
        updates, params = [], []
        if req.name is not None:
            updates.append("name=?")
            params.append(req.name)
        if req.description is not None:
            updates.append("description=?")
            params.append(req.description)
        if not updates:
            raise HTTPException(400, "没有需要更新的字段")
        params.append(group_id)
        conn.execute(f"UPDATE ai_groups SET {', '.join(updates)} WHERE id=?", params)
        conn.commit()
        return {"status": "updated"}
    finally:
        conn.close()


@router.delete("/{group_id}/agents/{agent_id}")
def remove_agent(group_id: str, agent_id: str):
    conn = get_db()
    try:
        result = conn.execute(
            "DELETE FROM ai_group_agents WHERE group_id=? AND agent_id=?", (group_id, agent_id)
        )
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(404, "Agent不存在")
        return {"status": "removed"}
    finally:
        conn.close()


@router.patch("/{group_id}/agents/{agent_id}")
def update_agent(group_id: str, agent_id: str, req: UpdateAgentRequest):
    conn = get_db()
    try:
        agent = conn.execute(
            "SELECT id FROM ai_group_agents WHERE group_id=? AND agent_id=?", (group_id, agent_id)
        ).fetchone()
        if not agent:
            raise HTTPException(404, "Agent不存在")
        updates, params = [], []
        if req.model is not None:
            updates.append("model=?")
            params.append(req.model)
        if req.temperature is not None:
            updates.append("temperature=?")
            params.append(req.temperature)
        if req.role is not None:
            updates.append("role=?")
            params.append(req.role)
        if not updates:
            raise HTTPException(400, "没有需要更新的字段")
        params.extend([group_id, agent_id])
        conn.execute(
            f"UPDATE ai_group_agents SET {', '.join(updates)} WHERE group_id=? AND agent_id=?",
            params,
        )
        conn.commit()
        return {"status": "updated"}
    finally:
        conn.close()


# ── Discussion & Scoring Endpoints ─────────────────────────


@router.post("/{group_id}/discuss")
def start_discussion(group_id: str, req: StartDiscussionRequest):
    """发起群组讨论式任务"""
    conn = get_db()
    try:
        group = conn.execute("SELECT id FROM ai_groups WHERE id=?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "AI群不存在")

        now = datetime.now().isoformat()
        task_id = str(uuid.uuid4())[:8]

        # 创建任务（状态为 discussing）
        conn.execute(
            "INSERT INTO ai_group_tasks (id, group_id, goal, constraints, completion_criteria, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                task_id,
                group_id,
                req.goal,
                json.dumps(req.constraints, ensure_ascii=False),
                json.dumps(req.completion_criteria, ensure_ascii=False),
                "discussing",
                now,
                now,
            ),
        )

        # 创建系统消息
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                "system",
                "系统",
                "comment",
                f"新任务已发布：{req.goal}。请各Agent讨论并认领子任务。",
                now,
            ),
        )

        conn.commit()
        return {"task_id": task_id, "status": "discussing", "round": 1}
    finally:
        conn.close()


@router.post("/{group_id}/discuss/{task_id}/respond")
def discussion_respond(group_id: str, task_id: str, req: DiscussionResponseRequest):
    """Agent提交讨论回复"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT id, status FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        if task["status"] != "discussing":
            raise HTTPException(400, "任务不在讨论状态")

        now = datetime.now().isoformat()

        # 计算当前轮次
        last = conn.execute(
            "SELECT MAX(round_num) as r FROM discussion_messages WHERE task_id=?", (task_id,)
        ).fetchone()
        current_round = (last["r"] or 1) if last else 1

        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, round_num, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                req.agent_id,
                req.agent_name,
                req.intent,
                req.content,
                current_round,
                now,
            ),
        )
        conn.commit()
        return {"status": "recorded", "round": current_round}
    finally:
        conn.close()


@router.post("/{group_id}/discuss/{task_id}/decide")
def decide_discussion(group_id: str, task_id: str, req: DecideDiscussionRequest):
    """结束讨论，分配子任务"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT id, status FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        now = datetime.now().isoformat()

        # 创建系统消息标记讨论结束
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                "system",
                "系统",
                "comment",
                f"讨论结束，共分配 {len(req.assignments)} 个子任务。",
                now,
            ),
        )

        # 创建子任务
        for assign in req.assignments:
            conn.execute(
                "INSERT INTO ai_group_tasks (id, group_id, parent_task_id, goal, status, assigned_agent_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4())[:8],
                    group_id,
                    task_id,
                    assign.subgoal,
                    "pending",
                    assign.agent_id,
                    now,
                    now,
                ),
            )

        # 更新主任务状态
        conn.execute(
            "UPDATE ai_group_tasks SET status='assigned', updated_at=? WHERE id=?",
            (now, task_id),
        )
        conn.commit()
        return {"status": "assigned", "subtasks_count": len(req.assignments)}
    finally:
        conn.close()


@router.get("/{group_id}/discuss/{task_id}/messages")
def get_discussion_messages(group_id: str, task_id: str):
    """获取任务讨论消息列表"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT id FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        messages = rows_to_list(
            conn.execute(
                "SELECT * FROM discussion_messages WHERE task_id=? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        )
        return messages
    finally:
        conn.close()


@router.post("/{group_id}/tasks/{task_id}/review")
def review_task(group_id: str, task_id: str, req: ReviewTaskRequest):
    """提交任务结果供群组评审"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT * FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE ai_group_tasks SET result=?, status='reviewing', updated_at=? WHERE id=?",
            (req.result, now, task_id),
        )

        # 创建系统消息通知各Agent评分
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                "system",
                "系统",
                "comment",
                f"任务「{task['goal']}」已完成，请各Agent对结果进行评分。",
                now,
            ),
        )

        conn.commit()
        return {"status": "reviewing", "task_id": task_id}
    finally:
        conn.close()


@router.post("/{group_id}/tasks/{task_id}/score")
def score_task(group_id: str, task_id: str, req: ScoreTaskRequest):
    """Agent对任务结果评分"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT * FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        if req.score < 1 or req.score > 10:
            raise HTTPException(400, "分数必须在1-10之间")

        now = datetime.now().isoformat()
        assigned_agent = task["assigned_agent_id"] or ""

        # 记录评分
        conn.execute(
            "INSERT INTO agent_scores (id, group_id, task_id, scored_agent_id, scorer_agent_id, score, reason, capability, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                assigned_agent,
                req.scorer_agent_id,
                req.score,
                req.reason,
                req.capability,
                now,
            ),
        )

        # 在讨论中记录评分消息
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, metadata, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                req.scorer_agent_id,
                "",
                "score",
                f"评分 {req.score}/10: {req.reason}",
                json.dumps(
                    {"score": req.score, "target_agent_id": assigned_agent}, ensure_ascii=False
                ),
                now,
            ),
        )

        conn.commit()

        # 计算平均分
        avg = calculate_task_avg_score(task_id)

        # 如果有能力和被评分Agent，更新能力画像
        if req.capability and assigned_agent:
            update_agent_capability(group_id, assigned_agent, req.capability, req.score)

        # 检查是否所有Agent都已评分
        agent_count = conn.execute(
            "SELECT COUNT(*) as c FROM ai_group_agents WHERE group_id=? AND status='online'",
            (group_id,),
        ).fetchone()["c"]
        score_count = conn.execute(
            "SELECT COUNT(DISTINCT scorer_agent_id) as c FROM agent_scores WHERE task_id=?",
            (task_id,),
        ).fetchone()["c"]

        # 所有Agent都评分后，更新任务状态
        if score_count >= agent_count and avg is not None:
            conn.execute(
                "UPDATE ai_group_tasks SET quality_score=?, status='scored', updated_at=? WHERE id=?",
                (avg, now, task_id),
            )
            conn.commit()

        return {"status": "scored", "avg_score": avg}
    finally:
        conn.close()


@router.get("/{group_id}/agents/{agent_id}/capabilities")
def get_agent_capabilities(group_id: str, agent_id: str):
    """获取Agent能力画像"""
    conn = get_db()
    try:
        caps = rows_to_list(
            conn.execute(
                "SELECT * FROM agent_capabilities WHERE group_id=? AND agent_id=? ORDER BY avg_score DESC",
                (group_id, agent_id),
            ).fetchall()
        )

        if not caps:
            return {
                "agent_id": agent_id,
                "overall_rank": 0,
                "capabilities": [],
                "strengths": [],
                "weaknesses": [],
            }

        # 整体排名 = 所有能力平均分的加权平均
        total_weight = sum(c["task_count"] for c in caps) or 1
        overall = sum(c["avg_score"] * c["task_count"] for c in caps) / total_weight

        strengths = [c["capability"] for c in caps if c["avg_score"] >= 7.5]
        weaknesses = [c["capability"] for c in caps if c["avg_score"] < 5.0]

        return {
            "agent_id": agent_id,
            "overall_rank": round(overall, 2),
            "capabilities": [
                {
                    "capability": c["capability"],
                    "avg_score": c["avg_score"],
                    "task_count": c["task_count"],
                    "trend": c["trend"],
                }
                for c in caps
            ],
            "strengths": strengths,
            "weaknesses": weaknesses,
        }
    finally:
        conn.close()


# ── Capability keyword mapping ──────────────────────────────────────
# Maps Chinese/English task keywords to capability dimensions
_CAPABILITY_KEYWORDS: dict[str, list[str]] = {
    "coding": [
        "代码",
        "编程",
        "开发",
        "实现",
        "编写",
        "code",
        "programming",
        "develop",
        "implement",
        "build",
        "写代码",
        "函数",
        "接口",
        "api",
        "bug",
        "修复",
        "fix",
    ],
    "writing": [
        "文档",
        "写作",
        "报告",
        "方案",
        "文章",
        "write",
        "document",
        "report",
        "article",
        "撰写",
        "编写文档",
        "README",
    ],
    "analysis": [
        "分析",
        "研究",
        "调研",
        "对比",
        "评估",
        "analyze",
        "research",
        "evaluate",
        "compare",
        "数据",
        "统计",
    ],
    "design": [
        "设计",
        "架构",
        "规划",
        "UI",
        "UX",
        "design",
        "architecture",
        "plan",
        "界面",
        "交互",
    ],
    "devops": [
        "部署",
        "运维",
        "配置",
        "容器",
        "docker",
        "deploy",
        "config",
        "server",
        "服务器",
        "nginx",
        "CI",
        "CD",
    ],
    "testing": ["测试", "验证", "检查", "test", "verify", "check", "质量", "QA", "单元测试"],
    "data": ["数据", "数据库", "SQL", "data", "database", "查询", "ETL", "pipeline"],
    "security": ["安全", "加密", "权限", "认证", "security", "auth", "encrypt", "RBAC"],
    "frontend": [
        "前端",
        "页面",
        "组件",
        "React",
        "CSS",
        "HTML",
        "frontend",
        "page",
        "component",
        "UI组件",
    ],
    "backend": ["后端", "服务", "微服务", "API", "backend", "service", "FastAPI", "Express"],
}


class SmartAssignRequest(BaseModel):
    goal: str
    constraints: list[str] = []


def _extract_capability_tags(goal: str) -> list[str]:
    """Extract capability dimension tags from a task goal string."""
    goal_lower = goal.lower()
    matched = []
    for cap, keywords in _CAPABILITY_KEYWORDS.items():
        if any(kw.lower() in goal_lower for kw in keywords):
            matched.append(cap)
    # If nothing matched, default to 'coding' as the most general
    return matched if matched else ["coding"]


@router.post("/{group_id}/smart-assign")
async def smart_assign(group_id: str, req: SmartAssignRequest):
    """智能分配 — 基于Agent能力画像匹配任务到最佳Agent。

    1. 从goal提取能力维度标签
    2. 查询群组所有Agent的能力画像
    3. 按匹配度排序，返回推荐分配方案
    """
    conn = get_db()
    try:
        group = conn.execute("SELECT id FROM ai_groups WHERE id=?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "群组不存在")

        agents = rows_to_list(
            conn.execute("SELECT * FROM ai_group_agents WHERE group_id=?", (group_id,)).fetchall()
        )
        if not agents:
            raise HTTPException(400, "群组无Agent")

        tags = _extract_capability_tags(req.goal)
        logger.info(f"Smart assign: goal='{req.goal[:50]}' → tags={tags}")

        # Score each agent
        scored_agents = []
        for agent in agents:
            agent_id = agent["agent_id"]
            role = agent.get("role", "executor")

            # Get capability scores for this agent
            caps = rows_to_list(
                conn.execute(
                    "SELECT capability, avg_score, task_count FROM agent_capabilities WHERE group_id=? AND agent_id=?",
                    (group_id, agent_id),
                ).fetchall()
            )

            # Calculate match score
            if caps:
                cap_map = {c["capability"]: c for c in caps}
                # Matched capabilities with scores
                matched_scores = []
                for tag in tags:
                    if tag in cap_map:
                        matched_scores.append(cap_map[tag]["avg_score"])
                    # Also check partial matches
                    for cap_name, cap_data in cap_map.items():
                        if tag in cap_name or cap_name in tag:
                            if cap_data["avg_score"] not in matched_scores:
                                matched_scores.append(cap_data["avg_score"])

                if matched_scores:
                    avg_match = sum(matched_scores) / len(matched_scores)
                    # Bonus for having more matching capabilities
                    match_ratio = len(matched_scores) / len(tags) if tags else 0
                    suitability = avg_match * 0.7 + match_ratio * 3  # max ~10
                else:
                    # Has capabilities but none match → use overall average
                    all_scores = [c["avg_score"] for c in caps]
                    suitability = sum(all_scores) / len(all_scores) * 0.5  # penalize mismatch
            else:
                # No capability data → neutral score (new agent)
                suitability = 5.0

            scored_agents.append(
                {
                    "agent_id": agent_id,
                    "agent_name": agent.get("name", agent_id),
                    "role": role,
                    "suitability": round(suitability, 2),
                    "matched_capabilities": [
                        t for t in tags if any(t in c["capability"] for c in caps)
                    ]
                    if caps
                    else [],
                    "overall_rank": round(sum(c["avg_score"] for c in caps) / len(caps), 2)
                    if caps
                    else 0,
                    "task_count": sum(c["task_count"] for c in caps) if caps else 0,
                }
            )

        # Sort by suitability descending
        scored_agents.sort(key=lambda a: a["suitability"], reverse=True)

        # Build assignment plan
        executors = [a for a in scored_agents if a["role"] == "executor"]
        advisors = [a for a in scored_agents if a["role"] == "advisor"]
        verifiers = [a for a in scored_agents if a["role"] == "verifier"]

        assignments = []
        reasoning = []

        if executors:
            # Pick the best executor
            best = executors[0]
            assignments.append(
                {
                    "agent_id": best["agent_id"],
                    "subgoal": req.goal,
                    "reason": f"最佳匹配: 能力评分{best['suitability']}, 已完成{best['task_count']}个任务",
                }
            )
            reasoning.append(f"执行者选择 {best['agent_name']} (匹配度{best['suitability']})")

            # If task is complex (long goal), add a second executor for parallel work
            if len(req.goal) > 50 and len(executors) > 1:
                second = executors[1]
                assignments.append(
                    {
                        "agent_id": second["agent_id"],
                        "subgoal": f"辅助完成: {req.goal}",
                        "reason": f"辅助执行: 能力评分{second['suitability']}",
                    }
                )
                reasoning.append(
                    f"辅助执行者 {second['agent_name']} (匹配度{second['suitability']})"
                )

        if advisors:
            best_advisor = advisors[0]
            assignments.append(
                {
                    "agent_id": best_advisor["agent_id"],
                    "subgoal": f"审查任务执行结果: {req.goal}",
                    "reason": f"审查: 能力评分{best_advisor['suitability']}",
                }
            )
            reasoning.append(f"审查者 {best_advisor['agent_name']}")

        if verifiers:
            best_verifier = verifiers[0]
            assignments.append(
                {
                    "agent_id": best_verifier["agent_id"],
                    "subgoal": f"验证任务完成质量: {req.goal}",
                    "reason": f"验证: 能力评分{best_verifier['suitability']}",
                }
            )
            reasoning.append(f"验证者 {best_verifier['agent_name']}")

        return {
            "task_tags": tags,
            "agents_ranked": scored_agents,
            "assignments": assignments,
            "reasoning": reasoning,
        }
    finally:
        conn.close()


# ── Auto-Evaluate: close the feedback loop ─────────────────────────


def _auto_score_from_result(result_text: str, success: bool, goal: str) -> tuple[int, str, str]:
    """基于执行结果自动评分，返回 (score, reason, capability)。

    评分逻辑：
    - 执行失败 → 3分
    - 结果为空/过短 → 4分
    - 结果有内容但很短 → 6分
    - 结果充实 → 7-9分
    - 结果非常完整且包含结构化内容 → 9分
    """
    # Auto-detect capability from goal
    tags = _extract_capability_tags(goal)
    capability = tags[0] if tags else "coding"

    if not success:
        return 3, "执行失败: Agent未返回有效结果", capability

    text = (result_text or "").strip()
    if not text:
        return 4, "执行结果为空", capability

    length = len(text)

    # Quality signals
    has_structure = any(marker in text for marker in ["##", "```", "| ", "- ", "1.", "2."])
    has_code = "```" in text
    has_explanation = length > 200
    is_comprehensive = length > 800
    is_very_long = length > 2000

    if is_very_long and has_structure:
        score = 9
        reason = f"结果非常完整({length}字符)，包含结构化内容"
    elif is_comprehensive and has_structure:
        score = 8
        reason = f"结果充实({length}字符)，有良好结构"
    elif is_comprehensive:
        score = 7
        reason = f"结果充实({length}字符)"
    elif has_explanation and has_code:
        score = 7
        reason = f"包含代码和说明({length}字符)"
    elif has_explanation:
        score = 6
        reason = f"结果有说明但不够详细({length}字符)"
    elif length > 50:
        score = 5
        reason = f"结果较短({length}字符)"
    else:
        score = 4
        reason = f"结果过短({length}字符)，可能未完成任务"

    return score, reason, capability


@router.post("/{group_id}/tasks/{task_id}/auto-evaluate")
async def auto_evaluate_task(group_id: str, task_id: str, req: AutoEvaluateRequest):
    """自动评估任务结果 — 无需人工干预，直接生成评分并更新能力画像。

    评分维度：
    1. 执行成功/失败
    2. 结果长度和结构化程度
    3. 内容质量信号（代码、解释、格式）

    自动触发能力画像更新，完成闭环。
    """
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT * FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")

        assigned_agent = task["assigned_agent_id"]
        if not assigned_agent:
            raise HTTPException(400, "任务未分配Agent")

        result_text = task["result"] or ""
        goal = task["goal"] or ""

        # Calculate auto score
        score, reason, capability = _auto_score_from_result(result_text, req.success, goal)

        # Use user-specified capability if provided
        if req.capability:
            capability = req.capability

        now = datetime.now().isoformat()

        # Insert auto-score record
        conn.execute(
            "INSERT INTO agent_scores (id, task_id, group_id, scored_agent_id, scorer_agent_id, score, reason, capability, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                task_id,
                group_id,
                assigned_agent,
                "auto-evaluator",
                score,
                reason,
                capability,
                now,
            ),
        )

        # Update capability profile
        # 先提交外层事务再调用（独立连接写库，防嵌套连接死锁 database is locked）
        conn.commit()
        update_agent_capability(group_id, assigned_agent, capability, score)

        # Mark task as scored
        conn.execute(
            "UPDATE ai_group_tasks SET quality_score=?, status='scored', updated_at=? WHERE id=?",
            (float(score), now, task_id),
        )

        # Record auto-evaluate message in discussion
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, metadata, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                task_id,
                "auto-evaluator",
                "Auto-Evaluator",
                "score",
                f"📊 自动评分: {score}/10\n能力维度: {capability}\n{reason}",
                json.dumps(
                    {"auto": True, "score": score, "capability": capability}, ensure_ascii=False
                ),
                now,
            ),
        )

        conn.commit()

        # Get updated capability profile
        caps = rows_to_list(
            conn.execute(
                "SELECT capability, avg_score, task_count, trend FROM agent_capabilities WHERE group_id=? AND agent_id=?",
                (group_id, assigned_agent),
            ).fetchall()
        )

        return {
            "status": "scored",
            "auto_score": score,
            "reason": reason,
            "capability": capability,
            "agent_id": assigned_agent,
            "capability_profile": caps,
        }
    finally:
        conn.close()
