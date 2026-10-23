"""SQLite persistence for AlertManager._history（GR-①b-⑤）——告警史唯一落盘口。

修前（GR-① 定性「事件流型真无界」）：_history 每次告警触发 append 一行（AlertRecord）
永不裁剪，/api/vital/stats 的 alerts.total/active 计数（len(history)/未解决数）与
列表耦合派生——长跑进程内存无界增长，且与自身「告警史」语义不对称（零落盘口，
重启 total/active 归零、firing 状态全丢=重启后同条件重复告警重复通知）。本模块落行
真源：告警随写随落盘、解决态写穿更新，回载/读面回落共用，内存窗改纯缓存后计数
恒对账行聚合。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
全列显式 str()/float()/int()、alert_id 显式 str()。

测试隔离：ALERT_HISTORY_DB 环境变量覆盖库路径（DREAM_HISTORY_DB/GLAND_USAGE_DB
同款先例），缺省 <repo>/data/alert_history.db；库路径每次连接按 env 解析（缺省
default_db_path）——AlertManager 跨用例存活面同纪律，测试面每用例 tmp 真源。

表语义（healer_history「状态快照表」同款裁决如实记）：AlertRecord 是可变对象
（evaluate() 在记录之后把 resolved 置 True=终值迁移），行必须随影内存终值——
故 resolved 列随解决点 mark_resolved() 写穿（定向 UPDATE 该行，禁 INSERT OR
REPLACE 覆盖他行）；其余字段记录后不可变（AlertRecord 无第二处字段迁移），
insert 一次成行。排序键=隐式 rowid（修前 _history 列表序=append 序，rowid 序
逐行同序=读面零可观测变化；ts 可并列/回拨不作排序键）。

诚实边界：
①写失败不阻断告警本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——
  告警判定/通知已发出，为落盘失败炸掉告警不成比例；代价是故障窗口内该行只活
  内存窗、回落/重启后不可见（如实记，非谎报 durable）。
②回载失败=空史起步+logger.error 可见，行真源仍在库下次启动重试（GR-①b-④
  同语义）；不在回载故障上发明降级判法。
③无老化/裁剪面：表只增不减（告警史=运维审计性质；mark_resolved 是状态更新非
  删行；DB retention 归 GR-①b-⑦ 统一拍板件，本件不自决删史）。
④mark_resolved 对不存在的 alert_id=0 行更新静默 no-op（写失败行只活内存的解决
  态无法落行——内存对象仍置 resolved=True，读面回落时该行不可见=非谎报 durable
  的对应面，不为孤儿 UPDATE 抛错阻断告警解决本体）。
⑤alert_id=PRIMARY KEY 重复写=真撞行，如实抛可见（INSERT 禁 OR REPLACE 静默
  覆盖旧行=禁丢史）；调用方按记账旁路纪律吞掉并留日志。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger("opensoul.vital.alert_persist")

_TABLE = "alert_history"

# 行列（单一源：load_recent/load_all 回读形状=AlertRecord 行形状逐键同形）
_COLUMNS = (
    "alert_id",
    "rule_name",
    "severity",
    "message",
    "value",
    "threshold",
    "ts",
    "resolved",
)


def default_db_path() -> str:
    """库路径：ALERT_HISTORY_DB 环境变量优先（测试隔离），缺省 <repo>/data/alert_history.db。"""
    env = os.environ.get("ALERT_HISTORY_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "alert_history.db")


def _empty_counters() -> dict:
    """计数空面（与 counters() 逐键同形状，单一形状禁两套）。"""
    return {"total": 0, "active": 0}


class AlertPersistence:
    """告警史持久化真源（单表 alert_history：insert 写穿 + mark_resolved 终值写穿 +
    load_recent 有界回载 + load_all 全史回读 + counters 行聚合）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——缺省库路径随 env 走，测试面每用例 tmp 真源。构造
        # 零 IO（不建库）：回载只读不建库防构造期建空生产库（GR-①b-④ 同纪律），
        # 写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "alert_id TEXT PRIMARY KEY, "
            "rule_name TEXT NOT NULL, "
            "severity TEXT NOT NULL, "
            "message TEXT NOT NULL, "
            "value REAL NOT NULL, "
            "threshold REAL NOT NULL, "
            "ts REAL NOT NULL, "
            "resolved INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_alert_history_ts ON {_TABLE}(ts)")
        return conn

    def _exists(self) -> bool:
        path = self.explicit_path or default_db_path()
        return Path(path).exists()

    def insert(self, record: dict) -> None:
        """写穿一行告警史（record=AlertRecord 行形状 dict，逐键取用单一形状）。

        失败如实抛（禁静默丢写），是否阻断调用方归消费方裁决（AlertManager 记账
        旁路纪律：告警判定/通知已发出，落盘失败只 logger.error 不阻断）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ("
                    + ", ".join(_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(record.get("alert_id", "")),
                        str(record.get("rule_name", "")),
                        str(record.get("severity", "")),
                        str(record.get("message", "")),
                        float(record.get("value", 0.0)),
                        float(record.get("threshold", 0.0)),
                        float(record.get("ts", 0.0)),
                        1 if record.get("resolved") else 0,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def mark_resolved(self, alert_id: str) -> None:
        """终值写穿：该告警 resolved=1（AlertRecord.resolved=True 的行影，healer
        状态快照「行随影内存终值」同款）。定向 UPDATE 该行；不存在=0 行 no-op
        （诚实边界④）。失败如实抛，消费方记账旁路。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"UPDATE {_TABLE} SET resolved = 1 WHERE alert_id = ?",
                    (str(alert_id),),
                )
                conn.commit()
            finally:
                conn.close()

    def _load(self, conn: sqlite3.Connection, sql_tail: str, params: tuple) -> list[dict]:
        rows = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} {sql_tail}", params
        ).fetchall()
        out: list[dict] = []
        for row in rows:
            raw = dict(zip(_COLUMNS, row))
            out.append(
                {
                    "alert_id": raw["alert_id"],
                    "rule_name": raw["rule_name"],
                    "severity": raw["severity"],
                    "message": raw["message"],
                    "value": raw["value"],
                    "threshold": raw["threshold"],
                    "ts": raw["ts"],
                    "resolved": bool(raw["resolved"]),
                }
            )
        return out

    def load_recent(self, limit: int = 1000) -> list[dict]:
        """回载最近 limit 行（rowid 序升序返回=内存窗回填序，最旧行在前）。

        回载只读不建库：库文件缺失=空史起步直接返回（防构造期回载顺手建出空生产
        库——写穿才建库，GR-①b-④ load_recent 同纪律）。行重建为 AlertRecord 行
        形状（resolved 还原 bool，与内存窗内逐键同形状=读面零可观测变化）。
        """
        with self._lock:
            if not self._exists():
                return []
            conn = self._connect()
            try:
                rows = self._load(conn, "ORDER BY rowid DESC LIMIT ?", (int(limit),))
            finally:
                conn.close()
        return list(reversed(rows))

    def load_all(self) -> list[dict]:
        """全史回读口（/alerts 全量列表读面回落用；rowid 升序=append 序）。

        回载只读不建库（同 load_recent）。行形状与 load_recent 逐键同形（单一
        形状禁两套）。
        """
        with self._lock:
            if not self._exists():
                return []
            conn = self._connect()
            try:
                return self._load(conn, "ORDER BY rowid ASC", ())
            finally:
                conn.close()

    def counters(self) -> dict:
        """计数真源（GR-①b-⑤「计数真源化」MEM-⑥ 纪律）：全表行聚合单一口——
        单查询 COUNT，禁第二套聚合查询（两套真源=漂移复发面）。

        返回形状与 AlertManager 缓存派生逐键一致（单一形状禁两套）：
        {total, active}。聚合恒算全表（内存窗裁剪不影响计数=历史总量语义不变；
        active=resolved=0 行数=解决态写穿后的真实未解决数）。
        """
        with self._lock:
            if not self._exists():
                return _empty_counters()
            conn = self._connect()
            try:
                row = conn.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(1 - resolved), 0) FROM {_TABLE}"
                ).fetchone()
            finally:
                conn.close()
        return {"total": int(row[0]), "active": int(row[1])}
