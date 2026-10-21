"""SQLite persistence for echo MessageDispatcher._history（RV-①b-④）——消息历史唯一落盘口。

修前（RV-① A 组定性）：MessageDispatcher._history 全在进程内存（deque maxlen=1000），
send() 只 append——echo /history /stats 真源（DEC-⑦-②/⑬/⑭ 加轴读面）随 service
重启归零=消息历史丢、/history /stats 归零。本模块把消息行落 SQLite：send() 随写
随落盘，首读/首写懒回载，重启消息历史不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、无 dict/list 列（Message 各字段均标量，无需 json.dumps）。

测试隔离：ECHO_HISTORY_DB 环境变量覆盖库路径（GLAND_USAGE_DB/IMMUNE_ACCESS_DB
同款先例），缺省 <repo>/data/echo_history.db；库路径每次连接按 env 解析（模块级
api/echo dispatcher 单例跨用例存活，测试面每用例 tmp 真源）。

诚实边界：
①写失败不阻断调用方但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——消息
  派发本体已发生，为落盘失败炸掉已送达消息不成比例；代价是故障窗口内该行只活
  内存、重启丢（如实记，非谎报 durable）。
②回载失败=空历史起步+logger.error 可见，行真源仍在库下次启动重试（RV-①b-②
  语义）；不在回载故障上发明降级判法。
③无老化/裁剪面：表只增不减；内存面 maxlen=1000 既有语义不变（回载只取最近
  1000 行=deque 回填语义，更旧行留在库不进内存，如实记）。
④行内单字段不可解（如 channel 枚举值来自未来版本）跳过该行并 logger.error
  可见，不因单行坏数据丢整段历史（禁静默丢：跳过必留日志真因）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

_TABLE = "echo_history"

_COLUMNS = (
    "msg_id",
    "channel",
    "title",
    "content",
    "timestamp",
    "status",
    "error",
    "target",
    "priority",
    "tenant_id",
)


def default_db_path() -> str:
    """库路径：ECHO_HISTORY_DB 环境变量优先（测试隔离），缺省 <repo>/data/echo_history.db。"""
    env = os.environ.get("ECHO_HISTORY_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "echo_history.db")


class HistoryPersistence:
    """消息历史持久化真源（单表 echo_history：insert 写穿 + load_recent 懒回载）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 dispatcher 单例跨用例存活，测试面每用例
        # tmp 真源。构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
        # （RV-①b-② 同纪律），写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "msg_id TEXT PRIMARY KEY, "
            "channel TEXT NOT NULL, "
            "title TEXT NOT NULL, "
            "content TEXT NOT NULL, "
            "timestamp REAL NOT NULL, "
            "status TEXT NOT NULL, "
            "error TEXT NOT NULL DEFAULT '', "
            "target TEXT NOT NULL DEFAULT '', "
            "priority INTEGER NOT NULL, "
            "tenant_id TEXT NOT NULL)"
        )
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_echo_history_ts ON {_TABLE}(timestamp)")
        return conn

    def insert(
        self,
        *,
        msg_id: str,
        channel: str,
        title: str,
        content: str,
        timestamp: float,
        status: str,
        error: str,
        target: str,
        priority: int,
        tenant_id: str,
    ) -> None:
        """写穿一行消息历史。失败如实抛（禁静默丢写），是否阻断调用方归消费方裁决。

        msg_id=PRIMARY KEY：同 id 重复写=真撞行（dispatcher 侧 counter 回填从
        结构上消撞），撞行如实抛可见，禁 INSERT OR REPLACE 静默覆盖旧行=禁丢史。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ("
                    + ", ".join(_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(msg_id),
                        str(channel),
                        str(title),
                        str(content),
                        float(timestamp),
                        str(status),
                        str(error),
                        str(target),
                        int(priority),
                        str(tenant_id),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_recent(self, limit: int = 1000) -> list[dict]:
        """回载最近 limit 行（时间序升序返回=deque 回填序，最旧行在前）。

        回载只读不建库：库文件缺失=空历史起步直接返回（防模块 import 期构造
        回载顺手建出空生产库——写穿才建库，RV-①b-② load_all 同纪律）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE}"
                    " ORDER BY timestamp DESC, rowid DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            finally:
                conn.close()
        return [dict(zip(_COLUMNS, row)) for row in reversed(rows)]
