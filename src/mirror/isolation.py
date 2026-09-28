"""Real process isolation for OpenMirror sandboxes — bubblewrap namespace sandbox.

SUMMARY.md §五 mirror差距：「真隔离沙箱（E2B snapshot-fork/网络策略热更新）| E2B（现为目录级假沙箱）」。
本模块是"目录级假沙箱"的真隔离替代：命令在独立 user/pid/uts/ipc/net 命名空间内执行，
文件系统只见 /workspace（沙箱数据目录）+ 只读系统目录，宿主其余路径（/home 等）完全不可见。

设计来源（100-agent调研，逐点对照）：
- E2B snapshot-fork 语义 → snapshot() 拷贝 workspace + fork_from_snapshot() 独立分叉（见 sandbox.py）
- E2B 网络策略热更新 → network_policy 每次 exec 时读取，set_network_policy 改后立即生效，无需重建沙箱
- Letta §4.2 fail-closed → 无 bubblewrap / 命名空间不可用时 IsolationUnavailable 拒绝执行，
  绝不静默降级为"无隔离执行"（mem0 §1.1 失败必须可见）
- AIHawk SHOWN/SENT 双预算 → 输出超出预算必须显式标记截断（truncated 标志 + 明示字节数的标记行），
  不静默丢弃
- 环境变量隔离（--clearenv + 白名单）→ 宿主凭据类环境变量默认不进沙箱
  （顺带收窄 dev-report 遗留观察项"环境变量密钥外发误伤面"）

适配留痕（非照抄 E2B——E2B 是云端 Firecracker microVM，本实现按用户约束"纯开源+本地处理"
改用 Linux namespace/bubblewrap，语义对应：microVM 文件系统隔离→mount namespace、
snapshot-fork→workspace 目录快照拷贝、网络策略热更新→per-exec 读取策略）。
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

# ── 预算常量（AIHawk SHOWN/SENT 双预算语义）──────────────────────
MAX_OUTPUT_BYTES = 262_144  # SENT 预算：进模型/进日志的上限
SHOWN_PREVIEW_CHARS = 2_000  # SHOWN 预算：预览展示上限
DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S = 300

# 宿主环境变量白名单（--clearenv 后只放行这些；凭据类一律不进沙箱）
DEFAULT_ENV_ALLOWLIST = ("LANG", "LC_ALL", "TERM", "TZ")

BWRAP_CANDIDATES = ("bwrap", "/usr/bin/bwrap")

_NETWORK_POLICIES = ("allow", "deny")


class IsolationUnavailable(RuntimeError):
    """真隔离不可用——fail-closed：拒绝执行，绝不静默无隔离运行（Letta §4.2）。"""


class SandboxExecError(RuntimeError):
    """执行请求本身非法（未知网络策略等）。"""


@dataclass
class ExecResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_total_bytes: int
    stderr_total_bytes: int
    timed_out: bool
    duration_s: float
    network_policy: str
    isolation: str = "bubblewrap"

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_preview": self.stdout[:SHOWN_PREVIEW_CHARS],
            "stderr_preview": self.stderr[:SHOWN_PREVIEW_CHARS],
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "stdout_total_bytes": self.stdout_total_bytes,
            "stderr_total_bytes": self.stderr_total_bytes,
            "timed_out": self.timed_out,
            "duration_s": round(self.duration_s, 3),
            "network_policy": self.network_policy,
            "isolation": self.isolation,
        }


# ── 探测 seam（测试可替换）────────────────────────────────────
_probe_lock = threading.Lock()
_probe_cache: bool | None = None


def find_bwrap() -> str | None:
    for cand in BWRAP_CANDIDATES:
        path = (
            shutil.which(cand) if os.sep not in cand else (cand if os.path.isfile(cand) else None)
        )
        if path:
            return path
    return None


def probe_isolation(force: bool = False) -> bool:
    """探测真隔离是否可用（缓存）。不可用时 run_in_sandbox 会 fail-closed 拒绝执行。"""
    global _probe_cache
    with _probe_lock:
        if _probe_cache is not None and not force:
            return _probe_cache
        bwrap = find_bwrap()
        if not bwrap:
            _probe_cache = False
            return False
        try:
            proc = subprocess.run(
                [
                    bwrap,
                    "--unshare-user",
                    "--unshare-pid",
                    "--die-with-parent",
                    "--ro-bind",
                    "/usr",
                    "/usr",
                    "--symlink",
                    "usr/bin",
                    "/bin",
                    "--symlink",
                    "usr/lib",
                    "/lib",
                    "--symlink",
                    "usr/lib64",
                    "/lib64",
                    "/bin/true",
                ],
                capture_output=True,
                timeout=10,
            )
            _probe_cache = proc.returncode == 0
        except Exception:
            _probe_cache = False
        return _probe_cache


def reset_probe_cache() -> None:
    global _probe_cache
    with _probe_lock:
        _probe_cache = None


def _system_task_count() -> int:
    """宿主当前 task 总数（RLIMIT_NPROC 按线程计数——/proc 只列进程、Node/Chromium 线程数远超进程数，
    从 /proc/loadavg 第三段 '运行数/总task数' 取真实总数）。"""
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        return int(parts[3].split("/")[1])
    except Exception:
        return 4096


def nproc_limit() -> int:
    """进程/线程数上限：宿主当前 task 总数 + 1024 余量（下限4096）——防失控fork又不误伤正常执行。
    坑留痕：静态小值（如128/1024）在繁忙宿主上低于存量线程数，fork 直接 EAGAIN 拒绝执行。"""
    return max(4096, _system_task_count() + 1024)


def validate_policy(network_policy: str) -> str:
    if network_policy not in _NETWORK_POLICIES:
        raise SandboxExecError(
            f"unknown network_policy {network_policy!r} (allowed: {_NETWORK_POLICIES})"
        )
    return network_policy


def build_bwrap_argv(
    bwrap: str,
    workspace: str,
    command: str,
    network_policy: str = "deny",
    extra_env: dict[str, str] | None = None,
    env_allowlist: tuple[str, ...] = DEFAULT_ENV_ALLOWLIST,
) -> list[str]:
    """构造 bubblewrap 命令行——单一真源，测试直接断言语义（无网=--unshare-net）。"""
    validate_policy(network_policy)
    argv = [
        bwrap,
        "--unshare-user",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--dir",
        "/etc",
    ]
    if network_policy == "deny":
        argv += ["--unshare-net"]
    # net allow 时放行 DNS/TLS 解析所需文件（只读）
    for etc_file in ("/etc/resolv.conf", "/etc/hosts"):
        if os.path.exists(etc_file):
            argv += ["--ro-bind", etc_file, etc_file]
    if os.path.isdir("/etc/ssl"):
        argv += ["--ro-bind", "/etc/ssl", "/etc/ssl"]
    argv += [
        "--bind",
        workspace,
        "/workspace",
        "--chdir",
        "/workspace",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/workspace",
        "--setenv",
        "TMPDIR",
        "/tmp",
    ]
    for key in env_allowlist:
        if key in os.environ:
            argv += ["--setenv", key, os.environ[key]]
    for key, value in (extra_env or {}).items():
        argv += ["--setenv", str(key), str(value)]
    argv += ["--new-session", "--die-with-parent", "/bin/sh", "-c", command]
    return argv


def _drain(stream, cap: int, sink: list[bytes], counter: list[int]) -> None:
    """读管道：cap 内保留、超出只计数（防 OOM，截断字节数如实计数）。"""
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            counter[0] += len(chunk)
            kept = sum(len(b) for b in sink)
            if kept < cap:
                sink.append(chunk[: cap - kept])
    except Exception:
        pass


def _format_truncated(
    sink: list[bytes], total: int, cap: int, encoding: str = "utf-8"
) -> tuple[str, bool]:
    text = b"".join(sink).decode(encoding, errors="replace")
    if total <= cap:
        return text, False
    marker = f"\n…[{total - cap} more characters truncated — output capped at {cap} bytes]"
    return text + marker, True


def run_in_sandbox(
    command: str,
    workspace: str,
    *,
    network_policy: str = "deny",
    timeout: float = DEFAULT_TIMEOUT_S,
    env: dict[str, str] | None = None,
    env_allowlist: tuple[str, ...] = DEFAULT_ENV_ALLOWLIST,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> ExecResult:
    """在真隔离环境内执行命令（同步）。

    fail-closed：bubblewrap 不可用/命名空间被禁时抛 IsolationUnavailable，
    绝不退化为宿主直跑（Letta §4.2 无降级）。
    """
    validate_policy(network_policy)
    if not probe_isolation():
        raise IsolationUnavailable(
            "bubblewrap/namespace isolation unavailable — refusing to execute "
            "(fail-closed, no unisolated fallback)"
        )
    bwrap = find_bwrap()
    if not bwrap:
        raise IsolationUnavailable("bubblewrap binary not found — refusing to execute")

    workspace = str(workspace)
    os.makedirs(workspace, exist_ok=True)
    timeout = max(1.0, min(float(timeout), MAX_TIMEOUT_S))
    argv = build_bwrap_argv(
        bwrap,
        workspace,
        command,
        network_policy=network_policy,
        extra_env=env,
        env_allowlist=env_allowlist,
    )

    started = time.monotonic()
    # 资源上限：优先 prlimit 包装（线程安全——preexec_fn 在多线程宿主
    # [FastAPI asyncio.to_thread] 有 fork 死锁风险，仅 prlimit 缺失时回退）
    prlimit = shutil.which("prlimit")
    if prlimit:
        argv = [
            prlimit,
            "--cpu=60",
            f"--fsize={64 * 1024 * 1024}",
            f"--nproc={nproc_limit()}",
            "--nofile=256",
            f"--as={2 * 1024 * 1024 * 1024}",
        ] + argv
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin"},  # bwrap 自身进程环境最小化
        start_new_session=True,  # 独立进程组，超时可整组回收
        preexec_fn=None if prlimit else _apply_rlimits,
    )
    out_sink: list[bytes] = []
    err_sink: list[bytes] = []
    out_count = [0]
    err_count = [0]
    t_out = threading.Thread(
        target=_drain, args=(proc.stdout, max_output_bytes, out_sink, out_count), daemon=True
    )
    t_err = threading.Thread(
        target=_drain, args=(proc.stderr, max_output_bytes, err_sink, err_count), daemon=True
    )
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    t_out.join(timeout=5)
    t_err.join(timeout=5)

    stdout, stdout_trunc = _format_truncated(out_sink, out_count[0], max_output_bytes)
    stderr, stderr_trunc = _format_truncated(err_sink, err_count[0], max_output_bytes)
    if timed_out:
        stderr += f"\n…[sandbox exec timed out after {timeout:.0f}s — process group killed]"
        stderr_trunc = True  # 显式标记：尾部被截+终止原因可见（失败必须可见）

    exit_code = proc.returncode if proc.returncode is not None else -signal.SIGKILL
    if timed_out and exit_code == 0:
        exit_code = -signal.SIGKILL

    return ExecResult(
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_trunc,
        stderr_truncated=stderr_trunc,
        stdout_total_bytes=out_count[0],
        stderr_total_bytes=err_count[0],
        timed_out=timed_out,
        duration_s=time.monotonic() - started,
        network_policy=network_policy,
    )


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _apply_rlimits() -> None:  # pragma: no cover — 在子进程内执行
    """资源上限：防沙箱内失控进程拖垮宿主（SWE-agent per-instance 成本上限语义）。"""
    import resource

    limits = (
        (resource.RLIMIT_CPU, 60),
        (resource.RLIMIT_FSIZE, 64 * 1024 * 1024),
        (resource.RLIMIT_NPROC, nproc_limit()),
        (resource.RLIMIT_NOFILE, 256),
        (resource.RLIMIT_AS, 2 * 1024 * 1024 * 1024),
    )
    for res, value in limits:
        try:
            resource.setrlimit(res, (value, value))
        except Exception:
            pass
