"""brain 记忆面持久化（MEM-④）测试——MemoryStore 写穿 SQLite + 懒回载（重启不丢）。

MEM-② 边界①留档「brain 记忆面=进程内 MemoryStore 无持久化（持久化另拍）」：
修前记忆只能活在进程里，重启即清（live 实证：e2e 探针行随 systemctl 重启消失）。
本拍收口：add/update/get 访问刷新/delete/衰减归档/容量驱逐全轴写穿落盘，进程
重启（=新 store 实例）后回载，记忆不丢。

诚实边界（测试锁死）：
①embedding 行向量=查询期懒算缓存，刻意不落盘（回载 embedding=None）。
②写失败如实抛 MemoryPersistError 禁静默吞（禁谎报 durable）。
③纯内存实例（persist=None）零行为变化（控制组）。

数据面=tmp 全隔离（BRAIN_MEMORY_DB/USERS_DB/ENTERPRISE_DB tmp），绝不写生产库。
"""

import ast
import sqlite3
import time
from pathlib import Path

import pytest

from src.hippo.decay import DecayStrategy
from src.hippo.memory_persist import (
    MemoryPersistence,
    MemoryPersistError,
    SessionPersistence,
    default_db_path,
)
from src.hippo.memory_store import MemoryStore
from src.hippo.ownership import memory_owner, session_owner
from src.hippo.session import SessionManager, SessionStatus


def _p(tmp_path) -> MemoryPersistence:
    return MemoryPersistence(str(tmp_path / "brain_memory.db"))


def _rows(db_path) -> list[dict]:
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM brain_memories")]


# ── 重启语义（写穿+回载）─────────────────────────────────────────────


class TestRestartSurvives:
    def test_add_survives_restart(self, tmp_path):
        """add→新实例（=重启）→行还在，字段逐键往返。"""
        p = _p(tmp_path)
        a = MemoryStore(persist=p)
        a.add("s1", "持久化探针 MEM-4 alpha", importance=0.7, tags=["x", "y"])
        a.add("s2", "持久化探针 MEM-4 beta")

        b = MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db")))
        got = {m.content for m in b.search(limit=10)}
        assert got == {"持久化探针 MEM-4 alpha", "持久化探针 MEM-4 beta"}
        m = b.search(session_id="s1")[0]
        assert m.importance == 0.7
        assert m.tags == ["x", "y"]
        assert m.session_id == "s1"
        assert m.access_count == 0
        assert m.archived is False

    def test_metadata_json_roundtrip(self, tmp_path):
        """metadata dict json.dumps 落/loads 回（SQLite 铁律）。"""
        p = _p(tmp_path)
        a = MemoryStore(persist=p)
        mem = a.add("s1", "元数据往返 MEM-4", metadata={"k": "v", "n": 3, "lst": [1, 2]})
        b = MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db")))
        m2 = b.peek(mem.memory_id)
        assert m2 is not None
        assert m2.metadata == {"k": "v", "n": 3, "lst": [1, 2]}

    def test_embedding_cache_not_persisted(self, tmp_path):
        """诚实边界①：行向量=查询期缓存不落盘，回载恒 None（下次语义查询重算）。"""
        p = _p(tmp_path)
        a = MemoryStore(persist=p)
        mem = a.add("s1", "向量缓存边界 MEM-4")
        a.apply_embeddings({mem.memory_id: [0.1, 0.2]})
        cached = a.peek(mem.memory_id)
        assert cached is not None
        assert cached.embedding == [0.1, 0.2]

        b = MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db")))
        loaded = b.peek(mem.memory_id)
        assert loaded is not None
        assert loaded.embedding is None

    def test_load_merge_only_missing(self, tmp_path):
        """回载只补缺不覆盖：同 id 内存真源较新时禁被库行洗回。"""
        p = _p(tmp_path)
        b = MemoryStore(persist=p)
        mem = b.add("s1", "内存较新版本")
        # 直接改库行模拟「库中是旧版本」
        with sqlite3.connect(str(tmp_path / "brain_memory.db")) as c:
            c.execute(
                "UPDATE brain_memories SET content=? WHERE memory_id=?",
                ("库里旧版本", mem.memory_id),
            )
            c.commit()
        b._loaded = False  # 强制重新回载
        assert b.search(limit=10)[0].content == "内存较新版本"


# ── 写穿逐变更轴 ──────────────────────────────────────────────────


class TestWriteThrough:
    def test_update_persists(self, tmp_path):
        p = _p(tmp_path)
        a = MemoryStore(persist=p)
        mem = a.add("s1", "更新前 MEM-4")
        a.update(mem.memory_id, content="更新后 MEM-4", importance=0.9, tags=["t1"])
        b = MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db")))
        m2 = b.peek(mem.memory_id)
        assert m2 is not None
        assert m2.content == "更新后 MEM-4"
        assert m2.importance == 0.9
        assert m2.tags == ["t1"]

    def test_update_content_invalidates_embedding_cache(self, tmp_path):
        """MEM-① 语义：内容变更→旧向量失效；失效态不落盘（缓存本就不落盘）。"""
        p = _p(tmp_path)
        a = MemoryStore(persist=p)
        mem = a.add("s1", "旧文 MEM-4")
        a.apply_embeddings({mem.memory_id: [0.9]})
        a.update(mem.memory_id, content="新文 MEM-4")
        after = a.peek(mem.memory_id)
        assert after is not None
        assert after.embedding is None

    def test_get_access_refresh_persists(self, tmp_path):
        """get() 读取即活动（access_count/last_accessed_at）写穿落盘。"""
        p = _p(tmp_path)
        a = MemoryStore(persist=p)
        mem = a.add("s1", "访问刷新 MEM-4")
        before = mem.last_accessed_at
        a.get(mem.memory_id)
        b = MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db")))
        m2 = b.peek(mem.memory_id)
        assert m2 is not None
        assert m2.access_count == 1
        assert m2.last_accessed_at >= before

    def test_delete_persists_and_missing_is_false(self, tmp_path):
        p = _p(tmp_path)
        a = MemoryStore(persist=p)
        mem = a.add("s1", "删除写穿 MEM-4")
        assert a.delete(mem.memory_id) is True
        assert a.delete("no-such-id") is False
        b = MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db")))
        assert b.search(limit=10) == []
        assert _rows(str(tmp_path / "brain_memory.db")) == []

    def test_decay_forget_and_archive_persist(self, tmp_path):
        """衰减周期：遗忘行删行、归档行 archived=1、retention 刷新落盘。"""
        p = _p(tmp_path)
        a = MemoryStore(
            persist=p,
            strategy=DecayStrategy.EXPONENTIAL,
            half_life_hours=1.0,
            archive_threshold=0.3,
            forget_threshold=0.05,
        )
        now = time.time()
        old = a.add("s1", "远古将遗忘 MEM-4")
        old.created_at = now - 100 * 3600  # e^-69 ≈ 0 → forget
        mid = a.add("s1", "中年将归档 MEM-4")
        mid.created_at = now - 2 * 3600  # e^-1.38 ≈ 0.25 → archive
        a.add("s1", "新鲜不动 MEM-4")
        stats = a.run_decay_cycle()
        assert stats["forgotten"] == 1
        assert stats["archived"] == 1

        b = MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db")))
        by_content = {m.content: m for m in b.search(include_archived=True, limit=10)}
        assert "远古将遗忘 MEM-4" not in by_content
        assert by_content["中年将归档 MEM-4"].archived is True
        assert by_content["新鲜不动 MEM-4"].archived is False
        # retention 刷新真落盘（远古行已删，新鲜行≈1.0）
        assert by_content["新鲜不动 MEM-4"].retention == pytest.approx(1.0, abs=0.01)

    def test_evict_persists(self, tmp_path):
        """容量驱逐：内存 pop 与库删行同轴（禁内存丢了库里复活）。"""
        p = _p(tmp_path)
        a = MemoryStore(max_entries=1, persist=p)
        first = a.add("s1", "将被驱逐 MEM-4")
        second = a.add("s1", "留下 MEM-4")
        assert a.peek(first.memory_id) is None
        b = MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db")))
        contents = {m.content for m in b.search(limit=10)}
        assert contents == {"留下 MEM-4"}
        assert second.memory_id in {m.memory_id for m in b.search(limit=10)}


# ── 库路径/schema ─────────────────────────────────────────────────


class TestPathAndSchema:
    def test_default_db_path_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BRAIN_MEMORY_DB", str(tmp_path / "env.db"))
        assert default_db_path() == str(tmp_path / "env.db")
        p = MemoryPersistence()
        p.upsert(_FakeMem("m1"))
        assert (tmp_path / "env.db").exists()

    def test_explicit_path_beats_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BRAIN_MEMORY_DB", str(tmp_path / "env.db"))
        p = MemoryPersistence(str(tmp_path / "explicit.db"))
        p.upsert(_FakeMem("m1"))
        assert (tmp_path / "explicit.db").exists()
        assert not (tmp_path / "env.db").exists()

    def test_schema_idempotent_and_column_set(self, tmp_path):
        p = _p(tmp_path)
        p.upsert(_FakeMem("m1"))
        p2 = MemoryPersistence(str(tmp_path / "brain_memory.db"))
        p2.upsert(_FakeMem("m2"))  # 二次 _ensure_schema 幂等
        with sqlite3.connect(str(tmp_path / "brain_memory.db")) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(brain_memories)")}
        assert cols == {
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
        }
        assert len(_rows(str(tmp_path / "brain_memory.db"))) == 2

    def test_in_memory_store_touches_no_db(self, monkeypatch, tmp_path):
        """控制组：persist=None 纯内存实例零落盘（既有语义字节零变化）。"""
        monkeypatch.setenv("BRAIN_MEMORY_DB", str(tmp_path / "never.db"))
        s = MemoryStore()
        mem = s.add("s1", "纯内存 MEM-4")
        s.get(mem.memory_id)
        s.update(mem.memory_id, content="改 MEM-4")
        s.run_decay_cycle()
        s.delete(mem.memory_id)
        assert not (tmp_path / "never.db").exists()


# ── 失败可见（禁谎报 durable）──────────────────────────────────────


class _FailingPersist(MemoryPersistence):
    def upsert(self, mem):
        raise MemoryPersistError("injected upsert failure")


class _FailingBulkPersist(MemoryPersistence):
    def upsert_many(self, mems):
        raise MemoryPersistError("injected upsert_many failure")


class TestFailureVisible:
    def test_write_failure_raises_not_silent(self, tmp_path):
        """诚实边界②：写失败如实抛，禁静默丢写谎报 durable。"""
        s = MemoryStore(persist=_FailingPersist(str(tmp_path / "x.db")))
        with pytest.raises(MemoryPersistError):
            s.add("s1", "写失败可见 MEM-4")

    def test_decay_bulk_write_failure_raises(self, tmp_path):
        s = MemoryStore(
            persist=_FailingBulkPersist(str(tmp_path / "x.db")),
            strategy=DecayStrategy.EXPONENTIAL,
        )
        s.add("s1", "批量写失败可见 MEM-4")  # 单行 upsert 正常（真落盘）
        with pytest.raises(MemoryPersistError):
            s.run_decay_cycle()


# ── 父会话行持久化（归属=父会话行继承，父行不落盘=「不丢但取不回」）──────


class TestSessionPersistence:
    def test_session_roundtrip(self, tmp_path):
        """create→新实例（=重启）→会话行还在，字段逐键往返（含 status 枚举）。"""
        p = SessionPersistence(str(tmp_path / "brain_memory.db"))
        a = SessionManager(persist=p)
        s = a.create(user_id="u1", title="会话往返 MEM-4", session_id="sess-r1")
        a.increment_memory_count("sess-r1", 2)

        b = SessionManager(persist=SessionPersistence(str(tmp_path / "brain_memory.db")))
        s2 = b.peek("sess-r1")
        assert s2 is not None
        assert s2.user_id == "u1"
        assert s2.title == "会话往返 MEM-4"
        assert s2.status == SessionStatus.ACTIVE
        assert s2.memory_count == 2
        assert s2.session_id == s.session_id

    def test_session_mutations_persist(self, tmp_path):
        p = SessionPersistence(str(tmp_path / "brain_memory.db"))
        a = SessionManager(persist=p)
        a.create(user_id="u1", session_id="sess-m1")
        a.touch("sess-m1")
        a.archive("sess-m1")
        a.create(user_id="u1", session_id="sess-m2")
        a.delete("sess-m2")

        b = SessionManager(persist=SessionPersistence(str(tmp_path / "brain_memory.db")))
        s1 = b.peek("sess-m1")
        assert s1 is not None
        assert s1.status == SessionStatus.ARCHIVED
        assert b.peek("sess-m2") is None

    def test_lifecycle_status_persists(self, tmp_path):
        """run_lifecycle_check 状态迁移写穿（idle→落盘可见）。"""
        p = SessionPersistence(str(tmp_path / "brain_memory.db"))
        a = SessionManager(persist=p, idle_timeout_seconds=1.0, expire_timeout_seconds=100000.0)
        s = a.create(user_id="u1", session_id="sess-lc1")
        s.last_active_at = time.time() - 3600  # 远超 idle 门槛
        stats = a.run_lifecycle_check()
        assert stats["newly_idle"] == 1

        b = SessionManager(persist=SessionPersistence(str(tmp_path / "brain_memory.db")))
        s2 = b.peek("sess-lc1")
        assert s2 is not None
        assert s2.status == SessionStatus.IDLE

    def test_owner_lookup_survives_restart(self, tmp_path):
        """归属闸取数链（memory_owner/session_owner）重启后真命中父行不 404。"""
        p = SessionPersistence(str(tmp_path / "brain_memory.db"))
        a = SessionManager(persist=p)
        a.create(user_id="u-owner", session_id="sess-own")

        mem_p = MemoryPersistence(str(tmp_path / "brain_memory.db"))
        mem_store = MemoryStore(persist=mem_p)
        mem = mem_store.add("sess-own", "归属随重启 MEM-4")

        # 模拟重启：两真源都是新实例同库
        b_sessions = SessionManager(persist=SessionPersistence(str(tmp_path / "brain_memory.db")))
        b_store = MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db")))
        m2 = b_store.peek(mem.memory_id)
        assert m2 is not None
        assert memory_owner(b_sessions, m2) == "u-owner"
        assert session_owner(b_sessions, "sess-own") == "u-owner"

    def test_session_evict_persists(self, tmp_path):
        p = SessionPersistence(str(tmp_path / "brain_memory.db"))
        a = SessionManager(max_sessions=1, persist=p)
        a.create(user_id="u1", session_id="sess-ev1")
        a.archive("sess-ev1")  # 既有语义：_evict_oldest 只驱逐 idle/expired/archived 候选
        a.create(user_id="u1", session_id="sess-ev2")
        b = SessionManager(persist=SessionPersistence(str(tmp_path / "brain_memory.db")))
        assert b.peek("sess-ev1") is None
        assert b.peek("sess-ev2") is not None

    def test_in_memory_sessions_touch_no_db(self, monkeypatch, tmp_path):
        """控制组：SessionManager() 纯内存零落盘（既有语义字节零变化）。"""
        monkeypatch.setenv("BRAIN_MEMORY_DB", str(tmp_path / "never.db"))
        s = SessionManager()
        s.create(user_id="u1", session_id="sess-nc1")
        s.increment_memory_count("sess-nc1")
        s.archive("sess-nc1")
        s.delete("sess-nc1")
        s.run_lifecycle_check()
        assert not (tmp_path / "never.db").exists()


# ── brain API 全链（接线了≠跑通了）────────────────────────────────


_USERS_DDL = (
    "CREATE TABLE IF NOT EXISTS users ("
    "id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "email TEXT DEFAULT '', password_hash TEXT NOT NULL, "
    "role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1, "
    "tenant_id TEXT DEFAULT 'default', "
    "created_at TEXT DEFAULT (datetime('now')), "
    "updated_at TEXT DEFAULT (datetime('now')));"
)


@pytest.fixture()
def brain_env(tmp_path, monkeypatch):
    """brain router 进程内测试面 + store 换 persist=tmp 实例 + 身份库 tmp 隔离。"""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.brain as brain_api
    import src.database.postgres as pg_mod
    from src.config import settings
    from src.database.postgres import SQLitePool
    from src.hippo.memory_store import MemoryStore
    from src.hippo.session import SessionManager

    users_db = tmp_path / "opensoul.db"
    with sqlite3.connect(users_db) as c:
        c.executescript(_USERS_DDL)
        c.commit()
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setenv("BRAIN_MEMORY_DB", str(tmp_path / "brain_memory.db"))

    monkeypatch.setattr(
        brain_api,
        "store",
        MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db"))),
    )
    monkeypatch.setattr(
        brain_api,
        "sessions",
        SessionManager(persist=SessionPersistence(str(tmp_path / "brain_memory.db"))),
    )

    tmp_db = tmp_path / "brain_data.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    monkeypatch.setattr(pg_mod, "db_pool", SQLitePool())

    @asynccontextmanager
    async def _lifespan(app):
        yield

    app = FastAPI(lifespan=_lifespan)
    app.include_router(brain_api.router, prefix="/api/brain")
    return TestClient(app), tmp_path


class TestBrainApiEndToEnd:
    def test_remember_persists_and_delete_clears_db(self, brain_env):
        client, tmp_path = brain_env
        resp = client.post(
            "/api/brain/remember",
            params={"session_id": "s-mem4", "content": "API 全链 MEM-4 持久化", "tags": "a,b"},
        )
        assert resp.status_code == 200
        mid = resp.json()["memory_id"]

        # 数据工件回读：行真落 tmp 库
        rows = _rows(str(tmp_path / "brain_memory.db"))
        assert len(rows) == 1
        assert rows[0]["memory_id"] == mid
        assert rows[0]["content"] == "API 全链 MEM-4 持久化"

        # 模拟重启（换新 store+sessions 实例同库）→ 记忆与父会话行都在，
        # session 桶 /recall 归属闸真命中不 404（live --post P5 回归锁死）
        import src.api.brain as brain_api

        brain_api.store = MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db")))
        brain_api.sessions = SessionManager(
            persist=SessionPersistence(str(tmp_path / "brain_memory.db"))
        )
        recall = client.get("/api/brain/recall", params={"session_id": "s-mem4"})
        assert recall.status_code == 200  # 修前（父会话行不落盘）：重启后 404 "Session not found"
        contents = [m["content"] for m in recall.json()["memories"]]
        assert "API 全链 MEM-4 持久化" in contents

        # 删除口（MEM-③）与持久化同轴：删=库行真消失
        d = client.delete(f"/api/brain/memories/{mid}")
        assert d.status_code == 200
        assert _rows(str(tmp_path / "brain_memory.db")) == []

    def test_status_reflects_loaded_rows(self, brain_env):
        """回载行真进工作集（/status 计数可见，重启后计数不丢）。"""
        client, tmp_path = brain_env
        client.post(
            "/api/brain/remember",
            params={"session_id": "s-mem4b", "content": "状态计数 MEM-4"},
        )
        import src.api.brain as brain_api

        brain_api.store = MemoryStore(persist=MemoryPersistence(str(tmp_path / "brain_memory.db")))
        brain_api.sessions = SessionManager(
            persist=SessionPersistence(str(tmp_path / "brain_memory.db"))
        )
        st = client.get("/api/brain/status").json()
        assert st["memory"]["total_memories"] == 1


# ── 静态接线锁死 ──────────────────────────────────────────────────


class TestStaticWiring:
    def test_brain_store_constructed_with_persist(self):
        """brain.py 模块级 store 真带 persist 构造（接线锁死，防悄悄回退纯内存）。"""
        src = Path("src/api/brain.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "store":
                        call = node.value
                        if isinstance(call, ast.Call):
                            for kw in call.keywords:
                                if kw.arg == "persist":
                                    found = True
        assert found, "brain.py store 构造必须带 persist=MemoryPersistence()"


class _FakeMem:
    """MemoryPersistence 单元测试用最小行形（duck-typed）。"""

    def __init__(self, mid: str):
        self.memory_id = mid
        self.session_id = "s"
        self.content = "c"
        self.importance = 0.5
        self.tags = []
        self.metadata = {}
        self.created_at = time.time()
        self.last_accessed_at = time.time()
        self.access_count = 0
        self.archived = False
        self.retention = 1.0
