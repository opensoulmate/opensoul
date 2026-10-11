"""SQLite persistence for OpenNest — tenant registry + isolation audit log.

F3b-①（2026-10-12）：租户真源持久化。修前 TenantManager._tenants dict /
IsolationEngine._access_log list 全内存，重启即丢（F3 审计 live 实证：建
tenant-4cb267d0 → systemctl 重启 → 0 tenants，配额/审计同根归零）。本模块是
两者唯一落盘口：租户定义/配额/用量/审计日志重启不丢。

铁律（本仓 SQLite 约定）：dict/list 一律 json.dumps() 落 TEXT 列（禁直接绑参）、
tenant_id 显式 str()、纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）。

测试隔离：NEST_DB 环境变量覆盖库路径（AGENT_REGISTRY_DB 同款先例），缺省
data/nest.db。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

# 落 TEXT 列的 JSON 字段（写 dumps / 读 loads 在本模块单点收口）
_JSON_FIELDS = ("tags", "config", "metadata", "quota", "usage")

_ACCESS_LOG_COLUMNS = (
    "timestamp",
    "tenant_id",
    "resource_type",
    "resource_id",
    "action",
    "allowed",
    "reason",
    "source_ip",
)


def default_db_path() -> str:
    """库路径：NEST_DB 环境变量优先（测试隔离），缺省 <repo>/data/nest.db。"""
    env = os.environ.get("NEST_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "nest.db")


class NestStore:
    """OpenNest 持久化真源（租户表 + 审计日志表）。"""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or default_db_path()
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tenants (
                        tenant_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        tier TEXT NOT NULL,
                        status TEXT NOT NULL,
                        namespace TEXT NOT NULL,
                        owner_user_id TEXT NOT NULL DEFAULT '',
                        description TEXT NOT NULL DEFAULT '',
                        tags TEXT NOT NULL DEFAULT '[]',
                        config TEXT NOT NULL DEFAULT '{}',
                        metadata TEXT NOT NULL DEFAULT '{}',
                        quota TEXT NOT NULL DEFAULT '{}',
                        usage TEXT NOT NULL DEFAULT '{}',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS access_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        tenant_id TEXT NOT NULL,
                        resource_type TEXT NOT NULL,
                        resource_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        allowed INTEGER NOT NULL,
                        reason TEXT NOT NULL DEFAULT '',
                        source_ip TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_access_log_ts ON access_log(timestamp)"
                )
                conn.commit()
            finally:
                conn.close()

    # ── Tenants ─────────────────────────────────────────────

    def upsert_tenant(self, tenant: dict) -> None:
        """写入/更新一行租户（tenant dict 形状见 TenantManager._to_row）。"""
        row = dict(tenant)
        row["tenant_id"] = str(row["tenant_id"])
        for field in _JSON_FIELDS:
            value = row.get(field)
            if value is None:
                value = [] if field == "tags" else {}
            row[field] = json.dumps(value, ensure_ascii=False)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO tenants (
                        tenant_id, name, tier, status, namespace, owner_user_id,
                        description, tags, config, metadata, quota, usage,
                        created_at, updated_at
                    ) VALUES (
                        :tenant_id, :name, :tier, :status, :namespace, :owner_user_id,
                        :description, :tags, :config, :metadata, :quota, :usage,
                        :created_at, :updated_at
                    )
                    ON CONFLICT(tenant_id) DO UPDATE SET
                        name = excluded.name,
                        tier = excluded.tier,
                        status = excluded.status,
                        namespace = excluded.namespace,
                        owner_user_id = excluded.owner_user_id,
                        description = excluded.description,
                        tags = excluded.tags,
                        config = excluded.config,
                        metadata = excluded.metadata,
                        quota = excluded.quota,
                        usage = excluded.usage,
                        updated_at = excluded.updated_at
                    """,
                    row,
                )
                conn.commit()
            finally:
                conn.close()

    def delete_tenant(self, tenant_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM tenants WHERE tenant_id = ?", (str(tenant_id),))
                conn.commit()
            finally:
                conn.close()

    def load_tenants(self) -> list[dict]:
        """读回全部租户行（JSON 字段已 loads 还原 python 值）。"""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM tenants").fetchall()
            finally:
                conn.close()
        out = []
        for row in rows:
            item = dict(row)
            for field in _JSON_FIELDS:
                item[field] = json.loads(item[field])
            out.append(item)
        return out

    # ── Access log (audit) ──────────────────────────────────

    def insert_access_log(self, entry: dict, max_log: int = 10000) -> None:
        """追加一条审计记录，并按 max_log 保留最近 N 条（原内存 _max_log 语义延续）。"""
        row = {col: entry[col] for col in _ACCESS_LOG_COLUMNS}
        row["allowed"] = 1 if row["allowed"] else 0
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO access_log (
                        timestamp, tenant_id, resource_type, resource_id,
                        action, allowed, reason, source_ip
                    ) VALUES (
                        :timestamp, :tenant_id, :resource_type, :resource_id,
                        :action, :allowed, :reason, :source_ip
                    )
                    """,
                    row,
                )
                if max_log and max_log > 0:
                    conn.execute(
                        "DELETE FROM access_log WHERE id NOT IN "
                        "(SELECT id FROM access_log ORDER BY id DESC LIMIT ?)",
                        (int(max_log),),
                    )
                conn.commit()
            finally:
                conn.close()

    def list_access_log(
        self,
        tenant_id: str | None = None,
        action: str | None = None,
        allowed: bool | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """倒序查审计日志（过滤语义与原内存版 get_access_log 一致）。"""
        sql = f"SELECT {', '.join(_ACCESS_LOG_COLUMNS)} FROM access_log"
        clauses: list[str] = []
        params: list = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(str(tenant_id))
        if action:
            clauses.append("action = ?")
            params.append(str(action))
        if allowed is not None:
            clauses.append("allowed = ?")
            params.append(1 if allowed else 0)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        out = []
        for row in rows:
            item = dict(row)
            item["allowed"] = bool(item["allowed"])
            out.append(item)
        return out

    def access_stats(self) -> dict:
        """审计统计（形状与原内存版 stats 一致：total/blocked/by_resource_type）。"""
        with self._lock:
            conn = self._connect()
            try:
                total = conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0]
                blocked = conn.execute(
                    "SELECT COUNT(*) FROM access_log WHERE allowed = 0"
                ).fetchone()[0]
                by_type_rows = conn.execute(
                    "SELECT resource_type, COUNT(*) AS n FROM access_log GROUP BY resource_type"
                ).fetchall()
            finally:
                conn.close()
        return {
            "total_access_checks": total,
            "blocked_attempts": blocked,
            "by_resource_type": {r["resource_type"]: r["n"] for r in by_type_rows},
        }
