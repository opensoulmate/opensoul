"""Unit tests for download_plugins.py — plugin system for pluggable download protocols."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.download_plugins import (
    Aria2Plugin,
    AxelPlugin,
    CurlPlugin,
    CurlResumePlugin,
    DownloadManager,
    DownloadProgress,
    OdlPlugin,
    PluginInfo,
    PluginStatus,
    WgetPlugin,
    _detect_os,
    _refresh_path,
    get_download_manager,
)

# ─── Data class tests ────────────────────────────────────────────


class TestPluginStatus:
    def test_all_values(self):
        assert PluginStatus.AVAILABLE.value == "available"
        assert PluginStatus.INSTALLING.value == "installing"
        assert PluginStatus.UPDATING.value == "updating"
        assert PluginStatus.ERROR.value == "error"
        assert PluginStatus.DISABLED.value == "disabled"

    def test_from_value(self):
        assert PluginStatus("available") == PluginStatus.AVAILABLE


class TestDownloadProgress:
    def test_default_values(self):
        p = DownloadProgress(url="http://x", dest="/tmp/f")
        assert p.total_bytes == 0
        assert p.downloaded_bytes == 0
        assert p.speed == 0
        assert p.eta == 0
        assert p.status == "pending"
        assert p.error is None
        assert p.supports_resume is False
        assert p.plugin == ""

    def test_progress_pct_zero_total(self):
        p = DownloadProgress(url="x", dest="y", total_bytes=0, downloaded_bytes=100)
        assert p.progress_pct == 0

    def test_progress_pct_half(self):
        p = DownloadProgress(url="x", dest="y", total_bytes=200, downloaded_bytes=100)
        assert p.progress_pct == 50.0

    def test_progress_pct_full(self):
        p = DownloadProgress(url="x", dest="y", total_bytes=100, downloaded_bytes=100)
        assert p.progress_pct == 100.0

    def test_progress_pct_over_100_capped(self):
        p = DownloadProgress(url="x", dest="y", total_bytes=100, downloaded_bytes=200)
        assert p.progress_pct == 100.0

    def test_progress_pct_negative_total(self):
        p = DownloadProgress(url="x", dest="y", total_bytes=-1, downloaded_bytes=50)
        assert p.progress_pct == 0


class TestPluginInfo:
    def test_fields(self):
        info = PluginInfo(
            id="test",
            name="Test",
            description="desc",
            version="1.0",
            binary="test",
            supports_resume=True,
            supports_p2p=False,
            install_cmd={"linux": "apt install test"},
            update_cmd={},
            check_version_cmd="test --version",
        )
        assert info.id == "test"
        assert info.supports_resume is True
        assert info.supports_p2p is False
        assert info.priority == 100  # default
        assert info.status == PluginStatus.AVAILABLE  # default


# ─── Helper function tests ────────────────────────────────────────


class TestDetectOs:
    @patch("platform.system", return_value="Linux")
    def test_linux(self, _):
        assert _detect_os() == "linux"

    @patch("platform.system", return_value="Darwin")
    def test_darwin(self, _):
        assert _detect_os() == "darwin"

    @patch("platform.system", return_value="Windows")
    def test_windows(self, _):
        assert _detect_os() == "windows"


class TestRefreshPath:
    def test_adds_local_bin(self):
        original = "/usr/bin"
        with patch.dict("os.environ", {"PATH": original}):
            _refresh_path()
            assert "~/.local/bin" in os.environ["PATH"] or ".local/bin" in os.environ["PATH"]


# ─── Plugin info tests ────────────────────────────────────────────


class TestAria2Plugin:
    def test_info(self):
        p = Aria2Plugin()
        info = p.get_info()
        assert info.id == "aria2"
        assert info.supports_resume is True
        assert info.supports_p2p is True
        assert info.priority == 10
        assert "linux" in info.install_cmd
        assert "darwin" in info.install_cmd
        assert "windows" in info.install_cmd

    def test_is_available(self):
        p = Aria2Plugin()
        with patch("shutil.which", return_value="/usr/bin/aria2c"):
            assert p.is_available() is True
        with patch("shutil.which", return_value=None):
            assert p.is_available() is False

    @pytest.mark.asyncio
    async def test_resume_download_no_task_is_honest_refusal(self):
        # 契约随更新（H1-②b-⑨a）：修前断言 resume_download("any") is True——
        # 锁死的正是假承诺（单行常量返回、什么都没续）。现：无 paused 任务如实 False。
        p = Aria2Plugin()
        assert await p.resume_download("any") is False

    @pytest.mark.asyncio
    async def test_pause_no_task(self):
        p = Aria2Plugin()
        assert await p.pause("nonexistent") is False

    @pytest.mark.asyncio
    async def test_cancel_no_task(self):
        p = Aria2Plugin()
        assert await p.cancel("nonexistent") is False


class TestWgetPlugin:
    def test_info(self):
        p = WgetPlugin()
        info = p.get_info()
        assert info.id == "wget"
        assert info.supports_resume is True
        assert info.supports_p2p is False
        assert info.priority == 50

    def test_is_available(self):
        p = WgetPlugin()
        with patch("shutil.which", return_value="/usr/bin/wget"):
            assert p.is_available() is True
        with patch("shutil.which", return_value=None):
            assert p.is_available() is False


class TestCurlPlugin:
    def test_info(self):
        p = CurlPlugin()
        info = p.get_info()
        assert info.id == "curl"
        assert info.supports_resume is False
        assert info.supports_p2p is False
        assert info.priority == 100

    def test_is_available(self):
        p = CurlPlugin()
        with patch("shutil.which", return_value="/usr/bin/curl"):
            assert p.is_available() is True

    @pytest.mark.asyncio
    async def test_resume_download_returns_false(self):
        p = CurlPlugin()
        assert await p.resume_download("any") is False


class TestCurlResumePlugin:
    def test_info(self):
        p = CurlResumePlugin()
        info = p.get_info()
        assert info.id == "curl-resume"
        assert info.supports_resume is True
        assert info.priority == 45

    @pytest.mark.asyncio
    async def test_resume_download_no_task_is_honest_refusal(self):
        # 契约随更新（H1-②b-⑨a）：修前断言 True=锁死假承诺，现无 paused 任务如实 False。
        p = CurlResumePlugin()
        assert await p.resume_download("any") is False


class TestAxelPlugin:
    def test_info(self):
        p = AxelPlugin()
        info = p.get_info()
        assert info.id == "axel"
        assert info.supports_resume is True
        assert info.supports_p2p is False
        assert info.priority == 20


class TestOdlPlugin:
    def test_info(self):
        p = OdlPlugin()
        info = p.get_info()
        assert info.id == "odl"
        assert info.supports_resume is True
        assert info.supports_p2p is False
        assert info.priority == 5  # highest priority (native engine)


# ─── DownloadManager tests ────────────────────────────────────────


class TestDownloadManager:
    def test_register_and_list(self):
        mgr = DownloadManager()
        plugins = mgr.list_plugins()
        # Should have built-in plugins registered
        ids = [p.id for p in plugins]
        assert "aria2" in ids
        assert "wget" in ids
        assert "curl" in ids
        assert "curl-resume" in ids
        assert "axel" in ids
        assert "odl" in ids

    def test_get_plugin(self):
        mgr = DownloadManager()
        assert mgr.get_plugin("curl") is not None
        assert mgr.get_plugin("nonexistent") is None

    def test_unregister(self):
        mgr = DownloadManager()
        mgr.unregister("curl")
        assert mgr.get_plugin("curl") is None

    def test_get_best_plugin_prefers_lower_priority(self):
        mgr = DownloadManager()
        with patch("shutil.which", return_value="/usr/bin/something"):
            best = mgr.get_best_plugin(require_resume=False)
            # Should return the lowest priority number available
            assert best is not None
            info = best.get_info()
            # aria2 has priority 10, so it should be first
            assert info.priority <= 50

    def test_get_best_plugin_require_resume(self):
        mgr = DownloadManager()
        with patch("shutil.which", return_value="/usr/bin/something"):
            best = mgr.get_best_plugin(require_resume=True)
            if best:
                assert best.get_info().supports_resume is True

    def test_get_best_plugin_require_p2p(self):
        mgr = DownloadManager()
        with patch("shutil.which", return_value="/usr/bin/something"):
            best = mgr.get_best_plugin(require_p2p=True)
            if best:
                assert best.get_info().supports_p2p is True

    @pytest.mark.asyncio
    async def test_download_nonexistent_plugin(self):
        mgr = DownloadManager()
        result = await mgr.download("http://x", "/tmp/f", plugin_id="nonexistent")
        assert result.status == "error"
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_auto_update_no_plugins(self):
        mgr = DownloadManager()
        with patch("shutil.which", return_value=None):
            results = await mgr.auto_update_plugins()
            # No plugins available, so all should be skipped
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_install_nonexistent_plugin(self):
        mgr = DownloadManager()
        assert await mgr.install_plugin("nonexistent") is False

    def test_singleton(self):
        mgr1 = get_download_manager()
        mgr2 = get_download_manager()
        assert mgr1 is mgr2


# ─── Plugin install timeout test ─────────────────────────────────


class TestPluginInstall:
    @pytest.mark.asyncio
    async def test_install_timeout(self):
        """Test that install times out after 60s."""
        p = CurlPlugin()
        with patch("asyncio.create_subprocess_shell") as mock_shell:
            mock_proc = MagicMock()
            mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError)
            mock_proc.kill = MagicMock()
            mock_shell.return_value = mock_proc
            result = await p.install()
            assert result is False

    @pytest.mark.asyncio
    async def test_install_exception(self):
        """Test that install handles exceptions gracefully."""
        p = CurlPlugin()
        with patch("asyncio.create_subprocess_shell", side_effect=Exception("fail")):
            result = await p.install()
            assert result is False


import os
