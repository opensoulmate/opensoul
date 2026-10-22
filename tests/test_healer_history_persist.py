"""RV-①b-⑬ healer OrganHealer._history 修复史持久化——契约测试。

覆盖：行语义（状态快照 upsert/往返/只读不建库/计数键形）、写穿（diagnose 落行逐键
parity/heal 终值随影/mark_recovered 随影/写穿单一点静态锁）、重启回载（双实例逐键
parity/先载后录懒回载恰一次/ring 窗语义/坏行跳过可见）、计数真源（对账行聚合/对账
失败回落禁假 0）、故障旁路（写失败不阻断诊断本体/回载失败空起步）、纯内存零变化、
接线锁、API 全链。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

from src.healer.diagnose import Action, DiagnosisResult, OrganHealer, Severity
from src.healer.history_persist import HealerPersistence

DB = "healer_history.db"

STATS_KEYS = {
    "total_diagnoses",
    "healed",
    "failed",
    "actions_taken",
    "actions_succeeded",
    "success_rate",
    "organ_failure_frequency",
    "recent_healthy_rate",
}

HISTORY_KEYS = {
    "organ",
    "healthy",
    "severity",
    "symptoms",
    "root_cause",
    "action_taken",
    "action_success",
    "response_time_ms",
    "timestamp",
}

# 死端口=诊断恒 ConnectError 确定性（conftest healer_client 同款）
DEAD = "http://127.0.0.1:1"


def _persist(tmp_path, name: str = DB) -> HealerPersistence:
    return HealerPersistence(db_path=str(tmp_path / name))


def _healer(tmp_path, name: str = DB) -> OrganHealer:
    return OrganHealer(base_url=DEAD, persist=_persist(tmp_path, name))


def _diagnose(h: OrganHealer, organ: str = "probe") -> DiagnosisResult:
    return asyncio.run(h.diagnose(organ, "/api/never"))


def _row(tmp_path, entry_id: str, name: str = DB) -> dict | None:
    if not (tmp_path / name).exists():
        return None
    with sqlite3.connect(tmp_path / name) as conn:
        conn.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in conn.execute("SELECT * FROM healer_history WHERE entry_id = ?", (entry_id,))
        ]
    return rows[0] if rows else None


def _row_count(tmp_path, name: str = DB) -> int:
    if not (tmp_path / name).exists():
        return 0
    with sqlite3.connect(tmp_path / name) as conn:
        return conn.execute("SELECT COUNT(*) FROM healer_history").fetchone()[0]


def _seed(p: HealerPersistence, entry_id: str, **over) -> None:
    kw = dict(
        entry_id=entry_id,
        organ="vein",
        healthy=False,
        severity="warning",
        symptoms=["timeout"],
        root_cause="health check timed out",
        recommended_action="clear_cache",
        action_taken="none",
        action_success=False,
        response_time_ms=12.5,
        timestamp=1729000000.0,
    )
    kw.update(over)
    p.upsert_result(**kw)  # pyright: ignore[reportArgumentType]  # kwargs 形测试种子


# ── 行语义（persist 层）────────────────────────────────────────────────────────


def test_行往返逐键含entry_id与symptoms_json(tmp_path):
    p = _persist(tmp_path)
    p.upsert_result(
        entry_id="abc12345def6",
        organ="vein",
        healthy=False,
        severity="critical",
        symptoms=["connection refused", "HTTP 500"],
        root_cause="service not running",
        recommended_action="none",
        action_taken="clear_cache",
        action_success=True,
        response_time_ms=3.2,
        timestamp=1729000000.5,
    )
    rows = p.load_history(10)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {
        "entry_id",
        "organ",
        "healthy",
        "severity",
        "symptoms",
        "root_cause",
        "recommended_action",
        "action_taken",
        "action_success",
        "response_time_ms",
        "timestamp",
    }
    assert row["entry_id"] == "abc12345def6"
    assert row["organ"] == "vein"
    assert row["healthy"] is False
    assert row["severity"] == "critical"
    assert json.loads(row["symptoms"]) == ["connection refused", "HTTP 500"]  # JSON 原文回载
    assert row["root_cause"] == "service not running"
    assert row["recommended_action"] == "none"
    assert row["action_taken"] == "clear_cache"
    assert row["action_success"] is True
    assert row["response_time_ms"] == 3.2
    assert row["timestamp"] == 1729000000.5


def test_upsert同entry随终值更新异entry各存禁覆盖(tmp_path):
    p = _persist(tmp_path)
    _seed(p, "e1", severity="warning", action_taken="none")
    _seed(p, "e1", severity="recovered", action_taken="clear_cache", action_success=True)
    _seed(p, "e2")
    rows = p.load_history(10)
    assert len(rows) == 2  # 异 entry 各存=不丢史
    by_id = {r["entry_id"]: r for r in rows}
    assert by_id["e1"]["severity"] == "recovered"  # 同 entry 随终值更新
    assert by_id["e1"]["action_taken"] == "clear_cache"
    assert by_id["e1"]["action_success"] is True
    assert by_id["e2"]["severity"] == "warning"
    assert _row_count(tmp_path) == 2


def test_回载只读不建库(tmp_path):
    p = _persist(tmp_path)
    assert p.load_history(10) == []
    assert p.counters()["total_diagnoses"] == 0
    assert p.row_count() == 0
    assert not (tmp_path / DB).exists()  # 构造/读零 IO，写穿才建库


def test_计数行聚合键形同stats(tmp_path):
    p = _persist(tmp_path)
    _seed(p, "c1", healthy=True, severity="info", action_taken="none")
    _seed(p, "c2", severity="recovered", action_taken="clear_cache", action_success=True)
    _seed(p, "c3", organ="mirror", severity="critical", action_taken="none")
    _seed(p, "c4", organ="mirror", severity="warning", action_taken="cleanup", action_success=False)
    c = p.counters()
    assert set(c) == STATS_KEYS  # 单一形状=OrganHealer.stats() 同键形
    assert set(OrganHealer(base_url=DEAD).stats()) == STATS_KEYS
    assert c["total_diagnoses"] == 4
    assert c["healed"] == 1
    assert c["failed"] == 2  # c3 + c4（非 recovered 的不健康行）
    assert c["actions_taken"] == 2
    assert c["actions_succeeded"] == 1
    assert c["success_rate"] == 50.0
    assert c["organ_failure_frequency"] == {"mirror": 2, "vein": 1}
    assert c["recent_healthy_rate"] == 25.0


# ── 写穿（healer 侧）──────────────────────────────────────────────────────────


def test_diagnose落行与内存逐键parity(tmp_path):
    h = _healer(tmp_path)
    r = _diagnose(h)
    assert not r.healthy and r.severity == Severity.CRITICAL  # 死端口确定性
    row = _row(tmp_path, r.entry_id)
    assert row is not None
    assert row["organ"] == r.organ
    assert bool(row["healthy"]) == r.healthy
    assert row["severity"] == r.severity.value
    assert json.loads(row["symptoms"]) == r.symptoms
    assert row["root_cause"] == r.root_cause
    assert row["recommended_action"] == r.recommended_action.value
    assert row["action_taken"] == r.action_taken.value
    assert bool(row["action_success"]) == r.action_success
    assert row["response_time_ms"] == r.response_time_ms
    assert row["timestamp"] == r.timestamp
    assert _row_count(tmp_path) == 1


def test_heal终值随影落行(tmp_path):
    h = _healer(tmp_path)

    async def ok():
        return True

    h.register_handler("probe", ok)  # 自定义处理器=RESTART 真实实现
    r = _diagnose(h)
    assert r.recommended_action == Action.RESTART
    r2 = asyncio.run(h.heal(r))
    assert r2.action_taken == Action.CUSTOM and r2.action_success
    assert r2.severity == Severity.RECOVERED
    row = _row(tmp_path, r.entry_id)
    assert row is not None
    assert row["severity"] == "recovered"  # 行随影内存终值
    assert row["action_taken"] == "custom"
    assert bool(row["action_success"]) is True
    assert _row_count(tmp_path) == 1  # upsert 同 entry 不增行


def test_mark_recovered终值随影落行(tmp_path):
    h = _healer(tmp_path)
    r = _diagnose(h)
    out = h.mark_recovered(r)
    assert out is r and out.healthy and out.severity == Severity.RECOVERED
    row = _row(tmp_path, r.entry_id)
    assert row is not None
    assert row["severity"] == "recovered" and bool(row["healthy"]) is True
    assert _row_count(tmp_path) == 1


def test_写穿单一点静态锁():
    src = open("src/healer/diagnose.py", encoding="utf-8").read()
    assert src.count("self._sync_result(") == 5  # _record 1 + heal 3 + mark_recovered 1
    asrc = open("src/api/healer.py", encoding="utf-8").read()
    assert asrc.count("healer.mark_recovered(") == 2  # heal_all/cycle 收编随影口
    assert "healthy = True" not in asrc  # api 层禁散落直改字段致行与内存漂移


# ── 重启回载（双实例=重启语义）────────────────────────────────────────────────


def test_双实例回载逐键parity与跨重启随影(tmp_path):
    p = _persist(tmp_path)
    h1 = OrganHealer(base_url=DEAD, persist=p)
    r1 = _diagnose(h1, "alpha")
    r2 = _diagnose(h1, "beta")
    h2 = OrganHealer(base_url=DEAD, persist=p)  # 重启语义
    got = h2.history
    assert [e["organ"] for e in got] == ["alpha", "beta"]  # rowid 升序=ring 回填序
    assert got[0]["timestamp"] == r1.timestamp
    assert got[0]["root_cause"] == r1.root_cause
    assert got[1]["symptoms"] == r2.symptoms
    # 跨重启随影：回载的 entry_id 保真，终值更新同一行不增行
    loaded_first = h2._history[0]
    assert loaded_first.entry_id == r1.entry_id
    h2.mark_recovered(loaded_first)
    assert _row_count(tmp_path) == 2
    row1, row2 = _row(tmp_path, r1.entry_id), _row(tmp_path, r2.entry_id)
    assert row1 is not None and row2 is not None
    assert row1["severity"] == "recovered"
    assert row2["severity"] != "recovered"


def test_先载后录懒回载恰一次(tmp_path, monkeypatch):
    p = _persist(tmp_path)
    h = OrganHealer(base_url=DEAD, persist=p)
    calls: list[int] = []
    orig = p.load_history

    def counting(limit=500):
        calls.append(1)
        return orig(limit)

    monkeypatch.setattr(p, "load_history", counting)
    _diagnose(h, "first")  # 首触先载后录
    _diagnose(h, "second")
    assert len(calls) == 1  # 懒回载恰一次
    assert [e["organ"] for e in h.history] == ["first", "second"]  # 新录不被回载冲掉


def test_回载窗ring语义只取最近N行(tmp_path):
    p = _persist(tmp_path)
    for i in range(5):
        _seed(p, f"e{i}", organ=f"organ{i}")
    h = OrganHealer(base_url=DEAD, persist=p)
    h._max_history = 3  # ring 窗语义=只回填最近 N，更旧行留库不进内存
    assert [e["organ"] for e in h.history] == ["organ2", "organ3", "organ4"]
    assert p.row_count() == 5  # 裁剪不裁行


def test_坏行跳过可见不丢整段史(tmp_path, caplog):
    p = _persist(tmp_path)
    _seed(p, "good1", organ="goodone")
    with sqlite3.connect(tmp_path / DB) as conn:
        conn.execute(
            "INSERT INTO healer_history (entry_id, organ, healthy, severity, symptoms,"
            " root_cause, recommended_action, action_taken, action_success,"
            " response_time_ms, timestamp) VALUES ('badsev', 'x', 0, 'bogus_sev', '[]',"
            " '', 'none', 'none', 0, 0.0, 0.0)"
        )
        conn.execute(
            "INSERT INTO healer_history (entry_id, organ, healthy, severity, symptoms,"
            " root_cause, recommended_action, action_taken, action_success,"
            " response_time_ms, timestamp) VALUES ('badjson', 'y', 0, 'info', 'not-json',"
            " '', 'none', 'none', 0, 0.0, 0.0)"
        )
        conn.commit()
    _seed(p, "good2", organ="goodtwo")
    h = OrganHealer(base_url=DEAD, persist=p)
    with caplog.at_level(logging.ERROR):
        got = h.history
    assert [e["organ"] for e in got] == ["goodone", "goodtwo"]  # 好行保住
    assert sum(1 for r in caplog.records if "坏行跳过" in r.message) == 2  # 跳过必留真因


# ── 计数真源（MEM-⑥）──────────────────────────────────────────────────────────


def test_计数恒对账行聚合不信内存(tmp_path):
    h = _healer(tmp_path)
    _diagnose(h)
    h._history.append(  # 内存被污染（未随影）
        DiagnosisResult(organ="fake", healthy=False, severity=Severity.CRITICAL)
    )
    st = h.stats()
    assert st["total_diagnoses"] == 1  # 行真源
    assert st["failed"] == 1
    assert st["organ_failure_frequency"] == {"probe": 1}


def test_对账失败回落内存计数禁假0(tmp_path, monkeypatch, caplog):
    h = _healer(tmp_path)
    _diagnose(h)
    monkeypatch.setattr(
        h._persist,
        "counters",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    with caplog.at_level(logging.ERROR):
        st = h.stats()
    assert st["total_diagnoses"] == 1  # 回落内存计算（本进程已录条目），禁静默假 0
    assert any("计数对账失败" in r.message for r in caplog.records)


# ── 纯内存/故障旁路 ────────────────────────────────────────────────────────────


def test_纯内存零变化语义字节不变(tmp_path):
    h = OrganHealer(base_url=DEAD)  # persist 缺省
    r = _diagnose(h)
    assert set(h.stats()) == STATS_KEYS
    entry = h.history[0]
    assert set(entry) == HISTORY_KEYS  # 响应面 9 键不变（entry_id 不出线）
    assert entry["organ"] == "probe" and entry["severity"] == "critical"
    assert not (tmp_path / DB).exists()  # 零落盘口
    assert r.entry_id  # 加性字段不影响既有构造面


def test_写失败不阻断诊断本体可见(tmp_path, monkeypatch, caplog):
    h = _healer(tmp_path)
    monkeypatch.setattr(
        h._persist,
        "upsert_result",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("disk full")),
    )
    with caplog.at_level(logging.ERROR):
        r = _diagnose(h)
    assert not r.healthy  # 诊断本体不被落盘失败炸掉
    assert len(h.history) == 1  # 记录本体已在内存
    assert any("落盘失败" in r2.message for r2 in caplog.records)


def test_回载失败空史起步可见(tmp_path, monkeypatch, caplog):
    h = _healer(tmp_path)
    monkeypatch.setattr(
        h._persist,
        "load_history",
        lambda limit=0: (_ for _ in ()).throw(RuntimeError("db gone")),
    )
    with caplog.at_level(logging.ERROR):
        assert h.history == []
    assert any("回载失败" in r.message for r in caplog.records)
    assert h._history_loaded  # 失败也置位=下次触碰不反复炸


# ── 接线锁（AST/静态）+ API 全链 ──────────────────────────────────────────────


def test_生产单例真接persist与env锁():
    src = open("src/api/healer.py", encoding="utf-8").read()
    assert "OrganHealer(persist=HealerPersistence())" in src
    psrc = open("src/healer/history_persist.py", encoding="utf-8").read()
    assert "HEALER_HISTORY_DB" in psrc  # 测试隔离 env 单一源
    import src.api.healer as healer_api

    assert isinstance(healer_api.healer._persist, HealerPersistence)


def test_API全链diagnose落行与history读面(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.healer as healer_api

    monkeypatch.setattr(healer_api, "push_event", lambda *a, **k: None)  # 事件面桩
    fresh = OrganHealer(base_url=DEAD, persist=_persist(tmp_path))
    monkeypatch.setattr(healer_api, "healer", fresh)
    app = FastAPI()
    app.include_router(healer_api.router, prefix="/api/healer")
    with TestClient(app) as c:
        s = c.post("/api/healer/diagnose/soul")
        assert s.status_code == 200
        body = s.json()
        assert set(body) == HISTORY_KEYS | {"recommended_action"}  # 响应面键集不变
        assert body["organ"] == "soul" and body["healthy"] is False
        s = c.get("/api/healer/history?limit=50")
        assert s.status_code == 200
        got = s.json()
        assert got["total"] == 1 and got["history"][0]["organ"] == "soul"
        s = c.get("/api/healer/stats")
        assert s.status_code == 200
        st = s.json()
        assert st["total_diagnoses"] == 1 and st["failed"] == 1
        assert set(st) >= STATS_KEYS
    assert _row_count(tmp_path) == 1  # 单例 persist 真落行（tmp 真源，绝不写生产库）
