"""RV-①b-⑪ immune IntrusionDetector 威胁史+爆破证据窗持久化——契约测试。

覆盖：行语义（追加型/往返/坏行/只读不建库/per-IP 窗）、写穿（检出落行含 auto-block
终值/登录尝试逐次落行）、重启回载（ring 窗回填/先载后录/懒回载恰一次/爆破证据窗
跨实例续算）、计数真源（对账行聚合/对账失败回落禁假 0）、故障旁路（写失败不阻断
检测本体/回载失败空起步）、纯内存零变化、AST 接线锁、API 全链。
"""

from __future__ import annotations

import logging
import sqlite3

from src.immune.intrusion import IntrusionDetector
from src.immune.threat_persist import IntrusionPersistence

DB = "intrusion.db"

# 恰命中 1 条 SQLI 模式（hex-encoded injection）——单威胁确定性用例用
HEX_BODY = "0x41"


def _persist(tmp_path, name: str = DB) -> IntrusionPersistence:
    return IntrusionPersistence(db_path=str(tmp_path / name))


def _detector(tmp_path, name: str = DB) -> IntrusionDetector:
    return IntrusionDetector(persist=_persist(tmp_path, name))


def _inspect(det: IntrusionDetector, ip: str = "192.0.2.1", body: str = HEX_BODY):
    return det.inspect_request(ip=ip, method="GET", path="/api/t", body=body)


def _threat_rows(tmp_path, name: str = DB):
    with sqlite3.connect(tmp_path / name) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM intrusion_threats ORDER BY row_id")]


# ── 行语义（persist 层）────────────────────────────────────────────────────────


def test_威胁行往返逐键含blocked与pattern(tmp_path):
    p = _persist(tmp_path)
    p.insert_threat(
        threat_id="abc12345",
        attack_type="sql_injection",
        threat_level="critical",
        source_ip="10.0.0.1",
        path="/api/x",
        method="POST",
        detail="Tautology injection",
        matched_pattern="(?i)x",
        blocked=True,
        timestamp=1729000000.5,
    )
    rows = p.load_threats(10)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {
        "threat_id",
        "attack_type",
        "threat_level",
        "source_ip",
        "path",
        "method",
        "detail",
        "matched_pattern",
        "blocked",
        "timestamp",
    }
    assert row == {
        "threat_id": "abc12345",
        "attack_type": "sql_injection",
        "threat_level": "critical",
        "source_ip": "10.0.0.1",
        "path": "/api/x",
        "method": "POST",
        "detail": "Tautology injection",
        "matched_pattern": "(?i)x",
        "blocked": True,
        "timestamp": 1729000000.5,
    }


def test_追加型两写两行禁覆盖(tmp_path):
    p = _persist(tmp_path)
    for i in range(2):
        p.insert_threat(
            threat_id="dup00001",
            attack_type="xss",
            threat_level="high",
            source_ip="10.0.0.2",
            path=f"/api/{i}",
            method="GET",
            detail=f"e{i}",
            matched_pattern="",
            blocked=False,
            timestamp=float(i),
        )
    assert p.threat_count() == 2  # 同 threat_id 两行（8 位截断非自然主键，禁 REPLACE 丢史）


def test_登录行往返与per_ip窗互不混(tmp_path):
    p = _persist(tmp_path)
    for i in range(4):
        p.insert_login(ip="10.0.0.1", ts=float(i))
    p.insert_login(ip="10.0.0.2", ts=99.0)
    data = p.load_login_attempts(per_ip_limit=2)
    assert data == {"10.0.0.1": [2.0, 3.0], "10.0.0.2": [99.0]}  # 每 IP 最近 2 条升序、不混 IP
    assert p.login_count("10.0.0.1") == 4
    assert p.login_count() == 5


def test_回载只读不建库(tmp_path):
    p = _persist(tmp_path, name="never_written.db")
    assert p.load_threats(10) == []
    assert p.load_login_attempts(100) == {}
    assert p.threat_counters() == {"total_threats": 0, "by_type": {}, "by_level": {}}
    assert not (tmp_path / "never_written.db").exists()


def test_计数行聚合键形同内存_stats(tmp_path):
    p = _persist(tmp_path)
    for at, lv in (("sql_injection", "critical"), ("sql_injection", "critical"), ("xss", "high")):
        p.insert_threat(
            threat_id=at + lv,
            attack_type=at,
            threat_level=lv,
            source_ip="10.0.0.3",
            path="/api/t",
            method="GET",
            detail="d",
            matched_pattern="",
            blocked=False,
            timestamp=0.0,
        )
    assert p.threat_counters() == {
        "total_threats": 3,
        "by_type": {"sql_injection": 2, "xss": 1},
        "by_level": {"critical": 2, "high": 1},
    }


# ── 写穿（detector 侧）────────────────────────────────────────────────────────


def test_inspect检出落行与内存逐键parity(tmp_path):
    det = _detector(tmp_path)
    threats = _inspect(det)
    assert len(threats) == 1  # HEX_BODY 恰 1 模式
    rows = _threat_rows(tmp_path)
    assert len(rows) == 1
    mem = det.get_threats()[0]
    row = rows[0]
    assert row["threat_id"] == mem["threat_id"]
    assert row["attack_type"] == mem["attack_type"] == "sql_injection"
    assert row["threat_level"] == mem["threat_level"] == "critical"
    assert row["source_ip"] == mem["source_ip"] == "192.0.2.1"
    assert row["path"] == mem["path"] == "/api/t"
    assert row["method"] == mem["method"] == "GET"
    assert row["detail"] == mem["detail"]
    assert row["matched_pattern"] == mem["matched_pattern"]
    assert bool(row["blocked"]) == mem["blocked"] is False
    assert float(row["timestamp"]) == mem["timestamp"]


def test_auto_block终值落行blocked随判定更新(tmp_path):
    det = _detector(tmp_path)
    for _ in range(5):  # AUTO_BLOCK_THRESHOLD=5，第 5 次批内触发 auto-block
        _inspect(det)
    rows = _threat_rows(tmp_path)
    assert len(rows) == 5
    assert [bool(r["blocked"]) for r in rows] == [False, False, False, False, True]
    # 内存面终值一致（行=内存逐键保真）
    mem_blocked = [t["blocked"] for t in det.get_threats()]
    assert sorted(mem_blocked) == [False, False, False, False, True]


def test_登录尝试逐次落行成败都记(tmp_path):
    det = _detector(tmp_path)
    for ok in (True, False, True):
        det.record_login_attempt("192.0.2.9", success=ok)
    p = _persist(tmp_path)
    assert p.login_count("192.0.2.9") == 3  # 内存 _login_attempts 只存时间戳=成败都记


def test_写穿单一点静态锁():
    src = open("src/immune/intrusion.py", encoding="utf-8").read()
    assert src.count("self._sync_threats(") == 1  # 调用恰 1（禁散落写点）
    assert src.count("self._sync_login(") == 1


# ── 重启回载（双实例=重启语义）────────────────────────────────────────────────


def test_双实例回载逐键parity(tmp_path):
    d1 = _detector(tmp_path)
    _inspect(d1, body="0x41")
    _inspect(d1, body="0x42")
    d2 = _detector(tmp_path)
    assert d2.get_threats() == d1.get_threats()  # 逐键含 threat_id/timestamp
    assert len(d2.get_threats()) == 2


def test_爆破证据窗跨重启续算诱导重启不清计数(tmp_path):
    d1 = _detector(tmp_path)
    for _ in range(9):  # 阈值 10，未触发
        assert d1.record_login_attempt("192.0.2.8", success=False) is None
    d2 = _detector(tmp_path)  # 重启
    threat = d2.record_login_attempt("192.0.2.8", success=False)  # 第 10 次
    assert threat is not None
    assert "Brute force" in threat.detail  # 证据窗跨重启续算（修前：计数被重启清零→不触发）


def test_先载后录懒回载恰一次(tmp_path, monkeypatch):
    p = _persist(tmp_path)
    d1 = _detector(tmp_path)
    _inspect(d1)  # 预置 1 行
    calls = {"n": 0}
    orig = p.load_threats

    def counting(limit=10000):
        calls["n"] += 1
        return orig(limit)

    monkeypatch.setattr(p, "load_threats", counting)
    d2 = IntrusionDetector(persist=p)
    _inspect(d2)  # 首触=先载后录
    _inspect(d2)
    assert len(d2.get_threats()) == 3  # 预置行不被冲掉 + 新录 2 行
    assert calls["n"] == 1  # 懒回载恰一次


def test_回载窗ring语义只取最近N行(tmp_path, monkeypatch):
    monkeypatch.setattr(IntrusionDetector, "MAX_THREAT_HISTORY", 3)
    d1 = _detector(tmp_path)
    ids = []
    for i in range(5):
        ids.append(_inspect(d1, body=f"0x4{i}")[0].threat_id)
    d2 = _detector(tmp_path)
    got = d2.get_threats()
    assert len(got) == 3  # 旧行留库不进内存（ring 既有裁剪语义）
    # 序=ring 回填序（等刻时间戳稳定序），d1/d2 两面逐键一致；内容=恰最近 3 条
    assert [t["threat_id"] for t in got] == [t["threat_id"] for t in d1.get_threats()]
    assert {t["threat_id"] for t in got} == set(ids[-3:])


def test_坏行跳过可见不丢整段史(tmp_path, caplog):
    _persist(tmp_path)
    d1 = _detector(tmp_path)
    _inspect(d1)  # 1 好行
    with sqlite3.connect(tmp_path / DB) as conn:
        conn.execute(
            "INSERT INTO intrusion_threats (threat_id, attack_type, threat_level, source_ip,"
            " path, method, detail, matched_pattern, blocked, timestamp)"
            " VALUES ('bad00001', 'bogus_type', 'critical', '10.0.0.9', '/x', 'GET', 'bad', '', 0, 0.0)"
        )
        conn.commit()
    d2 = _detector(tmp_path)
    with caplog.at_level(logging.ERROR):
        got = d2.get_threats()
    assert len(got) == 1  # 好行保住
    assert any("坏行跳过" in r.message for r in caplog.records)


# ── 计数真源（MEM-⑥）──────────────────────────────────────────────────────────


def test_计数恒对账行聚合不信内存缓存(tmp_path):
    det = _detector(tmp_path)
    _inspect(det)
    det._stats["total_threats"] = 99  # 内存缓存被污染
    det._stats["by_type"]["fake"] = 7
    st = det.stats()
    assert st["total_threats"] == 1  # 行真源
    assert st["by_type"] == {"sql_injection": 1}
    assert st["by_level"] == {"critical": 1}


def test_对账失败回落内存计数禁假0(tmp_path, monkeypatch, caplog):
    det = _detector(tmp_path)
    _inspect(det)
    monkeypatch.setattr(
        det._persist, "threat_counters", lambda: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    with caplog.at_level(logging.ERROR):
        st = det.stats()
    assert st["total_threats"] == 1  # 回落内存缓存，禁静默假 0
    assert any("计数对账失败" in r.message for r in caplog.records)


# ── 纯内存/故障旁路 ────────────────────────────────────────────────────────────


def test_纯内存零变化语义字节不变(tmp_path):
    det = IntrusionDetector()  # persist 缺省
    threats = _inspect(det)
    assert len(threats) == 1
    st = det.stats()
    assert set(st) == {
        "total_inspected",
        "total_threats",
        "total_blocked",
        "active_blocks",
        "suspicious_ips",
        "tracked_ips",
        "by_type",
        "by_level",
        "recent_threats",
        "thresholds",
    }
    assert st["total_threats"] == 1
    assert st["by_type"] == {"sql_injection": 1}
    assert len(det.get_threats()) == 1
    det.record_login_attempt("192.0.2.5", success=False)
    assert not (tmp_path / DB).exists()  # 零落盘口


def test_写失败不阻断检测本体可见(tmp_path, monkeypatch, caplog):
    det = _detector(tmp_path)
    monkeypatch.setattr(
        det._persist, "insert_threat", lambda **kw: (_ for _ in ()).throw(RuntimeError("disk full"))
    )
    monkeypatch.setattr(
        det._persist, "insert_login", lambda **kw: (_ for _ in ()).throw(RuntimeError("disk full"))
    )
    with caplog.at_level(logging.ERROR):
        threats = _inspect(det)
        det.record_login_attempt("192.0.2.6", success=False)
    assert len(threats) == 1  # 检测本体不被落盘失败炸掉
    assert len(det.get_threats()) == 1  # 记录本体已在内存
    assert any("落盘失败" in r.message for r in caplog.records)


def test_回载失败空史起步可见(tmp_path, monkeypatch, caplog):
    det = _detector(tmp_path)
    monkeypatch.setattr(
        det._persist, "load_threats", lambda limit=0: (_ for _ in ()).throw(RuntimeError("db gone"))
    )
    monkeypatch.setattr(
        det._persist,
        "load_login_attempts",
        lambda n=0: (_ for _ in ()).throw(RuntimeError("db gone")),
    )
    with caplog.at_level(logging.ERROR):
        assert det.get_threats() == []
        assert det.record_login_attempt("192.0.2.7", success=False) is None
    assert any("回载失败" in r.message for r in caplog.records)
    assert det._threats_loaded and det._logins_loaded  # 失败也置位=下次触碰不反复炸


# ── 接线锁（AST/静态）+ API 全链 ──────────────────────────────────────────────


def test_生产单例真接persist与env锁():
    src = open("src/api/immune.py", encoding="utf-8").read()
    assert "intrusion = IntrusionDetector(persist=" in src
    psrc = open("src/immune/threat_persist.py", encoding="utf-8").read()
    assert "IMMUNE_INTRUSION_DB" in psrc  # 测试隔离 env 单一源
    import src.api.immune as immune_api

    assert isinstance(immune_api.intrusion._persist, IntrusionPersistence)


def test_API全链inspect检出落行与threats读面(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.immune as immune_api

    monkeypatch.setattr(
        immune_api, "push_event", lambda *a, **k: None
    )  # 事件面桩（不打生产 timeline）
    import src.api.notifications as notif_mod

    monkeypatch.setattr(notif_mod, "push_notification", lambda **kw: None)  # 通知面桩
    app = FastAPI()
    app.include_router(immune_api.router, prefix="/api/immune")
    with TestClient(app) as c:
        s = c.post(
            "/api/immune/intrusion/inspect",
            json={"ip": "192.0.2.10", "method": "GET", "path": "/api/t", "body": HEX_BODY},
        )
        assert s.status_code == 200
        assert s.json()["threats_found"] == 1
        s = c.get("/api/immune/intrusion/threats?ip=192.0.2.10&limit=50")
        assert s.status_code == 200
        got = s.json()["threats"]
        assert len(got) == 1
        assert got[0]["attack_type"] == "sql_injection"
    # 单例 persist 真落行（env=autouse tmp 真源 immune_intrusion.db，绝不写生产库）
    with sqlite3.connect(tmp_path / "immune_intrusion.db") as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM intrusion_threats WHERE source_ip = '192.0.2.10'"
        ).fetchone()[0]
    assert n == 1
