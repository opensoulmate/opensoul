"""SQLite persistence for a2a TaskManager push delivery log（RV-①b-⑤-3）
——webhook 投递留痕唯一落盘口。

修前（RV-① A 组定性）：TaskManager.push_delivery_log 全在进程内存（list），
_dispatch_push 只 append——「失败留痕禁静默」（F4b-⑮ §4.3.3 尽力投递纪律）的留痕
随 service 重启即清：投递成败审计（谁在何时投了哪个 webhook、成败/状态码/真因）
取证面跨重启不完整。本模块把投递留痕落 SQLite：每次投递尝试随写随落盘（写穿），
首触懒回载，重启审计史不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、entry 本体（含 error/status_code 可选键）整体 json.dumps
落 payload 列（dict 列禁裸存）。

测试隔离：A2A_PUSH_LOG_DB 环境变量覆盖库路径（A2A_PUSH_DB/A2A_TASKS_DB/
GLAND_USAGE_DB 同款先例），缺省 <repo>/data/a2a_push_delivery_log.db；库路径每次
连接按 env 解析（模块级 task_manager 单例跨用例存活，测试面每用例 tmp 真源）。

表语义：a2a_push_delivery_log 是**追加表**（与 a2a_push_configs 配置当前态表、
a2a_tasks 快照表刻意不同）——一次投递尝试一行，只增不改不删（审计史禁覆盖）；
行序=投递序（rowid 升序）；delivery_id=业务键（内存 entry 唯一 id），撞键 INSERT
如实抛 IntegrityError 禁 REPLACE 静默覆盖丢史（echo_history 同纪律）。

诚实边界：
①写失败不阻断投递本身但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——投递
  尝试已完成、内存留痕仍在，为落盘失败炸掉投递不成比例；代价是该行只活内存、
  重启丢（如实记，非谎报 durable）。
②回载失败=空审计史起步+logger.error 可见，行真源仍在库下次触碰重试（RV-①b-②④
  ⑤-1⑤-2 语义）；不在回载故障上发明降级判法。
③行内单条不可解（payload 坏行）跳过该行并 logger.error 可见，不因单行坏数据丢
  整库审计史（禁静默丢：跳过必留日志真因）。
④无老化/裁剪面：投递审计史只增不删（审计语义；如需老化另拍）。
⑤读面=库行 SQL 取证+count()/load_all() 回读口，无 HTTP API（push_delivery_log
  既有读面就是 tests/live SQL 取证，本拍不扩 API 面）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

_TABLE = "a2a_push_delivery_log"

_COLUMNS = (
    "delivery_id",
    "task_id",
    "config_id",
    "ok",
    "payload",
)


def default_db_path() -> str:
    """库路径：A2A_PUSH_LOG_DB 环境变量优先（测试隔离），缺省 <repo>/data/a2a_push_delivery_log.db。"""
    env = os.environ.get("A2A_PUSH_LOG_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "a2a_push_delivery_log.db")


class DeliveryLogPersistence:
    """投递留痕持久化真源（单表 a2a_push_delivery_log：append 写穿 + load_all 懒回载）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 task_manager 单例跨用例存活，测试面每用例
        # tmp 真源。构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
        # （RV-①b-②④⑤-1⑤-2 同纪律），写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "delivery_id TEXT PRIMARY KEY, "
            "task_id TEXT NOT NULL, "
            "config_id TEXT, "
            "ok INTEGER NOT NULL, "
            "payload TEXT NOT NULL)"
        )
        return conn

    def append(
        self, *, delivery_id: str, task_id: str, config_id: str, ok: bool, payload: str
    ) -> None:
        """写穿一行投递留痕（追加表：只增不改；撞主键如实抛禁覆盖丢史）。

        失败如实抛（禁静默丢写），是否阻断调用方归消费方裁决（TaskManager
        侧=记账旁路纪律：logger.error 不阻断投递本身）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) VALUES (?, ?, ?, ?, ?)",
                    (
                        str(delivery_id),
                        str(task_id),
                        str(config_id),
                        1 if ok else 0,
                        str(payload),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_all(self) -> list[dict]:
        """回载全部投递留痕 entry dict 列表，rowid 升序=投递序。

        回载只读不建库：库文件缺失=空审计史起步直接返回（防模块 import 期构造
        回载顺手建出空生产库——写穿才建库，RV-①b-②④⑤-1⑤-2 同纪律）。
        单行不可解（json 坏行）跳过该行并 logger.error 可见（诚实边界③）。
        delivery_id 以列值为权威（payload 内同键以列值覆盖防漂移）。
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
                rows = conn.execute(
                    f"SELECT delivery_id, task_id, config_id, ok, payload FROM {_TABLE} ORDER BY rowid"
                ).fetchall()
            finally:
                conn.close()
        out: list[dict] = []
        for delivery_id, task_id, config_id, ok, payload in rows:
            try:
                data = json.loads(payload)
            except (TypeError, ValueError) as e:
                logger.error(f"a2a push delivery 行 payload 不可解跳过: {e}")
                continue
            if not isinstance(data, dict):
                logger.error("a2a push delivery 行 payload 非对象跳过")
                continue
            data["delivery_id"] = str(delivery_id)
            data["task_id"] = str(task_id)
            data["config_id"] = str(config_id) if config_id is not None else None
            data["ok"] = bool(ok)
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
