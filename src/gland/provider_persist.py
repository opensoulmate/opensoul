"""SQLite persistence for gland ModelRouter.providers（RV-①b-⑩）——provider 注册配置唯一落盘口。

修前（RV-①c §5.1 藏活事实定性）：ModelRouter.providers 全在进程内存（dict），
API 注册的 name/base_url/models/priority 零重建——POST /api/gland/providers 注册的
provider 配置随 service 重启全清（内置三件套 openai/variant/ollama 随 settings
bootstrap 重建，API 注册面无重建源）。本模块把注册行落 SQLite：注册/修改/删除随写
随落盘（状态快照），首触懒回载，重启注册配置不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
name/base_url 显式 str()、models 是 dict 列整体 json.dumps 落 TEXT（dict 列禁裸存）。

钥面刻意不落（RV-①c 候选定性「无钥；钥另件 RV-①b-③」）：本表只有配置行，api_key
等凭据一律不进任何列——凭据落盘形态随 ㉛ 拍板（KeyManager 涉凭据需拍板），本件
不自决。回载出来的 provider 若此前带钥，重启后钥不随行（如实缺钥，鉴权失败走链
failover 可见，禁谎报有钥）。

测试隔离：GLAND_PROVIDERS_DB 环境变量覆盖库路径（GLAND_USAGE_DB/PULSE_SIGNALS_DB
同款先例），缺省 <repo>/data/gland_providers.db；库路径每次连接按 env 解析
（api/gland 模块级 gateway 单例跨用例存活，测试面每用例 tmp 真源）。

表语义（pulse_signals/limb_tasks 同族「状态快照表」）：name=PK upsert 当前态
一 provider 一行——与 echo_history/immune_audit_log 追加型历史表刻意不同（一个
provider 多次配置变更=同一行当前态更新，不是多行历史）。

诚实边界：
①写失败不阻断 provider 动作本体但 logger.error 可见（RV-①b-⑨ 记账旁路纪律同款）
  ——注册已进内存，为落盘失败炸掉运行时注册不成比例；代价是故障窗口内该行只活
  内存、重启丢（如实记，非谎报 durable）。
②回载失败=空注册表起步+logger.error 可见，不置位 loaded 下次触碰重试
  （RV-①b-⑨ 语义）；不在回载故障上发明降级判法。
③models JSON 不可解（未来版本行/手工坏行）=坏行跳过+logger.error 可见，不因单行
  坏数据丢整段注册表（禁静默丢：跳过必留日志真因；echo/pulse 同纪律）。
④删行幂等：delete 不存在的 name=同一效果无异常（快照表语义）。
⑤运行时状态不落（_consecutive_failures/_last_failure/_cooldown_until 是进程内
  熔断计数，重启清零=既有语义，非用户配置）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_TABLE = "gland_providers"

_COLUMNS = (
    "name",
    "base_url",
    "models",
    "enabled",
    "priority",
)


def default_db_path() -> str:
    """库路径：GLAND_PROVIDERS_DB 环境变量优先（测试隔离），缺省 <repo>/data/gland_providers.db。"""
    env = os.environ.get("GLAND_PROVIDERS_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "gland_providers.db")


class ProviderPersistence:
    """provider 注册配置持久化真源（单表 gland_providers：upsert 当前态 + load_all
    懒回载 + delete 幂等删行）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 gateway 单例跨用例存活，测试面每用例 tmp
        # 真源。构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
        # （RV-①b-②④⑨ 同纪律），写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "name TEXT PRIMARY KEY, "
            "base_url TEXT NOT NULL, "
            "models TEXT NOT NULL DEFAULT '{}', "
            "enabled INTEGER NOT NULL DEFAULT 1, "
            "priority INTEGER NOT NULL DEFAULT 0)"
        )
        return conn

    def upsert(self, row: dict) -> None:
        """写穿一行 provider 当前态（name=PK 状态快照：一 provider 一行，重复写=
        当前态更新，与追加型历史表刻意分语义）。失败如实抛（禁静默丢写），是否阻断
        调用方归消费方裁决（ModelRouter 侧=记账旁路纪律）。

        models dict 整体 json.dumps 落 TEXT 列（dict 列禁裸存铁律）；enabled 落
        INTEGER 0/1（bool 显式转 int）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} (" + ", ".join(_COLUMNS) + ") VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(name) DO UPDATE SET "
                    "base_url=excluded.base_url, models=excluded.models, "
                    "enabled=excluded.enabled, priority=excluded.priority",
                    (
                        str(row["name"]),
                        str(row["base_url"]),
                        json.dumps(row.get("models") or {}, ensure_ascii=False),
                        1 if row.get("enabled") else 0,
                        int(row.get("priority") or 0),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete(self, name: str) -> None:
        """删一行（删注册=删行，快照表语义：delete 幂等，缺行=同一效果无异常）。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(f"DELETE FROM {_TABLE} WHERE name = ?", (str(name),))
                conn.commit()
            finally:
                conn.close()

    def load_all(self) -> list[dict]:
        """回载全部注册行（rowid 升序=注册/写入序；条目 dict 键集=ProviderConfig
        配置字段集 {name,base_url,models,enabled,priority}，models 还原为 dict=与
        内存 ProviderConfig 往返形）。

        回载只读不建库：库文件缺失=空注册表直接返回（防模块 import 期构造回载
        顺手建出空生产库——写穿才建库，RV-①b-②④⑨ 同纪律）。
        models JSON 不可解=坏行跳过+logger.error 可见（诚实边界③）。
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
            name, base_url, models_raw, enabled, priority = row
            try:
                models = json.loads(models_raw) if models_raw else {}
                if not isinstance(models, dict):
                    raise ValueError(f"models 非对象: {type(models).__name__}")
            except Exception as e:
                logger.error(f"provider 注册坏行跳过（name={name}，models 不可解）: {e}")
                continue
            out.append(
                {
                    "name": str(name),
                    "base_url": str(base_url),
                    "models": {str(k): str(v) for k, v in models.items()},
                    "enabled": bool(enabled),
                    "priority": int(priority),
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
