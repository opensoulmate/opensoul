"""WakeupScheduler.overview() + due_wakeups窗口化查询 测试。

背景（多轮dev-reports遗留"due_wakeups全表扫描取每key最新，事件量大后需索引/窗口
优化"销账 + monitoring面板数据源新增）：
- overview()：全部 (agent_id, session_id) 唤醒循环一屏聚合，语义=各键 status() 同源；
- due_wakeups() 改窗口化查询（每key只取MAX(id)行）——**行为必须与旧全表版完全一致**，
  本文件的"旧事件被新事件顶掉"用例即该查询重写的回归护栏。

单元测试无网络依赖：SQLite 用 tmp_path 隔离；live 测试（@pytest.mark.live）经
conftest client → localhost:8090 验证 /wakeup/overview API 接线。
"""

import time

import pytest

from src.will.wakeup import MAX_FIRE_ATTEMPTS, WakeupScheduler


@pytest.fixture
def sched(tmp_path):
    return WakeupScheduler(db_path=str(tmp_path / "wakeup.db"))


# ── ① overview：全键聚合 + 语义与 status() 同源 ──────────────────


class TestOverview:
    def test_overview_empty(self, sched):
        ov = sched.overview()
        assert ov["total_keys"] == 0 and ov["loops"] == []
        assert ov["due_count"] == 0 and ov["active_count"] == 0

    def test_overview_lists_all_keys(self, sched):
        sched.record(reason="a1", noop=True, agent_id="a", session_id="s1")
        sched.record(reason="a2", noop=True, agent_id="a", session_id="s1")
        sched.record(reason="b1", noop=False, agent_id="b", session_id="s2")
        sched.record(reason="bye", noop=False, agent_id="b", session_id="s2", stop=True)
        ov = sched.overview()
        assert ov["total_keys"] == 2
        by_key = {(s["agent_id"], s["session_id"]): s for s in ov["loops"]}
        sa = by_key[("a", "s1")]
        sb = by_key[("b", "s2")]
        assert sa["active"] is True and sa["current_streak"] == 2 and sa["total_events"] == 2
        assert sb["active"] is False and sb["current_streak"] == 0 and sb["total_events"] == 2
        assert ov["active_count"] == 1

    def test_overview_loop_semantics_identical_to_status(self, sched):
        """overview 的每个键必须与 status() 同语义（判定逻辑单一真源）。"""
        sched.record(reason="n1", noop=True, agent_id="a", session_id="s")
        sched.record(reason="n2", noop=True, agent_id="a", session_id="s")
        ov = sched.overview()
        loop = ov["loops"][0]
        st = sched.status(agent_id="a", session_id="s")
        for field in ("active", "current_streak", "next_wakeup_at", "total_events", "noop_events"):
            assert loop[field] == st[field], field
        assert loop["fire"] == st["fire"]

    def test_overview_due_count_agrees_with_due_wakeups(self, sched):
        sched.record(reason="old", noop=False, delay_seconds=60, agent_id="a")
        sched.record(reason="far", noop=False, delay_seconds=3600, agent_id="b")
        assert sched.overview()["due_count"] == 0
        # 把 a 键的 next_wakeup_at 改到过去 = 到点
        with sched._lock, sched._conn() as conn:
            conn.execute(
                "UPDATE wakeup_events SET next_wakeup_at=? WHERE agent_id='a'",
                (time.time() - 1,),
            )
        ov = sched.overview()
        due = sched.due_wakeups()
        assert ov["due_count"] == len(due) == 1
        loop_a = next(s for s in ov["loops"] if s["agent_id"] == "a")
        assert loop_a["fire"]["due_now"] is True

    def test_overview_sorted_upcoming_first(self, sched):
        sched.record(reason="far", noop=False, delay_seconds=3600, agent_id="far")
        sched.record(reason="soon", noop=False, delay_seconds=60, agent_id="soon")
        sched.record(reason="done", noop=False, agent_id="gone", stop=True)
        keys = [(s["agent_id"], s["active"]) for s in sched.overview()["loops"]]
        # 活跃且最早到点在前；stopped（next_wakeup_at=None）沉底
        assert keys == [("soon", True), ("far", True), ("gone", False)]


# ── ② due_wakeups 窗口化查询回归护栏（行为必须与旧全表版一致） ──


class TestDueWakeupsWindowed:
    def test_old_due_event_superseded_by_newer_not_returned(self, sched):
        """旧事件"到点"但被同key更新事件顶掉（stop）→ 不得复活成due。

        若窗口化查询写错（取错行/漏GROUP BY），旧的到点行会被错误返回。"""
        sched.record(reason="old due", noop=False, delay_seconds=60, agent_id="a")
        with sched._lock, sched._conn() as conn:
            conn.execute(
                "UPDATE wakeup_events SET next_wakeup_at=? WHERE agent_id='a'",
                (time.time() - 10,),
            )
        assert len(sched.due_wakeups()) == 1  # 旧事件此刻due
        sched.record(reason="done", noop=False, agent_id="a", stop=True)  # 新事件顶掉
        assert sched.due_wakeups() == []

    def test_latest_row_per_key_is_the_judged_one(self, sched):
        for i in range(5):
            sched.record(reason=f"r{i}", noop=True, delay_seconds=60, agent_id="a", session_id="s")
        for i in range(3):
            sched.record(reason=f"r{i}", noop=True, delay_seconds=60, agent_id="b", session_id="s")
        # 只把 a 键最新一行改成到点——窗口化查询必须精确判到那一行
        with sched._lock, sched._conn() as conn:
            conn.execute(
                "UPDATE wakeup_events SET next_wakeup_at=? "
                "WHERE agent_id='a' AND id=(SELECT MAX(id) FROM wakeup_events WHERE agent_id='a')",
                (time.time() - 1,),
            )
        due = sched.due_wakeups()
        assert [e["agent_id"] for e in due] == ["a"]
        assert len(due) == 1  # 每key最多一行（行数=键数，非事件数）

    def test_many_events_per_key_still_one_judgment_each(self, sched):
        """量大场景（遗留原文："事件量大后需索引/窗口优化"）：50事件×3键，
        due判定仍每key一次，且语义只看最新。"""
        for key in ("a", "b", "c"):
            for i in range(50):
                sched.record(
                    reason=f"{key}{i}",
                    noop=(i % 2 == 0),
                    delay_seconds=60,
                    agent_id=key,
                    session_id="s",
                )
        with sched._lock, sched._conn() as conn:
            conn.execute(
                "UPDATE wakeup_events SET next_wakeup_at=? WHERE agent_id IN ('a','b')",
                (time.time() - 1,),
            )
        due = sched.due_wakeups()
        # 键的最新事件若noop=False且未stop仍active——逐key至多1条
        assert len(due) == len({(e["agent_id"], e["session_id"]) for e in due})
        assert {e["agent_id"] for e in due} <= {"a", "b"}
        ov = sched.overview()
        assert ov["total_keys"] == 3


# ── ③ overview 批量查询（N+1销账：旧1+N×status()=5N+1查询 → 固定2条） ──


class _CountingConn:
    """sqlite3.Connection代理：统计execute次数（overview查询数断言用）。"""

    def __init__(self, conn):
        self._conn = conn
        self.executes = 0

    def execute(self, *args, **kwargs):
        self.executes += 1
        return self._conn.execute(*args, **kwargs)

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _overview_executes(sched) -> int:
    """跑一次 overview() 返回期间的 SQL execute 总次数（连接代理计数）。"""
    boxes: list = []
    real_conn = WakeupScheduler._conn

    def factory():
        c = _CountingConn(real_conn(sched))
        boxes.append(c)
        return c

    sched._conn = factory  # 实例属性遮蔽类方法，仅本helper作用域内生效
    try:
        ov = sched.overview()
        assert ov["total_keys"] >= 1
    finally:
        del sched._conn  # 还原类方法查找
    return sum(b.executes for b in boxes)


class TestOverviewBatched:
    def test_overview_query_count_constant_in_keys(self, tmp_path):
        """N+1销账接线钉：overview查询数必须与键数无关（旧=5N+1，50键≈251条）。"""
        small = WakeupScheduler(db_path=str(tmp_path / "small.db"))
        big = WakeupScheduler(db_path=str(tmp_path / "big.db"))
        for i in range(5):
            small.record(reason=f"r{i}", noop=True, delay_seconds=60, agent_id=f"k{i}")
        for i in range(50):
            big.record(reason=f"r{i}", noop=True, delay_seconds=60, agent_id=f"k{i}")
        n_small = _overview_executes(small)
        n_big = _overview_executes(big)
        assert n_small == n_big, f"查询数随键数增长：{n_small}(5键) vs {n_big}(50键)"
        assert n_big <= 4, f"overview查询数应固定≈2，实测{n_big}"
        assert big.overview()["total_keys"] == 50

    def test_overview_does_not_call_status_per_key(self, sched):
        """接线钉：overview()走批量路径，不得回退成逐key调status()（N+1根源）。"""
        sched.record(reason="n1", noop=True, delay_seconds=60, agent_id="a", session_id="s")

        def _boom(*args, **kwargs):
            raise AssertionError("overview()不得逐key调status()（N+1根源）")

        sched.status = _boom  # 实例属性遮蔽：谁调谁炸
        ov = sched.overview()
        assert ov["total_keys"] == 1 and ov["loops"][0]["agent_id"] == "a"

    def test_overview_fire_counts_match_status_on_fire_states(self, sched):
        """批量聚合计数在 fired/exhausted 执行侧状态下与 status() 逐字同义。"""
        # 键 a：成功 fired（claim + ack ok）
        ev = sched.record(reason="fire me", noop=False, delay_seconds=60, agent_id="a")
        assert sched.claim_fire(ev["event_id"]) is not None
        sched.ack_fire(ev["event_id"], ok=True)
        # 键 b：重试用尽 EXHAUSTED（claim + ack失败 × MAX_FIRE_ATTEMPTS）
        ev2 = sched.record(reason="retry me", noop=False, delay_seconds=60, agent_id="b")
        for _ in range(MAX_FIRE_ATTEMPTS):
            assert sched.claim_fire(ev2["event_id"]) is not None
            sched.ack_fire(ev2["event_id"], ok=False, error="boom")
        # 键 c：纯noop空转
        sched.record(reason="n1", noop=True, delay_seconds=60, agent_id="c")
        sched.record(reason="n2", noop=True, delay_seconds=60, agent_id="c")

        loops = {(s["agent_id"], s["session_id"]): s for s in sched.overview()["loops"]}
        for key in (("a", ""), ("b", ""), ("c", "")):
            st = sched.status(agent_id=key[0], session_id=key[1])
            lp = loops[key]
            for field in (
                "active",
                "current_streak",
                "next_wakeup_at",
                "total_events",
                "noop_events",
            ):
                assert lp[field] == st[field], f"{key} {field}"
            assert lp["fire"] == st["fire"], key
        assert loops[("a", "")]["fire"]["fired"] == 1
        assert loops[("b", "")]["fire"]["exhausted"] == 1
        assert loops[("c", "")]["noop_events"] == 2
        assert sched.overview()["due_count"] == 0  # 三键next均未到点


# ── ④ live API（:8090 真实服务，重启后运行） ─────────────────────


@pytest.mark.live
class TestWakeupOverviewLive:
    @pytest.fixture(autouse=True)
    def _purge_test_rows(self, client):
        """live测试收编约定：产生侧声明source='test'，跑完自清生产wakeup.db。"""
        yield
        r = client.post("/api/will/wakeup/purge", params={"source": "test"})
        assert r.status_code == 200, r.text

    def test_overview_endpoint_returns_aggregate(self, client):
        sid = f"live-wakeup-overview-{int(time.time())}"
        client.post(
            "/api/will/wakeup/schedule",
            json={
                "agent_id": "live-ov",
                "session_id": sid,
                "reason": "overview探针",
                "noop": True,
                "source": "test",
            },
        )
        # include_test=true：测试行显式可见面——验证loop payload语义
        resp = client.get("/api/will/wakeup/overview", params={"include_test": "true"})
        assert resp.status_code == 200
        data = resp.json()
        assert "loops" in data and "total_keys" in data and "due_count" in data
        assert data["total_keys"] >= 1
        mine = [s for s in data["loops"] if s["agent_id"] == "live-ov" and s["session_id"] == sid]
        assert len(mine) == 1
        assert mine[0]["active"] is True and mine[0]["current_streak"] == 1
        assert mine[0]["countdown_s"] is not None

        # 默认面（monitoring面板）排除test循环——测试产物不进"我都不知道他们在干嘛"面板
        resp2 = client.get("/api/will/wakeup/overview")
        assert resp2.status_code == 200
        mine2 = [
            s
            for s in resp2.json()["loops"]
            if s["agent_id"] == "live-ov" and s["session_id"] == sid
        ]
        assert mine2 == []
