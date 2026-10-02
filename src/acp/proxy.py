"""ACP proxy with hermes -z fallback."""

import asyncio
import base64
import json
import logging
import os
import tempfile
import time
from typing import Any

logger = logging.getLogger(__name__)


class ACPProcess:
    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._msg_id: int = 0
        self._initialized: bool = False
        self._default_session_id: str | None = None
        self._read_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        if self._proc is None or self._proc.returncode is not None:
            return False
        # Also check if stdin pipe is still alive
        if self._proc.stdin and self._proc.stdin.is_closing():
            return False
        return True

    async def start(self):
        if self.is_running:
            return self._get_agent_info()

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self._proc = await asyncio.create_subprocess_exec(
            "hermes",
            "acp",
            "--accept-hooks",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        print(f"ACP: started pid={self._proc.pid}", flush=True)

        await self._rpc(
            "initialize",
            {"protocolVersion": 1, "clientInfo": {"name": "OpenMate", "version": "1.0.0"}},
        )
        self._initialized = True
        print("ACP: initialized", flush=True)
        return self._get_agent_info()

    async def stop(self):
        if self._proc:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                self._proc.kill()
            self._proc = None
        self._initialized = False
        self._default_session_id = None

    async def _restart(self):
        """Force-stop and restart the ACP process."""
        print("ACP: restarting due to broken pipe / stale process", flush=True)
        logger.warning("ACP: restarting due to broken pipe / stale process")
        await self.stop()
        await self.start()

    async def send_message(self, text: str, session_id: str | None = None) -> dict[str, Any]:
        if not self.is_running or not self._initialized:
            await self.start()
        sid = session_id or self._default_session_id or "default"
        for attempt in range(2):
            try:
                result = await self._prompt(text, sid)
                if result.get("response_text"):
                    return result
                print("ACP: events not captured (buffering), using hermes -z", flush=True)
            except (BrokenPipeError, OSError) as e:
                print(f"ACP: pipe error {e}, restarting (attempt {attempt + 1})", flush=True)
                logger.warning(f"ACP pipe error: {e}, restarting")
                if attempt == 0:
                    await self._restart()
                    continue
            except Exception as e:
                print(f"ACP: error {e}, falling back to hermes -z", flush=True)
            break
        return await self._cli(text)

    async def send_message_stream(self, text: str, session_id: str | None = None):
        """Yield chunks as they arrive from ACP. Falls back to CLI if needed."""
        if not self.is_running or not self._initialized:
            await self.start()
        sid = session_id or self._default_session_id or "default"
        try:
            async for chunk in self._prompt_parts_stream([{"type": "text", "text": text}], sid):
                yield chunk
        except (BrokenPipeError, OSError) as e:
            print(f"ACP: stream pipe error {e}, restarting", flush=True)
            await self._restart()
            try:
                async for chunk in self._prompt_parts_stream([{"type": "text", "text": text}], sid):
                    yield chunk
            except Exception as e2:
                print(f"ACP: stream retry error {e2}, falling back to CLI", flush=True)
                result = await self._cli(text)
                yield result.get("response_text", "")
        except Exception as e:
            print(f"ACP: stream error {e}, falling back to CLI", flush=True)
            result = await self._cli(text)
            yield result.get("response_text", "")

    async def send_message_with_image(
        self,
        text: str,
        image_data: str,
        mime_type: str = "image/png",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_running or not self._initialized:
            await self.start()
        sid = session_id or self._default_session_id or "default"
        for attempt in range(2):
            try:
                parts = []
                if text:
                    parts.append({"type": "text", "text": text})
                b64 = image_data.split(",")[-1] if "," in image_data else image_data
                parts.append({"type": "image", "data": b64, "mimeType": mime_type})
                result = await self._prompt_parts(parts, sid)
                if result.get("response_text"):
                    return result
            except (BrokenPipeError, OSError) as e:
                print(f"ACP: image pipe error {e}, restarting (attempt {attempt + 1})", flush=True)
                logger.warning(f"ACP image pipe error: {e}, restarting")
                if attempt == 0:
                    await self._restart()
                    continue
            except Exception as e:
                print(f"ACP: image error {e}", flush=True)
            break
        # Fallback: save image to temp file, let hermes read via vision tool
        tmp_path = None
        try:
            b64_clean = image_data.split(",")[-1] if "," in image_data else image_data
            ext = mime_type.split("/")[-1].split(";")[0] or "png"
            fd, tmp_path = tempfile.mkstemp(suffix=f".{ext}", prefix="openmate_img_")
            with os.fdopen(fd, "wb") as f:
                f.write(base64.b64decode(b64_clean))
            prompt = f"{text or '用户发送了一张图片'}\n\n[图片已保存到: {tmp_path}]"
            return await self._cli(prompt)
        except Exception as e:
            print(f"ACP: image fallback error {e}", flush=True)
            return await self._cli(text or "用户发送了一张图片")
        finally:
            # Clean up temp file after a delay (hermes needs time to read it)
            if tmp_path:
                asyncio.get_event_loop().call_later(
                    300, lambda: os.unlink(tmp_path) if os.path.exists(tmp_path) else None
                )

    async def list_sessions(self) -> list[dict]:
        if not self.is_running or not self._initialized:
            await self.start()
        resp = await self._rpc("session/list", {})
        return resp.get("sessions", [])

    async def new_session(self, cwd: str | None = None) -> dict:
        if not self.is_running or not self._initialized:
            await self.start()
        resp = await self._rpc(
            "session/new",
            {"cwd": cwd or os.environ.get("USER_HOME", "/home/climbing"), "mcpServers": []},
        )
        sid = resp.get("sessionId") or resp.get("session_id")
        if sid:
            self._default_session_id = sid
        return resp

    # ---- Internal ----

    async def _rpc(self, method: str, params: dict) -> dict:
        """Simple RPC: send request, read lines until matching response."""
        self._msg_id += 1
        msg_id = str(self._msg_id)
        request = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        async with self._read_lock:
            self._proc.stdin.write((json.dumps(request) + "\n").encode())
            await self._proc.stdin.drain()
            start = time.time()
            while time.time() - start < 30:
                try:
                    raw = await asyncio.wait_for(self._proc.stdout.readline(), timeout=5)
                except TimeoutError:
                    # 单次读等待超时不等于调用失败——继续等到 30s 总预算
                    # （原实现让 TimeoutError 直接炸掉 RPC，冷启动 session/new
                    # 稍慢就误报"WS chat error:"）
                    continue
                if not raw:
                    break
                msg = self._parse(raw)
                if msg and str(msg.get("id", "")) == msg_id:
                    if "error" in msg:
                        raise Exception(msg["error"])
                    return msg.get("result", {})
        raise TimeoutError(f"ACP timeout: {method}")

    async def _prompt(self, text: str, session_id: str) -> dict:
        return await self._prompt_parts([{"type": "text", "text": text}], session_id)

    async def _prompt_parts(self, parts: list[dict], session_id: str) -> dict:
        """Send prompt and collect response + events in one read pass."""
        self._msg_id += 1
        msg_id = str(self._msg_id)
        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "session/prompt",
            "params": {"prompt": parts, "sessionId": session_id},
        }

        async with self._read_lock:
            # Drain stale events from previous RPC calls
            while True:
                try:
                    stale = await asyncio.wait_for(self._proc.stdout.readline(), timeout=0.3)
                    if stale:
                        m = self._parse(stale)
                        if m:
                            print(f"ACP drain: {m.get('method', '')}", flush=True)
                    else:
                        break
                except TimeoutError:
                    break

            self._proc.stdin.write((json.dumps(request) + "\n").encode())
            await self._proc.stdin.drain()

            collected = []
            response = {}
            start = time.time()

            while time.time() - start < 60:
                try:
                    raw = await asyncio.wait_for(self._proc.stdout.readline(), timeout=2)
                except TimeoutError:
                    if response and collected:
                        break
                    if response:
                        break
                    continue
                if not raw:
                    break

                msg = self._parse(raw)
                if not msg:
                    continue

                # Our response?
                if str(msg.get("id", "")) == msg_id:
                    response = msg.get("result", {})
                    if "error" in msg:
                        raise Exception(msg["error"])
                    # Keep reading for more chunks
                    continue

                # Event?
                method = msg.get("method", "")
                if method == "session/update":
                    update = msg.get("params", {}).get("update", {})
                    su = update.get("sessionUpdate", "")
                    if su == "agent_message_chunk":
                        content = update.get("content", "")
                        if (
                            isinstance(content, dict)
                            and content.get("type") == "text"
                            and content.get("text")
                        ):
                            collected.append(content["text"])
                            print(f"ACP chunk: {content['text'][:40]}", flush=True)
                    elif su == "usage_update" and response and collected:
                        break

            print(f"ACP: {len(collected)} chunks, response={bool(response)}", flush=True)
            response["response_text"] = "".join(collected)
            response["source"] = "acp"
            return response

    async def _prompt_parts_stream(self, parts: list[dict], session_id: str):
        """Send prompt and yield chunks as they arrive (async generator)."""
        self._msg_id += 1
        msg_id = str(self._msg_id)
        request = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "session/prompt",
            "params": {"prompt": parts, "sessionId": session_id},
        }

        async with self._read_lock:
            # Drain stale events
            while True:
                try:
                    stale = await asyncio.wait_for(self._proc.stdout.readline(), timeout=0.3)
                    if stale:
                        self._parse(stale)
                    else:
                        break
                except TimeoutError:
                    break

            self._proc.stdin.write((json.dumps(request) + "\n").encode())
            await self._proc.stdin.drain()

            response_done = False
            start = time.time()

            while time.time() - start < 60:
                try:
                    raw = await asyncio.wait_for(self._proc.stdout.readline(), timeout=2)
                except TimeoutError:
                    if response_done:
                        break
                    continue
                if not raw:
                    break

                msg = self._parse(raw)
                if not msg:
                    continue

                # Our response?
                if str(msg.get("id", "")) == msg_id:
                    if "error" in msg:
                        raise Exception(msg["error"])
                    response_done = True
                    continue

                # Event?
                method = msg.get("method", "")
                if method == "session/update":
                    update = msg.get("params", {}).get("update", {})
                    su = update.get("sessionUpdate", "")
                    if su == "agent_message_chunk":
                        content = update.get("content", "")
                        if (
                            isinstance(content, dict)
                            and content.get("type") == "text"
                            and content.get("text")
                        ):
                            yield content["text"]
                    elif su == "usage_update" and response_done:
                        break

    async def _cli(self, text: str) -> dict:
        try:
            proc = await asyncio.create_subprocess_exec(
                "hermes", "-z", text, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            return {
                "stopReason": "end_turn",
                "response_text": stdout.decode("utf-8", errors="replace").strip(),
                "source": "hermes-cli",
            }
        except TimeoutError:
            return {"stopReason": "timeout", "response_text": "请求超时", "source": "hermes-cli"}
        except Exception as e:
            return {"stopReason": "error", "response_text": f"错误: {e}", "source": "hermes-cli"}

    @staticmethod
    def _parse(raw: bytes) -> dict | None:
        try:
            return json.loads(raw.decode().strip())
        except Exception:
            return None

    def _get_agent_info(self) -> dict:
        return {"agentInfo": {"name": "hermes-agent", "version": "0.20.0"}, "protocolVersion": 1}


_acp: ACPProcess | None = None


def get_acp_process() -> ACPProcess:
    global _acp
    if _acp is None:
        _acp = ACPProcess()
    return _acp
