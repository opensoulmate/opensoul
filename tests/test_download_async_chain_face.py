"""H1-②b-⑭ 异步面 fallback 链接线——POST /download（异步）auto 路径真消费
DownloadManager.download 跨插件 fallback 链（后台跑）+ 响应契约改造。

修前（H1-②b-⑬ 留档边界「链只进 sync（异步单引擎留档须改响应契约）」）：
异步 auto=择优单引擎直跑零 fallback，链首失败整个下载就停在错误上——
同步面有 fallback 链、异步面没有=同一 auto 语义两面不对称。

契约改造（接线前定形，如实记）：ack 即刻返回时真赢家未定——修前 plugin 键
=择优单引擎 id（那是「将跑谁」的自报），链语义下「实际跑了谁」链跑完才有
结果；现 plugin 如实标 "auto"（引擎未定禁自报，⑬「结果 plugin=真跑注册 id」
纪律在 ack 时点无结果可标），链序 fallback_chain 加性键可观测（_candidates
单一源），实际引擎真源=/tasks plugin_tasks（在途）+日志（结果）。

测试面：真实 DownloadManager/DownloadPlugin + 每插件类独立注册表
（__init_subclass__ 语义）+ 真实 router 进程内隔离 app（⑨b 同款，绝不写
~/.openmate/download-cache、绝不打 live :8090）。
"""

import asyncio
import logging
import time
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
    """每个调用生成一个全新插件类——__init_subclass__ 每类一套任务注册表。"""

    class _Scripted(DownloadPlugin):
        def __init__(
            self,
            plugin_id: str,
            results: list[tuple[str, str | None]] | None = None,
            priority: int = 50,
            supports_resume: bool = True,
            available: bool = True,
            hang: bool = False,
        ):
            self._id = plugin_id
            self._results = list(results or [])
            self._priority = priority
            self._supports_resume = supports_resume
            self._available = available
            self._hang = hang
            self._release = asyncio.Event()
            self._loop = None
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
            self._loop = asyncio.get_running_loop()  # 跨线程放行用（TestClient 门户线程）
            if self._hang:
                # 模拟在途传输：登记后等放行，收尾产出=被 kill 的退出码 error
                # （_untrack 按 pause 上下文如实改标 paused——真实插件 SIGTERM 同款）
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
                plugin=self._id,
                supports_resume=self._supports_resume,
            )
            progress.status = spec[0]
            progress.error = spec[1]
            return progress

    return _Scripted


@pytest.fixture
def dl_client(tmp_path, monkeypatch):
    """download API 隔离测试面（⑨b 同款）：真实 router + 独立 manager/native
    引擎实例；内置插件清空=链序可确定性；CACHE_DIR 指 tmp。"""
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


def _wait(cond, timeout: float = 5.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")


def _task_token(registry_key: str) -> str:
    import base64

    return base64.urlsafe_b64encode(registry_key.encode()).decode().rstrip("=")


# ─── ack 形状（契约改造本体） ──────────────────────────────────────


def test_async_auto_ack_shape_exact(dl_client):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    manager.register(cls("first", priority=5))
    dest = str(tmp_path / "d.bin")
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": dest, "resume": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    # auto 链 ack 恰 6 键（5 键契约 + fallback_chain 加性键）
    assert set(data) == {"task_id", "dest", "url", "status", "plugin", "fallback_chain"}
    assert data["plugin"] == "auto"  # 真赢家未定禁自报引擎
    assert data["status"] == "downloading"
    assert data["task_id"] == _task_token(dest)
    assert data["fallback_chain"] == ["first"]


def test_async_auto_chain_falls_back_in_background(dl_client):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("error", "boom1")], priority=5)
    second = cls("second", [("done", None)], priority=10)
    manager.register(first)
    manager.register(second)
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": str(tmp_path / "d.bin"), "resume": False},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "downloading"  # ack 即刻返回非等链跑完
    _wait(lambda: len(second.calls) >= 1)
    # 链真跑两级：链首真失败掉下一级（修前异步面 second 恒零调用）
    assert len(first.calls) == 1
    assert len(second.calls) == 1


def test_async_auto_chain_result_logged_not_silent(dl_client, caplog):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    manager.register(cls("first", [("done", None)], priority=5))
    with caplog.at_level(logging.INFO, logger="src.api.download_api"):
        resp = client.post(
            "/api/download/download",
            json={"url": "http://x/f", "dest": str(tmp_path / "d.bin"), "resume": False},
        )
        assert resp.status_code == 200
        _wait(lambda: any("Async fallback chain finished" in r.message for r in caplog.records))
    assert any("plugin=first" in r.message for r in caplog.records)  # 真赢家如实进日志


def test_async_auto_chain_failure_logged_with_cause(dl_client, caplog):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("error", "boom1")], priority=5)
    second = cls("second", [("error", "boom2")], priority=10)
    manager.register(first)
    manager.register(second)
    with caplog.at_level(logging.INFO, logger="src.api.download_api"):
        resp = client.post(
            "/api/download/download",
            json={"url": "http://x/f", "dest": str(tmp_path / "d.bin"), "resume": False},
        )
        assert resp.status_code == 200
        _wait(
            lambda: any(
                r.levelno >= logging.WARNING and "Async fallback chain failed" in r.message
                for r in caplog.records
            )
        )
    warned = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("All plugins failed" in m and "boom2" in m for m in warned)  # 末因可见禁静默


def test_async_auto_delegates_to_manager_download(dl_client, monkeypatch):
    """端点真委托 manager.download（禁 API 层重实现第二套链）。"""
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    manager.register(cls("first", [("done", None)], priority=5))
    calls: list[tuple] = []
    original = manager.download

    async def spy(url, dest, resume=True, plugin_id=None, progress_cb=None):
        calls.append((url, dest, resume, plugin_id))
        return await original(url, dest, resume=resume, plugin_id=plugin_id)

    monkeypatch.setattr(manager, "download", spy)
    dest = str(tmp_path / "d.bin")
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": dest, "resume": False},
    )
    assert resp.status_code == 200
    _wait(lambda: len(calls) >= 1)
    assert calls == [("http://x/f", dest, False, None)]


# ─── 显式面契约不变（5 键零 fallback） ─────────────────────────────


def test_async_explicit_plugin_contract_unchanged(dl_client):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("error", "boom")], priority=5)
    second = cls("second", [("done", None)], priority=10)
    manager.register(first)
    manager.register(second)
    dest = str(tmp_path / "d.bin")
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": dest, "plugin_id": "first", "resume": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"task_id", "dest", "url", "status", "plugin"}  # 恰 5 键零变化
    assert data["plugin"] == "first"
    _wait(lambda: len(first.calls) >= 1)
    assert second.calls == []  # 显式=单引擎直跑零 fallback（⑨b 契约不变）


def test_async_native_sentinel_contract_unchanged(dl_client):
    client, manager, native, tmp_path = dl_client
    cls = _make_plugin_cls()
    plugin = cls("first", priority=5)
    manager.register(plugin)
    try:
        resp = client.post(
            "/api/download/download",
            json={
                "url": "http://127.0.0.1:1/x",
                "dest": str(tmp_path / "d.bin"),
                "plugin_id": "native",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data) == {"task_id", "dest", "url", "status", "plugin"}
        assert data["plugin"] == "native"
        assert len(native.list_tasks()) == 1
        assert plugin.calls == []  # 强制 native=插件零触碰
    finally:
        for t in native.list_tasks():
            client.delete(f"/api/download/tasks/{t.id}")


def test_async_auto_no_candidates_falls_back_native(dl_client):
    client, manager, native, tmp_path = dl_client
    cls = _make_plugin_cls()
    only_nonresume = cls("only-nonresume", [("done", None)], priority=1, supports_resume=False)
    manager.register(only_nonresume)
    try:
        resp = client.post(
            "/api/download/download",
            json={"url": "http://127.0.0.1:1/x", "dest": str(tmp_path / "d.bin"), "resume": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        # 无可用候选（resume 过滤后空）→回落 native，5 键契约不变（非链 ack）
        assert set(data) == {"task_id", "dest", "url", "status", "plugin"}
        assert data["plugin"] == "native"
        assert len(native.list_tasks()) == 1
        assert only_nonresume.calls == []
    finally:
        for t in native.list_tasks():
            client.delete(f"/api/download/tasks/{t.id}")


# ─── 链序可观测（fallback_chain=_candidates 单一源） ────────────────


def test_async_fallback_chain_respects_resume_filter(dl_client):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    noresume = cls("noresume", [("done", None)], priority=1, supports_resume=False)
    resumy = cls("resumy", [("done", None)], priority=5)
    other = cls("other", [("done", None)], priority=9)
    manager.register(noresume)
    manager.register(resumy)
    manager.register(other)
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": str(tmp_path / "d.bin"), "resume": True},
    )
    assert resp.json()["fallback_chain"] == ["resumy", "other"]  # 非续传不进链序
    resp2 = client.post(
        "/api/download/download",
        json={"url": "http://x/f2", "dest": str(tmp_path / "d2.bin"), "resume": False},
    )
    # resume=False 全量按 priority 升序（_candidates 单一源=链真跑序）
    assert resp2.json()["fallback_chain"] == ["noresume", "resumy", "other"]


def test_chain_order_single_source_matches_candidates():
    """chain_order=链可观测面，序必须=_candidates 单一源（禁第二套排序漂移）。"""
    cls = _make_plugin_cls()
    manager = DownloadManager()
    manager._plugins.clear()
    a = cls("a", priority=30)
    b = cls("b", priority=10)
    c = cls("c", priority=20, supports_resume=False)
    for p in (a, b, c):
        manager.register(p)
    assert manager.chain_order(require_resume=False) == ["b", "c", "a"]
    assert manager.chain_order(require_resume=True) == ["b", "a"]
    assert manager.chain_order(require_resume=True) == [
        p.get_info().id for p in manager._candidates(require_resume=True)
    ]


# ─── 链上用户意图（pause 禁掉链双跑，⑬语义随异步接线零回归） ────────


def test_async_chain_pause_stops_chain_no_double_run(dl_client, monkeypatch):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", priority=5, hang=True)
    second = cls("second", [("done", None)], priority=10)
    manager.register(first)
    manager.register(second)
    results: list[DownloadProgress] = []
    original = manager.download

    async def spy(url, dest, resume=True, plugin_id=None, progress_cb=None):
        progress = await original(url, dest, resume=resume, plugin_id=plugin_id)
        results.append(progress)
        return progress

    monkeypatch.setattr(manager, "download", spy)
    dest = str(tmp_path / "d.bin")
    token = _task_token(dest)
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": dest, "resume": False},
    )
    assert resp.status_code == 200
    _wait(lambda: len(first.calls) >= 1)
    resp2 = client.post(f"/api/download/tasks/{token}/pause")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "paused"
    # 挂住的传输收尾（SIGTERM 同款：error→paused 归因）——放行必须回到 app 事件
    # 循环线程（TestClient 门户跨线程裸 set() 唤不醒 await wait()，测试自伤坑）
    assert first._loop is not None  # download 真启动过（calls 断言在前）
    first._loop.call_soon_threadsafe(first._release.set)
    _wait(lambda: len(results) >= 1)
    assert results[0].status == "paused"  # 链如实收在 paused（非 error 非 done）
    assert second.calls == []  # paused=用户意图禁掉下一级（禁双跑同 dest）
