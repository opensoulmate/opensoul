"""SQLite persistence for a2a TaskManager push notification configs（RV-①b-⑤-2）
——推送配置唯一落盘口。

修前（RV-① A 组定性）：TaskManager.push_configs 全在进程内存（dict），create/
delete 只改内存——a2a 推送通知配置面（Get/List/DeleteTaskPushNotificationConfig
真源、webhook 投递依据）随 service 重启全清：重启后已登记 webhook 不再被投递
（§3.1.7「persist until task completion or explicit deletion」语义被重启打破）。
本模块把推送配置落 SQLite：登记/删除随写随落盘（写穿），首触懒回载，重启配置不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、配置本体（含 authentication 嵌套 dict）整体 json.dumps
落 payload 列（dict 列禁裸存）。

测试隔离：A2A_PUSH_DB 环境变量覆盖库路径（A2A_TASKS_DB/GLAND_USAGE_DB/
ECHO_HISTORY_DB 同款先例），缺省 <repo>/data/a2a_push_configs.db；库路径每次
连接按 env 解析（模块级 task_manager 单例跨用例存活，测试面每用例 tmp 真源）。

表语义：a2a_push_configs 是**配置当前态表**——(task_id, config_id) 复合主键，
同 id 重复登记 upsert 覆盖=当前态（与 a2a_tasks 快照表同语义、与 echo_history
追加表刻意不同）；行序=登记序（rowid 升序），同任务多配置回载保插入序。

诚实边界：
①写失败不阻断配置登记但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——配置
  已在内存生效，为落盘失败炸掉登记不成比例；代价是故障窗口内该配置只活内存、
  重启丢（如实记，非谎报 durable）。
②回载失败=空配置起步+logger.error 可见，行真源仍在库下次触碰重试（RV-①b-②④⑤-1
  语义）；不在回载故障上发明降级判法。
③行内单条不可解（payload 坏行）跳过该行并 logger.error 可见，不因单行坏数据丢
  整库配置（禁静默丢：跳过必留日志真因）。
④无老化/裁剪面：配置保留到显式删除（F4b-⑮ 既有语义，终态后不自动清）。
⑤配置含 webhook 凭据（AuthenticationInfo.credentials）明文落本地 SQLite——
  投递需要 Authorization 头完整往返（脱敏=重启后投递失效，功能不完整）；
  本地文件不上传云端（用户纪律），泄露面=本机 data/ 目录权限（如实记）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

_TABLE = "a2a_push_configs"

_COLUMNS = (
    "task_id",
    "config_id",
    "payload",
)


def default_db_path() -> str:
    """库路径：A2A_PUSH_DB 环境变量优先（测试隔离），缺省 <repo>/data/a2a_push_configs.db。"""
    env = os.environ.get("A2A_PUSH_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "a2a_push_configs.db")


class PushPersistence:
    """推送配置持久化真源（单表 a2a_push_configs：upsert 写穿 + load_all 懒回载）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 task_manager 单例跨用例存活，测试面每用例
        # tmp 真源。构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
        # （RV-①b-②④⑤-1 同纪律），写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "task_id TEXT NOT NULL, "
            "config_id TEXT NOT NULL, "
            "payload TEXT NOT NULL, "
            "PRIMARY KEY (task_id, config_id))"
        )
        return conn

    def upsert(self, *, task_id: str, config_id: str, payload: str) -> None:
        """写穿一行配置当前态（复合主键 upsert 覆盖=当前态；重复登记保原行序）。

        失败如实抛（禁静默丢写），是否阻断调用方归消费方裁决（TaskManager
        侧=记账旁路纪律：logger.error 不阻断配置登记）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) VALUES (?, ?, ?)"
                    " ON CONFLICT(task_id, config_id) DO UPDATE SET payload=excluded.payload",
                    (str(task_id), str(config_id), str(payload)),
                )
                conn.commit()
            finally:
                conn.close()

    def delete(self, *, task_id: str, config_id: str) -> None:
        """删一行配置（幂等：行不存在=零效果零报错，§3.1.10 语义）。

        失败如实抛（同 upsert：是否阻断归消费方裁决）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"DELETE FROM {_TABLE} WHERE task_id=? AND config_id=?",
                    (str(task_id), str(config_id)),
                )
                conn.commit()
            finally:
                conn.close()

    def load_all(self) -> list[tuple[str, str, dict]]:
        """回载全部配置行 [(task_id, config_id, config dict), ...]，rowid 升序=登记序。

        回载只读不建库：库文件缺失=空配置起步直接返回（防模块 import 期构造
        回载顺手建出空生产库——写穿才建库，RV-①b-②④⑤-1 同纪律）。
        单行不可解（json 坏行）跳过该行并 logger.error 可见（诚实边界③）。
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
                    f"SELECT task_id, config_id, payload FROM {_TABLE} ORDER BY rowid"
                ).fetchall()
            finally:
                conn.close()
        out: list[tuple[str, str, dict]] = []
        for task_id, config_id, payload in rows:
            try:
                data = json.loads(payload)
            except (TypeError, ValueError) as e:
                logger.error(f"a2a push config 行 payload 不可解跳过: {e}")
                continue
            if not isinstance(data, dict):
                logger.error("a2a push config 行 payload 非对象跳过")
                continue
            out.append((str(task_id), str(config_id), data))
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
