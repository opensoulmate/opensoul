"""RV-①b-④ echo MessageDispatcher._history 持久化——写穿/懒回载/消息历史语义/真实路径接线。

修前（RV-① A 组定性）：_history 全在进程内存（deque maxlen=1000）——echo
/history /stats 真源（DEC-⑦-②/⑬/⑭ 加轴读面）随 service 重启归零。本文件锁死：
①send() 随写落盘行逐字段（含 failed 行=两个历史落点全收）②重启回载历史/stats
逐值一致+租户轴（DEC-⑦-⑬⑭）跨重启不丢 ③persist=None 纯内存既有语义零变化
④写/回载失败可见不阻断 ⑤msg_id 跨重启不撞（同秒）⑥回载 maxlen 语义+坏行跳过
⑦生产真实路径（api/echo dispatcher 单例）真接线+懒回载不建库。
"""

from __future__ import annotations

import sqlite3

import pytest


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT msg_id, channel, title, content, timestamp, status, error, target,"
            " priority, tenant_id FROM echo_history ORDER BY timestamp, rowid"
        ).fetchall()
    finally:
        conn.close()


def _persist(tmp_path):
    from src.echo.history_persist import HistoryPersistence

    return HistoryPersistence(str(tmp_path / "echo.db"))


def _dispatcher(tmp_path):
    from src.echo.dispatcher import MessageDispatcher

    return MessageDispatcher(persist=_persist(tmp_path))


# ── ① 写穿（send → DB 行）────────────────────────────────────────


def test_send_write_through_row_verbatim(tmp_path):
    """send() 随写落盘：DB 行与内存消息逐字段一致（channel 存枚举值）。"""
    from src.echo.dispatcher import Channel

    d = _dispatcher(tmp_path)
    r = d.send(Channel.CONSOLE, "标题", "内容", target="t1", priority=2, tenant_id="tenant-a")
    assert r.success is True
    (row,) = _rows(tmp_path / "echo.db")
    assert row[0] == r.msg_id and row[1] == "console"
    assert row[2] == "标题" and row[3] == "内容"
    assert isinstance(row[4], float) and row[4] > 0
    assert row[5] == "sent" and row[6] == "" and row[7] == "t1"
    assert row[8] == 2 and row[9] == "tenant-a"


def test_failed_channel_row_also_persisted(tmp_path):
    """未配置通道 failed 行同样落盘（历史落点单一点全收=两个 append 口收编）。"""
    from src.echo.dispatcher import Channel

    d = _dispatcher(tmp_path)
    r = d.send(Channel.WEBHOOK, "T", "C")  # webhook 未配置 → failed
    assert r.success is False
    (row,) = _rows(tmp_path / "echo.db")
    assert row[5] == "failed" and "not configured" in row[6]


def test_tenant_default_fallback_persisted(tmp_path):
    """tenant_id 空白→'default' 兜底落行（message_row_tenant 单一源写读同判）。"""
    from src.echo.dispatcher import Channel

    d = _dispatcher(tmp_path)
    d.send(Channel.CONSOLE, "T", "C", tenant_id="")
    (row,) = _rows(tmp_path / "echo.db")
    assert row[9] == "default"


# ── ② 重启回载（历史/stats 真源不丢）────────────────────────────


def test_restart_reload_history_and_stats(tmp_path):
    """新实例回载：3 行（2 sent 1 failed）历史/status 逐值一致，stats 归真。"""
    from src.echo.dispatcher import Channel

    d1 = _dispatcher(tmp_path)
    d1.send(Channel.CONSOLE, "T1", "C1")
    d1.send(Channel.CONSOLE, "T2", "C2")
    d1.send(Channel.WEBHOOK, "T3", "C3")  # failed

    d2 = _dispatcher(tmp_path)  # =重启
    hist = d2.history()
    assert [(h["title"], h["status"]) for h in hist] == [
        ("T3", "failed"),
        ("T2", "sent"),
        ("T1", "sent"),
    ]
    st = d2.stats()
    assert st["total_messages"] == 3 and st["sent"] == 2 and st["failed"] == 1


def test_content_persisted_full_view_truncates_as_before(tmp_path):
    """content 全文落盘；history() 读面 [:200] 截断语义逐字不变。"""
    from src.echo.dispatcher import Channel

    long_content = "长" * 500
    d1 = _dispatcher(tmp_path)
    d1.send(Channel.CONSOLE, "T", long_content)
    (row,) = _rows(tmp_path / "echo.db")
    assert row[3] == long_content  # 全文在库
    d2 = _dispatcher(tmp_path)
    (h,) = d2.history()
    assert h["content"] == long_content[:200]  # 读面截断既有语义不变


def test_tenant_axis_survives_restart(tmp_path):
    """租户轴（DEC-⑦-⑬⑭）跨重启不丢：行键回载→/history /stats 过滤照旧。"""
    from src.echo.dispatcher import Channel

    d1 = _dispatcher(tmp_path)
    d1.send(Channel.CONSOLE, "Ta", "Ca", tenant_id="tenant-a")
    d1.send(Channel.CONSOLE, "Tb", "Cb", tenant_id="tenant-b")

    d2 = _dispatcher(tmp_path)
    assert [h["title"] for h in d2.history(tenant_id="tenant-a")] == ["Ta"]
    assert [h["title"] for h in d2.history(tenant_id="tenant-b")] == ["Tb"]
    assert d2.stats(tenant_id="tenant-a")["total_messages"] == 1
    assert (h := d2.history(tenant_id="tenant-a")[0])["tenant_id"] == "tenant-a"


def test_restart_write_then_reload_merges(tmp_path):
    """重启后新发消息与旧行并存（回载补缺+新写落盘，两代历史同面）。"""
    from src.echo.dispatcher import Channel

    d1 = _dispatcher(tmp_path)
    d1.send(Channel.CONSOLE, "旧", "C")
    d2 = _dispatcher(tmp_path)
    d2.send(Channel.CONSOLE, "新", "C")
    d3 = _dispatcher(tmp_path)
    assert {h["title"] for h in d3.history()} == {"旧", "新"}
    assert d3.stats()["total_messages"] == 2


# ── ③ persist=None 零变化 ────────────────────────────────────────


def test_persist_none_zero_change(tmp_path, monkeypatch):
    """persist 缺省=纯内存既有语义零变化：不落盘、历史/stats 照常。"""
    from src.echo.dispatcher import Channel, MessageDispatcher

    monkeypatch.setenv("ECHO_HISTORY_DB", str(tmp_path / "never.db"))
    d = MessageDispatcher()
    d.send(Channel.CONSOLE, "T", "C")
    assert len(d.history()) == 1
    assert d.stats()["total_messages"] == 1
    assert not (tmp_path / "never.db").exists()


# ── ④ 失败可见不阻断 ────────────────────────────────────────────


def test_write_failure_visible_not_blocking(tmp_path, monkeypatch, caplog):
    """落盘失败不阻断派发：send 成功+内存历史完好+logger.error 可见（非静默）。"""
    import logging

    from src.echo.dispatcher import Channel

    d = _dispatcher(tmp_path)
    monkeypatch.setattr(
        d._persist, "insert", lambda **kw: (_ for _ in ()).throw(OSError("disk full"))
    )
    with caplog.at_level(logging.ERROR, logger="src.echo.dispatcher"):
        r = d.send(Channel.CONSOLE, "T", "C")
    assert r.success is True
    assert [h["title"] for h in d.history()] == ["T"]
    assert any("persist failed" in rec.getMessage() for rec in caplog.records)


def test_reload_failure_empty_start_visible(tmp_path, monkeypatch, caplog):
    """回载失败=空历史起步+可见，不炸构造/读写（行真源仍在库下次启动重试）。"""
    import logging

    from src.echo.dispatcher import Channel

    d1 = _dispatcher(tmp_path)
    d1.send(Channel.CONSOLE, "T", "C")

    d2 = _dispatcher(tmp_path)
    monkeypatch.setattr(
        d2._persist, "load_recent", lambda limit=1000: (_ for _ in ()).throw(OSError("db locked"))
    )
    with caplog.at_level(logging.ERROR, logger="src.echo.dispatcher"):
        assert d2.history() == []
        d2.send(Channel.CONSOLE, "T2", "C2")  # 读写照常不炸
    assert any("reload failed" in rec.getMessage() for rec in caplog.records)
    assert [h["title"] for h in d2.history()] == ["T2"]


# ── ⑤ msg_id 跨重启不撞（同秒）──────────────────────────────────


def test_msg_id_unique_across_same_second_restart(tmp_path, monkeypatch):
    """counter 回填：同秒重启新行 msg_id 不撞旧行（撞行=PK 写失败/覆盖丢史，回填消撞）。"""
    from src.echo.dispatcher import Channel

    monkeypatch.setattr("time.time", lambda: 1700000000.0)  # 两代同一秒
    d1 = _dispatcher(tmp_path)
    r1 = d1.send(Channel.CONSOLE, "T1", "C1")
    d2 = _dispatcher(tmp_path)  # =同秒重启
    r2 = d2.send(Channel.CONSOLE, "T2", "C2")
    assert r1.msg_id != r2.msg_id
    assert len(_rows(tmp_path / "echo.db")) == 2  # 两行都在，零覆盖丢史


# ── ⑥ 回载 maxlen 语义 + 坏行跳过 ────────────────────────────────


def test_reload_keeps_newest_maxlen(tmp_path):
    """超 maxlen 行回载只取最新 1000（deque 驱逐语义），最旧行不进内存。"""
    from src.echo.dispatcher import Channel

    p = _persist(tmp_path)
    for i in range(1200):
        p.insert(
            msg_id=f"msg_{1700000000 + i}_{i}",
            channel="console",
            title=f"T{i}",
            content="C",
            timestamp=1700000000.0 + i,
            status="sent",
            error="",
            target="",
            priority=5,
            tenant_id="default",
        )
    from src.echo.dispatcher import MessageDispatcher

    d = MessageDispatcher(persist=p)
    hist = d.history(limit=2000)
    assert len(hist) == 1000
    titles = {h["title"] for h in hist}
    assert "T0" not in titles and "T199" not in titles  # 最旧 200 行被驱逐
    assert "T200" in titles and "T1199" in titles
    assert d.stats()["total_messages"] == 1000


def test_unloadable_row_skipped_visible_others_load(tmp_path, caplog):
    """坏行（未知 channel 枚举值）跳过+logger.error 可见，其余行照常回载。"""
    import logging

    from src.echo.dispatcher import Channel

    d1 = _dispatcher(tmp_path)
    d1.send(Channel.CONSOLE, "好", "C")
    # 直接落一行未来/未知 channel 值（诚实边界④）
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "echo.db"))
    conn.execute(
        "INSERT INTO echo_history (msg_id, channel, title, content, timestamp, status,"
        " error, target, priority, tenant_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("msg_x_9", "no-such-channel", "坏", "C", 1700000001.0, "sent", "", "", 5, "default"),
    )
    conn.commit()
    conn.close()

    from src.echo.dispatcher import MessageDispatcher

    d2 = MessageDispatcher(persist=_persist(tmp_path))
    with caplog.at_level(logging.ERROR, logger="src.echo.dispatcher"):
        hist = d2.history()
    assert [h["title"] for h in hist] == ["好"]
    assert any("skipped" in rec.getMessage() for rec in caplog.records)


# ── ⑦ 真实路径接线 + 懒回载不建库 ────────────────────────────────


def test_api_singleton_wired():
    """生产真实路径：api/echo dispatcher 单例真带 persist（构造串静态锁死）。"""
    from pathlib import Path

    import src.api.echo as echo_api

    assert getattr(echo_api.dispatcher, "_persist", None) is not None
    src_text = (Path(__file__).resolve().parents[1] / "src" / "api" / "echo.py").read_text(
        encoding="utf-8"
    )
    assert "MessageDispatcher(persist=HistoryPersistence())" in src_text


def test_lazy_load_no_db_creation_on_construct(tmp_path):
    """构造零 IO：回载懒加载不建库（防 import 期建空生产库），首写才建库。"""
    from src.echo.dispatcher import Channel, MessageDispatcher
    from src.echo.history_persist import HistoryPersistence

    path = tmp_path / "lazy.db"
    d = MessageDispatcher(persist=HistoryPersistence(str(path)))
    assert not path.exists()  # 构造不建库
    d.history()  # 首读（回载）仍只读
    assert not path.exists()
    d.send(Channel.CONSOLE, "T", "C")  # 首写建库
    assert path.exists()


def test_load_recent_missing_db_returns_empty(tmp_path):
    """库文件缺失=空历史起步（回载只读不建库，RV-①b-② load_all 同纪律）。"""
    p = _persist(tmp_path)
    assert p.load_recent() == []
    assert not (tmp_path / "echo.db").exists()
