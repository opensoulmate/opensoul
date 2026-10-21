"""SQLite persistence for brain 记忆面（MEM-④）——记忆行 + 父会话行唯一落盘口。

修前（MEM-② 边界①留档）：brain 记忆面=进程内 MemoryStore 无持久化——重启即清
（live 实证：e2e 探针行随 systemctl 重启消失，清痕只能靠重启）。本模块把记忆行
落 SQLite：add/update/delete/衰减归档/容量驱逐随写随落盘，进程重启后回载，记忆
不丢。

父会话行同拍落盘（Gate3 逼出的挡路真断点，根因修）：记忆行归属=父会话行继承
（F3b-㉛），而 SessionManager 修前纯内存——记忆行活了、父会话行没了，/recall
带 session_id 走归属闸（session_owner 缺行→None→404 "Session not found"）重启
后恒 404=「记忆不丢」半真半假。brain_sessions 表与记忆行同库同生命周期。

铁律（本仓 SQLite 约定）：dict/list 一律 json.dumps() 落 TEXT 列（禁直接绑参）、
memory_id/session_id 显式 str()、纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）。

测试隔离：BRAIN_MEMORY_DB 环境变量覆盖库路径（NEST_DB/AGENT_REGISTRY_DB 同款
先例），缺省 <repo>/data/brain_memory.db；库路径每次连接按 env 解析（模块级
MemoryStore/SessionManager 单例跨用例存活，测试面每用例 tmp 真源）。

MEM-⑤ hippo 面复用（同一写穿/回载纪律，独立真源）：hippo /memories /sessions
（F3b-⑱⑳ 独立实例）走同一落盘口，但独立库（data/hippo_memory.db，
HIPPO_MEMORY_DB env 覆盖）+独立表（hippo_memories/hippo_sessions）——两面
各自的 MemoryStore/SessionManager 是独立内存真源，共库共表会互相回载对方
行=跨面串行（hippo 衰减遗忘能删掉 brain 面记忆）。故 resolve/table 参构造期
给定：resolve=库路径解析函数（缺省 default_db_path=brain 面）、table=表名
（缺省 brain_*，构造期正则校验禁 SQL 注入面）。

诚实边界：
①embedding 行向量是查询期懒算缓存（Memory.embedding 注释），刻意不落盘——
  回载行 embedding=None，下次语义查询重新懒算（与 update 内容变更失效同语义）。
②写失败如实抛 MemoryPersistError 禁静默吞——持久化契约是「记忆不丢」，静默
  丢写=谎报 durable。
③json.dumps(default=str)：metadata 里非 JSON 标量兜底转 str 落盘（元数据袋
  既有语义宽松，禁因序列化炸掉记忆写入；内容本体是 str 恒保真）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from pathlib import Path

# 落 TEXT 列的 JSON 字段（写 dumps / 读 loads 在本模块单点收口）
_JSON_FIELDS = ("tags", "metadata")

_COLUMNS = (
    "memory_id",
    "session_id",
    "content",
    "importance",
    "tags",
    "metadata",
    "created_at",
    "last_accessed_at",
    "access_count",
    "archived",
    "retention",
)

_SESSION_COLUMNS = (
    "session_id",
    "user_id",
    "title",
    "status",
    "created_at",
    "last_active_at",
    "memory_count",
    "total_tokens",
    "metadata",
)


class MemoryPersistError(RuntimeError):
    """持久化写失败（禁静默吞：记忆不丢是本模块的契约）。"""


def default_db_path() -> str:
    """库路径：BRAIN_MEMORY_DB 环境变量优先（测试隔离），缺省 <repo>/data/brain_memory.db。"""
    env = os.environ.get("BRAIN_MEMORY_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "brain_memory.db")


def hippo_db_path() -> str:
    """hippo 面库路径（MEM-⑤）：HIPPO_MEMORY_DB 环境变量优先（测试隔离），
    缺省 <repo>/data/hippo_memory.db——与 brain 面独立库（独立实例独立真源，
    共库共表互相回载=跨面串行，见模块 docstring）。"""
    env = os.environ.get("HIPPO_MEMORY_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "hippo_memory.db")


# 表名是 SQL 标识符（f-string 拼接，绑定参数塞不了标识符）——构造期白名单
# 校验，非法形如实 ValueError 禁拼进 SQL。
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MemoryPersistence:
    """记忆行持久化真源（单表，缺省 brain_memories；table 参=MEM-⑤ hippo 面独立表）。"""

    def __init__(
        self,
        db_path: str | None = None,
        resolve=None,
        table: str = "brain_memories",
    ):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 resolve 解析（缺省
        # default_db_path=brain 面）——模块级 MemoryStore 单例跨用例存活，测试面
        # 每用例 tmp 真源（proactive db_path retarget 同族语义）。
        self.explicit_path = db_path
        self._resolve = resolve or default_db_path
        if not _TABLE_NAME_RE.match(table):
            raise ValueError(f"invalid table name: {table!r}")
        self._table = table
        self._lock = threading.Lock()

    def _path(self) -> str:
        return self.explicit_path or self._resolve()

    def _connect(self) -> sqlite3.Connection:
        path = self._path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        # 表名拼接用 str concat 非 f-string：SQL 体含字面 '{}'（metadata 缺省），
        # f-string 会当空表达式炸（MEM-⑤ 坑记档）。
        conn.execute(
            "CREATE TABLE IF NOT EXISTS "
            + self._table
            + """ (
                memory_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                content TEXT NOT NULL,
                importance REAL NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                last_accessed_at REAL NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                retention REAL NOT NULL DEFAULT 1.0
            )
            """
        )

    def _row_values(self, mem) -> tuple:
        """Memory 行→绑定值（json.dumps 铁律 + memory_id 显式 str）。"""
        return (
            str(mem.memory_id),
            str(mem.session_id),
            str(mem.content),
            float(mem.importance),
            json.dumps(list(mem.tags or []), ensure_ascii=False, default=str),
            json.dumps(dict(mem.metadata or {}), ensure_ascii=False, default=str),
            float(mem.created_at),
            float(mem.last_accessed_at),
            int(mem.access_count),
            1 if mem.archived else 0,
            float(mem.retention),
        )

    def _upsert_many_locked(self, mems) -> None:
        with self._lock:
            conn = self._connect()
            try:
                self._ensure_schema(conn)
                conn.executemany(
                    "INSERT INTO "
                    + self._table
                    + """ (
                        memory_id, session_id, content, importance, tags, metadata,
                        created_at, last_accessed_at, access_count, archived, retention
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(memory_id) DO UPDATE SET
                        session_id=excluded.session_id,
                        content=excluded.content,
                        importance=excluded.importance,
                        tags=excluded.tags,
                        metadata=excluded.metadata,
                        created_at=excluded.created_at,
                        last_accessed_at=excluded.last_accessed_at,
                        access_count=excluded.access_count,
                        archived=excluded.archived,
                        retention=excluded.retention
                    """,
                    [self._row_values(m) for m in mems],
                )
                conn.commit()
            except sqlite3.Error as e:
                raise MemoryPersistError(f"brain memory upsert failed: {e}") from e
            finally:
                conn.close()

    def upsert(self, mem) -> None:
        """写穿单行（add/update/get 访问刷新/归档 共用）。"""
        self._upsert_many_locked([mem])

    def upsert_many(self, mems) -> None:
        """单事务批量写穿（run_decay_cycle 全量 retention 刷新用，禁逐行 N 次事务）。"""
        mems = list(mems)
        if mems:
            self._upsert_many_locked(mems)

    def delete(self, memory_id: str) -> bool:
        """删行（delete/遗忘/驱逐 共用）。返回真删行数>0。"""
        with self._lock:
            conn = self._connect()
            try:
                self._ensure_schema(conn)
                cur = conn.execute(
                    "DELETE FROM " + self._table + " WHERE memory_id = ?", (str(memory_id),)
                )
                conn.commit()
                return cur.rowcount > 0
            except sqlite3.Error as e:
                raise MemoryPersistError(f"brain memory delete failed: {e}") from e
            finally:
                conn.close()

    def load_rows(self) -> list[dict]:
        """全量回载（MemoryStore 懒回载唯一取数口）。

        返回 Memory 构造形 dict（tags/metadata loads 归一、archived int→bool）；
        embedding 刻意不出（缓存不落盘，见模块诚实边界①）。
        """
        with self._lock:
            conn = self._connect()
            try:
                self._ensure_schema(conn)
                rows = conn.execute(
                    "SELECT " + ", ".join(_COLUMNS) + " FROM " + self._table
                ).fetchall()
            except sqlite3.Error as e:
                raise MemoryPersistError(f"brain memory load failed: {e}") from e
            finally:
                conn.close()
        out = []
        for row in rows:
            item = {col: row[col] for col in _COLUMNS}
            for f in _JSON_FIELDS:
                try:
                    item[f] = json.loads(item[f])
                except (TypeError, ValueError):
                    item[f] = {} if f == "metadata" else []
            item["archived"] = bool(item["archived"])
            out.append(item)
        return out


class SessionPersistence:
    """父会话行持久化真源（单表，缺省 brain_sessions 与记忆行同库；table 参=
    MEM-⑤ hippo 面独立表）。

    记忆行归属=父会话行继承（F3b-㉛）——父会话行不落盘则重启后归属闸缺行 404，
    记忆「不丢但取不回」。SessionManager（src/hippo/session.py）duck-typed 消费
    upsert/upsert_many/delete/load_rows 四口（MemoryPersistence 同款纪律）。
    """

    def __init__(
        self,
        db_path: str | None = None,
        resolve=None,
        table: str = "brain_sessions",
    ):
        self.explicit_path = db_path
        self._resolve = resolve or default_db_path
        if not _TABLE_NAME_RE.match(table):
            raise ValueError(f"invalid table name: {table!r}")
        self._table = table
        self._lock = threading.Lock()

    def _path(self) -> str:
        return self.explicit_path or self._resolve()

    def _connect(self) -> sqlite3.Connection:
        path = self._path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        # 表名拼接 str concat 非 f-string（同 MemoryPersistence，SQL 体含字面 '{}'）。
        conn.execute(
            "CREATE TABLE IF NOT EXISTS "
            + self._table
            + """ (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                last_active_at REAL NOT NULL,
                memory_count INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )

    def _row_values(self, sess) -> tuple:
        """Session 行→绑定值（json.dumps 铁律 + session_id 显式 str）。"""
        return (
            str(sess.session_id),
            str(sess.user_id or ""),
            str(sess.title or ""),
            str(sess.status.value if hasattr(sess.status, "value") else sess.status),
            float(sess.created_at),
            float(sess.last_active_at),
            int(sess.memory_count),
            int(sess.total_tokens),
            json.dumps(dict(sess.metadata or {}), ensure_ascii=False, default=str),
        )

    def _upsert_many_locked(self, sessions) -> None:
        with self._lock:
            conn = self._connect()
            try:
                self._ensure_schema(conn)
                conn.executemany(
                    "INSERT INTO "
                    + self._table
                    + """ (
                        session_id, user_id, title, status, created_at,
                        last_active_at, memory_count, total_tokens, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        user_id=excluded.user_id,
                        title=excluded.title,
                        status=excluded.status,
                        created_at=excluded.created_at,
                        last_active_at=excluded.last_active_at,
                        memory_count=excluded.memory_count,
                        total_tokens=excluded.total_tokens,
                        metadata=excluded.metadata
                    """,
                    [self._row_values(s) for s in sessions],
                )
                conn.commit()
            except sqlite3.Error as e:
                raise MemoryPersistError(f"brain session upsert failed: {e}") from e
            finally:
                conn.close()

    def upsert(self, sess) -> None:
        self._upsert_many_locked([sess])

    def upsert_many(self, sessions) -> None:
        sessions = list(sessions)
        if sessions:
            self._upsert_many_locked(sessions)

    def delete(self, session_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                self._ensure_schema(conn)
                cur = conn.execute(
                    "DELETE FROM " + self._table + " WHERE session_id = ?", (str(session_id),)
                )
                conn.commit()
                return cur.rowcount > 0
            except sqlite3.Error as e:
                raise MemoryPersistError(f"brain session delete failed: {e}") from e
            finally:
                conn.close()

    def load_rows(self) -> list[dict]:
        """全量回载（SessionManager 懒回载唯一取数口，status 原样串由调用方归一
        枚举——持久化层不 import session 模块防循环依赖）。"""
        with self._lock:
            conn = self._connect()
            try:
                self._ensure_schema(conn)
                rows = conn.execute(
                    "SELECT " + ", ".join(_SESSION_COLUMNS) + " FROM " + self._table
                ).fetchall()
            except sqlite3.Error as e:
                raise MemoryPersistError(f"brain session load failed: {e}") from e
            finally:
                conn.close()
        out = []
        for row in rows:
            item = {col: row[col] for col in _SESSION_COLUMNS}
            try:
                item["metadata"] = json.loads(item["metadata"])
            except (TypeError, ValueError):
                item["metadata"] = {}
            out.append(item)
        return out
