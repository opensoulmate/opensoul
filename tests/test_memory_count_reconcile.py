"""memory_count 计数对账真源化（MEM-⑥）测试。

MEM-③ 诚实边界③④ / MEM-⑤ 诚实边界② 留档：decay 遗忘/容量驱逐/hippo 删除口
不回退 sessions.memory_count——既有记账漂移方向=多报。真源=记忆行本体：
memory_count 只是行数的缓存视图，值读面（get/list_sessions/get_stats）对账
归真，刻意不在每个删除路径逐点补 -1（缓存与真源双记账散落全仓=漂移复发面）。

语义锁死：
- 真源计数=当前行数含 archived 行（归档只改行状态不删行，与写入 +1 记账语义一致）
- count_source 缺省 None=纯内存记账语义字节零变化（既有契约）
- peek 零副作用不对账（归属闸「被拒读取行零改写」契约）
- 真源取数故障不阻断读面（回退增量缓存+日志可见，禁静默）

数据面=tmp 全隔离（tmp SQLite persist/纯内存实例），绝不写生产库。
"""

import ast
import logging
import time
from pathlib import Path

import pytest

from src.hippo.decay import DecayStrategy
from src.hippo.memory_persist import SessionPersistence
from src.hippo.memory_store import MemoryStore
from src.hippo.session import SessionManager

REPO = Path(__file__).resolve().parents[1]


def _wired(store: MemoryStore, **kw) -> SessionManager:
    """与 brain/hippo 面同款接线：count_source=零参取数口真源对账。"""
    try:
        return SessionManager(count_source=store.counts_by_session, **kw)
    except (TypeError, AttributeError):
        # 容缺 shim（仅控制组旧代码路径）：旧代码无 counts_by_session/count_source，
        # 让失败落在行为断言上而非 fixture 结构面
        return SessionManager(**kw)


def _drift_setup(store: MemoryStore, sm: SessionManager, sid: str, n: int = 2):
    """造 n 行真实记忆+计数对称记账（写入 +1），返回行 id 列表。"""
    ids = []
    for i in range(n):
        mem = store.add(session_id=sid, content=f"记忆{i}")
        sm.increment_memory_count(sid)
        ids.append(mem.memory_id)
    return ids


def _get_count(sm: SessionManager, sid: str) -> int:
    s = sm.get(sid)
    assert s is not None
    return s.memory_count


def _peek_count(sm: SessionManager, sid: str) -> int:
    s = sm.peek(sid)
    assert s is not None
    return s.memory_count


def _list_count(sm: SessionManager) -> int:
    rows = sm.list_sessions()
    assert len(rows) == 1
    return rows[0].memory_count


def _stats_tracked(sm: SessionManager) -> int:
    return sm.get_stats()["total_memories_tracked"]


# ── 真源单一口（MemoryStore.counts_by_session）────────────────────────


class TestCountsBySession:
    def test_counts_rows_including_archived(self):
        """真源语义：行数含 archived 行（归档不删行）。"""
        store = MemoryStore()
        m1 = store.add(session_id="s1", content="活")
        m2 = store.add(session_id="s1", content="归档")
        store.update(m2.memory_id, content=m2.content)  # 回读同形
        m2.archived = True  # 归档=行状态变更不删行（衰减归档同语义）
        counts = store.counts_by_session()
        assert counts == {"s1": 2}
        assert m1.memory_id  # 行真存在

    def test_orphan_rows_counted_in_dict(self):
        """孤儿行（无父会话行）照常归集；消费方无会话行即无处可挂。"""
        store = MemoryStore()
        store.add(session_id="ghost", content="孤儿")
        store.add(session_id="ghost", content="孤儿2")
        assert store.counts_by_session() == {"ghost": 2}

    def test_empty_store(self):
        assert MemoryStore().counts_by_session() == {}


# ── 三处既有漂移方向逐个收口（行为断言，控制组必炸）──────────────────


class TestDriftReconcile:
    def test_decay_forget_read_face_reconciles(self):
        """decay 遗忘删行后读面归真（修前读面恒多报）。"""
        store = MemoryStore(strategy=DecayStrategy.EXPONENTIAL, half_life_hours=24.0)
        sm = _wired(store)
        s = sm.create(user_id="u", session_id="s-drift")
        ids = _drift_setup(store, sm, s.session_id, 2)
        assert _peek_count(sm, s.session_id) == 2

        # 把一行催老到遗忘线以下（retention ~0 ≤ forget_threshold）
        old = store.peek(ids[0])
        assert old is not None
        old.created_at = time.time() - 1_000_000
        old.last_accessed_at = old.created_at
        stats = store.run_decay_cycle()
        assert stats["forgotten"] == 1  # 真删行=真源 1 行

        # 值读面对账归真：get / list_sessions / get_stats 三口一致
        assert _get_count(sm, s.session_id) == 1
        assert _list_count(sm) == 1
        assert _stats_tracked(sm) == 1

    def test_capacity_evict_read_face_reconciles(self):
        """容量驱逐删行后读面归真。"""
        store = MemoryStore(max_entries=1)
        sm = _wired(store)
        s = sm.create(user_id="u", session_id="s-evict")
        _drift_setup(store, sm, s.session_id, 2)  # 第二行写入驱逐第一行
        assert len(store.counts_by_session()) == 1  # 真源 1 行
        assert _get_count(sm, s.session_id) == 1  # 修前记账=2

    def test_direct_delete_read_face_reconciles(self):
        """删除口/直删（hippo 删除口同根）删行后读面归真。"""
        store = MemoryStore()
        sm = _wired(store)
        s = sm.create(user_id="u", session_id="s-del")
        ids = _drift_setup(store, sm, s.session_id, 2)
        assert store.delete(ids[0]) is True  # 无 -1 记账=漂移点
        assert _get_count(sm, s.session_id) == 1
        assert _list_count(sm) == 1

    def test_zero_row_session_reads_zero(self):
        """全删后读面=0（修前残留幽灵计数）。"""
        store = MemoryStore()
        sm = _wired(store)
        s = sm.create(user_id="u", session_id="s-zero")
        ids = _drift_setup(store, sm, s.session_id, 2)
        for mid in ids:
            store.delete(mid)
        assert _get_count(sm, s.session_id) == 0
        assert _stats_tracked(sm) == 0


# ── 不变面（既有契约锁死）───────────────────────────────────────────


class TestContractsUnchanged:
    def test_write_plus_one_immediate(self):
        """写入 +1 记账语义零变化（纯内存路径照旧即时可见）。"""
        store = MemoryStore()
        sm = _wired(store)
        s = sm.create(user_id="u", session_id="s-plus")
        _drift_setup(store, sm, s.session_id, 2)
        assert _peek_count(sm, s.session_id) == 2
        assert _get_count(sm, s.session_id) == 2

    def test_archived_rows_keep_count(self):
        """归档不删行→计数不变（与写入 +1 记账语义一致）。"""
        store = MemoryStore()
        sm = _wired(store)
        s = sm.create(user_id="u", session_id="s-arch")
        mem = store.add(session_id=s.session_id, content="归档候选")
        sm.increment_memory_count(s.session_id)
        mem.archived = True
        assert _get_count(sm, s.session_id) == 1

    def test_no_count_source_zero_change(self):
        """count_source 缺省 None=纯内存记账语义字节零变化（既有契约锁死）：
        不对账，增量缓存原样返回（含既有漂移面）。"""
        store = MemoryStore()
        sm = SessionManager()
        s = sm.create(user_id="u", session_id="s-legacy")
        ids = _drift_setup(store, sm, s.session_id, 2)
        store.delete(ids[0])  # 漂移 2 vs 真源 1
        assert _get_count(sm, s.session_id) == 2  # 不对账=修前语义
        assert _list_count(sm) == 2
        assert _stats_tracked(sm) == 2

    def test_peek_zero_side_effect_no_reconcile(self):
        """peek 零副作用契约延续：不对账不改行（归属闸「被拒读取行零改写」）；
        值读面 get 才对账。"""
        store = MemoryStore()
        sm = _wired(store)
        s = sm.create(user_id="u", session_id="s-peek")
        ids = _drift_setup(store, sm, s.session_id, 2)
        store.delete(ids[0])  # 漂移 2 vs 真源 1
        assert _peek_count(sm, s.session_id) == 2  # 闸读不修数不改行
        assert _get_count(sm, s.session_id) == 1  # 值读面对账归真

    def test_orphan_rows_do_not_create_sessions(self):
        """真源孤儿行不产生会话（「孤儿记忆无计数可退」同边界）。"""
        store = MemoryStore()
        sm = _wired(store)
        s = sm.create(user_id="u", session_id="s-own")
        _drift_setup(store, sm, s.session_id, 1)
        store.add(session_id="ghost", content="孤儿")
        store.add(session_id="ghost", content="孤儿2")
        assert _get_count(sm, s.session_id) == 1
        assert sm.get("ghost") is None
        assert sm.get_stats()["total_sessions"] == 1

    def test_increment_missing_session_noop_unchanged(self):
        """缺席会话增量如实 no-op（既有语义）。"""
        sm = SessionManager()
        sm.increment_memory_count("no-such")
        assert sm.get("no-such") is None
        assert sm.get_stats()["total_sessions"] == 0


# ── 对账修正落盘 + 故障可见 ─────────────────────────────────────────


class TestReconcilePersistenceAndFailure:
    def test_reconcile_persists_corrected_row(self):
        """对账修正随写穿落盘：新实例回载取回修正值（禁库行留旧漂移值）。"""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "m.db")
            store = MemoryStore()
            sm = _wired(store, persist=SessionPersistence(db_path=db))
            s = sm.create(user_id="u", session_id="s-persist")
            ids = _drift_setup(store, sm, s.session_id, 2)
            store.delete(ids[0])  # 漂移 2 vs 真源 1
            assert _get_count(sm, s.session_id) == 1  # 对账+写穿

            fresh = SessionManager(persist=SessionPersistence(db_path=db))
            assert _get_count(fresh, s.session_id) == 1  # 库行=修正值

    def test_count_source_failure_fallback_visible(self, caplog):
        """真源取数故障不阻断读面（回退增量缓存），日志可见禁静默。"""

        def broken():
            raise RuntimeError("真源炸了")

        try:
            sm = SessionManager(count_source=broken)
        except TypeError:
            pytest.skip("容缺 shim：旧代码无 count_source（控制组结构面）")
        s = sm.create(user_id="u", session_id="s-fail")
        sm.increment_memory_count(s.session_id, 3)
        with caplog.at_level(logging.WARNING):
            got = sm.get(s.session_id)
        assert got is not None
        assert got.memory_count == 3  # 回退增量缓存，读不炸
        assert "真源取数失败" in caplog.text

    def test_reconcile_memory_counts_primitive(self):
        """对账原语：修正数返回值/缺席会话置 0/幂等二次对账 0 修正。"""
        store = MemoryStore()
        sm = _wired(store)
        s = sm.create(user_id="u", session_id="s-prim")
        _drift_setup(store, sm, s.session_id, 3)
        assert sm.reconcile_memory_counts({"s-prim": 1}) == 1
        assert sm.peek(s.session_id).memory_count == 1
        assert sm.reconcile_memory_counts({"s-prim": 1}) == 0  # 幂等
        assert sm.reconcile_memory_counts({}) == 1  # 真源 0 行→置 0
        assert sm.peek(s.session_id).memory_count == 0


# ── API 全链（hippo 面：真实 router → handler → 单例）────────────────


@pytest.fixture()
def env(will_client, monkeypatch):
    """hippo 路由进程内测试面：store/sessions 换全新实例且按面同款接线
    count_source（真源对账随面生效），逐用例隔离防跨用例状态泄漏。"""
    import src.api.hippo as hippo_api

    monkeypatch.setattr(hippo_api, "store", MemoryStore())
    try:
        wired = SessionManager(count_source=lambda: hippo_api.store.counts_by_session())
    except (TypeError, AttributeError):
        wired = SessionManager()  # 容缺 shim（仅控制组旧代码路径）
    monkeypatch.setattr(hippo_api, "sessions", wired)
    return will_client, hippo_api


class TestAPIReadFace:
    def _create_session(self, client, uid="recon_user"):
        resp = client.post("/api/hippo/sessions", json={"user_id": uid})
        assert resp.status_code == 200
        return resp.json()["session_id"]

    def _add_memory(self, client, sid, uid="recon_user", content="记忆"):
        resp = client.post(
            "/api/hippo/memories",
            params={"user_id": uid},
            json={"content": content, "session_id": sid},
        )
        assert resp.status_code == 200
        return resp.json()["memory_id"]

    def test_hippo_delete_port_memory_count_reconciles(self, env):
        """hippo 删除口（不回退计数的既有漂移点）删除后读面归真：
        GET /sessions/{id} 与 /sessions 列表一致=真实行数（修前恒多报）。"""
        client, _ = env
        sid = self._create_session(client)
        mid1 = self._add_memory(client, sid, content="第一条")
        self._add_memory(client, sid, content="第二条")

        detail = client.get(f"/api/hippo/sessions/{sid}", params={"user_id": "recon_user"})
        assert detail.status_code == 200
        assert detail.json()["memory_count"] == 2

        assert (
            client.delete(
                f"/api/hippo/memories/{mid1}", params={"user_id": "recon_user"}
            ).status_code
            == 200
        )

        detail = client.get(f"/api/hippo/sessions/{sid}", params={"user_id": "recon_user"})
        assert detail.json()["memory_count"] == 1  # 修前 2（删除口不回退）
        listing = client.get("/api/hippo/sessions", params={"user_id": "recon_user"})
        row = [s for s in listing.json()["sessions"] if s["session_id"] == sid][0]
        assert row["memory_count"] == 1

    def test_hippo_decay_run_read_face_reconciles(self, env):
        """衰减遗忘（/decay/run 真删行）后读面归真。"""
        client, hippo_api = env
        sid = self._create_session(client)
        mid = self._add_memory(client, sid, content="待遗忘")
        mem = hippo_api.store.peek(mid)
        mem.created_at = time.time() - 1_000_000
        mem.last_accessed_at = mem.created_at

        resp = client.post("/api/hippo/decay/run")
        assert resp.status_code == 200
        assert resp.json()["forgotten"] == 1

        detail = client.get(f"/api/hippo/sessions/{sid}", params={"user_id": "recon_user"})
        assert detail.json()["memory_count"] == 0  # 修前 1

    def test_health_stats_reconciled(self, env):
        """/health sessions.get_stats total_memories_tracked 归真。"""
        client, hippo_api = env
        sid = self._create_session(client)
        mid = self._add_memory(client, sid, content="被直删")
        assert hippo_api.store.delete(mid) is True
        health = client.get("/api/hippo/health")
        assert health.status_code == 200
        assert health.json()["sessions"]["total_memories_tracked"] == 0


# ── 面接线锁死（brain/hippo 双面 count_source 真接线）────────────────


class TestModuleWiring:
    def test_runtime_wiring_both_faces(self):
        import src.api.brain as brain_api
        import src.api.hippo as hippo_api

        for api in (brain_api, hippo_api):
            assert api.sessions._count_source is not None, "count_source 未接线"
            counts = api.sessions._count_source()
            assert isinstance(counts, dict)

    def test_ast_locks_count_source_keyword(self):
        """AST 静态锁：双面 SessionManager 构造带 count_source 且真取
        store.counts_by_session（防回退成裸实例=对账静默失效）。"""
        for rel in ("src/api/brain.py", "src/api/hippo.py"):
            tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
            calls = [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "SessionManager"
            ]
            assert calls, f"{rel} 无 SessionManager 构造"
            kw = {k.arg: k.value for k in calls[0].keywords}
            assert "count_source" in kw, f"{rel} SessionManager 未接 count_source"
            assert "counts_by_session" in ast.unparse(kw["count_source"])
