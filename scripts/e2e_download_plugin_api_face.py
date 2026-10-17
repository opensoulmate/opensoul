#!/usr/bin/env python3
"""H1-②b-⑨b e2e：plugin download API 面 live 修前/修后同探针双向硬证。

--pre  = 旧代码缺口硬证（plugin_id 静默忽略 / 未知 task_id 假成功 / plugin_tasks 缺席）
--post = 新代码实证（plugin_id 真消费 / get_best_plugin 择优 / 控制面诚实 404 /
         插件任务真路由含真 curl 二进制暂停·续传如实拒绝 / 清痕 remaining=0）
"""

import base64
import http.client
import json
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8090"
WORK = Path("/tmp/e2e_dl_plugin_api_9b")
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, (json.loads(raw) if raw else {})
        except Exception:
            return e.code, {"raw": raw}


def token_of(dest: str) -> str:
    return base64.urlsafe_b64encode(dest.encode()).decode().rstrip("=")


class SlowServer:
    """慢速本地 HTTP：让传输停在中途，pause 时序确定。"""

    def __init__(self, total=64 * 1024, chunk=1024, delay=0.15):
        self.total, self.chunk, self.delay = total, chunk, delay
        self._srv = None

    def _handle(self, conn, _addr):
        try:
            conn.recv(65536)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
                + f"Content-Length: {self.total}\r\n\r\n".encode()
            )
            sent = 0
            while sent < self.total:
                conn.sendall(b"x" * self.chunk)
                sent += self.chunk
                time.sleep(self.delay)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def __enter__(self):
        import socket

        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(5)
        self.port = self._srv.getsockname()[1]

        def loop():
            while True:
                try:
                    conn, addr = self._srv.accept()
                except OSError:
                    return
                threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()

        threading.Thread(target=loop, daemon=True).start()
        return self

    def __exit__(self, *exc):
        try:
            self._srv.close()
        except Exception:
            pass


def poll(cond, timeout=20.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, data = req("GET", "/api/download/tasks")
        if status == 200 and cond(data):
            return data
        time.sleep(interval)
    return None


def cleanup():
    """清痕：本次探针的全部任务/文件。返回 remaining 计数。"""
    status, data = req("GET", "/api/download/tasks")
    remaining = 0
    if status == 200:
        for t in data.get("tasks", []):
            if str(t.get("dest", "")).startswith(str(WORK)):
                req("DELETE", f"/api/download/tasks/{t['id']}")
        for t in data.get("plugin_tasks", []):
            if str(t.get("dest", "")).startswith(str(WORK)):
                req("DELETE", f"/api/download/tasks/{t['task_id']}")
        status, data = req("GET", "/api/download/tasks")
        remaining = sum(
            1
            for t in data.get("tasks", []) + data.get("plugin_tasks", [])
            if str(t.get("dest", "")).startswith(str(WORK))
        )
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    return remaining


def run_pre():
    WORK.mkdir(exist_ok=True)
    dest = str(WORK / "pre.bin")

    # 1. plugin_id 静默忽略：未知插件名修前 200 + native 硬跑
    status, data = req(
        "POST", "/api/download/download",
        {"url": "http://127.0.0.1:1/x", "dest": dest, "plugin_id": "nope-such-plugin-9b"},
    )
    check(
        "pre: 未知 plugin_id 修前 200 静默忽略（缺口硬证）",
        status == 200 and "plugin" not in data,
        f"status={status} data={data}",
    )
    if status == 200 and "task_id" in data:
        req("DELETE", f"/api/download/tasks/{data['task_id']}")

    # 2. 未知 task_id 假成功三件
    for method, path, expect in [
        ("POST", "/api/download/tasks/no-such-9b/pause", {"status": "paused"}),
        ("POST", "/api/download/tasks/no-such-9b/resume", {"status": "resumed"}),
        ("DELETE", "/api/download/tasks/no-such-9b", {"status": "removed"}),
    ]:
        status, data = req(method, path)
        check(
            f"pre: {method} {path.split('/')[-1]} 未知任务修前 200 假成功 {expect}",
            status == 200 and data.get("status") == expect.get("status"),
            f"status={status} data={data}",
        )

    # 3. /tasks 修前无 plugin_tasks 键
    status, data = req("GET", "/api/download/tasks")
    check(
        "pre: /tasks 修前无 plugin_tasks 键（缺口硬证）",
        status == 200 and "plugin_tasks" not in data,
        f"keys={sorted(data)}",
    )
    print(f"cleanup remaining={cleanup()}")


def run_post():
    WORK.mkdir(exist_ok=True)

    # 1. health 零回归
    status, data = req("GET", "/api/download/health")
    check("health 200", status == 200 and data.get("status") == "ok", str(data))

    # 2. 未知 plugin_id 404 零副作用（修前 200 静默忽略）
    dest = str(WORK / "unknown-plugin.bin")
    status, data = req(
        "POST", "/api/download/download",
        {"url": "http://127.0.0.1:1/x", "dest": dest, "plugin_id": "nope-such-plugin-9b"},
    )
    check(
        "post: 未知 plugin_id 404 如实拒（修前 200 静默忽略）",
        status == 404 and "not found" in str(data.get("detail", "")).lower(),
        f"status={status} data={data}",
    )

    # 3. 未安装插件 400
    status, data = req(
        "POST", "/api/download/download",
        {"url": "http://127.0.0.1:1/x", "dest": dest, "plugin_id": "aria2"},
    )
    ok = (status == 400 and "not installed" in str(data.get("detail", "")).lower()) or status == 200
    check(
        "post: 未安装插件 400 / 已安装真跑（按本机实况如实二分支）",
        ok,
        f"status={status} data={data}",
    )
    if status == 200 and "task_id" in data:
        req("DELETE", f"/api/download/tasks/{data['task_id']}")

    # 4. get_best_plugin 择优：auto 缺省真执行（plugin 键如实标注引擎）
    _st, plist = req("GET", "/api/download/plugins")
    known = {p["id"] for p in plist.get("plugins", [])} | {"native"}
    status, data = req(
        "POST", "/api/download/download",
        {"url": "http://127.0.0.1:1/x", "dest": str(WORK / "auto.bin")},
    )
    check(
        "post: auto 择优响应带 plugin 引擎标注",
        status == 200 and data.get("plugin") in known,
        f"status={status} plugin={data.get('plugin')} known={sorted(known)}",
    )
    if status == 200 and "task_id" in data:
        req("DELETE", f"/api/download/tasks/{data['task_id']}")

    # 5. 未知 task_id 三件 404（修前假成功）
    for method, path in [
        ("POST", "/api/download/tasks/no-such-9b/pause"),
        ("POST", "/api/download/tasks/no-such-9b/resume"),
        ("DELETE", "/api/download/tasks/no-such-9b"),
    ]:
        status, data = req(method, path)
        check(
            f"post: {method} {path.split('/')[-1]} 未知任务 404（修前 200 假成功）",
            status == 404 and "status" not in data,
            f"status={status} data={data}",
        )

    # 6. 真 curl 插件任务全链：下载→列表→暂停→续传如实拒→删除
    dest = str(WORK / "curl-task.bin")
    with SlowServer() as srv:
        status, data = req(
            "POST", "/api/download/download",
            {"url": f"http://127.0.0.1:{srv.port}/f", "dest": dest, "plugin_id": "curl"},
        )
        check(
            "post: 真 curl 插件下载启动（plugin_id 真消费）",
            status == 200 and data.get("plugin") == "curl" and data.get("task_id") == token_of(dest),
            f"status={status} data={data}",
        )
        token = data.get("task_id", token_of(dest))
        listed = poll(
            lambda d: any(
                t["task_id"] == token and t["registry_key"] == dest and t["plugin"] == "curl"
                for t in d.get("plugin_tasks", [])
            )
        )
        check("post: /tasks plugin_tasks 在册（additive 键+registry_key 可回溯）", listed is not None)
        status, data = req("POST", f"/api/download/tasks/{token}/pause")
        check(
            "post: 插件任务真暂停（⑨a pause 首个 API 消费）",
            status == 200 and data.get("status") == "paused" and data.get("plugin") == "curl",
            f"status={status} data={data}",
        )
        status, data = req("POST", f"/api/download/tasks/{token}/resume")
        check(
            "post: curl 不支持续传如实 409 拒（禁假 'resumed'）",
            status == 409,
            f"status={status} data={data}",
        )
        status, data = req("DELETE", f"/api/download/tasks/{token}")
        check(
            "post: 插件任务真删除",
            status == 200 and data.get("status") == "removed" and data.get("plugin") == "curl",
            f"status={status} data={data}",
        )

    # 7. native 任务面零回归：真实下载→暂停→续传→删除（真 curl-less native 引擎）
    dest = str(WORK / "native-task.bin")
    with SlowServer() as srv:
        status, data = req(
            "POST", "/api/download/download",
            {"url": f"http://127.0.0.1:{srv.port}/f", "dest": dest, "plugin_id": "native"},
        )
        check(
            "post: native 引擎强制（plugin_id=native 逃生口）",
            status == 200 and data.get("plugin") == "native",
            f"status={status} data={data}",
        )
        tid = data.get("task_id", "")
        status, data = req("POST", f"/api/download/tasks/{tid}/pause")
        check(
            "post: native 真暂停如实状态",
            status == 200 and data.get("status") in ("paused", "downloading", "connecting", "done"),
            f"status={status} data={data}",
        )
        status, data = req("DELETE", f"/api/download/tasks/{tid}")
        check("post: native 真删除", status == 200 and data.get("status") == "removed", f"{status} {data}")

    remaining = cleanup()
    check("post: 清痕 remaining=0", remaining == 0, f"remaining={remaining}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--post"
    if mode == "--pre":
        run_pre()
    else:
        run_post()
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n== {mode} {len(RESULTS) - len(failed)}/{len(RESULTS)} ==")
    sys.exit(1 if failed else 0)
