"""RV-①b-⑦-2 RPA 执行历史持久化+计数真源化——追加写穿/懒回载/计数对账行真源/
纯内存零变化/真实路径接线。

修前（RV-① A 组定性）：RPAExecutor._history 与 _total_*/_tenant_counters 全在
进程内存——/history 执行史与 stats 计数随 service 重启归零漂移。本文件锁死：
执行历史追加写穿单一点（追加型禁 upsert 覆盖=禁丢史）、首触懒回载（读/写双首
触，先载后录防回载冲掉在录条目）、计数真源化（stats 恒对账行聚合，内存计数器
=缓存视图）、max_history 窗与计数全表聚合解耦、纯内存缺省零变化、写/回载失败
记账旁路、真实路径接线（src.api.limb 唯一单例）。

数据面全 tmp（conftest LIMB_HISTORY_DB 全隔离+本文件显式 db_path），绝不写生产
data/limb_history.db。
"""

import sqlite3

from src.limb.executor import RPAExecutor, RPATask, TaskPriority, TaskStatus
from src.limb.history_persist import HistoryPersistence, default_db_path
from src.limb.tasks import Action, StepResult


def _task(task_id: str = "task-h0000001", tenant_id: str = "tenant-a") -> RPATask:
    return RPATask(
        task_id=task_id,
        name="历史探针",
        actions=[Action.from_dict({"action_type": "navigate", "target": "https://x"})],
        status=TaskStatus.COMPLETED,
        priority=TaskPriority.NORMAL,
        created_at=1700000000.0,
        started_at=1700000001.0,
        completed_at=1700000002.5,
        current_step=1,
        error="",
        tags=["t1"],
        variables={"k": "v"},
        tenant_id=tenant_id,
    )


# ─── 追加写穿（追加型历史表：同 task_id 多行=多次执行如实各计） ─────────────


def test_insert_roundtrip_fields(tmp_path):
    db = tmp_path / "h.db"
    persist = HistoryPersistence(db_path=str(db))
    persist.insert(
        task_id="task-r1",
        name="中文名",
        status="completed",
        steps=3,
        elapsed_seconds=1.25,
        error="",
        completed_at=1700000002.5,
        tenant_id="tenant-a",
    )
    persist.insert(
        task_id="task-r2",
        name="no-completed-at",
        status="failed",
        steps=0,
        elapsed_seconds=0.5,
        error="boom",
        completed_at=None,
        tenant_id="default",
    )
    rows = persist.load_recent(10)
    assert [r["task_id"] for r in rows] == ["task-r1", "task-r2"]  # rowid 升序=回填序
    assert rows[0] == {
        "task_id": "task-r1",
        "name": "中文名",
        "status": "completed",
        "steps": 3,
        "elapsed_seconds": 1.25,
        "error": "",
        "completed_at": 1700000002.5,
        "tenant_id": "tenant-a",
    }
    assert rows[1]["completed_at"] is None  # NULL 往返不 fabricate
    assert rows[1]["error"] == "boom"


def test_append_not_upsert_same_task_two_rows(tmp_path):
    """追加型语义：同 task_id 两次执行=两行各计，禁 upsert 覆盖=禁丢史。"""
    db = tmp_path / "h.db"
    persist = HistoryPersistence(db_path=str(db))
    for status in ("completed", "failed"):
        persist.insert(
            task_id="task-same",
            name="n",
            status=status,
            steps=1,
            elapsed_seconds=1.0,
            error="",
            completed_at=1.0,
            tenant_id="default",
        )
    rows = persist.load_recent(10)
    assert len(rows) == 2
    assert [r["status"] for r in rows] == ["completed", "failed"]
    assert persist.count() == 2


def test_counters_exact_string_classification(tmp_path):
    """计数按精确串分类（F4b-㉗）：非成败语义串只计 executed 不硬塞。"""
    db = tmp_path / "h.db"
    persist = HistoryPersistence(db_path=str(db))
    specs = [
        ("completed", "tenant-a"),
        ("completed", "tenant-a"),
        ("failed", "tenant-a"),
        ("cancelled", "tenant-a"),  # 非成败语义串：只计 executed
        ("COMPLETED", "tenant-b"),  # 大写不误计（精确串）
        ("failed", "tenant-b"),
    ]
    for i, (status, tenant) in enumerate(specs):
        persist.insert(
            task_id=f"task-c{i}",
            name="n",
            status=status,
            steps=0,
            elapsed_seconds=0.1,
            error="",
            completed_at=1.0,
            tenant_id=tenant,
        )
    c = persist.counters()
    assert c["executed"] == 6
    assert c["succeeded"] == 2
    assert c["failed"] == 2
    assert c["by_tenant"]["tenant-a"] == {"executed": 4, "succeeded": 2, "failed": 1}
    assert c["by_tenant"]["tenant-b"] == {"executed": 2, "succeeded": 0, "failed": 1}


def test_missing_db_readonly_no_create(tmp_path):
    """回载只读不建库：库缺失=空起步，读口不建出空库（防 import 期建生产库）。"""
    db = tmp_path / "absent.db"
    persist = HistoryPersistence(db_path=str(db))
    assert persist.load_recent(5) == []
    assert persist.counters() == {"executed": 0, "succeeded": 0, "failed": 0, "by_tenant": {}}
    assert persist.count() == 0
    assert not db.exists()


# ─── 真实执行路径写穿 ────────────────────────────────────────────────────


def _run(executor: RPAExecutor, name: str, tenant_id: str, fail: bool = False) -> RPATask:
    task = executor.create_task(name=name, actions=[], tenant_id=tenant_id)
    if fail:

        def _boom(action, variables):
            return StepResult(
                action_type=action.action_type,
                success=False,
                error="动作失败探针",
            )

        executor._execute_action = _boom  # noqa: SLF001（真执行路径失败分支驱动）
        task.actions = [Action.from_dict({"action_type": "click", "target": "#x"})]
    done = executor.execute_task(task.task_id)
    assert done is not None
    return done


def test_write_through_on_real_execute(tmp_path):
    db = tmp_path / "h.db"
    executor = RPAExecutor(history_persist=HistoryPersistence(db_path=str(db)))
    ok = _run(executor, "成功任务", "tenant-a")
    bad = _run(executor, "失败任务", "tenant-b", fail=True)
    assert ok.status == TaskStatus.COMPLETED
    assert bad.status == TaskStatus.FAILED
    rows = HistoryPersistence(db_path=str(db)).load_recent(10)
    assert [(r["name"], r["status"], r["tenant_id"]) for r in rows] == [
        ("成功任务", "completed", "tenant-a"),
        ("失败任务", "failed", "tenant-b"),
    ]
    assert rows[1]["error"] == "动作失败探针"
    assert rows[0]["completed_at"] is not None
    assert rows[0]["steps"] == 0  # 空 actions=0 步如实


# ─── 懒回载（重启不丢）+ 计数真源化 ────────────────────────────────────


def test_history_and_counters_survive_restart(tmp_path):
    db = tmp_path / "h.db"
    a = RPAExecutor(history_persist=HistoryPersistence(db_path=str(db)))
    _run(a, "任务一", "tenant-a")
    _run(a, "任务二", "tenant-a")
    _run(a, "任务三", "tenant-b", fail=True)
    # 模拟重启：全新实例同库（内存全空）
    b = RPAExecutor(history_persist=HistoryPersistence(db_path=str(db)))
    hist = b.get_history()
    assert [h["name"] for h in hist] == ["任务一", "任务二", "任务三"]
    stats = b.stats()
    assert stats["total_executed"] == 3  # 修前重启归零
    assert stats["total_succeeded"] == 2
    assert stats["total_failed"] == 1
    assert stats["success_rate"] == 66.7
    # 分租户桶真源重建
    sa = b.stats(tenant_id="tenant-a")
    sb = b.stats(tenant_id="tenant-b")
    assert sa["total_executed"] == 2 and sa["total_succeeded"] == 2
    assert sb["total_executed"] == 1 and sb["total_failed"] == 1


def test_stats_reads_row_truth_not_cache(tmp_path):
    """计数真源化硬反证：内存计数缓存被毒化，stats 仍读行聚合真源。"""
    db = tmp_path / "h.db"
    executor = RPAExecutor(history_persist=HistoryPersistence(db_path=str(db)))
    _run(executor, "任务一", "tenant-a")
    executor._total_executed = 999  # noqa: SLF001（毒化缓存）
    executor._tenant_counters.clear()  # noqa: SLF001
    stats = executor.stats()
    assert stats["total_executed"] == 1 and stats["total_succeeded"] == 1
    assert executor.stats(tenant_id="tenant-a")["total_executed"] == 1
    assert executor.stats(tenant_id="tenant-z")["total_executed"] == 0


def test_window_trim_vs_counter_full_table(tmp_path):
    """内存窗裁剪（max_history）不减计数：计数聚合恒算全表（诚实边界③）。"""
    db = tmp_path / "h.db"
    executor = RPAExecutor(max_history=2, history_persist=HistoryPersistence(db_path=str(db)))
    for i in range(4):
        _run(executor, f"任务{i}", "tenant-a")
    assert [h["name"] for h in executor.get_history()] == ["任务2", "任务3"]  # 内存窗
    persist = HistoryPersistence(db_path=str(db))
    assert [r["name"] for r in persist.load_recent(2)] == ["任务2", "任务3"]
    assert persist.count() == 4  # 表全量不裁剪
    assert executor.stats()["total_executed"] == 4  # 计数不随窗减
    # 重启后回载窗=最近 2 行，计数仍全表 4
    b = RPAExecutor(max_history=2, history_persist=HistoryPersistence(db_path=str(db)))
    assert [h["name"] for h in b.get_history()] == ["任务2", "任务3"]
    assert b.stats()["total_executed"] == 4


def test_lazy_load_first_touch_is_record(tmp_path):
    """写首触懒回载：先载后录，新录条目不被回载冲掉（生产不变量）。"""
    db = tmp_path / "h.db"
    a = RPAExecutor(history_persist=HistoryPersistence(db_path=str(db)))
    _run(a, "旧任务", "tenant-a")
    b = RPAExecutor(history_persist=HistoryPersistence(db_path=str(db)))
    _run(b, "新任务", "tenant-a")  # 首触=首次写
    names = [h["name"] for h in b.get_history()]
    assert names == ["旧任务", "新任务"]  # 旧条目回载+新条目在录各恰一次


# ─── 纯内存零变化 + 失败旁路 ────────────────────────────────────────────


def test_pure_memory_zero_change(tmp_path, monkeypatch):
    """缺省 history_persist=None=纯内存既有语义零变化，且不落任何库。"""
    monkeypatch.setenv("LIMB_HISTORY_DB", str(tmp_path / "should_not_exist.db"))
    executor = RPAExecutor()
    _run(executor, "内存任务", "tenant-a")
    hist = executor.get_history()
    assert [h["name"] for h in hist] == ["内存任务"]
    stats = executor.stats()
    assert stats["total_executed"] == 1 and stats["total_succeeded"] == 1
    assert stats["by_status"] == {"completed": 1}
    assert not (tmp_path / "should_not_exist.db").exists()
    assert default_db_path() == str(tmp_path / "should_not_exist.db")


def test_write_failure_bypass(tmp_path):
    """落盘失败=记账旁路：执行本体照常完成，历史留内存，不抛不阻断。"""

    class BrokenPersist:
        def insert(self, **kwargs):
            raise RuntimeError("磁盘满探针")

        def load_recent(self, limit=1000):
            return []

        def counters(self):
            raise RuntimeError("磁盘满探针")

    executor = RPAExecutor(history_persist=BrokenPersist())
    task = _run(executor, "旁路任务", "tenant-a")
    assert task.status == TaskStatus.COMPLETED  # 执行本体不受阻
    assert [h["name"] for h in executor.get_history()] == ["旁路任务"]
    # 计数对账失败→回落内存计数缓存（缓存本职，禁静默假 0）
    assert executor.stats()["total_executed"] == 1


def test_load_failure_starts_empty_visible(tmp_path):
    """回载失败=空历史起步（行真源仍在库），后续新录照常进内存。"""

    class LoadBroken:
        def __init__(self):
            self.inserted = 0

        def insert(self, **kwargs):
            self.inserted += 1

        def load_recent(self, limit=1000):
            raise RuntimeError("库锁死探针")

        def counters(self):
            return {"executed": 0, "succeeded": 0, "failed": 0, "by_tenant": {}}

    persist = LoadBroken()
    executor = RPAExecutor(history_persist=persist)
    task = _run(executor, "新录任务", "tenant-a")
    assert task.status == TaskStatus.COMPLETED
    assert persist.inserted == 1  # 写穿真发生
    assert [h["name"] for h in executor.get_history()] == ["新录任务"]


# ─── 真实路径接线（src.api.limb 唯一单例） ───────────────────────────────


def test_real_wiring_singleton_and_env_isolation(tmp_path, monkeypatch):
    import src.api.limb as limb_api

    inst = limb_api.executor
    assert inst._history_persist is not None  # noqa: SLF001（构造即接真 persist）
    # conftest 每用例 tmp 真源：写穿落 tmp 库不落生产
    monkeypatch.setenv("LIMB_HISTORY_DB", str(tmp_path / "wired.db"))
    inst._history_loaded = False  # noqa: SLF001
    _run(inst, "接线任务", "default")
    assert (tmp_path / "wired.db").exists()
    assert HistoryPersistence(db_path=str(tmp_path / "wired.db")).count() >= 1
    # 读面契约键集不变（/history /stats 消费形）
    assert set(inst.get_history()[0].keys()) == {
        "task_id",
        "name",
        "status",
        "steps",
        "elapsed_seconds",
        "error",
        "completed_at",
        "tenant_id",
    }
    stats = inst.stats()
    for key in (
        "total_tasks",
        "by_status",
        "queue_length",
        "running",
        "total_executed",
        "total_succeeded",
        "total_failed",
        "success_rate",
        "templates",
    ):
        assert key in stats
