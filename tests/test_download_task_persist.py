"""RV-①b-⑥-2 NativeDownloader 下载任务行持久化——写穿/懒回载/纯内存零变化/接线。

修前（RV-① A 组定性）：NativeDownloader._tasks 全在进程内存（dict），任务面
（/tasks 列表/单查真源）随 service 重启全清。本文件锁死：状态变更写穿单一点、
首触懒回载（在途态如实置 paused 走真续传口）、删行随 remove()、纯内存缺省零
变化、写/回载失败记账旁路、真实路径接线（get_downloader 唯一单例）。

数据面全 tmp（conftest DOWNLOAD_TASKS_DB 全隔离+本文件显式 db_path），绝不写
生产 data/download_tasks.db。
"""

import asyncio
import json
import sqlite3

from src.api.download_persist import DownloadTaskPersistence, default_db_path
from src.api.native_downloader import (
    DownloadStatus,
    DownloadTask,
    NativeDownloader,
    get_downloader,
)


def _make_task(
    task_id: str = "t1", status: DownloadStatus = DownloadStatus.PENDING
) -> DownloadTask:
    return DownloadTask(
        id=task_id,
        url="http://example.com/f.bin",
        dest=f"/tmp/dl_{task_id}.bin",
        status=status,
        total_bytes=1024,
        downloaded_bytes=128,
        segments=4,
        supports_resume=True,
        plugin="native",
        added_at=1700000000.0,
    )


def _noop_worker(self, task):
    async def _run(_task):
        return None

    return _run(task)


# ─── 写穿（状态快照表：同 task_id upsert=当前态） ─────────────────────────


def test_sync_task_roundtrip_fields(tmp_path):
    persist = DownloadTaskPersistence(db_path=str(tmp_path / "dl.db"))
    dl = NativeDownloader(persist=persist)
    task = _make_task()
    task.error = "连接超时"
    dl._sync_task(task)
    assert persist.count() == 1
    rows = persist.load_all()
    assert len(rows) == 1
    # 逐键 parity：payload 即 to_dict 往返（progress_pct 为派生属性随行保留）
    assert rows[0] == task.to_dict()
    assert rows[0]["id"] == "t1"
    assert rows[0]["status"] == "pending"
    assert rows[0]["error"] == "连接超时"


def test_upsert_overwrites_current_state(tmp_path):
    persist = DownloadTaskPersistence(db_path=str(tmp_path / "dl.db"))
    dl = NativeDownloader(persist=persist)
    task = _make_task()
    dl._sync_task(task)
    task.status = DownloadStatus.DONE
    task.downloaded_bytes = 1024
    dl._sync_task(task)
    assert persist.count() == 1  # 状态快照表：一个任务一行
    rows = persist.load_all()
    assert rows[0]["status"] == "done"
    assert rows[0]["downloaded_bytes"] == 1024


def test_add_download_registers_row(tmp_path, monkeypatch):
    persist = DownloadTaskPersistence(db_path=str(tmp_path / "dl.db"))
    dl = NativeDownloader(persist=persist)
    monkeypatch.setattr(NativeDownloader, "_run_download", _noop_worker)
    task = asyncio.run(dl.add_download("http://example.com/f.bin", "/tmp/dl_t.bin"))
    assert persist.count() == 1
    rows = persist.load_all()
    assert rows[0]["id"] == task.id
    assert rows[0]["status"] == "pending"


def test_status_transitions_write_through(tmp_path, monkeypatch):
    persist = DownloadTaskPersistence(db_path=str(tmp_path / "dl.db"))
    dl = NativeDownloader(persist=persist)
    monkeypatch.setattr(NativeDownloader, "_run_download", _noop_worker)
    task = asyncio.run(dl.add_download("http://example.com/f.bin", "/tmp/dl_t.bin"))
    task.status = DownloadStatus.DOWNLOADING
    dl._sync_task(task)

    asyncio.run(dl.pause(task.id))
    assert persist.load_all()[0]["status"] == "paused"

    asyncio.run(dl.resume(task.id))
    assert persist.load_all()[0]["status"] == "downloading"

    asyncio.run(dl.cancel(task.id))
    assert persist.load_all()[0]["status"] == "cancelled"


def test_remove_deletes_row(tmp_path, monkeypatch):
    persist = DownloadTaskPersistence(db_path=str(tmp_path / "dl.db"))
    dl = NativeDownloader(persist=persist)
    monkeypatch.setattr(NativeDownloader, "_run_download", _noop_worker)
    task = asyncio.run(dl.add_download("http://example.com/f.bin", "/tmp/dl_t.bin"))
    assert persist.count() == 1
    asyncio.run(dl.remove(task.id))
    assert persist.count() == 0
    assert dl.get_task(task.id) is None
    # 删行幂等：再删不抛
    asyncio.run(dl.remove(task.id))
    assert persist.count() == 0


# ─── 重启回载（懒回载+在途态如实置 paused 走真续传口） ────────────────────


def test_reload_roundtrip_two_tasks(tmp_path, monkeypatch):
    db = str(tmp_path / "dl.db")
    persist_a = DownloadTaskPersistence(db_path=db)
    dl_a = NativeDownloader(persist=persist_a)
    t1 = _make_task("t1", DownloadStatus.DONE)
    t2 = _make_task("t2", DownloadStatus.ERROR)
    t2.error = "连接失败: refused"
    dl_a._sync_task(t1)
    dl_a._sync_task(t2)

    # 重启=新实例（内存空）同库回载
    dl_b = NativeDownloader(persist=DownloadTaskPersistence(db_path=db))
    tasks = {t.id: t for t in dl_b.list_tasks()}
    assert set(tasks) == {"t1", "t2"}
    for orig in (t1, t2):
        got = tasks[orig.id]
        assert got.url == orig.url
        assert got.dest == orig.dest
        assert got.status == orig.status
        assert got.total_bytes == orig.total_bytes
        assert got.downloaded_bytes == orig.downloaded_bytes
        assert got.error == orig.error
        assert got.added_at == orig.added_at
        assert got.supports_resume == orig.supports_resume


def test_reload_inflight_becomes_paused(tmp_path):
    db = str(tmp_path / "dl.db")
    dl_a = NativeDownloader(persist=DownloadTaskPersistence(db_path=db))
    for tid, st in (
        ("t1", DownloadStatus.PENDING),
        ("t2", DownloadStatus.CONNECTING),
        ("t3", DownloadStatus.DOWNLOADING),
        ("t4", DownloadStatus.PAUSED),
        ("t5", DownloadStatus.DONE),
    ):
        dl_a._sync_task(_make_task(tid, st))

    dl_b = NativeDownloader(persist=DownloadTaskPersistence(db_path=db))
    tasks = {t.id: t for t in dl_b.list_tasks()}
    # 重启后 worker 不在：在途态如实置 paused（禁 downloading 僵死空壳）
    assert tasks["t1"].status == DownloadStatus.PAUSED
    assert tasks["t2"].status == DownloadStatus.PAUSED
    assert tasks["t3"].status == DownloadStatus.PAUSED
    # 非在途态原样回载
    assert tasks["t4"].status == DownloadStatus.PAUSED
    assert tasks["t5"].status == DownloadStatus.DONE


def test_reload_paused_task_can_resume(tmp_path, monkeypatch):
    db = str(tmp_path / "dl.db")
    dl_a = NativeDownloader(persist=DownloadTaskPersistence(db_path=db))
    dl_a._sync_task(_make_task("t1", DownloadStatus.DOWNLOADING))

    dl_b = NativeDownloader(persist=DownloadTaskPersistence(db_path=db))
    task = dl_b.get_task("t1")
    assert task is not None
    assert task.status == DownloadStatus.PAUSED

    ran = []

    def _recording_worker(self, _task):
        async def _run(task):
            ran.append(task.id)

        return _run(_task)

    monkeypatch.setattr(NativeDownloader, "_run_download", _recording_worker)
    asyncio.run(dl_b.resume("t1"))
    assert ran == ["t1"]  # 真续传口重开 worker
    task_after = dl_b.get_task("t1")
    assert task_after is not None
    assert task_after.status == DownloadStatus.DOWNLOADING


def test_reload_reconstructs_events(tmp_path):
    db = str(tmp_path / "dl.db")
    dl_a = NativeDownloader(persist=DownloadTaskPersistence(db_path=db))
    dl_a._sync_task(_make_task("t1", DownloadStatus.PAUSED))

    dl_b = NativeDownloader(persist=DownloadTaskPersistence(db_path=db))
    task = dl_b.get_task("t1")
    assert task is not None
    assert isinstance(task._cancel_event, asyncio.Event)
    assert isinstance(task._pause_event, asyncio.Event)


def test_lazy_load_only_once(tmp_path):
    db = str(tmp_path / "dl.db")
    persist = DownloadTaskPersistence(db_path=db)
    NativeDownloader(persist=persist)._sync_task(_make_task())

    class SpyPersistence(DownloadTaskPersistence):
        calls = 0

        def load_all(self):
            SpyPersistence.calls += 1
            return super().load_all()

    dl = NativeDownloader(persist=SpyPersistence(db_path=db))
    dl.list_tasks()
    dl.list_tasks()
    dl.get_task("t1")
    assert SpyPersistence.calls == 1  # 首触懒回载只回载一次


def test_bad_row_skipped_visible(tmp_path, caplog):
    db = str(tmp_path / "dl.db")
    # 坏行直接落库（payload 非法 JSON）+ 一行好行
    persist = DownloadTaskPersistence(db_path=db)
    NativeDownloader(persist=persist)._sync_task(_make_task("good"))
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO download_tasks (task_id, status, payload) VALUES (?, ?, ?)",
            ("bad", "pending", "{not-json"),
        )
        conn.commit()

    dl = NativeDownloader(persist=DownloadTaskPersistence(db_path=db))
    tasks = dl.list_tasks()
    assert [t.id for t in tasks] == ["good"]  # 单行坏数据不丢整库任务
    assert any("跳过" in r.message for r in caplog.records)  # 禁静默丢


# ─── 纯内存零变化 + 失败旁路 ──────────────────────────────────────────────


def test_pure_memory_zero_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_TASKS_DB", str(tmp_path / "prod_would_be.db"))
    dl = NativeDownloader()  # 缺省 persist=None
    monkeypatch.setattr(NativeDownloader, "_run_download", _noop_worker)
    asyncio.run(dl.add_download("http://example.com/f.bin", "/tmp/dl_t.bin"))
    assert len(dl.list_tasks()) == 1
    assert not (tmp_path / "prod_would_be.db").exists()  # 零落盘=修前语义


def test_write_failure_not_blocking(tmp_path, monkeypatch, caplog):
    class BrokenPersistence(DownloadTaskPersistence):
        def upsert(self, **kwargs):
            raise RuntimeError("disk full")

    dl = NativeDownloader(persist=BrokenPersistence(db_path=str(tmp_path / "dl.db")))
    monkeypatch.setattr(NativeDownloader, "_run_download", _noop_worker)
    task = asyncio.run(dl.add_download("http://example.com/f.bin", "/tmp/dl_t.bin"))
    assert dl.get_task(task.id) is task  # 写失败=下载本体不受阻（任务照常注册）
    assert any("落盘失败" in r.message for r in caplog.records)  # 记账旁路可见


def test_load_failure_empty_start(tmp_path, caplog):
    class BrokenLoad(DownloadTaskPersistence):
        def load_all(self):
            raise RuntimeError("db corrupt")

    dl = NativeDownloader(persist=BrokenLoad(db_path=str(tmp_path / "dl.db")))
    assert dl.list_tasks() == []  # 空任务起步
    assert any("回载失败" in r.message for r in caplog.records)  # 真因可见


# ─── 持久化真源契约（回载只读不建库/env 解析/upsert 列值权威） ────────────


def test_load_all_readonly_no_db_create(tmp_path):
    path = tmp_path / "never.db"
    persist = DownloadTaskPersistence(db_path=str(path))
    assert persist.load_all() == []
    assert not path.exists()  # 回载只读不建库（写穿才建库）
    assert persist.count() == 0
    assert not path.exists()


def test_default_db_path_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DOWNLOAD_TASKS_DB", str(tmp_path / "env.db"))
    assert default_db_path() == str(tmp_path / "env.db")


def test_delete_idempotent_missing_row(tmp_path):
    persist = DownloadTaskPersistence(db_path=str(tmp_path / "dl.db"))
    persist.upsert(task_id="t1", status="pending", payload=json.dumps({"id": "t1"}))
    persist.delete("nope")  # 不存在=无操作不抛
    assert persist.count() == 1


# ─── 真实路径接线（生产单例唯一消费面） ──────────────────────────────────


def test_get_downloader_wired_persist():
    inst = get_downloader()
    assert isinstance(inst._persist, DownloadTaskPersistence)  # 生产单例真接线
    # 纯内存缺省面（测试显式构造）零变化
    assert NativeDownloader()._persist is None
