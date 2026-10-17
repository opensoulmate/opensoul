"""download 续传面暂停/续传真实性——真续传或如实拒绝，禁假承诺。

修前（H1-② 定性⑦ 假承诺面）：各插件 pause/resume_download/cancel 是单行
return True/False 常量——resume_download 什么都没续（接线即谎报 ok，F4b-⑲
同型），Odl 连 pause/cancel 都是纯常量零操作；native resume 无条件重开
_run_download = 双写者损坏。本文件用假二进制驱动真实子进程（真 spawn、真
SIGTERM、真二次拉起）+ 真 HTTP 服务驱动 native 续传，证明续传是真的发生、
拒绝是真的拒绝。
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.api.download_plugins import (
    Aria2Plugin,
    AxelPlugin,
    CurlPlugin,
    CurlResumePlugin,
    DownloadProgress,
    OdlPlugin,
    RsyncPlugin,
    WgetPlugin,
)
from src.api.native_downloader import DownloadStatus, NativeDownloader

# ─── 假二进制（真子进程生命周期） ─────────────────────────────────


def _make_fake_binary(bin_dir: Path, name: str, out_file: Path, log_file: Path) -> None:
    """可执行脚本：记录 argv；首跑写半成品后挂起（可被 pause 杀掉），再跑补完。

    二进制是假的，但机制是真的：create_subprocess_exec 真拉起、terminate 真
    SIGTERM、resume 真二次执行同一命令行（continuation flag 在 argv 里可验）。
    """
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


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """PATH 注入假二进制目录；返回 (out_file, log_file)。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out_file = tmp_path / "out.bin"
    log_file = tmp_path / "argv.log"
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir, out_file, log_file


async def _wait_until(cond, timeout: float = 10.0, interval: float = 0.02) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while not cond():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        await asyncio.sleep(interval)


def _argv_lines(log_file: Path) -> list[str]:
    if not log_file.exists():
        return []
    return [line for line in log_file.read_text().splitlines() if line.strip()]


# ─── 真暂停：停传输+半成品保留+如实 paused（非 error） ─────────────


@pytest.mark.asyncio
async def test_pause_stops_transfer_and_reports_paused_not_error(fake_env):
    bin_dir, out_file, log_file = fake_env
    _make_fake_binary(bin_dir, "aria2c", out_file, log_file)
    p = Aria2Plugin()
    dest = str(out_file)
    bg = asyncio.create_task(p.download("http://x/f", dest))
    try:
        await _wait_until(lambda: dest in p._running and log_file.exists())
        assert await p.pause(dest) is True
        progress = await bg
        # pause 自己的 SIGTERM 不是传输故障——如实 paused，禁谎报 error
        assert progress.status == "paused"
        assert progress.error is None
        # 半成品保留（真文件系统证据）
        assert out_file.read_bytes() == b"part"
    finally:
        await p.cancel(dest)
        bg.cancel()


@pytest.mark.asyncio
async def test_pause_is_idempotent_and_unknown_is_false(fake_env):
    bin_dir, out_file, log_file = fake_env
    _make_fake_binary(bin_dir, "aria2c", out_file, log_file)
    p = Aria2Plugin()
    dest = str(out_file)
    bg = asyncio.create_task(p.download("http://x/f", dest))
    try:
        await _wait_until(lambda: dest in p._running)
        assert await p.pause(dest) is True
        assert await p.pause(dest) is True  # 已停=幂等 True（确实处于 paused）
        assert await p.pause("nonexistent") is False
    finally:
        await p.cancel(dest)
        bg.cancel()


@pytest.mark.asyncio
async def test_cancel_kills_running_transfer(fake_env):
    bin_dir, out_file, log_file = fake_env
    _make_fake_binary(bin_dir, "aria2c", out_file, log_file)
    p = Aria2Plugin()
    dest = str(out_file)
    bg = asyncio.create_task(p.download("http://x/f", dest))
    try:
        await _wait_until(lambda: dest in p._running)
        assert await p.cancel(dest) is True
        await asyncio.wait_for(bg, timeout=10)  # 被杀后 download() 收尾返回
    finally:
        bg.cancel()


# ─── 真续传：二次拉起二进制 + continuation flag 可验 ───────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("plugin_cls", "binary", "resume_flag"),
    [
        (Aria2Plugin, "aria2c", "--continue=true"),
        (WgetPlugin, "wget", "-c"),
        (CurlResumePlugin, "curl", "-C"),
    ],
)
async def test_resume_download_genuinely_reinvokes_with_continuation_flag(
    fake_env, plugin_cls, binary, resume_flag
):
    bin_dir, out_file, log_file = fake_env
    _make_fake_binary(bin_dir, binary, out_file, log_file)
    p = plugin_cls()
    dest = str(out_file)
    bg = asyncio.create_task(p.download("http://x/f", dest))
    try:
        await _wait_until(lambda: dest in p._running and log_file.exists())
        assert await p.pause(dest) is True
        await bg
        assert len(_argv_lines(log_file)) == 1

        assert await p.resume_download(dest) is True
        await _wait_until(lambda: out_file.read_bytes() == b"partrest", timeout=10)

        lines = _argv_lines(log_file)
        assert len(lines) == 2, f"续传必须真二次拉起二进制，实际 argv={lines}"
        assert resume_flag in lines[1], f"续传必须带 continuation flag，实际 argv={lines[1]}"
    finally:
        await p.cancel(dest)
        bg.cancel()


@pytest.mark.asyncio
async def test_resume_without_pause_is_refused_and_fabricates_nothing(fake_env):
    bin_dir, out_file, log_file = fake_env
    _make_fake_binary(bin_dir, "aria2c", out_file, log_file)
    p = Aria2Plugin()
    dest = str(out_file)
    # 没有 paused 任务 → 如实 False，且绝不擅自拉起二进制
    assert await p.resume_download(dest) is False
    assert await p.resume_download("nonexistent") is False
    assert _argv_lines(log_file) == []


@pytest.mark.asyncio
async def test_resume_refused_when_plugin_declares_no_resume_support(fake_env):
    bin_dir, out_file, log_file = fake_env
    _make_fake_binary(bin_dir, "curl", out_file, log_file)
    p = CurlPlugin()  # supports_resume=False
    dest = str(out_file)
    bg = asyncio.create_task(p.download("http://x/f", dest))
    try:
        await _wait_until(lambda: dest in p._running and log_file.exists())
        assert await p.pause(dest) is True
        await bg
        # 如实拒绝（修前 curl 的 return False 是唯一诚实件，语义保留），不造二次执行
        assert await p.resume_download(dest) is False
        assert len(_argv_lines(log_file)) == 1
    finally:
        await p.cancel(dest)
        bg.cancel()


@pytest.mark.asyncio
async def test_cancel_after_pause_drops_resume_context(fake_env):
    bin_dir, out_file, log_file = fake_env
    _make_fake_binary(bin_dir, "aria2c", out_file, log_file)
    p = Aria2Plugin()
    dest = str(out_file)
    bg = asyncio.create_task(p.download("http://x/f", dest))
    try:
        await _wait_until(lambda: dest in p._running)
        assert await p.pause(dest) is True
        assert await p.cancel(dest) is True  # 取消的是 paused 记录
        assert await p.resume_download(dest) is False  # 上下文已丢，禁复活
    finally:
        bg.cancel()


@pytest.mark.asyncio
async def test_finished_before_pause_landed_discards_resume_context():
    """pause 与完成竞态：跑完了就没有可续的东西，续传上下文作废禁复活。"""
    p = Aria2Plugin()
    dest = "/tmp/race-does-not-exist"
    p._track(dest, "http://x/f", dest, True, MagicMock())
    assert await p.pause(dest) is True
    done = DownloadProgress(url="http://x/f", dest=dest, status="done")
    p._untrack(dest, done)
    assert await p.resume_download(dest) is False


# ─── 全插件诚实拒绝电池（修前假承诺的硬反证面） ─────────────────────

ALL_PLUGIN_CLASSES = [
    Aria2Plugin,
    WgetPlugin,
    CurlPlugin,
    CurlResumePlugin,
    AxelPlugin,
    OdlPlugin,
    RsyncPlugin,
]


@pytest.mark.parametrize("plugin_cls", ALL_PLUGIN_CLASSES)
@pytest.mark.asyncio
async def test_unknown_task_battery_all_honest_refusal(plugin_cls):
    """无登记任务一律如实 False——修前 resume_download 恒 True（6 插件）、
    Odl pause/cancel 恒 True（纯常量零操作）正是被本电池判死的假承诺。"""
    p = plugin_cls()
    assert await p.pause("nope") is False
    assert await p.resume_download("nope") is False
    assert await p.cancel("nope") is False


@pytest.mark.asyncio
async def test_odl_pause_cancel_are_real_ops(fake_env):
    """Odl 修前 pause/cancel/pause_download/resume_download 全是 return True
    纯常量（连进程都不碰）——现 pause 真停传输、cancel 真杀。"""
    bin_dir, out_file, log_file = fake_env
    _make_fake_binary(bin_dir, "odl", out_file, log_file)
    p = OdlPlugin()
    dest = str(out_file)
    bg = asyncio.create_task(p.download("http://x/f", dest))
    try:
        await _wait_until(lambda: dest in p._running and log_file.exists())
        assert await p.pause(dest) is True
        progress = await bg
        assert progress.status == "paused"
        assert await p.resume_download(dest) is True
        await _wait_until(lambda: out_file.read_bytes() == b"partrest", timeout=10)
    finally:
        await p.cancel(dest)
        bg.cancel()


# ─── native 续传：单写者续跑（修前双跑损坏） ────────────────────────


class _TrickleServer:
    """本地 HTTP 服务：按块慢速吐已知 payload，让 pause 能落在传输中途。"""

    def __init__(self, payload: bytes, chunk: int = 1024, delay: float = 0.05):
        self.payload = payload
        self.chunk = chunk
        self.delay = delay
        self.get_requests = 0
        self._server = None
        self.port = 0

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            req = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        except Exception:
            writer.close()
            return
        method = req.split(b" ", 1)[0]
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nAccept-Ranges: bytes\r\n"
            b"Connection: close\r\n\r\n" % len(self.payload)
        )
        if method != b"HEAD":
            self.get_requests += 1
            for i in range(0, len(self.payload), self.chunk):
                writer.write(self.payload[i : i + self.chunk])
                await writer.drain()
                await asyncio.sleep(self.delay)
        await writer.drain()
        writer.close()


@pytest.mark.asyncio
async def test_native_pause_resume_single_writer_end_to_end(tmp_path):
    """真 HTTP 传输全周期：pause 落在中途→resume 续跑同一 worker→文件逐字节
    恰等于 payload 且服务端只见过 1 次 GET。修前 resume 无条件重开
    _run_download=双写者，内容必损坏/GET=2（双跑硬反证）。"""
    payload = b"0123456789abcdef" * 2560  # 40KB
    dest = tmp_path / "native.bin"
    dl = NativeDownloader()
    async with _TrickleServer(payload) as server:
        url = f"http://127.0.0.1:{server.port}/f"
        task = await dl.add_download(url, str(dest))
        try:
            await _wait_until(lambda: task.downloaded_bytes >= 2048, timeout=15)
            await dl.pause(task.id)
            assert task.status == DownloadStatus.PAUSED
            paused_bytes = dest.stat().st_size
            assert 0 < paused_bytes < len(payload)

            await dl.resume(task.id)
            assert task.status == DownloadStatus.DOWNLOADING
            await _wait_until(lambda: task.status == DownloadStatus.DONE, timeout=20)

            assert dest.read_bytes() == payload, "双写者/截断/重复——续传语义损坏"
            assert server.get_requests == 1, f"resume 不得重开传输，实际 GET={server.get_requests}"
        finally:
            await dl.remove(task.id)


@pytest.mark.asyncio
async def test_native_resume_does_not_respawn_alive_worker(tmp_path, monkeypatch):
    """worker 活着（正等暂停事件）时 resume 只清事件续跑——修前无条件重开
    _run_download（双跑根因）。"""
    dl = NativeDownloader()
    calls = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def stub(task):
        calls.append(task.id)
        task.status = DownloadStatus.DOWNLOADING
        started.set()
        await release.wait()

    monkeypatch.setattr(dl, "_run_download", stub)
    task = await dl.add_download("http://x/f", str(tmp_path / "f"))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await dl.pause(task.id)
        assert task.status == DownloadStatus.PAUSED
        await dl.resume(task.id)
        assert task.status == DownloadStatus.DOWNLOADING
        await asyncio.sleep(0.05)  # 让出事件循环：若误重开，stub 此刻已跑
        assert len(calls) == 1, "worker 活着时 resume 重开=双跑"
        assert task._pause_event is not None
        assert not task._pause_event.is_set()
    finally:
        release.set()


@pytest.mark.asyncio
async def test_native_resume_respawns_when_worker_gone(tmp_path, monkeypatch):
    """worker 已不在（异常退出等）时 resume 从半成品重跑——续传不悬挂。"""
    dl = NativeDownloader()
    calls = []

    async def stub(task):
        calls.append(1)

    monkeypatch.setattr(dl, "_run_download", stub)
    task = await dl.add_download("http://x/f", str(tmp_path / "f"))
    await asyncio.sleep(0.05)  # bg stub 立即完成并从 _running 摘除
    task.status = DownloadStatus.PAUSED
    assert task._pause_event is not None
    task._pause_event.set()
    await dl.resume(task.id)
    assert task.status == DownloadStatus.DOWNLOADING
    await asyncio.sleep(0.05)  # 重开的 bg 任务要让出一次才真正跑起来
    assert len(calls) == 2, "worker 不在时 resume 必须真重跑"
