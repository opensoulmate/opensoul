"""H1-②b-⑨b plugin download API 面——plugin_id 真消费 + get_best_plugin 能力择优
+ /tasks 控制面双引擎真路由 + 未知 task_id 如实 404（修前假成功）。

修前（H1-②b-⑨a 留档 4 件）：
①DownloadRequest.plugin_id 被静默忽略（字段写了没接线，/download 恒 native）
②get_best_plugin 能力择优全仓零消费
③/tasks/{id}/pause|resume|DELETE 对未知 task_id 也回 {"status":"paused"} 等假成功
④plugin download() 无 API 面（插件引擎不可达）

测试面：真实 router + 进程内隔离 app（gene_client 同款路线，绝不写生产/不依赖
live 服务）；插件机制用真子进程假二进制（⑨a 同款：二进制假、机制真）。
"""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.api.download_plugins import (
    Aria2Plugin,
    DownloadManager,
    DownloadPlugin,
    DownloadProgress,
    PluginInfo,
)

try:
    from src.api.download_api import _task_token
except ImportError:  # 控制组（旧代码无该符号）容缺：让失败暴露在行为断言上

    def _task_token(registry_key: str) -> str:
        import base64

        return base64.urlsafe_b64encode(registry_key.encode()).decode().rstrip("=")


from src.api.native_downloader import DownloadStatus, DownloadTask, NativeDownloader

# ─── 隔离测试面（真实 router + tmp 数据面） ─────────────────────────


@pytest.fixture
def dl_client(tmp_path, monkeypatch):
    """download API 隔离测试面：真实 router + 独立 manager/native 引擎实例。

    内置 7 插件清空=择优可确定性（真机装了 curl/wget 会左右 auto 选择）；
    CACHE_DIR 指 tmp 绝不写 ~/.openmate/download-cache。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.download_api as dapi

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


class _ScriptedPlugin(DownloadPlugin):
    """可控行为插件：download() 记录调用并按需登记/hang（不 spawn 子进程）。

    pause/resume_download/cancel 用基类真实现（注册表+续传上下文真机制），
    proc 用 MagicMock 承接 terminate/kill。
    """

    def __init__(
        self,
        plugin_id: str = "fake",
        available: bool = True,
        supports_resume: bool = True,
        priority: int = 50,
        hang: bool = False,
    ):
        self._id = plugin_id
        self._available = available
        self._supports_resume = supports_resume
        self._priority = priority
        self._hang = hang
        self.calls: list[tuple[str, str, bool]] = []
        self._release = asyncio.Event()

    def get_info(self) -> PluginInfo:
        return PluginInfo(
            id=self._id,
            name=self._id,
            description="",
            version="0",
            binary="fakebin",
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
            self._track(dest, url, dest, resume, proc=MagicMock())
            try:
                await self._release.wait()
            finally:
                self._untrack(dest)
            return DownloadProgress(
                url=url,
                dest=dest,
                status="paused",
                plugin=self._id,
                supports_resume=self._supports_resume,
            )
        return DownloadProgress(
            url=url,
            dest=dest,
            status="done",
            plugin=self._id,
            supports_resume=self._supports_resume,
            total_bytes=1,
            downloaded_bytes=1,
        )


def _poll(client, path: str, cond, timeout: float = 8.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(path).json()
        if cond(data):
            return data
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s: {path}")


# ─── ① plugin_id 真消费（修前静默忽略） ────────────────────────────


def test_download_unknown_plugin_id_is_404_not_silently_ignored(dl_client):
    client, manager, native, tmp_path = dl_client
    manager.register(_ScriptedPlugin(plugin_id="fake"))
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": str(tmp_path / "d.bin"), "plugin_id": "nope"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()
    # 修前：200 + 静默忽略 plugin_id 硬跑 native——必须零副作用
    assert native.list_tasks() == []


def test_download_uninstalled_plugin_is_400_not_hard_run(dl_client):
    client, manager, native, tmp_path = dl_client
    manager.register(_ScriptedPlugin(plugin_id="ghost", available=False))
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": str(tmp_path / "d.bin"), "plugin_id": "ghost"},
    )
    assert resp.status_code == 400
    assert "not installed" in resp.json()["detail"].lower()
    assert native.list_tasks() == []


def test_download_explicit_plugin_is_used_and_reported(dl_client):
    client, manager, native, tmp_path = dl_client
    plugin = _ScriptedPlugin(plugin_id="fake", hang=True)
    manager.register(plugin)
    dest = str(tmp_path / "d.bin")
    token = _task_token(dest)
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": dest, "plugin_id": "fake", "resume": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    # plugin 引擎的 task_id=注册表键（dest）URL 安全令牌（path 段放不下斜杠）
    assert data["task_id"] == token
    assert data["plugin"] == "fake"
    assert data["status"] == "downloading"
    try:
        # 真调用送达插件：url/dest/resume 逐参可验
        _poll(
            client,
            "/api/download/tasks",
            lambda d: any(t["task_id"] == token for t in d.get("plugin_tasks", [])),
        )
        assert plugin.calls == [("http://x/f", dest, True)]
    finally:
        # 先删（hang 任务在册）再放行——放行后 download() 收尾会 _untrack，
        # 顺序反了 DELETE 会撞 404（测试时序自伤）
        client.delete(f"/api/download/tasks/{token}")
        plugin._release.set()


def test_download_native_sentinel_forces_native_engine(dl_client):
    client, manager, native, tmp_path = dl_client
    plugin = _ScriptedPlugin(plugin_id="fake")
    manager.register(plugin)
    dest = str(tmp_path / "d.bin")
    try:
        resp = client.post(
            "/api/download/download",
            json={"url": "http://127.0.0.1:1/x", "dest": dest, "plugin_id": "native"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["plugin"] == "native"
        assert len(native.list_tasks()) == 1
        assert plugin.calls == []  # 强制 native=插件零触碰
    finally:
        for t in native.list_tasks():
            client.delete(f"/api/download/tasks/{t.id}")


def test_download_sync_explicit_plugin_progress_contract(dl_client):
    client, manager, native, tmp_path = dl_client
    plugin = _ScriptedPlugin(plugin_id="fake")
    manager.register(plugin)
    dest = str(tmp_path / "d.bin")
    resp = client.post(
        "/api/download/download/sync",
        json={"url": "http://x/f", "dest": dest, "plugin_id": "fake"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {
        "status",
        "dest",
        "url",
        "total_bytes",
        "downloaded_bytes",
        "speed",
        "eta",
        "progress_pct",
        "plugin",
        "supports_resume",
        "error",
        "task_id",
    }
    assert data["plugin"] == "fake"
    assert data["status"] == "done"
    assert data["task_id"] == _task_token(dest)


# ─── ② get_best_plugin 能力择优（修前全仓零消费） ──────────────────


def test_auto_prefers_best_available_plugin(dl_client):
    client, manager, native, tmp_path = dl_client
    manager.register(_ScriptedPlugin(plugin_id="slow", priority=10))
    manager.register(_ScriptedPlugin(plugin_id="best", priority=5))
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": str(tmp_path / "d.bin")},
    )
    assert resp.status_code == 200
    data = resp.json()
    # 契约随更新（H1-②b-⑭）：异步 auto=fallback 链 ack——真赢家未定 plugin 如实
    # "auto"，择优语义在链序（链首=择优同源 _candidates）
    assert data["plugin"] == "auto"
    assert data["fallback_chain"] == ["best", "slow"]
    assert native.list_tasks() == []


def test_auto_require_resume_skips_non_resume_plugin(dl_client):
    client, manager, native, tmp_path = dl_client
    # 优先级数字更小=更高优先，但不支持 resume——resume=True 的下载必须择优跳过它
    manager.register(_ScriptedPlugin(plugin_id="noresume", priority=1, supports_resume=False))
    manager.register(_ScriptedPlugin(plugin_id="resumy", priority=5, supports_resume=True))
    resp = client.post(
        "/api/download/download",
        json={"url": "http://x/f", "dest": str(tmp_path / "d.bin"), "resume": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    # 契约随更新（H1-②b-⑭）：异步 auto=fallback 链 ack——resume=True 链序只含
    # 可续传插件（require_resume 口径与择优同源），非续传被过滤不出现在链序
    assert data["plugin"] == "auto"
    assert data["fallback_chain"] == ["resumy"]


def test_auto_falls_back_to_native_when_no_plugin_available(dl_client):
    client, manager, native, tmp_path = dl_client
    manager.register(_ScriptedPlugin(plugin_id="ghost", available=False))
    try:
        resp = client.post(
            "/api/download/download",
            json={"url": "http://127.0.0.1:1/x", "dest": str(tmp_path / "d.bin")},
        )
        assert resp.status_code == 200
        assert resp.json()["plugin"] == "native"
        assert len(native.list_tasks()) == 1
    finally:
        for t in native.list_tasks():
            client.delete(f"/api/download/tasks/{t.id}")


def test_get_best_plugin_returns_none_when_nothing_available(dl_client):
    _client, manager, _native, _tmp = dl_client
    manager.register(_ScriptedPlugin(plugin_id="ghost", available=False))
    manager.register(_ScriptedPlugin(plugin_id="noresume", supports_resume=False))
    # 修前：fallback 返回不可用插件（"最优可用"名不符实）；现无可用候选如实 None
    assert manager.get_best_plugin(require_resume=True) is None
    assert manager.get_best_plugin() is not None  # 可用的 noresume 仍可被无需求择优选中
    assert manager.get_best_plugin(require_p2p=True) is None  # 能力过滤如实 None


# ─── ③ /tasks 控制面诚实（修前未知 task_id 假成功） ─────────────────


def test_pause_unknown_task_is_404_not_fake_success(dl_client):
    client, _manager, _native, _tmp = dl_client
    resp = client.post("/api/download/tasks/no-such-task/pause")
    assert resp.status_code == 404
    # 修前：200 {"status": "paused"} ——对不存在的任务谎报成功
    assert "status" not in resp.json()


def test_resume_unknown_task_is_404_not_fake_success(dl_client):
    client, _manager, _native, _tmp = dl_client
    resp = client.post("/api/download/tasks/no-such-task/resume")
    assert resp.status_code == 404
    assert "status" not in resp.json()


def test_delete_unknown_task_is_404_not_fake_success(dl_client):
    client, _manager, _native, _tmp = dl_client
    resp = client.delete("/api/download/tasks/no-such-task")
    assert resp.status_code == 404
    assert "status" not in resp.json()


def _seed_native(native: NativeDownloader, task_id: str, status: DownloadStatus) -> DownloadTask:
    task = DownloadTask(
        id=task_id,
        url="http://x/f",
        dest=f"/tmp/{task_id}.bin",
        status=status,
        _pause_event=asyncio.Event(),
        _cancel_event=asyncio.Event(),
    )
    native._tasks[task_id] = task
    return task


def test_native_pause_reports_true_engine_status(dl_client):
    client, _manager, native, _tmp = dl_client
    _seed_native(native, "t1", DownloadStatus.DOWNLOADING)
    resp = client.post("/api/download/tasks/t1/pause")
    assert resp.status_code == 200
    assert resp.json() == {"status": "paused", "task_id": "t1", "plugin": "native"}
    # 幂等：已停再停仍如实 paused
    resp2 = client.post("/api/download/tasks/t1/pause")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "paused"


def test_native_pause_done_task_reports_done_not_fabricated_paused(dl_client):
    client, _manager, native, _tmp = dl_client
    _seed_native(native, "t-done", DownloadStatus.DONE)
    resp = client.post("/api/download/tasks/t-done/pause")
    # 修前无条件回 "paused"（F4b-⑲ fabricated 同型）；现如实引擎真实状态
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"


def test_native_resume_reports_true_engine_status(dl_client):
    client, _manager, native, _tmp = dl_client
    _seed_native(native, "t2", DownloadStatus.PAUSED)
    # worker 在场哨兵：resume 走"原 worker 还活着"分支不重开传输（⑨a 单写者语义）
    native._running["t2"] = object()
    resp = client.post("/api/download/tasks/t2/resume")
    assert resp.status_code == 200
    assert resp.json() == {"status": "downloading", "task_id": "t2", "plugin": "native"}
    # 非 paused 任务 resume 不谎报 "resumed"
    _seed_native(native, "t3", DownloadStatus.PENDING)
    resp2 = client.post("/api/download/tasks/t3/resume")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "pending"


# ─── ④ plugin 任务真路由（⑨a pause/resume/cancel 首个 API 消费面） ──


def test_plugin_task_pause_resume_cancel_routes_to_plugin(dl_client):
    client, manager, _native, _tmp = dl_client
    plugin = _ScriptedPlugin(plugin_id="fake", hang=True)
    manager.register(plugin)
    dest = "/tmp/plugin-task-1"
    token = _task_token(dest)
    plugin._track(dest, "http://x/f", dest, True, proc=MagicMock())
    try:
        # pause：基类真实现（注册表迁移+SIGTERM 语义），响应带引擎身份
        resp = client.post(f"/api/download/tasks/{token}/pause")
        assert resp.status_code == 200
        assert resp.json() == {"status": "paused", "task_id": token, "plugin": "fake"}
        # resume：真二次拉起 download(resume=True)（⑨a 续传链首个 API 消费）
        resp2 = client.post(f"/api/download/tasks/{token}/resume")
        assert resp2.status_code == 200
        assert resp2.json() == {"status": "resumed", "task_id": token, "plugin": "fake"}
        _poll(
            client,
            "/api/download/tasks",
            lambda d: any(t["task_id"] == token for t in d.get("plugin_tasks", [])),
        )
        assert ("http://x/f", dest, True) in plugin.calls  # 续传真二次调用
        # 先删（续传任务在册）再放行——顺序反了收尾 _untrack 会让 DELETE 撞 404
        resp3 = client.delete(f"/api/download/tasks/{token}")
        assert resp3.status_code == 200
        assert resp3.json() == {"status": "removed", "task_id": token, "plugin": "fake"}
    finally:
        plugin._release.set()


def test_plugin_non_resumable_resume_refused_409(dl_client):
    client, manager, _native, _tmp = dl_client
    plugin = _ScriptedPlugin(plugin_id="flat", supports_resume=False)
    manager.register(plugin)
    dest = "/tmp/plugin-task-2"
    token = _task_token(dest)
    plugin._paused[dest] = ("http://x/f", dest, True)
    resp = client.post(f"/api/download/tasks/{token}/resume")
    # 如实拒绝（⑨a：supports_resume=False 先拒）——禁假 "resumed"
    assert resp.status_code == 409
    assert dest in plugin._paused  # 拒绝零副作用（续传上下文不丢）


def test_tasks_list_shows_plugin_tasks_additive_shape(dl_client):
    client, manager, native, _tmp = dl_client
    plugin = _ScriptedPlugin(plugin_id="fake", hang=True)
    manager.register(plugin)
    dest = "/tmp/plugin-task-3"
    token = _task_token(dest)
    plugin._track(dest, "http://x/f", dest, True, proc=MagicMock())
    _seed_native(native, "t-n", DownloadStatus.DOWNLOADING)
    try:
        resp = client.get("/api/download/tasks")
        assert resp.status_code == 200
        data = resp.json()
        # native tasks 契约不变 + additive plugin_tasks
        assert [t["id"] for t in data["tasks"]] == ["t-n"]
        rows = [t for t in data["plugin_tasks"] if t["task_id"] == token]
        assert rows and set(rows[0]) == {
            "task_id",
            "registry_key",
            "url",
            "dest",
            "status",
            "plugin",
            "supports_resume",
        }
        assert rows[0]["status"] == "downloading"
        assert rows[0]["plugin"] == "fake"
        assert rows[0]["registry_key"] == dest  # 令牌↔注册表键一一对应可回溯
    finally:
        plugin._release.set()
        plugin._paused.pop(dest, None)
        plugin._running.pop(dest, None)


# ─── ⑤ 真子进程 e2e：API→插件二进制全链（⑨a 假二进制同款） ─────────


def _make_fake_binary(bin_dir: Path, name: str, out_file: Path, log_file: Path) -> None:
    script = bin_dir / name
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log_file}"\n'
        f'if [ -s "{out_file}" ]; then\n'
        f'  printf rest >> "{out_file}"\n'
        "  exit 0\n"
        "fi\n"
        f'printf part >> "{out_file}"\n'
        "exec sleep 60\n"
    )
    script.chmod(0o755)


def test_api_to_plugin_binary_full_loop(dl_client, monkeypatch):
    client, manager, _native, tmp_path = dl_client
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out_file = tmp_path / "out.bin"
    log_file = tmp_path / "argv.log"
    _make_fake_binary(bin_dir, "aria2c", out_file, log_file)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    manager.register(Aria2Plugin())
    dest = str(out_file)
    token = _task_token(dest)
    try:
        resp = client.post(
            "/api/download/download",
            json={"url": "http://x/f", "dest": dest, "plugin_id": "aria2"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "task_id": token,
            "dest": dest,
            "url": "http://x/f",
            "status": "downloading",
            "plugin": "aria2",
        }
        # 真 spawn 登记进控制面 + 半成品落盘
        _poll(
            client,
            "/api/download/tasks",
            lambda d: any(t["task_id"] == token for t in d.get("plugin_tasks", [])),
        )
        _poll(
            client,
            "/api/download/tasks",
            lambda d: out_file.exists() and out_file.read_bytes() == b"part",
        )
        # 真暂停：SIGTERM 停传输留半成品
        resp2 = client.post(f"/api/download/tasks/{token}/pause")
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "paused"
        assert out_file.read_bytes() == b"part"
        # 真续传：二次拉起补完（补完后任务收尾从控制面消失）
        resp3 = client.post(f"/api/download/tasks/{token}/resume")
        assert resp3.status_code == 200
        assert resp3.json()["status"] == "resumed"
        _poll(
            client,
            "/api/download/tasks",
            lambda d: (
                out_file.read_bytes() == b"partrest"
                and not any(t["task_id"] == token for t in d.get("plugin_tasks", []))
            ),
        )
        assert len([ln for ln in log_file.read_text().splitlines() if ln.strip()]) == 2
    finally:
        client.delete(f"/api/download/tasks/{token}")
