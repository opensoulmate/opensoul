"""H1-②b-⑮ 异步链终态查询面——POST /download 后台跑（auto 链+显式插件）
终态结果记录 + GET /api/download/results 查询出口。

修前（H1-②b-⑭ 留档边界「异步链终态可见性=日志+/tasks 在途（完成即消失）」）：
插件任务收尾即从 /tasks 在途面消失，链真赢家/失败真因只有日志可查——
ack 时说好了 task_id，之后拿这个 id 再问就 404（结果面不存在）。

契约（接线前定形，如实记）：
- ack 时建在途行（finished_at=null），后台收尾 upsert 终态行（同键单行）
- 行形状 13 键恒定；plugin=真跑注册 id（链真赢家），链全败=None 禁 fabricate
- 记录纯进程内（非持久化）：后台跑本体就活在进程内，重启=下载也没了
- native 引擎任务不在此面（其注册表保留 done 行，/tasks 持久可见）

测试面：真实 DownloadManager/DownloadPlugin + 每插件类独立注册表
（__init_subclass__ 语义）+ 真实 router 进程内隔离 app（⑭ 同款，绝不写
~/.openmate/download-cache、绝不打 live :8090）。
"""

import asyncio
import base64
import time
from unittest.mock import MagicMock

import pytest

from src.api.download_plugins import (
    DownloadManager,
    DownloadPlugin,
    DownloadProgress,
    PluginInfo,
)

# ─── 可控行为插件（每实例独立类=独立注册表，⑭ 同款） ──────────────────


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
    """download API 隔离测试面（⑭ 同款）+ 结果存储全隔离（模块级全局清空）。"""
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
    # 容缺清空（getattr 容缺：控制组旧代码无此符号时失败暴露在行为断言上，
    # 不在 fixture 结构面——⑲ 家族纪律）
    for holder in ("_async_results", "_async_result_order"):
        store = getattr(dapi, holder, None)
        if store is not None:
            store.clear()
    app = FastAPI()
    app.include_router(dapi.router, prefix="/api/download")
    with TestClient(app) as c:
        yield c, manager, native, tmp_path
    for holder in ("_async_results", "_async_result_order"):
        store = getattr(dapi, holder, None)
        if store is not None:
            store.clear()


def _wait(cond, timeout: float = 5.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")


def _task_token(registry_key: str) -> str:
    return base64.urlsafe_b64encode(registry_key.encode()).decode().rstrip("=")


def _post_auto(client, dest: str, url: str = "http://x/f", **extra):
    return client.post(
        "/api/download/download", json={"url": url, "dest": dest, "resume": False, **extra}
    )


ROW_KEYS = {
    "task_id",
    "registry_key",
    "url",
    "dest",
    "status",
    "plugin",
    "error",
    "fallback_chain",
    "total_bytes",
    "downloaded_bytes",
    "progress_pct",
    "started_at",
    "finished_at",
}


# ─── 未知/未记录如实 404 ────────────────────────────────────────────


def test_result_unknown_404(dl_client):
    client, _manager, _native, tmp_path = dl_client
    dest = str(tmp_path / "nope.bin")
    resp = client.get(f"/api/download/results/{_task_token(dest)}")
    assert resp.status_code == 404
    assert "no async result record" in resp.json()["detail"]
    # 不可解码的垃圾 id 同样如实 404（禁 fabricate）
    resp2 = client.get("/api/download/results/!!!not-base64!!!")
    assert resp2.status_code == 404


# ─── ack 在途行 → 终态行（同键 upsert） ────────────────────────────


def test_running_record_then_all_failed_terminal(dl_client):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", priority=5, hang=True)
    manager.register(first)
    dest = str(tmp_path / "d.bin")
    token = _task_token(dest)
    resp = _post_auto(client, dest)
    assert resp.status_code == 200
    _wait(lambda: len(first.calls) >= 1)
    # 在途行：ack 后立刻可查，finished_at=null（禁对在途谎报终态）
    row = client.get(f"/api/download/results/{token}").json()
    assert set(row) == ROW_KEYS
    assert row["status"] == "downloading"
    assert row["finished_at"] is None
    assert row["plugin"] == "auto"  # 真赢家未定禁自报（⑭ ack 契约同语义）
    assert row["fallback_chain"] == ["first"]
    assert row["registry_key"] == dest
    # 放行收尾 → 终态行（链全败 plugin=None 禁 fabricate 赢家）
    assert first._loop is not None
    first._loop.call_soon_threadsafe(first._release.set)
    _wait(lambda: client.get(f"/api/download/results/{token}").json()["finished_at"] is not None)
    row2 = client.get(f"/api/download/results/{token}").json()
    assert row2["status"] == "error"
    assert row2["plugin"] is None
    assert "All plugins failed" in row2["error"] and "exit code -9" in row2["error"]
    assert set(row2) == ROW_KEYS  # 同键 upsert，形状零变化


def test_chain_terminal_result_records_winner(dl_client):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("error", "boom1")], priority=5)
    second = cls("second", [("done", None)], priority=10)
    manager.register(first)
    manager.register(second)
    dest = str(tmp_path / "d.bin")
    token = _task_token(dest)
    resp = _post_auto(client, dest)
    assert resp.status_code == 200
    _wait(lambda: client.get(f"/api/download/results/{token}").json()["finished_at"] is not None)
    row = client.get(f"/api/download/results/{token}").json()
    assert row["status"] == "done"
    assert row["plugin"] == "second"  # 真赢家=真跑注册 id（声明从实现派生）
    assert row["error"] is None
    assert row["fallback_chain"] == ["first", "second"]
    assert len(first.calls) == 1 and len(second.calls) == 1


def test_chain_all_failed_error_recorded_with_cause(dl_client):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("error", "boom1")], priority=5)
    second = cls("second", [("error", "boom2")], priority=10)
    manager.register(first)
    manager.register(second)
    dest = str(tmp_path / "d.bin")
    token = _task_token(dest)
    _post_auto(client, dest)
    _wait(lambda: client.get(f"/api/download/results/{token}").json()["finished_at"] is not None)
    row = client.get(f"/api/download/results/{token}").json()
    assert row["status"] == "error"
    assert row["plugin"] is None  # 无赢家如实 None
    assert "All plugins failed" in row["error"] and "boom2" in row["error"]  # 末因可见


def test_chain_crash_recorded_not_silent(dl_client, monkeypatch):
    """后台链异常也必须留痕（既无响应体也无 /tasks 行可看，禁静默吞）。"""
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    manager.register(cls("first", priority=5))
    dest = str(tmp_path / "d.bin")
    token = _task_token(dest)

    async def boom(url, dest, resume=True, plugin_id=None, progress_cb=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(manager, "download", boom)
    _post_auto(client, dest)
    _wait(lambda: client.get(f"/api/download/results/{token}").json()["finished_at"] is not None)
    row = client.get(f"/api/download/results/{token}").json()
    assert row["status"] == "error"
    assert "kaboom" in row["error"]


# ─── 显式插件后台跑同一结果面（⑭ 对称语义） ─────────────────────────


def test_explicit_plugin_terminal_recorded(dl_client):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("done", None)], priority=5)
    manager.register(first)
    dest = str(tmp_path / "d.bin")
    token = _task_token(dest)
    resp = _post_auto(client, dest, plugin_id="first")
    assert resp.status_code == 200
    assert set(resp.json()) == {"task_id", "dest", "url", "status", "plugin"}  # ack 契约不变
    _wait(lambda: client.get(f"/api/download/results/{token}").json()["finished_at"] is not None)
    row = client.get(f"/api/download/results/{token}").json()
    assert row["status"] == "done"
    assert row["plugin"] == "first"
    assert row["fallback_chain"] is None  # 显式=零 fallback（⑨b 契约不变）


def test_explicit_plugin_crash_recorded_with_engine(dl_client):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    first = cls("first", [("raise", "plugin-impl-bug")], priority=5)
    manager.register(first)
    dest = str(tmp_path / "d.bin")
    token = _task_token(dest)
    _post_auto(client, dest, plugin_id="first")
    _wait(lambda: client.get(f"/api/download/results/{token}").json()["finished_at"] is not None)
    row = client.get(f"/api/download/results/{token}").json()
    assert row["status"] == "error"
    assert row["plugin"] == "first"  # 引擎如实标注（跑的是谁）
    assert "plugin-impl-bug" in row["error"]


# ─── 列表面/寻址/容量 ──────────────────────────────────────────────


def test_list_results_newest_first_and_total(dl_client):
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    manager.register(cls("first", [("done", None)], priority=5))
    _post_auto(client, str(tmp_path / "a.bin"))
    _post_auto(client, str(tmp_path / "b.bin"))
    _wait(lambda: client.get("/api/download/results").json()["total"] == 2)
    _wait(
        lambda: all(
            r["finished_at"] is not None
            for r in client.get("/api/download/results").json()["results"]
        )
    )
    data = client.get("/api/download/results").json()
    assert data["total"] == 2
    assert set(data) == {"results", "total"}
    # ack 序最新在前
    assert [r["dest"] for r in data["results"]] == [
        str(tmp_path / "b.bin"),
        str(tmp_path / "a.bin"),
    ]


def test_registry_key_addressing_resolves(dl_client):
    """token/注册表键双认寻址（_plugin_task 同款），多形同回一行。

    边界如实记：raw 注册表键=dest 路径——带斜杠的键作为 path 段放不下
    （ASGI %2F 解码回斜杠，⑨b 令牌根因），只能走 token；无斜杠键可直查。
    """
    client, manager, _native, _tmp_path = dl_client
    cls = _make_plugin_cls()
    manager.register(cls("first", [("done", None)], priority=5))
    dest = "d.bin"  # 无斜杠注册表键（带斜杠 dest 只能走 token，见上）
    token = _task_token(dest)
    _post_auto(client, dest)
    _wait(lambda: client.get(f"/api/download/results/{token}").json()["finished_at"] is not None)
    by_token = client.get(f"/api/download/results/{token}").json()
    by_key = client.get(f"/api/download/results/{dest}").json()
    assert by_token == by_key
    assert by_key["registry_key"] == dest
    # 非规范 base64 变体（带 padding）走解码回查路径
    by_padded = client.get(f"/api/download/results/{token}=").json()
    assert by_padded == by_token


def test_cap_eviction_oldest_first(dl_client, monkeypatch):
    import src.api.download_api as dapi

    monkeypatch.setattr(dapi, "_ASYNC_RESULT_CAP", 2, raising=False)
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    manager.register(cls("first", [("done", None)], priority=5))
    tokens = []
    for name in ("a.bin", "b.bin", "c.bin"):
        dest = str(tmp_path / name)
        tokens.append(_task_token(dest))
        _post_auto(client, dest)
    _wait(lambda: client.get("/api/download/results").json()["total"] == 2)
    assert client.get(f"/api/download/results/{tokens[0]}").status_code == 404  # 最旧淘汰
    assert client.get(f"/api/download/results/{tokens[2]}").status_code == 200


def test_upsert_same_dest_single_row(dl_client):
    """同 dest 重跑=同一 task_id 同键 upsert（禁行堆积）。"""
    client, manager, _native, tmp_path = dl_client
    cls = _make_plugin_cls()
    manager.register(cls("first", [("done", None), ("done", None)], priority=5))
    dest = str(tmp_path / "d.bin")
    _post_auto(client, dest)
    _wait(
        lambda: (
            client.get(f"/api/download/results/{_task_token(dest)}").json()["finished_at"]
            is not None
        )
    )
    _post_auto(client, dest)
    _wait(lambda: client.get("/api/download/results").json()["total"] == 1)
    assert client.get("/api/download/results").json()["total"] == 1


# ─── native 面边界锁死（/tasks 持久可见，禁在此面 fabricate） ─────────


def test_native_async_not_fabricated_in_results(dl_client):
    client, manager, native, tmp_path = dl_client
    cls = _make_plugin_cls()
    manager.register(cls("first", [("done", None)], priority=5))
    dest = str(tmp_path / "d.bin")
    try:
        resp = client.post(
            "/api/download/download",
            json={"url": "http://127.0.0.1:1/x", "dest": dest, "plugin_id": "native"},
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]
        assert client.get(f"/api/download/results/{task_id}").status_code == 404
        # native 终态真源=/tasks（其注册表保留 done 行），不重复记录
        assert any(t.id == task_id for t in native.list_tasks())
        # 同测对照：结果面真存在（插件路径真进面）——native 的 404 是
        # 「不入面」而非「无面」（修前全 404=无面，此对照让判定不靠假过）
        pdest = str(tmp_path / "p.bin")
        _post_auto(client, pdest)
        _wait(lambda: client.get(f"/api/download/results/{_task_token(pdest)}").status_code == 200)
    finally:
        for t in native.list_tasks():
            client.delete(f"/api/download/tasks/{t.id}")
