"""RV-①b-⑤-2 a2a TaskManager 推送配置持久化——写穿/懒回载/重启配置不丢/真实路径接线。

修前（RV-① A 组定性）：TaskManager.push_configs 全在进程内存——a2a 推送通知配置
面（Get/List/DeleteTaskPushNotificationConfig 真源、webhook 投递依据）随 service
重启全清。本文件锁死：
①PushPersistence 行语义（复合主键当前态/登记序/幂等删/payload=json.dumps）
②create/delete 变更点随写落盘行（写穿单一点）
③重启回载配置 nested 面（authentication/token）逐键一致+登记序
④push_persist=None 纯内存既有语义零变化（零 IO 硬证）
⑤写/回载失败可见不阻断（记账旁路纪律）+懒回载只补缺不覆盖+坏行跳过+回载不建库
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

import pytest


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT task_id, config_id, payload FROM a2a_push_configs ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()


def _persist(tmp_path):
    from src.a2a.push_persist import PushPersistence

    return PushPersistence(str(tmp_path / "a2a_push_configs.db"))


def _manager(tmp_path, with_persist: bool = True):
    from src.a2a.task_manager import TaskManager

    if with_persist:
        return TaskManager(push_persist=_persist(tmp_path))
    return TaskManager()


def _cfg(url: str = "http://127.0.0.1:1/hook", **kw):
    from src.a2a.models import TaskPushNotificationConfig

    return TaskPushNotificationConfig(url=url, **kw)


def _create_task(tm):
    from src.a2a.models import Message

    return asyncio.run(tm.create_task(Message(role="user", parts=[{"text": "hi"}])))


# ── ① PushPersistence 行语义 ────────────────────────────────────


def test_upsert_row_verbatim_and_payload_json(tmp_path):
    """写穿行逐列 + payload=json.dumps（dict 列禁裸存，铁律）——authentication 嵌套往返。"""
    p = _persist(tmp_path)
    p.upsert(
        task_id="t1",
        config_id="c1",
        payload=json.dumps(
            {
                "id": "c1",
                "taskId": "t1",
                "url": "http://x/hook",
                "token": "tok-1",
                "authentication": {"scheme": "Bearer", "credentials": "sec-1"},
            },
            ensure_ascii=False,
        ),
    )
    (row,) = _rows(tmp_path / "a2a_push_configs.db")
    assert row[0] == "t1" and row[1] == "c1"
    data = json.loads(row[2])
    assert data["authentication"] == {"scheme": "Bearer", "credentials": "sec-1"}
    assert data["token"] == "tok-1"
    assert p.count() == 1


def test_upsert_current_state_semantics(tmp_path):
    """同 (task_id, config_id) upsert 覆盖=当前态一配置一行（快照语义，与追加表刻意不同）。"""
    p = _persist(tmp_path)
    p.upsert(task_id="t1", config_id="c1", payload=json.dumps({"url": "old"}))
    p.upsert(task_id="t1", config_id="c1", payload=json.dumps({"url": "new"}))
    assert p.count() == 1
    (row,) = _rows(tmp_path / "a2a_push_configs.db")
    assert json.loads(row[2])["url"] == "new"


def test_delete_idempotent_row_gone(tmp_path):
    """删行幂等：行消失、再删零报错（§3.1.10 语义）。"""
    p = _persist(tmp_path)
    p.upsert(task_id="t1", config_id="c1", payload="{}")
    p.delete(task_id="t1", config_id="c1")
    p.delete(task_id="t1", config_id="c1")  # 幂等
    p.delete(task_id="t9", config_id="c9")  # 不存在=零效果
    assert p.count() == 0
    assert _rows(tmp_path / "a2a_push_configs.db") == []


def test_load_all_insertion_order(tmp_path):
    """回载序=登记序（rowid 升序）——同任务多配置保插入序（list 翻页稳定序）。"""
    p = _persist(tmp_path)
    for i in range(3):
        p.upsert(task_id="t1", config_id=f"c{i}", payload=json.dumps({"i": i}))
    p.upsert(task_id="t0", config_id="c9", payload=json.dumps({"i": 9}))
    got = [(tid, cid, d["i"]) for tid, cid, d in p.load_all()]
    assert got == [("t1", "c0", 0), ("t1", "c1", 1), ("t1", "c2", 2), ("t0", "c9", 9)]


# ── ② 坏行/回载边界 ─────────────────────────────────────────────


def test_load_all_bad_row_skipped_with_log(tmp_path, caplog):
    """坏行跳过+logger 可见禁静默丢，不因单行坏数据丢整库配置。"""
    p = _persist(tmp_path)
    p.upsert(task_id="t1", config_id="good", payload=json.dumps({"ok": 1}))
    # 直插坏行（绕过 upsert）
    conn = sqlite3.connect(str(tmp_path / "a2a_push_configs.db"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS a2a_push_configs (task_id TEXT NOT NULL, config_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (task_id, config_id))"
    )
    conn.execute("INSERT INTO a2a_push_configs VALUES ('t1','bad','{not-json')")
    conn.commit()
    conn.close()
    with caplog.at_level("ERROR"):
        out = p.load_all()
    assert [(t, c) for t, c, _ in out] == [("t1", "good")]
    assert any("不可解" in r.message for r in caplog.records)


def test_load_all_missing_db_empty_no_create(tmp_path):
    """回载只读不建库（防 import 期建空生产库）：库缺失=空起步且文件不被建出。"""
    p = _persist(tmp_path)
    assert p.load_all() == []
    assert not (tmp_path / "a2a_push_configs.db").exists()
    assert p.count() == 0
    assert not (tmp_path / "a2a_push_configs.db").exists()


# ── ③ TaskManager 写穿/回载 ─────────────────────────────────────


def test_create_push_config_write_through(tmp_path):
    """登记即落盘行（写穿单一点），行 payload 逐键==返回 cfg（含 authentication 嵌套）。"""
    tm = _manager(tmp_path)
    cfg = tm.create_push_config(
        "t1",
        _cfg(
            url="http://x/hook",
            token="tok-1",
            authentication={"scheme": "Bearer", "credentials": "sec-1"},
        ),
    )
    (row,) = _rows(tmp_path / "a2a_push_configs.db")
    assert row[0] == "t1" and row[1] == cfg["id"]
    assert json.loads(row[2]) == cfg


def test_server_assigned_id_persisted(tmp_path):
    """服务端指派 id（§3.1.7）同样落行——id 出现在行键与 payload。"""
    tm = _manager(tmp_path)
    cfg = tm.create_push_config("t1", _cfg())
    assert cfg.get("id")
    rows = _rows(tmp_path / "a2a_push_configs.db")
    assert rows[0][1] == cfg["id"]
    assert json.loads(rows[0][2])["id"] == cfg["id"]


def test_delete_push_config_write_through(tmp_path):
    """删除即删行（写穿）：内存与库行同消。"""
    tm = _manager(tmp_path)
    cfg = tm.create_push_config("t1", _cfg())
    assert tm.delete_push_config("t1", cfg["id"]) is True
    assert tm.delete_push_config("t1", cfg["id"]) is False  # 幂等语义不变
    assert _rows(tmp_path / "a2a_push_configs.db") == []


def test_restart_reload_roundtrip(tmp_path):
    """重启（新实例懒回载）配置 nested 面逐键一致+登记序不乱。"""
    tm = _manager(tmp_path)
    c1 = tm.create_push_config(
        "t1",
        _cfg(
            url="http://x/1",
            token="tok-1",
            authentication={"scheme": "Bearer", "credentials": "sec-1"},
        ),
    )
    c2 = tm.create_push_config("t1", _cfg(url="http://x/2"))
    tm2 = _manager(tmp_path)  # 模拟重启：全新实例
    listed = tm2.list_push_configs("t1")
    assert [c["id"] for c in listed] == [c1["id"], c2["id"]]  # 登记序
    assert listed[0]["authentication"] == {"scheme": "Bearer", "credentials": "sec-1"}
    assert listed[0]["token"] == "tok-1"
    assert listed[0]["url"] == "http://x/1"
    got = tm2.get_push_config("t1", c1["id"])
    assert got == dict(c1)  # 逐键一致


def test_reload_only_fills_missing(tmp_path):
    """懒回载只补缺不覆盖：内存已有新态不被回载洗掉（禁回载洗内存新态）。"""
    p = _persist(tmp_path)
    p.upsert(
        task_id="t1", config_id="c1", payload=json.dumps({"id": "c1", "taskId": "t1", "url": "old"})
    )
    tm = _manager(tmp_path)
    tm.push_configs["t1"] = {"c1": {"id": "c1", "taskId": "t1", "url": "new"}}  # 内存新态
    tm._ensure_push_loaded()
    assert tm.push_configs["t1"]["c1"]["url"] == "new"  # 不被洗回 old


def test_dispatch_after_reload_delivers(tmp_path):
    """重启后 webhook 投递真跑（真 socket 桩）：回载配置仍是有效投递依据=不只可读。"""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            received.append({"headers": dict(self.headers), "body": json.loads(body)})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/hook"
        tm = _manager(tmp_path)
        task = _create_task(tm)
        tm.create_push_config(
            task.id,
            _cfg(url=url, authentication={"scheme": "Bearer", "credentials": "sec-r"}),
        )
        tm2 = _manager(tmp_path)  # 模拟重启
        asyncio.run(tm2._dispatch_push(task, {"statusUpdate": {"x": 1}}))
        assert len(received) == 1
        assert received[0]["headers"].get("Authorization") == "Bearer sec-r"
        assert received[0]["body"] == {"statusUpdate": {"x": 1}}
    finally:
        server.shutdown()
        server.server_close()


# ── ④ 纯内存零变化 ──────────────────────────────────────────────


def test_pure_memory_zero_io_unchanged(tmp_path, monkeypatch):
    """push_persist=None 既有语义零变化+零 IO 硬证（不建库）。"""
    monkeypatch.setenv("A2A_PUSH_DB", str(tmp_path / "should_not_exist.db"))
    tm = _manager(tmp_path, with_persist=False)
    cfg = tm.create_push_config("t1", _cfg())
    assert tm.get_push_config("t1", cfg["id"]) == cfg
    assert [c["id"] for c in tm.list_push_configs("t1")] == [cfg["id"]]
    assert tm.delete_push_config("t1", cfg["id"]) is True
    assert tm.delete_push_config("t1", cfg["id"]) is False
    assert tm.list_push_configs("t1") == []
    assert not (tmp_path / "should_not_exist.db").exists()  # 零 IO


# ── ⑤ 故障可见不阻断 ────────────────────────────────────────────


def test_write_failure_bypass_visible_not_blocking(tmp_path, caplog):
    """写失败=旁路纪律：配置登记照常（内存生效）+logger.error 可见。"""

    class Boom(_persist(tmp_path).__class__):
        def upsert(self, **kw):
            raise RuntimeError("disk full")

        def delete(self, **kw):
            raise RuntimeError("disk full")

        def load_all(self):
            return []

    from src.a2a.task_manager import TaskManager

    tm = TaskManager(push_persist=Boom())
    with caplog.at_level("ERROR"):
        cfg = tm.create_push_config("t1", _cfg())
    assert tm.get_push_config("t1", cfg["id"]) == cfg  # 内存照常生效
    with caplog.at_level("ERROR"):
        assert tm.delete_push_config("t1", cfg["id"]) is True
    assert any("写穿失败" in r.message for r in caplog.records)
    assert any("删除写穿失败" in r.message for r in caplog.records)


def test_reload_failure_retries_next_touch(tmp_path, caplog):
    """回载失败不置位（下次触碰重试）：首触失败→二触真回载可见。"""
    calls = {"n": 0}

    class Flaky(_persist(tmp_path).__class__):
        def upsert(self, **kw):
            pass

        def delete(self, **kw):
            pass

        def load_all(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("db locked")
            return [("t1", "c1", {"id": "c1", "taskId": "t1", "url": "u"})]

    from src.a2a.task_manager import TaskManager

    tm = TaskManager(push_persist=Flaky())
    with caplog.at_level("ERROR"):
        tm._ensure_push_loaded()
    assert tm._push_loaded is False  # 失败不置位
    assert calls["n"] == 1
    # 下次触碰（读点自带懒回载）= 重试真回载
    got = tm.get_push_config("t1", "c1")
    assert got is not None and got["url"] == "u"  # 重试真回载
    assert calls["n"] == 2
    assert tm._push_loaded is True
    assert any("回载失败" in r.message for r in caplog.records)


# ── ⑥ 真实路径接线（AST 锁）─────────────────────────────────────


def test_singleton_wired_with_push_persist_keyword():
    """生产单例构造带 push_persist= 关键字（AST 锁，防接线漂移）+真实例在场。"""
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
            assert "persist" in kws and "push_persist" in kws
            wired = True
    assert wired
    assert a2a_tm.task_manager._push_persist is not None


def test_change_points_wired_static():
    """变更点/读点全接线（AST 静态锁）：写穿/删行/懒回载各在真实方法体内。"""
    import src.a2a.task_manager as a2a_tm

    src = Path(a2a_tm.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    bodies = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            "create_push_config",
            "delete_push_config",
            "get_push_config",
            "list_push_configs",
            "_dispatch_push",
            "_notify_push",
        }:
            bodies[node.name] = ast.unparse(node)

    def calls(body: str, name: str) -> bool:
        return f"self.{name}(" in body

    assert calls(bodies["create_push_config"], "_sync_push_config")
    assert calls(bodies["delete_push_config"], "_ensure_push_loaded")
    assert calls(bodies["delete_push_config"], "_drop_push_config")
    assert calls(bodies["get_push_config"], "_ensure_push_loaded")
    assert calls(bodies["list_push_configs"], "_ensure_push_loaded")
    assert calls(bodies["_dispatch_push"], "_ensure_push_loaded")
    assert calls(bodies["_notify_push"], "_ensure_push_loaded")
