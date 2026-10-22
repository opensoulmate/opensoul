"""RV-①b-⑨ pulse 脉冲信号注册表持久化——PulseEngine._signals 写穿/懒回载回归。

修前（RV-①c §5.1 定性）：_signals 全进程内存，create/tick/pause/resume/cancel/
delete 只改内存——用户创建脉冲信号（POST /api/pulse/signals + by-ID/tick/pause/
resume 契约）随 service 重启全清，bootstrap 种子信号也只在首次 bootstrap 建
（main.py is_bootstrapped 一次性）=重启后定时脉冲静默消失。

覆盖：行语义（状态快照 upsert/往返/删行幂等/坏行跳过/回载只读不建库）、写穿
（6 变更点）、重启回载（逐键 parity/状态与 max_fires 保真/懒回载恰一次/只补缺
不覆盖/total_ticks 对账下限）、纯内存零变化、故障旁路（写失败/回载失败重试）、
env 解析、API 全链（用户真实路径：POST → 行 → 模拟重启 → by-ID 回载可见）。

数据面全 tmp（PULSE_SIGNALS_DB env 每用例 tmp——conftest 全隔离）；引擎用显式
db_path 的独立实例（不碰 api 单例），API 全链例走真 router 真单例。
"""

from __future__ import annotations

import sqlite3

import pytest

from src.pulse.signal_persist import SignalPersistence, default_db_path
from src.pulse.timer import PulseEngine, PulseSignal, SignalStatus, SignalType


def _persist(tmp_path, name="pulse_signals.db") -> SignalPersistence:
    return SignalPersistence(db_path=str(tmp_path / name))


def _engine(tmp_path, name="pulse_signals.db") -> PulseEngine:
    return PulseEngine(persist=_persist(tmp_path, name))


def _seed_raw(db_file: str, row: dict) -> None:
    """直接落一行（造坏行/旁路 persist 用）。"""
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pulse_signals ("
            "signal_id TEXT PRIMARY KEY, signal_type TEXT NOT NULL, name TEXT NOT NULL, "
            "interval_ms REAL NOT NULL, status TEXT NOT NULL, "
            "callback_url TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL DEFAULT '{}', "
            "created_at REAL NOT NULL, last_fired_at REAL, next_fire_at REAL, "
            "fire_count INTEGER NOT NULL DEFAULT 0, max_fires INTEGER NOT NULL DEFAULT 0, "
            "precision_ms REAL NOT NULL DEFAULT 1.0, drift_correction REAL NOT NULL DEFAULT 0.0)"
        )
        conn.execute(
            "INSERT INTO pulse_signals (signal_id, signal_type, name, interval_ms, status,"
            " callback_url, payload, created_at, last_fired_at, next_fire_at, fire_count,"
            " max_fires, precision_ms, drift_correction)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["signal_id"],
                row.get("signal_type", "interval"),
                row.get("name", "seed"),
                row.get("interval_ms", 1000.0),
                row.get("status", "active"),
                row.get("callback_url", ""),
                row.get("payload", "{}"),
                row.get("created_at", 1000.0),
                row.get("last_fired_at"),
                row.get("next_fire_at"),
                row.get("fire_count", 0),
                row.get("max_fires", 0),
                row.get("precision_ms", 1.0),
                row.get("drift_correction", 0.0),
            ),
        )
        conn.commit()


def _row(db_file: str, signal_id: str):
    with sqlite3.connect(db_file) as conn:
        return conn.execute(
            "SELECT signal_id, signal_type, name, interval_ms, status, callback_url,"
            " payload, created_at, last_fired_at, next_fire_at, fire_count, max_fires,"
            " precision_ms, drift_correction FROM pulse_signals WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()


# ── 行语义（状态快照表）─────────────────────────────────────────────────────────


class TestRowSemantics:
    def test_upsert_load往返逐键含payload与NULL(self, tmp_path):
        p = _persist(tmp_path)
        p.upsert(
            {
                "signal_id": "pulse_x",
                "signal_type": "cron",
                "name": "每日",
                "interval_ms": 1500.5,
                "status": "paused",
                "callback_url": "http://127.0.0.1:9/hook",
                "payload": {"type": "health_check", "中文": "值"},
                "created_at": 1234.5,
                "last_fired_at": None,
                "next_fire_at": 2345.5,
                "fire_count": 3,
                "max_fires": 7,
                "precision_ms": 2.0,
                "drift_correction": -0.25,
            }
        )
        (row,) = p.load_all()
        assert row == {
            "signal_id": "pulse_x",
            "signal_type": "cron",
            "name": "每日",
            "interval_ms": 1500.5,
            "status": "paused",
            "callback_url": "http://127.0.0.1:9/hook",
            "payload": {"type": "health_check", "中文": "值"},
            "created_at": 1234.5,
            "last_fired_at": None,
            "next_fire_at": 2345.5,
            "fire_count": 3,
            "max_fires": 7,
            "precision_ms": 2.0,
            "drift_correction": -0.25,
        }

    def test_upsert当前态一信号一行(self, tmp_path):
        p = _persist(tmp_path)
        row = {
            "signal_id": "pulse_x",
            "signal_type": "interval",
            "name": "a",
            "interval_ms": 1000.0,
            "status": "active",
            "created_at": 1.0,
            "fire_count": 0,
            "max_fires": 0,
        }
        p.upsert(row)
        p.upsert({**row, "status": "paused", "fire_count": 2})
        assert p.count() == 1
        (loaded,) = p.load_all()
        assert loaded["status"] == "paused" and loaded["fire_count"] == 2

    def test_delete幂等缺行无异常(self, tmp_path):
        p = _persist(tmp_path)
        p.upsert(
            {
                "signal_id": "pulse_x",
                "signal_type": "once",
                "name": "a",
                "interval_ms": 1.0,
                "status": "active",
                "created_at": 1.0,
            }
        )
        p.delete("pulse_x")
        assert p.load_all() == []
        p.delete("pulse_x")  # 幂等：缺行=同一效果
        p.delete("never_existed")
        assert p.count() == 0

    def test坏行_payload不可解跳过可见(self, tmp_path, caplog):
        db = str(tmp_path / "p.db")
        _seed_raw(db, {"signal_id": "good", "payload": '{"a": 1}'})
        _seed_raw(db, {"signal_id": "bad", "payload": "{不是JSON"})
        p = SignalPersistence(db_path=db)
        with caplog.at_level("ERROR"):
            rows = p.load_all()
        assert [r["signal_id"] for r in rows] == ["good"]
        assert any("坏行跳过" in r.message and "bad" in r.message for r in caplog.records)

    def test坏行_枚举值不可解跳过可见(self, tmp_path, caplog):
        db = str(tmp_path / "p.db")
        _seed_raw(db, {"signal_id": "good", "status": "active"})
        _seed_raw(db, {"signal_id": "bad", "status": "future_status"})
        eng = PulseEngine(persist=SignalPersistence(db_path=db))
        with caplog.at_level("ERROR"):
            sigs = eng.list_signals()
        assert [s.signal_id for s in sigs] == ["good"]
        assert any("坏行跳过" in r.message and "bad" in r.message for r in caplog.records)

    def test回载只读不建库(self, tmp_path):
        db = tmp_path / "nope.db"
        p = SignalPersistence(db_path=str(db))
        assert p.load_all() == []
        assert not db.exists()
        assert p.count() == 0
        assert not db.exists()


# ── 写穿（变更点全收）───────────────────────────────────────────────────────────


class TestWriteThrough:
    def test_create落行逐键(self, tmp_path):
        db = tmp_path / "p.db"
        eng = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        sig = eng.create_signal(
            "每日巡检",
            "interval",
            60000.0,
            callback_url="http://127.0.0.1:9/hook",
            payload={"type": "health_check"},
            max_fires=5,
        )
        row = _row(str(db), sig.signal_id)
        assert row is not None
        (
            sid,
            stype,
            name,
            interval,
            status,
            cb,
            payload,
            created,
            last_fired,
            next_fire,
            fire_count,
            max_fires,
            prec,
            drift,
        ) = row
        assert sid == sig.signal_id and stype == "interval" and name == "每日巡检"
        assert interval == 60000.0 and status == "active"
        assert cb == "http://127.0.0.1:9/hook" and payload == '{"type": "health_check"}'
        assert created == sig.created_at and last_fired is None
        assert next_fire == sig.next_fire_at and fire_count == 0 and max_fires == 5
        assert prec == 1.0 and drift == 0.0

    def test_tick落行_fire_count时间漂移随更(self, tmp_path):
        db = tmp_path / "p.db"
        eng = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        sig = eng.create_signal("s", "interval", 1000.0)
        rec = eng.tick(sig.signal_id)
        assert rec is not None
        row = _row(str(db), sig.signal_id)
        assert row[10] == 1  # fire_count
        assert row[8] == pytest.approx(rec.timestamp)  # last_fired_at
        assert row[9] is not None  # next_fire_at 重算
        assert row[13] == pytest.approx(rec.drift_ms * 0.1)  # drift_correction

    def test_pause_resume_cancel状态落行(self, tmp_path):
        db = tmp_path / "p.db"
        eng = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        sig = eng.create_signal("s", "interval", 1000.0)
        assert eng.pause_signal(sig.signal_id)
        assert _row(str(db), sig.signal_id)[4] == "paused"
        assert eng.resume_signal(sig.signal_id)
        assert _row(str(db), sig.signal_id)[4] == "active"
        assert eng.cancel_signal(sig.signal_id)
        assert _row(str(db), sig.signal_id)[4] == "cancelled"

    def test_delete_signal删行不复活(self, tmp_path):
        db = tmp_path / "p.db"
        eng = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        sig = eng.create_signal("s", "once", 1.0)
        assert eng.delete_signal(sig.signal_id)
        assert _row(str(db), sig.signal_id) is None
        # 删行后重启不复活
        eng2 = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        assert eng2.get_signal(sig.signal_id) is None


# ── 重启回载 ──────────────────────────────────────────────────────────────────


class TestReload:
    def test重启回载逐键parity与状态保真(self, tmp_path):
        db = tmp_path / "p.db"
        eng = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        a = eng.create_signal("a", "interval", 2000.0, payload={"k": "v"})
        b = eng.create_signal("b", "once", 1.0)
        c = eng.create_signal("c", "cron", 3000.0)
        eng.tick(a.signal_id)
        eng.pause_signal(c.signal_id)
        eng.cancel_signal(b.signal_id)

        eng2 = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        ra, rb, rc = (eng2.get_signal(x) for x in (a.signal_id, b.signal_id, c.signal_id))
        assert ra is not None and rb is not None and rc is not None
        # 逐键 parity（含嵌套 payload/时间/计数）
        for old, new in ((a, ra), (b, rb), (c, rc)):
            assert new.signal_id == old.signal_id
            assert new.signal_type == old.signal_type and new.name == old.name
            assert new.interval_ms == old.interval_ms and new.status == old.status
            assert new.callback_url == old.callback_url and new.payload == old.payload
            assert new.created_at == old.created_at
            assert new.last_fired_at == old.last_fired_at
            assert new.next_fire_at == old.next_fire_at
            assert new.fire_count == old.fire_count and new.max_fires == old.max_fires
        # 状态保真：paused 不复活调度、cancelled 不复活
        assert rc.status == SignalStatus.PAUSED and rb.status == SignalStatus.CANCELLED
        assert ra.fire_count == 1

    def test_max_fires耗尽语义跨重启保真(self, tmp_path):
        db = tmp_path / "p.db"
        eng = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        sig = eng.create_signal("s", "interval", 1000.0, max_fires=1)
        assert eng.tick(sig.signal_id) is not None
        assert sig.status == SignalStatus.COMPLETED

        eng2 = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        restored = eng2.get_signal(sig.signal_id)
        assert restored.status == SignalStatus.COMPLETED
        assert eng2.tick(sig.signal_id) is None  # 耗尽后不复活再触发

    def test_懒回载恰一次_先载后录(self, tmp_path):
        db = tmp_path / "p.db"
        eng = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        eng.create_signal("old", "interval", 1000.0)

        eng2 = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        calls = []
        real = eng2._persist.load_all

        def spy():
            calls.append(1)
            return real()

        eng2._persist.load_all = spy
        # 先载后录：create 前先回载旧行，新行不冲掉旧行
        new = eng2.create_signal("new", "interval", 1000.0)
        eng2.list_signals()
        eng2.get_stats()
        assert len(calls) == 1
        names = {s.name for s in eng2.list_signals()}
        assert names == {"old", "new"}
        assert new.signal_id in eng2._signals

    def test_只补缺不覆盖_内存新态不被回载洗(self, tmp_path, caplog):
        db = tmp_path / "p.db"
        eng = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        sig = eng.create_signal("s", "interval", 1000.0)
        # 内存推进到 fire_count=2 但模拟落盘失败行仍 0（内存更真）
        with caplog.at_level("ERROR"):
            eng._persist.upsert = lambda row: (_ for _ in ()).throw(RuntimeError("disk full"))
            eng.tick(sig.signal_id)
            eng.tick(sig.signal_id)
        assert eng.get_signal(sig.signal_id).fire_count == 2
        # 强制再触回载：内存新态不被旧行洗掉
        eng._loaded = False
        eng._persist = SignalPersistence(db_path=str(db))
        assert eng.get_signal(sig.signal_id).fire_count == 2

    def test_total_ticks对账下限(self, tmp_path):
        db = tmp_path / "p.db"
        eng = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        a = eng.create_signal("a", "interval", 1000.0)
        eng.tick(a.signal_id)
        eng.tick(a.signal_id)
        b = eng.create_signal("b", "interval", 1000.0)
        eng.tick(b.signal_id)
        eng.delete_signal(b.signal_id)  # 删行即删其 tick 计数（诚实边界）

        eng2 = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        eng2.list_signals()  # 首触回载
        # 在册信号 fire_count 合计=2（a 两次；b 已删不虚构）
        assert eng2._total_ticks == 2
        assert eng2.get_stats()["total_ticks"] == 2


# ── 纯内存零变化 + 故障旁路 ────────────────────────────────────────────────────


class TestPureMemoryAndFailures:
    def test纯内存零变化零落盘(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        eng = PulseEngine()
        sig = eng.create_signal("s", "interval", 1000.0, payload={"a": 1})
        eng.tick(sig.signal_id)
        eng.pause_signal(sig.signal_id)
        eng.resume_signal(sig.signal_id)
        eng.cancel_signal(sig.signal_id)
        assert eng.list_signals()[0].status == SignalStatus.CANCELLED
        assert eng.delete_signal(sig.signal_id)
        # 零落盘：显式 db_path 缺省+env 缺省下 tmp cwd 无库文件
        assert list(tmp_path.glob("*.db")) == []
        assert eng.get_stats()["total_signals"] == 0

    def test写失败旁路不阻断信号动作(self, tmp_path, caplog):
        p = _persist(tmp_path)

        def boom(row):
            raise RuntimeError("disk full")

        p.upsert = boom
        eng = PulseEngine(persist=p)
        with caplog.at_level("ERROR"):
            sig = eng.create_signal("s", "interval", 1000.0)
        assert sig.signal_id in eng._signals  # 动作本体不受阻
        assert any("落盘失败" in r.message for r in caplog.records)
        assert p.count() == 0  # 如实：该行只活内存

    def test回载失败空起步_下次触碰重试(self, tmp_path, caplog):
        db = tmp_path / "p.db"
        eng = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        eng.create_signal("seed", "interval", 1000.0)

        p2 = SignalPersistence(db_path=str(db))
        calls = []
        real = SignalPersistence.load_all

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("db locked")
            return real(p2)

        p2.load_all = flaky
        eng2 = PulseEngine(persist=p2)
        with caplog.at_level("ERROR"):
            assert eng2.list_signals() == []  # 空起步
        assert any("回载失败" in r.message for r in caplog.records)
        assert eng2._loaded is False  # 不置位=下次触碰重试
        assert [s.name for s in eng2.list_signals()] == ["seed"]  # 修复后真回载
        assert eng2._loaded is True

    def test_env解析与显式路径优先(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PULSE_SIGNALS_DB", str(tmp_path / "env.db"))
        assert default_db_path() == str(tmp_path / "env.db")
        p = SignalPersistence(db_path=str(tmp_path / "explicit.db"))
        p.upsert(
            {
                "signal_id": "x",
                "signal_type": "tick",
                "name": "n",
                "interval_ms": 1.0,
                "status": "active",
                "created_at": 1.0,
            }
        )
        assert (tmp_path / "explicit.db").exists()
        assert not (tmp_path / "env.db").exists()  # 显式优先不碰 env 库


# ── 接线（API 全链=用户真实路径 + 静态接线锁）──────────────────────────────────


class TestWiring:
    def test_API全链_写穿回载_模拟重启后by_ID可见(self, tmp_path, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import src.api.pulse as pulse_api

        # 真 router + tmp 隔离引擎（gene_client 同款缝：进程内 app 挂真 router，
        # 数据面隔离；conftest 的 client fixture 打 live :8090 不适用本例）。
        eng = PulseEngine(persist=SignalPersistence(db_path=str(tmp_path / "api.db")))
        monkeypatch.setattr(pulse_api, "engine", eng)
        app = FastAPI()
        app.include_router(pulse_api.router, prefix="/api/pulse")
        with TestClient(app) as c:
            resp = c.post(
                "/api/pulse/signals",
                json={
                    "name": "e2e_sig",
                    "signal_type": "interval",
                    "interval_ms": 1000.0,
                    "payload": {"type": "health_check"},
                },
            )
            assert resp.status_code == 200
            sid = resp.json()["signal_id"]
            # 行真源在场（真 router→真引擎→写穿）
            assert eng._persist is not None
            rows = eng._persist.load_all()
            assert [r["signal_id"] for r in rows] == [sid]
            assert rows[0]["payload"] == {"type": "health_check"}
            # 模拟重启：内存清空懒回载标记复位（conftest 每用例复位同款）
            eng._signals.clear()
            eng._loaded = False
            resp2 = c.get(f"/api/pulse/signals/{sid}")
            assert resp2.status_code == 200
            assert resp2.json()["name"] == "e2e_sig"
            assert resp2.json()["payload"] == {"type": "health_check"}
            resp3 = c.get("/api/pulse/signals")
            assert resp3.json()["total"] == 1

    def test_bootstrap种子信号去重跨重启真生效(self, tmp_path):
        # bootstrap 语义：'if not any(s.name == "system_health_check" ...)' 去重
        db = tmp_path / "p.db"
        eng = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        eng.create_signal("system_health_check", "interval", 60000.0)
        eng2 = PulseEngine(persist=SignalPersistence(db_path=str(db)))
        existing = eng2.list_signals()
        assert any(s.name == "system_health_check" for s in existing)  # 重启后在册=不再重复建

    def test接线锁_单例带persist与静态标记(self):
        src = open("src/api/pulse.py", encoding="utf-8").read()
        assert "engine = PulseEngine(persist=SignalPersistence())" in src
        timer_src = open("src/pulse/timer.py", encoding="utf-8").read()
        assert "def _ensure_loaded(self) -> None:" in timer_src
        # 写穿单一点+删行单一点（禁散落写点）
        assert timer_src.count("self._persist.upsert(") == 1
        assert timer_src.count("self._persist.delete(") == 1
