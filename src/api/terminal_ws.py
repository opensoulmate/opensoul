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

TOKEN_REVALIDATE_INTERVAL_S = 60.0


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


def _identity_for_token(token):
    """凭据效力判定单一判定口（握手 _terminal_identity 与连接期看门狗共用，
    禁第二套判法——F3b-④b 同款）：bind_identity 单一派生口 + authed 必真。

    - 凭据缺失/无效（JWT 过期/签名失效/用户停用或消失）→ None
    - 判定口自身故障（DB 瞬断等非 HTTPException）→ 异常原样上抛：故障≠失效
      凭据（F3b-④b「禁误杀」同款），握手面 fail-closed 拒、看门狗面记日志
      继续，由调用方按各自语义处置。
    """
    if not token:
        return None
    try:
        identity = bind_identity(f"Bearer {token}")
    except HTTPException:
        return None
    return identity if identity.authed else None


def _terminal_identity(websocket: WebSocket):
    """DEC-⑭-① 握手凭据必持（fail-closed，DEC-⑬ 同款「无证可验=拒」）：

    token 必持且须为有效活跃用户凭据（_identity_for_token 单一判定口 →
    bind_identity 单一派生口，F3b-② 家族禁第二套判法）——PTY shell 是执行面，
    无证/坏证/停用身份一律不给 shell。判定口故障同样 fail-closed（无证可验=拒）。
    返回 BoundIdentity 或 None（None=拒绝）。
    """
    token = _ws_presented_token(websocket)
    try:
        return _identity_for_token(token)
    except Exception:
        return None


async def _terminal_token_watchdog(websocket: WebSocket, token: str):
    """Terminal WS 连接期凭据周期重校验（F3b-④b/1c-⑤ 同族；DEC-⑭-① 诚实边界①
    留档件「token 过期后已建立的 shell 会话延续至断开」收口）。

    每 TOKEN_REVALIDATE_INTERVAL_S 秒重跑 _identity_for_token（单一判定口，
    与握手同源禁第二套判法）：
    - 凭据失效（JWT 过期/签名失效/用户停用或消失）→ close 1008（与握手拒绝
      同语义）且看门狗自身返回=wait 集触发会话拆收尾 SIGHUP+reap 杀 shell
      （收尾不挂 receive 抛错上，F3b-④b「禁泄漏」同款根因纪律——恶意客户端
      不回 close 帧也留不下 shell）——失效凭据不留 shell（RCE 面连接期收口）。
    - 判定口瞬断（DB 故障）→ 记录可见后继续，故障≠失效凭据禁误杀
      （F3b-④b 同款）。
    诚实边界：失效与关闭之间 <= TOKEN_REVALIDATE_INTERVAL_S 秒有界滞后（周期
    重校验语义固有）；连接期内凭据字符串不变（重校验的是同一凭据当前效力，
    非轮换）；无 re-key 语义（terminal 无租户路由轴，派生租户仅握手记录）。
    """
    while True:
        await asyncio.sleep(TOKEN_REVALIDATE_INTERVAL_S)
        try:
            identity = _identity_for_token(token)
        except Exception as exc:
            logger.warning("terminal WS token revalidation probe failed: %s", exc)
            continue
        if identity is None:
            logger.warning(
                "Terminal WS closed by revalidation: invalid/expired/inactive credentials"
            )
            try:
                await websocket.close(code=1008, reason="invalid token")
            except Exception:
                pass
            return


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

    # 连接期凭据周期重校验（F3b-④b/1c-⑤ 同族，DEC-⑭-① 诚实边界①留档件收口）：
    # 失效凭据 close 1008 → read_ws 结束 → 本 handler finally 收尾 SIGHUP+reap
    # 杀 shell——失效凭据不留 shell（RCE 面连接期收口）。
    watchdog = asyncio.create_task(
        _terminal_token_watchdog(websocket, _ws_presented_token(websocket) or "")
    )

    t_pty = asyncio.create_task(read_pty())
    t_ws = asyncio.create_task(read_ws())
    try:
        # When either pump ends (shell exit or client disconnect) or the watchdog
        # revokes the session (credential invalid), tear the session down so the
        # handler doesn't leak a spinning reader on a dead session. 收尾不挂
        # receive 抛错上（F3b-④b「禁泄漏」同款根因纪律）：看门狗进 wait 集，
        # 撤销即拆会话杀 shell——恶意客户端不回 close 帧也留不下 shell。
        await asyncio.wait({t_pty, t_ws, watchdog}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (t_pty, t_ws, watchdog):
            task.cancel()
        await asyncio.gather(t_pty, t_ws, watchdog, return_exceptions=True)
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
