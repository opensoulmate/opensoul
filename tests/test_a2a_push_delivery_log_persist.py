"""RV-①b-⑤-3 a2a TaskManager 投递留痕持久化——push_delivery_log 写穿/懒回载/
重启投递审计史不丢/真实路径接线。

修前（RV-① A 组定性）：TaskManager.push_delivery_log 全在进程内存——「失败留痕
禁静默」的投递审计（谁投了哪个 webhook、成败/状态码/真因）随 service 重启即清。
本文件锁死：
①DeliveryLogPersistence 行语义（追加表只增不改/投递序/payload=json.dumps/撞主键
  如实抛禁覆盖丢史）
②_dispatch_push 投递尝试随写落盘行（写穿单一点）——成败两型都留痕
③重启回载 entry 逐键一致+只补缺不覆盖（按 delivery_id 去重）
④delivery_persist=None 纯内存既有语义零变化（零 IO 硬证）
⑤写/回载失败可见不阻断（记账旁路纪律）+懒回载重试+坏行跳过+回载不建库
⑥生产真实路径（task_manager 单例）真接线（AST 锁）。
"""

from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def _rows(db_path):
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


def _manager(tmp_path, with_persist: bool = True):
    from src.a2a.task_manager import TaskManager

    if with_persist:
        return TaskManager(delivery_persist=_persist(tmp_path))
    return TaskManager()


def _cfg(url: str = "http://127.0.0.1:1/hook", **kw):
    from src.a2a.models import TaskPushNotificationConfig

    return TaskPushNotificationConfig(url=url, **kw)


def _create_task(tm):
    from src.a2a.models import Message

    return asyncio.run(tm.create_task(Message(role="user", parts=[{"text": "hi"}])))


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


# ── ① DeliveryLogPersistence 行语义 ────────────────────────────


def test_append_row_verbatim_and_payload_json(tmp_path):
    """写穿行逐列 + payload=json.dumps（dict 列禁裸存，铁律）——error 可选键往返。"""
    p = _persist(tmp_path)
    entry = {
        "delivery_id": "d1",
        "task_id": "t1",
        "config_id": "c1",
        "url": "http://x/hook",
        "payload_key": "statusUpdate",
        "ok": False,
        "error": "conn refused",
    }
    p.append(
        delivery_id="d1",
        task_id="t1",
        config_id="c1",
        ok=False,
        payload=json.dumps(entry, ensure_ascii=False),
    )
    (row,) = _rows(tmp_path / "a2a_push_delivery_log.db")
    assert row[0] == "d1" and row[1] == "t1" and row[2] == "c1" and row[3] == 0
    data = json.loads(row[4])
    assert data["error"] == "conn refused"
    assert data["url"] == "http://x/hook"
    assert p.count() == 1


def test_append_only_two_appends_two_rows(tmp_path):
    """追加表语义：一次投递尝试一行，只增不改（与配置当前态表/任务快照表刻意不同）。"""
    p = _persist(tmp_path)
    p.append(delivery_id="d1", task_id="t1", config_id="c1", ok=True, payload="{}")
    p.append(delivery_id="d2", task_id="t1", config_id="c1", ok=False, payload="{}")
    assert p.count() == 2
    assert [r[0] for r in _rows(tmp_path / "a2a_push_delivery_log.db")] == ["d1", "d2"]


def test_append_duplicate_id_raises_no_silent_overwrite(tmp_path):
    """撞主键如实抛（echo_history 纪律：INSERT 禁 REPLACE，禁静默覆盖丢史）。"""
    import pytest

    p = _persist(tmp_path)
    p.append(delivery_id="d1", task_id="t1", config_id="c1", ok=True, payload="{}")
    with pytest.raises(sqlite3.IntegrityError):
        p.append(delivery_id="d1", task_id="t1", config_id="c1", ok=False, payload="{}")
    assert p.count() == 1  # 原行未被覆盖


def test_load_all_order_and_column_authority(tmp_path):
    """回载序=投递序（rowid 升序）；delivery_id/ok 以列值为权威（payload 漂移被纠回）。"""
    p = _persist(tmp_path)
    p.append(delivery_id="d1", task_id="t1", config_id="c1", ok=True, payload="{}")
    p.append(
        delivery_id="d2",
        task_id="t2",
        config_id="",
        ok=False,
        payload=json.dumps({"delivery_id": "drifted", "ok": True}),
    )
    got = p.load_all()
    assert [e["delivery_id"] for e in got] == ["d1", "d2"]
    assert got[1]["delivery_id"] == "d2" and got[1]["task_id"] == "t2"
    assert got[1]["ok"] is False  # 列值权威，payload 内漂移 true 不生效


def test_load_all_skips_bad_row_visible(tmp_path, caplog):
    """坏行跳过+logger.error 可见，不因单行坏数据丢整库审计史（禁静默丢）。"""
    p = _persist(tmp_path)
    p.append(delivery_id="d1", task_id="t1", config_id="c1", ok=True, payload="{}")
    p.append(delivery_id="d2", task_id="t1", config_id="c1", ok=True, payload="{not json")
    with caplog.at_level("ERROR"):
        got = p.load_all()
    assert [e["delivery_id"] for e in got] == ["d1"]
    assert any("不可解" in r.message for r in caplog.records)


def test_load_all_readonly_no_db_creation(tmp_path):
    """回载只读不建库（写穿才建库）：空路径 load_all=[] 且库文件不被顺手建出。"""
    p = _persist(tmp_path)
    assert p.load_all() == []
    assert not (tmp_path / "a2a_push_delivery_log.db").exists()
    assert p.count() == 0


# ── ② 写穿：投递尝试随写落盘（成败两型都留痕）──────────────────


def test_dispatch_success_writes_row(tmp_path):
    """真 socket webhook 投递成功→库行落盘（ok=1/status_code/entry 逐键进 payload）。"""
    server, url, received = _stub()
    try:
        tm = _manager(tmp_path)
        task = _create_task(tm)
        tm.create_push_config(task.id, _cfg(url))
        _dispatch(tm, task)
        assert len(received) == 1
        (row,) = _rows(tmp_path / "a2a_push_delivery_log.db")
        did, tid, cid, ok, payload = row
        assert tid == task.id and ok == 1
        data = json.loads(payload)
        assert data["delivery_id"] == did
        assert data["ok"] is True
        assert data["status_code"] == 200
        assert data["payload_key"] == "statusUpdate"
        assert len(tm.push_delivery_log) == 1
        assert tm.push_delivery_log[0]["delivery_id"] == did
    finally:
        server.shutdown()
        server.server_close()


def test_dispatch_failure_writes_row_too(tmp_path):
    """投递失败同样留痕落盘（失败留痕禁静默）：ok=0+error 真因进 payload。"""
    tm = _manager(tmp_path)
    task = _create_task(tm)
    tm.create_push_config(task.id, _cfg(_closed_port_url()))
    _dispatch(tm, task)
    (row,) = _rows(tmp_path / "a2a_push_delivery_log.db")
    data = json.loads(row[4])
    assert row[3] == 0
    assert data["ok"] is False and data["error"]
    assert tm.push_delivery_log[0]["ok"] is False


def test_two_attempts_two_rows_in_order(tmp_path):
    """每更新每配置各投一次=各留一行，行序=投递序。"""
    server, url, _received = _stub()
    try:
        tm = _manager(tmp_path)
        task = _create_task(tm)
        tm.create_push_config(task.id, _cfg(url))
        _dispatch(tm, task)
        _dispatch(tm, task)
        rows = _rows(tmp_path / "a2a_push_delivery_log.db")
        assert len(rows) == 2
        assert [json.loads(r[4])["payload_key"] for r in rows] == [
            "statusUpdate",
            "statusUpdate",
        ]
        assert rows[0][0] != rows[1][0]  # delivery_id 唯一
    finally:
        server.shutdown()
        server.server_close()


# ── ③ 重启回载：逐键一致 + 只补缺不覆盖 ─────────────────────────


def test_restart_reload_parity(tmp_path):
    """双实例（=重启）：第二实例回载后 entry 逐键一致（成败两型）、投递序保持。"""
    server, url, _received = _stub()
    try:
        tm1 = _manager(tmp_path)
        task = _create_task(tm1)
        dead = tm1.create_push_config(task.id, _cfg(_closed_port_url()))
        _dispatch(tm1, task)  # 失败行
        tm1.delete_push_config(task.id, dead["id"])
        tm1.create_push_config(task.id, _cfg(url))
        _dispatch(tm1, task)  # 成功行
    finally:
        server.shutdown()
        server.server_close()
    rows = _rows(tmp_path / "a2a_push_delivery_log.db")
    assert len(rows) == 2 and rows[0][3] == 0 and rows[1][3] == 1
    tm2 = _manager(tmp_path)  # 新进程（内存全空）
    assert tm2.push_delivery_log == []
    tm2._ensure_delivery_loaded()
    assert tm2.push_delivery_log == tm1.push_delivery_log  # 逐键一致含 delivery_id
    assert [e["ok"] for e in tm2.push_delivery_log] == [False, True]  # 投递序保持
    assert tm2._delivery_log_loaded is True


def test_reload_skips_existing_no_dup_no_clobber(tmp_path):
    """只补缺不覆盖：内存已有 entry（含同 delivery_id）不被重复追加/洗掉。"""
    p = _persist(tmp_path)
    p.append(delivery_id="d1", task_id="t1", config_id="c1", ok=True, payload="{}")
    p.append(delivery_id="d2", task_id="t1", config_id="c1", ok=False, payload="{}")
    tm = _manager(tmp_path)
    sentinel = {"delivery_id": "d1", "task_id": "t1", "note": "in-memory-new"}
    tm.push_delivery_log.append(sentinel)
    tm._ensure_delivery_loaded()
    # d1 已在内存=不重复追加；d2 补缺；sentinel 原样不被洗
    assert [e["delivery_id"] for e in tm.push_delivery_log] == ["d1", "d2"]
    assert tm.push_delivery_log[0] is sentinel
    # 已回载=零操作（幂等）
    tm._ensure_delivery_loaded()
    assert len(tm.push_delivery_log) == 2
    # 强制再回载（模拟重试路径）仍不重复
    tm._delivery_log_loaded = False
    tm._ensure_delivery_loaded()
    assert len(tm.push_delivery_log) == 2


# ── ④ 纯内存零变化（delivery_persist=None）─────────────────────


def test_pure_memory_zero_io_and_shape(tmp_path, monkeypatch):
    """delivery_persist=None=纯内存既有语义零变化：零落盘 IO+entry 行为不变。"""
    server, url, _received = _stub()
    try:
        monkeypatch.setenv("A2A_PUSH_LOG_DB", str(tmp_path / "never_created.db"))
        tm = _manager(tmp_path, with_persist=False)
        task = _create_task(tm)
        tm.create_push_config(task.id, _cfg(url))
        _dispatch(tm, task)
        assert len(tm.push_delivery_log) == 1
        e = tm.push_delivery_log[0]
        assert e["ok"] is True and e["task_id"] == task.id  # 既有键行为不变
        assert not (tmp_path / "never_created.db").exists()  # 零 IO 硬证
    finally:
        server.shutdown()
        server.server_close()


# ── ⑤ 故障可见不阻断 + 懒回载重试 ──────────────────────────────


def test_write_failure_visible_not_blocking(tmp_path, caplog):
    """写失败=旁路纪律：内存留痕照常（审计可见）+logger.error 可见。"""

    class Boom(_persist(tmp_path).__class__):
        def append(self, **kw):
            raise RuntimeError("disk full")

        def load_recent(self, limit=1000):  # GR-①b-② 契约随更新（懒回载读口）
            return []

    from src.a2a.task_manager import TaskManager

    tm = TaskManager(delivery_persist=Boom())
    entry = {"delivery_id": "d1", "task_id": "t1", "config_id": "c1", "ok": False, "error": "x"}
    with caplog.at_level("ERROR"):
        tm.push_delivery_log.append(entry)
        tm._sync_delivery_entry(entry)
    assert tm.push_delivery_log == [entry]  # 内存留痕仍在
    assert any("写穿失败" in r.message for r in caplog.records)


def test_reload_failure_retries_next_touch(tmp_path, caplog):
    """回载失败不置位（下次触碰重试）：首触失败→二触真回载可见。"""
    calls = {"n": 0}

    class Flaky(_persist(tmp_path).__class__):
        def append(self, **kw):
            pass

        def load_recent(self, limit=1000):  # 懒回载读口（GR-①b-② load_all→load_recent 契约随更新）
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("db locked")
            return [{"delivery_id": "d9", "task_id": "t9", "ok": True}]

    from src.a2a.task_manager import TaskManager

    tm = TaskManager(delivery_persist=Flaky())
    with caplog.at_level("ERROR"):
        tm._ensure_delivery_loaded()
    assert tm._delivery_log_loaded is False  # 失败不置位
    assert calls["n"] == 1
    tm._ensure_delivery_loaded()  # 下次触碰=重试真回载
    assert [e["delivery_id"] for e in tm.push_delivery_log] == ["d9"]
    assert calls["n"] == 2
    assert tm._delivery_log_loaded is True
    assert any("回载失败" in r.message for r in caplog.records)


# ── ⑥ 真实路径接线（AST 锁）─────────────────────────────────────


def test_singleton_wired_with_delivery_persist_keyword():
    """生产单例构造带 delivery_persist= 关键字（AST 锁，防接线漂移）+真实例在场。"""
    import src.a2a.task_manager as a2a_tm

    src = Path(a2a_tm.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    wired = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "task_manager" for t in node.targets
        ):
            call = node.value
            assert isinstance(call, ast.Call)
            kws = {k.arg for k in call.keywords}
            assert "persist" in kws and "push_persist" in kws and "delivery_persist" in kws
            wired = True
    assert wired
    assert a2a_tm.task_manager._delivery_persist is not None


def test_change_points_wired_static():
    """变更点/读点全接线（AST 静态锁）：写穿/懒回载在 _dispatch_push 真实方法体内。"""
    import src.a2a.task_manager as a2a_tm

    src = Path(a2a_tm.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    bodies = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "_dispatch_push",
            "_notify_push",
            "_ensure_delivery_loaded",
            "_sync_delivery_entry",
        }:
            bodies[node.name] = ast.unparse(node)

    def calls(body: str, name: str) -> bool:
        return f"self.{name}(" in body

    assert calls(bodies["_dispatch_push"], "_ensure_delivery_loaded")
    assert calls(bodies["_dispatch_push"], "_sync_delivery_entry")
    assert calls(bodies["_notify_push"], "_ensure_push_loaded")
    # 写穿单一点：_sync_delivery_entry 全仓仅 _dispatch_push 一个调用点（禁散落写点）
    assert src.count("self._sync_delivery_entry(") == 1
