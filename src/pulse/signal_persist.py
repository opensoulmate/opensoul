"""SQLite persistence for PulseEngine._signals（RV-①b-⑨）——脉冲信号注册表唯一落盘口。

修前（RV-①c §5.1 藏活事实定性）：PulseEngine._signals 全在进程内存（dict），
create/tick/pause/resume/cancel/delete 只改内存——用户创建脉冲信号（POST
/api/pulse/signals + by-ID/tick/pause/resume 契约）随 service 重启全清，且
bootstrap 种子信号只在首次 bootstrap 建（main.py is_bootstrapped 一次性）=
重启后定时脉冲静默消失。本模块把信号行落 SQLite：变更点随写随落盘（状态
快照），首触懒回载，重启信号不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、payload 是 dict 列整体 json.dumps 落 TEXT（dict 列禁裸存）。

测试隔离：PULSE_SIGNALS_DB 环境变量覆盖库路径（GLAND_USAGE_DB/IMMUNE_AUDIT_DB
同款先例），缺省 <repo>/data/pulse_signals.db；库路径每次连接按 env 解析
（api/pulse 模块级 engine 单例跨用例存活，测试面每用例 tmp 真源）。

表语义（a2a_tasks/limb_tasks 同族「状态快照表」）：signal_id=PK upsert 当前态
一信号一行——与 echo_history/immune_audit_log 追加型历史表刻意不同（一个信号
多次状态迁移=同一行当前态更新，不是多行历史）；tick 史不落（_tick_history
有界 ring=RV-①c 拆出不随判，零建设）。

诚实边界：
①写失败不阻断信号动作本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——
  动作已进内存，为落盘失败炸掉定时信号动作不成比例；代价是故障窗口内该行只活
  内存、重启丢（如实记，非谎报 durable）。
②回载失败=空注册表起步+logger.error 可见，行真源仍在库、不置位下次触碰重试
  （RV-①b-⑤-1 语义）；不在回载故障上发明降级判法。
③payload JSON 不可解/枚举值不可解（未来版本行）=坏行跳过+logger.error 可见，
  不因单行坏数据丢整段注册表（禁静默丢：跳过必留日志真因；echo ④ 同纪律）。
  枚举合法值判定在 timer 侧（SignalType/SignalStatus 单一源），本模块只管行形。
④删行幂等：delete 不存在的 id=同一效果无异常（快照表语义）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_TABLE = "pulse_signals"

_COLUMNS = (
    "signal_id",
    "signal_type",
    "name",
    "interval_ms",
    "status",
    "callback_url",
    "payload",
    "created_at",
    "last_fired_at",
    "next_fire_at",
    "fire_count",
    "max_fires",
    "precision_ms",
    "drift_correction",
)


def default_db_path() -> str:
    """库路径：PULSE_SIGNALS_DB 环境变量优先（测试隔离），缺省 <repo>/data/pulse_signals.db。"""
    env = os.environ.get("PULSE_SIGNALS_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "pulse_signals.db")


class SignalPersistence:
    """脉冲信号注册表持久化真源（单表 pulse_signals：upsert 当前态 + load_all
    懒回载 + delete 幂等删行）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 engine 单例跨用例存活，测试面每用例 tmp
        # 真源。构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
        # （RV-①b-②④ 同纪律），写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "signal_id TEXT PRIMARY KEY, "
            "signal_type TEXT NOT NULL, "
            "name TEXT NOT NULL, "
            "interval_ms REAL NOT NULL, "
            "status TEXT NOT NULL, "
            "callback_url TEXT NOT NULL DEFAULT '', "
            "payload TEXT NOT NULL DEFAULT '{}', "
            "created_at REAL NOT NULL, "
            "last_fired_at REAL, "
            "next_fire_at REAL, "
            "fire_count INTEGER NOT NULL DEFAULT 0, "
            "max_fires INTEGER NOT NULL DEFAULT 0, "
            "precision_ms REAL NOT NULL DEFAULT 1.0, "
            "drift_correction REAL NOT NULL DEFAULT 0.0)"
        )
        return conn

    def upsert(self, row: dict) -> None:
        """写穿一行信号当前态（signal_id=PK 状态快照：一信号一行，重复写=当前态
        更新，与追加型历史表刻意分语义）。失败如实抛（禁静默丢写），是否阻断
        调用方归消费方裁决（PulseEngine 侧=记账旁路纪律）。

        payload dict 整体 json.dumps 落 TEXT 列（dict 列禁裸存铁律）；
        last_fired_at/next_fire_at 可 NULL（从未触发的信号）显式往返。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ("
                    + ", ".join(_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(signal_id) DO UPDATE SET "
                    "signal_type=excluded.signal_type, name=excluded.name, "
                    "interval_ms=excluded.interval_ms, status=excluded.status, "
                    "callback_url=excluded.callback_url, payload=excluded.payload, "
                    "created_at=excluded.created_at, last_fired_at=excluded.last_fired_at, "
                    "next_fire_at=excluded.next_fire_at, fire_count=excluded.fire_count, "
                    "max_fires=excluded.max_fires, precision_ms=excluded.precision_ms, "
                    "drift_correction=excluded.drift_correction",
                    (
                        str(row["signal_id"]),
                        str(row["signal_type"]),
                        str(row["name"]),
                        float(row["interval_ms"]),
                        str(row["status"]),
                        str(row.get("callback_url") or ""),
                        json.dumps(row.get("payload") or {}, ensure_ascii=False),
                        float(row["created_at"]),
                        None if row.get("last_fired_at") is None else float(row["last_fired_at"]),
                        None if row.get("next_fire_at") is None else float(row["next_fire_at"]),
                        int(row.get("fire_count") or 0),
                        int(row.get("max_fires") or 0),
                        float(1.0 if row.get("precision_ms") is None else row["precision_ms"]),
                        float(row.get("drift_correction") or 0.0),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete(self, signal_id: str) -> None:
        """删一行（删信号=删行，快照表语义：delete 幂等，缺行=同一效果无异常）。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(f"DELETE FROM {_TABLE} WHERE signal_id = ?", (str(signal_id),))
                conn.commit()
            finally:
                conn.close()

    def load_all(self) -> list[dict]:
        """回载全部信号行（rowid 升序=创建/写入序；条目 dict 键集=PulseSignal 字段集，
        payload 还原为 dict=与内存 PulseSignal 往返形）。

        回载只读不建库：库文件缺失=空注册表直接返回（防模块 import 期构造回载
        顺手建出空生产库——写穿才建库，RV-①b-②④ 同纪律）。
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
                signal_id,
                signal_type,
                name,
                interval_ms,
                status,
                callback_url,
                payload_raw,
                created_at,
                last_fired_at,
                next_fire_at,
                fire_count,
                max_fires,
                precision_ms,
                drift_correction,
            ) = row
            try:
                payload = json.loads(payload_raw) if payload_raw else {}
                if not isinstance(payload, dict):
                    raise ValueError(f"payload 非对象: {type(payload).__name__}")
            except Exception as e:
                logger.error(f"pulse 信号坏行跳过（signal_id={signal_id}，payload 不可解）: {e}")
                continue
            out.append(
                {
                    "signal_id": str(signal_id),
                    "signal_type": str(signal_type),
                    "name": str(name),
                    "interval_ms": float(interval_ms),
                    "status": str(status),
                    "callback_url": str(callback_url or ""),
                    "payload": payload,
                    "created_at": float(created_at),
                    "last_fired_at": None if last_fired_at is None else float(last_fired_at),
                    "next_fire_at": None if next_fire_at is None else float(next_fire_at),
                    "fire_count": int(fire_count),
                    "max_fires": int(max_fires),
                    "precision_ms": float(precision_ms),
                    "drift_correction": float(drift_correction),
                }
            )
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
