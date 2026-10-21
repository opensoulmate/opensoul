"""RV-①b-⑤-1 a2a TaskManager 任务行持久化——写穿/懒回载/重启任务不丢/真实路径接线。

修前（RV-① A 组定性）：TaskManager.tasks 全在进程内存——a2a 任务面（GetTask/
ListTasks 真源、history/artifacts/status）随 service 重启全清。本文件锁死：
①create_task/状态迁移/入史/artifact/metadata 各变更点随写落盘行（写穿单一点）
②重启回载任务 nested 面（history/artifacts/status/metadata）逐键一致
③persist=None 纯内存既有语义零变化（不建库零 IO）
④写/回载失败可见不阻断（记账旁路纪律）
⑤懒回载只补缺不覆盖+坏行跳过+回载不建库
⑥生产真实路径（task_manager 单例）真接线。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT task_id, context_id, state, status_timestamp, payload"
            " FROM a2a_tasks ORDER BY status_timestamp, task_id"
        ).fetchall()
    finally:
        conn.close()


def _persist(tmp_path):
    from src.a2a.task_persist import TaskPersistence

    return TaskPersistence(str(tmp_path / "a2a_tasks.db"))


def _manager(tmp_path):
    from src.a2a.task_manager import TaskManager

    return TaskManager(persist=_persist(tmp_path))


def _msg(text: str = "你好", **kw):
    from src.a2a.models import Message

    return Message(role="user", parts=[{"type": "text", "text": text}], **kw)


def _create(tm, text: str = "你好", metadata: dict | None = None):
    return asyncio.run(tm.create_task(_msg(text), metadata=metadata))


# ── ① TaskPersistence 行语义 ─────────────────────────────────────


def test_upsert_row_verbatim_and_payload_json(tmp_path):
    """写穿行逐列 + payload=json.dumps（dict 列禁裸存，铁律）。"""
    p = _persist(tmp_path)
    p.upsert(
        task_id="t1",
        context_id="c1",
        state="submitted",
        status_timestamp="2026-10-21T00:00:00Z",
        payload=json.dumps({"id": "t1", "x": {"y": 1}}, ensure_ascii=False),
    )
    (row,) = _rows(tmp_path / "a2a_tasks.db")
    assert row[0] == "t1" and row[1] == "c1" and row[2] == "submitted"
    assert row[3] == "2026-10-21T00:00:00Z"
    assert json.loads(row[4]) == {"id": "t1", "x": {"y": 1}}


def test_upsert_current_state_semantics(tmp_path):
    """状态快照表语义：同 task_id upsert 覆盖=一任务一行当前态（与 echo 追加表刻意不同）。"""
    p = _persist(tmp_path)
    p.upsert(task_id="t1", context_id="c", state="working", status_timestamp="a", payload="{}")
    p.upsert(task_id="t1", context_id="c", state="completed", status_timestamp="b", payload="{}")
    assert p.count() == 1
    (row,) = _rows(tmp_path / "a2a_tasks.db")
    assert row[2] == "completed" and row[3] == "b"


def test_load_all_order_and_count(tmp_path):
    p = _persist(tmp_path)
    p.upsert(task_id="t2", context_id="c", state="s", status_timestamp="2", payload='{"i":2}')
    p.upsert(task_id="t1", context_id="c", state="s", status_timestamp="1", payload='{"i":1}')
    got = p.load_all()
    assert [d["i"] for d in got] == [1, 2]
    assert p.count() == 2


def test_load_all_missing_db_no_file_created(tmp_path):
    """回载只读不建库（RV-①b-②④ 纪律）：防 import 期建空生产库。"""
    p = _persist(tmp_path)
    assert p.load_all() == []
    assert p.count() == 0
    assert not (tmp_path / "a2a_tasks.db").exists()


def test_load_all_bad_row_skipped_visible(tmp_path, caplog):
    """坏行跳过+真因可见，不因单行坏数据丢整库任务（禁静默丢）。"""
    p = _persist(tmp_path)
    p.upsert(task_id="good", context_id="c", state="s", status_timestamp="1", payload='{"i":1}')
    p.upsert(task_id="bad", context_id="c", state="s", status_timestamp="2", payload="{broken")
    with caplog.at_level("ERROR"):
        got = p.load_all()
    assert [d["i"] for d in got] == [1]
    assert any("不可解" in r.message for r in caplog.records)


# ── ② 写穿（任务变更点 → DB 行）─────────────────────────────────


def test_create_task_write_through(tmp_path):
    """create_task 随写落盘：行 state=context 与内存任务一致。"""
    tm = _manager(tmp_path)
    t = _create(tm, "你好世界", metadata={"k": "值"})
    (row,) = _rows(tmp_path / "a2a_tasks.db")
    assert row[0] == t.id and row[1] == t.contextId and row[2] == "submitted"
    data = json.loads(row[4])
    assert data["metadata"] == {"k": "值"}
    assert data["history"][0]["parts"][0]["text"] == "你好世界"


def test_record_message_and_require_input_write_through(tmp_path):
    """入史/状态迁移变更点写穿：require_input 后行 state=input-required 含问句入史。"""
    tm = _manager(tmp_path)
    t = _create(tm)
    tm.record_message(t.id, _msg("补充参数"), metadata={"m": 1})
    tm.require_input(t, "请补充参数")
    (row,) = _rows(tmp_path / "a2a_tasks.db")
    data = json.loads(row[4])
    assert data["status"]["state"] == "input-required"
    assert [m["role"] for m in data["history"]] == ["user", "user", "agent"]
    assert data["metadata"] == {"m": 1}


def test_cancel_by_user_and_cancel_task_write_through(tmp_path):
    tm = _manager(tmp_path)
    t1 = _create(tm)
    tm.cancel_by_user(t1, "好的已取消")
    t2 = _create(tm)
    asyncio.run(tm.cancel_task(t2.id))
    states = {
        json.loads(r[4])["id"]: json.loads(r[4])["status"]["state"]
        for r in _rows(tmp_path / "a2a_tasks.db")
    }
    assert states[t1.id] == "canceled" and states[t2.id] == "canceled"


def test_artifact_chunk_stream_write_through(tmp_path):
    """artifact 分块变更点写穿（_ArtifactChunkStream._emit → _sync_task）。"""
    from src.a2a.task_manager import _ArtifactChunkStream

    tm = _manager(tmp_path)
    t = _create(tm)
    s = _ArtifactChunkStream(tm, t)
    s.push("第一块")
    s.push("第二块")
    s.close()
    (row,) = [r for r in _rows(tmp_path / "a2a_tasks.db") if r[0] == t.id]
    data = json.loads(row[4])
    assert data["artifacts"][0]["name"] == "response"
    parts = data["artifacts"][0]["parts"]
    assert [p["text"] for p in parts] == ["第一块", "第二块"]


def test_pending_skill_metadata_write_through(tmp_path):
    """pending 路由记忆（task.metadata）变更点写穿：缺参数提问后行内可见。"""
    from src.a2a.task_manager import _PENDING_SKILL_KEY, TaskInputRequired

    tm = _manager(tmp_path)
    t = _create(tm)
    with pytest.raises(TaskInputRequired):
        asyncio.run(tm._process_message("搜索", t))
    (row,) = [r for r in _rows(tmp_path / "a2a_tasks.db") if r[0] == t.id]
    data = json.loads(row[4])
    assert data["metadata"].get(_PENDING_SKILL_KEY) == "search"


# ── ③ 重启回载（nested 面逐键一致）──────────────────────────────


def test_restart_reload_roundtrip_nested(tmp_path):
    """重启回载：history/artifacts/status/metadata nested 面逐键一致（json 往返保真）。"""
    tm = _manager(tmp_path)
    t = _create(tm, "中文消息🀄", metadata={"tags": ["a", "b"], "n": 3})
    tm.record_message(t.id, _msg("第二条"))
    # artifact 追加走真实变更点（_ArtifactChunkStream._emit → _sync_task 写穿）
    from src.a2a.task_manager import _ArtifactChunkStream

    s = _ArtifactChunkStream(tm, t, name="x")
    s.push("块")
    s.close()
    tm.require_input(t, "问句？")

    tm2 = _manager(tmp_path)  # 新实例=重启
    t2 = asyncio.run(tm2.get_task(t.id))
    assert t2 is not None
    assert t2.contextId == t.contextId
    assert t2.status.state == "input-required"
    assert t2.status.message is not None
    assert t2.status.message.parts[0]["text"] == "问句？"
    assert [m.parts[0]["text"] for m in t2.history] == ["中文消息🀄", "第二条", "问句？"]
    assert t2.metadata == {"tags": ["a", "b"], "n": 3}
    assert t2.artifacts[0].name == "x" and t2.artifacts[0].parts[0]["text"] == "块"


def test_restart_list_tasks_sees_persisted(tmp_path):
    tm = _manager(tmp_path)
    t = _create(tm)
    tm2 = _manager(tmp_path)
    page, next_token, total = tm2.list_tasks()
    assert total == 1 and page[0].id == t.id and next_token == ""


def test_restart_pending_skill_survives(tmp_path):
    """pending 路由记忆跨重启存活：§6.3 续接回填不因重启丢路由。"""
    from src.a2a.task_manager import _PENDING_SKILL_KEY, TaskInputRequired

    tm = _manager(tmp_path)
    t = _create(tm)
    with pytest.raises(TaskInputRequired):
        asyncio.run(tm._process_message("搜索", t))
    tm2 = _manager(tmp_path)
    t2 = asyncio.run(tm2.get_task(t.id))
    assert t2 is not None
    assert t2.metadata.get(_PENDING_SKILL_KEY) == "search"


# ── ④ 纯内存零变化 + 故障可见不阻断 ────────────────────────────


def test_persist_none_pure_memory_zero_change(tmp_path):
    """persist=None 纯内存既有语义零变化：建任务/取任务照旧、零落盘、不建库
    （不变面=新旧代码皆过，控制组精确命中依据）。"""
    from src.a2a.task_manager import TaskManager

    tm = TaskManager()
    t = _create(tm)
    assert not (tmp_path / "a2a_tasks.db").exists()
    got = asyncio.run(tm.get_task(t.id))
    assert got is not None and got.id == t.id


def test_write_failure_visible_not_blocking(tmp_path, caplog):
    """写失败=记账旁路纪律：任务本体照常返回，logger.error 可见（非谎报 durable）。"""

    class BoomPersist:
        def upsert(self, **kw):
            raise RuntimeError("db down")

        def load_all(self):
            return []

    from src.a2a.task_manager import TaskManager

    tm = TaskManager(persist=BoomPersist())  # type: ignore[arg-type]  # duck-typed 故障桩
    with caplog.at_level("ERROR"):
        t = _create(tm)
    assert t.id in tm.tasks
    assert any("写穿失败" in r.message for r in caplog.records)


def test_load_failure_visible_and_retries(tmp_path, caplog):
    """回载失败=空任务起步+可见+_loaded 不置位（下次触碰重试，RV-①b-②④ 语义）。"""

    class FlakyPersist:
        def __init__(self):
            self.calls = 0

        def load_all(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("db busy")
            return []

        def upsert(self, **kw):
            pass

    from src.a2a.task_manager import TaskManager

    fp = FlakyPersist()
    tm = TaskManager(persist=fp)  # type: ignore[arg-type]  # duck-typed 故障桩
    with caplog.at_level("ERROR"):
        asyncio.run(tm.get_task("nope"))
    assert tm._loaded is False
    assert any("回载失败" in r.message for r in caplog.records)
    asyncio.run(tm.get_task("nope"))  # 第二次触碰重试
    assert fp.calls == 2 and tm._loaded is True


def test_lazy_load_does_not_clobber_in_memory(tmp_path):
    """懒回载只补缺不覆盖：内存真源优先，禁回载洗掉进程内新态。"""
    tm = _manager(tmp_path)
    t = _create(tm)
    tm._loaded = False  # 强制再次懒回载
    tm.tasks[t.id].status.state = "working"  # 进程内新态
    asyncio.run(tm.get_task(t.id))
    assert tm.tasks[t.id].status.state == "working"


# ── ⑤ 真实路径接线（生产单例）──────────────────────────────────


def test_singleton_wired_with_persist():
    """生产单例真接线：task_manager._persist 非 None（构造零 IO 不建库由上例锁死）。"""
    from src.a2a.task_manager import task_manager

    assert getattr(task_manager, "_persist", None) is not None


def test_singleton_ast_lock_persist_kwarg():
    """AST 静态锁：TaskManager 单例构造恒带 persist= 关键字（禁回退裸实例）。"""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "a2a" / "task_manager.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    hits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "task_manager" for t in node.targets)
        and isinstance(node.value, ast.Call)
    ]
    assert hits, "task_manager 单例构造点不存在"
    kws = {kw.arg for kw in getattr(hits[0].value, "keywords", [])}
    assert "persist" in kws


def test_singleton_write_through_env_isolated(tmp_path, monkeypatch):
    """真实路径：单例 _ensure_loaded/_sync_task 走 env 解析库路径（测试隔离真源）。"""
    from src.a2a.task_manager import task_manager

    monkeypatch.setenv("A2A_TASKS_DB", str(tmp_path / "singleton.db"))
    monkeypatch.setattr(task_manager, "_loaded", False)
    t = _create(task_manager, "单例写穿")
    assert (tmp_path / "singleton.db").exists()
    (row,) = _rows(tmp_path / "singleton.db")
    assert row[0] == t.id
