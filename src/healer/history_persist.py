"""SQLite persistence for OrganHealer._history（RV-①b-⑬）——healer 修复史唯一落盘口。

修前（RV-①c §5.1 藏活事实定性）：OrganHealer._history（DiagnosisResult 列表，
_max_history=500 ring）全在进程内存，_record() 只 append——修复史（healer 修复史，
/api/healer/history 与 /api/healer/stats 真源、admin healer 页消费面）随 service
重启全清（诊断/修复/恢复的全量史丢、统计归零）。本模块把修复史落 SQLite：随写随
落盘（状态快照），首触懒回载，重启修复史/统计不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
全列显式 str()/float()/int()、symptoms list 列 json.dumps（读侧 json.loads）。

测试隔离：HEALER_HISTORY_DB 环境变量覆盖库路径（GLAND_USAGE_DB/IMMUNE_INTRUSION_DB/
ECHO_HISTORY_DB 同款先例），缺省 <repo>/data/healer_history.db；库路径每次连接按
env 解析（模块级 api/healer 单例跨用例存活，测试面每用例 tmp 真源）。

表语义（与 echo_history/immune_audit_log「追加型历史表」刻意不同的裁决如实记）：
healer_history 是**状态快照表**（RV-①b-⑥-3 limb template「当前态一模板一行」同款，
内存=真源、行=影子）——DiagnosisResult 是可变对象（heal() 在 diagnose() 记录之后
写 action_taken/action_success/severity=RECOVERED、api heal_all/cycle 写
healthy/severity），行必须随影内存终值（⑪「行与内存 ThreatEvent 逐键保真」纪律的
持续版）。故 entry_id=PK 一诊断一行 upsert（ON CONFLICT(entry_id) DO UPDATE）：
同一诊断的行随终值更新=不丢史（禁 INSERT OR REPLACE 覆盖**他**行），不同诊断各
entry_id 各行互不覆盖。排序键=隐式 rowid（entry_id TEXT 主键不占 rowid 别名）。

诚实边界：
①写失败不阻断诊断/修复本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——
  记录本体已在内存，为落盘失败炸掉诊断动作不成比例；代价是故障窗口内该行只活
  内存、重启丢（如实记，非谎报 durable）。
②回载失败=空史起步+logger.error 可见，行真源仍在库下次触碰不重试（已置 loaded
  位，RV-①b-② 语义）。
③无老化/裁剪面：行只增不减（每次诊断一行如实增长，echo RV-①b-④ ③ 同款无界增长
  如实记，cleanup/retention 留另拍）；内存面 _max_history ring 既有裁剪语义不变
  （回载只取最近 _max_history 行=回填语义，更旧行留在库不进内存）。
④坏行（symptoms JSON 解析失败/枚举不在单一源集合）跳过判定在消费侧单点
  （OrganHealer._ensure_loaded，跳过必 logger.error 真因禁静默丢；⑪ ④ 同纪律），
  本层 load_history 只做标量还原不做语义判定。
⑤计数从落盘行起算如实：修前无落盘口、旧史不可重建（无中生有=fabricate），
  不虚构旧次数（⑪ ⑤ 同纪律）。
⑥counters() 恒算全表（⑪ ③ 同纪律：内存 ring 裁剪不裁行=计数不随裁剪回退）——
  persist 在场时 stats 口径=全量落盘史（跨重启续算），与纯内存 ring（≤500）计数
  口径差异如实记（缺省 persist=None 字节不变）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

_TABLE = "healer_history"

_COLUMNS = (
    "entry_id",
    "organ",
    "healthy",
    "severity",
    "symptoms",
    "root_cause",
    "recommended_action",
    "action_taken",
    "action_success",
    "response_time_ms",
    "timestamp",
)

_RECOVERED = "recovered"
_ACTION_NONE = "none"


def default_db_path() -> str:
    """库路径：HEALER_HISTORY_DB 环境变量优先（测试隔离），缺省 <repo>/data/healer_history.db。"""
    env = os.environ.get("HEALER_HISTORY_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "healer_history.db")


def _zero_counters() -> dict:
    """空计数形状（=OrganHealer.stats() 空内存同形状，MEM-⑥ 单一形状纪律）。"""
    return {
        "total_diagnoses": 0,
        "healed": 0,
        "failed": 0,
        "actions_taken": 0,
        "actions_succeeded": 0,
        "success_rate": 0,
        "organ_failure_frequency": {},
        "recent_healthy_rate": 100.0,
    }


class HealerPersistence:
    """修复史持久化真源（healer_history 单表：状态快照 upsert——内存=真源、行=影子，
    entry_id=PK 一诊断一行随终值更新；load_history 懒回载 + counters 行聚合=计数真源
    （MEM-⑥））。"""

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
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "entry_id TEXT PRIMARY KEY, "
            "organ TEXT NOT NULL, "
            "healthy INTEGER NOT NULL, "
            "severity TEXT NOT NULL, "
            "symptoms TEXT NOT NULL, "
            "root_cause TEXT NOT NULL, "
            "recommended_action TEXT NOT NULL, "
            "action_taken TEXT NOT NULL, "
            "action_success INTEGER NOT NULL, "
            "response_time_ms REAL NOT NULL, "
            "timestamp REAL NOT NULL)"
        )
        return conn

    def upsert_result(
        self,
        *,
        entry_id: str,
        organ: str,
        healthy: bool,
        severity: str,
        symptoms: list[str],
        root_cause: str,
        recommended_action: str,
        action_taken: str,
        action_success: bool,
        response_time_ms: float,
        timestamp: float,
    ) -> None:
        """状态快照写穿（一诊断一行随终值 upsert）。失败如实抛（禁静默丢写），是否
        阻断调用方归消费方裁决（OrganHealer 侧=记账旁路纪律：logger.error 不阻断）。

        upsert 语义（诚实边界见模块头）：同 entry_id=同一诊断的终值随影更新（非丢史），
        异 entry_id 各行互不覆盖。全列显式 str()/int()/float()，symptoms json.dumps。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ("
                    + ", ".join(_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(entry_id) DO UPDATE SET "
                    "organ=excluded.organ, "
                    "healthy=excluded.healthy, "
                    "severity=excluded.severity, "
                    "symptoms=excluded.symptoms, "
                    "root_cause=excluded.root_cause, "
                    "recommended_action=excluded.recommended_action, "
                    "action_taken=excluded.action_taken, "
                    "action_success=excluded.action_success, "
                    "response_time_ms=excluded.response_time_ms, "
                    "timestamp=excluded.timestamp",
                    (
                        str(entry_id),
                        str(organ),
                        int(bool(healthy)),
                        str(severity),
                        json.dumps(list(symptoms), ensure_ascii=False),
                        str(root_cause),
                        str(recommended_action),
                        str(action_taken),
                        int(bool(action_success)),
                        float(response_time_ms),
                        float(timestamp),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_history(self, limit: int = 500) -> list[dict]:
        """回载最近 limit 行（rowid 升序返回=ring 回填序，最旧行在前；条目 dict 键集=
        DiagnosisResult 字段同键，healthy/action_success 还原 bool）。

        回载只读不建库：库文件缺失=空史起步直接返回（写穿才建库）。
        symptoms 如实回 JSON 原文（str）——坏行语义判定单点在消费侧（诚实边界④）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT rowid, {', '.join(_COLUMNS)} FROM {_TABLE}"
                    " ORDER BY rowid DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            finally:
                conn.close()
        out: list[dict] = []
        for row in reversed(rows):
            (
                _rowid,
                entry_id,
                organ,
                healthy,
                severity,
                symptoms,
                root_cause,
                recommended_action,
                action_taken,
                action_success,
                response_time_ms,
                timestamp,
            ) = row
            out.append(
                {
                    "entry_id": str(entry_id),
                    "organ": str(organ),
                    "healthy": bool(healthy),
                    "severity": str(severity),
                    "symptoms": str(symptoms),
                    "root_cause": str(root_cause),
                    "recommended_action": str(recommended_action),
                    "action_taken": str(action_taken),
                    "action_success": bool(action_success),
                    "response_time_ms": float(response_time_ms),
                    "timestamp": float(timestamp),
                }
            )
        return out

    def counters(self) -> dict:
        """计数真源（RV-①b-⑦-2/⑧「计数真源化」同款）：全表行聚合单一口，键形=
        OrganHealer.stats() 同键形（单一形状禁两套 MEM-⑥ 纪律）。

        聚合恒算全表（诚实边界⑥）：内存 ring 裁剪不裁行=计数不随裁剪回退。
        recent_healthy_rate 窗=最近 50 行（stats 的 _recent_healthy_rate 同窗语义）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return _zero_counters()
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT healthy, severity, action_taken, action_success, COUNT(*)"
                    f" FROM {_TABLE} GROUP BY healthy, severity, action_taken, action_success"
                ).fetchall()
                organ_rows = conn.execute(
                    f"SELECT organ, COUNT(*) FROM {_TABLE} WHERE healthy = 0 GROUP BY organ"
                ).fetchall()
                recent = conn.execute(
                    f"SELECT healthy FROM {_TABLE} ORDER BY rowid DESC LIMIT 50"
                ).fetchall()
            finally:
                conn.close()
        total = 0
        healed = 0
        failed = 0
        actions_taken = 0
        actions_succeeded = 0
        for healthy, severity, action_taken, action_success, cnt in rows:
            n = int(cnt)
            total += n
            if str(severity) == _RECOVERED:
                healed += n
            if not bool(healthy) and str(severity) != _RECOVERED:
                failed += n
            if str(action_taken) != _ACTION_NONE:
                actions_taken += n
            if bool(action_success):
                actions_succeeded += n
        organ_failures = {str(o): int(c) for o, c in organ_rows}
        if recent:
            healthy_n = sum(1 for (h,) in recent if bool(h))
            recent_healthy_rate: float = round(healthy_n / len(recent) * 100, 1)
        else:
            recent_healthy_rate = 100.0
        return {
            "total_diagnoses": total,
            "healed": healed,
            "failed": failed,
            "actions_taken": actions_taken,
            "actions_succeeded": actions_succeeded,
            "success_rate": round(actions_succeeded / actions_taken * 100, 1)
            if actions_taken
            else 0,
            "organ_failure_frequency": dict(
                sorted(organ_failures.items(), key=lambda x: -x[1])[:10]
            ),
            "recent_healthy_rate": recent_healthy_rate,
        }

    def row_count(self) -> int:
        """修复史行数（探针/取证回读口）。库缺失=0。"""
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
