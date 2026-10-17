"""Native Python download engine - no external binaries needed.

Features: multi-segment parallel download, resume, progress tracking.
Like Xunlei/Thunder but pure Python.
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

CHUNK_SIZE = 8192
DEFAULT_SEGMENTS = 4
MAX_SEGMENTS = 16
SEGMENT_MIN_SIZE = 1024 * 1024  # 1MB minimum per segment


class DownloadStatus(StrEnum):
    PENDING = "pending"
    CONNECTING = "connecting"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class DownloadTask:
    """A download task with multi-segment support"""

    id: str
    url: str
    dest: str
    status: DownloadStatus = DownloadStatus.PENDING
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed: float = 0  # bytes/sec
    eta: int = 0  # seconds
    segments: int = DEFAULT_SEGMENTS
    supports_resume: bool = False
    error: str | None = None
    plugin: str = "native"
    added_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    _cancel_event: asyncio.Event | None = field(default=None, repr=False)
    _pause_event: asyncio.Event | None = field(default=None, repr=False)

    @property
    def progress_pct(self) -> float:
        if self.total_bytes <= 0:
            return 0
        return min(100, round(self.downloaded_bytes / self.total_bytes * 100, 1))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "dest": self.dest,
            "status": self.status.value,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "speed": self.speed,
            "eta": self.eta,
            "progress_pct": self.progress_pct,
            "segments": self.segments,
            "supports_resume": self.supports_resume,
            "error": self.error,
            "plugin": self.plugin,
            "added_at": self.added_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class NativeDownloader:
    """Pure Python download engine with multi-segment + resume support."""

    def __init__(self, max_concurrent: int = 3):
        self._tasks: dict[str, DownloadTask] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def list_tasks(self) -> list[DownloadTask]:
        return list(self._tasks.values())

    def get_task(self, task_id: str) -> DownloadTask | None:
        return self._tasks.get(task_id)

    async def add_download(
        self, url: str, dest: str, segments: int = DEFAULT_SEGMENTS
    ) -> DownloadTask:
        """Add a new download task."""
        task_id = hashlib.md5(f"{url}:{dest}".encode()).hexdigest()[:12]

        # If task already exists and is done/error, allow re-download
        if task_id in self._tasks:
            existing = self._tasks[task_id]
            if existing.status in (
                DownloadStatus.DONE,
                DownloadStatus.ERROR,
                DownloadStatus.CANCELLED,
            ):
                pass  # allow re-download
            elif existing.status == DownloadStatus.PAUSED:
                await self.resume(task_id)
                return existing
            else:
                return existing

        task = DownloadTask(
            id=task_id,
            url=url,
            dest=dest,
            segments=min(segments, MAX_SEGMENTS),
            _cancel_event=asyncio.Event(),
            _pause_event=asyncio.Event(),
        )
        self._tasks[task_id] = task

        # Start download in background
        bg_task = asyncio.create_task(self._run_download(task))
        self._running[task_id] = bg_task
        bg_task.add_done_callback(lambda _: self._running.pop(task_id, None))

        return task

    async def pause(self, task_id: str):
        task = self._tasks.get(task_id)
        if task and task.status == DownloadStatus.DOWNLOADING:
            task.status = DownloadStatus.PAUSED
            task._pause_event.set()

    async def resume(self, task_id: str):
        task = self._tasks.get(task_id)
        if task and task.status == DownloadStatus.PAUSED:
            task._pause_event.clear()
            task.status = DownloadStatus.DOWNLOADING
            if task_id in self._running:
                # 原 worker 还活着：它正等在 _pause_event 上，清事件即续跑同一条
                # 传输。修前无条件重开 _run_download = 两个写者同时写同一 dest
                # （双跑损坏——续传面真缺陷，H1-②b-⑨a 收口）。
                return
            # worker 已不在（异常退出等）：从半成品重跑
            bg_task = asyncio.create_task(self._run_download(task))
            self._running[task_id] = bg_task
            bg_task.add_done_callback(lambda _: self._running.pop(task_id, None))

    async def cancel(self, task_id: str):
        task = self._tasks.get(task_id)
        if task:
            task._cancel_event.set()
            task.status = DownloadStatus.CANCELLED
            if task_id in self._running:
                self._running[task_id].cancel()

    async def remove(self, task_id: str):
        await self.cancel(task_id)
        self._tasks.pop(task_id, None)

    async def _run_download(self, task: DownloadTask):
        """Main download logic with multi-segment support."""
        async with self._semaphore:
            task.status = DownloadStatus.CONNECTING
            task.started_at = time.time()

            try:
                dest_path = Path(task.dest)
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(30, connect=10),
                    follow_redirects=True,
                    limits=httpx.Limits(max_connections=task.segments + 5),
                ) as client:
                    # Step 1: HEAD request to check file size and resume support
                    try:
                        head_resp = await client.head(task.url)
                        task.total_bytes = int(head_resp.headers.get("content-length", 0))
                        accept_ranges = head_resp.headers.get("accept-ranges", "")
                        task.supports_resume = accept_ranges == "bytes" or task.total_bytes > 0
                    except Exception:
                        # Some servers don't support HEAD, try GET with range
                        task.supports_resume = True

                    # Step 2: Check existing partial file
                    existing_size = 0
                    if dest_path.exists() and task.supports_resume:
                        existing_size = dest_path.stat().st_size
                        if task.total_bytes > 0 and existing_size >= task.total_bytes:
                            task.status = DownloadStatus.DONE
                            task.downloaded_bytes = task.total_bytes
                            task.completed_at = time.time()
                            return
                        task.downloaded_bytes = existing_size

                    # Step 3: Determine segments
                    if task.total_bytes <= 0 or task.total_bytes < SEGMENT_MIN_SIZE * 2:
                        # Small or unknown size: single segment
                        await self._download_single(client, task, existing_size)
                    else:
                        # Multi-segment download
                        await self._download_multi(client, task, existing_size)

            except httpx.ConnectError as e:
                task.status = DownloadStatus.ERROR
                task.error = f"连接失败: {str(e)}"
            except httpx.TimeoutException:
                task.status = DownloadStatus.ERROR
                task.error = "连接超时"
            except asyncio.CancelledError:
                task.status = DownloadStatus.CANCELLED
            except Exception as e:
                task.status = DownloadStatus.ERROR
                task.error = f"下载失败: {str(e)}"
                logger.error(f"Download error for {task.url}: {e}", exc_info=True)

    async def _download_single(
        self, client: httpx.AsyncClient, task: DownloadTask, resume_from: int = 0
    ):
        """Single-segment download (for small files or when resume not supported)."""
        task.status = DownloadStatus.DOWNLOADING
        headers = {}
        mode = "wb"
        if resume_from > 0 and task.supports_resume:
            headers["Range"] = f"bytes={resume_from}-"
            mode = "ab"

        speed_samples = []
        last_time = time.time()
        last_bytes = task.downloaded_bytes

        async with client.stream("GET", task.url, headers=headers) as resp:
            if resp.status_code not in (200, 206):
                task.status = DownloadStatus.ERROR
                task.error = f"HTTP {resp.status_code}"
                return

            if task.total_bytes == 0:
                cl = resp.headers.get("content-length")
                if cl:
                    task.total_bytes = int(cl) + resume_from

            with open(task.dest, mode) as f:
                async for chunk in resp.aiter_bytes(CHUNK_SIZE):
                    if task._cancel_event and task._cancel_event.is_set():
                        return
                    if task._pause_event and task._pause_event.is_set():
                        # Wait until unpaused
                        while task._pause_event.is_set():
                            await asyncio.sleep(0.5)
                            if task._cancel_event and task._cancel_event.is_set():
                                return

                    f.write(chunk)
                    task.downloaded_bytes += len(chunk)

                    # Speed calculation
                    now = time.time()
                    elapsed = now - last_time
                    if elapsed >= 0.5:
                        speed = (task.downloaded_bytes - last_bytes) / elapsed
                        speed_samples.append(speed)
                        if len(speed_samples) > 5:
                            speed_samples.pop(0)
                        task.speed = sum(speed_samples) / len(speed_samples)
                        if task.speed > 0 and task.total_bytes > 0:
                            task.eta = int((task.total_bytes - task.downloaded_bytes) / task.speed)
                        last_time = now
                        last_bytes = task.downloaded_bytes

        task.status = DownloadStatus.DONE
        task.completed_at = time.time()
        task.speed = 0
        task.eta = 0

    async def _download_multi(
        self, client: httpx.AsyncClient, task: DownloadTask, resume_from: int = 0
    ):
        """Multi-segment parallel download."""
        task.status = DownloadStatus.DOWNLOADING
        remaining = task.total_bytes - resume_from
        seg_size = remaining // task.segments
        segments = []

        for i in range(task.segments):
            start = resume_from + i * seg_size
            end = start + seg_size - 1 if i < task.segments - 1 else task.total_bytes - 1
            segments.append((start, end))

        # Create temp files for each segment
        temp_dir = Path(task.dest).parent / f".{Path(task.dest).name}.parts"
        temp_dir.mkdir(parents=True, exist_ok=True)

        time.time()

        async def download_segment(idx: int, start: int, end: int):
            temp_file = temp_dir / f"part_{idx}"
            existing_part_size = temp_file.stat().st_size if temp_file.exists() else 0

            if existing_part_size >= (end - start + 1):
                return  # segment already complete

            actual_start = start + existing_part_size
            if actual_start > end:
                return

            headers = {"Range": f"bytes={actual_start}-{end}"}
            mode = "ab" if existing_part_size > 0 else "wb"

            async with client.stream("GET", task.url, headers=headers) as resp:
                if resp.status_code not in (200, 206):
                    raise Exception(f"Segment {idx}: HTTP {resp.status_code}")

                with open(temp_file, mode) as f:
                    async for chunk in resp.aiter_bytes(CHUNK_SIZE):
                        if task._cancel_event and task._cancel_event.is_set():
                            return
                        while task._pause_event and task._pause_event.is_set():
                            await asyncio.sleep(0.5)
                            if task._cancel_event and task._cancel_event.is_set():
                                return

                        f.write(chunk)
                        task.downloaded_bytes += len(chunk)

        try:
            # Download all segments in parallel
            await asyncio.gather(*[download_segment(i, s, e) for i, (s, e) in enumerate(segments)])

            # Merge segments into final file
            with open(task.dest, "wb" if resume_from == 0 else "r+b") as dest_f:
                if resume_from > 0:
                    dest_f.seek(resume_from)
                for i in range(len(segments)):
                    temp_file = temp_dir / f"part_{i}"
                    if temp_file.exists():
                        with open(temp_file, "rb") as pf:
                            while True:
                                data = pf.read(CHUNK_SIZE * 16)
                                if not data:
                                    break
                                dest_f.write(data)

            # Cleanup temp files
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)

            task.status = DownloadStatus.DONE
            task.completed_at = time.time()
            task.speed = 0
            task.eta = 0

        except Exception as e:
            logger.error(f"Multi-segment download error: {e}")
            raise

    async def download_sync(
        self, url: str, dest: str, segments: int = DEFAULT_SEGMENTS
    ) -> DownloadTask:
        """Synchronous download - wait for completion and return task."""
        task = await self.add_download(url, dest, segments)

        # Wait for completion
        max_wait = 300  # 5 minutes max
        start = time.time()
        while task.status in (
            DownloadStatus.PENDING,
            DownloadStatus.CONNECTING,
            DownloadStatus.DOWNLOADING,
        ):
            await asyncio.sleep(0.5)
            if time.time() - start > max_wait:
                task.status = DownloadStatus.ERROR
                task.error = "下载超时(5分钟)"
                break

        return task


# Singleton
_downloader: NativeDownloader | None = None


def get_downloader() -> NativeDownloader:
    global _downloader
    if _downloader is None:
        _downloader = NativeDownloader()
    return _downloader
