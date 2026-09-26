"""ScheduleWakeup（noop自报+streak折叠）单元+live测试。

调研来源：claude-code SDK ScheduleWakeup（feature-matrix/10-claude-code-source.md #4）：
- delaySeconds 钳到 [60, 3600]；reason 必填；noop 自报；连续 noop 折叠+streak；stop 结束循环。
- 该功能此前"完全没有"（SUMMARY.md P0-4 可观测性："我都不知道他们在干嘛"教科书答案）。

单元测试无网络依赖：SQLite 用 tmp_path 隔离；live 测试（@pytest.mark.live）经
conftest client → localhost:8090 验证 API 接线。
"""

import time

import pytest

from src.will.wakeup import (
    MAX_WAKEUP_DELAY_S,
    MIN_WAKEUP_DELAY_S,
    WakeupScheduler,
    clamp_delay,
)


@pytest.fixture
def sched(tmp_path):
    return WakeupScheduler(db_path=str(tmp_path / "wakeup.db"))


# ── ① 延迟钳制（claude-code：delaySeconds 被 runtime 钳到 [60,3600]） ──


class TestClampDelay:
    def test_under_min_clamped_to_60(self):
        requested, clamped, was = clamp_delay(5)
        assert requested == 5 and clamped == MIN_WAKEUP_DELAY_S and was is True

    def test_over_max_clamped_to_3600(self):
        requested, clamped, was = clamp_delay(99999)
        assert clamped == MAX_WAKEUP_DELAY_S and was is True

    def test_in_range_not_clamped(self):
        requested, clamped, was = clamp_delay(180)
        assert (requested, clamped, was) == (180, 180, False)

    def test_bad_input_falls_back_to_min(self):
        requested, clamped, was = clamp_delay("not-a-number")
        assert clamped == MIN_WAKEUP_DELAY_S

    def test_record_marks_clamped_explicitly(self, sched):
        ev = sched.record(reason="x", noop=True, delay_seconds=3)
        assert ev["clamped"] is True
        assert ev["requested_delay_s"] == 3 and ev["delay_s"] == 60


# ── ② reason 必填 + 显式截断标记（禁止静默截断） ──


class TestReason:
    def test_empty_reason_rejected(self, sched):
        with pytest.raises(ValueError):
            sched.record(reason="", noop=True)
        with pytest.raises(ValueError):
            sched.record(reason="   \n  ", noop=False)

    def test_long_reason_truncation_is_marked(self, sched):
        ev = sched.record(reason="r" * 600, noop=True)
        assert ev["reason"].endswith("[+100 chars]")
        assert len(ev["reason"]) < 600

    def test_normal_reason_untouched(self, sched):
        ev = sched.record(reason="检查构建队列无新任务", noop=True)
        assert ev["reason"] == "检查构建队列无新任务"


# ── ③ noop streak（连续noop计数 + 有进展即归零） ──


class TestNoopStreak:
    def test_consecutive_noops_increment(self, sched):
        e1 = sched.record(reason="no news", noop=True, agent_id="a")
        e2 = sched.record(reason="still no news", noop=True, agent_id="a")
        e3 = sched.record(reason="nothing yet", noop=True, agent_id="a")
        assert (e1["streak"], e2["streak"], e3["streak"]) == (1, 2, 3)
        assert e3["folded"] is True and e2["folded"] is True
        assert e1["folded"] is False

    def test_progress_resets_streak(self, sched):
        sched.record(reason="n1", noop=True, agent_id="a")
        sched.record(reason="n2", noop=True, agent_id="a")
        e3 = sched.record(reason="部署完成了", noop=False, agent_id="a")
        assert e3["streak"] == 0
        e4 = sched.record(reason="n3", noop=True, agent_id="a")
        assert e4["streak"] == 1

    def test_streak_isolated_per_loop_key(self, sched):
        sched.record(reason="n", noop=True, agent_id="a", session_id="s1")
        e2 = sched.record(reason="n", noop=True, agent_id="a", session_id="s2")
        assert e2["streak"] == 1  # 不同session不共用streak

    def test_stop_breaks_streak(self, sched):
        sched.record(reason="n1", noop=True, agent_id="a")
        e2 = sched.record(reason="n2", noop=True, agent_id="a", stop=True)
        assert e2["streak"] == 0
        e3 = sched.record(reason="n3", noop=True, agent_id="a")
        assert e3["streak"] == 1  # 循环结束后重新计


# ── ④ 调度语义（next_wakeup_at / stop） ──


class TestSchedule:
    def test_next_wakeup_at_in_future(self, sched):
        ev = sched.record(reason="r", noop=False, delay_seconds=300, agent_id="a")
        assert ev["active"] is True
        assert ev["next_wakeup_at"] is not None
        assert ev["next_wakeup_at"] > time.time() + 200

    def test_stop_closes_loop(self, sched):
        sched.record(reason="r", noop=False, agent_id="a")
        ev = sched.record(reason="完成", noop=False, stop=True, agent_id="a")
        assert ev["active"] is False and ev["next_wakeup_at"] is None

    def test_due_wakeups_only_due_and_latest(self, sched):
        sched.record(reason="old", noop=False, delay_seconds=60, agent_id="a")
        # 未到点的不出现在due里
        assert sched.due_wakeups() == []
        # 到点：把next_wakeup_at改到过去（负延迟会被钳到60，故直接改库验证判定逻辑）
        with sched._lock, sched._conn() as conn:
            conn.execute(
                "UPDATE wakeup_events SET next_wakeup_at=? WHERE agent_id='a'",
                (time.time() - 1,),
            )
        due = sched.due_wakeups()
        assert len(due) == 1 and due[0]["agent_id"] == "a"
        # stop后不再due
        sched.record(reason="done", noop=False, stop=True, agent_id="a")
        assert sched.due_wakeups() == []


# ── ⑤ status（"它现在在干嘛"） ──


class TestStatus:
    def test_status_empty(self, sched):
        st = sched.status(agent_id="none")
        assert st["active"] is False and st["total_events"] == 0

    def test_status_counts_and_countdown(self, sched):
        sched.record(reason="n1", noop=True, agent_id="a")
        sched.record(reason="n2", noop=True, agent_id="a")
        st = sched.status(agent_id="a")
        assert st["active"] is True
        assert st["current_streak"] == 2
        assert st["total_events"] == 2 and st["noop_events"] == 2
        assert st["countdown_s"] is not None and st["countdown_s"] > 0

    def test_status_after_stop(self, sched):
        sched.record(reason="r", noop=False, agent_id="a")
        sched.record(reason="bye", noop=False, stop=True, agent_id="a")
        st = sched.status(agent_id="a")
        assert st["active"] is False and st["countdown_s"] is None


# ── ⑥ folded_view（连续noop折叠显示 + streak统计） ──


class TestFoldedView:
    def test_noop_run_folds_to_single_block(self, sched):
        for i in range(4):
            sched.record(reason=f"no news {i}", noop=True, agent_id="a")
        sched.record(reason="有进展了", noop=False, agent_id="a")
        view = sched.folded_view(agent_id="a")
        kinds = [it["kind"] for it in view["items"]]
        # 最新在前：progress事件 + 一个折叠块
        assert kinds == ["wakeup", "noop_streak"]
        block = view["items"][1]
        assert block["count"] == 4
        assert block["streak_max"] == 4
        assert block["first_reason"] == "no news 0"
        assert block["last_reason"] == "no news 3"
        assert block["folded"] is True
        assert len(block["event_ids"]) == 4

    def test_single_noop_not_folded_block(self, sched):
        sched.record(reason="n", noop=True, agent_id="a")
        view = sched.folded_view(agent_id="a")
        assert len(view["items"]) == 1
        assert view["items"][0]["kind"] == "wakeup"
        assert view["current_streak"] == 1

    def test_two_runs_folded_separately(self, sched):
        sched.record(reason="n1", noop=True, agent_id="a")
        sched.record(reason="n2", noop=True, agent_id="a")
        sched.record(reason="progress", noop=False, agent_id="a")
        sched.record(reason="m1", noop=True, agent_id="a")
        sched.record(reason="m2", noop=True, agent_id="a")
        sched.record(reason="m3", noop=True, agent_id="a")
        view = sched.folded_view(agent_id="a")
        counts = sorted(it["count"] for it in view["items"] if it["kind"] == "noop_streak")
        assert counts == [2, 3]
        assert view["current_streak"] == 3

    def test_folded_limit(self, sched):
        for i in range(5):
            sched.record(reason=f"r{i}", noop=False, agent_id="a")
        view = sched.folded_view(agent_id="a", limit=2)
        assert len(view["items"]) == 2
        assert view["total_events"] == 5  # 总数不被limit吞掉


# ── ⑦ 持久化（重启后状态不丢） ──


class TestPersistence:
    def test_state_survives_new_scheduler_instance(self, tmp_path):
        db = str(tmp_path / "wakeup.db")
        s1 = WakeupScheduler(db_path=db)
        s1.record(reason="n1", noop=True, agent_id="a")
        s1.record(reason="n2", noop=True, agent_id="a")
        s2 = WakeupScheduler(db_path=db)
        st = s2.status(agent_id="a")
        assert st["current_streak"] == 2 and st["total_events"] == 2
        # 续跑 streak 跨实例延续
        e3 = s2.record(reason="n3", noop=True, agent_id="a")
        assert e3["streak"] == 3


# ── ⑧ live API（:8090 真实服务，重启后运行） ──


@pytest.mark.live
class TestWakeupApiLive:
    def test_schedule_and_folded_roundtrip(self, client):
        sid = f"live-wakeup-{int(time.time())}"
        # noop streak 3 连
        for i in range(3):
            r = client.post(
                "/api/will/wakeup/schedule",
                json={
                    "agent_id": "live",
                    "session_id": sid,
                    "reason": f"live noop {i}",
                    "noop": True,
                    "delay_seconds": 300,
                },
            )
            assert r.status_code == 200, r.text
        body = r.json()
        assert body["streak"] == 3 and body["folded"] is True
        assert body["delay_s"] == 300 and body["clamped"] is False

        r = client.get(f"/api/will/wakeup/folded?agent_id=live&session_id={sid}")
        assert r.status_code == 200
        view = r.json()
        assert view["current_streak"] == 3
        assert any(it["kind"] == "noop_streak" and it["count"] == 3 for it in view["items"])

        r = client.get(f"/api/will/wakeup/status?agent_id=live&session_id={sid}")
        assert r.status_code == 200
        assert r.json()["active"] is True

    def test_clamp_visible_live(self, client):
        sid = f"live-clamp-{int(time.time())}"
        r = client.post(
            "/api/will/wakeup/schedule",
            json={
                "agent_id": "live",
                "session_id": sid,
                "reason": "clamp check",
                "noop": False,
                "delay_seconds": 5,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["clamped"] is True and body["delay_s"] == 60

    def test_empty_reason_rejected_live(self, client):
        r = client.post(
            "/api/will/wakeup/schedule",
            json={"agent_id": "live", "reason": "", "noop": True},
        )
        assert r.status_code == 400

    def test_stop_closes_loop_live(self, client):
        sid = f"live-stop-{int(time.time())}"
        r = client.post(
            "/api/will/wakeup/schedule",
            json={
                "agent_id": "live",
                "session_id": sid,
                "reason": "done",
                "noop": False,
                "stop": True,
            },
        )
        assert r.status_code == 200
        assert r.json()["active"] is False
        st = client.get(f"/api/will/wakeup/status?agent_id=live&session_id={sid}").json()
        assert st["active"] is False
