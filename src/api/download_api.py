"""Download plugin management API.

Endpoints for managing download plugins, triggering downloads with resume/P2P,
and auto-updating plugins.
"""

import asyncio
import base64
import json
import logging
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.download_plugins import DownloadPlugin, DownloadProgress, get_download_manager
from src.api.native_downloader import get_downloader

router = APIRouter()


@router.get("/health")
async def download_api_health():
    """DownloadAPI health check."""
    return {"status": "ok", "component": "DownloadAPI"}


logger = logging.getLogger(__name__)

# Cache dir for downloaded packages
CACHE_DIR = Path.home() / ".openmate" / "download-cache"


# ─── Plugin Management ───────────────────────────────────────────


@router.get("/plugins")
async def list_plugins():
    """List all download plugins and their status"""
    dm = get_download_manager()
    plugins = dm.list_plugins()
    return {
        "plugins": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "version": p.version,
                "status": p.status.value,
                "supports_resume": p.supports_resume,
                "supports_p2p": p.supports_p2p,
                "priority": p.priority,
            }
            for p in plugins
        ]
    }


@router.post("/plugins/{plugin_id}/install")
async def install_plugin(plugin_id: str):
    """Install a download plugin"""
    dm = get_download_manager()
    success = await dm.install_plugin(plugin_id)
    if success:
        return {"success": True, "message": f"Plugin {plugin_id} installed"}
    raise HTTPException(status_code=400, detail=f"Failed to install plugin {plugin_id}")


@router.post("/plugins/{plugin_id}/update")
async def update_plugin(plugin_id: str):
    """Update a specific plugin"""
    dm = get_download_manager()
    plugin = dm.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    if not plugin.is_available():
        raise HTTPException(status_code=400, detail="Plugin not installed")
    success = await plugin.update()
    return {"success": success}


@router.post("/plugins/update-all")
async def update_all_plugins():
    """Auto-update all installed plugins"""
    dm = get_download_manager()
    results = await dm.auto_update_plugins()
    return {"results": results}


class BatchPluginRequest(BaseModel):
    action: str  # 'install' | 'uninstall' | 'update'
    plugin_ids: list[str]


@router.post("/plugins/batch")
async def batch_plugin_action(req: BatchPluginRequest):
    """Batch install/uninstall/update plugins"""
    dm = get_download_manager()
    results = {}

    for plugin_id in req.plugin_ids:
        plugin = dm.get_plugin(plugin_id)
        if not plugin:
            results[plugin_id] = {"success": False, "error": "Plugin not found"}
            continue

        try:
            if req.action == "install":
                if plugin.is_available():
                    results[plugin_id] = {"success": True, "message": "Already installed"}
                else:
                    success = await plugin.install()
                    results[plugin_id] = {"success": success}

            elif req.action == "update":
                if not plugin.is_available():
                    results[plugin_id] = {"success": False, "error": "Not installed"}
                else:
                    success = await plugin.update()
                    results[plugin_id] = {"success": success}

            elif req.action == "uninstall":
                if not plugin.is_available():
                    results[plugin_id] = {"success": True, "message": "Not installed"}
                else:
                    info = plugin.get_info()
                    import platform

                    os_name = "darwin" if platform.system() == "Darwin" else "linux"
                    # Construct uninstall command from binary name
                    if os_name == "darwin":
                        cmd = f"brew uninstall {info.binary} 2>/dev/null || true"
                    else:
                        cmd = f"sudo pacman -R --noconfirm {info.binary} 2>/dev/null || sudo apt remove -y {info.binary} 2>/dev/null || true"
                    proc = await asyncio.create_subprocess_shell(cmd)
                    await proc.wait()
                    results[plugin_id] = {"success": True, "message": "Uninstalled"}
            else:
                results[plugin_id] = {"success": False, "error": f"Unknown action: {req.action}"}
        except Exception as e:
            logger.error(f"Batch {req.action} failed for {plugin_id}: {e}")
            results[plugin_id] = {"success": False, "error": str(e)}

    return {"results": results}


@router.get("/plugins/{plugin_id}/version")
async def get_plugin_version(plugin_id: str):
    """Get installed version of a plugin"""
    dm = get_download_manager()
    plugin = dm.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    version = await plugin.get_version()
    return {"id": plugin_id, "version": version, "available": plugin.is_available()}


# ─── Download with Resume/P2P ────────────────────────────────────


class DownloadRequest(BaseModel):
    url: str
    dest: str | None = None  # defaults to cache dir
    resume: bool = True
    plugin_id: str | None = None  # auto-select if None（⑨b 起真执行，修前静默忽略）


NATIVE_ENGINE = "native"
AUTO_CHAIN = "auto"  # auto fallback 链 ack 的 plugin 键：真赢家链跑完才知道，禁自报引擎


def _task_token(registry_key: str) -> str:
    """URL 安全任务令牌——插件注册表身份键是 dest 路径（可含 '/'），path 段
    放不下（ASGI 会把 %2F 解码回斜杠）；base64url 令牌与注册表键一一对应，
    /tasks 列表与 /tasks/{token} 控制面同一寻址形。"""
    return base64.urlsafe_b64encode(registry_key.encode()).decode().rstrip("=")


def _resolve_dest(req: DownloadRequest) -> str:
    if req.dest:
        return req.dest
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    filename = req.url.split("/")[-1].split("?")[0] or "download"
    return str(CACHE_DIR / filename)


def _resolve_engine(req: DownloadRequest) -> tuple[str, "DownloadPlugin | None"]:
    """引擎单一解析口——plugin_id 真消费（修前 DownloadRequest.plugin_id 被静默
    忽略=字段写了没接线）。

    - 缺省 → get_best_plugin 能力择优（resume 需求随 req.resume），无可用插件
      如实回落 native
    - "native" → 显式强制内置引擎（既有调用方逃生口）
    - 指定插件 → 未知 404 / 未安装 400 如实拒（禁静默忽略、禁拿不可用插件硬跑）

    返回 (引擎标签, plugin)：plugin=None 表示 native 引擎。
    """
    dm = get_download_manager()
    if req.plugin_id is None:
        plugin = dm.get_best_plugin(require_resume=req.resume)
        if plugin is not None and plugin.is_available():
            return plugin.get_info().id, plugin
        return NATIVE_ENGINE, None
    if req.plugin_id == NATIVE_ENGINE:
        return NATIVE_ENGINE, None
    plugin = dm.get_plugin(req.plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"Plugin not found: {req.plugin_id}")
    if not plugin.is_available():
        raise HTTPException(status_code=400, detail=f"Plugin not installed: {req.plugin_id}")
    return plugin.get_info().id, plugin


# ─── 异步终态结果记录（H1-②b-⑮ 异步链终态查询面）────────────────────
# 修前（⑭ 留档边界）：异步 ack 后后台跑结果只有日志可查——插件任务收尾即从
# /tasks 在途面消失（完成即消失=既有 async 语义），链真赢家/失败真因无处查。
# 此处落进程内结果记录：ack 时建在途行、后台收尾 upsert 终态行，
# GET /download/results 查询。进程内（非持久化）是结构诚实而非偷懒：
# asyncio.create_task 的后台跑本体就活在进程内，重启=下载本身也没了，
# 记录若活过重启会对着已消失的下载谎报 "downloading"。
_ASYNC_RESULT_CAP = 200  # 结果行上限（按 ack 序淘汰最旧，测试可 monkeypatch）
_async_results: dict[str, dict] = {}
_async_result_order: deque[str] = deque()


def _record_async_result(
    task_id: str,
    url: str,
    dest: str,
    *,
    status: str,
    plugin: str | None,
    finished: bool,
    fallback_chain: list[str] | None = None,
    progress: DownloadProgress | None = None,
    error: str | None = None,
) -> dict:
    """结果行单一写口（ack 在途行与终态行同一键 upsert，禁两套记录）。

    字段全取自真实数据：plugin=真跑插件注册 id（链真赢家；链全败=None 禁
    fabricate）、status/error/字节数=DownloadProgress 原样、finished_at=None
    表示后台仍在跑（行形状 13 键恒定）。
    """
    now = time.time()
    row = _async_results.get(task_id)
    if row is None:
        row = {
            "task_id": task_id,
            "registry_key": dest,  # 插件层身份键（与 /tasks plugin_tasks 同源）
            "url": url,
            "dest": dest,
            "status": status,
            "plugin": plugin,
            "error": error,
            "fallback_chain": fallback_chain,
            "total_bytes": 0,
            "downloaded_bytes": 0,
            "progress_pct": 0,
            "started_at": now,
            "finished_at": None,
        }
        _async_results[task_id] = row
        _async_result_order.append(task_id)
        while len(_async_result_order) > _ASYNC_RESULT_CAP:
            _async_results.pop(_async_result_order.popleft(), None)
    row["status"] = status
    row["plugin"] = plugin
    row["error"] = error
    if finished:
        row["finished_at"] = now
    if progress is not None:
        row["total_bytes"] = progress.total_bytes
        row["downloaded_bytes"] = progress.downloaded_bytes
        row["progress_pct"] = progress.progress_pct
    return row


async def _run_auto_chain(url: str, dest: str, resume: bool, task_id: str) -> None:
    """auto 后台 fallback 链（与 /download/sync 同一 dm.download 单一源禁两套链）。

    异步 ack 即刻返回无结果可报，链结果可见性=终态结果行（GET /download/results
    真赢家/失败真因可查）+日志（成功 info 带真赢家注册 id、失败 warning 带真因）
    + /tasks 在途面——禁静默吞链结果（F2b-② 纪律）。
    """
    dm = get_download_manager()
    try:
        progress = await dm.download(url, dest, resume=resume)
    except Exception as e:
        # 后台链故障也必须留痕（禁静默：既无响应体也无 /tasks 行可看）
        logger.warning(f"Async fallback chain crashed for {url}: {e}")
        _record_async_result(
            task_id, url, dest, status="error", plugin=None, finished=True, error=str(e)
        )
        return
    _record_async_result(
        task_id,
        url,
        dest,
        status=progress.status,
        plugin=progress.plugin or None,
        finished=True,
        progress=progress,
        error=progress.error,
    )
    if progress.status == "error":
        logger.warning(f"Async fallback chain failed for {url}: {progress.error}")
    else:
        logger.info(
            f"Async fallback chain finished ({progress.status}, plugin={progress.plugin}): {url}"
        )


async def _run_plugin_download(
    plugin: DownloadPlugin, url: str, dest: str, resume: bool, task_id: str, engine: str
) -> None:
    """显式插件后台跑收尾记录（与 auto 链同一结果面，⑭ 对称语义）。"""
    try:
        progress = await plugin.download(url, dest, resume=resume)
    except Exception as e:
        logger.warning(f"Async plugin download crashed ({engine}) for {url}: {e}")
        _record_async_result(
            task_id, url, dest, status="error", plugin=engine, finished=True, error=str(e)
        )
        return
    _record_async_result(
        task_id,
        url,
        dest,
        status=progress.status,
        plugin=progress.plugin or engine,
        finished=True,
        progress=progress,
        error=progress.error,
    )


@router.post("/download")
async def start_download(req: DownloadRequest):
    """Start a background download.

    引擎随 _resolve_engine 解析（plugin_id 真消费/get_best_plugin 择优）；
    plugin 引擎的 task_id=注册表键（dest）的 URL 安全令牌（与 /tasks/{token}
    控制面同一寻址形）。auto（plugin_id 缺省）=跨插件 fallback 链后台跑
    （H1-②b-⑭ 链接入异步面）：ack 时真赢家未定，plugin 如实标 "auto" 禁自报
    引擎，链序 fallback_chain 可观测（_candidates 单一源），实际引擎真源=
    /tasks plugin_tasks；显式 plugin_id/"native" 单引擎契约不变。
    """
    dest = _resolve_dest(req)
    engine, plugin = _resolve_engine(req)
    if plugin is None:
        dl = get_downloader()
        task = await dl.add_download(req.url, dest)
        return {
            "task_id": task.id,
            "dest": dest,
            "url": req.url,
            "status": task.status.value,
            "plugin": NATIVE_ENGINE,
        }
    task_id = _task_token(dest)
    if req.plugin_id is None:
        dm = get_download_manager()
        chain = dm.chain_order(require_resume=req.resume)
        _record_async_result(
            task_id,
            req.url,
            dest,
            status="downloading",
            plugin=AUTO_CHAIN,
            finished=False,
            fallback_chain=chain,
        )
        asyncio.create_task(_run_auto_chain(req.url, dest, req.resume, task_id))
        return {
            "task_id": task_id,
            "dest": dest,
            "url": req.url,
            "status": "downloading",
            "plugin": AUTO_CHAIN,
            "fallback_chain": chain,
        }
    _record_async_result(
        task_id, req.url, dest, status="downloading", plugin=engine, finished=False
    )
    asyncio.create_task(_run_plugin_download(plugin, req.url, dest, req.resume, task_id, engine))
    return {
        "task_id": task_id,
        "dest": dest,
        "url": req.url,
        "status": "downloading",
        "plugin": engine,
    }


@router.post("/download/sync")
async def download_sync(req: DownloadRequest):
    """Download to completion（native 多段引擎或插件引擎，引擎同 _resolve_engine）。

    auto（plugin_id 缺省）=跨插件 fallback 链（DownloadManager.download 首个
    生产消费点，修前全仓零消费=wired-but-not-functional）：链首=择优同源，
    真失败掉下一级，响应 plugin=真赢家注册 id 如实标注；显式 plugin_id/
    "native" 单引擎直跑零 fallback（⑨b 契约不变）。
    """
    dest = _resolve_dest(req)
    engine, plugin = _resolve_engine(req)
    if plugin is None:
        dl = get_downloader()
        task = await dl.download_sync(req.url, dest)
        return {
            "status": task.status.value,
            "dest": task.dest,
            "url": task.url,
            "total_bytes": task.total_bytes,
            "downloaded_bytes": task.downloaded_bytes,
            "speed": task.speed,
            "eta": task.eta,
            "progress_pct": task.progress_pct,
            "plugin": task.plugin,
            "supports_resume": task.supports_resume,
            "error": task.error,
            "task_id": task.id,
        }
    if req.plugin_id is not None:
        progress = await plugin.download(req.url, dest, resume=req.resume)
    else:
        dm = get_download_manager()
        progress = await dm.download(req.url, dest, resume=req.resume)
    return {
        "status": progress.status,
        "dest": progress.dest,
        "url": progress.url,
        "total_bytes": progress.total_bytes,
        "downloaded_bytes": progress.downloaded_bytes,
        "speed": progress.speed,
        "eta": progress.eta,
        "progress_pct": progress.progress_pct,
        "plugin": progress.plugin or engine,
        "supports_resume": progress.supports_resume,
        "error": progress.error,
        "task_id": _task_token(dest),
    }


@router.get("/download/progress")
async def download_progress_stream():
    """SSE stream for download progress (all active downloads)"""
    dl = get_downloader()

    async def stream():
        while True:
            await asyncio.sleep(1)
            tasks = [
                t.to_dict()
                for t in dl.list_tasks()
                if t.status.value in ("downloading", "connecting", "pending")
            ]
            if tasks:
                yield f"data: {json.dumps({'tasks': tasks})}\n\n"
            else:
                yield f"data: {json.dumps({'tasks': [], 'status': 'idle'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ─── 异步终态结果面（H1-②b-⑮）─────────────────────────────────────


def _resolve_result(task_id: str) -> dict | None:
    """token/注册表键双认寻址（_plugin_task 同款）：精确键直查 → raw 注册表键
    （dest）再令牌化 → base64url 令牌解码回键再令牌化。未记录如实 None（禁
    fabricate）。"""
    hit = _async_results.get(task_id)
    if hit is not None:
        return hit
    candidates = [_task_token(task_id)]
    pad = "=" * (-len(task_id) % 4)
    try:
        key = base64.urlsafe_b64decode(task_id + pad).decode()
        candidates.append(_task_token(key))
    except Exception:
        pass
    for cand in candidates:
        hit = _async_results.get(cand)
        if hit is not None:
            return hit
    return None


@router.get("/results")
async def list_download_results():
    """异步下载结果列表（ack 序最新在前）——POST /download 后台跑的终态查询面。

    在途行 finished_at=null（后台仍在跑）；终态行带真赢家 plugin（链全败=null
    禁 fabricate）/失败真因 error。native 引擎任务不在此面（其注册表保留 done
    行，/tasks 持久可见，不重复记录）。
    """
    rows = [_async_results[tid] for tid in reversed(_async_result_order) if tid in _async_results]
    return {"results": rows, "total": len(rows)}


@router.get("/results/{task_id}")
async def get_download_result(task_id: str):
    """单任务结果（token 或注册表键双认寻址）；未记录 404 如实拒。"""
    row = _resolve_result(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no async result record: {task_id}")
    return row


# ─── Cache Management ────────────────────────────────────────────


@router.get("/cache")
async def list_cache():
    """List cached downloads"""
    if not CACHE_DIR.exists():
        return {"files": [], "total_size": 0}

    files = []
    total_size = 0
    for f in CACHE_DIR.iterdir():
        if f.is_file() and not f.name.endswith(".aria2"):
            size = f.stat().st_size
            total_size += size
            files.append(
                {
                    "name": f.name,
                    "path": str(f),
                    "size": size,
                    "modified": f.stat().st_mtime,
                }
            )

    return {
        "files": sorted(files, key=lambda x: x["modified"], reverse=True),
        "total_size": total_size,
    }


@router.delete("/cache")
async def clear_cache():
    """Clear download cache"""
    import shutil

    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return {"success": True}


@router.delete("/cache/{filename}")
async def delete_cache_file(filename: str):
    """Delete a specific cached file"""
    filepath = CACHE_DIR / filename
    if filepath.exists():
        filepath.unlink()
        return {"success": True}
    raise HTTPException(status_code=404, detail="File not found")


# ─── Task Management ────────────────────────────────────────────


@router.get("/tasks")
async def list_download_tasks():
    """List all download tasks（native tasks + additive plugin_tasks 两真源如实分列）"""
    dl = get_downloader()
    dm = get_download_manager()
    return {
        "tasks": [t.to_dict() for t in dl.list_tasks()],
        # task_id=URL 安全令牌（可直接拼 /tasks/{task_id}/pause）+ registry_key=插件层身份键
        "plugin_tasks": [
            {**row, "registry_key": row["task_id"], "task_id": _task_token(row["task_id"])}
            for row in dm.plugin_tasks()
        ],
    }


def _plugin_task(task_id: str) -> tuple[DownloadPlugin, str] | None:
    """token/注册表键双认寻址：精确键直查 → base64url 令牌解码回注册表键再查。

    返回 (plugin, 注册表键)——pause/resume/cancel 的插件层身份键是 dest 路径，
    与线上寻址的令牌不同形，翻译只此一处。
    """
    dm = get_download_manager()
    candidates = [task_id]
    pad = "=" * (-len(task_id) % 4)
    try:
        candidates.append(base64.urlsafe_b64decode(task_id + pad).decode())
    except Exception:
        pass
    for key in candidates:
        hit = dm.find_task(key)
        if hit is not None:
            plugin, _state = hit
            return plugin, key
    return None


@router.post("/tasks/{task_id}/pause")
async def pause_download(task_id: str):
    """Pause a download（native/插件双引擎真路由；未知任务 404 如实拒——
    修前对未知 task_id 也回 {"status":"paused"} 假成功）"""
    dl = get_downloader()
    task = dl.get_task(task_id)
    if task is not None:
        await dl.pause(task_id)
        # 如实回报引擎真实结果（done/pending 任务 pause 不生效=状态原样，
        # 伪造 "paused" 假成功，F4b-⑲ 同型）
        return {"status": task.status.value, "task_id": task_id, "plugin": NATIVE_ENGINE}
    found = _plugin_task(task_id)
    if found is not None:
        plugin, key = found
        if await plugin.pause(key):
            return {"status": "paused", "task_id": task_id, "plugin": plugin.get_info().id}
        raise HTTPException(
            status_code=409, detail=f"task exists but pause did not take effect: {task_id}"
        )
    raise HTTPException(status_code=404, detail=f"task not found: {task_id}")


@router.post("/tasks/{task_id}/resume")
async def resume_download(task_id: str):
    """Resume a paused download（同 pause：真路由+未知 404 如实拒——修前无条件回
    {"status":"resumed"} 假成功）"""
    dl = get_downloader()
    task = dl.get_task(task_id)
    if task is not None:
        await dl.resume(task_id)
        return {"status": task.status.value, "task_id": task_id, "plugin": NATIVE_ENGINE}
    found = _plugin_task(task_id)
    if found is not None:
        plugin, key = found
        if await plugin.resume_download(key):
            return {"status": "resumed", "task_id": task_id, "plugin": plugin.get_info().id}
        raise HTTPException(
            status_code=409,
            detail=f"task exists but resume was refused (no paused context / unsupported): {task_id}",
        )
    raise HTTPException(status_code=404, detail=f"task not found: {task_id}")


@router.delete("/tasks/{task_id}")
async def cancel_download(task_id: str):
    """Cancel and remove a download task（真路由+未知 404 如实拒——修前无条件回
    {"status":"removed"} 假成功）"""
    dl = get_downloader()
    task = dl.get_task(task_id)
    if task is not None:
        await dl.remove(task_id)
        return {"status": "removed", "task_id": task_id, "plugin": NATIVE_ENGINE}
    found = _plugin_task(task_id)
    if found is not None:
        plugin, key = found
        if await plugin.cancel(key):
            return {"status": "removed", "task_id": task_id, "plugin": plugin.get_info().id}
        raise HTTPException(
            status_code=409, detail=f"task exists but cancel did not take effect: {task_id}"
        )
    raise HTTPException(status_code=404, detail=f"task not found: {task_id}")


# ─── OpenWing Config ──────────────────────────────────────────


@router.get("/config")
async def get_config():
    """Get OpenWing engine configuration"""
    import subprocess

    try:
        proc = subprocess.run(
            ["openwing", "config", "list"], capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0:
            import json

            return json.loads(proc.stdout.strip())
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)
    return {"threads": 8, "speed_limit": 0, "download_dir": "~/Downloads", "proxy": None}


@router.post("/config")
async def set_config(body: dict):
    """Set OpenWing engine configuration"""
    import subprocess

    key = body.get("key", "")
    value = body.get("value", "")
    try:
        proc = subprocess.run(
            ["openwing", "config", "set", key, str(value)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return {"status": "ok", "key": key, "value": value}
        return {"status": "error", "message": proc.stderr.strip()}
    except Exception as e:
        return {"status": "error", "message": str(e)}
