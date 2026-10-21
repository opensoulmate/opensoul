"""hippo 面持久化（MEM-⑤）测试——api/hippo.py 独立实例写穿 SQLite + 懒回载（重启不丢）。

MEM-④ 诚实边界③留档「hippo /sessions /memories 独立实例仍纯内存（F3b-⑱⑳ 面
另拍）」：hippo 面 store/sessions 与 brain 面是两套独立内存真源，修前纯内存
重启即清。本拍收口：hippo 双单例接 persist（独立库 data/hippo_memory.db，
HIPPO_MEMORY_DB env；独立表 hippo_memories/hippo_sessions），重启记忆/会话不丢。

诚实边界（测试锁死）：
①与 brain 面独立库独立表——共库共表会互相回载对方行=跨面串行（hippo 衰减
  遗忘能删掉 brain 面记忆），隔离必须真隔离。
②纯内存构造（persist 缺省）零行为变化（控制组）。
③写失败如实抛 MemoryPersistError（MEM-④ 同契约，共用类不重复测）。

数据面=tmp 全隔离（HIPPO_MEMORY_DB/BRAIN_MEMORY_DB/USERS_DB/ENTERPRISE_DB
随 tmp），绝不写生产库。
"""

import ast
import sqlite3
from pathlib import Path

import pytest

from src.hippo.decay import DecayStrategy
from src.hippo.memory_persist import (
    MemoryPersistence,
    SessionPersistence,
    default_db_path,
    hippo_db_path,
)
from src.hippo.memory_store import MemoryStore
from src.hippo.ownership import memory_owner
from src.hippo.session import SessionManager


def _hp(tmp_path) -> MemoryPersistence:
    return MemoryPersistence(str(tmp_path / "hippo_memory.db"), table="hippo_memories")


def _hs(tmp_path) -> SessionPersistence:
    return SessionPersistence(str(tmp_path / "hippo_memory.db"), table="hippo_sessions")


def _tables(db_path) -> set:
    with sqlite3.connect(db_path) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _mem_rows(db_path, table="hippo_memories") -> list:
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(f"SELECT * FROM {table}")]


# ── 重启语义（写穿+回载，hippo 表）────────────────────────────────


class TestRestartSurvives:
    def test_memory_survives_restart(self, tmp_path):
        """add→新实例（=重启）→行还在；hippo 构造形（DecayStrategy）随 persist 不变。"""
        a = MemoryStore(
            strategy=DecayStrategy.ACCESS_REINFORCED,
            half_life_hours=24.0,
            persist=_hp(tmp_path),
        )
        a.add("hs1", "持久化探针 MEM-5 alpha", importance=0.7, tags=["x", "y"])
        a.add("hs2", "持久化探针 MEM-5 beta")

        b = MemoryStore(persist=_hp(tmp_path))
        got = {m.content for m in b.search(limit=10)}
        assert got == {"持久化探针 MEM-5 alpha", "持久化探针 MEM-5 beta"}
        m = b.search(session_id="hs1")[0]
        assert m.importance == 0.7
        assert m.tags == ["x", "y"]
        assert m.session_id == "hs1"
        # 驱动面构造形零变化（strategy/half_life 只进内存引擎不落盘，构造即生效）
        assert b.decay_engine.strategy == DecayStrategy.ACCESS_REINFORCED

    def test_metadata_json_roundtrip(self, tmp_path):
        """metadata dict json.dumps 落/loads 回（SQLite 铁律，hippo 表同纪律）。"""
        a = MemoryStore(persist=_hp(tmp_path))
        mem = a.add("hs1", "元数据往返 MEM-5", metadata={"k": "v", "n": 3, "lst": [1, 2]})
        b = MemoryStore(persist=_hp(tmp_path))
        m2 = b.peek(mem.memory_id)
        assert m2 is not None
        assert m2.metadata == {"k": "v", "n": 3, "lst": [1, 2]}

    def test_session_survives_restart(self, tmp_path):
        """会话行往返：user_id/title/status/memory_count/metadata 逐键。"""
        a = SessionManager(persist=_hs(tmp_path))
        s = a.create(user_id="user-mem5", title="MEM-5 会话", metadata={"room": "b"})
        a.increment_memory_count(s.session_id)

        b = SessionManager(persist=_hs(tmp_path))
        s2 = b.get(s.session_id)
        assert s2 is not None
        assert s2.user_id == "user-mem5"
        assert s2.title == "MEM-5 会话"
        assert s2.memory_count == 1
        assert s2.metadata == {"room": "b"}

    def test_write_axes_survive_restart(self, tmp_path):
        """update/get 访问刷新/delete 全轴写穿（hippo 表 SQL 真跑通）。"""
        a = MemoryStore(persist=_hp(tmp_path))
        mem = a.add("hs1", "更新前 MEM-5")
        a.update(mem.memory_id, content="更新后 MEM-5", importance=0.9, tags=["t1"])
        a.get(mem.memory_id)  # 读取即活动→access_count 落盘

        b = MemoryStore(persist=_hp(tmp_path))
        m2 = b.peek(mem.memory_id)
        assert m2 is not None
        assert m2.content == "更新后 MEM-5"
        assert m2.importance == 0.9
        assert m2.access_count == 1

        b.delete(mem.memory_id)
        c = MemoryStore(persist=_hp(tmp_path))
        assert c.peek(mem.memory_id) is None
        assert _mem_rows(tmp_path / "hippo_memory.db") == []

    def test_decay_archive_persists(self, tmp_path):
        """衰减周期归档/遗忘走 hippo 表：archived 标志与删行重启可见（upsert_many
        批量写穿真跑通 hippo 表 SQL）。"""
        import time as _time

        a = MemoryStore(
            persist=_hp(tmp_path),
            strategy=DecayStrategy.EXPONENTIAL,
            half_life_hours=1.0,
            archive_threshold=0.3,
            forget_threshold=0.05,
        )
        now = _time.time()
        old = a.add("hs1", "远古将遗忘 MEM-5")
        old.created_at = now - 100 * 3600  # e^-69 ≈ 0 → forget
        mid = a.add("hs1", "中年将归档 MEM-5")
        mid.created_at = now - 2 * 3600  # e^-1.38 ≈ 0.25 → archive
        stats = a.run_decay_cycle()
        assert stats["forgotten"] == 1
        assert stats["archived"] == 1

        b = MemoryStore(persist=_hp(tmp_path))
        by_content = {m.content: m for m in b.search(include_archived=True, limit=10)}
        assert "远古将遗忘 MEM-5" not in by_content
        assert by_content["中年将归档 MEM-5"].archived is True


# ── 库路径/表名（独立真源）────────────────────────────────────────


class TestDbPathAndTables:
    def test_hippo_db_path_env_and_default(self, monkeypatch, tmp_path):
        """HIPPO_MEMORY_DB env 覆盖；缺省=data/hippo_memory.db 且与 brain 面分库。"""
        monkeypatch.setenv("HIPPO_MEMORY_DB", str(tmp_path / "x.db"))
        assert hippo_db_path() == str(tmp_path / "x.db")
        monkeypatch.delenv("HIPPO_MEMORY_DB")
        default = hippo_db_path()
        assert default.endswith(str(Path("data") / "hippo_memory.db"))
        assert default != default_db_path()

    def test_explicit_path_wins_env(self, monkeypatch, tmp_path):
        """显式 db_path 胜 env（MEM-④ 同语义）。"""
        monkeypatch.setenv("HIPPO_MEMORY_DB", str(tmp_path / "env.db"))
        a = MemoryStore(
            persist=MemoryPersistence(str(tmp_path / "explicit.db"), table="hippo_memories")
        )
        a.add("hs1", "显式路径 MEM-5")
        assert len(_mem_rows(tmp_path / "explicit.db")) == 1
        assert not (tmp_path / "env.db").exists()

    def test_tables_isolated_from_brain(self, tmp_path):
        """独立表：hippo 库=hippo_memories/hippo_sessions；brain 缺省表名零漂移。"""
        db = tmp_path / "hippo_memory.db"
        MemoryStore(persist=_hp(tmp_path)).add("hs1", "表名探针 MEM-5")
        SessionManager(persist=_hs(tmp_path)).create(user_id="u", title="t")
        assert {"hippo_memories", "hippo_sessions"} <= _tables(db)
        assert "brain_memories" not in _tables(db)

        bdb = tmp_path / "brain_memory.db"
        MemoryStore(persist=MemoryPersistence(str(bdb))).add("s1", "brain 面行")
        SessionManager(persist=SessionPersistence(str(bdb))).create(user_id="u", title="t")
        assert {"brain_memories", "brain_sessions"} <= _tables(bdb)

    def test_cross_face_no_cross_load(self, tmp_path):
        """隔离真隔离：brain 行不进 hippo 面、hippo 行不进 brain 面（修前若共库共表
        互相回载=跨面串行，hippo 衰减遗忘能删掉 brain 面记忆）。"""
        bdb = tmp_path / "brain_memory.db"
        hdb = tmp_path / "hippo_memory.db"
        MemoryStore(persist=MemoryPersistence(str(bdb))).add("s1", "brain-side-row")
        MemoryStore(persist=MemoryPersistence(str(hdb), table="hippo_memories")).add(
            "s1", "hippo-side-row"
        )

        b2 = MemoryStore(persist=MemoryPersistence(str(bdb)))
        h2 = MemoryStore(persist=MemoryPersistence(str(hdb), table="hippo_memories"))
        assert [m.content for m in b2.search(limit=10)] == ["brain-side-row"]
        assert [m.content for m in h2.search(limit=10)] == ["hippo-side-row"]
        # hippo 面删行绝不触碰 brain 面行
        h2.delete(h2.search(limit=10)[0].memory_id)
        assert _mem_rows(bdb, "brain_memories")[0]["content"] == "brain-side-row"
        assert _mem_rows(hdb) == []

    def test_invalid_table_name_rejected(self):
        """表名是拼接标识符：非法形如实 ValueError 禁拼进 SQL。"""
        for bad in ("brain_memories; DROP TABLE x", "", "1abc", "a b"):
            with pytest.raises(ValueError):
                MemoryPersistence(table=bad)
            with pytest.raises(ValueError):
                SessionPersistence(table=bad)


# ── 缺省契约（纯内存零变化）────────────────────────────────────────


class TestDefaultContract:
    def test_pure_memory_default_unchanged(self):
        """persist 缺省=None 纯内存（session_importer 等既有构造点零行为变化）。"""
        assert MemoryStore(strategy=DecayStrategy.ACCESS_REINFORCED)._persist is None
        assert SessionManager()._persist is None

    def test_brain_defaults_zero_drift(self):
        """brain 面缺省表名/缺省解析函数零漂移（schema 契约锁死）。"""
        p = MemoryPersistence()
        s = SessionPersistence()
        assert p._table == "brain_memories"
        assert s._table == "brain_sessions"
        assert p._resolve is default_db_path
        assert s._resolve is default_db_path


# ── API 全链（重启=换新实例）────────────────────────────────────────


def _restart(hippo_api, monkeypatch, tmp_path):
    """重启语义=换全新 persist 实例（进程重启后单例重建同款）。"""
    monkeypatch.setattr(
        hippo_api,
        "store",
        MemoryStore(
            strategy=DecayStrategy.ACCESS_REINFORCED,
            half_life_hours=24.0,
            persist=MemoryPersistence(str(tmp_path / "hippo_memory.db"), table="hippo_memories"),
        ),
    )
    monkeypatch.setattr(
        hippo_api,
        "sessions",
        SessionManager(
            persist=SessionPersistence(str(tmp_path / "hippo_memory.db"), table="hippo_sessions")
        ),
    )


class TestApiRestart:
    def test_memories_sessions_survive_restart(self, will_client, monkeypatch, tmp_path):
        """/api/hippo 会话+记忆重启真取回；归属闸（父会话行继承）重启后仍命中。"""
        import src.api.hippo as hippo_api

        c = will_client
        _restart(hippo_api, monkeypatch, tmp_path)
        sid = c.post(
            "/api/hippo/sessions", json={"user_id": "default", "title": "MEM-5 e2e"}
        ).json()["session_id"]
        mid = c.post(
            "/api/hippo/memories",
            json={"session_id": sid, "content": "重启取回 MEM-5", "tags": ["mem5"]},
        ).json()["memory_id"]

        _restart(hippo_api, monkeypatch, tmp_path)  # = 重启
        r = c.get("/api/hippo/memories", params={"user_id": "default"})
        contents = [m["content"] for m in r.json()["memories"]]
        assert "重启取回 MEM-5" in contents
        r = c.get(f"/api/hippo/memories/{mid}", params={"user_id": "default"})
        assert r.status_code == 200
        assert r.json()["content"] == "重启取回 MEM-5"
        r = c.get("/api/hippo/sessions", params={"user_id": "default"})
        assert any(
            s["session_id"] == sid and s["title"] == "MEM-5 e2e" for s in r.json()["sessions"]
        )

        # 删=重启后真消失
        assert (
            c.delete(f"/api/hippo/memories/{mid}", params={"user_id": "default"}).status_code == 200
        )
        _restart(hippo_api, monkeypatch, tmp_path)
        assert c.get(f"/api/hippo/memories/{mid}", params={"user_id": "default"}).status_code == 404

    def test_ownership_gate_survives_restart(self, will_client, monkeypatch, tmp_path):
        """F3b-⑳ 归属闸跨重启真命中（父会话行持久化）：B 冒读 A 记忆 404 行存活。"""
        import src.api.hippo as hippo_api

        c = will_client
        _restart(hippo_api, monkeypatch, tmp_path)
        sid = c.post(
            "/api/hippo/sessions", json={"user_id": "user-mem5-a", "title": "A 的会话"}
        ).json()["session_id"]
        mid = c.post(
            "/api/hippo/memories",
            json={"session_id": sid, "content": "A 的记忆 MEM-5"},
            params={"user_id": "user-mem5-a"},
        ).json()["memory_id"]

        _restart(hippo_api, monkeypatch, tmp_path)  # = 重启
        # B 冒读：归属闸=父会话行继承（修前重启后父行缺失=归属 'default' 兜底串桶）
        r = c.get(f"/api/hippo/memories/{mid}", params={"user_id": "user-mem5-b"})
        assert r.status_code == 404
        r = c.get("/api/hippo/memories", params={"session_id": sid, "user_id": "user-mem5-b"})
        assert r.status_code == 404
        # A 自读：闸真命中（父会话行真回载）
        r = c.get(f"/api/hippo/memories/{mid}", params={"user_id": "user-mem5-a"})
        assert r.status_code == 200
        assert r.json()["content"] == "A 的记忆 MEM-5"

    def test_ownership_derivation_uses_persisted_session(self, tmp_path):
        """归属判定单一源跨重启：memory_owner 拿持久化回载的父会话行。"""
        store = MemoryStore(persist=_hp(tmp_path))
        sessions = SessionManager(persist=_hs(tmp_path))
        s = sessions.create(user_id="user-mem5-owner", title="t")
        mem = store.add(s.session_id, "归属行 MEM-5")

        store2 = MemoryStore(persist=_hp(tmp_path))
        sessions2 = SessionManager(persist=_hs(tmp_path))
        m2 = store2.peek(mem.memory_id)
        assert m2 is not None
        assert memory_owner(sessions2, m2) == "user-mem5-owner"


# ── 静态接线（写了≠接线了）────────────────────────────────────────


class TestWiring:
    def test_api_singletons_wired_to_persist(self):
        """api/hippo.py 双单例构造真带 persist=（修前纯内存硬反证面）。"""
        import src.api.hippo as hippo_api

        assert hippo_api.store._persist is not None
        assert hippo_api.store._persist._table == "hippo_memories"
        assert hippo_api.store._persist._resolve is hippo_db_path
        assert hippo_api.sessions._persist is not None
        assert hippo_api.sessions._persist._table == "hippo_sessions"
        assert hippo_api.sessions._persist._resolve is hippo_db_path

    def test_static_source_lock(self):
        """源码静态锁死：store/sessions 赋值调用带 persist 关键字。"""
        src = Path("src/api/hippo.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        wired = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                t = node.targets[0]
                if isinstance(t, ast.Name) and t.id in ("store", "sessions"):
                    call = node.value
                    assert isinstance(call, ast.Call), f"{t.id} 不是构造调用"
                    wired[t.id] = {kw.arg for kw in call.keywords}
        assert wired.get("store") == {"strategy", "half_life_hours", "persist"}
        # MEM-⑥ 契约随更新：sessions 构造面新增 count_source（计数对账真源），
        # persist 接线锁死意图零丢失，顺带锁死 count_source 防回退裸实例
        assert wired.get("sessions") == {"persist", "count_source"}
