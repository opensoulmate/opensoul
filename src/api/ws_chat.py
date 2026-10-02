"""WebSocket endpoint for real-time chat communication."""

import asyncio
import logging
import os
import shutil
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.acp.proxy import get_acp_process
from src.api.user import decode_token

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def ws_chat_health():
    """WSChat health check."""
    return {"status": "ok", "component": "WSChat"}


# CLI argument overrides — agents not listed here default to: binary -p "message"
CLI_ARGS: dict[str, list[str]] = {
    "hermes": ["-z"],
    "mimo": ["run"],
    "codex": ["exec"],
    "opencode": ["-q"],
    "openclaw": ["agent", "--agent", "main", "-m"],
    "copilot": ["copilot", "-p"],
    "amazon-q": ["chat", "--no-interactive", "-p"],
}

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


async def run_agent_proxy(agent_id: str, text: str) -> tuple[str, str, bool]:
    """Run a message through any installed agent with system LLM config injected."""
    _refresh_agent_list()

    agent_info = _available_agents.get(agent_id)
    binary = agent_info["binary"] if agent_info else agent_id

    if not shutil.which(binary):
        return f"Agent未安装: {binary}", "error", False

    args = CLI_ARGS.get(agent_id, ["-p"])
    cmd = [binary] + args + [text]
    logger.info(f"Agent proxy running: {' '.join(cmd)}")

    try:
        env = os.environ.copy()
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
        )
        stdout, stderr = await proc.communicate()
        response = stdout.decode("utf-8", errors="replace").strip()
        if not response and proc.returncode != 0:
            response = stderr.decode("utf-8", errors="replace").strip()
        return response or "（无响应）", agent_id, proc.returncode == 0
    except Exception as e:
        logger.error(f"Agent proxy error: {e}")
        return f"Agent执行出错: {type(e).__name__}", "error", False


@router.websocket("/chat")
async def chat_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time chat.

    Protocol:
    - Client sends: {"type": "message", "text": "...", "mode": "hermes|acp|a2a|agent_proxy", "session_id": "...", "agent_id": "..."}
    - Server sends: {"type": "thinking"} (when processing starts)
    - Server sends: {"type": "chunk", "text": "..."} (streaming chunks)
    - Server sends: {"type": "done", "text": "...", "source": "..."} (final response)
    - Server sends: {"type": "error", "message": "..."} (on error)
    """
    await websocket.accept()

    # Authenticate via first message or query param
    token = websocket.query_params.get("token", "")
    if not token:
        await websocket.send_json({"type": "error", "message": "Missing token"})
        await websocket.close()
        return

    user_id = decode_token(token)
    if not user_id:
        await websocket.send_json({"type": "error", "message": "Invalid token"})
        await websocket.close()
        return

    await websocket.send_json({"type": "connected", "user_id": str(user_id)})

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

                # Signal thinking
                await websocket.send_json({"type": "thinking"})

                try:
                    # Check for image attachments
                    image_attachments = [a for a in attachments if a.get("type") == "image"]

                    if mode == "agent_proxy" and agent_id:
                        # Route to specific CLI agent
                        response_text, source, success = await run_agent_proxy(agent_id, text)
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
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception as exc:
            logging.getLogger(__name__).debug("operation skipped: %s", exc)
