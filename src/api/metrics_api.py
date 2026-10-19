"""Prometheus-compatible metrics endpoint for OpenSoul.

Exposes standard HTTP request metrics, organ health gauges,
and system resource metrics in Prometheus exposition format.

Compatible with Grafana dashboards and Prometheus scraping.
"""

import logging
import os
import platform
import time
from collections import defaultdict
from threading import Lock

from fastapi import APIRouter, Response

logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)
router = APIRouter()

# ── In-process metrics collector ──────────────────────────────

_lock = Lock()

# Counters
_request_count: dict[str, int] = defaultdict(int)  # method_path -> count
_error_count: dict[str, int] = defaultdict(int)  # status_code -> count
_organ_health: dict[str, float] = {}  # organ -> 1.0(ok) / 0.0(down)

# Histograms (simple bucket approach)
_latency_buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
_latency_counts: dict[str, list[int]] = defaultdict(lambda: [0] * (len(_latency_buckets) + 1))
_latency_sum: dict[str, float] = defaultdict(float)
_latency_count: dict[str, int] = defaultdict(int)

# Startup time
_start_time = time.time()


def uptime_seconds() -> float:
    """OpenSoul 进程运行时长（秒）——进程 uptime 单一源（DEC-㉘a-① 真源补现 B1 语义）。

    /metrics 的 opensoul_uptime_seconds 与 /api/system/overview 的 uptime_seconds
    同走本口（禁两套解释漂移）。
    """
    return time.time() - _start_time


def record_request(
    method: str,
    path: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    """Record an HTTP request for metrics. Called from middleware."""
    # Normalize path — strip query params, limit cardinality
    clean = _normalize_path(path)
    key = f"{method}_{clean}"

    with _lock:
        _request_count[key] += 1

        if status_code >= 400:
            _error_count[str(status_code)] += 1

        # Latency histogram
        bucket_idx = len(_latency_buckets)  # +Inf bucket
        for i, boundary in enumerate(_latency_buckets):
            if duration_seconds <= boundary:
                bucket_idx = i
                break
        _latency_counts[key][bucket_idx] += 1
        _latency_sum[key] += duration_seconds
        _latency_count[key] += 1


def record_organ_health(organ: str, healthy: bool) -> None:
    """Record organ health status as a gauge."""
    with _lock:
        _organ_health[organ] = 1.0 if healthy else 0.0


def _normalize_path(path: str) -> str:
    """Normalize URL path to limit cardinality in metrics."""
    # Strip query string
    path = path.split("?")[0]
    # Replace UUIDs and numeric IDs with placeholder
    import re

    path = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{id}",
        path,
    )
    path = re.sub(r"/\d+", "/{id}", path)
    # Only track /api/* paths
    if not path.startswith("/api"):
        return "other"
    # Limit depth
    parts = path.split("/")
    if len(parts) > 5:
        path = "/".join(parts[:5]) + "/..."
    return path


# ── Prometheus exposition format ──────────────────────────────


def _format_metrics() -> str:
    """Generate Prometheus exposition format text."""
    lines: list[str] = []
    time.time()

    # ── Metadata ──
    lines.append("# HELP opensoul_info OpenSoul instance information")
    lines.append("# TYPE opensoul_info gauge")
    lines.append(
        f'opensoul_info{{version="2.0",python="{platform.python_version()}",'
        f'os="{platform.system()}"}} 1'
    )
    lines.append("")

    # ── Uptime ──
    lines.append("# HELP opensoul_uptime_seconds Time since OpenSoul started")
    lines.append("# TYPE opensoul_uptime_seconds gauge")
    lines.append(f"opensoul_uptime_seconds {uptime_seconds():.1f}")
    lines.append("")

    # ── Process metrics ──
    try:
        import resource

        ru = resource.getrusage(resource.RUSAGE_SELF)
        lines.append("# HELP opensoul_process_resident_memory_bytes Resident memory size")
        lines.append("# TYPE opensoul_process_resident_memory_bytes gauge")
        lines.append(f"opensoul_process_resident_memory_bytes {ru.ru_maxrss * 1024}")

        lines.append("# HELP opensoul_process_cpu_seconds_total Total CPU time")
        lines.append("# TYPE opensoul_process_cpu_seconds_total counter")
        lines.append(f"opensoul_process_cpu_seconds_total {ru.ru_utime + ru.ru_stime:.3f}")
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)
    lines.append("")

    # ── HTTP request counter ──
    lines.append("# HELP opensoul_http_requests_total Total HTTP requests")
    lines.append("# TYPE opensoul_http_requests_total counter")
    with _lock:
        for key, count in sorted(_request_count.items()):
            method, path = key.split("_", 1)
            lines.append(f'opensoul_http_requests_total{{method="{method}",path="{path}"}} {count}')
    lines.append("")

    # ── HTTP error counter ──
    lines.append("# HELP opensoul_http_errors_total Total HTTP error responses")
    lines.append("# TYPE opensoul_http_errors_total counter")
    with _lock:
        for code, count in sorted(_error_count.items()):
            lines.append(f'opensoul_http_errors_total{{status="{code}"}} {count}')
    lines.append("")

    # ── HTTP request duration histogram ──
    lines.append("# HELP opensoul_http_request_duration_seconds HTTP request latency")
    lines.append("# TYPE opensoul_http_request_duration_seconds histogram")
    with _lock:
        for key in sorted(_latency_counts.keys()):
            method, path = key.split("_", 1)
            cumulative = 0
            for i, boundary in enumerate(_latency_buckets):
                cumulative += _latency_counts[key][i]
                lines.append(
                    f'opensoul_http_request_duration_seconds_bucket{{method="{method}",'
                    f'path="{path}",le="{boundary}"}} {cumulative}'
                )
            cumulative += _latency_counts[key][-1]
            lines.append(
                f'opensoul_http_request_duration_seconds_bucket{{method="{method}",'
                f'path="{path}",le="+Inf"}} {cumulative}'
            )
            lines.append(
                f'opensoul_http_request_duration_seconds_sum{{method="{method}",'
                f'path="{path}"}} {_latency_sum[key]:.6f}'
            )
            lines.append(
                f'opensoul_http_request_duration_seconds_count{{method="{method}",'
                f'path="{path}"}} {_latency_count[key]}'
            )
    lines.append("")

    # ── Organ health gauge ──
    lines.append("# HELP opensoul_organ_health Organ health status (1=ok, 0=down)")
    lines.append("# TYPE opensoul_organ_health gauge")
    with _lock:
        for organ, val in sorted(_organ_health.items()):
            lines.append(f'opensoul_organ_health{{organ="{organ}"}} {val}')
    lines.append("")

    # ── System resource gauges ──
    try:
        import shutil

        mem = _get_memory()
        disk = shutil.disk_usage("/")
        cpu_count = os.cpu_count() or 1

        lines.append("# HELP opensoul_system_cpu_count Number of CPU cores")
        lines.append("# TYPE opensoul_system_cpu_count gauge")
        lines.append(f"opensoul_system_cpu_count {cpu_count}")

        if mem:
            lines.append("# HELP opensoul_system_memory_total_bytes Total system memory")
            lines.append("# TYPE opensoul_system_memory_total_bytes gauge")
            lines.append(f"opensoul_system_memory_total_bytes {mem['total']}")
            lines.append("# HELP opensoul_system_memory_available_bytes Available system memory")
            lines.append("# TYPE opensoul_system_memory_available_bytes gauge")
            lines.append(f"opensoul_system_memory_available_bytes {mem['available']}")

        lines.append("# HELP opensoul_system_disk_total_bytes Total disk space")
        lines.append("# TYPE opensoul_system_disk_total_bytes gauge")
        lines.append(f"opensoul_system_disk_total_bytes {disk.total}")
        lines.append("# HELP opensoul_system_disk_free_bytes Free disk space")
        lines.append("# TYPE opensoul_system_disk_free_bytes gauge")
        lines.append(f"opensoul_system_disk_free_bytes {disk.free}")
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)

    return "\n".join(lines) + "\n"


def _get_memory() -> dict | None:
    """Read system memory from /proc/meminfo (Linux)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]  # kB value
                    info[key] = int(val) * 1024  # Convert to bytes
        return {
            "total": info.get("MemTotal", 0),
            "available": info.get("MemAvailable", 0),
            "used": info.get("MemTotal", 0) - info.get("MemAvailable", 0),
        }
    except Exception:
        return None


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/metrics")
async def prometheus_metrics():
    """Prometheus-compatible metrics endpoint.

    Scrape this endpoint with Prometheus to collect OpenSoul metrics.
    Compatible with Grafana dashboards.
    """
    body = _format_metrics()
    return Response(
        content=body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/metrics/health")
async def metrics_health():
    """Metrics module health check."""
    return {
        "status": "ok",
        "component": "OpenMetrics",
        "description": "Prometheus-compatible metrics for OpenSoul",
        "tracked_endpoints": len(_request_count),
        "tracked_organs": len(_organ_health),
    }
