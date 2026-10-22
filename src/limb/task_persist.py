"""SQLite persistence for RPAExecutor._tasks（RV-①b-⑦-1）——RPA 任务行唯一落盘口。

修前（RV-① A 组定性）：RPAExecutor._tasks 全在进程内存（dict），create_task/
execute_task/cancel_task/delete_task 只改内存——RPA 任务面（/tasks 列表/单查真源、
状态/进度/错误信息）随 service 重启全清。本模块把任务行落 SQLite：任务状态每次
变更随写随落盘（写穿），首触懒回载，重启任务不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、任务本体（含 actions/results/variables 嵌套）整体
json.dumps 落 payload 列（dict 列禁裸存）。

测试隔离：LIMB_TASKS_DB 环境变量覆盖库路径（GLAND_USAGE_DB/IMMUNE_ACCESS_DB/
ECHO_HISTORY_DB/A2A_TASKS_DB/VEIN_UPLOAD_DB/DOWNLOAD_TASKS_DB 同款先例），缺省
<repo>/data/limb_tasks.db；库路径每次连接按 env 解析（模块级 executor 单例跨
用例存活，测试面每用例 tmp 真源）。

表语义（与 download_tasks/a2a_tasks 同族「状态快照表」，与 echo_history 追加表
刻意不同，如实记）：limb_tasks 是**状态快照表**——task_id=PRIMARY KEY，同任务
每次变更 upsert 覆盖旧行=「当前态」语义（一个任务一行）；echo_history 是追加型
历史表故禁 REPLACE。两者纪律不混用。

诚实边界：
①写失败不阻断任务本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——任务
  动作已执行，为落盘失败炸掉执行不成比例；代价是故障窗口内该态只活内存、重启丢
  （如实记，非谎报 durable）。
②回载失败=空任务起步+logger.error 可见，行真源仍在库下次触碰重试（RV-①b-②④
  语义）；不在回载故障上发明降级判法。
③行内单条不可解（payload 坏行/枚举值非法）跳过该行并 logger.error 可见，不因
  单行坏数据丢整库任务（禁静默丢：跳过必留日志真因）。
④无老化/裁剪面：表只增不减（任务行被 upsert 覆盖不膨胀；delete_task 删行口在
  RPAExecutor 侧真消费本模块 delete()，删任务即删行）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

_TABLE = "limb_tasks"

_COLUMNS = (
    "task_id",
    "status",
    "payload",
)


def default_db_path() -> str:
    """库路径：LIMB_TASKS_DB 环境变量优先（测试隔离），缺省 <repo>/data/limb_tasks.db。"""
    env = os.environ.get("LIMB_TASKS_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "limb_tasks.db")


class RPATaskPersistence:
    """RPA 任务行持久化真源（单表 limb_tasks：upsert 写穿 + load_all 懒回载）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 executor 单例跨用例存活，测试面每用例
        # tmp 真源。构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
        # （RV-①b-②④ 同纪律），写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "task_id TEXT PRIMARY KEY, "
            "status TEXT NOT NULL, "
            "payload TEXT NOT NULL)"
        )
        return conn

    def upsert(self, *, task_id: str, status: str, payload: str) -> None:
        """写穿一行任务当前态（状态快照表语义：同 task_id upsert 覆盖=当前态）。

        失败如实抛（禁静默丢写），是否阻断调用方归消费方裁决（RPAExecutor
        侧=记账旁路纪律：logger.error 不阻断任务本体）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) VALUES (?, ?, ?)"
                    " ON CONFLICT(task_id) DO UPDATE SET "
                    "status=excluded.status, payload=excluded.payload",
                    (str(task_id), str(status), str(payload)),
                )
                conn.commit()
            finally:
                conn.close()

    def delete(self, task_id: str) -> None:
        """删一行任务（delete_task 口真消费；幂等：不存在=无操作不抛）。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(f"DELETE FROM {_TABLE} WHERE task_id = ?", (str(task_id),))
                conn.commit()
            finally:
                conn.close()

    def load_all(self) -> list[dict]:
        """回载全部任务行 payload（json.loads 后的 dict 列表，rowid 升序=稳定回载序）。

        回载只读不建库：库文件缺失=空任务起步直接返回（防模块 import 期构造
        回载顺手建出空生产库——写穿才建库，RV-①b-②④ 同纪律）。
        单行不可解（json 坏行/非对象）跳过该行并 logger.error 可见（诚实边界③）。
        """
        import json
        import logging

        logger = logging.getLogger(__name__)
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(f"SELECT payload FROM {_TABLE} ORDER BY rowid").fetchall()
            finally:
                conn.close()
        out: list[dict] = []
        for (payload,) in rows:
            try:
                data = json.loads(payload)
            except (TypeError, ValueError) as e:
                logger.error(f"limb task 行 payload 不可解跳过: {e}")
                continue
            if not isinstance(data, dict):
                logger.error("limb task 行 payload 非对象跳过")
                continue
            out.append(data)
        return out

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
