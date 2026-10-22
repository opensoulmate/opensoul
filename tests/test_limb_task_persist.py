"""RV-①b-⑦-1 RPA 任务行持久化——写穿/懒回载/纯内存零变化/真实路径接线。

修前（RV-① A 组定性）：RPAExecutor._tasks 全在进程内存（dict），任务面（/tasks
列表/单查真源）随 service 重启全清。本文件锁死：任务状态变更写穿单一点、首触
懒回载（在途态 running 如实降 failed 带真因——本执行器无续传口）、_queue 忠实
重建、删行随 delete_task()、纯内存缺省零变化、写/回载失败记账旁路、真实路径
接线（src.api.limb 唯一单例）。

数据面全 tmp（conftest LIMB_TASKS_DB 全隔离+本文件显式 db_path），绝不写生产
data/limb_tasks.db。
"""

import json
import sqlite3

from src.limb.executor import RPAExecutor, RPATask, TaskPriority, TaskStatus
from src.limb.task_persist import RPATaskPersistence, default_db_path
from src.limb.tasks import Action, ActionType, StepResult


def _actions():
    return [
        {
            "action_type": "navigate",
            "target": "https://example.com",
            "value": "",
            "description": "打开页面",
            "timeout": 5,
            "retry": 1,
            "optional": False,
            "condition": "x > 1",
            "metadata": {"k": "v"},
        },
        {"action_type": "click", "target": "#btn"},
    ]


def _make_task(task_id: str = "task-aaaa0001", status: TaskStatus = TaskStatus.QUEUED):
    task = RPATask(
        task_id=task_id,
        name="探针任务",
        actions=[Action.from_dict(a) for a in _actions()],
        status=status,
        priority=TaskPriority.HIGH,
        created_at=1700000000.0,
        started_at=1700000001.0,
        completed_at=1700000002.0,
        current_step=1,
        error="",
        tags=["t1", "探针"],
        variables={"url": "https://x", "中文键": "中文值"},
        tenant_id="tenant-a",
    )
    task.results = [
        StepResult(
            action_type="navigate",
            success=True,
            action_index=0,
            output="y" * 900,  # 展示形截断 500，落盘形须全文保真
            error="",
            duration_ms=12,
            screenshot_path="/tmp/shot.png",
        )
    ]
    return task


# ─── 写穿（状态快照表：同 task_id upsert=当前态） ─────────────────────────


def test_to_state_roundtrip_fields(tmp_path):
    """to_state 逐字段保真（非展示形 to_dict：results 全文/variables/condition）。"""
    task = _make_task()
    state = task.to_state()
    back = RPATask.from_state(state)
    assert back.to_state() == state
    assert back.results[0].output == "y" * 900  # 展示形截断 500 不进落盘形
    assert back.results[0].screenshot_path == "/tmp/shot.png"
    assert back.variables == {"url": "https://x", "中文键": "中文值"}
    assert back.actions[0].condition == "x > 1"
    assert back.actions[0].metadata == {"k": "v"}
    assert back.tags == ["t1", "探针"]
    assert back.priority == TaskPriority.HIGH
    assert back.status == TaskStatus.QUEUED


def test_sync_task_roundtrip_row(tmp_path):
    persist = RPATaskPersistence(db_path=str(tmp_path / "limb.db"))
    ex = RPAExecutor(persist=persist)
    task = _make_task()
    ex._sync_task(task)
    assert persist.count() == 1
    rows = persist.load_all()
    assert len(rows) == 1
    assert rows[0] == task.to_state()  # 逐键 parity：payload 即 to_state 往返
    assert rows[0]["task_id"] == "task-aaaa0001"
    assert rows[0]["status"] == "queued"
    assert rows[0]["tenant_id"] == "tenant-a"


def test_upsert_overwrites_current_state(tmp_path):
    persist = RPATaskPersistence(db_path=str(tmp_path / "limb.db"))
    ex = RPAExecutor(persist=persist)
    task = _make_task()
    ex._sync_task(task)
    task.status = TaskStatus.COMPLETED
    task.error = "x"
    ex._sync_task(task)
    assert persist.count() == 1  # 状态快照表：一个任务一行
    rows = persist.load_all()
    assert rows[0]["status"] == "completed"
    assert rows[0]["error"] == "x"


def test_create_task_writes_row(tmp_path):
    """真实变更点：create_task 落行（不只 _sync_task 单点可写）。"""
    persist = RPATaskPersistence(db_path=str(tmp_path / "limb.db"))
    ex = RPAExecutor(persist=persist)
    task = ex.create_task(name="n", actions=_actions(), tenant_id="tenant-a")
    assert persist.count() == 1
    rows = persist.load_all()
    assert rows[0]["task_id"] == task.task_id
    assert rows[0]["status"] == "queued"
    assert rows[0]["tenant_id"] == "tenant-a"
    assert len(rows[0]["actions"]) == 2


def test_execute_state_chain_persists(tmp_path):
    """执行状态链随写随落盘：running→completed（含 results 落盘）。"""
    persist = RPATaskPersistence(db_path=str(tmp_path / "limb.db"))
    ex = RPAExecutor(persist=persist)
    task = ex.create_task(name="n", actions=_actions())
    ex.execute_task(task.task_id)
    rows = persist.load_all()
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["current_step"] == 2
    assert len(rows[0]["results"]) == 2
    assert rows[0]["completed_at"] is not None


def test_failed_and_cancel_and_delete_rows(tmp_path):
    persist = RPATaskPersistence(db_path=str(tmp_path / "limb.db"))
    ex = RPAExecutor(persist=persist)

    # 失败任务落行（失败路径也写穿）
    def _boom(action, variables):
        return StepResult(action_type=action.action_type.value, success=False, error="boom")

    ex._execute_action = _boom
    t1 = ex.create_task(name="f", actions=_actions())
    ex.execute_task(t1.task_id)
    rows = persist.load_all()
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] == "boom"

    # 取消落行
    t2 = ex.create_task(name="c", actions=_actions())
    assert ex.cancel_task(t2.task_id) is True
    rows = {r["task_id"]: r for r in persist.load_all()}
    assert rows[t2.task_id]["status"] == "cancelled"

    # 删任务即删行（幂等缺行不抛）
    assert ex.delete_task(t1.task_id) is True
    assert persist.count() == 1
    ex._drop_task("task-gone")  # 缺行幂等
    assert persist.count() == 1
    assert ex.delete_task(t2.task_id) is True
    assert persist.count() == 0


# ─── 重启回载 ────────────────────────────────────────────────────────────


def test_restart_reload_fields_parity(tmp_path):
    """双实例=重启：逐键 parity（to_state 真源）。"""
    db = str(tmp_path / "limb.db")
    ex1 = RPAExecutor(persist=RPATaskPersistence(db_path=db))
    task = ex1.create_task(name="n", actions=_actions(), tags=["x"], variables={"v": "1"})
    ex1.execute_task(task.task_id)
    before = task.to_state()

    ex2 = RPAExecutor(persist=RPATaskPersistence(db_path=db))
    got = ex2.get_task(task.task_id)
    assert got is not None
    assert got.to_state() == before
    listed = ex2.list_tasks()
    assert [t["task_id"] for t in listed] == [task.task_id]


def test_reload_inflight_running_downshifts_to_failed(tmp_path):
    """在途态 running 重启后如实降 failed（worker 不在，禁僵死空壳）；
    回载只读不写——DB 行仍记原态，下次变更点覆盖。"""
    db = str(tmp_path / "limb.db")
    persist = RPATaskPersistence(db_path=db)
    task = _make_task(status=TaskStatus.RUNNING)
    persist.upsert(
        task_id=task.task_id,
        status="running",
        payload=json.dumps(task.to_state(), ensure_ascii=False),
    )

    ex = RPAExecutor(persist=persist)
    got = ex.get_task(task.task_id)
    assert got is not None
    assert got.status == TaskStatus.FAILED
    assert "interrupted by service restart" in got.error
    assert got.completed_at is not None
    # 回载只读不写：行真源仍记 running
    assert persist.load_all()[0]["status"] == "running"


def test_reload_rebuilds_queue_faithfully(tmp_path):
    """_queue 忠实重建：行状态非 cancelled 的 id 按回载序（既有语义：创建即
    入队、执行不退队、取消才退队）。"""
    db = str(tmp_path / "limb.db")
    ex1 = RPAExecutor(persist=RPATaskPersistence(db_path=db))
    t1 = ex1.create_task(name="a", actions=_actions())
    t2 = ex1.create_task(name="b", actions=_actions())
    t3 = ex1.create_task(name="c", actions=_actions())
    ex1.execute_task(t1.task_id)  # 执行不退队（既有语义）
    ex1.cancel_task(t3.task_id)  # 取消才退队
    assert list(ex1._queue) == [t1.task_id, t2.task_id]

    ex2 = RPAExecutor(persist=RPATaskPersistence(db_path=db))
    ex2.list_tasks()  # 首触回载
    assert list(ex2._queue) == [t1.task_id, t2.task_id]
    assert ex2.stats()["queue_length"] == 2


def test_reload_only_once(tmp_path):
    """懒回载恰一次：首触后外部新行不再进内存（只补缺语义的前提=只载一次）。"""
    db = str(tmp_path / "limb.db")
    persist = RPATaskPersistence(db_path=db)
    task = _make_task()
    persist.upsert(task_id=task.task_id, status="queued", payload=json.dumps(task.to_state()))
    ex = RPAExecutor(persist=persist)
    assert ex.get_task(task.task_id) is not None
    # 首触后落的新行
    persist.upsert(task_id="task-late", status="queued", payload=json.dumps(task.to_state()))
    assert ex.get_task("task-late") is None  # 不二次回载
    assert ex._loaded is True


def test_reload_bad_row_skipped_visible(tmp_path, caplog):
    """坏行跳过+可见，不因单行坏数据丢整库任务。"""
    db = str(tmp_path / "limb.db")
    persist = RPATaskPersistence(db_path=db)
    task = _make_task()
    persist.upsert(task_id="task-bad", status="queued", payload="{not json")
    persist.upsert(task_id="task-bad2", status="queued", payload=json.dumps(["not", "a", "dict"]))
    persist.upsert(
        task_id="task-bad3",
        status="no-such-status",
        payload=json.dumps({**task.to_state(), "status": "no-such-status"}),
    )
    persist.upsert(task_id=task.task_id, status="queued", payload=json.dumps(task.to_state()))
    ex = RPAExecutor(persist=persist)
    with caplog.at_level("ERROR"):
        got = ex.get_task(task.task_id)
    assert got is not None  # 好行存活
    assert ex.get_task("task-bad") is None
    assert ex.get_task("task-bad2") is None
    assert ex.get_task("task-bad3") is None  # 非法枚举如实跳过
    assert any("跳过" in r.message for r in caplog.records)


def test_reload_does_not_create_db(tmp_path):
    """回载只读不建库：库缺失=空任务起步，禁 import 期建空生产库。"""
    db = str(tmp_path / "nope" / "limb.db")
    ex = RPAExecutor(persist=RPATaskPersistence(db_path=db))
    assert ex.list_tasks() == []
    import os

    assert not os.path.exists(db)


# ─── 纯内存零变化 + 故障旁路 ────────────────────────────────────────────


def test_pure_memory_zero_persist(tmp_path, monkeypatch):
    """缺省 persist=None 纯内存既有语义零变化：不建库、不写库。"""
    monkeypatch.setenv("LIMB_TASKS_DB", str(tmp_path / "never.db"))
    ex = RPAExecutor()
    task = ex.create_task(name="n", actions=_actions())
    ex.execute_task(task.task_id)
    ex.cancel_task(task.task_id)
    assert ex.get_task(task.task_id) is not None
    import os

    assert not os.path.exists(str(tmp_path / "never.db"))
    assert ex._persist is None


def test_write_failure_does_not_block_task(caplog):
    class _Boom:
        def upsert(self, **kw):
            raise RuntimeError("disk full")

        def delete(self, task_id):
            raise RuntimeError("disk full")

        def load_all(self):
            return []

    ex = RPAExecutor(persist=_Boom())
    with caplog.at_level("ERROR"):
        task = ex.create_task(name="n", actions=_actions())
    assert task is not None  # 任务本体不受阻
    assert ex.get_task(task.task_id) is not None  # 内存态照常
    assert any("落盘失败" in r.message for r in caplog.records)


def test_reload_failure_starts_empty(caplog):
    class _Boom:
        def upsert(self, **kw):
            pass

        def delete(self, task_id):
            pass

        def load_all(self):
            raise RuntimeError("db corrupt")

    ex = RPAExecutor(persist=_Boom())
    with caplog.at_level("ERROR"):
        assert ex.list_tasks() == []
    assert any("回载失败" in r.message for r in caplog.records)
    assert ex._loaded is True  # 失败也置位=不逐次重试打满日志（下次重启重试）


# ─── 租户轴往返（既有语义零回归） ────────────────────────────────────────


def test_tenant_axis_roundtrip_and_gate(tmp_path):
    db = str(tmp_path / "limb.db")
    ex1 = RPAExecutor(persist=RPATaskPersistence(db_path=db))
    ta = ex1.create_task(name="a", actions=_actions(), tenant_id="tenant-a")
    tb = ex1.create_task(name="b", actions=_actions(), tenant_id="tenant-b")

    ex2 = RPAExecutor(persist=RPATaskPersistence(db_path=db))
    # 跨租户 by-ID 闸零回归（回载行同样受闸）
    assert ex2.get_task(ta.task_id, tenant_id="tenant-b") is None
    got = ex2.get_task(ta.task_id, tenant_id="tenant-a")
    assert got is not None
    assert got.to_state()["tenant_id"] == "tenant-a"
    listed = ex2.list_tasks(tenant_id="tenant-b")
    assert [t["task_id"] for t in listed] == [tb.task_id]
    # stats 租户桶：回载行计入 total_tasks（total_* 计数器为 ⑦-2 面不持久化）
    st = ex2.stats(tenant_id="tenant-a")
    assert st["total_tasks"] == 1
    assert st["by_status"] == {"queued": 1}


# ─── 真实路径接线 ────────────────────────────────────────────────────────


def test_prod_singleton_wires_persist():
    """AST 静态接线锁死：src.api.limb 单例构造真带 persist 关键字。"""
    import ast
    from pathlib import Path

    src = Path("src/api/limb.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "RPAExecutor":
                kw = {k.arg for k in node.keywords}
                if "persist" in kw:
                    found = True
    assert found, "RPAExecutor 构造缺 persist 关键字=单例未接线"

    import src.api.limb as limb_api

    assert isinstance(limb_api.executor._persist, RPATaskPersistence)


def test_default_db_path_env(monkeypatch):
    monkeypatch.setenv("LIMB_TASKS_DB", "/tmp/x/limb_tasks_test.db")
    assert default_db_path() == "/tmp/x/limb_tasks_test.db"
    monkeypatch.delenv("LIMB_TASKS_DB")
    assert default_db_path().endswith("data/limb_tasks.db")


def test_sqlite_row_shape(tmp_path):
    """行形直查：task_id/status TEXT + payload json 可解（dict 列禁裸存纪律）。"""
    db = str(tmp_path / "limb.db")
    persist = RPATaskPersistence(db_path=db)
    ex = RPAExecutor(persist=persist)
    task = ex.create_task(name="行形", actions=_actions())
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT task_id, status, payload FROM limb_tasks").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    task_id, status, payload = rows[0]
    assert task_id == task.task_id
    assert status == "queued"
    data = json.loads(payload)
    assert data["task_id"] == task.task_id
    assert data["variables"] == {}
