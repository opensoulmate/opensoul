"""SQLite persistence for gland TokenMeter 用量记录（RV-①b-①）——用量 UI 真源唯一落盘口。

修前（RV-① A 组定性）：TokenMeter._records/_by_model/_by_provider/_by_user 全在
进程内存，record() 只 append——gland 用量 UI 真源（DEC-㉖-①/㉖B2-① 建的显示面消费
recent_records/summary）随 service 重启归零=用量面显示空。本模块把用量行落
SQLite：record() 随写随落盘，进程启动回载，重启用量不丢。

GR-①b-①（内存窗+计数真源化）读口收口：load_recent(limit)=有界读（内存窗回填/
读面回落共用，不为回载全表进内存）+ counters()=全表行聚合单一口（GROUP BY
provider,model,user_id 单查询 Python 归集，禁第二套聚合查询 MEM-⑥ 纪律）；
load_all 退场（修前唯一消费点=全量回载致 _records 无界，随内存窗收口一并替换，
契约随更新）。

铁律（本仓 SQLite 约定）：user_id/task 显式 str()、纯 sqlite3 语法（migrations 是
PG 语法禁直接跑）、无 dict/list 列（UsageRecord 各字段均标量，无需 json.dumps）。

聚合不落盘（MEM-⑥「计数对账真源化」纪律）：_by_model/_by_provider/_by_user/_
total 是用量行的派生值——回载时从行真源折叠重算，禁第二套聚合列（两套真源=漂移
复发面）。

测试隔离：GLAND_USAGE_DB 环境变量覆盖库路径（BRAIN_MEMORY_DB/NEST_DB 同款先例），
缺省 <repo>/data/gland_usage.db；库路径每次连接按 env 解析（模块级 gateway 单例
跨用例存活，测试面每用例 tmp 真源）。

诚实边界：
①写失败不阻断调用方但 logger.error 可见（F4b-㉕ 记账写点先例）——token_meter.
  record() 在 LLM 调用路径尾部，为记账落盘失败炸掉已完成的 LLM 回复不成比例；
  代价是故障窗口内该行只活内存、重启丢（如实记，非谎报 durable）。
②无老化/裁剪面：表只增不减（生产用量行=审计性质，与 metacognition_log 同轴）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

_TABLE = "gland_usage"

_COLUMNS = (
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "user_id",
    "task",
    "timestamp",
)


def default_db_path() -> str:
    """库路径：GLAND_USAGE_DB 环境变量优先（测试隔离），缺省 <repo>/data/gland_usage.db。"""
    env = os.environ.get("GLAND_USAGE_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "gland_usage.db")


def _empty_counters() -> dict:
    """counters() 零行形状（单一形状禁两套：与聚合产物逐键一致）。"""
    return {
        "call_count": 0,
        "total_tokens": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "by_model": {},
        "by_user": {},
        "by_provider": {},
    }


class UsagePersistence:
    """用量行持久化真源（单表 gland_usage：insert 写穿 + load_recent 有界回载 +
    counters 计数真源）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 gateway 单例跨用例存活，测试面每用例 tmp 真源。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS gland_usage ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "provider TEXT NOT NULL, "
            "model TEXT NOT NULL, "
            "prompt_tokens INTEGER NOT NULL, "
            "completion_tokens INTEGER NOT NULL, "
            "total_tokens INTEGER NOT NULL, "
            "user_id TEXT, "
            "task TEXT, "
            "timestamp REAL NOT NULL)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gland_usage_ts ON gland_usage(timestamp)")
        return conn

    def insert(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        user_id: str | None = None,
        task: str | None = None,
        timestamp: float,
    ) -> None:
        """写穿一行用量记录。失败如实抛（禁静默丢写），是否阻断调用方归消费方裁决。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO gland_usage ("
                    + ", ".join(_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        provider,
                        model,
                        int(prompt_tokens),
                        int(completion_tokens),
                        int(total_tokens),
                        str(user_id) if user_id is not None else None,
                        str(task) if task is not None else None,
                        float(timestamp),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_recent(self, limit: int = 1000) -> list[dict]:
        """回载最近 limit 行（rowid 升序返回=回填序/尾切语义对齐，最旧行在前）。

        回载只读不建库：库文件缺失=空账起步直接返回（防读口顺手建出空生产库——
        写穿才建库，RV-①b-②④ 同纪律）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT " + ", ".join(_COLUMNS) + " FROM gland_usage ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            finally:
                conn.close()
        return [dict(zip(_COLUMNS, row)) for row in reversed(rows)]

    def counters(self) -> dict:
        """计数真源（GR-①b-①「计数真源化」）：全表行聚合单一口——GROUP BY
        provider, model, user_id 单查询，Python 归集，禁第二套聚合查询（MEM-⑥）。

        返回形状与 TokenMeter 缓存派生逐键一致（单一形状禁两套）：
        {call_count, total_tokens, total_prompt_tokens, total_completion_tokens,
         by_model, by_user, by_provider}。user_id NULL 行不进 by_user（与 record()
        聚合口径一致：user_id 提供才计）；聚合恒算全表（内存窗裁剪不影响计数）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return _empty_counters()
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT provider, model, user_id, COUNT(*), "
                    "SUM(prompt_tokens), SUM(completion_tokens), SUM(total_tokens) "
                    "FROM gland_usage GROUP BY provider, model, user_id"
                ).fetchall()
            finally:
                conn.close()
        out = _empty_counters()
        for provider, model, user_id, cnt, prompt, completion, total in rows:
            n = int(cnt)
            out["call_count"] += n
            out["total_tokens"] += int(total)
            out["total_prompt_tokens"] += int(prompt)
            out["total_completion_tokens"] += int(completion)
            out["by_model"][model] = out["by_model"].get(model, 0) + int(total)
            if user_id is not None:
                out["by_user"][str(user_id)] = out["by_user"].get(str(user_id), 0) + int(total)
            prov = out["by_provider"].setdefault(
                str(provider), {"prompt": 0, "completion": 0, "total": 0, "requests": 0}
            )
            prov["prompt"] += int(prompt)
            prov["completion"] += int(completion)
            prov["total"] += int(total)
            prov["requests"] += n
        return out
