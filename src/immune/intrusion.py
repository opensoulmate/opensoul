"""Intrusion Detection — monitors API requests for attack patterns.

Detects:
- SQL injection attempts (union, comment, tautology patterns)
- XSS / script injection
- Path traversal (../, ..%5c)
- Command injection (;, |, &&, backticks)
- Brute-force login detection
- Unusual request frequency (anomaly detection)
- Header injection
- SSRF patterns (internal IPs, metadata endpoints)

Auto-actions:
- Alert via event bridge
- Auto-blacklist after threshold breaches
- Log all suspicious activity to audit trail
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import StrEnum


class ThreatLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AttackType(StrEnum):
    SQL_INJECTION = "sql_injection"
    XSS = "xss"
    PATH_TRAVERSAL = "path_traversal"
    COMMAND_INJECTION = "command_injection"
    BRUTE_FORCE = "brute_force"
    HEADER_INJECTION = "header_injection"
    SSRF = "ssrf"
    RATE_ANOMALY = "rate_anomaly"
    BOT_DETECTED = "bot_detected"


@dataclass
class ThreatEvent:
    """A single detected threat."""

    threat_id: str
    attack_type: AttackType
    threat_level: ThreatLevel
    source_ip: str
    path: str
    method: str
    detail: str
    matched_pattern: str = ""
    blocked: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "threat_id": self.threat_id,
            "attack_type": self.attack_type.value,
            "threat_level": self.threat_level.value,
            "source_ip": self.source_ip,
            "path": self.path,
            "method": self.method,
            "detail": self.detail,
            "matched_pattern": self.matched_pattern,
            "blocked": self.blocked,
            "timestamp": self.timestamp,
        }


@dataclass
class IPSuspicion:
    """Tracks suspicion level for a single IP."""

    ip: str
    threat_count: int = 0
    threat_types: set = field(default_factory=set)
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    blocked: bool = False
    blocked_at: float = 0.0


class IntrusionDetector:
    """Monitors requests and detects intrusion patterns."""

    # ── SQL Injection Patterns ──────────────────────────────────────
    SQLI_PATTERNS = [
        (r"(?i)\bunion\b.*\bselect\b", "UNION SELECT injection"),
        (r"(?i)'\s*(or|and)\s*'?\d*\s*=\s*'?\d*", "Tautology injection"),
        (r"(?i)'\s*(or|and)\s+'[^']*'\s*=\s*'[^']*'", "String tautology injection"),
        (r"(?i);\s*(drop|truncate|delete|update|insert|alter)\b", "DDL/DML injection"),
        (r"(?i)--\s*$|/\*.*\*/", "SQL comment injection"),
        (r"(?i)\bexec\b.*\bxp_cmdshell\b", "xp_cmdshell injection"),
        (r"(?i)\binto\b\s+(outfile|dumpfile)\b", "File write injection"),
        (r"(?i)\b(load_file|benchmark|sleep|waitfor)\s*\(", "SQL function injection"),
        (r"(?i)0x[0-9a-f]+", "Hex-encoded injection"),
        (r"(?i)\bchar\s*\(\s*\d+", "CHAR() injection"),
    ]

    # ── XSS Patterns ───────────────────────────────────────────────
    XSS_PATTERNS = [
        (r"<script[^>]*>", "Script tag injection"),
        (r"javascript\s*:", "JavaScript URI injection"),
        (r"on(load|error|click|mouseover|focus|blur|submit|change)\s*=", "Event handler injection"),
        (r"<\s*img[^>]+onerror", "IMG onerror injection"),
        (r"<\s*svg[^>]+onload", "SVG onload injection"),
        (r"<\s*iframe", "IFrame injection"),
        (r"<\s*object\s", "Object tag injection"),
        (r"<\s*embed\s", "Embed tag injection"),
        (r"expression\s*\(", "CSS expression injection"),
        (r"eval\s*\(", "eval() injection"),
        (r"document\.(cookie|location|write)", "DOM access injection"),
        (r"window\.(location|open)", "Window manipulation"),
        (r"alert\s*\(", "alert() injection"),
        (r"String\.fromCharCode", "String.fromCharCode obfuscation"),
        (r"atob\s*\(", "Base64 decode injection"),
    ]

    # ── Path Traversal Patterns ─────────────────────────────────────
    TRAVERSAL_PATTERNS = [
        (r"\.\./", "Unix path traversal"),
        (r"\.\.\\", "Windows path traversal"),
        (r"\.\.%2[fF]", "URL-encoded path traversal"),
        (r"\.\.%5[cC]", "URL-encoded Windows path traversal"),
        (r"%2e%2e[/\\]", "Double URL-encoded traversal"),
        (r"/etc/(passwd|shadow|hosts)", "Sensitive file access"),
        (r"/proc/self/", "Proc filesystem access"),
        (r"C:\\[Ww]indows", "Windows system directory access"),
    ]

    # ── Command Injection Patterns ──────────────────────────────────
    CMDI_PATTERNS = [
        (r"[;|]\s*(ls|cat|whoami|id|uname|curl|wget|nc|ncat)\b", "Command chaining"),
        (r"&&\s*(ls|cat|whoami|id|uname|curl|wget)\b", "Command chaining (&&)"),
        (r"\|\s*(ls|cat|whoami|id|uname|curl|wget)\b", "Command piping"),
        (r"`[^`]+`", "Backtick command substitution"),
        (r"\$\([^)]+\)", "Dollar-paren command substitution"),
        (r"\$\{[^}]+\}", "Variable expansion injection"),
        (r">\s*/dev/", "Device file access"),
        (r"mkfifo|nc\s+-|bash\s+-i|/bin/(ba)?sh", "Reverse shell attempt"),
    ]

    # ── SSRF Patterns ───────────────────────────────────────────────
    SSRF_PATTERNS = [
        (r"(?i)http://(127\.|0\.0\.0\.0|localhost|::1)", "Localhost SSRF"),
        (r"(?i)http://10\.\d+\.\d+\.\d+", "Internal IP SSRF (10.x)"),
        (r"(?i)http://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+", "Internal IP SSRF (172.x)"),
        (r"(?i)http://192\.168\.\d+\.\d+", "Internal IP SSRF (192.168.x)"),
        (r"(?i)http://169\.254\.169\.254", "Cloud metadata SSRF"),
        (r"(?i)file://", "File protocol SSRF"),
        (r"(?i)gopher://", "Gopher protocol SSRF"),
        (r"(?i)dict://", "Dict protocol SSRF"),
    ]

    # ── Header Injection Patterns ───────────────────────────────────
    HEADER_INJECTION_PATTERNS = [
        (r"[\r\n]\s*\w+:", "CRLF header injection"),
        (r"%0[da]%0[da]", "URL-encoded CRLF injection"),
    ]

    # ── Suspicious User-Agents ──────────────────────────────────────
    BOT_UA_PATTERNS = [
        (r"(?i)(sqlmap|nikto|nessus|burp|dirbuster|gobuster|wfuzz|hydra)", "Security scanner"),
        (r"(?i)(curl|wget|python-requests|go-http-client)/", "Script-based client"),
        (r"(?i)(nmap|masscan|zmap)", "Network scanner"),
    ]

    # ── Thresholds ──────────────────────────────────────────────────
    AUTO_BLOCK_THRESHOLD = 5  # threats before auto-block
    AUTO_BLOCK_DURATION = 3600  # 1 hour
    BRUTE_FORCE_WINDOW = 300  # 5 min window
    BRUTE_FORCE_THRESHOLD = 10  # failed logins before alert
    RATE_WINDOW = 60  # 1 min window
    RATE_THRESHOLD = 200  # requests per minute = anomaly
    MAX_THREAT_HISTORY = 10000  # keep last N threats

    def __init__(self):
        self._lock = threading.Lock()
        self._ip_suspicion: dict[str, IPSuspicion] = {}
        self._threat_history: deque[ThreatEvent] = deque(maxlen=self.MAX_THREAT_HISTORY)
        self._login_attempts: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=100))
        self._request_counts: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=1000))
        self._stats = {
            "total_inspected": 0,
            "total_threats": 0,
            "total_blocked": 0,
            "by_type": defaultdict(int),
            "by_level": defaultdict(int),
        }

    def inspect_request(
        self,
        ip: str,
        method: str,
        path: str,
        query: str = "",
        body: str = "",
        headers: dict | None = None,
        user_agent: str = "",
    ) -> list[ThreatEvent]:
        """Inspect a request for all known attack patterns.

        Returns a list of detected threats (empty if clean).
        """
        with self._lock:
            self._stats["total_inspected"] += 1

        threats: list[ThreatEvent] = []
        combined = f"{path} {query} {body}"
        headers = headers or {}

        # Skip inspection for static assets and health checks
        if path.startswith(("/static/", "/_next/", "/favicon", "/api/health")):
            return threats

        # Check if IP is already blocked
        with self._lock:
            sus = self._ip_suspicion.get(ip)
            if sus and sus.blocked:
                if time.time() - sus.blocked_at < self.AUTO_BLOCK_DURATION:
                    return [
                        self._make_threat(
                            ip,
                            path,
                            method,
                            AttackType.RATE_ANOMALY,
                            ThreatLevel.CRITICAL,
                            f"Request from blocked IP {ip}",
                            blocked=True,
                        )
                    ]
                else:
                    sus.blocked = False

        # ── Pattern matching ────────────────────────────────────────
        # Mask API keys in body before pattern matching — keys can contain 0x, --, /* etc.
        # that falsely trigger SQL injection / hex encoding patterns.
        import json as _json

        try:
            _parsed = _json.loads(body)
            if isinstance(_parsed, dict):
                for _k in ("api_key", "apiKey", "token", "secret", "key"):
                    if _k in _parsed and isinstance(_parsed[_k], str):
                        _parsed[_k] = "***"
                body = _json.dumps(_parsed)
                combined = f"{path} {query} {body}"
        except (ValueError, TypeError):
            pass

        for pattern, desc in self.SQLI_PATTERNS:
            if re.search(pattern, combined):
                threats.append(
                    self._make_threat(
                        ip,
                        path,
                        method,
                        AttackType.SQL_INJECTION,
                        ThreatLevel.CRITICAL,
                        desc,
                        matched_pattern=pattern,
                    )
                )

        for pattern, desc in self.XSS_PATTERNS:
            if re.search(pattern, combined):
                threats.append(
                    self._make_threat(
                        ip,
                        path,
                        method,
                        AttackType.XSS,
                        ThreatLevel.HIGH,
                        desc,
                        matched_pattern=pattern,
                    )
                )

        for pattern, desc in self.TRAVERSAL_PATTERNS:
            if re.search(pattern, combined):
                threats.append(
                    self._make_threat(
                        ip,
                        path,
                        method,
                        AttackType.PATH_TRAVERSAL,
                        ThreatLevel.HIGH,
                        desc,
                        matched_pattern=pattern,
                    )
                )

        for pattern, desc in self.CMDI_PATTERNS:
            if re.search(pattern, combined):
                threats.append(
                    self._make_threat(
                        ip,
                        path,
                        method,
                        AttackType.COMMAND_INJECTION,
                        ThreatLevel.CRITICAL,
                        desc,
                        matched_pattern=pattern,
                    )
                )

        for pattern, desc in self.SSRF_PATTERNS:
            if re.search(pattern, combined):
                threats.append(
                    self._make_threat(
                        ip,
                        path,
                        method,
                        AttackType.SSRF,
                        ThreatLevel.HIGH,
                        desc,
                        matched_pattern=pattern,
                    )
                )

        for pattern, desc in self.HEADER_INJECTION_PATTERNS:
            # Check in URL and header values
            header_str = " ".join(f"{k}: {v}" for k, v in headers.items())
            if re.search(pattern, f"{path} {header_str}"):
                threats.append(
                    self._make_threat(
                        ip,
                        path,
                        method,
                        AttackType.HEADER_INJECTION,
                        ThreatLevel.MEDIUM,
                        desc,
                        matched_pattern=pattern,
                    )
                )

        if user_agent:
            for pattern, desc in self.BOT_UA_PATTERNS:
                if re.search(pattern, user_agent):
                    threats.append(
                        self._make_threat(
                            ip,
                            path,
                            method,
                            AttackType.BOT_DETECTED,
                            ThreatLevel.MEDIUM,
                            f"{desc}: {user_agent[:80]}",
                            matched_pattern=pattern,
                        )
                    )

        # ── Rate anomaly detection ──────────────────────────────────
        now = time.time()
        with self._lock:
            self._request_counts[ip].append(now)
            # Count requests in the last minute
            cutoff = now - self.RATE_WINDOW
            recent = sum(1 for t in self._request_counts[ip] if t > cutoff)
            if recent > self.RATE_THRESHOLD:
                threats.append(
                    self._make_threat(
                        ip,
                        path,
                        method,
                        AttackType.RATE_ANOMALY,
                        ThreatLevel.HIGH,
                        f"Rate anomaly: {recent} requests in {self.RATE_WINDOW}s (threshold: {self.RATE_THRESHOLD})",
                    )
                )

        # Debug: log detected threats
        if threats:
            logging.getLogger(__name__).debug(
                "WAF threats: ip=%s path=%s threats=%s",
                ip,
                path,
                [(t.attack_type.value, t.threat_level.value, str(t)[:50]) for t in threats],
            )

        # ── Update suspicion scores ─────────────────────────────────
        if threats:
            self._record_threats(ip, threats)

        return threats

    def record_login_attempt(self, ip: str, success: bool, path: str = "/api/login"):
        """Record a login attempt for brute-force detection."""
        now = time.time()
        threat = None
        with self._lock:
            self._login_attempts[ip].append(now)
            if not success:
                # Count failed attempts in window
                cutoff = now - self.BRUTE_FORCE_WINDOW
                recent_fails = sum(1 for t in self._login_attempts[ip] if t > cutoff)
                if recent_fails >= self.BRUTE_FORCE_THRESHOLD:
                    threat = self._make_threat(
                        ip,
                        path,
                        "POST",
                        AttackType.BRUTE_FORCE,
                        ThreatLevel.HIGH,
                        f"Brute force: {recent_fails} failed logins in {self.BRUTE_FORCE_WINDOW}s",
                    )
        # F3b-⑨b-②b-2 顺手收口（Gate3 逼出的挡路真断点，根因修非守卫）：_record_threats
        # 自身取 self._lock（非重入 threading.Lock）——原在 with self._lock 内调用=自死锁，
        # 第 10 次失败登录永久挂死本端点并扣死全局锁（后续 inspect/block/stats 全挂，
        # 进程内复现实证）。威胁记录移出锁外=原设计语义（报警+计数+自动拉黑）恰一次。
        if threat is not None:
            self._record_threats(ip, [threat])
            return threat
        return None

    def get_threats(
        self,
        ip: str | None = None,
        attack_type: str | None = None,
        level: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query threat history with optional filters."""
        with self._lock:
            threats = list(self._threat_history)

        if ip:
            threats = [t for t in threats if t.source_ip == ip]
        if attack_type:
            threats = [t for t in threats if t.attack_type.value == attack_type]
        if level:
            threats = [t for t in threats if t.threat_level.value == level]

        # Sort by timestamp descending
        threats.sort(key=lambda t: t.timestamp, reverse=True)
        return [t.to_dict() for t in threats[:limit]]

    def get_blocked_ips(self) -> list[dict]:
        """List all currently blocked IPs."""
        with self._lock:
            result = []
            for ip, sus in self._ip_suspicion.items():
                if sus.blocked and (time.time() - sus.blocked_at < self.AUTO_BLOCK_DURATION):
                    result.append(
                        {
                            "ip": ip,
                            "threat_count": sus.threat_count,
                            "threat_types": [t.value for t in sus.threat_types],
                            "blocked_at": sus.blocked_at,
                            "expires_at": sus.blocked_at + self.AUTO_BLOCK_DURATION,
                            "first_seen": sus.first_seen,
                            "last_seen": sus.last_seen,
                        }
                    )
            return result

    def block_ip(self, ip: str, reason: str = "manual"):
        """Manually block an IP."""
        with self._lock:
            sus = self._ip_suspicion.setdefault(ip, IPSuspicion(ip=ip))
            sus.blocked = True
            sus.blocked_at = time.time()
            self._stats["total_blocked"] += 1

    def unblock_ip(self, ip: str):
        """Unblock an IP."""
        with self._lock:
            sus = self._ip_suspicion.get(ip)
            if sus:
                sus.blocked = False

    def stats(self) -> dict:
        """Get intrusion detection statistics."""
        with self._lock:
            active_blocks = sum(
                1
                for s in self._ip_suspicion.values()
                if s.blocked and (time.time() - s.blocked_at < self.AUTO_BLOCK_DURATION)
            )
            suspicious_ips = sum(
                1 for s in self._ip_suspicion.values() if s.threat_count > 0 and not s.blocked
            )
            return {
                "total_inspected": self._stats["total_inspected"],
                "total_threats": self._stats["total_threats"],
                "total_blocked": self._stats["total_blocked"],
                "active_blocks": active_blocks,
                "suspicious_ips": suspicious_ips,
                "tracked_ips": len(self._ip_suspicion),
                "by_type": dict(self._stats["by_type"]),
                "by_level": dict(self._stats["by_level"]),
                "recent_threats": len(self._threat_history),
                "thresholds": {
                    "auto_block_threshold": self.AUTO_BLOCK_THRESHOLD,
                    "auto_block_duration_s": self.AUTO_BLOCK_DURATION,
                    "brute_force_threshold": self.BRUTE_FORCE_THRESHOLD,
                    "rate_threshold_per_min": self.RATE_THRESHOLD,
                },
            }

    def _make_threat(
        self,
        ip: str,
        path: str,
        method: str,
        attack_type: AttackType,
        level: ThreatLevel,
        detail: str,
        matched_pattern: str = "",
        blocked: bool = False,
    ) -> ThreatEvent:
        import uuid

        return ThreatEvent(
            threat_id=str(uuid.uuid4())[:8],
            attack_type=attack_type,
            threat_level=level,
            source_ip=ip,
            path=path,
            method=method,
            detail=detail,
            matched_pattern=matched_pattern,
            blocked=blocked,
        )

    def _record_threats(self, ip: str, threats: list[ThreatEvent]):
        """Record threats and auto-block if threshold exceeded."""
        with self._lock:
            sus = self._ip_suspicion.setdefault(ip, IPSuspicion(ip=ip))
            for threat in threats:
                sus.threat_count += 1
                sus.threat_types.add(threat.attack_type)
                sus.last_seen = time.time()
                self._threat_history.append(threat)
                self._stats["total_threats"] += 1
                self._stats["by_type"][threat.attack_type.value] = (
                    self._stats["by_type"].get(threat.attack_type.value, 0) + 1
                )
                self._stats["by_level"][threat.threat_level.value] = (
                    self._stats["by_level"].get(threat.threat_level.value, 0) + 1
                )

            # Auto-block if threshold exceeded
            if sus.threat_count >= self.AUTO_BLOCK_THRESHOLD and not sus.blocked:
                sus.blocked = True
                sus.blocked_at = time.time()
                self._stats["total_blocked"] += 1
                for t in threats:
                    t.blocked = True
