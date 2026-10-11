"""Soma Discovery Engine — Local software and service discovery.

Scans the local system for:
- Running processes (via ps aux)
- CLI tools installed on PATH (via which)
- Listening network services (via ss -tlnp)

Provides a unified SoftwareAdapter interface for extending discovery
with custom scanners, and exposes all results via REST API.

Results are cached for 60 seconds to avoid repeated system scans.
"""

import asyncio
import fnmatch
import logging
import os
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

# Optional async dependencies — graceful fallback if not installed
try:
    import httpx as _httpx
except ImportError:
    _httpx = None  # type: ignore[assignment]

try:
    import aiosqlite
except ImportError:
    aiosqlite = None  # type: ignore[assignment]

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]

try:
    from watchfiles import awatch as _awatch
except ImportError:
    _awatch = None

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Cache ────────────────────────────────────────────────────────────────

CACHE_TTL = 60  # seconds


class _Cache:
    """Simple time-based cache for scan results."""

    def __init__(self, ttl: int = CACHE_TTL):
        self._store: dict[str, tuple[float, Any]] = {}
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.time() - ts > self._ttl:
            del self._store[key]
            return None
        return data

    def set(self, key: str, data: Any) -> None:
        self._store[key] = (time.time(), data)

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._store.clear()
        else:
            self._store.pop(key, None)


_cache = _Cache()
# Common CLI tool descriptions (static, no probing needed)
_TOOL_DESCRIPTIONS: dict[str, str] = {
    "python3": "Python 3 interpreter",
    "python": "Python interpreter",
    "node": "Node.js JavaScript runtime",
    "npm": "Node.js package manager",
    "yarn": "JavaScript package manager",
    "pnpm": "Fast, disk-space-efficient package manager",
    "git": "Distributed version control system",
    "docker": "Container runtime and orchestration",
    "podman": "Daemonless container engine",
    "kubectl": "Kubernetes cluster management",
    "cargo": "Rust package manager and build tool",
    "rustc": "Rust compiler",
    "gcc": "GNU C compiler",
    "g++": "GNU C++ compiler",
    "clang": "LLVM C/C++ compiler",
    "make": "GNU build automation tool",
    "cmake": "Cross-platform build system generator",
    "go": "Go programming language toolchain",
    "java": "Java application launcher",
    "javac": "Java compiler",
    "curl": "Command-line HTTP client",
    "wget": "Network file retriever",
    "ssh": "OpenSSH remote login client",
    "rsync": "Fast file synchronization tool",
    "tar": "Tape archive utility",
    "unzip": "Extraction utility for .zip archives",
    "jq": "Command-line JSON processor",
    "sed": "Stream editor for text transformation",
    "awk": "Pattern-directed text processing",
    "grep": "Pattern search utility",
    "rg": "ripgrep — fast recursive grep",
    "fd": "fd-find — fast alternative to find",
    "htop": "Interactive process viewer",
    "vim": "Vi IMproved text editor",
    "nvim": "Neovim text editor",
    "code": "Visual Studio Code editor",
    "nano": "Simple terminal text editor",
    "tmux": "Terminal multiplexer",
    "screen": "Terminal session manager",
    "uv": "Fast Python package installer",
    "pip": "Python package installer",
    "pip3": "Python 3 package installer",
    "conda": "Package and environment manager",
    "ffmpeg": "Multimedia framework for audio/video",
    "sqlite3": "SQLite database CLI",
    "psql": "PostgreSQL interactive terminal",
    "mysql": "MySQL command-line client",
    "redis-cli": "Redis command-line interface",
    "mongosh": "MongoDB shell",
    "nginx": "High-performance HTTP server",
    "systemctl": "Systemd service manager",
    "journalctl": "Systemd journal viewer",
    "flatpak": "Linux application sandboxing framework",
    "snap": "Universal Linux package manager",
    "paru": "AUR helper for Arch Linux",
    "pacman": "Arch Linux package manager",
    "yay": "Yet Another AUR helper",
    "top": "System process monitor",
    "df": "Disk space report",
    "du": "Disk usage estimator",
    "free": "Memory usage report",
    "lsof": "List open files and sockets",
    "strace": "System call tracer",
    "ltrace": "Library call tracer",
    "gdb": "GNU debugger",
    "valgrind": "Memory debugging and profiling",
    "nmap": "Network exploration and security scanner",
    "dig": "DNS lookup utility",
    "ip": "Network interface configuration",
    "ss": "Socket statistics",
    "ping": "Network reachability test",
    "traceroute": "Network route tracer",
    "hermes": "Hermes AI agent CLI",
}


# ── Data Models ──────────────────────────────────────────────────────────


class ProcessInfo(BaseModel):
    pid: int
    user: str
    cpu: float
    mem: float
    command: str
    executable: str | None = None
    description: str = ""


class CLITool(BaseModel):
    name: str
    path: str
    version: str | None = None
    description: str = ""


class ServiceInfo(BaseModel):
    protocol: str
    local_address: str
    local_port: int
    state: str
    process_name: str | None = None
    pid: int | None = None
    description: str = ""


class ScanResult(BaseModel):
    scan_time: float
    processes: list[ProcessInfo]
    cli_tools: list[CLITool]
    services: list[ServiceInfo]
    adapters: list[str]


class AdapterInfo(BaseModel):
    name: str
    description: str
    capabilities: list[str] = []
    configured: bool = False
    registered_at: float


class AdapterExecuteRequest(BaseModel):
    action: str
    params: dict[str, Any] = {}


class AdapterExecuteResponse(BaseModel):
    adapter: str
    action: str
    success: bool
    result: Any = None
    error: str | None = None


# ── New Adapter Request/Response Models ────────────────────────────────


class RestConfigureRequest(BaseModel):
    base_url: str
    headers: dict[str, str] = {}
    auth_token: str | None = None
    timeout: float = 30.0


class RestProbeRequest(BaseModel):
    base_url: str
    headers: dict[str, str] = {}
    auth_token: str | None = None


class RestRequestModel(BaseModel):
    method: str = "GET"
    path: str = "/"
    headers: dict[str, str] = {}
    body: Any = None
    timeout: float = 30.0


class DatabaseConfigureRequest(BaseModel):
    db_type: str  # "sqlite" or "postgresql"
    connection_string: str  # file path for sqlite, dsn for postgresql
    max_queries: int = 100  # max rows returned


class DatabaseQueryRequest(BaseModel):
    sql: str
    params: list[Any] = []
    timeout: float = 30.0


class FileSystemConfigureRequest(BaseModel):
    directory: str
    watch: bool = False
    max_depth: int = 5


class FileSystemListRequest(BaseModel):
    pattern: str = "*"
    include_hidden: bool = False
    max_results: int = 200


# ── Software Adapter Base ────────────────────────────────────────────────


class SoftwareAdapter(ABC):
    """Base class for software discovery adapters.

    Subclass this to add custom discovery logic for specific
    software categories (databases, containers, IDEs, etc.).
    """

    def __init__(self, name: str, description: str = "", capabilities: list[str] | None = None):
        self.name = name
        self.description = description
        self.capabilities = capabilities or []
        self.registered_at = time.time()

    @property
    def configured(self) -> bool:
        """适配器是否已具备工作所需配置（本地扫描类无需配置，即就绪）。"""
        return True

    @abstractmethod
    async def scan(self) -> list[dict[str, Any]]:
        """Run the adapter's discovery scan and return results."""
        ...

    @abstractmethod
    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        """Execute an action through this adapter."""
        ...

    def to_info(self) -> AdapterInfo:
        return AdapterInfo(
            name=self.name,
            description=self.description,
            capabilities=list(self.capabilities),
            configured=self.configured,
            registered_at=self.registered_at,
        )


# ── Adapter Registry ────────────────────────────────────────────────────

_adapter_registry: dict[str, SoftwareAdapter] = {}


def register_adapter(adapter: SoftwareAdapter) -> None:
    """Register a SoftwareAdapter instance."""
    _adapter_registry[adapter.name] = adapter
    logger.info("Registered software adapter: %s", adapter.name)


def get_adapter(name: str) -> SoftwareAdapter | None:
    return _adapter_registry.get(name)


# ── System Scanners ──────────────────────────────────────────────────────


async def _run_cmd(args: list[str], timeout: float = 10.0) -> tuple[str, str, int]:
    """Run a command via asyncio subprocess with timeout.

    Returns (stdout, stderr, returncode).
    Returns empty strings if the command fails or times out.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # isolate so kill() gets all children
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            proc.returncode or 0,
        )
    except TimeoutError:
        logger.warning("Command timed out: %s", " ".join(args))
        try:
            proc.kill()
        except (ProcessLookupError, UnboundLocalError):
            pass
        return ("", "timeout", -1)
    except PermissionError:
        logger.warning("Permission denied: %s", " ".join(args))
        return ("", "permission denied", -1)
    except FileNotFoundError:
        return ("", "command not found", -1)
    except Exception as e:
        logger.warning("Command failed [%s]: %s", " ".join(args), e)
        return ("", str(e), -1)


async def scan_processes() -> list[ProcessInfo]:
    """Scan running processes using 'ps aux'."""
    cached = _cache.get("processes")
    if cached is not None:
        return cached

    stdout, stderr, rc = await _run_cmd(["ps", "aux"])
    if rc != 0:
        logger.error("ps aux failed: %s", stderr)
        return []

    results: list[ProcessInfo] = []
    lines = stdout.strip().split("\n")
    for line in lines[1:]:  # Skip header
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        try:
            user = parts[0]
            pid = int(parts[1])
            cpu = float(parts[2])
            mem = float(parts[3])
            command_full = parts[10]
            executable = command_full.split()[0] if command_full else None

            # Extract a short name from the executable path
            _exe_name = Path(executable).name if executable else command_full[:40]

            results.append(
                ProcessInfo(
                    pid=pid,
                    user=user,
                    cpu=cpu,
                    mem=mem,
                    command=command_full,
                    executable=executable,
                    description=f"PID {pid} ({user}): {command_full[:80]}",
                )
            )
        except (ValueError, IndexError):
            continue

    _cache.set("processes", results)
    return results


async def scan_cli_tools() -> list[CLITool]:
    """Discover CLI tools by scanning PATH directories."""
    cached = _cache.get("cli_tools")
    if cached is not None:
        return cached

    path_dirs = os.environ.get("PATH", "").split(":")
    seen: set[str] = set()
    results: list[CLITool] = []

    for dir_path in path_dirs:
        if not dir_path or not os.path.isdir(dir_path):
            continue
        try:
            entries = os.listdir(dir_path)
        except PermissionError:
            continue

        for entry in entries:
            if entry in seen:
                continue
            full_path = os.path.join(dir_path, entry)
            if not os.path.isfile(full_path):
                continue
            # Check if executable
            if not os.access(full_path, os.X_OK):
                continue
            # Skip common non-tool files
            if entry.endswith((".py", ".pyc", ".sh", ".conf", ".txt", ".md", ".so")):
                continue
            seen.add(entry)
            description = _TOOL_DESCRIPTIONS.get(entry, f"CLI tool: {entry}")
            results.append(
                CLITool(
                    name=entry,
                    path=full_path,
                    version=None,
                    description=description,
                )
            )

    # Sort alphabetically
    results.sort(key=lambda t: t.name)
    _cache.set("cli_tools", results)
    return results


async def scan_services() -> list[ServiceInfo]:
    """Scan listening TCP services using 'ss -tlnp'."""
    cached = _cache.get("services")
    if cached is not None:
        return cached

    stdout, stderr, rc = await _run_cmd(["ss", "-tlnp"])
    if rc != 0:
        logger.error("ss -tlnp failed: %s", stderr)
        return []

    results: list[ServiceInfo] = []
    lines = stdout.strip().split("\n")
    for line in lines[1:]:  # Skip header
        parts = line.split()
        if len(parts) < 5:
            continue
        # ss output: State  Recv-Q  Send-Q  Local Address:Port  Peer Address:Port  ...
        state = parts[0]
        local = parts[3]
        # Split address and port
        if ":" in local:
            # Handle IPv6 [::]:port format
            if local.startswith("["):
                bracket_end = local.find("]")
                addr = local[1:bracket_end]
                port_str = local[bracket_end + 2 :]  # skip ]:
            else:
                addr, port_str = local.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                continue
        else:
            continue

        # Extract process info from the last column (users:(("name",pid=N,fd=N)))
        process_name = None
        pid = None
        if len(parts) >= 6:
            info_str = " ".join(parts[5:])
            if 'users:(("' in info_str:
                try:
                    start = info_str.index('users:(("') + 9
                    end = info_str.index('"', start)
                    process_name = info_str[start:end]
                    # Extract PID
                    pid_start = info_str.index("pid=", end) + 4
                    pid_end = info_str.index(",", pid_start)
                    pid = int(info_str[pid_start:pid_end])
                except (ValueError, IndexError):
                    pass

        # Build a useful description
        addr_display = "0.0.0.0" if addr == "*" else addr
        desc = f"{process_name or 'unknown'} listening on {addr_display}:{port}"
        if process_name:
            desc = f"{process_name} (PID {pid}) — port {port}"

        results.append(
            ServiceInfo(
                protocol="tcp",
                local_address=addr,
                local_port=port,
                state=state,
                process_name=process_name,
                pid=pid,
                description=desc,
            )
        )

    results.sort(key=lambda s: s.local_port)
    _cache.set("services", results)
    return results


# ── Built-in Adapters ───────────────────────────────────────────────────


class ProcessAdapter(SoftwareAdapter):
    """Adapter that exposes the process scanner."""

    def __init__(self):
        super().__init__(
            "process",
            "Discover and inspect running processes",
            capabilities=["scan", "kill", "info"],
        )

    async def scan(self) -> list[dict[str, Any]]:
        procs = await scan_processes()
        return [p.model_dump() for p in procs]

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        if action == "kill":
            pid = params.get("pid")
            if not pid:
                return {"error": "missing 'pid' parameter"}
            try:
                os.kill(int(pid), 15)
                return {"message": f"Sent SIGTERM to PID {pid}"}
            except ProcessLookupError:
                return {"error": f"Process {pid} not found"}
            except PermissionError:
                return {"error": f"Permission denied for PID {pid}"}
        elif action == "info":
            pid = params.get("pid")
            if not pid:
                return {"error": "missing 'pid' parameter"}
            procs = await scan_processes()
            for p in procs:
                if p.pid == int(pid):
                    return p.model_dump()
            return {"error": f"Process {pid} not found"}
        return {"error": f"Unknown action: {action}"}


class CLIToolAdapter(SoftwareAdapter):
    """Adapter that exposes CLI tool scanning."""

    def __init__(self):
        super().__init__(
            "cli-tools",
            "Discover command-line tools on PATH",
            capabilities=["scan", "help", "which", "version"],
        )

    async def scan(self) -> list[dict[str, Any]]:
        tools = await scan_cli_tools()
        return [t.model_dump() for t in tools]

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        if action == "help":
            tool_name = params.get("name")
            if not tool_name:
                return {"error": "missing 'name' parameter"}
            tool_path = shutil.which(tool_name)
            if not tool_path:
                return {"error": f"Tool '{tool_name}' not found on PATH"}
            stdout, stderr, rc = await _run_cmd([tool_path, "--help"], timeout=5.0)
            return {
                "tool": tool_name,
                "help": stdout[:2000] if rc == 0 else stderr[:2000],
            }
        elif action == "which":
            tool_name = params.get("name")
            if not tool_name:
                return {"error": "missing 'name' parameter"}
            tool_path = shutil.which(tool_name)
            return {"name": tool_name, "path": tool_path, "found": tool_path is not None}
        elif action == "version":
            tool_name = params.get("name")
            if not tool_name:
                return {"error": "missing 'name' parameter"}
            tool_path = shutil.which(tool_name)
            if not tool_path:
                return {"error": f"Tool '{tool_name}' not found on PATH"}
            for flag in ("--version", "-v"):
                stdout, stderr, rc = await _run_cmd([tool_path, flag], timeout=3.0)
                if rc == 0 and stdout.strip():
                    return {"tool": tool_name, "version": stdout.strip().split(chr(10))[0][:200]}
            return {"tool": tool_name, "version": None, "note": "No version output"}
        return {"error": f"Unknown action: {action}"}


class ServiceAdapter(SoftwareAdapter):
    """Adapter that exposes local service scanning."""

    def __init__(self):
        super().__init__(
            "services",
            "Discover local listening services",
            capabilities=["scan", "connections"],
        )

    async def scan(self) -> list[dict[str, Any]]:
        services = await scan_services()
        return [s.model_dump() for s in services]

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        if action == "connections":
            port = params.get("port")
            if port:
                stdout, stderr, rc = await _run_cmd(
                    ["ss", "-tnp", "sport", "=", str(port)], timeout=5.0
                )
            else:
                stdout, stderr, rc = await _run_cmd(["ss", "-tnp"], timeout=5.0)
            if rc == 0:
                return {"connections": stdout.strip()}
            return {"error": stderr.strip()}
        return {"error": f"Unknown action: {action}"}


# ── REST API Adapter ───────────────────────────────────────────────────


class RestAdapter(SoftwareAdapter):
    """Adapter for interacting with arbitrary REST APIs.

    Supports GET/POST/PUT/DELETE, custom headers, auth tokens,
    and automatic OpenAPI/Swagger schema discovery.
    """

    def __init__(self):
        super().__init__(
            "rest",
            "Interact with arbitrary REST APIs (GET/POST/PUT/DELETE)",
            capabilities=["scan", "configure", "probe", "request"],
        )
        self._config: dict[str, Any] = {}  # base_url, headers, auth_token, timeout
        self._schema_cache: dict[str, Any] | None = None

    @property
    def configured(self) -> bool:
        return bool(self._config.get("base_url"))

    def _build_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        headers.update(self._config.get("headers", {}))
        token = self._config.get("auth_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if extra:
            headers.update(extra)
        return headers

    async def scan(self) -> list[dict[str, Any]]:
        """Return current REST adapter configuration."""
        return [
            {
                "configured": bool(self._config.get("base_url")),
                "base_url": self._config.get("base_url"),
                "has_auth": bool(self._config.get("auth_token")),
                "schema_cached": self._schema_cache is not None,
            }
        ]

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        dispatch = {
            "configure": self._do_configure,
            "request": self._do_request,
            "probe": self._do_probe,
        }
        handler = dispatch.get(action)
        if not handler:
            return {"error": f"Unknown action: {action}. Available: {list(dispatch.keys())}"}
        try:
            return await handler(params)
        except Exception as e:
            logger.exception("REST adapter action '%s' failed", action)
            return {"error": str(e)}

    async def _do_configure(self, params: dict[str, Any]) -> dict[str, Any]:
        base_url = params.get("base_url")
        if not base_url:
            return {"error": "Missing 'base_url'"}
        self._config = {
            "base_url": base_url.rstrip("/"),
            "headers": params.get("headers", {}),
            "auth_token": params.get("auth_token"),
            "timeout": params.get("timeout", 30.0),
        }
        self._schema_cache = None
        return {"status": "configured", "base_url": self._config["base_url"]}

    async def _do_request(self, params: dict[str, Any]) -> Any:
        if _httpx is None:
            return {"error": "httpx not installed — pip install httpx"}
        base_url = self._config.get("base_url", "")
        if not base_url:
            return {"error": "REST adapter not configured. Call 'configure' first."}
        method = params.get("method", "GET").upper()
        path = params.get("path", "/")
        url = f"{base_url}{path}" if path.startswith("/") else f"{base_url}/{path}"
        timeout = params.get("timeout", self._config.get("timeout", 30.0))
        headers = self._build_headers(params.get("headers"))
        body = params.get("body")

        try:
            async with _httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=body if body is not None else None,
                )
                # Try to parse JSON, fallback to text
                try:
                    resp_body = resp.json()
                except Exception:
                    resp_body = resp.text[:5000]
                return {
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                    "body": resp_body,
                    "url": str(resp.url),
                }
        except _httpx.TimeoutException:
            return {"error": f"Request timed out after {timeout}s"}
        except _httpx.ConnectError as e:
            return {"error": f"Connection failed: {e}"}

    async def _do_probe(self, params: dict[str, Any]) -> Any:
        """Try to discover API schema via OpenAPI/Swagger endpoints."""
        if _httpx is None:
            return {"error": "httpx not installed"}
        base_url = params.get("base_url") or self._config.get("base_url")
        if not base_url:
            return {"error": "No base_url configured or provided"}
        base_url = base_url.rstrip("/")
        timeout = params.get("timeout", 15.0)
        headers = self._build_headers()

        # Common OpenAPI/Swagger paths to try
        schema_paths = [
            "/openapi.json",
            "/swagger.json",
            "/api-docs",
            "/v1/openapi.json",
            "/v2/openapi.json",
            "/docs/openapi.json",
            "/api/openapi.json",
            "/api/swagger.json",
        ]

        async with _httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for sp in schema_paths:
                try:
                    resp = await client.get(f"{base_url}{sp}", headers=headers)
                    if resp.status_code == 200:
                        try:
                            schema = resp.json()
                            # Extract useful info
                            info = {
                                "found_at": sp,
                                "title": schema.get("info", {}).get("title", "Unknown"),
                                "version": schema.get("info", {}).get("version", "Unknown"),
                                "base_path": schema.get("basePath", ""),
                                "paths_count": len(schema.get("paths", {})),
                                "paths": list(schema.get("paths", {}).keys())[:50],
                            }
                            self._schema_cache = schema
                            return info
                        except Exception:
                            continue
                except Exception:
                    continue

        return {
            "found_at": None,
            "note": "No OpenAPI/Swagger schema found at common endpoints",
            "tried": schema_paths,
        }


# ── Database Adapter ──────────────────────────────────────────────────


class DatabaseAdapter(SoftwareAdapter):
    """Adapter for querying SQLite and PostgreSQL databases.

    Read-only — SELECT queries only for safety.
    Auto-discovers table structure and schema.
    """

    # SQL keywords that indicate write operations
    _WRITE_KEYWORDS = frozenset(
        {
            "INSERT",
            "UPDATE",
            "DELETE",
            "DROP",
            "ALTER",
            "CREATE",
            "TRUNCATE",
            "REPLACE",
            "GRANT",
            "REVOKE",
            "EXECUTE",
        }
    )

    def __init__(self):
        super().__init__(
            "database",
            "Query SQLite/PostgreSQL databases (read-only)",
            capabilities=["scan", "configure", "query", "tables", "describe"],
        )
        self._db_type: str | None = None
        self._conn_str: str | None = None
        self._max_queries: int = 100
        self._pg_pool: Any | None = None  # asyncpg pool

    @property
    def configured(self) -> bool:
        return bool(self._db_type and self._conn_str)

    def _is_readonly(self, sql: str) -> bool:
        """Check that the SQL is a read-only statement."""
        stripped = sql.strip().upper()
        # Allow SELECT, SHOW, EXPLAIN, WITH (CTE for select)
        if stripped.startswith(("SELECT", "SHOW", "EXPLAIN", "WITH", "PRAGMA")):
            return True
        return False

    async def scan(self) -> list[dict[str, Any]]:
        """Discover tables and schema from the connected database."""
        if not self._db_type or not self._conn_str:
            return [{"configured": False, "note": "Database not configured"}]
        try:
            if self._db_type == "sqlite":
                return await self._scan_sqlite()
            elif self._db_type == "postgresql":
                return await self._scan_postgresql()
            return [{"error": f"Unsupported db_type: {self._db_type}"}]
        except Exception as e:
            logger.exception("Database scan failed")
            return [{"error": str(e)}]

    async def _scan_sqlite(self) -> list[dict[str, Any]]:
        if aiosqlite is None:
            return [{"error": "aiosqlite not installed"}]
        try:
            assert aiosqlite is not None
            async with aiosqlite.connect(self._conn_str) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
                )
                rows = await cursor.fetchall()
                tables = []
                for row in rows:
                    tbl = row[0] if isinstance(row, (list, tuple)) else row["name"]
                    tbl_type = row[1] if isinstance(row, (list, tuple)) else row["type"]
                    # Get columns
                    col_cursor = await db.execute(f'PRAGMA table_info("{tbl}")')
                    cols = await col_cursor.fetchall()
                    columns = [
                        {
                            "name": c[1] if isinstance(c, (list, tuple)) else c["name"],
                            "type": c[2] if isinstance(c, (list, tuple)) else c["type"],
                            "notnull": bool(c[3] if isinstance(c, (list, tuple)) else c["notnull"]),
                            "pk": bool(c[5] if isinstance(c, (list, tuple)) else c["pk"]),
                        }
                        for c in cols
                    ]
                    tables.append({"name": tbl, "type": tbl_type, "columns": columns})
                return tables
        except Exception as e:
            return [{"error": f"SQLite scan failed: {e}"}]

    async def _scan_postgresql(self) -> list[dict[str, Any]]:
        if asyncpg is None:
            return [{"error": "asyncpg not installed"}]
        try:
            pool = await self._get_pg_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT table_name, table_type
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    ORDER BY table_name
                """)
                tables = []
                for row in rows:
                    tbl = row["table_name"]
                    cols = await conn.fetch(
                        """
                        SELECT column_name, data_type, is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = $1
                        ORDER BY ordinal_position
                    """,
                        tbl,
                    )
                    tables.append(
                        {
                            "name": tbl,
                            "type": row["table_type"],
                            "columns": [
                                {
                                    "name": c["column_name"],
                                    "type": c["data_type"],
                                    "nullable": c["is_nullable"] == "YES",
                                    "default": c["column_default"],
                                }
                                for c in cols
                            ],
                        }
                    )
                return tables
        except Exception as e:
            return [{"error": f"PostgreSQL scan failed: {e}"}]

    async def _get_pg_pool(self) -> Any:
        if self._pg_pool is None or self._pg_pool._closed:
            assert asyncpg is not None, "asyncpg not installed"
            self._pg_pool = await asyncpg.create_pool(
                self._conn_str, min_size=1, max_size=5, timeout=10.0
            )
        return self._pg_pool

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        dispatch = {
            "configure": self._do_configure,
            "query": self._do_query,
            "tables": self._do_tables,
            "describe": self._do_describe,
        }
        handler = dispatch.get(action)
        if not handler:
            return {"error": f"Unknown action: {action}. Available: {list(dispatch.keys())}"}
        try:
            return await asyncio.wait_for(handler(params), timeout=params.get("timeout", 30.0))
        except TimeoutError:
            return {"error": "Operation timed out"}
        except Exception as e:
            logger.exception("Database adapter action '%s' failed", action)
            return {"error": str(e)}

    async def _do_configure(self, params: dict[str, Any]) -> dict[str, Any]:
        db_type = params.get("db_type", "").lower()
        if db_type not in ("sqlite", "postgresql"):
            return {"error": "db_type must be 'sqlite' or 'postgresql'"}
        conn_str = params.get("connection_string")
        if not conn_str:
            return {"error": "Missing 'connection_string'"}

        # Close existing pool if reconfiguring
        if self._pg_pool and not self._pg_pool._closed:
            await self._pg_pool.close()
            self._pg_pool = None

        # Validate connection
        try:
            if db_type == "sqlite":
                if aiosqlite is None:
                    return {"error": "aiosqlite not installed — pip install aiosqlite"}
                async with aiosqlite.connect(conn_str) as db:
                    await db.execute("SELECT 1")
            elif db_type == "postgresql":
                if asyncpg is None:
                    return {"error": "asyncpg not installed — pip install asyncpg"}
                conn = await asyncpg.connect(conn_str, timeout=10.0)
                await conn.fetch("SELECT 1")
                await conn.close()
        except Exception as e:
            return {"error": f"Connection failed: {e}"}

        self._db_type = db_type
        self._conn_str = conn_str
        self._max_queries = params.get("max_queries", 100)
        return {"status": "configured", "db_type": db_type, "connection": conn_str}

    async def _do_query(self, params: dict[str, Any]) -> Any:
        if not self._db_type or not self._conn_str:
            return {"error": "Database not configured. Call 'configure' first."}
        sql = params.get("sql", "").strip()
        if not sql:
            return {"error": "Missing 'sql'"}
        if not self._is_readonly(sql):
            return {"error": "Only SELECT/SHOW/EXPLAIN queries allowed (read-only mode)"}

        query_params = params.get("params", [])

        if self._db_type == "sqlite":
            return await self._query_sqlite(sql, query_params)
        elif self._db_type == "postgresql":
            return await self._query_postgresql(sql, query_params)
        return {"error": f"Unsupported db_type: {self._db_type}"}

    async def _query_sqlite(self, sql: str, params: list[Any]) -> Any:
        if aiosqlite is None:
            return {"error": "aiosqlite not installed"}
        try:
            assert aiosqlite is not None
            async with aiosqlite.connect(self._conn_str) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                if cursor.description:
                    rows = await cursor.fetchmany(self._max_queries)
                    columns = [d[0] for d in cursor.description]
                    return {
                        "columns": columns,
                        "rows": [dict(zip(columns, row)) for row in rows],
                        "row_count": len(rows),
                        "truncated": len(rows) >= self._max_queries,
                    }
                else:
                    return {"message": "Query executed (no result set)"}
        except Exception as e:
            return {"error": f"SQLite query error: {e}"}

    async def _query_postgresql(self, sql: str, params: list[Any]) -> Any:
        if asyncpg is None:
            return {"error": "asyncpg not installed"}
        try:
            pool = await self._get_pg_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                if rows:
                    columns = list(rows[0].keys())
                    limited = rows[: self._max_queries]
                    return {
                        "columns": columns,
                        "rows": [dict(r) for r in limited],
                        "row_count": len(limited),
                        "truncated": len(rows) >= self._max_queries,
                    }
                return {"columns": [], "rows": [], "row_count": 0}
        except Exception as e:
            return {"error": f"PostgreSQL query error: {e}"}

    async def _do_tables(self, params: dict[str, Any]) -> Any:
        return await self.scan()

    async def _do_describe(self, params: dict[str, Any]) -> Any:
        table = params.get("table")
        if not table:
            return {"error": "Missing 'table' name"}
        if not self._db_type or not self._conn_str:
            return {"error": "Database not configured"}
        if self._db_type == "sqlite":
            try:
                assert aiosqlite is not None
                async with aiosqlite.connect(self._conn_str) as db:
                    db.row_factory = aiosqlite.Row
                    cursor = await db.execute(f'PRAGMA table_info("{table}")')
                    cols = await cursor.fetchall()
                    return {
                        "table": table,
                        "columns": [
                            {"name": c["name"], "type": c["type"], "pk": bool(c["pk"])}
                            for c in cols
                        ],
                    }
            except Exception as e:
                return {"error": str(e)}
        elif self._db_type == "postgresql":
            try:
                pool = await self._get_pg_pool()
                async with pool.acquire() as conn:
                    cols = await conn.fetch(
                        """
                        SELECT column_name, data_type, is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = $1
                        ORDER BY ordinal_position
                    """,
                        table,
                    )
                    return {
                        "table": table,
                        "columns": [
                            {
                                "name": c["column_name"],
                                "type": c["data_type"],
                                "nullable": c["is_nullable"] == "YES",
                            }
                            for c in cols
                        ],
                    }
            except Exception as e:
                return {"error": str(e)}
        return {"error": f"Unsupported db_type: {self._db_type}"}


# ── File System Adapter ────────────────────────────────────────────────


class FileSystemAdapter(SoftwareAdapter):
    """Adapter for monitoring and exploring local file systems.

    Supports directory watching (via watchfiles or polling fallback),
    file listing with glob patterns, and file content reading.
    """

    def __init__(self):
        super().__init__(
            "filesystem",
            "Monitor and explore local file systems",
            capabilities=[
                "scan",
                "configure",
                "list",
                "read",
                "search",
                "info",
                "changes",
                "stop_watch",
            ],
        )
        self._directory: str | None = None
        self._watching: bool = False
        self._max_depth: int = 5
        self._watch_task: asyncio.Task | None = None
        self._change_log: list[dict[str, Any]] = []
        self._max_change_log: int = 500

    @property
    def configured(self) -> bool:
        return self._directory is not None

    async def scan(self) -> list[dict[str, Any]]:
        """Return filesystem adapter status and recent changes."""
        return [
            {
                "configured": self._directory is not None,
                "directory": self._directory,
                "watching": self._watching,
                "watcher": "watchfiles" if _awatch else "polling",
                "recent_changes": self._change_log[-20:],
                "total_changes": len(self._change_log),
            }
        ]

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        dispatch = {
            "configure": self._do_configure,
            "list": self._do_list,
            "read": self._do_read,
            "search": self._do_search,
            "info": self._do_info,
            "changes": self._do_changes,
            "stop_watch": self._do_stop_watch,
        }
        handler = dispatch.get(action)
        if not handler:
            return {"error": f"Unknown action: {action}. Available: {list(dispatch.keys())}"}
        try:
            return await asyncio.wait_for(handler(params), timeout=params.get("timeout", 30.0))
        except TimeoutError:
            return {"error": "Operation timed out"}
        except Exception as e:
            logger.exception("FileSystem adapter action '%s' failed", action)
            return {"error": str(e)}

    async def _do_configure(self, params: dict[str, Any]) -> dict[str, Any]:
        directory = params.get("directory")
        if not directory:
            return {"error": "Missing 'directory'"}
        expanded = os.path.expanduser(directory)
        if not os.path.isdir(expanded):
            return {"error": f"Directory not found: {expanded}"}
        self._directory = expanded
        self._max_depth = params.get("max_depth", 5)

        # Start watching if requested
        should_watch = params.get("watch", False)
        if should_watch and not self._watching:
            await self._start_watch()
        elif not should_watch and self._watching:
            await self._stop_watch()

        return {
            "status": "configured",
            "directory": self._directory,
            "watching": self._watching,
            "max_depth": self._max_depth,
        }

    async def _start_watch(self) -> None:
        """Start background file watcher."""
        self._watching = True
        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.info("Started filesystem watcher on %s", self._directory)

    async def _stop_watch(self) -> None:
        """Stop background file watcher."""
        self._watching = False
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None
        logger.info("Stopped filesystem watcher")

    async def _watch_loop(self) -> None:
        """Background loop that monitors directory for changes."""
        if _awatch is not None and self._directory is not None:
            # Use watchfiles for efficient OS-level monitoring
            try:
                async for changes in _awatch(self._directory, stop_event=asyncio.Event()):
                    for change_type, path in changes:
                        self._add_change(change_type.name, path)
                    if not self._watching:
                        break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("watchfiles watcher error: %s — falling back to polling", e)
                await self._polling_watch()
        else:
            await self._polling_watch()

    async def _polling_watch(self) -> None:
        """Fallback polling-based watcher using os.walk."""
        snapshot = self._take_snapshot()
        while self._watching:
            await asyncio.sleep(2.0)
            new_snapshot = self._take_snapshot()
            # Detect added/removed files
            old_paths = set(snapshot.keys())
            new_paths = set(new_snapshot.keys())
            for p in new_paths - old_paths:
                self._add_change("added", p)
            for p in old_paths - new_paths:
                self._add_change("removed", p)
            # Detect modified files
            for p in new_paths & old_paths:
                if new_snapshot[p] != snapshot[p]:
                    self._add_change("modified", p)
            snapshot = new_snapshot

    def _take_snapshot(self) -> dict[str, float]:
        """Take a snapshot of file mtimes in the directory."""
        result: dict[str, float] = {}
        if not self._directory:
            return result
        try:
            for root, dirs, files in os.walk(self._directory):
                # Respect max depth
                depth = root.replace(self._directory, "").count(os.sep)
                if depth >= self._max_depth:
                    dirs.clear()
                    continue
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        result[fp] = os.path.getmtime(fp)
                    except OSError:
                        pass
        except Exception as e:
            logger.warning("Snapshot error: %s", e)
        return result

    def _add_change(self, change_type: str, path: str) -> None:
        self._change_log.append(
            {
                "type": change_type,
                "path": str(path),
                "time": time.time(),
            }
        )
        # Trim to max size
        if len(self._change_log) > self._max_change_log:
            self._change_log = self._change_log[-self._max_change_log :]

    async def _do_list(self, params: dict[str, Any]) -> Any:
        directory = params.get("directory") or self._directory
        if not directory:
            return {"error": "No directory configured. Call 'configure' first or pass 'directory'."}
        directory = os.path.expanduser(directory)
        if not os.path.isdir(directory):
            return {"error": f"Not a directory: {directory}"}

        pattern = params.get("pattern", "*")
        include_hidden = params.get("include_hidden", False)
        max_results = params.get("max_results", 200)
        results: list[dict[str, Any]] = []

        try:
            for entry in sorted(os.listdir(directory)):
                if not include_hidden and entry.startswith("."):
                    continue
                if not fnmatch.fnmatch(entry, pattern):
                    continue
                full_path = os.path.join(directory, entry)
                try:
                    stat = os.stat(full_path)
                    results.append(
                        {
                            "name": entry,
                            "path": full_path,
                            "is_dir": os.path.isdir(full_path),
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                        }
                    )
                except OSError:
                    results.append({"name": entry, "path": full_path, "error": "stat failed"})
                if len(results) >= max_results:
                    break
        except PermissionError:
            return {"error": f"Permission denied: {directory}"}
        except Exception as e:
            return {"error": str(e)}

        return {"directory": directory, "pattern": pattern, "count": len(results), "files": results}

    async def _do_read(self, params: dict[str, Any]) -> Any:
        file_path = params.get("path")
        if not file_path:
            return {"error": "Missing 'path'"}
        file_path = os.path.expanduser(file_path)
        if not os.path.isfile(file_path):
            return {"error": f"Not a file: {file_path}"}
        # Security: check file size (max 1MB)
        try:
            size = os.path.getsize(file_path)
            if size > 1_048_576:
                return {"error": f"File too large ({size} bytes). Max 1MB."}
        except OSError as e:
            return {"error": str(e)}

        try:
            encoding = params.get("encoding", "utf-8")
            with open(file_path, encoding=encoding, errors="replace") as f:
                content = f.read()
            return {"path": file_path, "size": len(content), "content": content}
        except Exception as e:
            return {"error": f"Read failed: {e}"}

    async def _do_search(self, params: dict[str, Any]) -> Any:
        directory = params.get("directory") or self._directory
        if not directory:
            return {"error": "No directory configured"}
        directory = os.path.expanduser(directory)
        pattern = params.get("pattern", "*")
        content_search = params.get("content")  # optional text to search for
        max_results = params.get("max_results", 50)
        results: list[dict[str, Any]] = []

        def _walk_with_limit() -> list[str]:
            found: list[str] = []
            for root, dirs, files in os.walk(directory):
                depth = root.replace(directory, "").count(os.sep)
                if depth >= self._max_depth:
                    dirs.clear()
                    continue
                for f in files:
                    if fnmatch.fnmatch(f, pattern):
                        found.append(os.path.join(root, f))
                        if len(found) >= max_results:
                            return found
            return found

        matched_files = _walk_with_limit()

        if content_search:
            # Search inside file contents
            for fp in matched_files:
                try:
                    if os.path.getsize(fp) > 500_000:  # skip large files
                        continue
                    with open(fp, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                    if content_search.lower() in text.lower():
                        # Find the matching line(s)
                        for i, line in enumerate(text.splitlines(), 1):
                            if content_search.lower() in line.lower():
                                results.append({"path": fp, "line": i, "text": line.strip()[:300]})
                                break
                except Exception:
                    continue
                if len(results) >= max_results:
                    break
        else:
            results = [{"path": fp} for fp in matched_files]

        return {
            "directory": directory,
            "pattern": pattern,
            "count": len(results),
            "results": results,
        }

    async def _do_info(self, params: dict[str, Any]) -> Any:
        file_path = params.get("path")
        if not file_path:
            return {"error": "Missing 'path'"}
        file_path = os.path.expanduser(file_path)
        try:
            stat = os.stat(file_path)
            return {
                "path": file_path,
                "exists": True,
                "is_file": os.path.isfile(file_path),
                "is_dir": os.path.isdir(file_path),
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "created": stat.st_ctime,
                "mode": oct(stat.st_mode),
            }
        except FileNotFoundError:
            return {"path": file_path, "exists": False}
        except Exception as e:
            return {"error": str(e)}

    async def _do_changes(self, params: dict[str, Any]) -> Any:
        limit = params.get("limit", 50)
        return {"changes": self._change_log[-limit:], "total": len(self._change_log)}

    async def _do_stop_watch(self, params: dict[str, Any]) -> Any:
        if not self._watching:
            return {"message": "Not watching"}
        await self._stop_watch()
        return {"message": "Watch stopped"}


# Auto-register built-in adapters
register_adapter(ProcessAdapter())
register_adapter(CLIToolAdapter())
register_adapter(ServiceAdapter())
register_adapter(RestAdapter())
register_adapter(DatabaseAdapter())
register_adapter(FileSystemAdapter())


# ── API Endpoints ────────────────────────────────────────────────────────


@router.get("/scan", response_model=ScanResult)
async def api_full_scan():
    """Run a full system scan — processes, CLI tools, and services."""
    start = time.time()
    procs, tools, services = await asyncio.gather(
        scan_processes(),
        scan_cli_tools(),
        scan_services(),
    )
    return ScanResult(
        scan_time=round(time.time() - start, 3),
        processes=procs,
        cli_tools=tools,
        services=services,
        adapters=list(_adapter_registry.keys()),
    )


@router.get("/processes", response_model=list[ProcessInfo])
async def api_list_processes():
    """List all running processes."""
    return await scan_processes()


@router.get("/cli-tools", response_model=list[CLITool])
async def api_list_cli_tools():
    """List all CLI tools found on PATH."""
    return await scan_cli_tools()


@router.get("/services", response_model=list[ServiceInfo])
async def api_list_services():
    """List all locally listening services."""
    return await scan_services()


@router.get("/adapters", response_model=list[AdapterInfo])
async def api_list_adapters():
    """List all registered software adapters."""
    return [a.to_info() for a in _adapter_registry.values()]


@router.post(
    "/adapters/{name}/execute",
    response_model=AdapterExecuteResponse,
)
async def api_execute_adapter(name: str, req: AdapterExecuteRequest):
    """Execute an action through a named adapter."""
    adapter = _adapter_registry.get(name)
    if not adapter:
        raise HTTPException(
            status_code=404,
            detail=f"Adapter '{name}' not found. Available: {list(_adapter_registry.keys())}",
        )
    try:
        result = await adapter.execute(req.action, req.params)
        error = None
        success = True
        if isinstance(result, dict) and "error" in result:
            error = result["error"]
            success = False
        return AdapterExecuteResponse(
            adapter=name,
            action=req.action,
            success=success,
            result=result,
            error=error,
        )
    except Exception as e:
        logger.exception("Adapter %s execute failed", name)
        return AdapterExecuteResponse(
            adapter=name,
            action=req.action,
            success=False,
            error=str(e),
        )


@router.get("/health")
async def api_discovery_health():
    """Discovery engine health check."""
    return {
        "status": "ok",
        "adapters_registered": len(_adapter_registry),
        "cache_ttl_seconds": CACHE_TTL,
    }


# ── REST Adapter Endpoints ─────────────────────────────────────────────


@router.post("/adapters/rest/configure")
async def api_rest_configure(req: RestConfigureRequest):
    """Configure the REST API adapter with a base URL, headers, and optional auth token."""
    adapter = get_adapter("rest")
    if not adapter:
        raise HTTPException(status_code=500, detail="REST adapter not registered")
    try:
        result = await adapter.execute(
            "configure",
            {
                "base_url": req.base_url,
                "headers": req.headers,
                "auth_token": req.auth_token,
                "timeout": req.timeout,
            },
        )
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("REST configure failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/adapters/rest/probe")
async def api_rest_probe(req: RestProbeRequest):
    """Probe a REST API for OpenAPI/Swagger schema discovery."""
    adapter = get_adapter("rest")
    if not adapter:
        raise HTTPException(status_code=500, detail="REST adapter not registered")
    try:
        result = await adapter.execute(
            "probe",
            {
                "base_url": req.base_url,
                "headers": req.headers,
                "auth_token": req.auth_token,
            },
        )
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("REST probe failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/adapters/rest/request")
async def api_rest_request(req: RestRequestModel):
    """Make an HTTP request through the configured REST adapter."""
    adapter = get_adapter("rest")
    if not adapter:
        raise HTTPException(status_code=500, detail="REST adapter not registered")
    try:
        result = await adapter.execute(
            "request",
            {
                "method": req.method,
                "path": req.path,
                "headers": req.headers,
                "body": req.body,
                "timeout": req.timeout,
            },
        )
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("REST request failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── Database Adapter Endpoints ─────────────────────────────────────────


@router.post("/adapters/database/configure")
async def api_database_configure(req: DatabaseConfigureRequest):
    """Configure the database adapter with connection details (SQLite or PostgreSQL)."""
    adapter = get_adapter("database")
    if not adapter:
        raise HTTPException(status_code=500, detail="Database adapter not registered")
    try:
        result = await adapter.execute(
            "configure",
            {
                "db_type": req.db_type,
                "connection_string": req.connection_string,
                "max_queries": req.max_queries,
            },
        )
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Database configure failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/adapters/database/query")
async def api_database_query(req: DatabaseQueryRequest):
    """Execute a read-only SQL query on the configured database."""
    adapter = get_adapter("database")
    if not adapter:
        raise HTTPException(status_code=500, detail="Database adapter not registered")
    try:
        result = await adapter.execute(
            "query",
            {
                "sql": req.sql,
                "params": req.params,
                "timeout": req.timeout,
            },
        )
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Database query failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/adapters/database/tables")
async def api_database_tables():
    """Discover all tables and their schemas from the configured database."""
    adapter = get_adapter("database")
    if not adapter:
        raise HTTPException(status_code=500, detail="Database adapter not registered")
    try:
        result = await adapter.execute("tables", {})
        return result
    except Exception as e:
        logger.exception("Database tables failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/adapters/database/describe")
async def api_database_describe(table: str = Query(..., description="Table name")):
    """Describe the columns of a specific table."""
    adapter = get_adapter("database")
    if not adapter:
        raise HTTPException(status_code=500, detail="Database adapter not registered")
    try:
        result = await adapter.execute("describe", {"table": table})
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Database describe failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── File System Adapter Endpoints ──────────────────────────────────────


@router.post("/adapters/filesystem/configure")
async def api_filesystem_configure(req: FileSystemConfigureRequest):
    """Configure the filesystem adapter with a directory to monitor."""
    adapter = get_adapter("filesystem")
    if not adapter:
        raise HTTPException(status_code=500, detail="FileSystem adapter not registered")
    try:
        result = await adapter.execute(
            "configure",
            {
                "directory": req.directory,
                "watch": req.watch,
                "max_depth": req.max_depth,
            },
        )
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FileSystem configure failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/adapters/filesystem/list")
async def api_filesystem_list(
    directory: str | None = Query(
        None, description="Directory to list (uses configured dir if omitted)"
    ),
    pattern: str = Query("*", description="Glob pattern to filter files"),
    include_hidden: bool = Query(False, description="Include hidden files"),
    max_results: int = Query(200, description="Max files to return"),
):
    """List files in a directory with optional glob pattern matching."""
    adapter = get_adapter("filesystem")
    if not adapter:
        raise HTTPException(status_code=500, detail="FileSystem adapter not registered")
    try:
        params: dict[str, Any] = {
            "pattern": pattern,
            "include_hidden": include_hidden,
            "max_results": max_results,
        }
        if directory:
            params["directory"] = directory
        result = await adapter.execute("list", params)
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FileSystem list failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/adapters/filesystem/read")
async def api_filesystem_read(path: str = Query(..., description="File path to read")):
    """Read the contents of a file."""
    adapter = get_adapter("filesystem")
    if not adapter:
        raise HTTPException(status_code=500, detail="FileSystem adapter not registered")
    try:
        result = await adapter.execute("read", {"path": path})
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FileSystem read failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/adapters/filesystem/search")
async def api_filesystem_search(
    directory: str | None = Query(None),
    pattern: str = Query("*", description="Glob pattern for filenames"),
    content: str | None = Query(None, description="Text to search for in file contents"),
    max_results: int = Query(50),
):
    """Search for files matching a pattern, optionally searching inside file contents."""
    adapter = get_adapter("filesystem")
    if not adapter:
        raise HTTPException(status_code=500, detail="FileSystem adapter not registered")
    try:
        params: dict[str, Any] = {
            "pattern": pattern,
            "max_results": max_results,
        }
        if directory:
            params["directory"] = directory
        if content:
            params["content"] = content
        result = await adapter.execute("search", params)
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("FileSystem search failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/adapters/filesystem/changes")
async def api_filesystem_changes(limit: int = Query(50, description="Max changes to return")):
    """Get recent file system change events."""
    adapter = get_adapter("filesystem")
    if not adapter:
        raise HTTPException(status_code=500, detail="FileSystem adapter not registered")
    try:
        return await adapter.execute("changes", {"limit": limit})
    except Exception as e:
        logger.exception("FileSystem changes failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/adapters/filesystem/stop-watch")
async def api_filesystem_stop_watch():
    """Stop the filesystem watcher."""
    adapter = get_adapter("filesystem")
    if not adapter:
        raise HTTPException(status_code=500, detail="FileSystem adapter not registered")
    try:
        return await adapter.execute("stop_watch", {})
    except Exception as e:
        logger.exception("FileSystem stop-watch failed")
        raise HTTPException(status_code=500, detail=str(e))
