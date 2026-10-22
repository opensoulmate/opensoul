"""SQLite persistence for AuditLogger._log（RV-①b-⑧）——安全审计留痕唯一落盘口。

修前（RV-①c §5.1 藏活事实定性）：AuditLogger._log 全在进程内存（deque maxlen=10000），
log() 只 append——安全审计留痕 ring（permission_denied/intrusion 等安全动作史，
api/immune.py 单例 5 写点真消费）随 service 重启归零=安全审计断档。本模块把审计
留痕行落 SQLite：log() 随写随落盘（追加型），首触懒回载，重启审计史不丢；计数
读面对账行聚合计数真源化（MEM-⑥ 纪律：计数器=行缓存视图，值读面读真源禁信
第二套聚合列；RV-①b-⑦-2 同款）。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、metadata 是 dict 列整体 json.dumps 落 TEXT（dict 列禁裸存）。

测试隔离：IMMUNE_AUDIT_DB 环境变量覆盖库路径（GLAND_USAGE_DB/IMMUNE_ACCESS_DB/
ECHO_HISTORY_DB/LIMB_HISTORY_DB 同款先例），缺省 <repo>/data/immune_audit.db；
库路径每次连接按 env 解析（模块级 api/immune audit 单例跨用例存活，测试面每用例
tmp 真源）。

表语义（echo_history/limb_history 同族「追加型历史表」）：审计留痕是**追加型**——
同一动作多次发生=多行如实各计，INSERT 禁 REPLACE（禁丢史=安全审计不可被覆盖）；
无自然 id（留痕身份不对外暴露）故主键=AUTOINCREMENT rowid（limb_history 同款）。

诚实边界：
①写失败不阻断审计本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——留痕
  已进内存 ring，为落盘失败炸掉审计动作不成比例；代价是故障窗口内该行只活内存、
  重启丢（如实记，非谎报 durable）。
②回载失败=空审计起步+logger.error 可见，行真源仍在库下次启动重试（RV-①b-②
  语义）；不在回载故障上发明降级判法。
③无老化/裁剪面：表只增不减；内存面 maxlen=10000 既有 ring 语义不变（回载只取
  最近 maxlen 行=ring 回填语义，更旧行留在库不进内存——echo RV-①b-④ ③ 同语义）。
  cleanup() 是内存头寸操作（原 docstring「为降级器官腾出内存头寸」），不删行=
  审计史不可被 cleanup 抹除（安全审计语义）；计数聚合恒算全表=裁剪不回退计数
  （audit.cleanup 既有契约「counters 是累计计数，不随裁剪回退」由此保真）。
④行内 metadata JSON 不可解（损坏行）跳过该行并 logger.error 可见，不因单行坏
  数据丢整段审计史（禁静默丢：跳过必留日志真因；echo ④ 同纪律）。
⑤计数从落盘行起算如实：修前无落盘口、旧计数不可重建（无中生有=fabricate），
  不虚构旧次数；写失败窗口内该行不进计数真源（计数恒对账行=落盘语义，内存
  计数缓存只作对账失败回落面）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_TABLE = "immune_audit_log"

_COLUMNS = (
    "timestamp",
    "action",
    "client_ip",
    "user_id",
    "endpoint",
    "detail",
    "risk_level",
    "metadata",
)


def default_db_path() -> str:
    """库路径：IMMUNE_AUDIT_DB 环境变量优先（测试隔离），缺省 <repo>/data/immune_audit.db。"""
    env = os.environ.get("IMMUNE_AUDIT_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "immune_audit.db")


class AuditPersistence:
    """安全审计留痕持久化真源（单表 immune_audit_log：insert 追加写穿 +
    load_recent 懒回载 + counters 行聚合=计数真源）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 audit 单例跨用例存活，测试面每用例 tmp
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
            "row_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "timestamp REAL NOT NULL, "
            "action TEXT NOT NULL, "
            "client_ip TEXT NOT NULL DEFAULT '', "
            "user_id TEXT NOT NULL DEFAULT '', "
            "endpoint TEXT NOT NULL DEFAULT '', "
            "detail TEXT NOT NULL DEFAULT '', "
            "risk_level TEXT NOT NULL, "
            "metadata TEXT NOT NULL DEFAULT '{}')"
        )
        return conn

    def insert(
        self,
        *,
        timestamp: float,
        action: str,
        client_ip: str,
        user_id: str,
        endpoint: str,
        detail: str,
        risk_level: str,
        metadata: dict,
    ) -> None:
        """追加一行审计留痕。失败如实抛（禁静默丢写），是否阻断调用方归消费方
        裁决（AuditLogger 侧=记账旁路纪律：logger.error 不阻断审计本体）。

        追加型语义（echo_history/limb_history 同纪律）：同一动作多次发生=多行
        各计，禁 INSERT OR REPLACE 覆盖旧行=禁丢史（安全审计不可被覆盖）。
        metadata dict 整体 json.dumps 落 TEXT 列（dict 列禁裸存铁律）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ("
                    + ", ".join(_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        float(timestamp),
                        str(action),
                        str(client_ip),
                        str(user_id),
                        str(endpoint),
                        str(detail),
                        str(risk_level),
                        json.dumps(metadata or {}, ensure_ascii=False),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_recent(self, limit: int = 10000) -> list[dict]:
        """回载最近 limit 行（rowid 升序返回=ring 回填序，最旧行在前；条目 dict
        键集=timestamp/action/client_ip/user_id/endpoint/detail/risk_level/metadata，
        metadata 还原为 dict=与内存 AuditEntry asdict 往返形）。

        回载只读不建库：库文件缺失=空审计起步直接返回（防模块 import 期构造
        回载顺手建出空生产库——写穿才建库，RV-①b-②④ 同纪律）。
        metadata JSON 不可解=坏行跳过+logger.error 可见（诚实边界④）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT row_id, {', '.join(_COLUMNS)} FROM {_TABLE}"
                    " ORDER BY row_id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            finally:
                conn.close()
        out: list[dict] = []
        for row in reversed(rows):
            row_id, ts, action, client_ip, user_id, endpoint, detail, risk_level, meta_raw = row
            try:
                meta = json.loads(meta_raw) if meta_raw else {}
                if not isinstance(meta, dict):
                    raise ValueError(f"metadata 非对象: {type(meta).__name__}")
            except Exception as e:
                logger.error(f"immune 审计留痕坏行跳过（row_id={row_id}，metadata 不可解）: {e}")
                continue
            out.append(
                {
                    "timestamp": float(ts),
                    "action": str(action),
                    "client_ip": str(client_ip),
                    "user_id": str(user_id),
                    "endpoint": str(endpoint),
                    "detail": str(detail),
                    "risk_level": str(risk_level),
                    "metadata": meta,
                }
            )
        return out

    def counters(self) -> dict:
        """计数真源（RV-①b-⑦-2「计数真源化」同款）：全表行聚合单一口——
        GROUP BY action, risk_level 单查询，Python 归集为内存 _counters 同键形
        （"<action>:<risk_level>"，单一形状禁两套 MEM-⑥ 纪律）。

        聚合恒算全表（诚实边界③）：cleanup 裁内存不裁行=计数不随裁剪回退
        （audit.cleanup 既有契约保真）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return {}
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT action, risk_level, COUNT(*) FROM {_TABLE} GROUP BY action, risk_level"
                ).fetchall()
            finally:
                conn.close()
        return {f"{action}:{risk}": int(cnt) for action, risk, cnt in rows}

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
