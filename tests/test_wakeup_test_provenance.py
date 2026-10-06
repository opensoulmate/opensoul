"""wakeup live测试生产污染收编——测试行provenance防线回归钉（2026-10-06收编轮）。

背景（勘察实锤）：生产wakeup.db 196行中190行为live测试产物（agent_id
'live'/'live-ov' 或 session_id 'live-*' 指纹），其中62行 stopped=0 且
fired_at=NULL——会被真实 wakeup_executor 经 /wakeup/due 当唤醒点火
（spurious wakeup）；monitoring面板被129个测试key刷屏（真实仅~6行）。

三面防线（jobs.source provenance轮同款"产生侧标记+消费侧不认+自清"）：
1. 产生侧：live测试schedule声明 source='test'（静态钉扫描 test_wakeup*.py）
2. 消费侧：_is_due_now 排除 source='test'——测试行永不点火真实唤醒
   （单一谓词=status due_now / overview due_count / due_wakeups 三面同步免疫）
3. 自清：POST /wakeup/purge?source=test（零过滤拒绝），live测试fixture跑完自清
4. 面板面：overview() 默认排除test循环（include_test=True 显式查看）
"""

import ast
import sqlite3
import time
from pathlib import Path

import pytest

from src.will.wakeup import WakeupScheduler


def _make_sched(tmp_path) -> WakeupScheduler:
    return WakeupScheduler(db_path=str(tmp_path / "wakeup.db"))


def _force_due(db_path: str, event_id: int) -> None:
    """把事件的next_wakeup_at改到过去（越过MIN_WAKEUP_DELAY_S钳制，制造到点现场）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE wakeup_events SET next_wakeup_at=? WHERE id=?", (time.time() - 1, event_id)
        )
        conn.commit()
    finally:
        conn.close()


class TestDueNeverFiresTestRows:
    """消费侧防线：source='test' 事件永不due——测试行不允许唤醒真实agent。"""

    def test_test_source_event_never_due(self, tmp_path):
        sched = _make_sched(tmp_path)
        ev = sched.record(
            reason="测试探针", noop=True, agent_id="t", session_id="s1", source="test"
        )
        _force_due(sched._db_path, ev["event_id"])
        assert sched.due_wakeups() == []
        st = sched.status("t", "s1")
        assert st["fire"]["due_now"] is False
        assert sched.overview()["due_count"] == 0

    def test_real_source_event_due_all_surfaces(self, tmp_path):
        """对照组：同现场换真实source，三处消费面同一判定（单一谓词不漂移）。"""
        sched = _make_sched(tmp_path)
        ev = sched.record(reason="真实唤醒", noop=True, agent_id="t", session_id="s2", source="")
        _force_due(sched._db_path, ev["event_id"])
        due = sched.due_wakeups()
        assert [e["event_id"] for e in due] == [ev["event_id"]]
        assert sched.status("t", "s2")["fire"]["due_now"] is True
        assert sched.overview()["due_count"] == 1

    def test_fired_and_stopped_guards_compose(self, tmp_path):
        """既有守卫不回归：test守卫是叠加项，fired/stopped/超限守卫照旧生效。"""
        sched = _make_sched(tmp_path)
        ev = sched.record(
            reason="停止的测试行",
            noop=False,
            stop=True,
            agent_id="t",
            session_id="s3",
            source="test",
        )
        _force_due(sched._db_path, ev["event_id"])
        assert sched.due_wakeups() == []
        ev2 = sched.record(reason="真实已领取", noop=True, agent_id="t", session_id="s4")
        _force_due(sched._db_path, ev2["event_id"])
        assert sched.claim_fire(ev2["event_id"]) is not None
        assert sched.due_wakeups() == []


class TestOverviewPanelExcludesTest:
    """面板面防线：monitoring面板默认只呈现真实唤醒循环。"""

    def test_default_excludes_test_loops(self, tmp_path):
        sched = _make_sched(tmp_path)
        sched.record(reason="探针", noop=True, agent_id="live-ov", session_id="p1", source="test")
        sched.record(reason="真实", noop=True, agent_id="soulmate", session_id="s1")
        ov = sched.overview()
        keys = {(s["agent_id"], s["session_id"]) for s in ov["loops"]}
        assert ("soulmate", "s1") in keys
        assert ("live-ov", "p1") not in keys
        assert ov["total_keys"] == 1

    def test_include_test_shows_all(self, tmp_path):
        sched = _make_sched(tmp_path)
        sched.record(reason="探针", noop=True, agent_id="live-ov", session_id="p1", source="test")
        ov = sched.overview(include_test=True)
        keys = {(s["agent_id"], s["session_id"]) for s in ov["loops"]}
        assert ("live-ov", "p1") in keys
        assert ov["total_keys"] == 1


class TestPurgeProvenance:
    """自清防线：purge只清schema级标记行（不按名字猜），零过滤拒绝。"""

    def test_purge_deletes_only_test_rows(self, tmp_path):
        sched = _make_sched(tmp_path)
        sched.record(reason="探针1", noop=True, agent_id="a", session_id="x", source="test")
        sched.record(reason="探针2", noop=True, agent_id="b", session_id="y", source="test")
        sched.record(reason="真实", noop=True, agent_id="c", session_id="z", source="")
        result = sched.purge("test")
        assert result == {"source": "test", "purged": 2}
        assert sched.status("c", "z")["total_events"] == 1
        assert sched.status("a", "x")["total_events"] == 0

    def test_purge_rejects_empty_source(self, tmp_path):
        sched = _make_sched(tmp_path)
        sched.record(reason="真实", noop=True, agent_id="c", session_id="z")
        with pytest.raises(ValueError):
            sched.purge("")
        with pytest.raises(ValueError):
            sched.purge("   ")
        assert sched.status("c", "z")["total_events"] == 1  # 行仍在

    def test_purge_nonexistent_source_is_noop(self, tmp_path):
        sched = _make_sched(tmp_path)
        sched.record(reason="真实", noop=True, agent_id="c", session_id="z")
        assert sched.purge("test") == {"source": "test", "purged": 0}


class TestWakeupProvenanceApi:
    """API接线钉（will_client隔离面：真实router+tmp wakeup.db，绝不写生产）。"""

    def test_api_endpoints_wired_e2e(self, will_client):
        r = will_client.post(
            "/api/will/wakeup/schedule",
            json={
                "agent_id": "live-ov",
                "session_id": "p1",
                "reason": "探针",
                "noop": True,
                "source": "test",
            },
        )
        assert r.status_code == 200
        # 默认面板排除
        ov = will_client.get("/api/will/wakeup/overview").json()
        assert all(s["session_id"] != "p1" for s in ov["loops"])
        # include_test 显式可见
        ov2 = will_client.get("/api/will/wakeup/overview", params={"include_test": "true"}).json()
        assert any(s["session_id"] == "p1" for s in ov2["loops"])
        # 自清
        r = will_client.post("/api/will/wakeup/purge", params={"source": "test"})
        assert r.status_code == 200 and r.json() == {"source": "test", "purged": 1}
        st = will_client.get(
            "/api/will/wakeup/status", params={"agent_id": "live-ov", "session_id": "p1"}
        ).json()
        assert st["total_events"] == 0

    def test_api_purge_zero_filter_rejected(self, will_client):
        r = will_client.post("/api/will/wakeup/purge")
        assert r.status_code == 400
        assert "purge requires source" in r.json()["detail"]

    def test_data_plane_is_tmp_wakeup_db(self, will_client, tmp_path):
        """隔离护栏：will_client的wakeup数据面必须是tmp库（防脱线回归生产wakeup.db）。"""
        db = will_client.app.state.will_client_wakeup_db
        assert str(db).startswith(str(tmp_path))
        assert Path(db).name == "wakeup.db"


class TestLiveTestsDeclareAndPurge:
    """静态钉：live测试文件必须产生侧声明source='test'且跑完自清（防脱线回归）。

    AST级扫描（非正则——负载含 f-string 内嵌花括号时正则截断误报，实测踩坑）：
    遍历 client.post("/api/will/wakeup/schedule", json={...}) 调用点，检查
    json关键字字典的 source 键值。"""

    _LIVE_FILES = ("test_wakeup.py", "test_wakeup_overview.py")
    _SCHEDULE_URL = "/api/will/wakeup/schedule"

    def _paths(self):
        base = Path(__file__).parent
        return [base / n for n in self._LIVE_FILES]

    def _schedule_calls(self, path: Path):
        tree = ast.parse(path.read_text())
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "post"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            if node.args[0].value != self._SCHEDULE_URL:
                continue
            json_kw = next((k for k in node.keywords if k.arg == "json"), None)
            found.append((node, json_kw))
        return found

    def test_every_schedule_payload_declares_test_source(self):
        total = 0
        for p in self._paths():
            calls = self._schedule_calls(p)
            assert calls, f"{p.name}: 未找到schedule调用点（扫描面失效？）"
            for node, json_kw in calls:
                assert json_kw is not None, f"{p.name}:{node.lineno} schedule调用缺json负载"
                payload = json_kw.value
                assert isinstance(payload, ast.Dict), f"{p.name}:{node.lineno} json负载非字典"
                src_val = None
                for k, v in zip(payload.keys, payload.values):
                    if isinstance(k, ast.Constant) and k.value == "source":
                        src_val = v.value if isinstance(v, ast.Constant) else None
                assert src_val == "test", (
                    f"{p.name}:{node.lineno} schedule负载缺test出身声明（source={src_val!r}）"
                )
            total += len(calls)
        assert total >= 5  # 5个live schedule调用点（test_wakeup.py 4 + overview 1）

    def test_live_classes_self_purge(self):
        for p in self._paths():
            text = p.read_text()
            assert "/api/will/wakeup/purge" in text, f"{p.name} 缺自清调用（wakeup/purge）"
            assert 'source": "test"' in text or "'source': 'test'" in text
