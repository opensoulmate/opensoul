"""H1-②b-⑬ manager.download fallback 链接线——/download/sync auto 路径真消费
DownloadManager.download 跨插件 fallback 链（修前全仓零生产消费=
wired-but-not-functional）。

修前（H1-② 定性⑨「download 续传面」+⑨b 诚实边界留档）：
①DownloadManager.download（"Download with auto-fallback across plugins"）
  全仓零生产消费——fallback 链写了没人走
②链实现带病（接线前定形收口）：paused/canceled（用户意图）被当失败掉下一级
  =链式接管双跑同一 dest（⑨a native 双写者同型）；resume=True 链序与
  get_best_plugin(require_resume) 口径漂移（重排保留非续传 vs 择优跳过）；
  结果 plugin=插件自报无真源

测试面：真实 DownloadManager/DownloadPlugin + 独立类注册表（每插件类一套
_running/_paused/_canceled，__init_subclass__ 语义）；API 面=真实 router +
进程内隔离 app（⑨b gene_client 同款路线，绝不写 ~/.openmate/download-cache）。
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from src.api.download_plugins import (
    DownloadManager,
    DownloadPlugin,
    DownloadProgress,
    PluginInfo,
)

# ─── 可控行为插件（每实例独立类=独立注册表） ────────────────────────


def _make_plugin_cls():
    """每个调用生成一个全新插件类——__init_subclass__ 每类一套任务注册表，
    多实例并存互不串（基类注册表是类属性，同类多实例会共享）。"""

    class _Scripted(DownloadPlugin):
        def __init__(
            self,
            plugin_id: str,
            results: list[tuple[str, str | None]] | None = None,
            priority: int = 50,
            supports_resume: bool = True,
            available: bool = True,
            hang: bool = False,
            report_plugin: str | None = None,
        ):
            self._id = plugin_id
            self._results = list(results or [])
            self._priority = priority
            self._supports_resume = supports_resume
            self._available = available
            self._hang = hang
            self._report = report_plugin
            self._release = asyncio.Event()
            self.calls: list[tuple[str, str, bool]] = []

        def get_info(self) -> PluginInfo:
            return PluginInfo(
                id=self._id,
                name=self._id,
                description=self._id,
                version="0",
                binary="true",
                supports_resume=self._supports_resume,
                supports_p2p=False,
                install_cmd={},
                update_cmd={},
                check_version_cmd="true",
                priority=self._priority,
            )

        def is_available(self) -> bool:
            return self._available

        async def install(self) -> bool:
            return False

        async def update(self) -> bool:
            return False

        async def get_version(self) -> str | None:
            return "0"

        async def download(
            self, url: str, dest: str, resume: bool = True, progress_cb=None
        ) -> DownloadProgress:
            self.calls.append((url, dest, resume))
            if self._hang:
                # 模拟在途传输：登记后等放行，收尾产出=被 kill 的退出码 error
                self._track(dest, url, dest, resume, proc=MagicMock())
                await self._release.wait()
                progress = DownloadProgress(
                    url=url,
                    dest=dest,
                    status="error",
                    error="exit code -9",
                    plugin=self._id,
                    supports_resume=self._supports_resume,
                )
                self._untrack(dest, progress)
                return progress
            spec = self._results.pop(0) if self._results else ("done", None)
            if spec[0] == "raise":
                raise RuntimeError(spec[1])
            progress = DownloadProgress(
                url=url,
                dest=dest,
                plugin=self._report if self._report is not None else self._id,
                supports_resume=self._supports_resume,
            )
            progress.status = spec[0]
            progress.error = spec[1]
            return progress

    return _Scripted


def _fresh_manager() -> DownloadManager:
    """内置 7 插件清空=链序可确定性（真机装了 curl/wget 会左右链首）。"""
    mgr = DownloadManager()
    mgr._plugins.clear()
    return mgr


async def _wait_until(cond, timeout: float = 5.0, interval: float = 0.01):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if cond():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met within timeout")


# ─── 链语义（DownloadManager.download 接线前定形） ──────────────────


@pytest.mark.asyncio
async def test_chain_first_success_runs_only_first():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    first = cls("first", [("done", None)], priority=5)
    second = cls("second", [("done", None)], priority=10)
    mgr.register(first)
    mgr.register(second)
    result = await mgr.download("http://x/f", "/tmp/d")
    assert result.status == "done"
    assert first.calls == [("http://x/f", "/tmp/d", True)]
    assert second.calls == []  # 链首成功=零回退


@pytest.mark.asyncio
async def test_chain_falls_back_on_error_and_reports_true_winner():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    first = cls("first", [("error", "boom1")], priority=5)
    second = cls("second", [("done", None)], priority=10)
    mgr.register(first)
    mgr.register(second)
    result = await mgr.download("http://x/f", "/tmp/d")
    assert result.status == "done"
    assert len(first.calls) == 1
    assert len(second.calls) == 1  # 真失败掉下一级
    assert result.plugin == "second"  # 真赢家如实标注


@pytest.mark.asyncio
async def test_chain_all_fail_honest_error_tries_all():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    first = cls("first", [("error", "boom1")], priority=5)
    second = cls("second", [("error", "boom2")], priority=10)
    mgr.register(first)
    mgr.register(second)
    result = await mgr.download("http://x/f", "/tmp/d")
    assert result.status == "error"  # 全败禁谎报 done
    err = result.error or ""
    assert "All plugins failed" in err
    assert "first" in err and "second" in err  # 试过谁可观测
    assert "boom2" in err  # 末因如实


@pytest.mark.asyncio
async def test_chain_stops_on_paused_no_double_run():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    first = cls("first", [("paused", None)], priority=5)
    second = cls("second", [("done", None)], priority=10)
    mgr.register(first)
    mgr.register(second)
    result = await mgr.download("http://x/f", "/tmp/d")
    # paused=用户意图终止，禁掉链接管双跑同一 dest（⑨a 双写者同型）
    assert result.status == "paused"
    assert second.calls == []


@pytest.mark.asyncio
async def test_chain_stops_on_user_cancel_no_double_run():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    first = cls("first", hang=True, priority=5)
    second = cls("second", [("done", None)], priority=10)
    mgr.register(first)
    mgr.register(second)
    bg = asyncio.create_task(mgr.download("http://x/f", "/tmp/d"))
    await _wait_until(lambda: "/tmp/d" in first._running)
    assert await first.cancel("/tmp/d") is True  # 真 kill + 取消归因
    first._release.set()
    result = await bg
    # 用户取消≠传输失败：如实 canceled（error 是 kill 退出码被归因清掉）
    assert result.status == "canceled"
    assert result.error is None
    assert second.calls == []  # 禁把取消当失败掉下一插件


@pytest.mark.asyncio
async def test_chain_resume_requirement_excludes_non_resume():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    fast_nonresume = cls("fast-nonresume", [("done", None)], priority=1, supports_resume=False)
    slow_resume = cls("slow-resume", [("done", None)], priority=9, supports_resume=True)
    mgr.register(fast_nonresume)
    mgr.register(slow_resume)
    result = await mgr.download("http://x/f", "/tmp/d", resume=True)
    # resume=True=require_resume 口径（⑨b「resume 需求随 req.resume」同源）：
    # 非续传插件不进链，禁优先级更优的非续传顶掉续传能力
    assert result.status == "done"
    assert result.plugin == "slow-resume"
    assert fast_nonresume.calls == []


@pytest.mark.asyncio
async def test_chain_resume_false_includes_all_in_priority_order():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    fast_nonresume = cls("fast-nonresume", [("error", "boom")], priority=1, supports_resume=False)
    slow_resume = cls("slow-resume", [("done", None)], priority=9, supports_resume=True)
    mgr.register(fast_nonresume)
    mgr.register(slow_resume)
    result = await mgr.download("http://x/f", "/tmp/d", resume=False)
    # resume=False=全部可用插件按 priority 序（非续传照进链）
    assert result.status == "done"
    assert len(fast_nonresume.calls) == 1  # 链首=priority 最优
    assert result.plugin == "slow-resume"


@pytest.mark.asyncio
async def test_chain_empty_candidates_honest_no_suitable():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    only_nonresume = cls("only-nonresume", [("done", None)], priority=1, supports_resume=False)
    mgr.register(only_nonresume)
    result = await mgr.download("http://x/f", "/tmp/d", resume=True)
    # 空候选≠全败：如实 no suitable，禁含混谎报
    assert result.status == "error"
    assert "No suitable plugins available" in (result.error or "")
    assert only_nonresume.calls == []  # 不进链=零硬跑


@pytest.mark.asyncio
async def test_chain_stamps_true_plugin_id_over_self_report():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    liar = cls("real-id", [("done", None)], priority=5, report_plugin="bogus")
    mgr.register(liar)
    result = await mgr.download("http://x/f", "/tmp/d")
    # 结果 plugin=真跑插件注册 id（声明从实现派生），禁插件自报顶替
    assert result.plugin == "real-id"


@pytest.mark.asyncio
async def test_chain_explicit_plugin_zero_fallback():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    first = cls("first", [("error", "boom")], priority=5)
    second = cls("second", [("done", None)], priority=10)
    mgr.register(first)
    mgr.register(second)
    result = await mgr.download("http://x/f", "/tmp/d", plugin_id="first")
    # 显式指定=单插件直跑零 fallback（⑨b 显式语义延续）
    assert result.status == "error"
    assert "boom" in (result.error or "")
    assert second.calls == []


@pytest.mark.asyncio
async def test_chain_explicit_unavailable_plugin_refused():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    ghost = cls("ghost", [("done", None)], available=False)
    mgr.register(ghost)
    result = await mgr.download("http://x/f", "/tmp/d", plugin_id="ghost")
    # 未安装如实拒，禁静默换引擎硬跑
    assert result.status == "error"
    assert "not available" in (result.error or "")
    assert ghost.calls == []


@pytest.mark.asyncio
async def test_chain_order_matches_get_best_plugin_single_source():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    p3 = cls("p3-nonresume", [("done", None)], priority=3, supports_resume=False)
    p7 = cls("p7-resume", [("done", None)], priority=7, supports_resume=True)
    p9 = cls("p9-resume", [("done", None)], priority=9, supports_resume=True)
    mgr.register(p3)
    mgr.register(p7)
    mgr.register(p9)
    # 择优与链首同一 _candidates 单一源（禁两套排序/过滤漂移，F4b-㉗ 纪律）
    best = mgr.get_best_plugin(require_resume=True)
    assert best is not None and best.get_info().id == "p7-resume"
    result = await mgr.download("http://x/f", "/tmp/d", resume=True)
    assert p7.calls and result.plugin == "p7-resume"  # 链首==择优
    assert p3.calls == [] and p9.calls == []

    best2 = mgr.get_best_plugin(require_resume=False)
    assert best2 is not None and best2.get_info().id == "p3-nonresume"
    result2 = await mgr.download("http://x/f", "/tmp/d2", resume=False)
    assert p3.calls and result2.plugin == "p3-nonresume"  # 链首==择优


@pytest.mark.asyncio
async def test_chain_unknown_status_not_treated_as_failure():
    cls = _make_plugin_cls()
    mgr = _fresh_manager()
    first = cls("first", [("downloading", None)], priority=5)
    second = cls("second", [("done", None)], priority=10)
    mgr.register(first)
    mgr.register(second)
    result = await mgr.download("http://x/f", "/tmp/d")
    # 只有真失败（error）掉链；未知/在途状态如实返回禁当失败接管
    assert result.status == "downloading"
    assert second.calls == []


# ─── 基类取消归因（链不掉级的结构保证） ────────────────────────────


@pytest.mark.asyncio
async def test_cancel_marks_result_canceled_not_error():
    cls = _make_plugin_cls()
    p = cls("p", hang=True)
    bg = asyncio.create_task(p.download("http://x/f", "/tmp/d"))
    await _wait_until(lambda: "/tmp/d" in p._running)
    assert await p.cancel("/tmp/d") is True
    p._release.set()
    progress = await bg
    # cancel 的 kill 退出码不是传输故障——如实 canceled，禁谎报 error
    assert progress.status == "canceled"
    assert progress.error is None


@pytest.mark.asyncio
async def test_genuine_error_stays_error_not_attributed_canceled():
    cls = _make_plugin_cls()
    p = cls("p", [("error", "real failure")])
    progress = await p.download("http://x/f", "/tmp/d")
    # 无取消归因标记=真失败保持 error（禁误标 canceled 吞掉故障）
    assert progress.status == "error"
    assert progress.error == "real failure"


# ─── API 面（/download/sync auto 真消费链） ─────────────────────────


@pytest.fixture
def dl_client(tmp_path, monkeypatch):
    """download API 隔离测试面（⑨b 同款）：真实 router + 独立 manager/native
    引擎实例；CACHE_DIR 指 tmp 绝不写 ~/.openmate/download-cache。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.download_api as dapi
    from src.api.native_downloader import NativeDownloader

    manager = DownloadManager()
    manager._plugins.clear()
    native = NativeDownloader()
    monkeypatch.setattr(dapi, "get_download_manager", lambda: manager)
    monkeypatch.setattr(dapi, "get_downloader", lambda: native)
    monkeypatch.setattr(dapi, "CACHE_DIR", tmp_path / "cache")
    app = FastAPI()
    app.include_router(dapi.router, prefix="/api/download")
    with TestClient(app) as c:
        yield c, manager, native, tmp_path


def test_sync_auto_falls_back_and_reports_winner(dl_client):
    client, manager, native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("error", "boom1")], priority=5)
    second = cls("second", [("done", None)], priority=10)
    manager.register(first)
    manager.register(second)
    resp = client.post(
        "/api/download/download/sync",
        json={"url": "http://x/f", "dest": str(tmp_path / "d.bin"), "resume": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    # auto=跨插件 fallback 链：链首失败掉下一级，响应 plugin=真赢家如实标注
    assert data["status"] == "done"
    assert data["plugin"] == "second"
    assert len(first.calls) == 1 and len(second.calls) == 1


def test_sync_auto_all_fail_error_honest(dl_client):
    client, manager, native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("error", "boom1")], priority=5)
    second = cls("second", [("error", "boom2")], priority=10)
    manager.register(first)
    manager.register(second)
    resp = client.post(
        "/api/download/download/sync",
        json={"url": "http://x/f", "dest": str(tmp_path / "d.bin"), "resume": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"  # 全败禁谎报 ok
    assert "All plugins failed" in data["error"]
    assert "boom2" in data["error"]


def test_sync_auto_no_candidates_falls_back_native(dl_client):
    client, manager, native, tmp_path = dl_client
    cls = _make_plugin_cls()
    only_nonresume = cls("only-nonresume", [("done", None)], priority=1, supports_resume=False)
    manager.register(only_nonresume)
    resp = client.post(
        "/api/download/download/sync",
        json={"url": "http://127.0.0.1:1/x", "dest": str(tmp_path / "d.bin"), "resume": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    # ⑨b 回落契约不变：无可用插件（resume 需求过滤后空）如实回落 native
    assert data["plugin"] == "native"
    assert only_nonresume.calls == []


def test_sync_explicit_plugin_zero_fallback(dl_client):
    client, manager, native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("error", "boom")], priority=5)
    second = cls("second", [("done", None)], priority=10)
    manager.register(first)
    manager.register(second)
    resp = client.post(
        "/api/download/download/sync",
        json={"url": "http://x/f", "dest": str(tmp_path / "d.bin"), "plugin_id": "first"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # 显式指定=单引擎直跑零 fallback（⑨b 契约不变面）
    assert data["status"] == "error"
    assert "boom" in data["error"]
    assert second.calls == []


def test_sync_auto_delegates_to_manager_download(dl_client, monkeypatch):
    client, manager, native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("done", None)], priority=5)
    manager.register(first)
    calls: list[tuple] = []
    original = manager.download

    async def spy(url, dest, resume=True, plugin_id=None, progress_cb=None):
        calls.append((url, dest, resume, plugin_id))
        return await original(url, dest, resume=resume, plugin_id=plugin_id)

    monkeypatch.setattr(manager, "download", spy)
    resp = client.post(
        "/api/download/download/sync",
        json={"url": "http://x/f", "dest": str(tmp_path / "d.bin"), "resume": False},
    )
    assert resp.status_code == 200
    # 端点真委托 manager.download（禁 API 层重实现第二套链）
    assert calls == [("http://x/f", str(tmp_path / "d.bin"), False, None)]


def test_async_download_auto_runs_fallback_chain(dl_client):
    """契约随更新（H1-②b-⑭）：/download（异步）auto=跨插件 fallback 链后台跑。

    修前（本函数原名 test_async_download_auto_stays_single_engine）：异步 auto
    单引擎直跑零 fallback，留档边界注明「链接入另拍须先改本契约」——本拍即该拍：
    ack 时真赢家未定，plugin 如实标 "auto" 禁自报引擎（结果 plugin=真跑注册 id
    ⑬纪律在 ack 时点无结果可标），链序 fallback_chain 可观测（_candidates 单一源）。
    """
    import time

    from src.api.download_api import _task_token

    client, manager, native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("error", "boom")], priority=5)
    second = cls("second", [("done", None)], priority=10)
    manager.register(first)
    manager.register(second)
    dest = str(tmp_path / "d.bin")
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": dest, "resume": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["plugin"] == "auto"  # 真赢家未定禁自报引擎
    assert data["fallback_chain"] == ["first", "second"]
    assert data["task_id"] == _task_token(dest)
    assert data["status"] == "downloading"
    deadline = time.time() + 5
    while time.time() < deadline and len(second.calls) == 0:
        time.sleep(0.05)
    # 异步面真消费链：链首失败掉下一级（修前 second.calls == [] 零 fallback）
    assert len(first.calls) == 1
    assert len(second.calls) == 1
