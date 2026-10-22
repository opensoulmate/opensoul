"""SQLite persistence for RPAExecutor._history（RV-①b-⑦-2）——执行历史唯一落盘口。

修前（RV-① A 组定性）：RPAExecutor._history 全在进程内存（list，max_history=1000
裁剪窗），_record_history 只 append——/history 执行历史与 stats 计数
（_total_executed/_total_succeeded/_total_failed/_tenant_counters）随 service
重启归零=执行史丢、计数漂移。本模块把执行历史行落 SQLite：每次执行随写随落盘
（追加型），首触懒回载，重启执行史不丢；计数读面对账行聚合计数真源化
（MEM-⑥ 纪律：计数器=行缓存视图，值读面读真源禁信第二套聚合列）。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、无 dict/list 列（历史条目各字段均标量，无需 json.dumps）。

测试隔离：LIMB_HISTORY_DB 环境变量覆盖库路径（GLAND_USAGE_DB/IMMUNE_ACCESS_DB/
ECHO_HISTORY_DB/LIMB_TASKS_DB 同款先例），缺省 <repo>/data/limb_history.db；
库路径每次连接按 env 解析（模块级 executor 单例跨用例存活，测试面每用例 tmp
真源）。

表语义（echo_history 同族「追加型历史表」，与 limb_tasks 状态快照表刻意不同）：
limb_history 是**追加型**——同一任务多次执行=多行如实各计，INSERT 禁 REPLACE
（禁丢史，echo_history 同纪律）；无自然 id（条目身份不对外暴露）故主键=
AUTOINCREMENT rowid（echo 的 msg_id 猜撞/回填机制在本表构造上不可达，如实记）。

诚实边界：
①写失败不阻断执行本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——
  执行已跑完，为落盘失败炸掉已完成任务不成比例；代价是故障窗口内该行只活内存、
  重启丢（如实记，非谎报 durable）。
②回载失败=空历史起步+logger.error 可见，行真源仍在库下次启动重试（RV-①b-②
  语义）；不在回载故障上发明降级判法。
③无老化/裁剪面：表只增不减；内存面 max_history=1000 既有裁剪语义不变（回载只取
  最近 max_history 行=内存窗回填语义，更旧行留在库不进内存——echo RV-①b-④ ③
  同语义）。计数聚合恒算全表（内存计数从不因裁剪减少，全表聚合才保语义不漂移）。
④建设前的历史执行不可重建（修前无落盘口、无中生有=fabricate）——计数从落盘行
  起算如实，不虚构旧次数。
⑤行内单字段异常（如 status 非枚举值）不跳行：计数按 status 精确串分类（F4b-㉗
  纪律），非 'completed'/'failed' 串只计 executed 不硬塞 succeeded/failed。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

_TABLE = "limb_history"

_COLUMNS = (
    "task_id",
    "name",
    "status",
    "steps",
    "elapsed_seconds",
    "error",
    "completed_at",
    "tenant_id",
)

# 成败轴单一源精确串（F4b-㉗ 纪律：禁字面抄写漂移、禁 LIKE 子串误计）
_SUCCESS = "completed"
_FAILURE = "failed"


def default_db_path() -> str:
    """库路径：LIMB_HISTORY_DB 环境变量优先（测试隔离），缺省 <repo>/data/limb_history.db。"""
    env = os.environ.get("LIMB_HISTORY_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "limb_history.db")


class HistoryPersistence:
    """执行历史持久化真源（单表 limb_history：insert 追加写穿 + load_recent 懒回载 +
    counters 行聚合=计数真源）。"""

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
            "row_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "task_id TEXT NOT NULL, "
            "name TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "steps INTEGER NOT NULL, "
            "elapsed_seconds REAL NOT NULL, "
            "error TEXT NOT NULL DEFAULT '', "
            "completed_at REAL, "
            "tenant_id TEXT NOT NULL)"
        )
        return conn

    def insert(
        self,
        *,
        task_id: str,
        name: str,
        status: str,
        steps: int,
        elapsed_seconds: float,
        error: str,
        completed_at: float | None,
        tenant_id: str,
    ) -> None:
        """追加一行执行历史。失败如实抛（禁静默丢写），是否阻断调用方归消费方
        裁决（RPAExecutor 侧=记账旁路纪律：logger.error 不阻断执行本体）。

        追加型语义（echo_history 同纪律）：同 task_id 多次执行=多行各计，禁
        INSERT OR REPLACE 覆盖旧行=禁丢史。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ("
                    + ", ".join(_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(task_id),
                        str(name),
                        str(status),
                        int(steps),
                        float(elapsed_seconds),
                        str(error),
                        None if completed_at is None else float(completed_at),
                        str(tenant_id),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_recent(self, limit: int = 1000) -> list[dict]:
        """回载最近 limit 行（rowid 升序返回=内存窗回填序，最旧行在前；条目 dict
        键集与内存 _history 条目逐键一致=往返形）。

        回载只读不建库：库文件缺失=空历史起步直接返回（防模块 import 期构造
        回载顺手建出空生产库——写穿才建库，RV-①b-②④ 同纪律）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} ORDER BY row_id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            finally:
                conn.close()
        return [dict(zip(_COLUMNS, row)) for row in reversed(rows)]

    def counters(self) -> dict:
        """计数真源（RV-①b-⑦-2「计数真源化」）：全表行聚合单一口——全局
        executed/succeeded/failed + 分租户桶（GROUP BY tenant_id, status 单查询，
        Python 归集，禁第二套聚合查询 MEM-⑥ 纪律）。

        成败按精确串分类（F4b-㉗）：succeeded=COUNT(status='completed')、
        failed=COUNT(status='failed')、executed=COUNT(*)（非成败语义串只计
        executed 不硬塞，诚实边界⑤）。聚合恒算全表（诚实边界③）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return {
                    "executed": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "by_tenant": {},
                }
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT tenant_id, status, COUNT(*) FROM {_TABLE} GROUP BY tenant_id, status"
                ).fetchall()
            finally:
                conn.close()
        out: dict = {"executed": 0, "succeeded": 0, "failed": 0, "by_tenant": {}}
        for tenant_id, status, cnt in rows:
            key = str(tenant_id)
            bucket = out["by_tenant"].setdefault(key, {"executed": 0, "succeeded": 0, "failed": 0})
            n = int(cnt)
            out["executed"] += n
            bucket["executed"] += n
            if status == _SUCCESS:
                out["succeeded"] += n
                bucket["succeeded"] += n
            elif status == _FAILURE:
                out["failed"] += n
                bucket["failed"] += n
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
