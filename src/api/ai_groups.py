"""AI群管理API — 基于Graph层四角色模型(advisor/executor/verifier/human)"""

import asyncio
import json
import logging
import re
import sqlite3
import statistics
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.api.ws_chat import (
    _PROXY_HEARTBEAT_S,
    cancel_proxy_run,
    find_proxy_run,
    finish_proxy_run,
    register_proxy_run,
    run_agent_proxy,
    which_agent,
)

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
            source_task_id TEXT DEFAULT '',
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
    # B2 为已有表添加 source_task_id 列（复盘讨论回链源任务；ALTER幂等）
    try:
        conn.execute("ALTER TABLE ai_group_tasks ADD COLUMN source_task_id TEXT DEFAULT ''")
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

    # 执行过程事件表（Paperclip式任务执行可视化：逐行落库，供ws_group尾随广播+事后回放）
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS task_execution_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            agent_id TEXT DEFAULT '',
            event_type TEXT NOT NULL,
            content TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tee_task ON task_execution_events(task_id, id);
        CREATE INDEX IF NOT EXISTS idx_tee_group ON task_execution_events(group_id, id);
    """)
    conn.commit()
    conn.close()


init_db()


def _emit_exec_event(group_id: str, task_id: str, agent_id: str, event_type: str, content: str):
    """写入一条执行过程事件（独立短连接，立即提交，供ws_group尾随广播+回放）"""
    try:
        c = get_db()
        c.execute(
            "INSERT INTO task_execution_events (group_id, task_id, agent_id, event_type, content, created_at) VALUES (?,?,?,?,?,?)",
            (group_id, task_id, agent_id, event_type, content[:2000], datetime.now().isoformat()),
        )
        c.commit()
        c.close()
    except Exception as e:
        logger.warning(f"emit_exec_event failed: {e}")


# ── ETA预估 & 卡死检测（只提醒不杀任务，不设硬超时） ──────
_HEAVY_GOAL_VERBS = ("开发", "实现", "分析", "测试", "研究")
_DEFAULT_ETA_RANGE = (20, 90)  # 无历史数据时的默认预估区间（秒）
_STALLED_AFTER_SECONDS = 600  # 执行中任务10分钟零事件视为疑似卡死


def _parse_ts(value) -> datetime | None:
    """解析ISO时间戳字符串，失败返回None"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _finished_duration_history(conn, agent_id: str, limit: int = 20) -> list[float]:
    """历史基准数据：finished事件对应任务的实际耗时（秒），按agent_id分组取最近limit条。
    优先 started→finished 事件时间差，缺失时回退 tasks.updated_at-created_at 近似。
    指定agent无历史时回退到全局历史。"""
    rows = conn.execute(
        """SELECT e.agent_id, e.created_at AS finished_at,
                  (SELECT MIN(s.created_at) FROM task_execution_events s
                     WHERE s.task_id = e.task_id AND s.event_type = 'started') AS started_at,
                  t.created_at AS task_created_at, t.updated_at AS task_updated_at
             FROM task_execution_events e
             LEFT JOIN ai_group_tasks t ON t.id = e.task_id
            WHERE e.event_type = 'finished'
            ORDER BY e.id DESC LIMIT 400"""
    ).fetchall()
    per_agent: dict[str, list[float]] = {}
    global_hist: list[float] = []
    for r in rows:
        start = _parse_ts(r["started_at"]) or _parse_ts(r["task_created_at"])
        end = _parse_ts(r["finished_at"]) or _parse_ts(r["task_updated_at"])
        if not start or not end:
            continue
        secs = (end - start).total_seconds()
        if not (0 < secs <= 7200):  # 过滤非正数/异常离群值
            continue
        aid = (r["agent_id"] or "").strip()
        bucket = per_agent.setdefault(aid, [])
        if len(bucket) < limit:
            bucket.append(secs)
        if len(global_hist) < limit:
            global_hist.append(secs)
    if agent_id and per_agent.get(agent_id):
        return per_agent[agent_id]
    return global_hist


def _estimate_eta_seconds(task_row, events_hist) -> tuple[int, int]:
    """任务ETA预估（纯函数，不做I/O）：历史基准(events_hist为该agent最近finished耗时秒数列表)
    × 特征修正系数。仅用于展示提醒，不设硬超时、不杀任务。
    修正规则：goal>60字或含开发/实现/分析/测试/研究等动词 ×2.5；子任务数>2 ×1.3。
    无历史时默认 (20, 90) 秒。返回 (low, high)。"""
    d = dict(task_row)
    goal = d.get("goal") or ""
    subs = d.get("subtasks")
    subtask_count = (
        len(subs) if isinstance(subs, (list, tuple)) else int(d.get("subtask_count") or 0)
    )
    mult = 1.0
    if len(goal) > 60 or any(v in goal for v in _HEAVY_GOAL_VERBS):
        mult *= 2.5
    if subtask_count > 2:
        mult *= 1.3
    if events_hist:
        mean = statistics.mean(events_hist)
        median = statistics.median(events_hist)
        base_low, base_high = min(mean, median), max(mean, median) * 1.2
    else:
        base_low, base_high = _DEFAULT_ETA_RANGE
    return (max(1, round(base_low * mult)), max(2, round(base_high * mult)))


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
    discuss_after: bool = False  # B2：任务链跑完自动发起复盘讨论


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


class DiscussAfterRequest(BaseModel):
    """B2：任务执行完发起复盘讨论（goal为空则自动生成复盘主题）"""

    goal: str = ""


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
async def submit_task(group_id: str, req: SubmitTaskRequest):
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
        # 自动链式执行：规划→执行→验证→审查（每步结果回群聊）
        # B2联动：discuss_after=True时链跑完自动发起复盘讨论
        asyncio.create_task(_run_task_chain(group_id, task_id, discuss_after=req.discuss_after))
        return {"task_id": task_id, "subtasks_count": len(subtasks), "status": "planning"}
    finally:
        conn.close()


# ── B1 旧任务/子任务状态治理（历史pending清理+父任务收口，看板数据可信） ──
_TASK_TERMINAL_STATUSES = ("scored", "failed", "completed", "skipped")
_GOVERNABLE_OPEN_STATUSES = ("pending", "planning", "discussing")
_GOVERN_TTL_HOURS = 24  # 未启动任务超龄作废时限；executing/reviewing永不在此强杀


def _as_naive_local(dt: datetime | None) -> datetime | None:
    """时间戳归一：带时区转本地naive。存量行有+00:00格式，aware/naive相减会TypeError"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _insert_govern_event(conn, group_id: str, task_id: str, content: str):
    """治理留痕——必须走同一连接：外层事务未提交时另开连接写库必死锁（SQLite教训）"""
    conn.execute(
        "INSERT INTO task_execution_events (group_id, task_id, agent_id, event_type, content, created_at) VALUES (?,?,?,?,?,?)",
        (group_id, task_id, "governor", "governed", content[:2000], datetime.now().isoformat()),
    )


def _finalize_parent_task(conn, parent_task_id: str) -> str | None:
    """R3 父任务收口：子任务全部终结但父任务卡planning → scored/failed/skipped。"""
    parent = conn.execute(
        "SELECT * FROM ai_group_tasks WHERE id=? AND parent_task_id IS NULL",
        (parent_task_id,),
    ).fetchone()
    if not parent or parent["status"] not in _GOVERNABLE_OPEN_STATUSES:
        return None
    subs = conn.execute(
        "SELECT status, quality_score FROM ai_group_tasks WHERE parent_task_id=?",
        (parent_task_id,),
    ).fetchall()
    if not subs or any(s["status"] not in _TASK_TERMINAL_STATUSES for s in subs):
        return None
    statuses = [s["status"] for s in subs]
    if all(s == "skipped" for s in statuses):
        new_status = "skipped"
    elif "failed" in statuses:
        new_status = "failed"
    else:
        new_status = "scored"
    scores = [
        float(s["quality_score"] or 0) for s in subs if s["status"] in ("scored", "completed")
    ]
    avg = round(statistics.mean(scores), 2) if scores else 0.0
    conn.execute(
        "UPDATE ai_group_tasks SET status=?, quality_score=?, updated_at=? WHERE id=?",
        (new_status, avg, datetime.now().isoformat(), parent_task_id),
    )
    _insert_govern_event(
        conn,
        parent["group_id"],
        parent_task_id,
        f"chain_finalized: {parent['status']}->{new_status} subtasks={len(subs)} avg_score={avg}",
    )
    return new_status


def govern_stale_tasks(
    conn, group_id: str | None = None, ttl_hours: float = _GOVERN_TTL_HOURS
) -> dict:
    """B1 旧任务/子任务状态治理：历史pending清理+父任务收口，看板数据可信。

    R1 孤儿子任务：open子任务的父任务已终结（链永远不会跑到它）→ skipped
    R3 父任务收口：子任务全部终结但父任务卡planning → scored（子任务均分）/
       failed（任一failed）/skipped（全部skipped）。先于R2跑：真跑完的链要如实
       收口，不能被超龄清扫误杀成skipped。
    R2 超龄未启动：pending/planning/discussing超过TTL零进展 → skipped
    只清"从未启动"的行；executing/reviewing永不在此强杀（卡死只提醒不杀任务）。
    每次状态变化都在task_execution_events留痕（governed事件），禁静默改状态。
    """
    now = datetime.now()
    cutoff = now - timedelta(hours=ttl_hours)
    where = " WHERE group_id=?" if group_id else ""
    params = (group_id,) if group_id else ()
    rows = conn.execute(f"SELECT * FROM ai_group_tasks{where}", params).fetchall()
    stats = {"skipped_parent_finished": 0, "skipped_stale": 0, "parent_finalized": 0}
    details: list[dict] = []
    by_id = {r["id"]: r for r in rows}
    handled: set[str] = set()  # 已治理行id：内存快照陈旧，防R2重复治理R1已处理的行

    def _skip(row, reason: str, stat_key: str):
        handled.add(row["id"])
        conn.execute(
            "UPDATE ai_group_tasks SET status='skipped', updated_at=? WHERE id=?",
            (now.isoformat(), row["id"]),
        )
        _insert_govern_event(
            conn, row["group_id"], row["id"], f"{reason}: {row['status']}->skipped (never started)"
        )
        stats[stat_key] += 1
        details.append({"task_id": row["id"], "action": "skipped", "reason": reason})

    # R1 孤儿子任务：父任务已终结（含R2刚skipped的父任务），子任务永远轮不到跑
    for r in rows:
        if r["status"] not in _GOVERNABLE_OPEN_STATUSES or not r["parent_task_id"]:
            continue
        parent = by_id.get(r["parent_task_id"])
        if parent and parent["status"] in _TASK_TERMINAL_STATUSES:
            _skip(r, "parent_finished", "skipped_parent_finished")

    # R3 父任务收口（子任务状态以库为准，含刚被R1改过的行）
    def _finalize_all():
        for r in rows:
            if r["parent_task_id"] is not None:
                continue
            if r["status"] not in _GOVERNABLE_OPEN_STATUSES:
                continue
            new_status = _finalize_parent_task(conn, r["id"])
            if new_status:
                stats["parent_finalized"] += 1
                details.append({"task_id": r["id"], "action": "finalized", "status": new_status})

    _finalize_all()
    # R2 超龄未启动清扫（R3之后：真跑完的链先如实收口）
    for r in rows:
        if r["id"] in handled or r["status"] not in _GOVERNABLE_OPEN_STATUSES:
            continue
        last = _as_naive_local(_parse_ts(r["updated_at"])) or _as_naive_local(
            _parse_ts(r["created_at"])
        )
        if last and last < cutoff:
            _skip(r, f"stale_ttl_{int(ttl_hours)}h", "skipped_stale")
    # R3尾部复跑：R2清完超龄子任务后，其父任务若已全终结立即收口（单次扫描即收敛）
    _finalize_all()
    return {**stats, "details": details}


@router.get("/{group_id}/tasks")
def list_tasks(group_id: str):
    conn = get_db()
    try:
        # B1惰性治理：看板读取前先清扫旧任务/子任务（与POST .../tasks/govern同一函数），
        # 历史pending不再冒充"待处理"，看板数据可信
        govern_stale_tasks(conn, group_id)
        conn.commit()
        tasks = rows_to_list(
            conn.execute(
                "SELECT * FROM ai_group_tasks WHERE group_id=? AND parent_task_id IS NULL ORDER BY created_at DESC",
                (group_id,),
            ).fetchall()
        )
        # 一次性取每任务最后事件时间 + 按agent分组的历史耗时（ETA/卡死检测用）
        last_events = {
            r["task_id"]: r["last_at"]
            for r in conn.execute(
                "SELECT task_id, MAX(created_at) AS last_at FROM task_execution_events WHERE group_id=? GROUP BY task_id",
                (group_id,),
            ).fetchall()
        }
        hist_cache: dict[str, list[float]] = {}

        def _hist_for(agent_id: str) -> list[float]:
            key = agent_id or ""
            if key not in hist_cache:
                hist_cache[key] = _finished_duration_history(conn, key)
            return hist_cache[key]

        def _annotate(t: dict):
            low, high = _estimate_eta_seconds(t, _hist_for(t.get("assigned_agent_id") or ""))
            t["estimated_low_s"] = low
            t["estimated_high_s"] = high
            t["last_event_at"] = last_events.get(t["id"])
            # 卡死检测：执行中且>600秒零事件 → stalled=true（只提醒，不杀任务）
            stalled = False
            if t.get("status") == "executing":
                last = _parse_ts(t["last_event_at"]) or _parse_ts(t.get("updated_at"))
                if last and (datetime.now() - last).total_seconds() > _STALLED_AFTER_SECONDS:
                    stalled = True
            t["stalled"] = stalled

        for t in tasks:
            t["subtasks"] = rows_to_list(
                conn.execute(
                    "SELECT * FROM ai_group_tasks WHERE parent_task_id=? ORDER BY created_at",
                    (t["id"],),
                ).fetchall()
            )
            for st in t["subtasks"]:
                st["subtask_count"] = 0
                _annotate(st)
            t["constraints"] = json.loads(t.get("constraints", "[]"))
            t["completion_criteria"] = json.loads(t.get("completion_criteria", "[]"))
            _annotate(t)
        return tasks
    finally:
        conn.close()


@router.post("/{group_id}/tasks/govern")
def govern_tasks(group_id: str):
    """手动触发旧任务/子任务状态治理（list_tasks读取时惰性清扫的同一函数），返回治理统计。"""
    conn = get_db()
    try:
        stats = govern_stale_tasks(conn, group_id)
        conn.commit()
        return {"group_id": group_id, **stats}
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
    goal: str = ""  # 为空时用任务行自身的goal（子任务单独执行/重试场景）


class RetryTaskRequest(BaseModel):
    agent_id: str = ""


_ROLE_SLOT_RE = re.compile(r"^(advisor|executor|verifier|human)(-\d+)?$", re.IGNORECASE)


def _resolve_agent_binary(agent_id: str, model: str = "") -> str:
    """将AI群组agent_id映射到实际CLI agent二进制名（2026-10-11 独立性铁律重写）。

    两类 id 两种语义：
    - 角色槽位（advisor-1/executor/verifier…）：不是真实身份，按 model/关键词映射 CLI；
    - 真实身份（soulmate/hermes/mimo/…，含 claude-2 这类实例后缀）：**只认自己**，
      找不到就原样返回、由下游显式报"Agent未安装"。

    旧版尾部"默认用hermes"兜底把 mimo/soulmate 成员的任务静默变身 hermes 大脑
    （周期#53 e2e 实证：journal "Agent resolve: mimo -> hermes"），违反
    "每个agent都应该是独立的"铁律——已删除，禁止任何冒名兜底。"""
    aid = (agent_id or "").strip()
    if not aid:
        return agent_id

    # 直接匹配 — agent_id本身就是binary名（含用户安装目录，修 systemd PATH 盲区）
    if which_agent(aid):
        return aid

    # 实例后缀身份（claude-2/mimo-1）：剥后缀找本体，仍只认自己
    m = re.match(r"^(.+?)-\d+$", aid)
    if m and which_agent(m.group(1)):
        return m.group(1)

    # 真实身份绝不映射成别的 agent（找不到=显式未安装，不变身）
    if not _ROLE_SLOT_RE.match(aid):
        return agent_id

    # ── 以下仅角色槽位：按 model 字段推断 ──
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
        if keyword in model_lower and which_agent(binary):
            return binary

    # 角色关键词推断（advisor/executor/verifier 槽位 → 常规CLI）
    id_lower = aid.lower()
    id_to_agent = {
        "advisor": "hermes",
        "executor": "hermes",
        "verifier": "hermes",
    }
    for keyword, binary in id_to_agent.items():
        if keyword in id_lower and which_agent(binary):
            return binary

    return agent_id  # 槽位无可用映射 → 原样返回，下游显式报"Agent未安装"


def _channel_desc(agent_id: str, real_binary: str) -> str:
    """started 事件的执行通道标签——按 agent 独立性铁律如实标注真实通道。

    soulmate 子任务走自己的 ACP WS（_run_soulmate_turn），不是 CLI，
    事件里禁显示 "binary=hermes"（_resolve_agent_binary 的信息位推断会误导观测）。"""
    if (agent_id or "").lower() == "soulmate":
        return "channel=soulmate-acp"
    return f"binary={real_binary}"


# 任务执行并发保护（异步202模式）：同task_id同时只允许一个执行
_RUNNING_TASKS: set = set()


async def _run_task_background(group_id: str, task_id: str, req: "ExecuteTaskRequest"):
    """后台执行包装：异常兜底转failed事件，finally释放并发锁"""
    try:
        await _execute_task_impl(group_id, task_id, req)
    except Exception as e:
        logger.error(f"task {task_id} execution error: {e}")
        _emit_exec_event(
            group_id, task_id, req.agent_id, "failed", f"执行异常: {type(e).__name__}: {e}"
        )
    finally:
        _RUNNING_TASKS.discard(task_id)


@router.post("/{group_id}/tasks/{task_id}/execute")
async def execute_task(group_id: str, task_id: str, req: ExecuteTaskRequest):
    """执行任务（异步）：立即返回202，执行进度靠task_execution_events+WS广播task_progress"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT id FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
    finally:
        conn.close()
    if task_id in _RUNNING_TASKS:
        raise HTTPException(409, "task already executing")
    _RUNNING_TASKS.add(task_id)
    asyncio.create_task(_run_task_background(group_id, task_id, req))
    return JSONResponse(
        {"status": "executing", "task_id": task_id, "message": "已开始执行，进度见事件流"},
        status_code=202,
    )


SOULMATE_WS = "ws://127.0.0.1:8092/ws/acp"
GROUP_MSG_PUSH = "http://127.0.0.1:8092/internal/group-message"


async def _notify_group_chat(
    group_id: str, agent_id: str, agent_name: str, content: str, task_id: str = ""
):
    """把 Agent 执行结果发进群聊消息流。
    落点=discussion_messages（OpenSoul与acp-proxy ws_group._persist_message共享的聊天存储，
    ws_group.py: _OPENSOUL_DB=data/ai_groups.db），实时可见性走_emit_exec_event→
    ws_group尾随task_execution_events广播。写入失败只记日志，不影响任务本体。"""
    try:
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, metadata, round_num, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4())[:8],
                    group_id,
                    task_id or f"ws_{group_id}",
                    agent_id or "soulmate",
                    agent_name or agent_id or "SoulMate",
                    "agent_reply",
                    content,
                    json.dumps({"source": "task_chain"}, ensure_ascii=False),
                    1,
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        # 实时可见：ws_group尾随task_execution_events，把新事件广播到群组WS房间
        _emit_exec_event(group_id, task_id, agent_id or "soulmate", "agent_reply", content[:2000])
        # 实时可见（聊天流）：推送 agent_message 广播帧到 acp-proxy ws_group 内部端点。
        # 聊天流前端只认 agent_message 帧（task_progress 只进时间线面板），验收行4 要求
        # "WS 收到 agent_message 帧"。落库以上面的直写为单一真源，此处只广播；
        # 旁路纪律：推送失败只记日志，绝不阻塞任务本体。
        try:
            async with httpx.AsyncClient(timeout=3.0) as push_cli:
                await push_cli.post(
                    GROUP_MSG_PUSH,
                    json={
                        "group_id": group_id,
                        "agent_id": agent_id or "soulmate",
                        "agent_name": agent_name or agent_id or "SoulMate",
                        "content": content[:2000],
                        "task_id": task_id,
                        "intent": "agent_reply",
                    },
                )
        except Exception as e:
            logger.warning(f"群聊实时推送失败(不影响任务): {e}")
        logger.info(f"群聊回帖已落库: group={group_id} task={task_id}")
    except Exception as e:
        logger.warning(f"群聊回帖失败(不影响任务): {e}")


async def _run_soulmate_turn(
    goal: str,
    timeout: float = 600.0,
    on_event=None,
    run_id: str | None = None,
    task_ref: str | None = None,
    heartbeat_s: float | None = None,
) -> tuple[str, str, bool]:
    """走 SoulMate 自己的 agent（acp-proxy :8092 /ws/acp JSON-RPC）跑一轮，
    返回(回复文本, source, 是否成功)。source: 'soulmate-agent' | 'cancelled'。

    红线：SoulMate 是独立 agent，绝不经 hermes CLI。
    线协议对齐 live :8092/ws/acp 实测（2026-10-10探测取证）：
      initialize(protocolVersion=1) → session/new 取 result.sessionId（om-开头）
      → session/prompt(sessionId,messageId,prompt=[{type:text,text:...}])
      流式通知 method=session/update：params.update.content.text 逐块
      （params.update.sessionUpdate=agent_message_chunk），id=3 result.stopReason 收尾。

    11.2 挂死可见性（10.3 三件套扩到群组任务的 ACP WS 回合通道，同一登记表）：
    ① 进度心跳 on_event("heartbeat", ...)（默认 SOULMATE_PROXY_HEARTBEAT_S=30s）
    ② 执行时长可查询（register_proxy_run 登记 → exec-status/proxy_run_snapshot）
    ③ 可显式取消（cancel_proxy_run → cancel_cb 摘观察窗停止本次执行等待）。
    **无到点自动杀**：timeout 只是等待预算（耗尽如实报未完成，禁把超时当成功），
    远端生成按会话生命周期处理（WS断开不杀进程）。
    """
    import websockets

    from src.services.auth import create_access_token

    token = create_access_token(uuid.uuid4())
    url = f"{SOULMATE_WS}?token={token}"
    chunks: list[str] = []
    run_id, entry = register_proxy_run(
        "soulmate", task_ref=task_ref, run_id=run_id, channel="acp_ws"
    )
    heartbeat_task: asyncio.Task | None = None
    completed = False

    def _note_chunk(text: str) -> None:
        chunks.append(text)
        entry["lines"] = entry.get("lines", 0) + 1
        entry["last_event_type"] = "output"
        entry["last_event"] = text[:200]

    async def _emit(etype: str, line: str) -> None:
        """事件旁路纪律：回调失败只计数不冒泡，绝不阻塞回合。"""
        if on_event is None:
            return
        try:
            r = on_event(etype, line)
            if asyncio.iscoroutine(r):
                await r
        except Exception:
            pass

    async def _heartbeat() -> None:
        """进度心跳：只报进度不打断执行（已用时/已收块数/最近输出）。"""
        interval = float(heartbeat_s if heartbeat_s is not None else _PROXY_HEARTBEAT_S)
        if interval <= 0:
            return
        try:
            while entry["status"] == "running":
                await asyncio.sleep(interval)
                if entry["status"] != "running":
                    break
                elapsed = time.monotonic() - entry["started_mono"]
                last = (entry.get("last_event") or "（暂无输出）").replace("\n", " ")
                await _emit(
                    "heartbeat",
                    "⏳ SoulMate回合进行中：已用 {e:.0f}s｜已收 {n} 块｜最近: {last}".format(
                        e=elapsed, n=entry.get("lines", 0), last=last[:80]
                    ),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    try:
        heartbeat_task = asyncio.create_task(_heartbeat())
        async with websockets.connect(url, max_size=8 * 1024 * 1024, open_timeout=15) as ws:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout

            async def _cancel_cb() -> None:
                # 显式取消唤醒：关掉观察窗连接，recv 立即醒来（不杀远端生成）
                entry["cancel_requested"] = True
                try:
                    await ws.close()
                except Exception:
                    pass

            entry["cancel_cb"] = _cancel_cb
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": 1},
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "session/new",
                        "params": {"_meta": {"source": "ai-group-task-chain"}},
                    }
                )
            )
            # 等 initialize(id=1)/session/new(id=2) 响应，从 id=2 取 sessionId
            session_id = ""
            pending = {1, 2}
            while pending and loop.time() < deadline and not entry.get("cancel_requested"):
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=max(1.0, deadline - loop.time())
                    )
                except TimeoutError:
                    break
                except Exception:
                    break  # 连接被显式取消关闭等，收口逻辑按 cancel_requested 如实报
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                if frame.get("error"):
                    logger.error(f"SoulMate回合握手失败: {frame['error']}")
                    finish_proxy_run(run_id, "failed")
                    return "", "soulmate-agent", False
                fid = frame.get("id")
                if fid in pending:
                    pending.discard(fid)
                    if fid == 2:
                        session_id = str((frame.get("result") or {}).get("sessionId") or "")
            if not session_id:
                if entry.get("cancel_requested"):
                    elapsed = time.monotonic() - entry["started_mono"]
                    finish_proxy_run(run_id, "cancelled")
                    return (
                        f"⚠️ SoulMate回合已被显式取消（用时 {elapsed:.1f}s，握手阶段）",
                        "cancelled",
                        False,
                    )
                logger.error("SoulMate回合失败: session/new未返回sessionId")
                finish_proxy_run(run_id, "failed")
                return "", "soulmate-agent", False
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "session/prompt",
                        "params": {
                            "sessionId": session_id,
                            "messageId": str(uuid.uuid4()),
                            "prompt": [{"type": "text", "text": goal}],
                        },
                    }
                )
            )
            while loop.time() < deadline and not entry.get("cancel_requested"):
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=max(1.0, deadline - loop.time())
                    )
                except TimeoutError:
                    break
                except Exception:
                    break  # 连接被显式取消关闭等，收口逻辑按 cancel_requested 如实报
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                if frame.get("error") and frame.get("id") == 3:
                    logger.error(f"SoulMate回合prompt失败: {frame['error']}")
                    finish_proxy_run(run_id, "failed")
                    return "", "soulmate-agent", False
                if frame.get("id") == 3:
                    # 最终响应（stopReason等），文本已由session/update流式攒齐
                    completed = True
                    break
                if frame.get("method") == "session/update":
                    upd = (frame.get("params") or {}).get("update") or {}
                    c = upd.get("content")
                    if isinstance(c, dict) and isinstance(c.get("text"), str):
                        _note_chunk(c["text"])
                    elif isinstance(c, str):
                        _note_chunk(c)
                    if upd.get("last"):
                        completed = True
                        break
    except Exception as e:
        logger.error(f"SoulMate回合失败: {e}")
        if entry.get("cancel_requested"):
            elapsed = time.monotonic() - entry["started_mono"]
            finish_proxy_run(run_id, "cancelled")
            return (
                f"⚠️ SoulMate回合已被显式取消（用时 {elapsed:.1f}s，已收 {len(chunks)} 块）",
                "cancelled",
                False,
            )
        finish_proxy_run(run_id, "failed")
        return "", "soulmate-agent", False
    finally:
        entry["cancel_cb"] = None
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    elapsed = time.monotonic() - entry["started_mono"]
    text = "".join(chunks).strip()
    if entry.get("cancel_requested"):
        # 显式取消：如实报告被取消 + 部分输出，绝不冒充正常完成
        finish_proxy_run(run_id, "cancelled")
        partial = text[-800:]
        note = "（截断显示末尾800字）" if len(text) > 800 else ""
        body = f"\n已收到的部分回复{note}：\n{partial}" if partial else ""
        return (
            f"⚠️ SoulMate回合已被显式取消（用时 {elapsed:.1f}s，已收 {len(chunks)} 块）{body}",
            "cancelled",
            False,
        )
    if not completed:
        # 等待预算耗尽：如实报未完成（禁把超时当成功），已收部分保留可见
        finish_proxy_run(run_id, "timeout")
        partial = text[-800:]
        note = "（截断显示末尾800字）" if len(text) > 800 else ""
        body = f"\n已收到的部分回复{note}：\n{partial}" if partial else "（无任何回复块）"
        return (
            f"⚠️ SoulMate回合等待超时（等待预算 {timeout:.0f}s，已用 {elapsed:.1f}s，"
            f"已收 {len(chunks)} 块，回合未完成）{body}",
            "soulmate-agent",
            False,
        )
    finish_proxy_run(run_id, "done" if text else "failed")
    return text, "soulmate-agent", bool(text)


# ── B2 任务链↔讨论联动：任务执行完可发起讨论（start_discussion与任务链联动） ──
def _start_post_task_discussion(group_id: str, source_task_id: str, goal: str = "") -> dict:
    """任务执行完发起复盘讨论：讨论任务挂source_task_id回链源任务，系统消息带
    子任务结果摘要，start_discussion的respond/decide/messages端点原样可用。
    幂等：源任务已有进行中的复盘讨论则返回既有讨论（existing=True），禁重复发起。
    只接受已终结源任务（scored/failed/completed/skipped），没跑完不许开复盘。"""
    conn = get_db()
    try:
        src = conn.execute(
            "SELECT * FROM ai_group_tasks WHERE id=? AND group_id=?", (source_task_id, group_id)
        ).fetchone()
        if not src:
            raise HTTPException(404, "任务不存在")
        if src["status"] not in _TASK_TERMINAL_STATUSES:
            raise HTTPException(409, "任务尚未执行完，不能发起复盘讨论")
        dup = conn.execute(
            "SELECT id FROM ai_group_tasks WHERE source_task_id=? AND status='discussing' ORDER BY created_at DESC LIMIT 1",
            (source_task_id,),
        ).fetchone()
        if dup:
            return {
                "discussion_task_id": dup["id"],
                "status": "discussing",
                "source_task_id": source_task_id,
                "existing": True,
            }
        # 结果摘要：子任务逐行goal→status(score)，禁截断（投标审查同款铁律）
        subs = conn.execute(
            "SELECT goal, status, quality_score FROM ai_group_tasks WHERE parent_task_id=? ORDER BY created_at, rowid",
            (source_task_id,),
        ).fetchall()
        sub_lines = [
            f"· {s['goal']} → {s['status']}（{float(s['quality_score'] or 0)}分）" for s in subs
        ]
        summary = (
            f"源任务[{source_task_id}]「{src['goal']}」已执行完毕："
            f"状态={src['status']}，均分={float(src['quality_score'] or 0)}。"
        )
        summary += f"子任务结果：{'；'.join(sub_lines)}" if sub_lines else "无子任务。"
        now = datetime.now().isoformat()
        discussion_id = str(uuid.uuid4())[:8]
        discussion_goal = goal or f"复盘讨论：{src['goal']}"
        conn.execute(
            "INSERT INTO ai_group_tasks (id, group_id, goal, constraints, completion_criteria, status, source_task_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                discussion_id,
                group_id,
                discussion_goal,
                "[]",
                json.dumps(["讨论结论落库", "决定是否分配后续子任务"], ensure_ascii=False),
                "discussing",
                source_task_id,
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO discussion_messages (id, group_id, task_id, agent_id, agent_name, intent, content, metadata, round_num, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4())[:8],
                group_id,
                discussion_id,
                "system",
                "系统",
                "comment",
                f"{summary}\n请各Agent讨论执行结果，并用decide决定后续子任务。",
                json.dumps(
                    {"source": "post_task_discussion", "source_task_id": source_task_id},
                    ensure_ascii=False,
                ),
                1,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    # 可观测：源任务事件流留痕（独立短连接，_emit_exec_event自带try不吃链路）
    _emit_exec_event(
        group_id,
        source_task_id,
        "system",
        "discussion-started",
        f"复盘讨论已发起: {discussion_id}（{discussion_goal}）",
    )
    return {
        "discussion_task_id": discussion_id,
        "status": "discussing",
        "source_task_id": source_task_id,
        "goal": discussion_goal,
        "summary": summary,
        "existing": False,
    }


async def _run_task_chain(group_id: str, parent_task_id: str, discuss_after: bool = False):
    """子任务链自动执行：按创建顺序（规划→执行→验证→审查）逐个跑，结果回群聊。
    B2联动：discuss_after=True时链跑完自动发起复盘讨论（讨论失败不影响任务链结果）。"""
    conn = get_db()
    try:
        subs = conn.execute(
            "SELECT id, goal, assigned_agent_id FROM ai_group_tasks WHERE parent_task_id=? ORDER BY created_at, rowid",
            (parent_task_id,),
        ).fetchall()
    finally:
        conn.close()
    for st in subs:
        try:
            req = ExecuteTaskRequest(
                agent_id=st["assigned_agent_id"] or "soulmate", goal=st["goal"]
            )
            await _execute_task_impl(group_id, st["id"], req)
        except Exception as e:
            logger.error(f"任务链子任务 {st['id']} 失败: {e}")
            _emit_exec_event(
                group_id,
                st["id"],
                st["assigned_agent_id"] or "soulmate",
                "failed",
                f"链式执行失败: {e}",
            )
    # B1根因修复：链跑完收口父任务（此前父任务永远卡planning，看板不可信）
    conn = get_db()
    try:
        final_status = _finalize_parent_task(conn, parent_task_id)
        conn.commit()
    finally:
        conn.close()
    if final_status:
        _emit_exec_event(
            group_id,
            parent_task_id,
            "chain",
            "chain-finished",
            f"任务链执行完毕，父任务收口为{final_status}",
        )
    # B2联动：链跑完自动发起复盘讨论（讨论自身故障不吃任务链结果，只留痕可见）
    if discuss_after:
        try:
            _start_post_task_discussion(group_id, parent_task_id)
        except Exception as e:
            logger.warning(f"链跑完发起复盘讨论失败(不影响任务链): {e}")
            _emit_exec_event(
                group_id, parent_task_id, "system", "discussion-error", f"复盘讨论发起失败: {e}"
            )


async def _execute_task_impl(group_id: str, task_id: str, req: "ExecuteTaskRequest") -> dict:
    """真正执行Agent任务 — 调用run_agent_proxy获取实际结果（由_run_task_background后台调用）"""
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
        run_goal = req.goal or (task["goal"] if "goal" in task.keys() else "")
        # phase事件：子任务goal的前缀（如"规划"/"执行"）即阶段名
        phase_name = run_goal.split("：", 1)[0][:12] if "：" in run_goal else "执行"
        _emit_exec_event(group_id, task_id, req.agent_id, "phase", f"阶段: {phase_name}")
        _emit_exec_event(
            group_id,
            task_id,
            req.agent_id,
            "started",
            f"agent={req.agent_id} {_channel_desc(req.agent_id, real_binary)} goal={run_goal[:120]}",
        )

        # ── A1认知层接入：执行前brain.think（意图+风险+决策），被调用而非仅存在 ──
        # 决策真正生效：deny（critical风险）直接拒执行落failed，不静默跳过；
        # 认知层自身故障不阻断执行但必须可见（brain-error事件）。同一session_id=task_id，
        # 执行后的brain.verify记忆与think记忆同会话可回溯。
        brain_decision: dict | None = None
        try:
            from src.api.brain import ThinkRequest
            from src.api.brain import think as brain_think

            think_result = await brain_think(
                ThinkRequest(
                    agent_id=req.agent_id,
                    user_input=run_goal,
                    session_id=task_id,
                )
            )
            brain_decision = dict(think_result.get("decision") or {})
            _emit_exec_event(
                group_id,
                task_id,
                req.agent_id,
                "brain-think",
                "goal={g} risk={r} mode={m}".format(
                    g=(think_result.get("intent") or {}).get("goal", "?"),
                    r=(think_result.get("risk") or {}).get("overall_level", "?"),
                    m=brain_decision.get("execute_mode", "?"),
                ),
            )
            if brain_decision.get("execute_mode") == "deny":
                deny_reason = (
                    brain_decision.get("confirm_prompt") or "brain风险门禁拒绝（critical风险）"
                )
                conn.execute(
                    "UPDATE ai_group_tasks SET status='failed', result=?, updated_at=? WHERE id=?",
                    (deny_reason, datetime.now().isoformat(), task_id),
                )
                conn.commit()
                _emit_exec_event(
                    group_id, task_id, req.agent_id, "failed", f"brain deny: {deny_reason}"
                )
                return {
                    "status": "denied",
                    "task_id": task_id,
                    "agent_id": req.agent_id,
                    "success": False,
                    "deny_reason": deny_reason,
                    "brain_decision": brain_decision,
                }
        except Exception as e:
            _emit_exec_event(
                group_id, task_id, req.agent_id, "brain-error", f"think失败(不阻断执行): {e}"
            )

        async def _on_exec_event(etype: str, line: str):
            _emit_exec_event(group_id, task_id, req.agent_id, etype, line)

        # 失败自动重试1次（自动重试上限1，手动retry不受影响）
        # run_ref=任务级执行登记（10.3：进度心跳/时长查询/显式取消的查找键）
        run_ref = f"{group_id}/{task_id}"
        attempt = 0
        while True:
            if (req.agent_id or "").lower() == "soulmate":
                # 红线：SoulMate 走自己的 agent（acp-proxy），不经 hermes CLI
                # 11.2：与 CLI 通道同套挂死可见性——心跳/时长查询/显式取消（task_ref 同一查找键）
                response_text, source, success = await _run_soulmate_turn(
                    run_goal, on_event=_on_exec_event, task_ref=run_ref
                )
            else:
                response_text, source, success = await run_agent_proxy(
                    real_binary, run_goal, on_event=_on_exec_event, task_ref=run_ref
                )
            if success or attempt >= 1 or source == "cancelled":
                break
            attempt += 1
            conn.execute(
                "UPDATE ai_group_tasks SET iteration=?, updated_at=? WHERE id=?",
                (attempt, datetime.now().isoformat(), task_id),
            )
            conn.commit()
            _emit_exec_event(
                group_id, task_id, req.agent_id, "retry", f"auto-retry #{attempt}（上次失败）"
            )
        if source == "cancelled":
            _emit_exec_event(
                group_id,
                task_id,
                req.agent_id,
                "cancelled",
                f"任务执行被显式取消（不自动重试、不评分）：{response_text[:120]}",
            )
        else:
            _emit_exec_event(
                group_id,
                task_id,
                req.agent_id,
                "finished" if success else "failed",
                f"success={success} len={len(response_text)} attempts={attempt + 1}",
            )

        # 显式取消：任务以取消收口（尊重用户意图，跳过评分/能力画像）
        if source == "cancelled":
            result_now = datetime.now().isoformat()
            conn.execute(
                "UPDATE ai_group_tasks SET result=?, status='failed', updated_at=? WHERE id=?",
                (response_text, result_now, task_id),
            )
            conn.commit()
            return {
                "status": "cancelled",
                "task_id": task_id,
                "agent_id": req.agent_id,
                "success": False,
            }

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
        _emit_exec_event(
            group_id,
            task_id,
            req.agent_id,
            "scored",
            f"score={auto_score} reason={auto_reason} capability={capability}",
        )

        conn.commit()

        # ── A1认知层接入：执行后brain.verify（反思+记忆+事件，失败记忆加重） ──
        # 与think同session（task_id），执行结果进认知层可回溯；故障不阻断回群聊。
        try:
            from src.api.brain import VerifyRequest
            from src.api.brain import verify as brain_verify

            await brain_verify(
                VerifyRequest(
                    agent_id=req.agent_id,
                    task_id=task_id,
                    action=run_goal,
                    result={
                        "success": success,
                        "source": source,
                        "auto_score": auto_score,
                        "attempts": attempt + 1,
                        "length": len(response_text),
                    },
                )
            )
            _emit_exec_event(
                group_id,
                task_id,
                req.agent_id,
                "brain-verify",
                f"success={success} score={auto_score} 已入认知记忆(session={task_id})",
            )
        except Exception as e:
            _emit_exec_event(
                group_id, task_id, req.agent_id, "brain-error", f"verify失败(不阻断执行): {e}"
            )

        # 执行结果回群聊（用户可见的 Agent 回复）
        _agent_name = (
            agent_row["name"] if agent_row and "name" in agent_row.keys() else req.agent_id
        )
        await _notify_group_chat(group_id, req.agent_id, _agent_name, response_text, task_id)

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
            "brain_decision": brain_decision,
        }
    finally:
        conn.close()


@router.post("/{group_id}/tasks/{task_id}/retry")
async def retry_task(group_id: str, task_id: str, req: RetryTaskRequest):
    """Paperclip式单任务/单子任务重试：重置该行状态后独立重跑（不影响兄弟子任务）"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT * FROM ai_group_tasks WHERE id=? AND group_id=?", (task_id, group_id)
        ).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE ai_group_tasks SET status='pending', result='', iteration=0, updated_at=? WHERE id=?",
            (now, task_id),
        )
        conn.commit()
    finally:
        conn.close()
    agent_id = req.agent_id or (task["assigned_agent_id"] or "")
    _emit_exec_event(
        group_id, task_id, agent_id, "retry", f"手动重试 by={agent_id or 'unassigned'}"
    )
    if task_id in _RUNNING_TASKS:
        raise HTTPException(409, "task already executing")
    _RUNNING_TASKS.add(task_id)
    exec_req = ExecuteTaskRequest(agent_id=agent_id, goal="")
    asyncio.create_task(_run_task_background(group_id, task_id, exec_req))
    return JSONResponse(
        {"status": "executing", "task_id": task_id, "message": "已开始重试，进度见事件流"},
        status_code=202,
    )


class CancelExecRequest(BaseModel):
    force: bool = False


@router.get("/{group_id}/tasks/{task_id}/exec-status")
def get_task_exec_status(group_id: str, task_id: str):
    """执行时长/进度可查询（10.3 挂死可见性）：agent 执行登记项快照 + 任务行状态。

    快照含 elapsed_s（实时执行时长）/last_event（最近输出行）/lines（已收行数）/
    cancel_requested；无在跑执行时 run=None（不造假）。"""
    conn = get_db()
    try:
        task = conn.execute(
            "SELECT status, assigned_agent_id FROM ai_group_tasks WHERE id=? AND group_id=?",
            (task_id, group_id),
        ).fetchone()
    finally:
        conn.close()
    run = find_proxy_run(f"{group_id}/{task_id}", running_only=False)
    return {
        "group_id": group_id,
        "task_id": task_id,
        "task_status": task["status"] if task else None,
        "assigned_agent_id": task["assigned_agent_id"] if task else None,
        "run": run,
    }


@router.post("/{group_id}/tasks/{task_id}/exec-cancel")
async def cancel_task_exec(group_id: str, task_id: str, req: CancelExecRequest):
    """显式取消任务的 agent 执行（用户意图：SIGTERM→有界等待，force=True 才 SIGKILL）。

    不加到点自动杀——正常长回合绝不被误杀；取消只由本端点显式发起。"""
    run = find_proxy_run(f"{group_id}/{task_id}")
    if not run:
        raise HTTPException(404, "该任务当前没有在跑的 agent 执行进程（无可取消对象）")
    result = await cancel_proxy_run(run["run_id"], force=req.force)
    if result.get("ok"):
        _emit_exec_event(
            group_id,
            task_id,
            run.get("agent_id") or "",
            "cancel-requested",
            "显式取消执行：mode={m} signal={s} force={f} exited={e}".format(
                m=result.get("mode"),
                s=result.get("signal"),
                f=req.force,
                e=result.get("exited"),
            ),
        )
    return result


@router.get("/{group_id}/tasks/{task_id}/events")
def get_task_events(group_id: str, task_id: str, after_id: int = 0):
    """执行过程事件回放（after_id增量拉取，供时间线面板回看）"""
    conn = get_db()
    try:
        rows = rows_to_list(
            conn.execute(
                "SELECT * FROM task_execution_events WHERE task_id=? AND id>? ORDER BY id LIMIT 500",
                (task_id, after_id),
            ).fetchall()
        )
        # computed: 首个started事件 → finished/failed（未结束则到当前）的耗时秒数
        started = conn.execute(
            "SELECT created_at FROM task_execution_events WHERE task_id=? AND event_type='started' ORDER BY id LIMIT 1",
            (task_id,),
        ).fetchone()
        ended = conn.execute(
            "SELECT created_at FROM task_execution_events WHERE task_id=? AND event_type IN ('finished','failed') ORDER BY id LIMIT 1",
            (task_id,),
        ).fetchone()
        elapsed_seconds = None
        start_ts = _parse_ts(started["created_at"]) if started else None
        if start_ts:
            end_ts = _parse_ts(ended["created_at"]) if ended else None
            if end_ts is None:
                end_ts = datetime.now()
            elapsed_seconds = round((end_ts - start_ts).total_seconds(), 1)
        return {"events": rows, "elapsed_seconds": elapsed_seconds}
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


@router.post("/{group_id}/tasks/{task_id}/discuss-after")
def discuss_after_task(group_id: str, task_id: str, req: DiscussAfterRequest | None = None):
    """B2 任务执行完可发起讨论：为已终结任务创建复盘讨论（讨论任务挂source_task_id
    回链源任务、系统消息带子任务结果摘要），respond/decide/messages等既有讨论端点
    原样可用。幂等：已有进行中的复盘讨论则返回既有讨论（existing=True）。"""
    return _start_post_task_discussion(group_id, task_id, req.goal if req else "")


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
