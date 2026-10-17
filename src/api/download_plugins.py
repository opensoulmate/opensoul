"""Download plugin system - pluggable download protocols.

Supports: aria2 (resume+P2P), wget (resume), curl (basic), IPFS, BitTorrent.
Plugins can self-update and are hot-swappable.
"""

import asyncio
import logging
import os
import shutil
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


def _detect_os() -> str:
    import platform

    system = platform.system()
    if system == "Darwin":
        return "darwin"
    elif system == "Windows":
        return "windows"
    return "linux"


def _refresh_path():
    local_bin = os.path.expanduser("~/.local/bin")
    if local_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = local_bin + os.pathsep + os.environ.get("PATH", "")


class PluginStatus(Enum):
    AVAILABLE = "available"
    INSTALLING = "installing"
    UPDATING = "updating"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class DownloadProgress:
    """Progress info for a download task"""

    url: str
    dest: str
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed: float = 0  # bytes/sec
    eta: int = 0  # seconds
    status: str = "pending"  # pending, downloading, paused, done, error
    error: str | None = None
    supports_resume: bool = False
    plugin: str = ""

    @property
    def progress_pct(self) -> float:
        if self.total_bytes <= 0:
            return 0
        return min(100, (self.downloaded_bytes / self.total_bytes) * 100)


@dataclass
class PluginInfo:
    """Metadata for a download plugin"""

    id: str
    name: str
    description: str
    version: str
    binary: str
    supports_resume: bool
    supports_p2p: bool
    install_cmd: dict[str, str]  # os -> install command
    update_cmd: dict[str, str]
    check_version_cmd: str
    status: PluginStatus = PluginStatus.AVAILABLE
    priority: int = 100  # lower = higher priority


@dataclass
class _RunningDownload:
    """In-flight plugin download: the process plus the context needed to
    genuinely resume after a pause (re-run the binary with its continuation
    flag). Task identity = the ``dest`` path the download registers under —
    the same key ``pause``/``resume_download``/``cancel`` get as task_id.
    """

    url: str
    dest: str
    resume: bool
    proc: asyncio.subprocess.Process


class DownloadPlugin(ABC):
    """Abstract base for download plugins"""

    @abstractmethod
    def get_info(self) -> PluginInfo:
        """Return plugin metadata"""
        ...

    @abstractmethod
    async def download(
        self,
        url: str,
        dest: str,
        resume: bool = True,
        progress_cb: Callable[[DownloadProgress], None] | None = None,
    ) -> DownloadProgress:
        """Download a file with optional resume support"""
        ...

    # ── task registry + pause/resume/cancel（全部插件单一源） ──────────
    # 修前（H1-② 定性⑦ 假承诺面）：各插件 pause/resume_download/cancel 是
    # 单行 return True/False 常量——resume_download 什么都没续（接线即谎报
    # ok，F4b-⑲ 同型；Odl 连 pause/cancel 都是纯常量零操作）。现语义：
    # pause=停传输（SIGTERM）保留半成品+续传上下文；resume_download=真重跑
    # download(resume=True) 续传（continuation flag 由各 download() 的
    # resume=True 路径携带）；每个返回值反映真实发生的事，禁常量假承诺。

    _running: dict[str, _RunningDownload]  # 每插件类独立，__init_subclass__ 建
    _paused: dict[str, tuple[str, str, bool]]  # task_id -> (url, dest, resume)
    _resume_handles: dict[str, asyncio.Task]  # resume_download 派生的后台任务

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # 每插件类独立注册表（沿用旧 _tasks 类属性的隔离语义，禁跨插件共享）
        cls._running = {}
        cls._paused = {}
        cls._resume_handles = {}

    def _track(
        self,
        task_id: str,
        url: str,
        dest: str,
        resume: bool,
        proc: asyncio.subprocess.Process,
    ) -> None:
        self._running[task_id] = _RunningDownload(url=url, dest=dest, resume=resume, proc=proc)

    def _untrack(self, task_id: str, progress: DownloadProgress | None = None) -> None:
        self._running.pop(task_id, None)
        if progress is None:
            return
        if task_id in self._paused:
            if progress.status == "done":
                # pause 落地前刚好跑完：没有可续的东西，续传上下文作废
                self._paused.pop(task_id, None)
            elif progress.status == "error":
                # 这个 error 是 pause() 自己的 SIGTERM，不是传输故障——如实标 paused
                progress.status = "paused"
                progress.error = None

    async def pause(self, task_id: str) -> bool:
        """停传输并保留半成品+续传上下文。

        True=任务现在确实处于 paused（运行中→已停；已停→幂等 True）；
        无此任务如实 False。
        """
        running = self._running.get(task_id)
        if running is None:
            return task_id in self._paused
        self._paused[task_id] = (running.url, running.dest, running.resume)
        self._running.pop(task_id, None)
        running.proc.terminate()
        return True

    async def resume_download(self, task_id: str) -> bool:
        """真续传：重跑 download(resume=True)，二进制从半成品继续。

        False=如实拒绝：插件声明不支持续传（supports_resume=False）、
        无 paused 任务、或任务正在运行（没东西可续）。
        """
        if not self.get_info().supports_resume:
            return False
        ctx = self._paused.pop(task_id, None)
        if ctx is None:
            return False
        url, dest, _resume_flag = ctx
        handle = asyncio.create_task(self.download(url, dest, resume=True))

        def _cleanup(_h: asyncio.Task) -> None:
            if self._resume_handles.get(task_id) is handle:
                self._resume_handles.pop(task_id, None)

        handle.add_done_callback(_cleanup)
        self._resume_handles[task_id] = handle
        return True

    async def cancel(self, task_id: str) -> bool:
        """杀传输并丢弃续传上下文。True=确实取消了东西（运行中/已停/续传中）。"""
        running = self._running.pop(task_id, None)
        paused = self._paused.pop(task_id, None)
        handle = self._resume_handles.pop(task_id, None)
        if handle is not None and not handle.done():
            handle.cancel()
        if running is not None:
            running.proc.kill()
            return True
        return paused is not None or handle is not None

    # ── 任务控制面 introspection（/tasks 路由单一判法，禁各端点手抄注册表） ──
    def task_states(self) -> dict[str, str]:
        """在途任务面：task_id -> 'downloading' | 'paused'（注册表原样，不 fabricate）。"""
        states: dict[str, str] = {tid: "downloading" for tid in self._running}
        for tid in self._paused:
            states[tid] = "paused"
        return states

    def task_status(self, task_id: str) -> str | None:
        """单任务状态；不在册如实 None（禁拿未知任务硬报状态）。"""
        return self.task_states().get(task_id)

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the binary is installed"""
        ...

    @abstractmethod
    async def install(self) -> bool:
        """Install the plugin binary"""
        ...

    @abstractmethod
    async def update(self) -> bool:
        """Update the plugin binary"""
        ...

    @abstractmethod
    async def get_version(self) -> str | None:
        """Get installed version"""
        ...


# ─── Aria2 Plugin (resume + P2P via BitTorrent) ──────────────────


class Aria2Plugin(DownloadPlugin):
    """aria2c - supports HTTP resume, BitTorrent, Metalink"""

    def get_info(self) -> PluginInfo:
        return PluginInfo(
            id="aria2",
            name="Aria2",
            description="高速下载器，支持断点续传+BT+Metalink",
            version="1.37.0",
            binary="aria2c",
            supports_resume=True,
            supports_p2p=True,
            install_cmd={
                "linux": "mkdir -p ~/.local/bin && curl -sL https://ghfast.top/https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-linux-gnu-64bit-build1.tar.gz | tar xzf - -C /tmp && cp /tmp/aria2-1.37.0-linux-gnu-64bit-build1/aria2c ~/.local/bin/ && chmod +x ~/.local/bin/aria2c",
                "darwin": "brew install aria2",
                "windows": "winget install aria2.aria2 || choco install aria2",
            },
            update_cmd={
                "linux": "pacman -Syu --noconfirm aria2 2>/dev/null || apt upgrade -y aria2 2>/dev/null || echo already_latest",
                "darwin": "brew upgrade aria2",
                "windows": "winget upgrade aria2.aria2 || choco upgrade aria2",
            },
            check_version_cmd="aria2c --version | head -1",
            priority=10,  # highest priority
        )

    async def download(
        self,
        url: str,
        dest: str,
        resume: bool = True,
        progress_cb: Callable[[DownloadProgress], None] | None = None,
    ) -> DownloadProgress:
        progress = DownloadProgress(url=url, dest=dest, plugin="aria2", supports_resume=True)
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Check for partial file
        dest + ".aria2"
        if resume and dest_path.exists():
            progress.downloaded_bytes = dest_path.stat().st_size
            progress.status = "downloading"

        cmd = [
            "aria2c",
            "--dir",
            str(dest_path.parent),
            "--out",
            dest_path.name,
            "--continue=true" if resume else "--continue=false",
            "--file-allocation=none",
            "--summary-interval=1",
            "--enable-color=false",
            url,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._track(dest, url, dest, resume, proc)

            while True:
                line = await (proc.stdout.readline() if proc.stdout else asyncio.sleep(0))
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()

                # Parse aria2 progress: [#abc123 1.2MiB/10MiB(12%) CN:1 DL:512KiB ETA:18s]
                if "/" in text and "(" in text:
                    try:
                        parts = text.split("(")[1].split(")")[0].replace("%", "")
                        progress.progress_pct  # just reference
                        progress.downloaded_bytes = int(progress.total_bytes * float(parts) / 100)
                        if "DL:" in text:
                            speed_str = (
                                text.split("DL:")[1]
                                .split()[0]
                                .replace("KiB", "")
                                .replace("MiB", "")
                                .replace("GiB", "")
                            )
                            progress.speed = float(speed_str) * 1024  # approximate
                        if "ETA:" in text:
                            eta_str = text.split("ETA:")[1].split("]")[0].strip().replace("s", "")
                            progress.eta = int(eta_str)
                    except (ValueError, IndexError):
                        pass

                if progress_cb:
                    progress_cb(progress)

            await proc.wait()
            if proc.returncode == 0:
                progress.status = "done"
                progress.downloaded_bytes = progress.total_bytes or dest_path.stat().st_size
                progress.total_bytes = progress.downloaded_bytes
            else:
                progress.status = "error"
                progress.error = f"aria2c exit code {proc.returncode}"

        except FileNotFoundError:
            progress.status = "error"
            progress.error = "aria2c not found"
        except Exception as e:
            progress.status = "error"
            progress.error = str(e)
        finally:
            self._untrack(dest, progress)

        if progress_cb:
            progress_cb(progress)
        return progress

    def is_available(self) -> bool:
        return shutil.which("aria2c") is not None

    async def install(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.install_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no install command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: install timed out after 60s")
                return False
            if proc.returncode == 0:
                _refresh_path()
                return True
            logger.error(f"{info.name}: install failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: install exception: {e}")
            return False

    async def update(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.update_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no update command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: update timed out after 60s")
                return False
            if proc.returncode == 0:
                return True
            logger.error(f"{info.name}: update failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: update exception: {e}")
            return False

    async def get_version(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "aria2c",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip().split("\n")[0] if stdout else None
        except Exception as e:
            logger.error(f"Aria2: get_version exception: {e}")
            return None


# ─── Wget Plugin (HTTP resume) ────────────────────────────────────


class WgetPlugin(DownloadPlugin):
    """wget - supports HTTP resume via Range header"""

    def get_info(self) -> PluginInfo:
        return PluginInfo(
            id="wget",
            name="Wget",
            description="经典下载器，支持HTTP断点续传",
            version="1.24",
            binary="wget",
            supports_resume=True,
            supports_p2p=False,
            install_cmd={
                "linux": "pacman -S --noconfirm wget 2>/dev/null || apt install -y wget 2>/dev/null || pip install wget",
                "darwin": "brew install wget",
                "windows": "winget install GNU.Wget || choco install wget",
            },
            update_cmd={
                "linux": "pacman -Syu --noconfirm wget 2>/dev/null || apt upgrade -y wget 2>/dev/null || echo already_latest",
                "darwin": "brew upgrade wget",
                "windows": "winget upgrade GNU.Wget || choco upgrade wget",
            },
            check_version_cmd="wget --version | head -1",
            priority=50,
        )

    async def download(
        self,
        url: str,
        dest: str,
        resume: bool = True,
        progress_cb: Callable[[DownloadProgress], None] | None = None,
    ) -> DownloadProgress:
        progress = DownloadProgress(url=url, dest=dest, plugin="wget", supports_resume=True)
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["wget", "-c" if resume else "", "-O", str(dest_path), "--progress=dot:mega", url]
        cmd = [c for c in cmd if c]  # remove empty strings

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._track(dest, url, dest, resume, proc)

            while True:
                line = await (proc.stdout.readline() if proc.stdout else asyncio.sleep(0))
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                # wget progress: .......... .......... ..........  50%  512K  18s
                if "%" in text:
                    try:
                        pct = float(text.split("%")[0].strip().split()[-1])
                        progress.downloaded_bytes = int(progress.total_bytes * pct / 100)
                    except (ValueError, IndexError):
                        pass
                if progress_cb:
                    progress_cb(progress)

            await proc.wait()
            progress.status = "done" if proc.returncode == 0 else "error"
            if proc.returncode != 0:
                progress.error = f"wget exit code {proc.returncode}"
        except Exception as e:
            progress.status = "error"
            progress.error = str(e)
        finally:
            self._untrack(dest, progress)

        if progress_cb:
            progress_cb(progress)
        return progress

    def is_available(self) -> bool:
        return shutil.which("wget") is not None

    async def install(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.install_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no install command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: install timed out after 60s")
                return False
            if proc.returncode == 0:
                _refresh_path()
                return True
            logger.error(f"{info.name}: install failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: install exception: {e}")
            return False

    async def update(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.update_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no update command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: update timed out after 60s")
                return False
            if proc.returncode == 0:
                return True
            logger.error(f"{info.name}: update failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: update exception: {e}")
            return False

    async def get_version(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "wget",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip().split("\n")[0] if stdout else None
        except Exception as e:
            logger.error(f"Wget: get_version exception: {e}")
            return None


# ─── Curl Plugin (basic, no resume) ──────────────────────────────


class CurlPlugin(DownloadPlugin):
    """curl - basic download, always available as fallback"""

    def get_info(self) -> PluginInfo:
        return PluginInfo(
            id="curl",
            name="cURL",
            description="基础下载器，无断点续传",
            version="8.0",
            binary="curl",
            supports_resume=False,
            supports_p2p=False,
            install_cmd={
                "linux": "echo curl is pre-installed",
                "darwin": "echo curl is pre-installed",
                "windows": "echo curl is pre-installed",
            },
            update_cmd={
                "linux": "echo curl is system-managed",
                "darwin": "echo curl is system-managed",
                "windows": "echo curl is system-managed",
            },
            check_version_cmd="curl --version | head -1",
            priority=100,  # lowest priority
        )

    async def download(
        self,
        url: str,
        dest: str,
        resume: bool = True,
        progress_cb: Callable[[DownloadProgress], None] | None = None,
    ) -> DownloadProgress:
        progress = DownloadProgress(url=url, dest=dest, plugin="curl", supports_resume=False)
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["curl", "-L", "-o", str(dest_path), "--progress-bar", url]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._track(dest, url, dest, resume, proc)

            while True:
                line = await (proc.stdout.readline() if proc.stdout else asyncio.sleep(0))
                if not line:
                    break
                if progress_cb:
                    progress_cb(progress)

            await proc.wait()
            progress.status = "done" if proc.returncode == 0 else "error"
            if proc.returncode != 0:
                progress.error = f"curl exit code {proc.returncode}"
        except Exception as e:
            progress.status = "error"
            progress.error = str(e)
        finally:
            self._untrack(dest, progress)

        if progress_cb:
            progress_cb(progress)
        return progress

    def is_available(self) -> bool:
        return shutil.which("curl") is not None

    async def install(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.install_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no install command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: install timed out after 60s")
                return False
            if proc.returncode == 0:
                _refresh_path()
                return True
            logger.error(f"{info.name}: install failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: install exception: {e}")
            return False

    async def update(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.update_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no update command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: update timed out after 60s")
                return False
            if proc.returncode == 0:
                return True
            logger.error(f"{info.name}: update failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: update exception: {e}")
            return False

    async def get_version(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip().split("\n")[0] if stdout else None
        except Exception as e:
            logger.error(f"cURL: get_version exception: {e}")
            return None


# ─── Plugin Manager ───────────────────────────────────────────────


class DownloadManager:
    """Manages download plugins with auto-fallback and auto-update"""

    def __init__(self):
        self._plugins: dict[str, DownloadPlugin] = {}
        self._register_builtins()

    def _register_builtins(self):
        """Register built-in plugins"""
        self.register(Aria2Plugin())
        self.register(WgetPlugin())
        self.register(CurlPlugin())
        self.register(CurlResumePlugin())
        self.register(AxelPlugin())
        self.register(OdlPlugin())
        # self.register(RsyncPlugin())  # removed: rsync is sync tool, not download

    def register(self, plugin: DownloadPlugin):
        """Register a download plugin"""
        info = plugin.get_info()
        self._plugins[info.id] = plugin
        logger.info(
            f"Registered download plugin: {info.name} (resume={info.supports_resume}, p2p={info.supports_p2p})"
        )

    def unregister(self, plugin_id: str):
        """Unregister a plugin"""
        self._plugins.pop(plugin_id, None)

    def get_plugin(self, plugin_id: str) -> DownloadPlugin | None:
        """Get a specific plugin"""
        return self._plugins.get(plugin_id)

    def get_best_plugin(
        self, require_resume: bool = False, require_p2p: bool = False
    ) -> DownloadPlugin | None:
        """Get the best available plugin matching requirements.

        择优只在**可用**插件里选（函数名/文档即契约）：修前无可用候选时
        fallback 返回一个不可用插件（"download() will fail and we'll fallback"
        ——但全仓无该 fallback 链消费方）=「最优可用」名不符实，调用方拿去
        硬跑必炸。现无可用候选如实返回 None，回落决策权在调用方。
        """
        candidates = []
        for plugin in self._plugins.values():
            info = plugin.get_info()
            if not plugin.is_available():
                continue
            if require_resume and not info.supports_resume:
                continue
            if require_p2p and not info.supports_p2p:
                continue
            candidates.append((info.priority, plugin))

        candidates.sort(key=lambda x: x[0])
        return candidates[0][1] if candidates else None

    def find_task(self, task_id: str) -> tuple["DownloadPlugin", str] | None:
        """跨插件查在途任务（task 控制面路由单一判法）：(plugin, 状态) 或 None。"""
        for plugin in self._plugins.values():
            state = plugin.task_status(task_id)
            if state is not None:
                return plugin, state
        return None

    def plugin_tasks(self) -> list[dict]:
        """全部插件在途任务（/tasks 列表面 additive 键的数据源）。"""
        rows: list[dict] = []
        for plugin in sorted(self._plugins.values(), key=lambda p: p.get_info().priority):
            info = plugin.get_info()
            for task_id, state in sorted(plugin.task_states().items()):
                running = plugin._running.get(task_id)
                if running is not None:
                    url, dest = running.url, running.dest
                else:
                    paused_ctx = plugin._paused.get(task_id)
                    url, dest = (
                        (paused_ctx[0], paused_ctx[1]) if paused_ctx is not None else ("", task_id)
                    )
                rows.append(
                    {
                        "task_id": task_id,
                        "url": url,
                        "dest": dest,
                        "status": state,
                        "plugin": info.id,
                        "supports_resume": info.supports_resume,
                    }
                )
        return rows

    def list_plugins(self) -> list[PluginInfo]:
        """List all registered plugins with status"""
        result = []
        for plugin in self._plugins.values():
            info = plugin.get_info()
            info.status = PluginStatus.AVAILABLE if plugin.is_available() else PluginStatus.DISABLED
            result.append(info)
        return sorted(result, key=lambda x: x.priority)

    async def download(
        self,
        url: str,
        dest: str,
        resume: bool = True,
        plugin_id: str | None = None,
        progress_cb: Callable[[DownloadProgress], None] | None = None,
    ) -> DownloadProgress:
        """Download with auto-fallback across plugins"""
        # Get plugin chain
        if plugin_id:
            plugin = self.get_plugin(plugin_id)
            if not plugin:
                return DownloadProgress(
                    url=url, dest=dest, status="error", error=f"Plugin {plugin_id} not found"
                )
            plugins = [plugin]
        else:
            # Build fallback chain
            plugins = sorted(self._plugins.values(), key=lambda p: p.get_info().priority)
            if resume:
                plugins = [p for p in plugins if p.get_info().supports_resume] + [
                    p for p in plugins if not p.get_info().supports_resume
                ]

        last_error = None
        for plugin in plugins:
            if not plugin.is_available():
                continue
            info = plugin.get_info()
            logger.info(f"Trying download with {info.name}: {url}")
            try:
                result = await plugin.download(url, dest, resume=resume, progress_cb=progress_cb)
                if result.status == "done":
                    return result
                last_error = result.error
                logger.warning(f"{info.name} failed: {last_error}")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"{info.name} exception: {last_error}")

        return DownloadProgress(
            url=url,
            dest=dest,
            status="error",
            error=f"All plugins failed. Last error: {last_error}",
        )

    async def auto_update_plugins(self) -> dict[str, bool]:
        """Auto-update all installed plugins"""
        results = {}
        for plugin in self._plugins.values():
            if plugin.is_available():
                try:
                    success = await plugin.update()
                    results[plugin.get_info().id] = success
                except Exception as e:
                    results[plugin.get_info().id] = False
                    logger.error(f"Failed to update {plugin.get_info().name}: {e}")
        return results

    async def install_plugin(self, plugin_id: str) -> bool:
        """Install a plugin"""
        plugin = self.get_plugin(plugin_id)
        if not plugin:
            return False
        if plugin.is_available():
            return True
        return await plugin.install()


# Singleton
_download_manager: DownloadManager | None = None


def get_download_manager() -> DownloadManager:
    global _download_manager
    if _download_manager is None:
        _download_manager = DownloadManager()
    return _download_manager


# ─── Axel Plugin (multi-threaded HTTP/FTP) ──────────────────────


class AxelPlugin(DownloadPlugin):
    """axel - multi-threaded download accelerator with resume"""

    def get_info(self) -> PluginInfo:
        return PluginInfo(
            id="axel",
            name="Axel",
            description="多线程下载加速器，支持HTTP/FTP断点续传",
            version="2.17",
            binary="axel",
            supports_resume=True,
            supports_p2p=False,
            install_cmd={
                "linux": "mkdir -p ~/.local/bin && curl -sL https://ghfast.top/https://github.com/axel-download-accelerator/axel/releases/download/v2.17.13/axel-2.17.13-linux-x86_64.tar.gz | tar xzf - -C /tmp && cp /tmp/axel-*/axel ~/.local/bin/ && chmod +x ~/.local/bin/axel",
                "darwin": "brew install axel",
                "windows": "choco install axel",
            },
            update_cmd={
                "linux": "pacman -Syu --noconfirm axel 2>/dev/null || apt upgrade -y axel 2>/dev/null || echo already_latest",
                "darwin": "brew upgrade axel",
                "windows": "choco upgrade axel",
            },
            check_version_cmd="axel --version | head -1",
            priority=20,  # between aria2 and wget
        )

    async def download(
        self,
        url: str,
        dest: str,
        resume: bool = True,
        progress_cb: Callable[[DownloadProgress], None] | None = None,
    ) -> DownloadProgress:
        progress = DownloadProgress(url=url, dest=dest, plugin="axel", supports_resume=True)
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["axel", "-n", "4"]  # 4 threads
        if resume and dest_path.exists():
            cmd.append("-c")  # continue
        cmd.extend(["-o", str(dest_path), url])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._track(dest, url, dest, resume, proc)

            while True:
                line = await (proc.stdout.readline() if proc.stdout else asyncio.sleep(0))
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                # axel progress: [  0%] [.......... .......... .......... ..........] [ 512KB/s] [ETA: 18s]
                if "%" in text:
                    try:
                        pct = float(
                            text.split("%")[0].strip().split()[-1].replace("[", "").replace("]", "")
                        )
                        progress.downloaded_bytes = int(progress.total_bytes * pct / 100)
                        if "KB/s" in text:
                            speed = float(text.split("KB/s")[0].split()[-1].split("[")[-1])
                            progress.speed = speed * 1024
                        elif "MB/s" in text:
                            speed = float(text.split("MB/s")[0].split()[-1].split("[")[-1])
                            progress.speed = speed * 1024 * 1024
                        if "ETA:" in text:
                            eta = text.split("ETA:")[1].split("]")[0].strip().replace("s", "")
                            progress.eta = int(float(eta))
                    except (ValueError, IndexError):
                        pass
                if progress_cb:
                    progress_cb(progress)

            await proc.wait()
            progress.status = "done" if proc.returncode == 0 else "error"
            if proc.returncode != 0:
                progress.error = f"axel exit code {proc.returncode}"
        except Exception as e:
            progress.status = "error"
            progress.error = str(e)
        finally:
            self._untrack(dest, progress)

        if progress_cb:
            progress_cb(progress)
        return progress

    def is_available(self) -> bool:
        return shutil.which("axel") is not None

    async def install(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.install_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no install command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: install timed out after 60s")
                return False
            if proc.returncode == 0:
                _refresh_path()
                return True
            logger.error(f"{info.name}: install failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: install exception: {e}")
            return False

    async def update(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.update_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no update command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: update timed out after 60s")
                return False
            if proc.returncode == 0:
                return True
            logger.error(f"{info.name}: update failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: update exception: {e}")
            return False

    async def get_version(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "axel",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip().split("\n")[0] if stdout else None
        except Exception as e:
            logger.error(f"Axel: get_version exception: {e}")
            return None


# ─── Curl Enhanced Plugin (with HTTP Range resume) ───────────────


class CurlResumePlugin(DownloadPlugin):
    """curl with HTTP Range header for resume support"""

    def get_info(self) -> PluginInfo:
        return PluginInfo(
            id="curl-resume",
            name="cURL (Resume)",
            description="cURL增强版，支持HTTP Range断点续传",
            version="8.0",
            binary="curl",
            supports_resume=True,
            supports_p2p=False,
            install_cmd={
                "linux": "echo curl is pre-installed",
                "darwin": "echo curl is pre-installed",
                "windows": "echo curl is pre-installed",
            },
            update_cmd={
                "linux": "echo curl is system-managed",
                "darwin": "echo curl is system-managed",
                "windows": "echo curl is system-managed",
            },
            check_version_cmd="curl --version | head -1",
            priority=45,  # between wget and axel
        )

    async def download(
        self,
        url: str,
        dest: str,
        resume: bool = True,
        progress_cb: Callable[[DownloadProgress], None] | None = None,
    ) -> DownloadProgress:
        progress = DownloadProgress(url=url, dest=dest, plugin="curl-resume", supports_resume=True)
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["curl", "-L", "-C", "-", "-o", str(dest_path), "--progress-bar", url]
        if not resume:
            cmd = ["curl", "-L", "-o", str(dest_path), "--progress-bar", url]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._track(dest, url, dest, resume, proc)

            while True:
                line = await (proc.stdout.readline() if proc.stdout else asyncio.sleep(0))
                if not line:
                    break
                if progress_cb:
                    progress_cb(progress)

            await proc.wait()
            progress.status = "done" if proc.returncode == 0 else "error"
            if proc.returncode != 0:
                progress.error = f"curl exit code {proc.returncode}"
        except Exception as e:
            progress.status = "error"
            progress.error = str(e)
        finally:
            self._untrack(dest, progress)

        if progress_cb:
            progress_cb(progress)
        return progress

    def is_available(self) -> bool:
        return shutil.which("curl") is not None

    async def install(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.install_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no install command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: install timed out after 60s")
                return False
            if proc.returncode == 0:
                _refresh_path()
                return True
            logger.error(f"{info.name}: install failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: install exception: {e}")
            return False

    async def update(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.update_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no update command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: update timed out after 60s")
                return False
            if proc.returncode == 0:
                return True
            logger.error(f"{info.name}: update failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: update exception: {e}")
            return False

    async def get_version(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip().split("\n")[0] if stdout else None
        except Exception as e:
            logger.error(f"cURL-Resume: get_version exception: {e}")
            return None


# ─── Rsync Plugin (large file sync) ──────────────────────────────
# ─── Odl Plugin (Rust native multi-segment downloader) ─────────


class OdlPlugin(DownloadPlugin):
    """odl - Rust native multi-segment downloader, no external deps"""

    def get_info(self) -> PluginInfo:
        return PluginInfo(
            id="odl",
            name="Odl (Native)",
            description="原生多线程下载引擎(Rust)，跨平台无需安装",
            version="1.0",
            binary="odl",
            supports_resume=True,
            supports_p2p=False,
            install_cmd={},
            update_cmd={},
            check_version_cmd="odl --help | head -1",
            priority=5,
        )

    def is_available(self) -> bool:
        return shutil.which("odl") is not None

    async def install(self) -> bool:
        return self.is_available()

    async def update(self) -> bool:
        return True

    async def download(
        self, url: str, dest: str, resume: bool = True, progress_cb=None
    ) -> DownloadProgress:
        progress = DownloadProgress(url=url, dest=dest, plugin="odl", supports_resume=True)
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["odl", "download", url, "-o", str(dest_path), "-s", "8"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._track(dest, url, dest, resume, proc)

            while True:
                line = await (proc.stdout.readline() if proc.stdout else asyncio.sleep(0))
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                try:
                    import json as _json

                    data = _json.loads(text)
                    if data.get("event") == "progress":
                        progress.downloaded_bytes = data.get("total_downloaded", 0)
                        progress.status = "downloading"
                    elif data.get("event") == "complete":
                        progress.downloaded_bytes = data.get("total_bytes", 0)
                        progress.total_bytes = data.get("total_bytes", 0)
                        progress.status = "done"
                except Exception as exc:
                    logging.getLogger(__name__).debug("operation skipped: %s", exc)
            await proc.wait()
            if proc.returncode != 0:
                progress.status = "error"
                progress.error = f"odl exit code {proc.returncode}"
            else:
                progress.status = "done"
        except FileNotFoundError:
            progress.status = "error"
            progress.error = "odl not found"
        except Exception as e:
            progress.status = "error"
            progress.error = str(e)
        finally:
            self._untrack(dest, progress)
        return progress

    async def get_version(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "odl",
                "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip().split("\n")[0] if stdout else "1.0"
        except Exception:
            return "1.0"


class RsyncPlugin(DownloadPlugin):
    """rsync - delta sync for large files, resume support"""

    def get_info(self) -> PluginInfo:
        return PluginInfo(
            id="rsync",
            name="Rsync",
            description="增量同步大文件，支持断点续传",
            version="3.3",
            binary="rsync",
            supports_resume=True,
            supports_p2p=False,
            install_cmd={
                "linux": "pacman -S --noconfirm rsync 2>/dev/null || apt install -y rsync 2>/dev/null || echo install_manually",
                "darwin": "brew install rsync",
                "windows": "winget install Rsync.Rsync || choco install rsync",
            },
            update_cmd={
                "linux": "pacman -Syu --noconfirm rsync 2>/dev/null || apt upgrade -y rsync 2>/dev/null || echo already_latest",
                "darwin": "brew upgrade rsync",
                "windows": "winget upgrade Rsync.Rsync || choco upgrade rsync",
            },
            check_version_cmd="rsync --version | head -1",
            priority=60,  # lower priority, for specific use cases
        )

    async def download(
        self,
        url: str,
        dest: str,
        resume: bool = True,
        progress_cb: Callable[[DownloadProgress], None] | None = None,
    ) -> DownloadProgress:
        progress = DownloadProgress(url=url, dest=dest, plugin="rsync", supports_resume=True)
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # rsync works with rsync:// protocol or local paths
        # For HTTP URLs, fall back to other plugins
        if url.startswith("http://") or url.startswith("https://"):
            progress.status = "error"
            progress.error = "rsync only supports rsync:// and local paths"
            return progress

        cmd = ["rsync", "-avP", "--partial", url, str(dest_path)]
        if not resume:
            cmd = ["rsync", "-av", url, str(dest_path)]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._track(dest, url, dest, resume, proc)

            while True:
                line = await (proc.stdout.readline() if proc.stdout else asyncio.sleep(0))
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                # rsync progress: 1,234,567  45%  512.00kB/s  0:00:18
                if "%" in text:
                    try:
                        parts = text.split()
                        for i, p in enumerate(parts):
                            if "%" in p:
                                float(p.replace("%", ""))
                                if i > 0:
                                    progress.downloaded_bytes = int(parts[i - 1].replace(",", ""))
                                break
                    except (ValueError, IndexError):
                        pass
                if progress_cb:
                    progress_cb(progress)

            await proc.wait()
            progress.status = "done" if proc.returncode == 0 else "error"
            if proc.returncode != 0:
                progress.error = f"rsync exit code {proc.returncode}"
        except Exception as e:
            progress.status = "error"
            progress.error = str(e)
        finally:
            self._untrack(dest, progress)

        if progress_cb:
            progress_cb(progress)
        return progress

    def is_available(self) -> bool:
        return shutil.which("rsync") is not None

    async def install(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.install_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no install command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: install timed out after 60s")
                return False
            if proc.returncode == 0:
                _refresh_path()
                return True
            logger.error(f"{info.name}: install failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: install exception: {e}")
            return False

    async def update(self) -> bool:
        info = self.get_info()
        os_name = _detect_os()
        cmd = info.update_cmd.get(os_name)
        if not cmd:
            logger.error(f"{info.name}: no update command for {os_name}")
            return False
        try:
            proc = await asyncio.create_subprocess_shell(cmd)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                proc.kill()
                logger.error(f"{info.name}: update timed out after 60s")
                return False
            if proc.returncode == 0:
                return True
            logger.error(f"{info.name}: update failed with exit code {proc.returncode}")
            return False
        except Exception as e:
            logger.error(f"{info.name}: update exception: {e}")
            return False

    async def get_version(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "rsync",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            return stdout.decode().strip().split("\n")[0] if stdout else None
        except Exception as e:
            logger.error(f"Rsync: get_version exception: {e}")
            return None
