"""SQLite persistence for immune IPAccessControl 黑白名单（RV-①b-②）——封禁名单唯一落盘口。

修前（RV-① A 组定性）：IPAccessControl._blacklist/_whitelist 全在进程内存，
blacklist_add/whitelist_add 只写内存——**安全面**：运行时拉黑的 IP（含 intrusion
brute_force_auto_block 自动封禁）随 service 重启即解封。本模块把名单行落 SQLite：
增删随写随落盘，进程启动回载，重启封禁不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
key/ip/reason 显式 str()、无 dict/list 列（各行字段均标量，无需 json.dumps）。

行状态整行落盘（hit_count 含）：hit_count 是条目自身的观测状态非他行派生值
（MEM-⑥「计数对账真源化」禁的是双聚合真源，此处只有行真源一份），随变更写穿
（增删/命中计数/过期清理各写点），重启后 list_blacklist 可见面 hit_count 不归零。

测试隔离：IMMUNE_ACCESS_DB 环境变量覆盖库路径（GLAND_USAGE_DB/BRAIN_MEMORY_DB
同款先例），缺省 <repo>/data/immune_access.db；库路径每次连接按 env 解析（模块级
api/immune 单例跨用例存活，测试面每用例 tmp 真源）。

诚实边界：
①写失败不阻断调用方但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——黑名单
  动作本体仍落内存（当下即生效），代价是故障窗口内该变更只活内存、重启丢/复活
  （如实记，非谎报 durable）。
②回载失败=空名单起步+logger.error 可见，行真源仍在库下次启动重试——安全面
  如实记：回载故障窗口内白名单-only 模式（「名单非空=只放行名单内」）不生效
  =放行面变宽，运维须看日志；不在回载故障上发明降级判法（语义同 RV-①b-①）。
③过期语义与纯内存逐字一致：过期条目回载原样（进程不停机时过期条目同样留到
  下次命中才懒清理），懒清理同时清内存与库行。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

_KINDS = ("blacklist", "whitelist")


def default_db_path() -> str:
    """库路径：IMMUNE_ACCESS_DB 环境变量优先（测试隔离），缺省 <repo>/data/immune_access.db。"""
    env = os.environ.get("IMMUNE_ACCESS_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "immune_access.db")


class AccessPersistence:
    """黑白名单持久化真源（单表 immune_access：upsert 写穿 + load_all 启动回载）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级单例跨用例存活，测试面每用例 tmp 真源。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS immune_access ("
            "list_kind TEXT NOT NULL, "
            "entry_key TEXT NOT NULL, "
            "ip TEXT NOT NULL, "
            "reason TEXT NOT NULL DEFAULT '', "
            "added_at REAL NOT NULL, "
            "expires_at REAL, "
            "hit_count INTEGER NOT NULL DEFAULT 0, "
            "PRIMARY KEY (list_kind, entry_key))"
        )
        return conn

    def upsert(
        self,
        *,
        list_kind: str,
        entry_key: str,
        ip: str,
        reason: str,
        added_at: float,
        expires_at: float | None,
        hit_count: int,
    ) -> None:
        """写穿一行名单条目（整行覆盖=内存条目即真源，hit_count 随变更同步）。失败如实抛。"""
        if list_kind not in _KINDS:
            raise ValueError(f"unknown list_kind: {list_kind!r}")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO immune_access ("
                    "list_kind, entry_key, ip, reason, added_at, expires_at, hit_count"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(list_kind),
                        str(entry_key),
                        str(ip),
                        str(reason),
                        float(added_at),
                        float(expires_at) if expires_at is not None else None,
                        int(hit_count),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete(self, list_kind: str, entry_key: str) -> None:
        """删除一行名单条目（幂等：无行=no-op，与内存 pop 缺省 None 同语义）。失败如实抛。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM immune_access WHERE list_kind=? AND entry_key=?",
                    (str(list_kind), str(entry_key)),
                )
                conn.commit()
            finally:
                conn.close()

    def load_all(self) -> list[dict]:
        """回载全部名单行（list_kind/entry_key/ip/reason/added_at/expires_at/hit_count）。

        回载只读不建库：库文件缺失=空名单起步直接返回（防模块 import 期构造回载
        顺手建出空生产库——写穿才建库）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT list_kind, entry_key, ip, reason, added_at, expires_at, hit_count"
                    " FROM immune_access ORDER BY list_kind, entry_key"
                ).fetchall()
            finally:
                conn.close()
        keys = ("list_kind", "entry_key", "ip", "reason", "added_at", "expires_at", "hit_count")
        return [dict(zip(keys, row)) for row in rows]
