from __future__ import annotations

import logging
import re

from src.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL dialect conversion: PostgreSQL → SQLite
# ---------------------------------------------------------------------------

_PARAM_RE = re.compile(r"\$(\d+)")
_ANY_RE = re.compile(r"ANY\(\$(\d+)\)")


def _convert_sql_for_sqlite(sql: str, args: tuple) -> tuple[str, tuple]:
    """Convert PostgreSQL-style SQL to SQLite-compatible SQL.

    Handles:
    - $N → ? positional placeholders
    - ANY($N) → expanded IN (?, ?, ...)
    - NOW() → datetime('now')
    - UUID objects → str for SQLite compatibility
    - 单tuple/list参数展开（调用约定兼容层，2026-09-21实证修复）
    """
    from uuid import UUID

    # 0. 调用约定兼容：器官模块（heredity/self_evolution、learn/long_term、
    # learn/experience、mind/user_memory、mirror/metacognition、
    # cortex/multi_agent_coord，共43处调用点）按"databases"库风格传参：
    # db.fetch(query, (tenant_id, agent_id, ts))——参数是单个tuple/list；
    # 而本适配器按asyncpg风格*args展开。此前每个此类查询都以
    # ProgrammingError绑定失败（"写了≠接线了"的适配层形态：表建好了，
    # 查询照样全挂）。仅当「单序列参数」且「占位符数量与序列长度一致」
    # 时展开；数量不一致保持原样→sqlite按原样报绑定错误（不静默吞）。
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        seq = tuple(args[0])
        n_sqlite = sql.count("?")
        n_pg = len(_PARAM_RE.findall(sql))
        if (n_sqlite == len(seq)) or (n_pg == len(seq) and n_sqlite == 0):
            args = seq

    # 0.1 Convert UUID objects to strings (SQLite doesn't support UUID type)
    args = tuple(str(a) if isinstance(a, UUID) else a for a in args)

    # 1. Handle ANY($N) first — expand list args into IN (?, ...)
    def _replace_any(m: re.Match) -> str:
        idx = int(m.group(1)) - 1  # $N is 1-indexed
        arg = args[idx]
        if isinstance(arg, (list, tuple)):
            return "IN (" + ", ".join("?" for _ in arg) + ")"
        return "IN (?)"

    any_indices = {int(m.group(1)) for m in _ANY_RE.finditer(sql)}
    new_sql = _ANY_RE.sub(_replace_any, sql)

    # Expand list arguments for ANY
    expanded_args: list = []
    for i, arg in enumerate(args):
        if (i + 1) in any_indices and isinstance(arg, (list, tuple)):
            expanded_args.extend(arg)
        else:
            expanded_args.append(arg)

    # 2. Replace remaining $N → ?
    new_sql = _PARAM_RE.sub("?", new_sql)

    # 3. Replace NOW() → datetime('now')
    new_sql = new_sql.replace("NOW()", "datetime('now')")

    return new_sql, tuple(expanded_args)


# ---------------------------------------------------------------------------
# SQLite adapter — mimics asyncpg Pool interface
# ---------------------------------------------------------------------------


class _SQLiteRecord:
    """Dict-like row wrapper that mimics asyncpg.Record attribute access."""

    __slots__ = ("_data",)

    def __init__(self, row: dict):
        self._data = row

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data[key]

    def __getattr__(self, name: str):
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name)

    def __contains__(self, key):
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return f"_SQLiteRecord({self._data})"

    def keys(self):
        return self._data.keys()


class _SQLiteConnection:
    """Wraps an aiosqlite connection with asyncpg-compatible methods."""

    def __init__(self, conn):
        self._conn = conn

    async def fetch(self, query: str, *args) -> list[_SQLiteRecord]:
        sql, params = _convert_sql_for_sqlite(query, args)
        async with self._conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            result = [_SQLiteRecord(dict(zip(columns, row))) for row in rows]
        # C2实测发现：INSERT/UPDATE/DELETE...RETURNING走fetch/fetchrow路径
        # （api/agent.py /register等），原实现只读不commit——写入留在未提交事务里，
        # 同连接可见但重启即丢。写语句在fetch路径同样必须提交。
        if sql.lstrip().split(None, 1)[0].upper() in ("INSERT", "UPDATE", "DELETE"):
            await self._conn.commit()
        return result

    async def fetchrow(self, query: str, *args) -> _SQLiteRecord | None:
        rows = await self.fetch(query, *args)
        return rows[0] if rows else None

    async def fetchval(self, query: str, *args):
        row = await self.fetchrow(query, *args)
        if row is None:
            return None
        return row[0]

    async def execute(self, query: str, *args) -> str:
        sql, params = _convert_sql_for_sqlite(query, args)
        cursor = await self._conn.execute(sql, params)
        await self._conn.commit()
        # Detect operation type from SQL to return PostgreSQL-compatible status
        sql_upper = sql.strip().upper()
        if sql_upper.startswith("DELETE"):
            return f"DELETE {cursor.rowcount}"
        elif sql_upper.startswith("UPDATE"):
            return f"UPDATE {cursor.rowcount}"
        return f"INSERT 0 {cursor.rowcount}"

    async def executemany(self, query: str, args_list: list) -> None:
        sql, _ = _convert_sql_for_sqlite(query, ())
        await self._conn.executemany(sql, args_list)
        await self._conn.commit()


class _SQLitePoolContext:
    """Mimics asyncpg pool.acquire() context manager."""

    def __init__(self, pool: SQLitePool):
        self._pool = pool

    async def __aenter__(self) -> _SQLiteConnection:
        conn = await self._pool._get_conn()
        return _SQLiteConnection(conn)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass  # Connection managed by pool


class SQLitePool:
    def __init__(self):
        self._conn = None

    async def connect(self):
        import aiosqlite

        db_path = settings.database_url.replace("sqlite:///", "").replace("sqlite://", "")
        self._conn = await aiosqlite.connect(db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._ensure_schema()

    async def _ensure_schema(self):
        """幂等schema guard：老库缺列自动补齐（migrations/*.sql只建新表不ALTER已有表，
        2026-09-19实证：live knowledge_chunks缺token_count列→知识入库100%报错）。

        模式：PRAGMA table_info探测→缺失才ALTER，幂等可重复执行。
        每补一列在_SCHEMA_FIXES登记一行；出现新的"no column named"错误时在此登记。
        C1（2026-10-11）：captures单表自愈推广到全库——对生产data/opensoul.db
        全表审计（实测DDL vs 调用点列集）后在此登记其余漂移表，补列留痕journal。
        """
        _SCHEMA_FIXES = [
            (
                "knowledge_chunks",
                "token_count",
                "ALTER TABLE knowledge_chunks ADD COLUMN token_count INT DEFAULT 0",
            ),
            # ── C1 2026-10-11 全库漂移审计登记（生产实测DDL vs 真实调用点列集）──
            # agents：生产是logo_url形状（type列非agent_type、无token），而
            # api/agent.py:81/103/139与middleware/auth.py:46（require_agent鉴权）
            # 引用agent_type/metadata/token/last_heartbeat——缺列时注册/心跳/
            # X-Agent-Token鉴权全no such column。token默认NULL而非''：空串会与
            # 存量行匹配（require_agent WHERE token=$1），NULL不等于任何传入值，
            # 防默认值造出"万能token"
            (
                "agents",
                "agent_type",
                "ALTER TABLE agents ADD COLUMN agent_type TEXT DEFAULT ''",
            ),
            (
                "agents",
                "metadata",
                "ALTER TABLE agents ADD COLUMN metadata TEXT DEFAULT '{}'",
            ),
            (
                "agents",
                "token",
                "ALTER TABLE agents ADD COLUMN token TEXT DEFAULT NULL",
            ),
            (
                "agents",
                "last_heartbeat",
                "ALTER TABLE agents ADD COLUMN last_heartbeat TEXT DEFAULT NULL",
            ),
            # C2（2026-10-11）：agent_reports 生产表只有 content 列（logo_url形状遗留），
            # 而 api/agent.py /report 与 migrations/001,002 都写 data 列——
            # "no column named data" 写路径必炸。按 C1 纪律登记缺列自愈，
            # content 存量列不动（防丢已有数据）。
            (
                "agent_reports",
                "data",
                "ALTER TABLE agent_reports ADD COLUMN data TEXT DEFAULT '{}'",
            ),
            # 审计另发现两张"同名不同形"表冲突（trajectory_events：store.py形状
            # vs api/trajectory_api.py形状；agent_messages：消息树形状 vs
            # cortex/multi_agent_coord形状）——NOT NULL无默认列ALTER补不了，
            # 补列只是堵漏。已根因拆表（各归各表，见对应模块注释），不在此登记。
        ]
        conn = self._conn
        if conn is None:
            return
        for table, column, ddl in _SCHEMA_FIXES:
            async with conn.execute(f"PRAGMA table_info({table})") as cursor:
                cols = [row[1] for row in await cursor.fetchall()]
            if cols and column not in cols:
                await conn.execute(ddl)
                await conn.commit()
                # 不可逆结构操作留痕（captures迁移同款纪律：失败/变更必须可见）
                logger.info("schema drift healed: %s.%s added", table, column)

    async def disconnect(self):
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _get_conn(self):
        if not self._conn:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    def acquire(self):
        return _SQLitePoolContext(self)

    async def fetch(self, query: str, *args) -> list[_SQLiteRecord]:
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args) -> _SQLiteRecord | None:
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args) -> str:
        async with self.acquire() as conn:
            return await conn.execute(query, *args)

    async def executemany(self, query: str, args_list: list) -> None:
        async with self.acquire() as conn:
            await conn.executemany(query, args_list)


# ---------------------------------------------------------------------------
# PostgreSQL adapter (original asyncpg)
# ---------------------------------------------------------------------------


class PostgresPool:
    def __init__(self):
        self._pool = None

    async def connect(self):
        import asyncpg

        self._pool = await asyncpg.create_pool(
            settings.database_url.replace("postgresql+asyncpg://", "postgresql://"),
            min_size=2,
            max_size=20,
        )

    async def disconnect(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self):
        if not self._pool:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._pool

    def acquire(self):
        return self.pool.acquire()

    async def fetch(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args) -> str:
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def executemany(self, query: str, args_list: list) -> None:
        async with self.pool.acquire() as conn:
            await conn.executemany(query, args_list)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _create_pool():
    if settings.database_url.startswith("sqlite"):
        return SQLitePool()
    return PostgresPool()


db_pool = _create_pool()
