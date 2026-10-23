"""SQLite persistence for VersionRegistry 3 面（RV-①b-⑭-1）——版本注册表唯一落盘口。

修前（RV-①c §5.1 藏活事实定性）：VersionRegistry._components/_migrations/_changelog
全在进程内存，register/create_migration/execute·rollback/_add_changelog/bump 只改
内存——API 注册组件版本/迁移计划（含 execute/rollback 状态）/changelog 随 service
重启全清（SEED 28 组件仅 import 期重建，API 注册与执行状态零重建）。
本模块把三面落 SQLite：变更点随写随落盘（状态快照），首触懒回载，重启注册表不丢。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、dict/list 列整体 json.dumps 落 TEXT（dict 列禁裸存）。

测试隔离：HEREDITY_REGISTRY_DB 环境变量覆盖库路径（GLAND_USAGE_DB/WILL_PLANS_DB
同款先例），缺省 <repo>/data/heredity_registry.db；库路径每次连接按 env 解析
（api/heredity 模块级 registry 单例跨用例存活，测试面每用例 tmp 真源）。

表语义（will_plans/healer_history 同族「状态快照表」）：
- component_versions：cv_id=PK 一版本一行——register 追加新版本行、状态迁移
  （active→deprecated）更新旧行当前态，不是多行历史。
- migration_plans：migration_id=PK 一计划一行——execute/rollback 的 status/
  started_at/completed_at/error/steps 状态整行随影更新。
- changelog_entries：追加型历史表（echo_history 同族）：entry_id=PK 且 INSERT
  禁 REPLACE——撞主键如实抛 IntegrityError 禁覆盖（一条变更一条史）。
- registry_meta：kv 小表（platform_version 等注册表标量态）。

诚实边界：
①写失败不阻断注册表动作本体但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——
  动作已进内存，为落盘失败炸掉注册不成比例；代价是故障窗口内该行只活内存、
  重启丢（如实记，非谎报 durable）。
②回载失败=空注册表起步+logger.error 可见，行真源仍在库、不置位下次触碰重试
  （RV-①b-⑤-1 语义）；不在回载故障上发明降级判法。
③payload JSON 不可解/坏行（未来版本行）=跳过+logger.error 可见，不因单行坏
  数据丢整段注册表（禁静默丢：跳过必留日志真因；echo ④/pulse ③ 同纪律）。
④dependencies/config_schema 是 dict 列、breaking_changes/steps/rollback_plan 是
  list 列：JSON 原生值逐值保真往返；非 JSON 可序列化对象落盘走 str() 形
  （json.dumps default=str）——如实记，不 fabricate 类型。
⑤counters() 恒算全表行聚合（组件/迁移/changelog 计数真源，MEM-⑥ 口径）——
  注册表内存容器无界（无 ring 裁剪），正常路径内存==行；写失败窗口行滞后内存
  （边界①），计数以行真源为准=重启后续算口径。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_TABLE_CV = "component_versions"
_TABLE_MIG = "migration_plans"
_TABLE_CL = "changelog_entries"
_TABLE_META = "registry_meta"

_CV_COLUMNS = (
    "cv_id",
    "component_id",
    "component_name",
    "version",
    "status",
    "release_notes",
    "dependencies",
    "config_schema",
    "breaking_changes",
    "created_at",
    "retired_at",
)

_MIG_COLUMNS = (
    "migration_id",
    "component_id",
    "from_version",
    "to_version",
    "steps",
    "status",
    "dry_run",
    "started_at",
    "completed_at",
    "error",
    "rollback_plan",
)

_CL_COLUMNS = (
    "entry_id",
    "component_id",
    "version",
    "change_type",
    "description",
    "author",
    "timestamp",
)


def default_db_path() -> str:
    """库路径：HEREDITY_REGISTRY_DB 环境变量优先（测试隔离），缺省 <repo>/data/heredity_registry.db。"""
    env = os.environ.get("HEREDITY_REGISTRY_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "heredity_registry.db")


class RegistryPersistence:
    """VersionRegistry 3 面持久化真源（组件版本/迁移计划/changelog + meta kv）。

    构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
    （RV-①b-②④⑨ 同纪律），写穿才建库。
    """

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 registry 单例跨用例存活，测试面每用例 tmp 真源。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE_CV} ("
            "cv_id TEXT PRIMARY KEY, "
            "component_id TEXT NOT NULL, "
            "component_name TEXT NOT NULL, "
            "version TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "release_notes TEXT NOT NULL DEFAULT '', "
            "dependencies TEXT NOT NULL DEFAULT '{}', "
            "config_schema TEXT NOT NULL DEFAULT '{}', "
            "breaking_changes TEXT NOT NULL DEFAULT '[]', "
            "created_at REAL NOT NULL, "
            "retired_at REAL)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_heredity_cv_component ON {_TABLE_CV} (component_id)"
        )
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE_MIG} ("
            "migration_id TEXT PRIMARY KEY, "
            "component_id TEXT NOT NULL, "
            "from_version TEXT NOT NULL, "
            "to_version TEXT NOT NULL, "
            "steps TEXT NOT NULL DEFAULT '[]', "
            "status TEXT NOT NULL, "
            "dry_run INTEGER NOT NULL DEFAULT 0, "
            "started_at REAL, "
            "completed_at REAL, "
            "error TEXT, "
            "rollback_plan TEXT NOT NULL DEFAULT '[]')"
        )
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE_CL} ("
            "entry_id TEXT PRIMARY KEY, "
            "component_id TEXT NOT NULL, "
            "version TEXT NOT NULL, "
            "change_type TEXT NOT NULL, "
            "description TEXT NOT NULL, "
            "author TEXT NOT NULL DEFAULT 'system', "
            "timestamp REAL NOT NULL)"
        )
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE_META} (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        return conn

    # ── component_versions ──────────────────────────────────────────

    def upsert_component(self, row: dict) -> None:
        """写穿一行组件版本当前态（cv_id=PK 状态快照：一版本一行，状态迁移=同行更新）。

        失败如实抛（禁静默丢写），是否阻断归消费方裁决（VersionRegistry 侧=记账旁路）。
        dict/list 列整体 json.dumps 落 TEXT（边界④）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE_CV} ("
                    + ", ".join(_CV_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(cv_id) DO UPDATE SET "
                    "component_id=excluded.component_id, component_name=excluded.component_name, "
                    "version=excluded.version, status=excluded.status, "
                    "release_notes=excluded.release_notes, dependencies=excluded.dependencies, "
                    "config_schema=excluded.config_schema, breaking_changes=excluded.breaking_changes, "
                    "created_at=excluded.created_at, retired_at=excluded.retired_at",
                    (
                        str(row["cv_id"]),
                        str(row["component_id"]),
                        str(row["component_name"]),
                        str(row["version"]),
                        str(row["status"]),
                        str(row.get("release_notes") or ""),
                        json.dumps(row.get("dependencies") or {}, ensure_ascii=False, default=str),
                        json.dumps(row.get("config_schema") or {}, ensure_ascii=False, default=str),
                        json.dumps(
                            row.get("breaking_changes") or [], ensure_ascii=False, default=str
                        ),
                        float(row["created_at"]),
                        None if row.get("retired_at") is None else float(row["retired_at"]),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_component_versions(self) -> list[dict]:
        """回载全部组件版本行（rowid 升序=注册序）。

        回载只读不建库（边界②语义）；坏 JSON 列=跳过+logger.error 可见（边界③）。
        """
        rows = self._read_rows(_TABLE_CV, _CV_COLUMNS)
        out: list[dict] = []
        for row in rows:
            (
                cv_id,
                component_id,
                component_name,
                version,
                status,
                release_notes,
                dependencies_raw,
                config_schema_raw,
                breaking_changes_raw,
                created_at,
                retired_at,
            ) = row
            try:
                dependencies = json.loads(dependencies_raw) if dependencies_raw else {}
                config_schema = json.loads(config_schema_raw) if config_schema_raw else {}
                breaking_changes = json.loads(breaking_changes_raw) if breaking_changes_raw else []
                if not isinstance(dependencies, dict):
                    raise ValueError(f"dependencies 非对象: {type(dependencies).__name__}")
                if not isinstance(config_schema, dict):
                    raise ValueError(f"config_schema 非对象: {type(config_schema).__name__}")
                if not isinstance(breaking_changes, list):
                    raise ValueError(f"breaking_changes 非数组: {type(breaking_changes).__name__}")
            except Exception as e:
                logger.error(f"heredity 组件版本坏行跳过（cv_id={cv_id}，JSON 列不可解）: {e}")
                continue
            out.append(
                {
                    "cv_id": str(cv_id),
                    "component_id": str(component_id),
                    "component_name": str(component_name),
                    "version": str(version),
                    "status": str(status),
                    "release_notes": str(release_notes or ""),
                    "dependencies": dependencies,
                    "config_schema": config_schema,
                    "breaking_changes": breaking_changes,
                    "created_at": float(created_at),
                    "retired_at": None if retired_at is None else float(retired_at),
                }
            )
        return out

    # ── migration_plans ────────────────────────────────────────────

    def upsert_migration(self, row: dict) -> None:
        """写穿一行迁移计划当前态（migration_id=PK：execute/rollback 状态随影更新）。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE_MIG} ("
                    + ", ".join(_MIG_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(migration_id) DO UPDATE SET "
                    "component_id=excluded.component_id, from_version=excluded.from_version, "
                    "to_version=excluded.to_version, steps=excluded.steps, status=excluded.status, "
                    "dry_run=excluded.dry_run, started_at=excluded.started_at, "
                    "completed_at=excluded.completed_at, error=excluded.error, "
                    "rollback_plan=excluded.rollback_plan",
                    (
                        str(row["migration_id"]),
                        str(row["component_id"]),
                        str(row["from_version"]),
                        str(row["to_version"]),
                        json.dumps(row.get("steps") or [], ensure_ascii=False, default=str),
                        str(row["status"]),
                        1 if row.get("dry_run") else 0,
                        None if row.get("started_at") is None else float(row["started_at"]),
                        None if row.get("completed_at") is None else float(row["completed_at"]),
                        None if row.get("error") is None else str(row["error"]),
                        json.dumps(row.get("rollback_plan") or [], ensure_ascii=False, default=str),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_migrations(self) -> list[dict]:
        """回载全部迁移计划行（rowid 升序=创建序）。坏 JSON 列=跳过可见（边界③）。"""
        rows = self._read_rows(_TABLE_MIG, _MIG_COLUMNS)
        out: list[dict] = []
        for row in rows:
            (
                migration_id,
                component_id,
                from_version,
                to_version,
                steps_raw,
                status,
                dry_run,
                started_at,
                completed_at,
                error,
                rollback_plan_raw,
            ) = row
            try:
                steps = json.loads(steps_raw) if steps_raw else []
                rollback_plan = json.loads(rollback_plan_raw) if rollback_plan_raw else []
                if not isinstance(steps, list):
                    raise ValueError(f"steps 非数组: {type(steps).__name__}")
                if not isinstance(rollback_plan, list):
                    raise ValueError(f"rollback_plan 非数组: {type(rollback_plan).__name__}")
            except Exception as e:
                logger.error(
                    f"heredity 迁移计划坏行跳过（migration_id={migration_id}，JSON 列不可解）: {e}"
                )
                continue
            out.append(
                {
                    "migration_id": str(migration_id),
                    "component_id": str(component_id),
                    "from_version": str(from_version),
                    "to_version": str(to_version),
                    "steps": steps,
                    "status": str(status),
                    "dry_run": bool(dry_run),
                    "started_at": None if started_at is None else float(started_at),
                    "completed_at": None if completed_at is None else float(completed_at),
                    "error": None if error is None else str(error),
                    "rollback_plan": rollback_plan,
                }
            )
        return out

    # ── changelog_entries（追加型：INSERT 禁 REPLACE）───────────────

    def append_changelog(self, row: dict) -> None:
        """追加一条 changelog（entry_id=PK；撞主键如实抛 IntegrityError 禁覆盖——
        一条变更一条史，append 表与状态快照表刻意分语义（echo_history 同族））。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE_CL} ("
                    + ", ".join(_CL_COLUMNS)
                    + ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(row["entry_id"]),
                        str(row["component_id"]),
                        str(row["version"]),
                        str(row["change_type"]),
                        str(row["description"]),
                        str(row.get("author") or "system"),
                        float(row["timestamp"]),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def load_changelog(self) -> list[dict]:
        """回载全部 changelog 行（rowid 升序=追加序）。"""
        rows = self._read_rows(_TABLE_CL, _CL_COLUMNS)
        out: list[dict] = []
        for row in rows:
            (
                entry_id,
                component_id,
                version,
                change_type,
                description,
                author,
                timestamp,
            ) = row
            out.append(
                {
                    "entry_id": str(entry_id),
                    "component_id": str(component_id),
                    "version": str(version),
                    "change_type": str(change_type),
                    "description": str(description),
                    "author": str(author or "system"),
                    "timestamp": float(timestamp),
                }
            )
        return out

    # ── registry_meta（kv 标量态）──────────────────────────────────

    def get_meta(self, key: str) -> str | None:
        """读 meta 标量（库缺失/键缺失=None，只读不建库）。"""
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return None
            conn = self._connect()
            try:
                row = conn.execute(
                    f"SELECT value FROM {_TABLE_META} WHERE key = ?", (str(key),)
                ).fetchone()
            finally:
                conn.close()
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        """写 meta 标量（key=PK upsert 当前态）。失败如实抛（同 upsert 纪律）。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE_META} (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(key), str(value)),
                )
                conn.commit()
            finally:
                conn.close()

    # ── counters（行聚合计数真源，MEM-⑥）──────────────────────────

    def counters(self) -> dict:
        """计数真源（MEM-⑥）：全表行聚合。返回形状=get_stats() 同键形单一形状禁两套。

        库缺失=全零（真实空账，非假 0——行真源确实为空）；platform_version 走 meta
        （无 meta 回 None，消费方回落内存默认值禁 fabricate）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return {
                    "total_components": 0,
                    "total_versions": 0,
                    "version_statuses": {},
                    "total_migrations": 0,
                    "migration_statuses": {},
                    "total_changelog_entries": 0,
                    "platform_version": None,
                }
            conn = self._connect()
            try:
                total_components = conn.execute(
                    f"SELECT COUNT(DISTINCT component_id) FROM {_TABLE_CV}"
                ).fetchone()[0]
                total_versions = conn.execute(f"SELECT COUNT(*) FROM {_TABLE_CV}").fetchone()[0]
                version_statuses: dict[str, int] = {}
                for status, n in conn.execute(
                    f"SELECT status, COUNT(*) FROM {_TABLE_CV} GROUP BY status"
                ).fetchall():
                    version_statuses[str(status)] = int(n)
                total_migrations = conn.execute(f"SELECT COUNT(*) FROM {_TABLE_MIG}").fetchone()[0]
                migration_statuses: dict[str, int] = {}
                for status, n in conn.execute(
                    f"SELECT status, COUNT(*) FROM {_TABLE_MIG} GROUP BY status"
                ).fetchall():
                    migration_statuses[str(status)] = int(n)
                total_changelog = conn.execute(f"SELECT COUNT(*) FROM {_TABLE_CL}").fetchone()[0]
                meta_row = conn.execute(
                    f"SELECT value FROM {_TABLE_META} WHERE key = ?", ("platform_version",)
                ).fetchone()
            finally:
                conn.close()
        return {
            "total_components": int(total_components),
            "total_versions": int(total_versions),
            "version_statuses": version_statuses,
            "total_migrations": int(total_migrations),
            "migration_statuses": migration_statuses,
            "total_changelog_entries": int(total_changelog),
            "platform_version": None if meta_row is None else str(meta_row[0]),
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
