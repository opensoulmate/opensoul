"""WebSocket terminal endpoint for OpenMate."""

import asyncio
import fcntl
import json
import logging
import os
import signal
import struct
import termios

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from src.services.tenant_binding import bind_identity

router = APIRouter()
logger = logging.getLogger(__name__)


def _ws_presented_token(websocket: WebSocket):
    """凭据取参双源（F3b-④ / 1c-⑤ 同款）：?token= query 主口（浏览器 WS 设不了
    头，OpenMate 终端面板现状即带 token）+ Authorization: Bearer 头兜底
    （非浏览器客户端）。"""
    token = (websocket.query_params.get("token") or "").strip()
    if token:
        return token
    auth = websocket.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def _terminal_identity(websocket: WebSocket):
    """DEC-⑭-① 握手凭据必持（fail-closed，DEC-⑬ 同款「无证可验=拒」）：

    token 必持且须为有效活跃用户凭据（bind_identity 单一派生口，F3b-② 家族
    禁第二套判法）——PTY shell 是执行面，无证/坏证/停用身份一律不给 shell。
    返回 BoundIdentity 或 None（None=拒绝）。
    """
    token = _ws_presented_token(websocket)
    if not token:
        return None
    try:
        identity = bind_identity(f"Bearer {token}")
    except HTTPException:
        return None
    return identity if identity.authed else None


@router.get("/health")
async def terminal_ws_health():
    """TerminalWS health check."""
    return {"status": "ok", "component": "TerminalWS"}


@router.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket):
    """WebSocket terminal - spawns a PTY shell.

    DEC-⑭-① 身份轴收口（候选「/ws/{user_id} 等其余长连接面身份轴」拆件首件）：
    握手凭据必持（fail-closed）——修前零鉴权，任意网络可达方真 spawn bash
    （live 修前探针无 token SHELL_OK=RCE 面硬证）。凭据=有效活跃用户 JWT
    （_ws_presented_token 双源 → bind_identity 单一派生口），无证/坏证/停用
    身份=握手拒绝（accept 前 close 1008，F3b-④ 同语义），无证不给 shell。
    """
    identity = _terminal_identity(websocket)
    if identity is None:
        logger.warning("Terminal WS rejected: missing/invalid/inactive credentials")
        # 握手拒绝（accept 前 close）：拿不到通道、不 spawn 任何进程
        await websocket.close(code=1008, reason="terminal token required")
        return
    logger.info("Terminal WS connected: user=%s", identity.user_id)
    await websocket.accept()

    # Fork a child attached to a fresh PTY and exec a login bash shell.
    # os.forkpty() returns (0, slave_fd) in the child and (pid, master_fd) in the
    # parent. The previous code used pty.openpty() — which never forks and returns
    # (master_fd, slave_fd) with master_fd != 0 — so `if child_pid == 0` was dead
    # code and no shell was ever spawned (the terminal opened a pty pair with
    # nothing driving it). Branching on forkpty's child return (0) makes the exec
    # live and attaches bash to the slave side.
    child_pid, fd = os.forkpty()
    if child_pid == 0:
        # Child process: replace this process with a login bash on the PTY.
        try:
            os.execvp("/bin/bash", ["/bin/bash", "-l"])
        finally:
            os._exit(1)  # only reached if exec fails

    # Parent process. Drive the master fd without blocking the event loop: a
    # blocking os.read() would freeze the loop so read_ws() could never deliver
    # keystrokes and the terminal would deadlock. Non-blocking + short poll keeps
    # both pumps concurrent.
    os.set_blocking(fd, False)

    async def read_pty():
        """Read from PTY master and send to WebSocket."""
        try:
            while True:
                await asyncio.sleep(0.02)
                try:
                    data = os.read(fd, 4096)
                except BlockingIOError:
                    continue  # no output yet, keep polling
                except OSError:
                    break  # master closed / shell gone
                if data:
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
                else:
                    break  # EOF: child exited
        except Exception as exc:
            logging.getLogger(__name__).debug("terminal read skipped: %s", exc)

    async def read_ws():
        """Read from WebSocket and write to PTY master."""
        try:
            while True:
                msg = await websocket.receive_text()
                data = json.loads(msg)
                if data.get("type") == "input":
                    try:
                        os.write(fd, data["data"].encode("utf-8"))
                    except (BlockingIOError, OSError):
                        pass
                elif data.get("type") == "resize":
                    cols = data.get("cols", 80)
                    rows = data.get("rows", 24)
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    try:
                        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
                    except OSError:
                        pass
        except (WebSocketDisconnect, Exception):
            pass

    t_pty = asyncio.create_task(read_pty())
    t_ws = asyncio.create_task(read_ws())
    try:
        # When either pump ends (shell exit or client disconnect), cancel the other
        # so the handler doesn't leak a spinning reader on a dead session.
        await asyncio.wait({t_pty, t_ws}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (t_pty, t_ws):
            task.cancel()
        await asyncio.gather(t_pty, t_ws, return_exceptions=True)
        try:
            os.close(fd)
        except OSError:
            pass
        # Reap the shell so it doesn't linger as a zombie; best-effort, non-blocking.
        try:
            os.kill(child_pid, signal.SIGHUP)
        except OSError:
            pass
        try:
            os.waitpid(child_pid, os.WNOHANG)
        except OSError:
            pass
