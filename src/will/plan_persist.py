"""SQLite persistence for DAGPlanner._plans（RV-①b-⑫）——DAG 计划注册表唯一落盘口。

修前（RV-①c §5.1 藏活事实定性）：DAGPlanner._plans 全在进程内存（dict），
create_plan/update_step 只改内存——DAG 计划面（get_plan by-ID 契约、
visualize_ascii/to_mermaid、get_stats 计数）随 service 重启全清（RV-①c：
「will/dag_planner._plans（get_plan by-ID 契约跨重启 404）」）。本模块把计划行
落 SQLite：变更点随写随落盘（状态快照），首触懒回载，重启计划不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、payload 是 dict 列整体 json.dumps 落 TEXT（dict 列禁裸存）。

测试隔离：WILL_PLANS_DB 环境变量覆盖库路径（GLAND_USAGE_DB/PULSE_SIGNALS_DB
同款先例），缺省 <repo>/data/will_plans.db；库路径每次连接按 env 解析
（api/will 模块级 dag_planner 单例跨用例存活，测试面每用例 tmp 真源）。

表语义（pulse_signals/limb_tasks 同族「状态快照表」）：plan_id=PK upsert 当前态
一计划一行——一个计划多次步进状态迁移=同一行当前态更新，不是多行历史；
步骤行不单独建表（steps 是计划本体的结构化部分，整体进 payload 列）。

诚实边界：
①写失败不阻断计划动作本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——
  动作已进内存，为落盘失败炸掉计划步进不成比例；代价是故障窗口内该行只活
  内存、重启丢（如实记，非谎报 durable）。
②回载失败=空注册表起步+logger.error 可见，行真源仍在库、不置位下次触碰重试
  （RV-①b-⑤-1 语义）；不在回载故障上发明降级判法。
③payload JSON 不可解/坏行（未来版本行）=跳过+logger.error 可见，不因单行坏
  数据丢整段注册表（禁静默丢：跳过必留日志真因；echo ④/pulse ③ 同纪律）。
④result 字段是 Any（步进结果本体）：JSON 原生值（dict/list/str/int/float/bool/
  None）逐值保真往返；非 JSON 可序列化对象（自定义类实例等）落盘走 str() 形
  （json.dumps default=str）——如实记，不 fabricate 类型。
⑤计数（total_plans/completed_plans/failed_plans/total_steps/active_plans）
  恒对账行聚合（MEM-⑥「计数器=行缓存视图」纪律）；行只增不减（DAGPlanner
  无删计划口），行计数=进程生命期累计计数跨重启真源。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_TABLE = "will_plans"

_COLUMNS = (
    "plan_id",
    "goal",
    "status",
    "tenant_id",
    "created_at",
    "started_at",
    "completed_at",
    "steps_count",
    "payload",
)


def default_db_path() -> str:
    """库路径：WILL_PLANS_DB 环境变量优先（测试隔离），缺省 <repo>/data/will_plans.db。"""
    env = os.environ.get("WILL_PLANS_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "will_plans.db")


class PlanPersistence:
    """DAG 计划注册表持久化真源（单表 will_plans：upsert 当前态 + load_all
    懒回载 + counters 行聚合计数真源）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 dag_planner 单例跨用例存活，测试面每用例
        # tmp 真源。构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
        # （RV-①b-②④⑨ 同纪律），写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "plan_id TEXT PRIMARY KEY, "
            "goal TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "tenant_id TEXT NOT NULL DEFAULT 'default', "
            "created_at REAL NOT NULL, "
            "started_at REAL NOT NULL DEFAULT 0.0, "
            "completed_at REAL NOT NULL DEFAULT 0.0, "
            "steps_count INTEGER NOT NULL DEFAULT 0, "
            "payload TEXT NOT NULL DEFAULT '{}')"
        )
        return conn

    def upsert(self, row: dict) -> None:
        """写穿一行计划当前态（plan_id=PK 状态快照：一计划一行，重复写=当前态
        更新，与追加型历史表刻意分语义）。失败如实抛（禁静默丢写），是否阻断
        调用方归消费方裁决（DAGPlanner 侧=记账旁路纪律）。

        payload dict 整体 json.dumps 落 TEXT 列（dict 列禁裸存铁律）——
        steps（含 arguments dict 列）/metadata 随 payload 整体往返；
        非 JSON 可序列化值走 default=str（诚实边界④）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ("
                    + ", ".join(_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(plan_id) DO UPDATE SET "
                    "goal=excluded.goal, status=excluded.status, "
                    "tenant_id=excluded.tenant_id, created_at=excluded.created_at, "
                    "started_at=excluded.started_at, completed_at=excluded.completed_at, "
                    "steps_count=excluded.steps_count, payload=excluded.payload",
                    (
                        str(row["plan_id"]),
                        str(row["goal"]),
                        str(row["status"]),
                        str(row.get("tenant_id") or "default"),
                        float(row["created_at"]),
                        float(row.get("started_at") or 0.0),
                        float(row.get("completed_at") or 0.0),
                        int(row.get("steps_count") or 0),
                        json.dumps(row.get("payload") or {}, ensure_ascii=False, default=str),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_all(self) -> list[dict]:
        """回载全部计划行（rowid 升序=创建/写入序；条目 dict 键集=落盘行形，
        payload 还原为 dict=与内存 ExecutionPlan.to_state 往返形）。

        回载只读不建库：库文件缺失=空注册表直接返回（防模块 import 期构造回载
        顺手建出空生产库——写穿才建库，RV-①b-②④⑨ 同纪律）。
        payload JSON 不可解=坏行跳过+logger.error 可见（诚实边界③）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} ORDER BY rowid ASC"
                ).fetchall()
            finally:
                conn.close()
        out: list[dict] = []
        for row in rows:
            (
                plan_id,
                goal,
                status,
                tenant_id,
                created_at,
                started_at,
                completed_at,
                steps_count,
                payload_raw,
            ) = row
            try:
                payload = json.loads(payload_raw) if payload_raw else {}
                if not isinstance(payload, dict):
                    raise ValueError(f"payload 非对象: {type(payload).__name__}")
            except Exception as e:
                logger.error(f"will 计划坏行跳过（plan_id={plan_id}，payload 不可解）: {e}")
                continue
            out.append(
                {
                    "plan_id": str(plan_id),
                    "goal": str(goal),
                    "status": str(status),
                    "tenant_id": str(tenant_id or "default"),
                    "created_at": float(created_at),
                    "started_at": float(started_at or 0.0),
                    "completed_at": float(completed_at or 0.0),
                    "steps_count": int(steps_count or 0),
                    "payload": payload,
                }
            )
        return out

    def counters(self) -> dict:
        """计数真源（MEM-⑥）：GROUP BY status 单查询全表聚合。

        返回键形与 DAGPlanner._stats+active_plans 同一形状（total_plans/
        completed_plans/failed_plans/total_steps/active_plans）单一形状禁两套；
        active=status in (pending, running)（与 get_stats 内存判法同谓词）。
        库缺失=全零（真实空账，非假 0——行真源确实为空）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return {
                    "total_plans": 0,
                    "completed_plans": 0,
                    "failed_plans": 0,
                    "total_steps": 0,
                    "active_plans": 0,
                }
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT status, COUNT(*), COALESCE(SUM(steps_count), 0) FROM {_TABLE} GROUP BY status"
                ).fetchall()
            finally:
                conn.close()
        total_plans = 0
        completed_plans = 0
        failed_plans = 0
        total_steps = 0
        active_plans = 0
        for status, cnt, steps in rows:
            n = int(cnt)
            total_plans += n
            total_steps += int(steps or 0)
            if str(status) == "completed":
                completed_plans += n
            elif str(status) == "failed":
                failed_plans += n
            elif str(status) in ("pending", "running"):
                active_plans += n
        return {
            "total_plans": total_plans,
            "completed_plans": completed_plans,
            "failed_plans": failed_plans,
            "total_steps": total_steps,
            "active_plans": active_plans,
        }

    def count(self) -> int:
        """行数（探针/取证回读口）。库缺失=0。"""
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return 0
            conn = self._connect()
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()
            finally:
                conn.close()
        return int(row[0])
