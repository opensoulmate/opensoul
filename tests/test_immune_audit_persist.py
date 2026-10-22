"""RV-①b-⑧ immune AuditLogger._log 安全审计留痕持久化——契约测试。

覆盖：行语义（追加型/往返/坏行/只读不建库）、写穿、重启回载（ring 窗回填/先载
后录/懒回载恰一次）、计数真源（对账行聚合/cleanup 不回退/对账失败回落禁假 0）、
故障旁路（写失败不阻断/回载失败空起步）、纯内存零变化、AST 接线锁。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from src.immune.audit import AuditAction, AuditLogger
from src.immune.audit_persist import AuditPersistence

DB = "audit.db"


def _persist(tmp_path, name: str = DB) -> AuditPersistence:
    return AuditPersistence(db_path=str(tmp_path / name))


def _log_one(log: AuditLogger, detail: str = "d", **kw):
    kw.setdefault("action", AuditAction.API_CALL)
    kw.setdefault("risk_level", "low")
    log.log(kw.pop("action"), detail=detail, **kw)


# ── 行语义（persist 层）────────────────────────────────────────────────────────


def test_往返逐键含metadata(tmp_path):
    p = _persist(tmp_path)
    p.insert(
        timestamp=1729000000.5,
        action="access_denied",
        client_ip="10.0.0.1",
        user_id="u1",
        endpoint="/api/x",
        detail="denied 原因",
        risk_level="high",
        metadata={"k": "v", "n": 3},
    )
    rows = p.load_recent(10)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {
        "timestamp",
        "action",
        "client_ip",
        "user_id",
        "endpoint",
        "detail",
        "risk_level",
        "metadata",
    }
    assert row["timestamp"] == 1729000000.5
    assert row["action"] == "access_denied"
    assert row["client_ip"] == "10.0.0.1"
    assert row["user_id"] == "u1"
    assert row["endpoint"] == "/api/x"
    assert row["detail"] == "denied 原因"
    assert row["risk_level"] == "high"
    assert row["metadata"] == {"k": "v", "n": 3}


def test_追加型两写两行禁覆盖(tmp_path):
    p = _persist(tmp_path)
    for i in range(2):
        p.insert(
            timestamp=float(i),
            action="api_call",
            client_ip="",
            user_id="",
            endpoint="",
            detail=f"e{i}",
            risk_level="low",
            metadata={},
        )
    assert p.count() == 2
    assert [r["detail"] for r in p.load_recent(10)] == ["e0", "e1"]


def test_坏metadata行跳过可见禁静默丢(tmp_path, caplog):
    p = _persist(tmp_path)
    for i in range(3):
        p.insert(
            timestamp=float(i),
            action="api_call",
            client_ip="",
            user_id="",
            endpoint="",
            detail=f"e{i}",
            risk_level="low",
            metadata={},
        )
    with sqlite3.connect(str(tmp_path / DB)) as conn:
        conn.execute("UPDATE immune_audit_log SET metadata='not-json' WHERE detail='e1'")
        conn.commit()
    with caplog.at_level(logging.ERROR):
        rows = p.load_recent(10)
    assert [r["detail"] for r in rows] == ["e0", "e2"]  # 坏行跳过、好行不丢
    assert any("坏行" in rec.message for rec in caplog.records)


def test_回载只读不建库(tmp_path):
    p = _persist(tmp_path, "no_db_yet.db")
    assert p.load_recent(10) == []
    assert not (tmp_path / "no_db_yet.db").exists()
    assert p.count() == 0
    assert not (tmp_path / "no_db_yet.db").exists()


def test_计数聚合键形与内存同键(tmp_path):
    p = _persist(tmp_path)
    p.insert(
        timestamp=0.0,
        action="api_call",
        client_ip="",
        user_id="",
        endpoint="",
        detail="",
        risk_level="low",
        metadata={},
    )
    p.insert(
        timestamp=0.0,
        action="api_call",
        client_ip="",
        user_id="",
        endpoint="",
        detail="",
        risk_level="low",
        metadata={},
    )
    p.insert(
        timestamp=0.0,
        action="ip_blocked",
        client_ip="",
        user_id="",
        endpoint="",
        detail="",
        risk_level="high",
        metadata={},
    )
    assert p.counters() == {"api_call:low": 2, "ip_blocked:high": 1}
    # 与内存 _counters 同键形（单一形状禁两套）
    log = AuditLogger()
    _log_one(log)
    assert set(log._counters) == {"api_call:low"}


# ── 写穿+重启回载（AuditLogger 接线）──────────────────────────────────────────


def test_log写穿落行(tmp_path):
    p = _persist(tmp_path)
    log = AuditLogger(persist=p)
    _log_one(log, detail="first")
    log.log(AuditAction.ACCESS_DENIED, client_ip="1.2.3.4", detail="denied", risk_level="high")
    rows = p.load_recent(10)
    assert len(rows) == 2
    assert rows[0]["detail"] == "first"
    assert rows[1]["action"] == "access_denied"
    assert rows[1]["client_ip"] == "1.2.3.4"
    assert rows[1]["risk_level"] == "high"


def test_重启回载逐键parity(tmp_path):
    p = _persist(tmp_path)
    a = AuditLogger(persist=p)
    a.log(AuditAction.LOGIN, user_id="u1", detail="登录", metadata={"m": 1})
    _log_one(a, detail="mid")
    a.log(AuditAction.IP_BLOCKED, client_ip="9.9.9.9", detail="blocked", risk_level="high")
    b = AuditLogger(persist=p)
    got = b.query(limit=10)
    assert [e["detail"] for e in got] == ["blocked", "mid", "登录"]
    assert got[2]["user_id"] == "u1"
    assert got[2]["metadata"] == {"m": 1}
    assert got[0]["risk_level"] == "high"
    # asdict 往返键集一致
    assert set(got[0]) == {
        "timestamp",
        "action",
        "client_ip",
        "user_id",
        "endpoint",
        "detail",
        "risk_level",
        "metadata",
    }


def test_ring窗回填只取最近maxlen行(tmp_path):
    p = _persist(tmp_path)
    a = AuditLogger(max_entries=5, persist=p)
    for i in range(7):
        _log_one(a, detail=f"e{i}")
    assert p.count() == 7  # 全量落盘（ring 裁剪不裁行）
    b = AuditLogger(max_entries=5, persist=p)
    got = [e["detail"] for e in b.query(limit=10)]
    assert got == ["e6", "e5", "e4", "e3", "e2"]  # 最近 5 行=ring 回填窗


def test_先载后录懒回载恰一次(tmp_path, monkeypatch):
    p = _persist(tmp_path)
    p.insert(
        timestamp=1.0,
        action="api_call",
        client_ip="",
        user_id="",
        endpoint="",
        detail="seeded",
        risk_level="low",
        metadata={},
    )
    log = AuditLogger(persist=p)
    calls = []
    orig = p.load_recent

    def spy(limit=10000):
        calls.append(limit)
        return orig(limit)

    monkeypatch.setattr(p, "load_recent", spy)
    _log_one(log, detail="after")  # 首写先载后录
    log.query(limit=10)
    log.stats()
    assert len(calls) == 1  # 懒回载恰一次
    details = [e["detail"] for e in log.query(limit=10)]
    assert details == ["after", "seeded"]  # 先载（seeded）后录（after）不互冲


def test_未知action枚举行跳过可见(tmp_path, caplog):
    p = _persist(tmp_path)
    p.insert(
        timestamp=0.0,
        action="future_action",
        client_ip="",
        user_id="",
        endpoint="",
        detail="x",
        risk_level="low",
        metadata={},
    )
    p.insert(
        timestamp=1.0,
        action="api_call",
        client_ip="",
        user_id="",
        endpoint="",
        detail="ok",
        risk_level="low",
        metadata={},
    )
    with caplog.at_level(logging.ERROR):
        log = AuditLogger(persist=p)
        assert [e["detail"] for e in log.query(limit=10)] == ["ok"]
    assert any("坏行" in rec.message for rec in caplog.records)


# ── 计数真源（_counter_truth 对账行聚合）──────────────────────────────────────


def test_计数对账行聚合非内存缓存(tmp_path):
    p = _persist(tmp_path)
    log = AuditLogger(persist=p)
    log.log(AuditAction.API_CALL, detail="1")
    log.log(AuditAction.API_CALL, detail="2")
    log.log(AuditAction.IP_BLOCKED, detail="3", risk_level="high")
    assert log.stats()["counters"] == {"api_call:low": 2, "ip_blocked:high": 1}


def test_cleanup裁内存不回退计数不删行(tmp_path):
    p = _persist(tmp_path)
    log = AuditLogger(persist=p)
    for i in range(4):
        _log_one(log, detail=f"e{i}")
    out = log.cleanup(keep_last=1)
    assert out == {"dropped": 3, "remaining": 1, "keep_last": 1}
    # 契约保真：counters 是累计计数，不随裁剪回退
    assert log.stats()["counters"] == {"api_call:low": 4}
    # 审计史不可被 cleanup 抹除：行只增不减
    assert p.count() == 4


def test_对账失败回落内存计数禁假0(tmp_path, monkeypatch, caplog):
    p = _persist(tmp_path)
    log = AuditLogger(persist=p)
    _log_one(log)

    def boom():
        raise RuntimeError("db 对账故障")

    monkeypatch.setattr(p, "counters", boom)
    with caplog.at_level(logging.ERROR):
        counters = log.stats()["counters"]
    assert counters == {"api_call:low": 1}  # 回落内存缓存（禁静默假 0）
    assert any("对账失败" in rec.message for rec in caplog.records)


def test_纯内存零落盘语义字节不变(tmp_path, monkeypatch):
    monkeypatch.setenv("IMMUNE_AUDIT_DB", str(tmp_path / "must_not_exist.db"))
    log = AuditLogger()  # persist=None 既有语义
    _log_one(log, detail="mem")
    assert log.stats()["counters"] == {"api_call:low": 1}
    assert [e["detail"] for e in log.query(limit=5)] == ["mem"]
    log.cleanup(keep_last=0)
    assert not (tmp_path / "must_not_exist.db").exists()


# ── 故障旁路（写失败/回载失败不阻断审计本体）──────────────────────────────────


def test_写失败不阻断审计本体可见(tmp_path, monkeypatch, caplog):
    p = _persist(tmp_path)
    log = AuditLogger(persist=p)

    def boom(**kw):
        raise RuntimeError("磁盘满")

    monkeypatch.setattr(p, "insert", boom)
    with caplog.at_level(logging.ERROR):
        _log_one(log, detail="still logged")
    assert [e["detail"] for e in log.query(limit=5)] == ["still logged"]  # 内存留痕仍在
    assert any("落盘失败" in rec.message for rec in caplog.records)


def test_回载失败空审计起步可见(tmp_path, monkeypatch, caplog):
    p = _persist(tmp_path)
    p.insert(
        timestamp=0.0,
        action="api_call",
        client_ip="",
        user_id="",
        endpoint="",
        detail="seeded",
        risk_level="low",
        metadata={},
    )

    def boom(limit=10000):
        raise RuntimeError("库锁死")

    monkeypatch.setattr(p, "load_recent", boom)
    with caplog.at_level(logging.ERROR):
        log = AuditLogger(persist=p)
        assert log.query(limit=5) == []
    _log_one(log, detail="new")
    assert [e["detail"] for e in log.query(limit=5)] == ["new"]
    assert any("回载失败" in rec.message for rec in caplog.records)


# ── 接线锁（AST/静态）────────────────────────────────────────────────────────


def test_写穿单一点与生产接线锁():
    root = Path(__file__).resolve().parent.parent
    audit_src = (root / "src" / "immune" / "audit.py").read_text(encoding="utf-8")
    api_src = (root / "src" / "api" / "immune.py").read_text(encoding="utf-8")
    # 写穿单一点：_persist.insert 恰一处（禁散落写点漂移）
    assert audit_src.count("self._persist.insert(") == 1
    assert "def _sync_entry" in audit_src
    assert "def _ensure_loaded" in audit_src
    assert "def _counter_truth" in audit_src
    # 生产单例真接 persist（写了≠接线了）
    assert "from src.immune.audit_persist import AuditPersistence" in api_src
    assert "audit = AuditLogger(persist=AuditPersistence())" in api_src


def test_生产单例真接persist():
    import src.api.immune as immune_api

    assert isinstance(immune_api.audit._persist, AuditPersistence)
