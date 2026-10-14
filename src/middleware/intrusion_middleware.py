"""Middleware for intrusion detection — inspects all incoming requests."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class IntrusionDetectionMiddleware(BaseHTTPMiddleware):
    """Middleware that inspects all requests for attack patterns.

    Integrates with the IntrusionDetector singleton to:
    - Scan URL paths, query params, and headers for attacks
    - Auto-block IPs after repeated violations
    - Log threats to the audit trail
    """

    # Paths that bypass detection (static, health, docs)
    SKIP_PREFIXES = ("/static/", "/_next/", "/favicon", "/docs", "/openapi.json", "/redoc")

    def __init__(self, app, detector=None):
        super().__init__(app)
        self._detector = detector

    def _get_detector(self):
        """Lazy-load detector to avoid circular imports."""
        if self._detector is None:
            try:
                from src.api.immune import intrusion

                self._detector = intrusion
            except Exception:
                return None
        return self._detector

    async def dispatch(self, request: Request, call_next):
        detector = self._get_detector()
        if detector is None:
            return await call_next(request)

        path = request.url.path

        # Skip static assets and health checks
        if any(path.startswith(p) for p in self.SKIP_PREFIXES):
            return await call_next(request)

        # Skip the immune API itself to avoid recursive detection
        if path.startswith("/api/immune/"):
            return await call_next(request)

        # Extract request info
        ip = request.client.host if request.client else "unknown"
        method = request.method

        # Whitelist localhost — the frontend (OpenMate) and cron jobs call from the same machine
        if ip in ("127.0.0.1", "::1", "localhost"):
            return await call_next(request)
        query = str(request.url.query) if request.url.query else ""
        user_agent = request.headers.get("user-agent", "")

        # Build headers dict (only safe headers, not huge ones)
        headers = {}
        for key in ("content-type", "authorization", "x-forwarded-for", "x-real-ip", "referer"):
            val = request.headers.get(key)
            if val:
                headers[key] = val[:200]  # truncate very long values

        # Read body for POST/PUT requests (limited to 8KB for inspection)
        body = ""
        if method in ("POST", "PUT", "PATCH"):
            try:
                body_bytes = await request.body()
                body = body_bytes[:8192].decode("utf-8", errors="replace")
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
        # Run detection
        start = time.time()
        threats = detector.inspect_request(
            ip=ip,
            method=method,
            path=path,
            query=query,
            body=body,
            headers=headers,
            user_agent=user_agent,
        )
        elapsed_ms = (time.time() - start) * 1000

        # If any critical threats detected, block the request
        critical = [t for t in threats if t.threat_level.value == "critical"]
        high = [t for t in threats if t.threat_level.value == "high"]

        if critical or len(high) >= 3:
            logger.warning(
                "Intrusion blocked: ip=%s path=%s threats=%d (%.1fms)",
                ip,
                path,
                len(threats),
                elapsed_ms,
            )
            # Emit event for blocked request
            try:
                from src.nerve.event_bridge import push_event

                # 系统事件归属（DEC-① 1d 裁决＝维持 'default' 兜底）：安全拦截史
                # 归系统，事件刻意不带 tenant_id（record() 落 'default'）；不按
                # 请求身份派生——攻击者视角的拦截记录挂到受害者租户名下反而失真。
                push_event(
                    {
                        "organ": "immune",
                        "emoji": "🛡",
                        "type": "request_blocked",
                        "summary": f"🚫 Request blocked from {ip}: {path} ({len(threats)} threats)",
                        "detail": {
                            "ip": ip,
                            "path": path,
                            "method": method,
                            "threat_count": len(threats),
                            "threat_types": list(set(t.attack_type.value for t in threats)),
                        },
                    }
                )
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Request blocked by intrusion detection",
                    "detail": "Your request contains patterns associated with known attack types.",
                    "threat_count": len(threats),
                },
            )

        # For non-critical threats, proceed but log
        if threats and elapsed_ms < 100:
            logger.info(
                "Threats detected (passing): ip=%s path=%s threats=%d",
                ip,
                path,
                len(threats),
            )

        response = await call_next(request)
        return response
