"""SQLite persistence for DreamDistiller._dream_history（GR-①b-④）——梦史唯一落盘口。

修前（GR-① 定性「事件流型真无界」）：_dream_history 每次 dream() 运行 append 一行
（DreamResult.to_dict()）永不裁剪，get_stats 三个计数（total_dreams/
total_actions_applied/echo_blocked_count）全量遍历派生——长跑进程内存无界增长，
且与「裁剪即破坏总计数」耦合（census 定性：须计数真源化先行）。本模块落行真源：
dream() 随写随落盘，回载/读面回落共用，内存窗改纯缓存后计数恒对账行聚合。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、counts dict 列 json.dumps()/json.loads() 单点收口、
dream_id 显式 str()。

测试隔离：DREAM_HISTORY_DB 环境变量覆盖库路径（GLAND_USAGE_DB/ECHO_HISTORY_DB
同款先例），缺省 <repo>/data/dream_history.db；库路径每次连接按 env 解析（模块级
api/hippo _dream_distiller 单例跨用例存活，测试面每用例 tmp 真源）。

诚实边界：
①写失败不阻断调用方但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——dream
  蒸馏本体已跑完，为落盘失败炸掉已完成的蒸馏不成比例；代价是故障窗口内该行只活
  内存窗、回落/重启后不可见（如实记，非谎报 durable）。
②回载失败=空史起步+logger.error 可见，行真源仍在库下次启动重试（RV-①b-②
  语义）；不在回载故障上发明降级判法。
③无老化/裁剪面：表只增不减（梦史=记忆蒸馏审计性质；DB retention 归
  GR-①b-⑦ 统一拍板件，本件不自决删史）。
④行内单字段不可解（counts 坏 JSON 等）跳过该行并 logger.error 可见，不因单行
  坏数据丢整段史（禁静默丢：跳过必留日志真因）。
⑤dream_id=PRIMARY KEY 重复写=真撞行，如实抛可见（INSERT 禁 OR REPLACE 静默
  覆盖旧行=禁丢史）；调用方按记账旁路纪律吞掉并留日志。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger("opensoul.hippo.dream_persist")

_TABLE = "dream_history"

# 行列（单一源：load_recent 回读形状=DreamResult.to_dict() 逐键同形状）
_COLUMNS = (
    "dream_id",
    "total_actions",
    "counts",
    "applied",
    "skipped",
    "failed",
    "echo_blocked",
    "error",
    "raw_response",
    "redacted_spans",
    "created_at",
)


def default_db_path() -> str:
    """库路径：DREAM_HISTORY_DB 环境变量优先（测试隔离），缺省 <repo>/data/dream_history.db。"""
    env = os.environ.get("DREAM_HISTORY_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "dream_history.db")


def _empty_counters() -> dict:
    """计数空面（与 counters() 逐键同形状，单一形状禁两套）。"""
    return {"total_dreams": 0, "total_applied": 0, "echo_blocked": 0}


class DreamPersistence:
    """梦史持久化真源（单表 dream_history：insert 写穿 + load_recent 有界回载 + counters 行聚合）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 _dream_distiller 单例跨用例存活，测试面每
        # 用例 tmp 真源。构造零 IO（不建库）：回载只读不建库防 import 期建空生产
        # 库（RV-①b-② 同纪律），写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "dream_id TEXT PRIMARY KEY, "
            "total_actions INTEGER NOT NULL, "
            "counts TEXT NOT NULL, "
            "applied INTEGER NOT NULL, "
            "skipped INTEGER NOT NULL, "
            "failed INTEGER NOT NULL, "
            "echo_blocked INTEGER NOT NULL, "
            "error TEXT NOT NULL DEFAULT '', "
            "raw_response TEXT NOT NULL DEFAULT '', "
            "redacted_spans INTEGER NOT NULL DEFAULT 0, "
            "created_at REAL NOT NULL)"
        )
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_dream_history_ts ON {_TABLE}(created_at)")
        return conn

    def insert(self, record: dict) -> None:
        """写穿一行梦史（record=DreamResult.to_dict() 形状，逐键取用单一形状）。

        失败如实抛（禁静默丢写），是否阻断调用方归消费方裁决（dream 记账旁路
        纪律：蒸馏本体已跑完，落盘失败只 logger.error 不阻断）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ("
                    + ", ".join(_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(record.get("dream_id", "")),
                        int(record.get("total_actions", 0)),
                        json.dumps(record.get("counts", {})),
                        int(record.get("applied", 0)),
                        int(record.get("skipped", 0)),
                        int(record.get("failed", 0)),
                        1 if record.get("echo_blocked") else 0,
                        str(record.get("error", "")),
                        str(record.get("raw_response", "")),
                        int(record.get("redacted_spans", 0)),
                        float(record.get("created_at", 0.0)),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_recent(self, limit: int = 1000) -> list[dict]:
        """回载最近 limit 行（时间序升序返回=内存窗回填序，最旧行在前）。

        回载只读不建库：库文件缺失=空史起步直接返回（防 import 期构造回载顺手
        建出空生产库——写穿才建库，RV-①b-② load_recent 同纪律）。行字段重建为
        DreamResult.to_dict() 形状（echo_blocked 还原 bool、counts 还原 dict，
        与内存窗内 dict 逐键同形状=读面零可观测变化）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE}"
                    " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            finally:
                conn.close()
        out: list[dict] = []
        for row in reversed(rows):
            raw = dict(zip(_COLUMNS, row))
            try:
                counts = json.loads(raw["counts"]) if raw["counts"] else {}
            except (TypeError, ValueError) as exc:
                logger.error(
                    "dream history row %s counts unreadable, skipped: %s", raw["dream_id"], exc
                )
                continue
            out.append(
                {
                    "dream_id": raw["dream_id"],
                    "total_actions": raw["total_actions"],
                    "counts": counts,
                    "applied": raw["applied"],
                    "skipped": raw["skipped"],
                    "failed": raw["failed"],
                    "echo_blocked": bool(raw["echo_blocked"]),
                    "error": raw["error"],
                    "raw_response": raw["raw_response"],
                    "redacted_spans": raw["redacted_spans"],
                    "created_at": raw["created_at"],
                }
            )
        return out

    def counters(self) -> dict:
        """计数真源（GR-①b-④「计数真源化」MEM-⑥ 纪律）：全表行聚合单一口——
        单查询 COUNT/SUM，禁第二套聚合查询（两套真源=漂移复发面）。

        返回形状与 DreamDistiller 缓存派生逐键一致（单一形状禁两套）：
        {total_dreams, total_applied, echo_blocked}。聚合恒算全表（内存窗裁剪
        不影响计数=历史总量语义不变）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return _empty_counters()
            conn = self._connect()
            try:
                row = conn.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(applied), 0), "
                    f"COALESCE(SUM(echo_blocked), 0) FROM {_TABLE}"
                ).fetchone()
            finally:
                conn.close()
        return {
            "total_dreams": int(row[0]),
            "total_applied": int(row[1]),
            "echo_blocked": int(row[2]),
        }
