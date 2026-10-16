"""WebSocket endpoint for real-time chat communication."""

import asyncio
import logging
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from src.acp.proxy import get_acp_process
from src.nerve.event_bridge import push_event
from src.services.tenant_binding import bind_identity

logger = logging.getLogger(__name__)
router = APIRouter()

# 连接期凭据周期重校验节拍（F3b-④b/1c-⑤/DEC-⑭-② 同族单一源）
TOKEN_REVALIDATE_INTERVAL_S = 60.0


@router.get("/health")
async def ws_chat_health():
    """WSChat health check."""
    return {"status": "ok", "component": "WSChat"}


# CLI argument overrides — agents not listed here default to: binary -p "message"
CLI_ARGS: dict[str, list[str]] = {
    "hermes": ["-z"],
    "mimo": ["run"],
    "codex": ["exec"],
    "opencode": ["run"],
    "openclaw": ["agent", "--agent", "main", "-m"],
    "copilot": ["copilot", "-p"],
    "amazon-q": ["chat", "--no-interactive", "-p"],
}

# agent CLI 常见用户安装目录——systemd 服务的 PATH 常不含这些目录，
# 裸 shutil.which 会把装在 ~/.local/bin 的 agent 误判"未安装"
# （2026-10-11 周期#53 e2e 实证：mimo 只在 ~/.local/bin，服务里 which("mimo")=None）。
_EXTRA_BIN_DIRS = (
    str(Path.home() / ".local" / "bin"),
    str(Path.home() / ".npm-global" / "bin"),
    "/usr/local/bin",
)


def which_agent(name: str) -> str | None:
    """找 agent CLI 的真实可执行路径：先常规 PATH，再补用户安装目录（返回绝对路径）。

    返回绝对路径同时解决子进程 PATH 盲区（spawn 用绝对路径不依赖 env PATH）。"""
    hit = shutil.which(name)
    if hit:
        return hit
    for d in _EXTRA_BIN_DIRS:
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


# Cache of available agents from detect API
_available_agents: dict[str, dict] = {}
_agents_cache_ts: float = 0.0


def _refresh_agent_list():
    """Refresh agent list from AGENT_REGISTRY (same-process, no HTTP)."""
    global _available_agents, _agents_cache_ts
    now = time.time()
    if now - _agents_cache_ts < 60 and _available_agents:
        return
    try:
        from src.api.agents import AGENT_REGISTRY

        _available_agents = {k: v for k, v in AGENT_REGISTRY.items() if v.get("available")}
        _agents_cache_ts = now
        logger.info(f"Agent list refreshed: {len(_available_agents)} available")
    except Exception as e:
        logger.warning(f"Agent list refresh failed: {e}")


def _get_llm_config() -> dict:
    """Read system LLM config from .env."""
    cfg: dict[str, str] = {}
    try:
        env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
        with open(os.path.abspath(env_path)) as f:
            for line in f:
                line = line.strip()
                if line.startswith("LLM_API_KEY="):
                    cfg["api_key"] = line.split("=", 1)[1].strip()
                elif line.startswith("LLM_BASE_URL="):
                    cfg["base_url"] = line.split("=", 1)[1].strip()
                elif line.startswith("LLM_MODEL="):
                    cfg["model"] = line.split("=", 1)[1].strip()
    except Exception:
        pass
    cfg.setdefault("api_key", os.environ.get("LLM_API_KEY", ""))
    cfg.setdefault("base_url", os.environ.get("LLM_BASE_URL", ""))
    cfg.setdefault("model", os.environ.get("LLM_MODEL", ""))
    return cfg


# 细粒度事件识别：hermes CLI的stderr里工具活动行（'⏺ Running tool...'等）→ tool_call事件
_TOOL_EVENT_MARKERS = ("tool_call", "tool", "running", "executing", "⏺", "●")


def _classify_stream_event(etype: str, line: str) -> str:
    """stderr行细分：含工具活动标记(tool/Running/Executing/⏺/●等) → 'tool_call'，
    其余stderr保持'stderr'；stdout行（含markdown标题/▁▁▁分隔线）原样透传'output'。"""
    if etype != "stderr":
        return etype
    low = line.lower()
    return "tool_call" if any(m in low for m in _TOOL_EVENT_MARKERS) else etype


# ── 10.3 run_agent_proxy 挂死可见性（周期#53 诚实边界②销账）────────────────
# 治理 = 进度心跳 + 执行时长可查询 + 可显式取消；**不加到点 SIGKILL**（禁硬杀铁律：
# hermes 长回合实测 12 分钟+仍完成，自动超时杀 = 误杀正常长回合）。取消是显式意图：
# 先 SIGTERM 独立进程组，有界等待，force=True 才升级 SIGKILL（对齐 process_tools 9.2
# 纪律）。心跳走 on_event("heartbeat", ...)，群组任务经 _emit_exec_event 可见进度。
# 11.2（周期#60）：三件套扩到群组任务的 soulmate ACP 回合通道（channel='acp_ws'，
# 同一登记表）——该通道无子进程对象，显式取消 = 停止本次执行等待并摘观察窗
# （会话生命周期归系统，WS断开不杀远端生成），同样绝无到点自动杀。
_PROXY_HEARTBEAT_S = float(os.environ.get("SOULMATE_PROXY_HEARTBEAT_S", "30"))
_PROXY_RUNS: dict[str, dict] = {}  # run_id -> 执行登记项（proc 不外泄，快照走 proxy_run_snapshot）
_PROXY_RUNS_MAX = 64


def _prune_proxy_runs() -> None:
    """登记表有界：超上限先删已结束的旧项（在跑的绝不删）。"""
    overflow = len(_PROXY_RUNS) - _PROXY_RUNS_MAX
    if overflow <= 0:
        return
    finished = [
        rid for rid, e in _PROXY_RUNS.items() if e["status"] not in ("running", "cancelling")
    ]
    for rid in sorted(finished, key=lambda r: _PROXY_RUNS[r]["started_mono"])[:overflow]:
        _PROXY_RUNS.pop(rid, None)


def register_proxy_run(
    agent_id: str,
    task_ref: str | None = None,
    run_id: str | None = None,
    channel: str = "cli",
) -> tuple[str, dict]:
    """登记一次 agent 执行（11.2：CLI 子进程与 soulmate ACP WS 回合共用同一登记表，
    exec-status/exec-cancel 两个任务级端点对两种通道同一套语义）。

    channel: 'cli'=子进程通道（取消走 killpg 信号）/'acp_ws'=ACP WS 回合通道
    （取消走 cancel_cb 摘观察窗）。返回 (run_id, entry)。
    """
    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    entry = {
        "run_id": run_id,
        "agent_id": agent_id,
        "task_ref": task_ref,
        "channel": channel,
        "status": "running",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "started_mono": time.monotonic(),
        "pid": None,
        "proc": None,
        "cancel_cb": None,
        "returncode": None,
        "lines": 0,
        "last_event_type": None,
        "last_event": None,
        "cancel_requested": False,
    }
    _PROXY_RUNS[run_id] = entry
    _prune_proxy_runs()
    return run_id, entry


def finish_proxy_run(run_id: str, status: str, returncode: int | None = None) -> None:
    """执行收口（run_agent_proxy 与 _run_soulmate_turn 共用：异常路径不留 running 假象）。"""
    entry = _PROXY_RUNS.get(run_id)
    if not entry:
        return
    entry["status"] = status
    if returncode is not None:
        entry["returncode"] = returncode


def proxy_run_snapshot(run_id: str) -> dict | None:
    """执行登记项的 JSON 安全快照（执行时长实时计算；proc 对象不外泄）。"""
    entry = _PROXY_RUNS.get(run_id)
    if not entry:
        return None
    return {
        "run_id": entry["run_id"],
        "agent_id": entry["agent_id"],
        "channel": entry.get("channel", "cli"),
        "task_ref": entry.get("task_ref"),
        "status": entry["status"],
        "started_at": entry["started_at"],
        "elapsed_s": round(time.monotonic() - entry["started_mono"], 1),
        "pid": entry.get("pid"),
        "returncode": entry.get("returncode"),
        "lines": entry.get("lines", 0),
        "last_event_type": entry.get("last_event_type"),
        "last_event": entry.get("last_event"),
        "cancel_requested": entry.get("cancel_requested", False),
    }


def list_proxy_runs(task_ref: str | None = None) -> list[dict]:
    snaps = [s for s in (proxy_run_snapshot(rid) for rid in list(_PROXY_RUNS)) if s]
    if task_ref is not None:
        snaps = [s for s in snaps if s.get("task_ref") == task_ref]
    return sorted(snaps, key=lambda s: s["started_at"])


def find_proxy_run(task_ref: str, running_only: bool = True) -> dict | None:
    """按 task_ref 找执行登记项：优先在跑的（含 cancelling），其次最近一次。"""
    cands = list_proxy_runs(task_ref)
    if running_only:
        running = [s for s in cands if s["status"] in ("running", "cancelling")]
        return running[-1] if running else None
    return cands[-1] if cands else None


async def cancel_proxy_run(run_id: str, force: bool = False) -> dict:
    """显式取消一次 agent 执行（用户意图，非到点自动杀）。

    SIGTERM 独立进程组（create_subprocess_exec start_new_session=True 保证进程组
    收编），有界等待退出；force=True 才升级 SIGKILL。禁任何定时自动 SIGKILL。
    """
    entry = _PROXY_RUNS.get(run_id)
    if not entry:
        return {"ok": False, "reason": f"未知 run_id: {run_id}"}
    if entry["status"] not in ("running", "cancelling"):
        return {
            "ok": False,
            "reason": f"run 已结束（status={entry['status']}），无可取消对象",
            "run_id": run_id,
        }
    proc = entry.get("proc")
    cancel_cb = entry.get("cancel_cb")
    if (proc is None or proc.returncode is not None) and cancel_cb is None:
        return {"ok": False, "reason": "执行进程已退出，无可取消对象", "run_id": run_id}
    entry["cancel_requested"] = True
    entry["status"] = "cancelling"
    channel = entry.get("channel", "cli")
    if proc is not None and proc.returncode is not None:
        proc = None  # 进程已退出但回合通道还有等待方 → 走 cancel_cb
    if proc is None:
        # ACP WS 回合通道（11.2）：无子进程对象，显式取消 = 停止本次执行等待并摘观察窗。
        # 会话生命周期归系统（WS断开不杀远端生成），绝无到点自动杀。
        if cancel_cb is None:
            return {"ok": False, "reason": "执行已退出，无可取消对象", "run_id": run_id}
        try:
            r = cancel_cb()
            if asyncio.iscoroutine(r):
                await r
        except Exception as e:
            return {
                "ok": False,
                "reason": f"取消唤醒失败: {type(e).__name__}: {e}",
                "run_id": run_id,
            }
        # 有界等待收口（等待观察窗口，不是超时杀）
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and entry["status"] == "cancelling":
            await asyncio.sleep(0.05)
        return {
            "ok": True,
            "run_id": run_id,
            "channel": channel,
            "mode": "acp_ws_detach",
            "signal": None,
            "force": force,
            "exited": entry["status"] != "cancelling",
            "returncode": entry.get("returncode"),
            "elapsed_s": round(time.monotonic() - entry["started_mono"], 1),
        }
    sig_name, signum = ("SIGKILL", 9) if force else ("SIGTERM", 15)
    try:
        os.killpg(os.getpgid(proc.pid), signum)
    except (ProcessLookupError, PermissionError, OSError) as e:
        return {"ok": False, "reason": f"信号发送失败: {type(e).__name__}: {e}", "run_id": run_id}
    # 有界等待退出（等待观察窗口，不是超时杀）
    deadline = time.monotonic() + (5.0 if force else 3.0)
    while time.monotonic() < deadline and proc.returncode is None:
        await asyncio.sleep(0.05)
    return {
        "ok": True,
        "run_id": run_id,
        "channel": channel,
        "mode": "signal",
        "signal": sig_name,
        "force": force,
        "exited": proc.returncode is not None,
        "returncode": proc.returncode,
        "elapsed_s": round(time.monotonic() - entry["started_mono"], 1),
    }


async def run_agent_proxy(
    agent_id: str,
    text: str,
    on_event=None,
    run_id: str | None = None,
    task_ref: str | None = None,
    heartbeat_s: float | None = None,
) -> tuple[str, str, bool]:
    """Run a message through any installed agent with system LLM config injected.

    on_event: 可选回调 on_event(event_type, line)，逐行推送执行过程事件
    （event_type: output=stdout行 / stderr=stderr行 / tool_call=stderr里的工具活动行
    / heartbeat=执行进度心跳[已用时/行数/最近输出]），实现执行过程可视化。
    run_id/task_ref：执行登记（查询/取消用，见 proxy_run_snapshot/cancel_proxy_run）；
    heartbeat_s 覆盖心跳间隔（测试用）。**无到点超时杀**：长回合跑完为止，取消只能显式发起。
    """
    _refresh_agent_list()

    agent_info = _available_agents.get(agent_id)
    binary = agent_info["binary"] if agent_info else agent_id

    binary_path = which_agent(binary)
    if not binary_path:
        return f"Agent未安装: {binary}", "error", False

    args = CLI_ARGS.get(agent_id, ["-p"])
    cmd = [binary_path] + args + [text]
    logger.info(f"Agent proxy running: {' '.join(cmd)}")

    run_id, entry = register_proxy_run(agent_id, task_ref=task_ref, run_id=run_id, channel="cli")

    heartbeat_task: asyncio.Task | None = None
    try:
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join(_EXTRA_BIN_DIRS) + os.pathsep + env.get("PATH", "")
        llm_cfg = _get_llm_config()
        if llm_cfg.get("api_key"):
            env["OPENAI_API_KEY"] = llm_cfg["api_key"]
            env["ANTHROPIC_API_KEY"] = llm_cfg["api_key"]
        if llm_cfg.get("base_url"):
            env["OPENAI_BASE_URL"] = llm_cfg["base_url"]
            env["OPENAI_API_BASE"] = llm_cfg["base_url"]
        if llm_cfg.get("model"):
            env["OPENAI_MODEL"] = llm_cfg["model"]
            env["MODEL"] = llm_cfg["model"]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,  # 独立进程组：显式取消可 killpg 收编整棵进程树
        )
        entry["proc"] = proc
        entry["pid"] = proc.pid

        # 流式逐行读取输出（替代原communicate()一次拿完，执行过程不可见问题的根因）
        collected: dict[str, list[str]] = {"out": [], "err": []}

        async def _pump(stream, bucket: list[str], etype: str):
            while True:
                line = await stream.readline()
                if not line:
                    break
                s = line.decode("utf-8", errors="replace").rstrip()
                if not s:
                    continue
                bucket.append(s)
                entry["lines"] = entry.get("lines", 0) + 1
                entry["last_event_type"] = etype
                entry["last_event"] = s
                if on_event is not None:
                    try:
                        r = on_event(_classify_stream_event(etype, s), s)
                        if asyncio.iscoroutine(r):
                            await r
                    except Exception:
                        pass  # 事件回调失败不影响执行本身

        async def _heartbeat():
            """进度心跳：只报进度不打断执行；失败旁路（只计时冒泡，不阻塞）。"""
            interval = float(heartbeat_s if heartbeat_s is not None else _PROXY_HEARTBEAT_S)
            if interval <= 0:
                return
            try:
                while entry["status"] == "running":
                    await asyncio.sleep(interval)
                    if entry["status"] != "running":
                        break
                    elapsed = time.monotonic() - entry["started_mono"]
                    last = entry.get("last_event") or "（暂无输出）"
                    msg = (
                        f"⏳ 执行进行中：已用 {elapsed:.0f}s｜已收 {entry.get('lines', 0)} 行"
                        f"｜最近: {last[:80]}"
                    )
                    if on_event is not None:
                        try:
                            r = on_event("heartbeat", msg)
                            if asyncio.iscoroutine(r):
                                await r
                        except Exception:
                            pass
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        heartbeat_task = asyncio.create_task(_heartbeat())

        await asyncio.gather(
            _pump(proc.stdout, collected["out"], "output"),
            _pump(proc.stderr, collected["err"], "stderr"),
            proc.wait(),
        )
        entry["returncode"] = proc.returncode
        response = "\n".join(collected["out"]).strip()
        if not response and proc.returncode != 0:
            response = "\n".join(collected["err"]).strip()
        if entry.get("cancel_requested"):
            # 显式取消：如实报告被取消 + 部分输出，绝不冒充正常完成
            entry["status"] = "cancelled"
            elapsed = time.monotonic() - entry["started_mono"]
            partial = response[-800:]
            note = "（截断显示末尾800字）" if len(response) > 800 else ""
            body = f"\n已收集的部分输出{note}：\n{partial}" if partial else ""
            return f"⚠️ 执行已被显式取消（用时 {elapsed:.1f}s）{body}", "cancelled", False
        entry["status"] = "done" if proc.returncode == 0 else "failed"
        return response or "（无响应）", agent_id, proc.returncode == 0
    except Exception as e:
        logger.error(f"Agent proxy error: {e}")
        entry["status"] = "failed"
        return f"Agent执行出错: {type(e).__name__}", "error", False
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        entry["proc"] = None  # 释放进程对象引用（快照仍留 pid/returncode）
        if entry["status"] == "running":
            entry["status"] = "failed"  # 兜底：异常路径不留 running 假象


async def _get_learned_advisory(text: str) -> tuple[str, bool]:
    """F2b-②b 推荐回流对话面：learned_patterns → 发给 agent 的 prompt 软建议块。

    取数口=src.learn.long_term.learned_advisory_for 单一源（与任务执行/chat 面同口径）。
    返回 (建议块, 取数是否失败)。学习层故障不阻断对话但必须可见
    （F2b-②"禁静默"纪律：learned-context-error 日志 + info 帧）；
    无推荐=("", False) 零注入（宁缺勿滥，无关建议是噪声）。
    """
    try:
        from src.learn.long_term import learned_advisory_for

        block, _n = await learned_advisory_for(text)
        return block, False
    except Exception as exc:
        logger.warning("learned-context-error: %s", exc)
        return "", True


async def _get_episodic_advisory(text: str) -> tuple[str, bool]:
    """DEC-③-① episodic 回流对话面：相似失败经验 error → 发给 agent 的软建议块。

    取数口=src.learn.experience.episodic_advisory_for 单一源（与任务执行/chat
    面同口径，pending-decisions ③ 试水形态 B：仅 failure 行 error 字段）。
    返回 (建议块, 取数是否失败)。episodic 层故障不阻断对话但必须可见
    （F2b-②"禁静默"纪律：episodic-context-error 日志 + info 帧）；
    无命中=("", False) 零注入（宁缺勿滥）。
    """
    try:
        from src.learn.experience import episodic_advisory_for

        block, _n = await episodic_advisory_for(text)
        return block, False
    except Exception as exc:
        logger.warning("episodic-context-error: %s", exc)
        return "", True


def _ws_client_host(websocket: WebSocket) -> str:
    """审计事件 client 来源（terminal_ws 同款判法）。"""
    return websocket.client.host if websocket.client else ""


def _audit(event_type: str, summary: str, detail: dict, tenant_id: str | None = None):
    """DEC-⑱-② WS 连接审计：ws_chat 连接生命周期事件落 timeline/活动流
    （organ=immune 🛡 安全归口，与 terminal_ws_* / intrusion_detected /
    permission_denied 同流可见）。

    闭 DEC-⑱ 诚实边界②留档件「ws_chat/link/collab 长连接面审计留待拍（⑱-②）」
    ——对话通道谁被拒/谁连上/会话何时以何因结束必须可审计回放。

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
        logger.warning("ws chat audit event dropped (%s): %s", event_type, exc)


def _identity_for_token(token):
    """凭据效力判定单一判定口（握手与连接期看门狗共用，禁第二套判法——
    F3b-④b/DEC-⑭-② 同款）：bind_identity 单一派生口 + authed 必真
    （F3b-② 家族，DEC-⑭-③ 边界③留档件收口——修前 decode_token 单一判定：
    UUID 标准 token 不查 users 行=用户停用/删除不触发关闭；企业 token 分支
    DB 瞬断被吞成 None=故障被当失效凭据误杀，与「故障≠失效凭据」声明相悖）。

    - 凭据缺失/无效（JWT 过期/签名失效/用户停用或消失）→ None
    - 判定口自身故障（DB 瞬断等非 HTTPException）→ 异常原样上抛：故障≠失效
      凭据（DEC-⑭-②「禁误杀」同款），握手面 fail-closed 拒、看门狗面记日志
      继续，由调用方按各自语义处置。
    """
    if not token:
        return None
    try:
        identity = bind_identity(f"Bearer {token}")
    except HTTPException:
        return None
    return identity if identity.authed else None


def _connected_user_id(identity) -> str:
    """connected 帧 user_id 载荷单一判法（DEC-⑰ 收口：dict repr 怪形→纯用户 id；
    DEC-⑭-④ 判口换 bind_identity 后输入形=BoundIdentity——载荷=单一派生口
    user_id 纯字符串，F3b-② 语义：标准用户=UUID、enterprise=数字 id 字符串）。

    修前（DEC-⑰）：str(user) 直接序列化判定口回包 dict——载荷是
    "{'user_id': UUID('...'), 'role': 'user'}" 的 Python dict repr（非纯 id，
    live 修前实证同形），客户端拿到的是怪形字符串不可直接当用户 id 用。

    判法（单一源，禁调用点手抄）：
    - 判定口派生 user_id → str() 纯用户 id（生态参照形：acp-proxy ws_chat.py
      connected 帧 user_id=纯 UUID 字符串，本仓向参照形对齐）
    - 缺失/None → "" 空串如实，禁 fabricate dict repr 怪形（键派生故障
      绝不捏造身份，F3b-⑨b 系旁路纪律同款：如实空值不掉落语义）
    """
    uid = getattr(identity, "user_id", None)
    return str(uid) if uid is not None else ""


async def _ws_chat_token_watchdog(websocket: WebSocket, token: str):
    """WS chat 连接期凭据周期重校验（F3b-④b/1c-⑤/DEC-⑭-② 同族；DEC-⑭-①
    诚实边界⑥留档件「已有 decode_token 握手轴但无连接期重校验」收口）。

    每 TOKEN_REVALIDATE_INTERVAL_S 秒重跑 _identity_for_token（单一判定口，与
    握手同源禁第二套判法）：
    - 凭据失效（JWT 过期/签名失效/用户停用或消失）→ close 1008（与握手拒绝
      同语义）：失效凭据不留对话通道，禁止持过期/失效凭据继续对话。
    - 判定口瞬断 → 记录可见后继续，故障≠失效凭据禁误杀（DEC-⑭-② 同款）。
    诚实边界：失效与关闭之间 <= TOKEN_REVALIDATE_INTERVAL_S 秒有界滞后（周期
    重校验语义固有）；连接期内凭据字符串不变（重校验的是同一凭据当前效力，非
    轮换）；处理中的当条消息跑完但收发面随 close 断开（无增量凭据轴）。
    """
    while True:
        await asyncio.sleep(TOKEN_REVALIDATE_INTERVAL_S)
        try:
            identity = _identity_for_token(token)
        except Exception as exc:
            logger.warning("ws chat token revalidation probe failed: %s", exc)
            continue
        if identity is None:
            logger.warning("WS chat closed by revalidation: invalid/expired/inactive credentials")
            try:
                await websocket.close(code=1008, reason="invalid token")
            except Exception:
                pass
            return


@router.websocket("/chat")
async def chat_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time chat.

    Protocol:
    - Client sends: {"type": "message", "text": "...", "mode": "hermes|acp|a2a|agent_proxy", "session_id": "...", "agent_id": "..."}
    - Server sends: {"type": "connected", "user_id": "<uuid>"} (握手成功；user_id=纯用户 id，DEC-⑰ 收口——修前为 dict repr 怪形)
    - Server sends: {"type": "thinking"} (when processing starts)
    - Server sends: {"type": "chunk", "text": "..."} (streaming chunks)
    - Server sends: {"type": "agent_event", "event": "output|stderr|tool_call|heartbeat", "text": "..."}
      (agent_proxy 模式过程可见事件；stdout 响应行走 chunk，其余事件走此帧不混入响应文本)
    - Server sends: {"type": "info", "message": "..."} (过程提示，如学习建议/失败教训获取失败；未知 type 客户端忽略即兼容)
    - Server sends: {"type": "done", "text": "...", "source": "..."} (final response)
    - Server sends: {"type": "error", "message": "..."} (on error)
    """
    await websocket.accept()

    # Authenticate via first message or query param
    token = websocket.query_params.get("token", "")
    if not token:
        # DEC-⑱-② 连接审计：被拒尝试是安全事件本体必留痕（cause 单列真因），
        # detail 不带凭据材料（has_token 只记在场与否，token 只字不进事件）。
        _audit(
            "ws_chat_rejected",
            "🛡 WS chat 握手拒绝（缺失凭据）",
            {
                "face": "ws_chat",
                "client": _ws_client_host(websocket),
                "has_token": False,
                "cause": "missing_token",
            },
        )
        await websocket.send_json({"type": "error", "message": "Missing token"})
        await websocket.close()
        return

    try:
        identity = _identity_for_token(token)
    except Exception:
        # 判定口故障=无证可验=拒（fail-closed，握手面照旧拒——DEC-⑭-② 判定口
        # 「故障原样上抛」在握手面的处置=拒，与「Invalid token」同契约帧形）
        identity = None
    if identity is None:
        _audit(
            "ws_chat_rejected",
            "🛡 WS chat 握手拒绝（无效/停用凭据）",
            {
                "face": "ws_chat",
                "client": _ws_client_host(websocket),
                "has_token": True,
                "cause": "invalid_token",
            },
        )
        await websocket.send_json({"type": "error", "message": "Invalid token"})
        await websocket.close()
        return

    await websocket.send_json({"type": "connected", "user_id": _connected_user_id(identity)})

    # DEC-⑱-② 连接审计：connected=会话真建立（connected 帧已送达）；user/tenant=
    # bind_identity 派生单一源（user_id 载荷走 _connected_user_id 同一判法）。
    # session_started 供 closed 事件算时长。
    session_started = time.time()
    _audit(
        "ws_chat_connected",
        f"💬 WS chat 连接建立: user={identity.user_id}",
        {
            "face": "ws_chat",
            "user_id": _connected_user_id(identity),
            "client": _ws_client_host(websocket),
        },
        tenant_id=identity.tenant_id,
    )

    # 连接期凭据周期重校验（DEC-⑭-① 诚实边界⑥留档件收口）：失效凭据 close 1008
    # 不留对话通道；看门狗收尾在 finally（不挂 receive 抛错上，F3b-④b/DEC-⑭-②
    # 同款根因纪律）。
    watchdog = asyncio.create_task(_ws_chat_token_watchdog(websocket, token))

    # DEC-⑱-② 会话结束归因缺省（正常收口=客户端断开；异常收口在 except 改写）。
    end_reason = "client_disconnect"
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "message":
                text = data.get("text", "").strip()
                mode = data.get("mode", "hermes")
                session_id = data.get("session_id")
                agent_id = data.get("agent_id")
                attachments = data.get("attachments", [])  # [{type, data, name}]

                # Build text from attachments if no text provided
                if not text and attachments:
                    image_parts = [a for a in attachments if a.get("type") == "image"]
                    file_parts = [a for a in attachments if a.get("type") == "file"]
                    if image_parts:
                        text = "用户发送了一张图片"
                    elif file_parts:
                        text = f"用户发送了文件: {', '.join(a.get('name', 'file') for a in file_parts)}"

                if not text:
                    await websocket.send_json({"type": "error", "message": "Empty message"})
                    continue

                # F2b-②b 推荐回流对话面：learned_patterns → 发给 agent 的 prompt 软建议块
                # （ws_chat 无 system 通道，建议块随消息文本送达且自带"以任务要求为准"
                # 免责；无关=零注入。取数失败可见不阻断：info 帧 + 日志）
                advisory, adv_failed = await _get_learned_advisory(text)
                if adv_failed:
                    await websocket.send_json(
                        {"type": "info", "message": "学习建议获取失败（不影响对话）。"}
                    )
                # DEC-③-① episodic 回流对话面：相似失败经验 error → 软建议块
                # （与 F2b-②b learned 块并列的第二条回流轴，仅 failure 行 error
                # 字段；取数用原文防 learned 块污染相关度输入；无关=零注入。
                # 取数失败可见不阻断：info 帧 + episodic-context-error 日志）
                episodic, epi_failed = await _get_episodic_advisory(text)
                if epi_failed:
                    await websocket.send_json(
                        {"type": "info", "message": "历史失败教训获取失败（不影响对话）。"}
                    )
                if advisory:
                    text = f"{text}\n\n{advisory}"
                if episodic:
                    text = f"{text}\n\n{episodic}"

                # Signal thinking
                await websocket.send_json({"type": "thinking"})

                try:
                    # Check for image attachments
                    image_attachments = [a for a in attachments if a.get("type") == "image"]

                    if mode == "agent_proxy" and agent_id:
                        # Route to specific CLI agent（F4b-③：on_event 接线逐行帧——
                        # stdout 行=响应文本流走 chunk（与 ACP chunk 同消费语义：逐块拼接即响应，
                        # 行尾补 \n 保行形态）；stderr/tool_call/heartbeat=过程可见事件走
                        # agent_event（不混入响应文本）。事件帧是增量可见面，done 契约不变。

                        async def _ws_event(etype: str, line: str):
                            if etype == "output":
                                await websocket.send_json({"type": "chunk", "text": line + "\n"})
                            else:
                                await websocket.send_json(
                                    {"type": "agent_event", "event": etype, "text": line}
                                )

                        response_text, source, success = await run_agent_proxy(
                            agent_id, text, on_event=_ws_event
                        )
                    else:
                        acp = get_acp_process()
                        if not session_id:
                            # 无会话时先建会话：否则 session/prompt 对未知
                            # sessionId 秒回 stopReason=refusal，客户端只见"无响应"
                            await acp.new_session()

                        if image_attachments and mode in ("hermes", "acp"):
                            # Send with image via ACP (no streaming for images)
                            img = image_attachments[0]
                            result = await acp.send_message_with_image(
                                text,
                                img.get("data", ""),
                                img.get("mime_type", "image/png"),
                                session_id,
                            )
                            response_text = result.get("response_text", "")
                            source = result.get("source", "acp")
                        elif mode in ("hermes", "acp"):
                            # Real streaming via ACP
                            collected = []
                            async for chunk in acp.send_message_stream(text, session_id):
                                collected.append(chunk)
                                await websocket.send_json({"type": "chunk", "text": chunk})
                            response_text = "".join(collected)
                            source = "acp"
                        else:
                            response_text = "不支持的模式"
                            source = "error"

                    # Send response
                    if response_text:
                        await websocket.send_json(
                            {
                                "type": "done",
                                "text": response_text,
                                "source": source,
                            }
                        )
                    else:
                        await websocket.send_json({"type": "error", "message": "无响应"})

                except Exception as e:
                    logger.error(f"WS chat error: {e}")
                    await websocket.send_json({"type": "error", "message": str(e)})

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        end_reason = "error"
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception as exc:
            logging.getLogger(__name__).debug("operation skipped: %s", exc)
    finally:
        # DEC-⑱-② 会话结束归因（同步，先于 task.cancel()——cancel 后 done/cancelled
        # 形态失真）：看门狗正常返回的唯一路径=凭据失效撤销（exception() 非空=看门狗
        # 自身崩溃，如实不算撤销），其余按收口路径归因 error / client_disconnect。
        revoked = watchdog.done() and not watchdog.cancelled() and watchdog.exception() is None
        if revoked:
            end_reason = "credential_revoked"
        # DEC-⑱-② 连接审计：closed=connected 的对偶（谁连过对话通道、连了多久、
        # 以何因结束可完整回放）。同步口置于任何 await 之前=取消免疫（DEC-⑱ 教训）。
        _audit(
            "ws_chat_closed",
            f"💬 WS chat 会话结束（{end_reason}）: user={identity.user_id}",
            {
                "face": "ws_chat",
                "user_id": _connected_user_id(identity),
                "client": _ws_client_host(websocket),
                "reason": end_reason,
                "duration_s": round(time.time() - session_started, 1),
            },
            tenant_id=identity.tenant_id,
        )
        # 看门狗收尾（不挂 receive 抛错上）：连接结束/撤销后不留后台任务。
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)
