"""SQLite persistence for MigrationEngine 两面（RV-①b-⑭-2）——注册 schema / 迁移脚本
applied 状态唯一落盘口。

修前（RV-①c §5.1 藏活事实定性 + api/heredity.py:68 留档注释）：MigrationEngine
_schemas/_scripts 全在进程内存，register_schema/create_migration_script/apply_script
只改内存——API 注册的 schema 版本与迁移脚本（含 applied 状态）随 service 重启全清
（/api/heredity/schemas|scripts 重启即 404/空列表，applied 标记白标）。
本模块把两面落 SQLite：变更点随写随落盘（状态快照），首触懒回载，重启不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑——本表
存的 up_sql/down_sql 是脚本内容元数据，只存不执行）、各 TEXT 列显式 str()、
fields 列整体 json.dumps 落 TEXT（list 列禁裸存）。

测试隔离：HEREDITY_MIGRATION_DB 环境变量覆盖库路径（HEREDITY_REGISTRY_DB 同款
先例），缺省 <repo>/data/heredity_migration.db；库路径每次连接按 env 解析
（api/heredity 模块级 engine 单例跨用例存活，测试面每用例 tmp 真源）。

表语义（heredity_registry 同族「状态快照表」）：
- schema_versions：schema_id=PK 一 schema 版本一行——register_schema 追加新行
  （SchemaVersion 注册后无状态迁移，行不可变语义），upsert 只为幂等写穿。
- migration_scripts：script_id=PK 一脚本一行——apply_script 的 applied/applied_at
  状态随影更新（创建后状态迁移=同行 upsert，不是多行历史）。

诚实边界：
①写失败不阻断动作本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——动作
  已进内存，为落盘失败炸掉注册不成比例；代价是故障窗口内该行只活内存、重启丢
  （如实记，非谎报 durable）。
②回载失败=空引擎起步+logger.error 可见，行真源仍在库、不置位下次触碰重试
  （RV-①b-⑤-1 语义）；不在回载故障上发明降级判法。
③fields JSON 不可解/坏行（未来版本行）=跳过+logger.error 可见，不因单行坏数据
  丢整段注册面（禁静默丢：跳过必留日志真因；echo ④/pulse ③ 同纪律）。
④fields 是 list 列（SchemaField 序列化形）：JSON 原生值逐值保真往返；非 JSON
  可序列化对象（default 字段任意值）落盘走 str() 形（json.dumps default=str）
  ——如实记，不 fabricate 类型。
⑤counters() 恒算全表行聚合（计数真源，MEM-⑥ 口径）——引擎内存容器无界（无
  ring 裁剪），正常路径内存==行；写失败窗口行滞后内存（边界①），计数以行真源
  为准=重启后续算口径。
⑥up_sql/down_sql 只存不执行（migrations 是 PG 语法禁直接跑铁律）；脚本内容
  随行保真往返但公共读面 list_scripts 不出 SQL 列（既有契约零变化）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_TABLE_SCHEMA = "schema_versions"
_TABLE_SCRIPT = "migration_scripts"

_SCHEMA_COLUMNS = (
    "schema_id",
    "component_id",
    "version",
    "fields",
    "created_at",
)

_SCRIPT_COLUMNS = (
    "script_id",
    "migration_type",
    "component_id",
    "from_version",
    "to_version",
    "up_sql",
    "down_sql",
    "transform",
    "created_at",
    "applied",
    "applied_at",
)


def default_db_path() -> str:
    """库路径：HEREDITY_MIGRATION_DB 环境变量优先（测试隔离），缺省 <repo>/data/heredity_migration.db。"""
    env = os.environ.get("HEREDITY_MIGRATION_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "heredity_migration.db")


class MigrationPersistence:
    """MigrationEngine 两面持久化真源（注册 schema / 迁移脚本 applied 状态）。

    构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
    （RV-①b-②④⑨ 同纪律），写穿才建库。
    """

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 engine 单例跨用例存活，测试面每用例 tmp 真源。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE_SCHEMA} ("
            "schema_id TEXT PRIMARY KEY, "
            "component_id TEXT NOT NULL, "
            "version TEXT NOT NULL, "
            "fields TEXT NOT NULL DEFAULT '[]', "
            "created_at REAL NOT NULL)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_heredity_schema_component ON {_TABLE_SCHEMA} (component_id)"
        )
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE_SCRIPT} ("
            "script_id TEXT PRIMARY KEY, "
            "migration_type TEXT NOT NULL, "
            "component_id TEXT NOT NULL, "
            "from_version TEXT NOT NULL, "
            "to_version TEXT NOT NULL, "
            "up_sql TEXT NOT NULL DEFAULT '', "
            "down_sql TEXT NOT NULL DEFAULT '', "
            "transform TEXT NOT NULL DEFAULT '', "
            "created_at REAL NOT NULL, "
            "applied INTEGER NOT NULL DEFAULT 0, "
            "applied_at REAL)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_heredity_script_component ON {_TABLE_SCRIPT} (component_id)"
        )
        return conn

    # ── schema_versions ───────────────────────────────────────────

    def upsert_schema(self, row: dict) -> None:
        """写穿一行 schema 版本（schema_id=PK 状态快照；注册后行不可变，upsert 为幂等写穿）。

        失败如实抛（禁静默丢写），是否阻断归消费方裁决（MigrationEngine 侧=记账旁路）。
        fields list 列整体 json.dumps 落 TEXT（边界④）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE_SCHEMA} ("
                    + ", ".join(_SCHEMA_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(schema_id) DO UPDATE SET "
                    "component_id=excluded.component_id, version=excluded.version, "
                    "fields=excluded.fields, created_at=excluded.created_at",
                    (
                        str(row["schema_id"]),
                        str(row["component_id"]),
                        str(row["version"]),
                        json.dumps(row.get("fields") or [], ensure_ascii=False, default=str),
                        float(row["created_at"]),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_schemas(self) -> list[dict]:
        """回载全部 schema 行（rowid 升序=注册序）。fields JSON 不可解=跳过+可见（边界③）。"""
        rows = self._read_rows(_TABLE_SCHEMA, _SCHEMA_COLUMNS)
        out: list[dict] = []
        for row in rows:
            schema_id, component_id, version, fields_raw, created_at = row
            try:
                fields = json.loads(fields_raw) if fields_raw else []
                if not isinstance(fields, list):
                    raise ValueError(f"fields 非数组: {type(fields).__name__}")
            except Exception as e:
                logger.error(
                    f"heredity schema 坏行跳过（schema_id={schema_id}，fields 不可解）: {e}"
                )
                continue
            out.append(
                {
                    "schema_id": str(schema_id),
                    "component_id": str(component_id),
                    "version": str(version),
                    "fields": fields,
                    "created_at": float(created_at),
                }
            )
        return out

    # ── migration_scripts ─────────────────────────────────────────

    def upsert_script(self, row: dict) -> None:
        """写穿一行迁移脚本当前态（script_id=PK：apply 的 applied/applied_at 随影更新）。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE_SCRIPT} ("
                    + ", ".join(_SCRIPT_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(script_id) DO UPDATE SET "
                    "migration_type=excluded.migration_type, component_id=excluded.component_id, "
                    "from_version=excluded.from_version, to_version=excluded.to_version, "
                    "up_sql=excluded.up_sql, down_sql=excluded.down_sql, "
                    "transform=excluded.transform, created_at=excluded.created_at, "
                    "applied=excluded.applied, applied_at=excluded.applied_at",
                    (
                        str(row["script_id"]),
                        str(row["migration_type"]),
                        str(row["component_id"]),
                        str(row["from_version"]),
                        str(row["to_version"]),
                        str(row.get("up_sql") or ""),
                        str(row.get("down_sql") or ""),
                        str(row.get("transform") or ""),
                        float(row["created_at"]),
                        1 if row.get("applied") else 0,
                        None if row.get("applied_at") is None else float(row["applied_at"]),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_scripts(self) -> list[dict]:
        """回载全部迁移脚本行（rowid 升序=创建序）。applied 整列往返 bool 保真。"""
        rows = self._read_rows(_TABLE_SCRIPT, _SCRIPT_COLUMNS)
        out: list[dict] = []
        for row in rows:
            (
                script_id,
                migration_type,
                component_id,
                from_version,
                to_version,
                up_sql,
                down_sql,
                transform,
                created_at,
                applied,
                applied_at,
            ) = row
            out.append(
                {
                    "script_id": str(script_id),
                    "migration_type": str(migration_type),
                    "component_id": str(component_id),
                    "from_version": str(from_version),
                    "to_version": str(to_version),
                    "up_sql": str(up_sql or ""),
                    "down_sql": str(down_sql or ""),
                    "transform": str(transform or ""),
                    "created_at": float(created_at),
                    "applied": bool(applied),
                    "applied_at": None if applied_at is None else float(applied_at),
                }
            )
        return out

    # ── counters（行聚合计数真源，MEM-⑥）──────────────────────────

    def counters(self) -> dict:
        """计数真源（MEM-⑥）：全表行聚合。返回形状=MigrationEngine.get_stats() 同键形
        单一形状禁两套。库缺失=全零（真实空账，非假 0——行真源确实为空）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return {
                    "total_components": 0,
                    "total_schema_versions": 0,
                    "total_scripts": 0,
                    "applied_scripts": 0,
                    "pending_scripts": 0,
                }
            conn = self._connect()
            try:
                total_components = conn.execute(
                    f"SELECT COUNT(DISTINCT component_id) FROM {_TABLE_SCHEMA}"
                ).fetchone()[0]
                total_schemas = conn.execute(f"SELECT COUNT(*) FROM {_TABLE_SCHEMA}").fetchone()[0]
                total_scripts = conn.execute(f"SELECT COUNT(*) FROM {_TABLE_SCRIPT}").fetchone()[0]
                applied_scripts = conn.execute(
                    f"SELECT COUNT(*) FROM {_TABLE_SCRIPT} WHERE applied = 1"
                ).fetchone()[0]
            finally:
                conn.close()
        return {
            "total_components": int(total_components),
            "total_schema_versions": int(total_schemas),
            "total_scripts": int(total_scripts),
            "applied_scripts": int(applied_scripts),
            "pending_scripts": int(total_scripts) - int(applied_scripts),
        }

    # ── 通用只读回载（只读不建库共用口）────────────────────────────

    def _read_rows(self, table: str, columns: tuple[str, ...]) -> list[tuple]:
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                return conn.execute(
                    f"SELECT {', '.join(columns)} FROM {table} ORDER BY rowid ASC"
                ).fetchall()
            finally:
                conn.close()
