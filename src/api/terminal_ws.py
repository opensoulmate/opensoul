"""WebSocket terminal endpoint for OpenMate."""

import asyncio
import fcntl
import json
import logging
import os
import signal
import struct
import termios
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from src.nerve.event_bridge import push_event
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


def _ws_client_host(websocket: WebSocket) -> str:
    """审计用客户端地址（enterprise/immune 审计面同款取法）。"""
    return websocket.client.host if websocket.client else ""


def _audit(event_type: str, summary: str, detail: dict, tenant_id: str | None = None):
    """DEC-⑱ WS 连接审计：terminal WS 连接生命周期事件落 timeline/活动流
    （organ=immune 🛡 安全归口，与 intrusion_detected/permission_denied 同流可见）。

    闭 DEC-⑭-① 诚实边界⑤留档件「连接不落 timeline/审计事件（仅 logger 行）」
    ——shell 是 RCE 面，谁被拒/谁开了 shell/会话何时以何因结束必须可审计回放。

    **同步口（push_event：活动流缓冲+持久 timeline 双落，nerve 总线既有
    fire-and-forget）而非 await 型 emit——取消免疫是硬要求**：会话收尾路径的宿主
    任务可被取消（anyio 会话 teardown 即取消，实测 cancel 兑现于 await 点把
    await 型审计连同收尾一起吞掉=静默丢审计），审计真源不赌 await 活到落盘。

    纪律：①detail/summary 绝不携带凭据材料（token 只字不进事件）②审计失败
    绝不影响连接主路径（旁路纪律，失败 logger.warning 可见禁静默丢审计）。
    """
    try:
        push_event(
            {
                "organ": "immune",
                "emoji": "🛡",
                "type": event_type,
                "summary": summary,
                "detail": detail,
                "timestamp": time.time(),
            },
            tenant_id=tenant_id,
        )
    except Exception as exc:
        logger.warning("terminal WS audit event dropped (%s): %s", event_type, exc)


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
        # DEC-⑱ 连接审计：握手拒绝落审计（被拒尝试是安全事件本体，RCE 面必留痕）。
        # detail 不带凭据材料（has_token 只记在场与否，token 只字不进事件）。
        _audit(
            "terminal_ws_rejected",
            "🛡 Terminal WS 握手拒绝（缺失/无效/停用凭据）",
            {
                "face": "terminal_ws",
                "client": _ws_client_host(websocket),
                "has_token": bool(_ws_presented_token(websocket)),
            },
        )
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

    # DEC-⑱ 连接审计：真 spawn shell 后落 connected 事件（connected=真会话建立
    # ——forkpty 已拉起 bash，非仅握手通过；user/tenant=bind_identity 派生单一源，
    # F3b-⑨b-② 调用点接派生租户键同款）。session_started 供 closed 事件算时长。
    session_started = time.time()
    _audit(
        "terminal_ws_connected",
        f"🖥 Terminal WS 连接建立: user={identity.user_id}",
        {
            "face": "terminal_ws",
            "user_id": str(identity.user_id),
            "client": _ws_client_host(websocket),
        },
        tenant_id=identity.tenant_id,
    )

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
        # DEC-⑱ 会话结束归因（sync，先于 task.cancel()——cancel 后 done/cancelled
        # 形态失真）：看门狗正常返回的唯一路径=凭据失效撤销（exception() 非空=看门狗
        # 自身崩溃，如实不算撤销）；其余按先结束的泵归因 shell_exit / client_disconnect。
        revoked = watchdog.done() and not watchdog.cancelled() and watchdog.exception() is None
        if revoked:
            end_reason = "credential_revoked"
        elif t_pty.done() and not t_pty.cancelled():
            end_reason = "shell_exit"
        else:
            end_reason = "client_disconnect"
        for task in (t_pty, t_ws, watchdog):
            task.cancel()
        # 关键收尾全同步、置于任何 await 之前（根因修，测试逼出的真缺陷）：会话收尾
        # 路径的宿主任务可被取消（anyio 会话 teardown 即取消），取消兑现于 await 点
        # ——await 型收尾会把其后一切关键动作连根吞掉（实测：closed 审计被吞；
        # 同型风险=SIGHUP 漏杀 shell 留 RCE 面）。close fd / SIGHUP+reap / 审计
        # 全是同步动作=取消免疫。
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
        # DEC-⑱ 连接审计：closed=connected 的对偶（谁开过 shell、开了多久、以何因
        # 结束可完整回放）。
        _audit(
            "terminal_ws_closed",
            f"🖥 Terminal WS 会话结束（{end_reason}）: user={identity.user_id}",
            {
                "face": "terminal_ws",
                "user_id": str(identity.user_id),
                "client": _ws_client_host(websocket),
                "reason": end_reason,
                "duration_s": round(time.time() - session_started, 1),
            },
            tenant_id=identity.tenant_id,
        )
        # 三泵退场收尾（唯一 await，置于全部关键动作之后=即便被宿主取消也不再吞
        # 任何关键动作；三泵均已 cancel，取消态自会退场，连接结束不留后台任务
        # DEC-⑭-③ 纪律延续）。
        await asyncio.gather(t_pty, t_ws, watchdog, return_exceptions=True)


# ── DEC-⑯ terminal 路径对齐别名 ────────────────────────────────────────────
# OpenMate terminal-panel 期望 ws://host:8090/ws/terminal（顶层，openmate/src/
# components/terminal-panel.tsx:49 实证），而本仓 canonical 面挂载于
# /api/terminal/ws/terminal（main.py prefix="/api/terminal"）——live 实证面板
# 连不上终端（跨仓消费方期望与本仓路由不齐，DEC-⑭-① 诚实边界④留档件）。
# 收口=本仓加挂顶层别名（改动只限 ~/opensoul 铁律；openmate 改 URL 不在本仓）：
# 别名挂**同一 handler 函数对象**（零第二实现=零鉴权/看门狗/PTY 漂移面），
# canonical 契约零变化。若拍板回「⑯改A」=撤下别名（回面板连不上态）。
alias_router = APIRouter()
alias_router.websocket("/ws/terminal")(terminal_ws)
