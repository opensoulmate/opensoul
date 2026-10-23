"""GR-①b-⑤ AlertManager._history 内存窗+告警史真源+计数真源化+解决态写穿——回归测试。

对齐 GR-①b-④（dream）家族纪律：
- 窗裁剪：persist 在场才裁、N<=0 不裁、persist=None 纯内存字节不变
- 读面零可观测变化：任何 N 值 /api/vital/alerts 列表与 /stats alerts 计数不变
- 计数真源化（MEM-⑥）：total/active 恒对账 alert_history 行聚合，毒化硬反证
- 解决态写穿（healer 状态快照同款）：resolved=True 行影终值落行
- 启动回载有界 + 重启 parity + firing 恢复（无重复行/无重复通知）
- 诚实边界：写失败行只活内存窗（窗内可见、回落/重启后不可见=非谎报 durable）
"""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from src.vital.alert import AlertManager, AlertRecord, AlertRule
from src.vital.alert_persist import AlertPersistence
from src.vital.collector import MetricsCollector, MetricsSnapshot, SystemMetrics


class StubCollector(MetricsCollector):
    """可控指标源（_resolve_path 走真实 MetricsSnapshot 属性路径）。"""

    def __init__(self, cpu: float = 0.0):
        super().__init__()
        self.cpu = cpu

    @property
    def snapshot(self) -> MetricsSnapshot:
        snap = MetricsSnapshot()
        snap.system = SystemMetrics(cpu_percent=self.cpu)
        return snap


class StubChecker:
    async def check(self):
        return SimpleNamespace(
            status=SimpleNamespace(value="ok"),
            components=[SimpleNamespace(status=SimpleNamespace(value="ok"))],
        )


def _rule(threshold: float = 80.0) -> AlertRule:
    return AlertRule(
        name="t_cpu",
        metric="system.cpu_percent",
        threshold=threshold,
        message_tpl="cpu {value:.0f}>{threshold:.0f}",
    )


def _rec(rule_name: str = "t_cpu", resolved: bool = False, ts: float = 1.0) -> AlertRecord:
    return AlertRecord(
        rule_name=rule_name,
        severity="warning",
        message="m",
        value=1.0,
        threshold=0.0,
        ts=ts,
        resolved=resolved,
    )


def _mgr(collector=None, rules=None, persist=None):
    kwargs = {} if persist is None else {"persist": persist}
    return AlertManager(collector or StubCollector(), rules=rules or [_rule()], **kwargs)


def _rows(p: AlertPersistence) -> list[dict]:
    return p.load_all()


# ── 窗裁剪（delitem 形态=普查判定单一源真认 grow_with_trim） ──────────────


class TestWindowTrim:
    def test_persist_none_pure_memory_no_trim(self, tmp_path):
        """persist=None 纯内存既有语义字节不变：无真源不裁=不丢唯一副本。
        （走 evaluate() 既有公开路径驱动记录=控制组不变面：旧码同过非自嗨。）"""
        rules = [_rule() for _ in range(5)]
        for i, r in enumerate(rules):
            r.name = f"t_{i}"
        m = _mgr(collector=StubCollector(cpu=95.0), rules=rules, persist=None)
        m.history_window = 3
        fired = asyncio.run(m.evaluate())
        assert len(fired) == 5
        assert len(m.history) == 5  # 修前面：永不裁

    def test_window_trims_memory_rows_survive_in_truth(self, tmp_path):
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m = _mgr(persist=p)
        m.history_window = 3
        for i in range(5):
            m._record_alert(_rec(ts=float(i)))
        assert len(m.history) == 3  # 窗裁剪
        assert [r.ts for r in m.history] == [2.0, 3.0, 4.0]  # 保新弃旧
        rows = _rows(p)
        assert len(rows) == 5  # 行真源恒全表
        assert [r["ts"] for r in rows] == [0.0, 1.0, 2.0, 3.0, 4.0]  # append 序

    def test_window_zero_no_trim(self, tmp_path):
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m = _mgr(persist=p)
        m.history_window = 0
        for i in range(5):
            m._record_alert(_rec(ts=float(i)))
        assert len(m.history) == 5
        assert len(_rows(p)) == 5

    def test_record_roundtrip_row_shape(self, tmp_path):
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        rec = _rec(ts=42.5)
        p.insert(rec.to_row())
        rows = _rows(p)
        assert len(rows) == 1
        back = AlertRecord.from_row(rows[0])
        assert back.to_row() == rec.to_row()  # 往返逐键保真（含 alert_id）


# ── 读面零可观测变化（/api/vital/alerts 列表 + /stats alerts 计数） ────────


def _client(mgr, cpu: float = 0.0):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.vital import router

    app = FastAPI()
    app.include_router(router, prefix="/api/vital")
    app.state.vital_alert_mgr = mgr
    app.state.vital_collector = StubCollector(cpu)
    app.state.vital_checker = StubChecker()
    return TestClient(app)


class TestReadFaceZeroChange:
    ALERT_KEYS = {"rule", "severity", "message", "value", "threshold", "resolved", "ts"}

    def test_small_history_shape_and_order(self, tmp_path):
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m = _mgr(persist=p)
        m._record_alert(_rec(rule_name="r1", ts=1.0))
        m._record_alert(_rec(rule_name="r2", ts=2.0))
        m._record_alert(_rec(rule_name="r1", resolved=True, ts=3.0))
        c = _client(m)
        data = c.get("/api/vital/alerts").json()["alerts"]
        assert [a["rule"] for a in data] == ["r1", "r2", "r1"]  # append 序
        assert all(set(a) == self.ALERT_KEYS for a in data)  # 键形逐键不变
        assert data[2]["resolved"] is True
        stats = c.get("/api/vital/stats").json()["alerts"]
        assert stats == {"total": 3, "active": 2}

    def test_trimmed_history_falls_back_to_full_truth(self, tmp_path):
        """窗不足回落行真源：任何 N 值读面输出不变（全量列表=全史）。"""
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m = _mgr(persist=p)
        m.history_window = 3
        for i in range(5):
            m._record_alert(_rec(rule_name=f"r{i}", ts=float(i)))
        assert len(m.history) == 3  # 前提：窗已裁
        c = _client(m)
        data = c.get("/api/vital/alerts").json()["alerts"]
        assert [a["rule"] for a in data] == [f"r{i}" for i in range(5)]  # 全史无丢

    def test_stats_alerts_keys_exact(self, tmp_path):
        """键形契约（旧码既有=控制组不变面）。"""
        m = _mgr(persist=None)
        c = _client(m)
        alerts = c.get("/api/vital/stats").json()["alerts"]
        assert set(alerts) == {"total", "active"}


# ── 计数真源化（MEM-⑥：恒对账行聚合，禁信第二套聚合） ────────────────────


class TestCounterTruth:
    def test_counters_survive_trim(self, tmp_path):
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m = _mgr(persist=p)
        m.history_window = 2
        recs = []
        for i in range(5):
            r = _rec(rule_name=f"r{i}", ts=float(i))
            recs.append(r)
            m._record_alert(r)
        m._resolve_alert(recs[0])
        m._resolve_alert(recs[1])
        assert len(m.history) == 2  # 窗只剩 2 行
        assert m.counters() == {"total": 5, "active": 3}  # 计数不随裁剪回退

    def test_counters_truth_sees_out_of_band_row(self, tmp_path):
        """毒化硬反证：库外行即时入账——读面读真源禁信窗缓存。"""
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m = _mgr(persist=p)
        m._record_alert(_rec(rule_name="r0"))
        assert m.counters() == {"total": 1, "active": 1}
        p.insert(_rec(rule_name="out_of_band").to_row())  # 库外 SQL 面
        assert m.counters() == {"total": 2, "active": 2}

    def test_counter_fallback_on_truth_failure(self, tmp_path):
        """对账失败回落缓存禁假 0（缓存本职），不抛不吞。"""
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m = _mgr(persist=p)
        m._record_alert(_rec(rule_name="r0"))

        def boom() -> dict:
            raise RuntimeError("db down")

        p.counters = boom
        assert m.counters() == {"total": 1, "active": 1}  # 回落窗缓存非假 0

    def test_persist_none_counters_unchanged(self, tmp_path):
        """persist=None 计数=修前字节同义（走 /stats 既有读面=控制组不变面）。"""
        rules = [_rule(), _rule()]
        rules[0].name = "t_a"
        rules[1].name = "t_b"
        m = _mgr(collector=StubCollector(cpu=95.0), rules=rules, persist=None)
        asyncio.run(m.evaluate())
        rules[0].threshold = 200.0  # t_a 不再触发→解决
        asyncio.run(m.evaluate())
        alerts = _client(m).get("/api/vital/stats").json()["alerts"]
        assert alerts == {"total": 2, "active": 1}  # 修前 len(history)/未解决数


# ── 解决态写穿 + 真实路径（evaluate() 全链） ──────────────────────────────


class TestResolveWriteThroughAndRealPath:
    def test_real_path_evaluate_records_and_resolves(self, tmp_path):
        """真实路径：evaluate() 触发→_record_alert 落行；不再触发→_resolve_alert 写穿。"""
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        col = StubCollector(cpu=95.0)
        m = _mgr(collector=col, persist=p)
        fired = asyncio.run(m.evaluate())
        assert len(fired) == 1
        rows = _rows(p)
        assert len(rows) == 1 and rows[0]["resolved"] is False
        assert rows[0]["message"] == "cpu 95>80"
        col.cpu = 10.0
        resolved_now = asyncio.run(m.evaluate())
        assert resolved_now == []
        rows = _rows(p)
        assert rows[0]["resolved"] is True  # 行影终值写穿
        assert m.counters() == {"total": 1, "active": 0}

    def test_resolve_idempotent_no_raise(self, tmp_path):
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m = _mgr(persist=p)
        r = _rec(rule_name="r0")
        m._record_alert(r)
        m._resolve_alert(r)
        m._resolve_alert(r)  # 再解决=行影同终值，不抛
        assert _rows(p)[0]["resolved"] is True

    def test_write_failure_row_lives_in_window_only(self, tmp_path, caplog):
        """诚实边界：写失败行只活内存（窗内可见、DB/回落/重启后不可见=非谎报 durable）。"""
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m = _mgr(persist=p)

        def boom(record: dict) -> None:
            raise RuntimeError("disk full")

        p.insert = boom
        with caplog.at_level(logging.ERROR):
            m._record_alert(_rec(rule_name="r0"))
        assert any("alert history persist failed" in r.message for r in caplog.records)
        assert [a.rule_name for a in m.history] == ["r0"]  # 窗内可见
        # 写失败行不进真源：计数真源如实 0 行（窗内可见而真源不可见=非谎报
        # durable 的对应面，GR-①b-④ 边界②同款；回落缓存只在真源读故障时发生）
        assert m._counter_truth() == {"total": 0, "active": 0}
        c = _client(m)
        data = c.get("/api/vital/alerts").json()["alerts"]
        assert [a["rule"] for a in data] == ["r0"]  # 快路径窗内可见=修前面
        # 解决写穿对不存在行=0 行 no-op（诚实边界④），不抛
        m._resolve_alert(m.history[0])


# ── 启动回载有界 + 重启 parity + firing 恢复（无重复行/无重复通知） ────────


class TestReloadAndRestart:
    def test_reload_bounded_to_window(self, tmp_path, monkeypatch):
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m1 = _mgr(persist=p)
        for i in range(10):
            m1._record_alert(_rec(rule_name=f"r{i}", ts=float(i)))
        monkeypatch.setattr(AlertManager, "history_window", 4)
        m2 = _mgr(persist=p)  # 重启：只回载最近 4 行（修前无落盘口；全量回载=重启即无界）
        assert [r.rule_name for r in m2.history] == ["r6", "r7", "r8", "r9"]
        assert len(_rows(p)) == 10  # 行真源不丢

    def test_restart_parity_counters_and_history(self, tmp_path):
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m1 = _mgr(persist=p)
        recs = [_rec(rule_name=f"r{i}", ts=float(i)) for i in range(3)]
        for r in recs:
            m1._record_alert(r)
        m1._resolve_alert(recs[0])
        before = (m1.counters(), [r.to_row() for r in m1.history])
        m2 = _mgr(persist=p)  # 重启
        assert m2.counters() == before[0] == {"total": 3, "active": 2}  # 不丢
        assert [r.to_row() for r in m2.history] == before[1]  # 序/逐键 parity

    def test_firing_restore_no_duplicate_fire_or_notify(self, tmp_path, monkeypatch):
        """重启后同条件不重复告警/不重复通知/同事件不双行（数据对账「无重复行」）。"""
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        col = StubCollector(cpu=95.0)
        calls = []

        async def fake_notify(self, record):
            calls.append(record)

        monkeypatch.setattr(AlertManager, "_notify", fake_notify)
        m1 = _mgr(collector=col, persist=p)
        asyncio.run(m1.evaluate())
        assert len(calls) == 1 and p.counters() == {"total": 1, "active": 1}
        m2 = _mgr(collector=col, persist=p)  # 重启（未解决行→firing 恢复）
        assert "t_cpu" in m2._firing
        asyncio.run(m2.evaluate())  # 条件仍触发
        assert len(calls) == 1  # 无第二次通知
        assert p.counters() == {"total": 1, "active": 1}  # 无重复行
        assert len(m2.history) == 1

    def test_firing_restore_resolves_when_cleared(self, tmp_path, monkeypatch):
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        col = StubCollector(cpu=95.0)

        async def fake_notify(self, record):
            pass

        monkeypatch.setattr(AlertManager, "_notify", fake_notify)
        m1 = _mgr(collector=col, persist=p)
        asyncio.run(m1.evaluate())
        col.cpu = 10.0
        m2 = _mgr(collector=col, persist=p)  # 重启（条件已恢复）
        asyncio.run(m2.evaluate())
        assert m2._firing == {}
        assert p.counters() == {"total": 1, "active": 0}  # 解决态写穿跨重启
        assert _rows(p)[0]["resolved"] is True

    def test_reload_failure_starts_empty_visible(self, tmp_path, caplog):
        p = AlertPersistence(db_path=str(tmp_path / "a.db"))
        m1 = _mgr(persist=p)
        m1._record_alert(_rec(rule_name="r0"))

        def boom(limit: int = 1000) -> list[dict]:
            raise RuntimeError("db down")

        p.load_recent = boom
        with caplog.at_level(logging.ERROR):
            m2 = _mgr(persist=p)
        assert m2.history == []  # 空史起步
        assert any("alert history reload failed" in r.message for r in caplog.records)
        assert p.counters() == {"total": 1, "active": 1}  # 行真源仍在库
