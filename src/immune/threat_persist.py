"""SQLite persistence for IntrusionDetector._threat_history/_login_attempts（RV-①b-⑪）——
威胁史+爆破检测证据窗唯一落盘口。

修前（RV-①c §5.1 藏活事实定性）：IntrusionDetector._threat_history（ThreatEvent ring）
与 _login_attempts（per-IP 失败登录时间窗 deque maxlen=100）全在进程内存，写点只
append——威胁事件史随 service 重启清史=安全证据丢；爆破检测证据窗随重启清零=攻击者
诱导重启即重置爆破计数（auto-block 前置状态，auto-block 结果本身已由 RV-①b-②
access_control 名单持久化，此处持久化的是过程史/证据窗）。本模块把两面落 SQLite：
随写随落盘（追加型），首触懒回载，重启威胁史/爆破计数不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、无 dict 列（ThreatEvent 全标量字段逐列落，无需 json.dumps）。

测试隔离：IMMUNE_INTRUSION_DB 环境变量覆盖库路径（GLAND_USAGE_DB/IMMUNE_ACCESS_DB/
IMMUNE_AUDIT_DB/ECHO_HISTORY_DB 同款先例），缺省 <repo>/data/immune_intrusion.db；
库路径每次连接按 env 解析（模块级 api/immune intrusion 单例跨用例存活，测试面每
用例 tmp 真源）。

表语义（echo_history/immune_audit_log 同族「追加型历史表」）两表：
- intrusion_threats：一次检出一行（ThreatEvent 逐字段），INSERT 禁 REPLACE 禁丢史；
  threat_id 是 _make_threat 的 8 位 uuid4 截断（非保证唯一）故不作自然主键——
  row_id AUTOINCREMENT 主键（limb_history「条目无自然 id」同款裁决）。
- intrusion_logins：一次登录尝试一行（内存 _login_attempts 只存时间戳，行=内存
  逐键保真刻意不多落成败标记——无消费方的装饰列不落）；row_id AUTOINCREMENT。

诚实边界：
①写失败不阻断检测本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——威胁/
  登录记录已进内存，为落盘失败炸掉检测动作不成比例；代价是故障窗口内该行只活
  内存、重启丢（如实记，非谎报 durable）。
②回载失败=空史起步+logger.error 可见，行真源仍在库下次触碰重试（RV-①b-② 语义）。
③无老化/裁剪面：两表只增不减（登录尝试行=每次登录一行如实增长，echo RV-①b-④ ③
  同款无界增长如实记，cleanup/retention 留另拍）；内存面 maxlen 既有 ring/窗语义
  不变（回载只取最近窗=回填语义，更旧行留在库不进内存）。
④坏行（attack_type/threat_level 不在单一源枚举内）跳过该行并 logger.error 可见，
  不因单行坏数据丢整段威胁史（禁静默丢：跳过必留日志真因；echo ④ 同纪律）。
⑤计数从落盘行起算如实：修前无落盘口、旧计数不可重建（无中生有=fabricate），
  不虚构旧次数（audit ⑤ 同纪律）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_THREAT_TABLE = "intrusion_threats"
_LOGIN_TABLE = "intrusion_logins"

_THREAT_COLUMNS = (
    "threat_id",
    "attack_type",
    "threat_level",
    "source_ip",
    "path",
    "method",
    "detail",
    "matched_pattern",
    "blocked",
    "timestamp",
)


def default_db_path() -> str:
    """库路径：IMMUNE_INTRUSION_DB 环境变量优先（测试隔离），缺省 <repo>/data/immune_intrusion.db。"""
    env = os.environ.get("IMMUNE_INTRUSION_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "immune_intrusion.db")


class IntrusionPersistence:
    """威胁史+爆破证据窗持久化真源（两表：intrusion_threats 追加写穿 +
    load_threats 懒回载 + threat_counters 行聚合=计数真源；intrusion_logins
    追加写穿 + load_login_attempts per-IP 窗回载）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析。构造零 IO
        # （不建库）：回载只读不建库防 import 期建空生产库（RV-①b-②④ 同纪律），
        # 写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_THREAT_TABLE} ("
            "row_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "threat_id TEXT NOT NULL, "
            "attack_type TEXT NOT NULL, "
            "threat_level TEXT NOT NULL, "
            "source_ip TEXT NOT NULL, "
            "path TEXT NOT NULL, "
            "method TEXT NOT NULL, "
            "detail TEXT NOT NULL, "
            "matched_pattern TEXT NOT NULL DEFAULT '', "
            "blocked INTEGER NOT NULL DEFAULT 0, "
            "timestamp REAL NOT NULL)"
        )
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_LOGIN_TABLE} ("
            "row_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ip TEXT NOT NULL, "
            "ts REAL NOT NULL)"
        )
        return conn

    # ── 威胁史面 ─────────────────────────────────────────────────────────

    def insert_threat(
        self,
        *,
        threat_id: str,
        attack_type: str,
        threat_level: str,
        source_ip: str,
        path: str,
        method: str,
        detail: str,
        matched_pattern: str,
        blocked: bool,
        timestamp: float,
    ) -> None:
        """追加一行威胁事件。失败如实抛（禁静默丢写），是否阻断调用方归消费方
        裁决（IntrusionDetector 侧=记账旁路纪律：logger.error 不阻断检测本体）。

        追加型语义（echo_history/immune_audit_log 同纪律）：同一威胁多次检出=多行
        各计，禁 INSERT OR REPLACE 覆盖旧行=禁丢史。全列显式 str()/float()/int()。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_THREAT_TABLE} ("
                    + ", ".join(_THREAT_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(threat_id),
                        str(attack_type),
                        str(threat_level),
                        str(source_ip),
                        str(path),
                        str(method),
                        str(detail),
                        str(matched_pattern),
                        int(bool(blocked)),
                        float(timestamp),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_threats(self, limit: int = 10000) -> list[dict]:
        """回载最近 limit 行（rowid 升序返回=ring 回填序，最旧行在前；条目 dict
        键集=ThreatEvent.to_dict() 同键，blocked 还原 bool）。

        回载只读不建库：库文件缺失=空史起步直接返回（写穿才建库）。
        坏行（不可解列值）在消费侧按枚举收口跳过（诚实边界④）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT row_id, {', '.join(_THREAT_COLUMNS)} FROM {_THREAT_TABLE}"
                    " ORDER BY row_id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            finally:
                conn.close()
        out: list[dict] = []
        for row in reversed(rows):
            (
                _row_id,
                threat_id,
                attack_type,
                threat_level,
                source_ip,
                path_,
                method,
                detail,
                matched_pattern,
                blocked,
                timestamp,
            ) = row
            out.append(
                {
                    "threat_id": str(threat_id),
                    "attack_type": str(attack_type),
                    "threat_level": str(threat_level),
                    "source_ip": str(source_ip),
                    "path": str(path_),
                    "method": str(method),
                    "detail": str(detail),
                    "matched_pattern": str(matched_pattern),
                    "blocked": bool(blocked),
                    "timestamp": float(timestamp),
                }
            )
        return out

    def threat_counters(self) -> dict:
        """计数真源（RV-①b-⑦-2/⑧「计数真源化」同款）：全表行聚合单一口——
        GROUP BY attack_type, threat_level 单查询，Python 归集为内存 _stats 同键形
        （total_threats/by_type/by_level，单一形状禁两套 MEM-⑥ 纪律）。

        聚合恒算全表（诚实边界③）：内存 ring 裁剪不裁行=计数不随裁剪回退。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return {"total_threats": 0, "by_type": {}, "by_level": {}}
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT attack_type, threat_level, COUNT(*) FROM {_THREAT_TABLE}"
                    " GROUP BY attack_type, threat_level"
                ).fetchall()
            finally:
                conn.close()
        total = 0
        by_type: dict[str, int] = {}
        by_level: dict[str, int] = {}
        for attack_type, threat_level, cnt in rows:
            n = int(cnt)
            total += n
            by_type[str(attack_type)] = by_type.get(str(attack_type), 0) + n
            by_level[str(threat_level)] = by_level.get(str(threat_level), 0) + n
        return {"total_threats": total, "by_type": by_type, "by_level": by_level}

    def threat_count(self) -> int:
        """威胁史行数（探针/取证回读口）。库缺失=0。"""
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return 0
            conn = self._connect()
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {_THREAT_TABLE}").fetchone()
            finally:
                conn.close()
        return int(row[0])

    # ── 爆破证据窗面 ─────────────────────────────────────────────────────

    def insert_login(self, *, ip: str, ts: float) -> None:
        """追加一行登录尝试时间戳（内存 _login_attempts 只存时间戳=行逐键保真）。
        失败如实抛，阻断与否归消费方（同 insert_threat 纪律）。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_LOGIN_TABLE} (ip, ts) VALUES (?, ?)",
                    (str(ip), float(ts)),
                )
                conn.commit()
            finally:
                conn.close()

    def load_login_attempts(self, per_ip_limit: int = 100) -> dict[str, list[float]]:
        """回载每个 IP 最近 per_ip_limit 条尝试时间戳（升序=窗回填序）。

        per-IP 窗语义=内存 deque(maxlen=100) 既有裁剪（只留最近 100 条），更旧行
        留在库不进内存（诚实边界③）。窗口函数按 ip 分区取最近 N（SQLite 3.25+）。
        回载只读不建库：库文件缺失=空窗起步直接返回。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return {}
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT ip, ts FROM ("
                    f"SELECT ip, ts, row_id, ROW_NUMBER() OVER (PARTITION BY ip ORDER BY row_id DESC) AS rn"
                    f" FROM {_LOGIN_TABLE}) WHERE rn <= ? ORDER BY row_id",
                    (int(per_ip_limit),),
                ).fetchall()
            finally:
                conn.close()
        out: dict[str, list[float]] = {}
        for ip, ts in rows:
            out.setdefault(str(ip), []).append(float(ts))
        return out

    def login_count(self, ip: str | None = None) -> int:
        """登录尝试行数（探针/取证回读口）。库缺失=0。"""
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return 0
            conn = self._connect()
            try:
                if ip is None:
                    row = conn.execute(f"SELECT COUNT(*) FROM {_LOGIN_TABLE}").fetchone()
                else:
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM {_LOGIN_TABLE} WHERE ip = ?", (str(ip),)
                    ).fetchone()
            finally:
                conn.close()
        return int(row[0])
