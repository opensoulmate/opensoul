"""GR-①b-② a2a TaskManager 投递留痕内存窗+有界回载——push_delivery_log 长跑无界增长收口。

修前（GR-① 定性「事件流型真无界」）：push_delivery_log 每投递 append 一行永不裁
+懒回载 load_all 全量进内存=长跑/重启即无界，与自身 DB 真源（a2a_push_delivery_log，
RV-①b-⑤-3）不对称。本文件锁死：
①内存窗裁剪（delitem 形态，普查判定单一源真认 grow_with_trim）：窗=缓存尺寸非
  数据阈值；persist 在场才裁（无真源裁=丢唯一副本）；N<=0 不裁；裁剪保投递序
②回载有界（load_recent 替换 load_all 全量进内存）：回填行数<=窗、N<=0 如实放开
③读面零可观测变化：recent_delivery_log 窗不足回落行真源，任何 N 值读面输出不变
  （裁窗前后逐键一致）；回落失败降级内存窗可见非静默
④计数/审计真源不受窗影响：persist.count()/load_all 恒全表（窗裁剪不丢审计史）
⑤真实路径接线：_dispatch_push 真 socket 投递→写穿落库+窗裁剪+读面回落全链
⑥诚实边界：写失败行只活内存（回落/DB 面不可见，如实非谎报 durable）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def _rows(db_path):
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT delivery_id, task_id, config_id, ok, payload"
            " FROM a2a_push_delivery_log ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()


def _persist(tmp_path):
    from src.a2a.delivery_log_persist import DeliveryLogPersistence

    return DeliveryLogPersistence(str(tmp_path / "a2a_push_delivery_log.db"))


def _manager(tmp_path, window: int | None = None):
    from src.a2a.task_manager import TaskManager

    tm = TaskManager(delivery_persist=_persist(tmp_path))
    if window is not None:
        tm.delivery_log_window = window
    return tm


def _cfg(url: str):
    from src.a2a.models import TaskPushNotificationConfig

    return TaskPushNotificationConfig(url=url)


def _create_task(tm):
    from src.a2a.models import Message

    return asyncio.run(tm.create_task(Message(role="user", parts=[{"text": "hi"}])))


def _seed(p, n: int) -> list[str]:
    """直接落库 n 行（d1..dn，payload 带行号），返回 delivery_id 序。"""
    ids = []
    for i in range(n):
        did = f"d{i + 1}"
        p.append(
            delivery_id=did,
            task_id="t1",
            config_id="c1",
            ok=(i % 2 == 0),
            payload=json.dumps({"delivery_id": did, "seq": i, "ok": i % 2 == 0}),
        )
        ids.append(did)
    return ids


def _stub():
    """真 HTTP webhook 桩（独立线程+真 socket），记录每次 POST。"""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            received.append({"path": self.path})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/hook", received


def _closed_port_url() -> str:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/dead"


def _dispatch(tm, task):
    asyncio.run(tm._dispatch_push(task, {"statusUpdate": {"taskId": task.id}}))


# ── ① 内存窗裁剪 ───────────────────────────────────────────────


def test_trim_keeps_last_window_rows_truth_full(tmp_path):
    """超窗 append→缓存只留最近 N 条（投递序尾部）；行真源恒全表落库不受窗影响。"""
    server, url, _received = _stub()
    try:
        tm = _manager(tmp_path, window=3)
        task = _create_task(tm)
        tm.create_push_config(task.id, _cfg(url))
        for _ in range(5):
            _dispatch(tm, task)
    finally:
        server.shutdown()
        server.server_close()
    assert len(tm.push_delivery_log) == 3  # 窗=3
    rows = _rows(tmp_path / "a2a_push_delivery_log.db")
    assert len(rows) == 5  # 行真源恒全表（窗=缓存尺寸非数据阈值）
    # 缓存=投递序尾部 3 条（最旧行先丢），逐键=行真源尾部
    kept = [e["delivery_id"] for e in tm.push_delivery_log]
    assert kept == [r[0] for r in rows[-3:]]
    assert kept != [r[0] for r in rows[:3]]  # 丢的是最旧行（反证：非随机/非丢新）


def test_no_trim_without_persist(tmp_path):
    """delivery_persist=None=无真源可回落，裁=丢唯一副本→不裁（GR-①b-① 同纪律）。"""
    from src.a2a.task_manager import TaskManager

    server, url, _received = _stub()
    try:
        tm = TaskManager()
        tm.delivery_log_window = 3
        task = _create_task(tm)
        tm.create_push_config(task.id, _cfg(url))
        for _ in range(5):
            _dispatch(tm, task)
    finally:
        server.shutdown()
        server.server_close()
    assert len(tm.push_delivery_log) == 5  # 唯一副本不裁


def test_no_trim_when_window_zero(tmp_path):
    """N<=0=不裁（缓存尺寸语义放开，与 GR-①b-① records_window 同契约）。"""
    server, url, _received = _stub()
    try:
        tm = _manager(tmp_path, window=0)
        task = _create_task(tm)
        tm.create_push_config(task.id, _cfg(url))
        for _ in range(5):
            _dispatch(tm, task)
    finally:
        server.shutdown()
        server.server_close()
    assert len(tm.push_delivery_log) == 5


# ── ② 回载有界 ─────────────────────────────────────────────────


def test_reload_bounded_to_window_and_load_all_not_used(tmp_path, monkeypatch):
    """懒回载行数<=窗（load_recent 有界读）；load_all 全量回载不再被调（修前
    重启即无界=全量进内存的硬反证面）。"""
    p = _persist(tmp_path)
    ids = _seed(p, 10)
    tm = _manager(tmp_path, window=3)
    monkeypatch.setattr(
        type(p), "load_all", lambda self: (_ for _ in ()).throw(AssertionError("禁全量回载"))
    )
    tm._ensure_delivery_loaded()
    assert [e["delivery_id"] for e in tm.push_delivery_log] == ids[-3:]  # 尾部 3 条=窗
    assert tm._delivery_log_loaded is True


def test_reload_unbounded_when_window_zero(tmp_path):
    """N<=0=不裁窗语义，回载上界如实放开（非静默截断）。"""
    p = _persist(tmp_path)
    ids = _seed(p, 10)
    tm = _manager(tmp_path, window=0)
    tm._ensure_delivery_loaded()
    assert [e["delivery_id"] for e in tm.push_delivery_log] == ids


def test_reload_dedup_and_trim_together(tmp_path):
    """窗+既有去重语义共存：内存已有行不重复、回填后总量<=窗。"""
    p = _persist(tmp_path)
    _seed(p, 6)
    tm = _manager(tmp_path, window=4)
    sentinel = {"delivery_id": "d6", "task_id": "t1", "note": "in-memory-new"}
    tm.push_delivery_log.append(sentinel)
    tm._ensure_delivery_loaded()
    ids = [e["delivery_id"] for e in tm.push_delivery_log]
    assert ids.count("d6") == 1  # 去重不双份
    assert len(tm.push_delivery_log) <= 4  # 回填后窗内
    assert tm.push_delivery_log[-1] is sentinel or "d6" in ids  # 新行不被回载洗掉


# ── ③ 读面零可观测变化 ─────────────────────────────────────────


def test_recent_read_falls_back_to_row_truth(tmp_path):
    """窗不足 limit=回落行真源：读面输出==全史（裁窗前逐字一致=零可观测变化）。"""
    p = _persist(tmp_path)
    ids = _seed(p, 10)
    tm = _manager(tmp_path, window=3)
    tm._ensure_delivery_loaded()
    assert len(tm.push_delivery_log) == 3  # 前提：窗真裁了
    out = tm.recent_delivery_log(10)
    assert [e["delivery_id"] for e in out] == ids  # 全史 10 条逐键=行真源
    assert out == p.load_all()  # 与全史回读口逐键一致


def test_recent_read_any_n_unchanged_by_window(tmp_path):
    """任何 N 值读面输出不变（零新阈值硬断言）：窗=1000（不裁）与窗=3（裁）两实例
    同库同读面逐键一致。"""
    p = _persist(tmp_path)
    ids = _seed(p, 10)
    tm_big = _manager(tmp_path, window=1000)
    tm_big._ensure_delivery_loaded()
    tm_small = _manager(tmp_path, window=3)
    tm_small._ensure_delivery_loaded()
    assert len(tm_small.push_delivery_log) == 3
    for n in (1, 3, 7, 10):
        a = tm_big.recent_delivery_log(n)
        b = tm_small.recent_delivery_log(n)
        assert a == b, f"n={n} 读面被窗改变=零可观测变化破口"
        assert [e["delivery_id"] for e in b] == ids[-n:]


def test_recent_read_window_path_no_db_needed(tmp_path, monkeypatch):
    """窗内足量=缓存尾切直出（快路径）；此路不触 DB。"""
    p = _persist(tmp_path)
    _seed(p, 2)
    tm = _manager(tmp_path, window=10)
    tm._ensure_delivery_loaded()
    monkeypatch.setattr(
        type(p),
        "load_recent",
        lambda self, limit=0: (_ for _ in ()).throw(AssertionError("快路径禁触库")),
    )
    out = tm.recent_delivery_log(2)
    assert [e["delivery_id"] for e in out] == ["d1", "d2"]


def test_recent_read_fallback_failure_degrades_window_visible(tmp_path, monkeypatch, caplog):
    """回落失败=降级内存窗可见非静默（禁静默空）。"""
    p = _persist(tmp_path)
    ids = _seed(p, 10)
    tm = _manager(tmp_path, window=3)
    tm._ensure_delivery_loaded()
    monkeypatch.setattr(
        type(p), "load_recent", lambda self, limit=0: (_ for _ in ()).throw(OSError("db down"))
    )
    with caplog.at_level(logging.ERROR):
        out = tm.recent_delivery_log(10)
    assert [e["delivery_id"] for e in out] == ids[-3:]  # 降级窗=尾部 3 条，非空
    assert any("读面回落失败" in r.message for r in caplog.records)  # 可见非静默


# ── ④ 计数/审计真源不受窗影响 ──────────────────────────────────


def test_count_and_full_history_unaffected_by_window(tmp_path):
    """窗裁剪不丢审计史：count()/load_all 恒全表（计数真源=库行聚合，非 len(缓存)）。"""
    p = _persist(tmp_path)
    ids = _seed(p, 7)
    tm = _manager(tmp_path, window=3)
    tm._ensure_delivery_loaded()
    assert len(tm.push_delivery_log) == 3
    assert p.count() == 7  # 计数真源=全表行数（窗外照样计数）
    assert [e["delivery_id"] for e in p.load_all()] == ids


# ── ⑤ 真实路径接线 ─────────────────────────────────────────────


def test_real_path_dispatch_write_through_and_trim(tmp_path):
    """真 socket 投递全链：写穿落库（成败留痕）+窗裁剪+读面回落真源。"""
    server, url, received = _stub()
    try:
        tm = _manager(tmp_path, window=2)
        task = _create_task(tm)
        tm.create_push_config(task.id, _cfg(url))
        for _ in range(3):
            _dispatch(tm, task)
        for c in tm.list_push_configs(task.id):  # 撤掉活配置，第 4 轮只投死端口=1 行
            tm.delete_push_config(task.id, c["id"])
        tm.create_push_config(task.id, _cfg(_closed_port_url()))
        _dispatch(tm, task)  # 失败投递同样留痕
        assert len(received) == 3
    finally:
        server.shutdown()
        server.server_close()
    rows = _rows(tmp_path / "a2a_push_delivery_log.db")
    assert len(rows) == 4  # 全部落库（窗不影响写穿）
    assert [r[3] for r in rows] == [1, 1, 1, 0]  # 成败两型都留痕
    assert len(tm.push_delivery_log) == 2  # 窗=2
    out = tm.recent_delivery_log(4)
    assert [e["delivery_id"] for e in out] == [r[0] for r in rows]  # 读面回落=行真源全史


# ── ⑥ 诚实边界 ─────────────────────────────────────────────────


def test_write_fail_row_memory_only_not_in_fallback(tmp_path, monkeypatch, caplog):
    """写失败行只活内存（诚实边界）：窗内可见，但回落/DB 面不可见=非谎报 durable。"""
    p = _persist(tmp_path)

    def _boom(**kw):
        raise OSError("disk full")

    monkeypatch.setattr(p, "append", _boom)
    from src.a2a.task_manager import TaskManager

    tm = TaskManager(delivery_persist=p)  # 同一实例（打桩才生效）
    tm.delivery_log_window = 10
    task = _create_task(tm)
    tm.create_push_config(task.id, _cfg(_closed_port_url()))
    with caplog.at_level(logging.ERROR):
        _dispatch(tm, task)
    assert len(tm.push_delivery_log) == 1  # 内存留痕仍在
    assert any("写穿失败" in r.message for r in caplog.records)  # 失败可见非静默
    assert p.count() == 0  # 库零行
    # 读面回落（limit>窗内 1 条）→DB 真源=空，写失败行不冒充 durable
    assert tm.recent_delivery_log(50) == []
