"""RV-①b-⑥-3 DownloadPlugin 插件任务行持久化——写穿/懒回载/纯内存零变化/接线。

修前（RV-① A 组定性+live --pre 硬证）：_running/_paused 全在进程内存（每插件类
独立 dict），_track/pause/resume_download/cancel 只改内存——插件任务面随 service
重启全清、半成品无人认领。本文件锁死：状态变更写穿单一点、首触懒回载（在途
'downloading' 行如实降 paused 走真续传口——进程手柄不持久化也无法持久化）、
删行随 cancel/终态、每插件类行命名空间隔离、纯内存缺省零变化、写/回载失败记账
旁路、真实路径接线（get_download_manager 生产单例）。

数据面全 tmp（conftest DOWNLOAD_PLUGIN_TASKS_DB 全隔离+本文件显式 db_path），
绝不写生产 data/download_plugin_tasks.db。
"""

import asyncio
import sqlite3
from typing import Optional
from unittest.mock import MagicMock

import pytest

from src.api.download_plugins import (
    DownloadManager,
    DownloadPlugin,
    DownloadProgress,
    PluginInfo,
    get_download_manager,
)
from src.api.download_plugins_persist import DownloadPluginTaskPersistence, default_db_path


class _StubPlugin(DownloadPlugin):
    """最小插件实现：download 可控登记生命周期（_track→[_gate 等待]→_untrack）。"""

    def __init__(self):
        self.calls = []
        self.gate: asyncio.Event | None = None  # 置位后 download 才收尾

    def get_info(self) -> PluginInfo:
        return PluginInfo(
            id="stub",
            name="stub",
            description="",
            version="1",
            binary="stub",
            supports_resume=True,
            supports_p2p=False,
            install_cmd={},
            update_cmd={},
            check_version_cmd="",
        )

    async def download(self, url, dest, resume=True, progress_cb=None):
        self.calls.append((url, dest, resume))
        self._track(dest, url, dest, resume, MagicMock())
        if self.gate is not None:
            await self.gate.wait()
        progress = DownloadProgress(url=url, dest=dest, status="done")
        self._untrack(dest, progress)
        return progress

    def is_available(self) -> bool:
        return True

    async def install(self) -> bool:
        return True

    async def update(self) -> bool:
        return True

    async def get_version(self) -> str:
        return "stub-1"


def _fresh(name: str = "P") -> _StubPlugin:
    """新插件类=新类级注册表（__init_subclass__ 建）。"""
    cls = type(name, (_StubPlugin,), {})
    return cls()


def _restart(inst: _StubPlugin) -> _StubPlugin:
    """重启模拟：同类名新类（新进程里类名不变、注册表从空起步）。"""
    return _fresh(type(inst).__name__)


def _persist(tmp_path, name: str = "p.db") -> DownloadPluginTaskPersistence:
    return DownloadPluginTaskPersistence(db_path=str(tmp_path / name))


def _ctx_check(rows, *, plugin: str, task_id: str, state: str, url: str, dest: str, resume: bool):
    assert len(rows) == 1
    assert rows[0] == {
        "plugin": plugin,
        "task_id": task_id,
        "state": state,
        "url": url,
        "dest": dest,
        "resume": resume,
    }


# ─── 写穿（状态快照表：同 (plugin, task_id) upsert=当前态） ─────────────


def test_track_writes_downloading_row(tmp_path):
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    assert persist.count() == 1
    _ctx_check(
        persist.load_all(),
        plugin="P",
        task_id="/d/f.bin",
        state="downloading",
        url="http://x/f.bin",
        dest="/d/f.bin",
        resume=True,
    )


def test_pause_writes_paused_row(tmp_path):
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", False, MagicMock())
    assert asyncio.run(p.pause("/d/f.bin")) is True
    rows = persist.load_all()
    assert rows[0]["state"] == "paused"
    assert rows[0]["resume"] is False  # 上下文逐键保真


def test_upsert_overwrites_current_state(tmp_path):
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    asyncio.run(p.pause("/d/f.bin"))
    assert persist.count() == 1  # 状态快照表：一个任务一行
    assert persist.load_all()[0]["state"] == "paused"


def test_untrack_done_deletes_row(tmp_path):
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    p._untrack("/d/f.bin", DownloadProgress(url="u", dest="/d/f.bin", status="done"))
    assert persist.count() == 0
    assert p.task_states() == {}


def test_untrack_pause_landed_keeps_paused_row(tmp_path):
    """pause() 的 SIGTERM 收尾（error→relabeled paused）：行保留 'paused' 续传上下文。"""
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    asyncio.run(p.pause("/d/f.bin"))
    progress = DownloadProgress(url="u", dest="/d/f.bin", status="error", error="killed")
    p._untrack("/d/f.bin", progress)
    assert progress.status == "paused"  # 既有语义零变化
    assert persist.count() == 1
    assert persist.load_all()[0]["state"] == "paused"


def test_untrack_done_after_pause_discards_ctx_and_row(tmp_path):
    """pause 落地前刚好跑完：续传上下文作废=行同删（行随注册表终态）。"""
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    asyncio.run(p.pause("/d/f.bin"))
    p._untrack("/d/f.bin", DownloadProgress(url="u", dest="/d/f.bin", status="done"))
    assert p.task_states() == {}
    assert persist.count() == 0


def test_cancel_deletes_row_running_and_paused(tmp_path):
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/a.bin", "http://x/a.bin", "/d/a.bin", True, MagicMock())
    assert asyncio.run(p.cancel("/d/a.bin")) is True
    assert persist.count() == 0
    p._track("/d/b.bin", "http://x/b.bin", "/d/b.bin", True, MagicMock())
    asyncio.run(p.pause("/d/b.bin"))
    assert asyncio.run(p.cancel("/d/b.bin")) is True
    assert persist.count() == 0


# ─── resume/续传（行随注册表对齐：消费上下文即删行，重跑 _track 再落行） ──


def test_resume_drops_row_then_track_rewrites(tmp_path):
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    asyncio.run(p.pause("/d/f.bin"))
    assert asyncio.run(p.resume_download("/d/f.bin")) is True
    asyncio.run(asyncio.sleep(0.02))  # 后台 download() 跑完（stub 即时收尾）
    assert p.calls == [("http://x/f.bin", "/d/f.bin", True)]  # 真续传=重跑 resume=True
    assert p.task_states() == {}  # stub 即时收尾
    assert persist.count() == 0  # 终态删行


def test_resume_inflight_row_is_downloading(tmp_path):
    """重跑在途时行='downloading'（gate 挂住 stub download），收尾后删行。"""
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    asyncio.run(p.pause("/d/f.bin"))

    async def _main():
        p.gate = asyncio.Event()
        assert await p.resume_download("/d/f.bin") is True
        await asyncio.sleep(0.02)
        assert p.task_states() == {"/d/f.bin": "downloading"}
        assert persist.load_all()[0]["state"] == "downloading"
        p.gate.set()
        await asyncio.sleep(0.02)

    asyncio.run(_main())
    assert persist.count() == 0


# ─── 懒回载（重启语义：'downloading' 如实降 paused，禁谎报） ─────────────


def test_reload_paused_row_restores_resume_context(tmp_path):
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    asyncio.run(p.pause("/d/f.bin"))

    p2 = _restart(p)
    p2.attach_persist(persist)
    assert p2.task_states() == {"/d/f.bin": "paused"}
    assert p2._paused["/d/f.bin"] == ("http://x/f.bin", "/d/f.bin", True)


def test_reload_downloading_row_honest_paused(tmp_path):
    """进程手柄不持久化（诚实边界④）：在途行回载如实降 paused，行同步改写。"""
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())

    p2 = _restart(p)
    p2.attach_persist(persist)
    assert p2.task_states() == {"/d/f.bin": "paused"}  # 禁谎报 downloading
    rows = persist.load_all()
    assert rows[0]["state"] == "paused"  # 行与内存双对齐
    # 真续传口对回载上下文生效
    assert asyncio.run(p2.resume_download("/d/f.bin")) is True


def test_reload_skips_bad_rows_visible_not_fatal(tmp_path):
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/good.bin", "http://x/good.bin", "/d/good.bin", True, MagicMock())
    asyncio.run(p.pause("/d/good.bin"))
    # 坏行直插：payload 非法 json + state 不可解
    conn = sqlite3.connect(str(tmp_path / "p.db"))
    conn.execute(
        "INSERT INTO download_plugin_tasks (plugin, task_id, state, payload) VALUES (?,?,?,?)",
        ("P", "/d/bad.json", "paused", "{not json"),
    )
    conn.execute(
        "INSERT INTO download_plugin_tasks (plugin, task_id, state, payload) VALUES (?,?,?,?)",
        ("P", "/d/bad.state", "weird", '{"url": "u", "dest": "/d/bad.state", "resume": true}'),
    )
    conn.commit()
    conn.close()

    p2 = _restart(p)
    p2.attach_persist(persist)
    assert p2.task_states() == {"/d/good.bin": "paused"}  # 单行坏数据不丢整库任务
    assert persist.count() == 3  # 坏行仍在库（跳过≠删除）


def test_reload_does_not_clobber_live_registry(tmp_path):
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    # 内存在册更真（正在跑的新传输），行回载禁覆盖
    p._running["/d/f.bin"] = p._running["/d/f.bin"].__class__(
        url="http://live/new", dest="/d/f.bin", resume=False, proc=MagicMock()
    )
    p._persist_loaded = False  # 强制首触回载
    assert p.task_states() == {"/d/f.bin": "downloading"}
    assert p._paused == {}


def test_lazy_load_reads_only_once(tmp_path):
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    p2 = _restart(p)
    calls = []
    orig = persist.load_all

    def _spy(plugin=None):
        calls.append(plugin)
        return orig(plugin=plugin)

    persist.load_all = _spy  # type: ignore[method-assign]
    p2.attach_persist(persist)
    p2.task_states()
    p2.task_states()
    assert calls == ["P"]  # 懒回载恰一次


def test_namespace_isolated_per_plugin_class(tmp_path):
    persist = _persist(tmp_path)
    pa, pb = _fresh("PA"), _fresh("PB")
    pa.attach_persist(persist)
    pb.attach_persist(persist)
    pa._track("/d/same.bin", "http://a/", "/d/same.bin", True, MagicMock())
    pb._track("/d/same.bin", "http://b/", "/d/same.bin", False, MagicMock())
    assert persist.count() == 2
    # 重启后各回各的行（同 task_id 不串）
    pa2, pb2 = _restart(pa), _restart(pb)
    pa2.attach_persist(persist)
    pb2.attach_persist(persist)
    pa2.task_states()  # 首触懒回载
    pb2.task_states()
    assert pa2._paused["/d/same.bin"][0] == "http://a/"
    assert pb2._paused["/d/same.bin"][0] == "http://b/"
    assert pb2._paused["/d/same.bin"][2] is False


# ─── 纯内存零变化 + 记账旁路 ────────────────────────────────────────────


def test_default_pure_memory_no_db_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_PLUGIN_TASKS_DB", str(tmp_path / "never.db"))
    p = _fresh("P")  # 未 attach_persist
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    asyncio.run(p.pause("/d/f.bin"))
    assert not (tmp_path / "never.db").exists()
    assert p.task_states() == {"/d/f.bin": "paused"}  # 行为面零变化


def test_attach_none_detaches_back_to_memory(tmp_path):
    persist = _persist(tmp_path)
    p = _fresh("P")
    p.attach_persist(persist)
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())
    assert persist.count() == 1
    p.attach_persist(None)  # 拆线
    p._track("/d/g2.bin", "http://x/g2.bin", "/d/g2.bin", True, MagicMock())
    assert persist.count() == 1  # 不再落盘


def test_write_failure_bypass_does_not_block_transfer(tmp_path):
    class _Broken:
        def upsert(self, **kw):
            raise RuntimeError("disk full")

        def delete(self, **kw):
            raise RuntimeError("disk full")

        def load_all(self, plugin=None):
            return []

    p = _fresh("P")
    p.attach_persist(_Broken())
    p._track("/d/f.bin", "http://x/f.bin", "/d/f.bin", True, MagicMock())  # 不抛
    assert p.task_states() == {"/d/f.bin": "downloading"}  # 内存即生效
    assert asyncio.run(p.cancel("/d/f.bin")) is True  # 删行失败也不抛


def test_load_failure_bypass_starts_empty(tmp_path):
    class _Broken:
        def upsert(self, **kw):
            pass

        def delete(self, **kw):
            pass

        def load_all(self, plugin=None):
            raise RuntimeError("db locked")

    p = _fresh("P")
    p.attach_persist(_Broken())
    assert p.task_states() == {}  # 空注册表起步不抛


def test_delete_idempotent_and_count_zero_without_db(tmp_path):
    persist = _persist(tmp_path)
    assert persist.count() == 0  # 库缺失=0（count 只读不建库）
    persist.delete(plugin="P", task_id="/d/nope.bin")  # 幂等不抛
    persist.delete(plugin="P", task_id="/d/nope.bin")  # 再删仍幂等
    assert persist.count() == 0


# ─── 真实路径接线（get_download_manager 生产单例） ──────────────────────


def test_get_download_manager_wiring_persists_and_reloads(tmp_path):
    dm = get_download_manager()
    for plugin in dm._plugins.values():
        assert type(plugin)._persist is not None  # 生产单例逐类接线
    # 真写穿（生产类 WgetPlugin）+ 重启模拟回载
    wget = dm.get_plugin("wget")
    assert wget is not None
    cls = type(wget)
    cls._running.clear()
    cls._paused.clear()
    cls._persist_loaded = False
    wget._track("/d/live.bin", "http://x/live.bin", "/d/live.bin", True, MagicMock())
    asyncio.run(wget.pause("/d/live.bin"))
    persist = cls._persist
    assert persist is not None
    assert persist.count() == 1
    cls._running.clear()
    cls._paused.clear()
    cls._persist_loaded = False  # 重启
    assert wget.task_states() == {"/d/live.bin": "paused"}
    asyncio.run(wget.cancel("/d/live.bin"))  # 清痕
    assert persist.count() == 0


def test_manager_default_constructor_pure_memory():
    """直接构造的 DownloadManager（缺省 persist=None）不接线=纯内存零变化。"""
    m = DownloadManager()
    for plugin in m._plugins.values():
        type(plugin)
        # 类级 _persist 保持既有值（生产单例可能已接线），新注册类恒 None
    fresh = _fresh("Fresh")
    m.register(fresh)
    assert type(fresh)._persist is None


def test_default_db_path_env_and_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("DOWNLOAD_PLUGIN_TASKS_DB", str(tmp_path / "env.db"))
    assert default_db_path() == str(tmp_path / "env.db")
    monkeypatch.delenv("DOWNLOAD_PLUGIN_TASKS_DB")
    assert default_db_path().endswith("data/download_plugin_tasks.db")
