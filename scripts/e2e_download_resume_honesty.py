#!/usr/bin/env python3
"""H1-②b-⑨a e2e：live 零回归 + 真二进制（curl -C -）暂停/续传实证。

本拍零 API 变化（"先修假承诺"前置件），live 面=服务重启零回归；
续传真实性用真 curl 二进制 + 本地支持 Range 的慢速服务端到端验证。
"""

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "http://127.0.0.1:8090"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def http_get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode())


# ─── 1. live 零回归 ───────────────────────────────────────────────


def live_zero_regression():
    try:
        status, data = http_get("/api/download/health")
        check("health 200", status == 200 and data.get("status") == "ok", str(data))
    except Exception as e:
        check("health 200", False, repr(e))
        return

    try:
        status, data = http_get("/api/download/plugins")
        plugins = data.get("plugins", [])
        ids = [p["id"] for p in plugins]
        shape_ok = all(
            set(p) >= {"id", "name", "status", "supports_resume", "supports_p2p", "priority"}
            for p in plugins
        )
        check(
            "plugins 列表契约不变",
            status == 200 and shape_ok and "aria2" in ids and "curl" in ids,
            f"ids={ids}",
        )
    except Exception as e:
        check("plugins 列表契约不变", False, repr(e))

    try:
        status, data = http_get("/api/download/tasks")
        check("tasks 列表契约不变", status == 200 and "tasks" in data, f"n={len(data.get('tasks', []))}")
    except Exception as e:
        check("tasks 列表契约不变", False, repr(e))


# ─── 2. 真二进制续传实证（真 curl + 真 Range 服务） ─────────────────


class RangeServer:
    """支持 Range 的慢速本地服务：让 pause 落在传输中途。"""

    def __init__(self, payload, chunk=2048, delay=0.05):
        self.payload = payload
        self.chunk = chunk
        self.delay = delay
        self.requests = []

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            req = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        except Exception:
            writer.close()
            return
        head = req.decode("latin1").split("\r\n")
        method = head[0].split(" ")[0]
        headers = {}
        for line in head[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        self.requests.append((method, headers.get("range", "")))

        start = 0
        status_line = b"HTTP/1.1 200 OK"
        extra = b""
        rng = headers.get("range", "")
        if rng.startswith("bytes="):
            start = int(rng.split("=")[1].split("-")[0])
            status_line = b"HTTP/1.1 206 Partial Content"
            extra = (
                f"Content-Range: bytes {start}-{len(self.payload) - 1}/{len(self.payload)}\r\n"
            ).encode()
        body = self.payload[start:]
        try:
            writer.write(
                status_line
                + b"\r\n"
                + extra
                + f"Content-Length: {len(body)}\r\nAccept-Ranges: bytes\r\nConnection: close\r\n\r\n".encode()
            )
            if method != "HEAD":
                for i in range(0, len(body), self.chunk):
                    writer.write(body[i : i + self.chunk])
                    await writer.drain()
                    await asyncio.sleep(self.delay)
            await writer.drain()
        except (ConnectionError, BrokenPipeError):
            pass  # 对端被 pause 杀掉=预期内
        writer.close()


async def real_binary_resume():
    from src.api.download_plugins import CurlResumePlugin

    payload = b"0123456789abcdef" * 4096  # 64KB
    dest = Path("/tmp/e2e_dl_resume_09a.bin")
    if dest.exists():
        dest.unlink()
    p = CurlResumePlugin()
    d = str(dest)
    async with RangeServer(payload) as server:
        url = f"http://127.0.0.1:{server.port}/f"
        bg = asyncio.create_task(p.download(url, d))
        try:
            for _ in range(500):
                # 等半成品真落盘再 pause：curl stdio 有缓冲，暂停瞬间 0 字节时
                # `curl -C -` 会从 0 重来（无 Range），测不到 continuation
                if d in p._running and dest.exists() and dest.stat().st_size > 0:
                    break
                await asyncio.sleep(0.02)
            ok = await p.pause(d)
            check("pause 真停传输", ok is True)
            progress = await bg
            check(
                "被 pause 的 download() 如实 paused 非 error",
                progress.status == "paused" and progress.error is None,
                f"status={progress.status} error={progress.error}",
            )
            check("半成品保留", dest.exists() and 0 < dest.stat().st_size < len(payload), f"size={dest.stat().st_size if dest.exists() else 0}")

            ok = await p.resume_download(d)
            check("resume_download 返回真", ok is True)
            for _ in range(500):
                if dest.exists() and dest.read_bytes() == payload:
                    break
                await asyncio.sleep(0.02)
            got = dest.read_bytes() if dest.exists() else b""
            check("续传后逐字节完整", got == payload, f"got={len(got)}/{len(payload)}")
            gets = [r for r in server.requests if r[0] == "GET"]
            check(
                "续传=二次 GET 带 Range（真 continuation）",
                len(gets) == 2 and gets[1][1].startswith("bytes="),
                f"requests={server.requests}",
            )
        finally:
            await p.cancel(d)
            bg.cancel()
    if dest.exists():
        dest.unlink()


def main():
    live_zero_regression()
    asyncio.run(real_binary_resume())
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n== {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
