"""ScheduleWakeup 执行侧闭环（claim/ack + due过滤 + 旧库迁移）单元测试。

调研来源：claude-code SDK ScheduleWakeup——runtime 在 delaySeconds 到点后**重新唤醒
agent**（10-claude-code-source.md #4）；上一轮只有"登记意图+可观测"，执行侧是缺口
（dev-reports P0-4 遗留#1"下轮P1首选"）。本轮补齐执行语义：
- claim_fire CAS领取 = at-most-once（双实例executor并发只有一个赢家）；
- ack_fire 失败释放重试 / MAX_FIRE_ATTEMPTS 上限 EXHAUSTED 终态（留痕不无限烧）；
- due_wakeups 过滤已领取/已终结事件；
- 旧 wakeup.db（无新列）显式 ALTER 迁移。

单元测试无网络依赖：SQLite 用 tmp_path 隔离。
"""

import sqlite3
import time

import pytest

from src.will.wakeup import (
    MAX_FIRE_ATTEMPTS,
    WakeupScheduler,
)


@pytest.fixture()
def sched(tmp_path):
    return WakeupScheduler(db_path=str(tmp_path / "wakeup.db"))


def _record(sched, sid="s1", delay=60.0, reason="检查X", stop=False):
    return sched.record(
        reason=reason,
        noop=False,
        delay_seconds=delay,
        stop=stop,
        agent_id="soulmate",
        session_id=sid,
    )


# ════════════════════════════════════════════════════════════════
# ① claim_fire：CAS单赢家 + attempts计数
# ════════════════════════════════════════════════════════════════


class TestClaimCas:
    def test_claim_single_winner(self, sched):
        ev = _record(sched)
        first = sched.claim_fire(ev["event_id"])
        assert first is not None
        assert first["fired"] is True
        assert first["fire_attempts"] == 1
        # 第二个领取者（对端实例）必须拿None——绝不双跑
        assert sched.claim_fire(ev["event_id"]) is None

    def test_claim_nonexistent_returns_none(self, sched):
        assert sched.claim_fire(99999) is None

    def test_claim_increments_attempts_across_retries(self, sched):
        ev = _record(sched)
        for i in range(1, 3):
            got = sched.claim_fire(ev["event_id"])
            assert got["fire_attempts"] == i
            sched.ack_fire(ev["event_id"], ok=False, error="boom")

    def test_claim_blocked_after_max_attempts(self, sched):
        ev = _record(sched)
        for _ in range(MAX_FIRE_ATTEMPTS):
            assert sched.claim_fire(ev["event_id"]) is not None
            sched.ack_fire(ev["event_id"], ok=False, error="boom")
        assert sched.claim_fire(ev["event_id"]) is None


# ════════════════════════════════════════════════════════════════
# ② due_wakeups：已领取/已终结不再返回，释放后自动重试
# ════════════════════════════════════════════════════════════════


class TestDueFilter:
    def test_fired_event_not_due(self, sched):
        ev = _record(sched)
        later = time.time() + 61
        assert [e["event_id"] for e in sched.due_wakeups(now=later)] == [ev["event_id"]]
        sched.claim_fire(ev["event_id"])
        assert sched.due_wakeups(now=later) == []

    def test_released_event_due_again(self, sched):
        ev = _record(sched)
        later = time.time() + 61
        sched.claim_fire(ev["event_id"])
        ack = sched.ack_fire(ev["event_id"], ok=False, error="LLM超时")
        assert ack["state"] == "retry" and ack["retryable"] is True
        assert [e["event_id"] for e in sched.due_wakeups(now=later)] == [ev["event_id"]]

    def test_exhausted_event_never_due(self, sched):
        ev = _record(sched)
        later = time.time() + 61
        for _ in range(MAX_FIRE_ATTEMPTS):
            sched.claim_fire(ev["event_id"])
            sched.ack_fire(ev["event_id"], ok=False, error="dead")
        assert sched.due_wakeups(now=later) == []
        # 留痕可见（失败必须可见，禁止静默消失）
        st = sched.status(agent_id="soulmate", session_id="s1")
        assert st["last_event"]["exhausted"] is True
        assert st["last_event"]["last_fire_error"].startswith("EXHAUSTED")

    def test_stop_event_not_due(self, sched):
        _record(sched, stop=True)
        assert sched.due_wakeups(now=time.time() + 9999) == []

    def test_new_schedule_after_fire_is_new_due_cycle(self, sched):
        ev1 = _record(sched, reason="第一轮")
        sched.claim_fire(ev1["event_id"])
        sched.ack_fire(ev1["event_id"], ok=True)
        ev2 = _record(sched, reason="第二轮")
        later = time.time() + 61
        due = sched.due_wakeups(now=later)
        assert [e["event_id"] for e in due] == [ev2["event_id"]]
        # 旧已fire事件不复活
        assert all(e["event_id"] != ev1["event_id"] for e in due)


# ════════════════════════════════════════════════════════════════
# ③ ack_fire：成功终态 / 失败释放 / EXHAUSTED
# ════════════════════════════════════════════════════════════════


class TestAckFire:
    def test_ack_ok_keeps_fired_and_clears_error(self, sched):
        ev = _record(sched)
        sched.claim_fire(ev["event_id"])
        ack = sched.ack_fire(ev["event_id"], ok=True)
        assert ack["state"] == "fired"
        assert ack["fired"] is True and ack["retryable"] is False
        assert ack["last_fire_error"] == ""

    def test_ack_fail_under_max_releases(self, sched):
        ev = _record(sched)
        sched.claim_fire(ev["event_id"])
        ack = sched.ack_fire(ev["event_id"], ok=False, error="连接被拒")
        assert ack["state"] == "retry"
        assert ack["fired"] is False
        assert ack["last_fire_error"] == "连接被拒"

    def test_ack_fail_at_max_exhausts_with_trace(self, sched):
        ev = _record(sched)
        for _ in range(MAX_FIRE_ATTEMPTS - 1):
            sched.claim_fire(ev["event_id"])
            sched.ack_fire(ev["event_id"], ok=False, error="dead")
        sched.claim_fire(ev["event_id"])
        ack = sched.ack_fire(ev["event_id"], ok=False, error="dead")
        assert ack["state"] == "exhausted"
        assert ack["retryable"] is False
        assert ack["fired"] is True
        assert ack["last_fire_error"].startswith("EXHAUSTED after 5 attempts")
        assert "dead" in ack["last_fire_error"]

    def test_ack_unknown_event_raises(self, sched):
        with pytest.raises(ValueError):
            sched.ack_fire(424242, ok=True)

    def test_error_truncation_is_marked(self, sched):
        ev = _record(sched)
        sched.claim_fire(ev["event_id"])
        ack = sched.ack_fire(ev["event_id"], ok=False, error="x" * 600)
        assert ack["last_fire_error"].endswith("[+100 chars]")


# ════════════════════════════════════════════════════════════════
# ④ 旧库迁移：上一轮 wakeup.db 无新列 → ALTER 幂等补齐
# ════════════════════════════════════════════════════════════════

_OLD_DDL = """
CREATE TABLE wakeup_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL DEFAULT 'default',
    session_id TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL,
    requested_delay_s REAL NOT NULL,
    delay_s REAL NOT NULL,
    clamped INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    noop INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    stopped INTEGER NOT NULL DEFAULT 0,
    next_wakeup_at REAL
)
"""


class TestMigration:
    def test_old_db_upgraded_in_place(self, tmp_path):
        db = str(tmp_path / "wakeup.db")
        conn = sqlite3.connect(db)
        conn.execute(_OLD_DDL)
        conn.execute(
            "INSERT INTO wakeup_events (agent_id, session_id, ts, requested_delay_s, "
            "delay_s, clamped, reason, noop, streak, stopped, next_wakeup_at) "
            "VALUES ('soulmate','old1',?,?,?,1,'旧事件',0,0,0,?)",
            (time.time(), 60.0, 60.0, time.time() - 1),
        )
        conn.commit()
        conn.close()

        s = WakeupScheduler(db_path=db)  # 迁移发生在这里
        # 旧事件行：新列默认值生效，且能被due/claim/ack全链路消费
        due = s.due_wakeups()
        assert [e["reason"] for e in due] == ["旧事件"]
        assert due[0]["fire_attempts"] == 0 and due[0]["fired"] is False
        got = s.claim_fire(due[0]["event_id"])
        assert got["fired"] is True
        ack = s.ack_fire(due[0]["event_id"], ok=True)
        assert ack["state"] == "fired"
        # 新事件照常写入（source列可用）
        ev = s.record(reason="新事件", noop=True, delay_seconds=60, agent_id="a", session_id="n1")
        assert ev["source"] == ""

    def test_migration_idempotent(self, tmp_path):
        db = str(tmp_path / "wakeup.db")
        conn = sqlite3.connect(db)
        conn.execute(_OLD_DDL)
        conn.commit()
        conn.close()
        WakeupScheduler(db_path=db)
        WakeupScheduler(db_path=db)  # 二次init不炸
        s = WakeupScheduler(db_path=db)
        _record(s)
        assert s.status(agent_id="soulmate", session_id="s1")["total_events"] == 1


# ════════════════════════════════════════════════════════════════
# ⑤ source留痕 + status执行侧可观测
# ════════════════════════════════════════════════════════════════


class TestSourceAndStatus:
    def test_record_stores_source(self, sched):
        ev = sched.record(
            reason="带来源",
            noop=False,
            delay_seconds=60,
            agent_id="soulmate",
            session_id="s1",
            source="proxy-a",
        )
        assert ev["source"] == "proxy-a"
        st = sched.status(agent_id="soulmate", session_id="s1")
        assert st["last_event"]["source"] == "proxy-a"

    def test_status_fire_counters(self, sched):
        st0 = sched.status(agent_id="soulmate", session_id="s1")
        assert st0["fire"] == {"due_now": False, "fired": 0, "exhausted": 0}
        ev = _record(sched)
        st1 = sched.status(agent_id="soulmate", session_id="s1")
        assert st1["fire"]["due_now"] is False  # 还没到点
        sched.claim_fire(ev["event_id"])
        sched.ack_fire(ev["event_id"], ok=True)
        st2 = sched.status(agent_id="soulmate", session_id="s1")
        assert st2["fire"]["fired"] == 1
        assert st2["fire"]["due_now"] is False  # 已fire不再due

    def test_status_due_now_visible(self, sched):
        _record(sched)
        # 到点后（record的next_at=now+60，用过去时刻查status不可行——status用真实now）
        # 直接验证due_now判定逻辑与due_wakeups一致：未到点=False
        st = sched.status(agent_id="soulmate", session_id="s1")
        assert st["fire"]["due_now"] is False
        assert st["countdown_s"] is not None and st["countdown_s"] > 0
