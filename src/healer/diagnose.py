"""OpenHealer — Auto-diagnosis and self-healing for Open-Soulmate organs.

Monitors organ health, diagnoses failures, attempts automatic recovery,
and notifies through OpenEcho when issues are detected or resolved.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

import httpx


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    RECOVERED = "recovered"


class Action(StrEnum):
    NONE = "none"
    RESTART = "restart"
    CLEAR_CACHE = "clear_cache"
    CLEANUP = "cleanup"
    RECONNECT_DB = "reconnect_db"
    CUSTOM = "custom"


@dataclass
class DiagnosisResult:
    organ: str
    healthy: bool
    severity: Severity
    symptoms: list[str] = field(default_factory=list)
    root_cause: str = ""
    recommended_action: Action = Action.NONE
    action_taken: Action = Action.NONE
    action_success: bool = False
    response_time_ms: float = 0
    detail: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ── Known organ repair strategies ─────────────────────────────

_REPAIR_STRATEGIES: dict[str, list[dict]] = {
    "vein": [
        {
            "symptom": "cache miss or expired",
            "action": Action.CLEAR_CACHE,
            "endpoint": "/api/vein/cache/clear",
            "method": "POST",
            "description": "Clear Vein cache to resolve stale entries",
        },
        {
            "symptom": "expired entries",
            "action": Action.CLEANUP,
            "endpoint": "/api/vein/cache/cleanup",
            "method": "POST",
            "description": "Cleanup expired cache entries",
        },
    ],
    "reflex": [
        {
            "symptom": "cache full or degraded",
            "action": Action.CLEAR_CACHE,
            "endpoint": "/api/reflex/cache/clear",
            "method": "POST",
            "description": "Clear Reflex response cache",
        },
    ],
    "mirror": [
        {
            "symptom": "expired sandbox",
            "action": Action.CLEANUP,
            "endpoint": "/api/mirror/cleanup",
            "method": "POST",
            "description": "Cleanup expired sandboxes",
        },
    ],
    "hippo": [
        {
            "symptom": "expired session",
            "action": Action.CLEANUP,
            "endpoint": "/api/hippo/sessions/cleanup",
            "method": "POST",
            "description": "Cleanup expired memory sessions",
        },
    ],
    "immune": [
        {
            "symptom": "audit log full",
            "action": Action.CLEANUP,
            "endpoint": "/api/immune/audit/cleanup",
            "method": "POST",
            "description": "Cleanup old audit log entries",
        },
    ],
}

# ── Organs that depend on external services ───────────────────

_EXTERNAL_DEPS: dict[str, list[str]] = {
    "soul": ["postgres", "qdrant", "meilisearch"],
    "gland": ["llm_providers"],
    "sense": ["tesseract"],
}


# ── Health status vocabulary (single source, HEAL-②b-①) ──────
# 真实产出普查（2026-10-13，全仓 health 端点 + vital Status 枚举）：
#   健康自报 = "ok"（众器官 health 口）/"healthy"（brain）/"up"（vital Status.UP）
#   degraded 自报 = "degraded"（main/echo 汇总口、vital Status.DEGRADED）
#   down 自报 = "down"（vital Status.DOWN 组件级汇总）/"error"（C2 错误回包
#   200 带 status=error）
# 判定哲学（HEAL-② 审计③收口）：HTTP 200 只说明健康口可达，器官自报状态才是
# 健康语义真源——「200 即健康」= 谎报（审计 live 硬证：vital 报 {"status": "down"}
# 被诊断 healthy=True 零症状）。词表禁各判定点手抄（F4b-㉗ 纪律）。


@dataclass(frozen=True)
class _StatusVerdict:
    severity: Severity
    symptom: str
    root_cause: str


_STATUS_HEALTHY = frozenset({"ok", "healthy", "up"})

_STATUS_VERDICTS: dict[str, _StatusVerdict] = {
    "degraded": _StatusVerdict(
        Severity.WARNING,
        "service reports degraded status",
        "component is running but degraded",
    ),
    "down": _StatusVerdict(
        Severity.CRITICAL,
        "service reports down status",
        "component reports down status",
    ),
    "error": _StatusVerdict(
        Severity.CRITICAL,
        "service reports error status",
        "component reports error status",
    ),
}


def _classify_status(status: object) -> _StatusVerdict | None:
    """判定健康口回包 status 词。None = 健康；非 None = 不健康（带症状/严重度）。

    词表单一源：健康词 → None；degraded → WARNING（既有契约逐字保留）；
    down 词（down/error）→ CRITICAL；识别不了的非空状态 → WARNING 如实不判
    健康（禁谎报健康，也不过度定罪 CRITICAL）；显式 null/空串 = 非健康自报，
    同未识别（缺失键在调用点按 "ok" 缺省，既有契约不变）。
    """
    if isinstance(status, str):
        word = status.strip().lower()
    else:
        word = "" if status is None else str(status).strip().lower()
    if word in _STATUS_HEALTHY:
        return None
    verdict = _STATUS_VERDICTS.get(word)
    if verdict is not None:
        return verdict
    shown = word or "<empty>"
    return _StatusVerdict(
        Severity.WARNING,
        f"service reports unrecognized status: {shown}",
        f"component reports unrecognized status '{shown}'",
    )


class OrganHealer:
    """Auto-diagnose and heal organ failures."""

    def __init__(self, base_url: str = "http://127.0.0.1:8090"):
        self._base = base_url
        self._history: list[DiagnosisResult] = []
        self._max_history = 500
        self._custom_handlers: dict[str, Callable[[], Awaitable[bool]]] = {}

    def register_handler(self, organ: str, handler: Callable[[], Awaitable[bool]]):
        """Register a custom healing handler for an organ."""
        self._custom_handlers[organ] = handler

    # ── Diagnosis ─────────────────────────────────────────────

    async def diagnose(self, organ: str, endpoint: str) -> DiagnosisResult:
        """Deep-diagnose a single organ: hit health endpoint, analyze response."""
        result = DiagnosisResult(organ=organ, healthy=False, severity=Severity.INFO)
        start = time.time()

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base}{endpoint}")
                result.response_time_ms = round((time.time() - start) * 1000, 1)

                if resp.status_code == 200:
                    result.healthy = True
                    result.severity = Severity.INFO
                    body = resp.json() if "json" in resp.headers.get("content-type", "") else {}

                    # 状态词表判定（HEAL-②b-①）：200 只代表健康口可达，器官
                    # 自报状态才是健康语义真源——非健康状态如实判不健康进症状面
                    # （修前只认 "degraded"，down/error 等被判 healthy=True）
                    status = body.get("status", "ok")
                    verdict = _classify_status(status)
                    if verdict is not None:
                        result.healthy = False
                        result.severity = verdict.severity
                        result.symptoms.append(verdict.symptom)
                        result.root_cause = verdict.root_cause

                    result.detail = body
                else:
                    result.healthy = False
                    result.severity = Severity.CRITICAL
                    result.symptoms.append(f"HTTP {resp.status_code}")
                    result.root_cause = f"health endpoint returned {resp.status_code}"
                    result.detail = {"status_code": resp.status_code}

        except httpx.ConnectError:
            result.healthy = False
            result.severity = Severity.CRITICAL
            result.symptoms.append("connection refused")
            result.root_cause = "service not running or port not listening"
            result.response_time_ms = round((time.time() - start) * 1000, 1)

        except httpx.TimeoutException:
            result.healthy = False
            result.severity = Severity.WARNING
            result.symptoms.append("timeout")
            result.root_cause = "health check timed out — service may be overloaded"
            result.response_time_ms = round((time.time() - start) * 1000, 1)

        except Exception as e:
            result.healthy = False
            result.severity = Severity.CRITICAL
            result.symptoms.append(f"exception: {type(e).__name__}")
            result.root_cause = str(e)
            result.response_time_ms = round((time.time() - start) * 1000, 1)

        # Check response time thresholds
        if result.healthy and result.response_time_ms > 5000:
            result.symptoms.append(f"slow response: {result.response_time_ms}ms")
            if result.severity == Severity.INFO:
                result.severity = Severity.WARNING

        # Determine recommended action
        if not result.healthy:
            result.recommended_action = self._recommend_action(organ, result)

        self._record(result)
        return result

    async def diagnose_all(self, organs: dict[str, str]) -> list[DiagnosisResult]:
        """Diagnose all organs in parallel. organs = {name: health_endpoint}."""
        tasks = [self.diagnose(name, ep) for name, ep in organs.items()]
        return await asyncio.gather(*tasks)

    # ── Healing ───────────────────────────────────────────────

    async def heal(self, result: DiagnosisResult) -> DiagnosisResult:
        """Attempt to heal a diagnosed organ."""
        if result.healthy:
            return result

        action = result.recommended_action
        if action == Action.NONE:
            return result

        # Try custom handler first
        if result.organ in self._custom_handlers:
            try:
                success = await self._custom_handlers[result.organ]()
                result.action_taken = Action.CUSTOM
                result.action_success = success
                if success:
                    result.severity = Severity.RECOVERED
                return result
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
        # Try built-in repair strategies
        strategies = _REPAIR_STRATEGIES.get(result.organ, [])
        for strategy in strategies:
            if self._matches_symptom(result.symptoms, strategy.get("symptom", "")):
                success = await self._execute_repair(strategy)
                result.action_taken = strategy["action"]
                result.action_success = success
                if success:
                    result.severity = Severity.RECOVERED
                return result

        # Generic attempt: try clearing cache if available
        if action == Action.CLEAR_CACHE:
            success = await self._try_generic_clear_cache(result.organ)
            result.action_taken = Action.CLEAR_CACHE
            result.action_success = success
            if success:
                result.severity = Severity.RECOVERED

        return result

    async def heal_all(self, results: list[DiagnosisResult]) -> list[DiagnosisResult]:
        """Attempt to heal all failed organs."""
        tasks = []
        for r in results:
            if not r.healthy and r.recommended_action != Action.NONE:
                tasks.append(self.heal(r))
            else:
                tasks.append(asyncio.coroutine(lambda r=r: r)() if False else self._noop(r))
        return await asyncio.gather(*tasks)

    async def _noop(self, r: DiagnosisResult) -> DiagnosisResult:
        return r

    # ── Diagnosis + Heal combined ─────────────────────────────

    async def diagnose_and_heal(self, organ: str, endpoint: str) -> DiagnosisResult:
        """Diagnose an organ, then attempt healing if unhealthy."""
        result = await self.diagnose(organ, endpoint)
        if not result.healthy:
            result = await self.heal(result)
            # Re-check after healing
            if result.action_success:
                recheck = await self.diagnose(organ, endpoint)
                if recheck.healthy:
                    result.severity = Severity.RECOVERED
                    result.healthy = True
        return result

    # ── Notification ──────────────────────────────────────────

    async def notify(
        self, results: list[DiagnosisResult], echo_endpoint: str = "/api/echo/send"
    ) -> dict:
        """Send notification summary through OpenEcho."""
        failed = [r for r in results if not r.healthy and r.severity != Severity.RECOVERED]
        recovered = [r for r in results if r.severity == Severity.RECOVERED]

        if not failed and not recovered:
            return {"notified": False, "reason": "all healthy"}

        # Build notification content
        title_parts = []
        content_parts = []

        if failed:
            title_parts.append(f"⚠️ {len(failed)} organ(s) unhealthy")
            for r in failed:
                content_parts.append(
                    f"❌ **{r.organ}**: {r.root_cause}\n"
                    f"   Symptoms: {', '.join(r.symptoms)}\n"
                    f"   Action: {r.action_taken.value} → {'✅ success' if r.action_success else '❌ failed'}"
                )

        if recovered:
            title_parts.append(f"✅ {len(recovered)} recovered")
            for r in recovered:
                content_parts.append(f"✅ **{r.organ}**: recovered via {r.action_taken.value}")

        title = " | ".join(title_parts)
        content = "\n\n".join(content_parts)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base}{echo_endpoint}",
                    json={
                        "channel": "console",
                        "title": f"[Healer] {title}",
                        "content": content,
                        "priority": 1 if failed else 5,
                    },
                )
                return {
                    "notified": True,
                    "status": resp.status_code,
                    "failed_count": len(failed),
                    "recovered_count": len(recovered),
                }
        except Exception as e:
            return {"notified": False, "error": str(e)}

    # ── Audit ─────────────────────────────────────────────────

    async def audit_log(self, results: list[DiagnosisResult]) -> None:
        """Log healing actions to immune audit trail."""
        actions_log = []
        for r in results:
            if r.action_taken != Action.NONE:
                actions_log.append(
                    {
                        "organ": r.organ,
                        "action": r.action_taken.value,
                        "success": r.action_success,
                        "root_cause": r.root_cause,
                        "timestamp": r.timestamp,
                    }
                )

        if not actions_log:
            return

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{self._base}/api/nerve/event",
                    json={
                        "organ": "healer",
                        "emoji": "💊",
                        "type": "healing_cycle",
                        "summary": f"Healing cycle: {len(actions_log)} action(s) taken",
                        "detail": {"actions": actions_log},
                    },
                )
        except Exception:
            pass  # Best effort

    # ── Stats & History ───────────────────────────────────────

    @property
    def history(self) -> list[dict]:
        return [
            {
                "organ": r.organ,
                "healthy": r.healthy,
                "severity": r.severity.value,
                "symptoms": r.symptoms,
                "root_cause": r.root_cause,
                "action_taken": r.action_taken.value,
                "action_success": r.action_success,
                "response_time_ms": r.response_time_ms,
                "timestamp": r.timestamp,
            }
            for r in self._history[-100:]
        ]

    def stats(self) -> dict:
        total = len(self._history)
        healed = sum(1 for r in self._history if r.severity == Severity.RECOVERED)
        failed = sum(1 for r in self._history if not r.healthy and r.severity != Severity.RECOVERED)
        actions_taken = sum(1 for r in self._history if r.action_taken != Action.NONE)
        actions_succeeded = sum(1 for r in self._history if r.action_success)

        # Organ failure frequency
        organ_failures: dict[str, int] = {}
        for r in self._history:
            if not r.healthy:
                organ_failures[r.organ] = organ_failures.get(r.organ, 0) + 1

        return {
            "total_diagnoses": total,
            "healed": healed,
            "failed": failed,
            "actions_taken": actions_taken,
            "actions_succeeded": actions_succeeded,
            "success_rate": round(actions_succeeded / actions_taken * 100, 1)
            if actions_taken
            else 0,
            "organ_failure_frequency": dict(
                sorted(organ_failures.items(), key=lambda x: -x[1])[:10]
            ),
            "recent_healthy_rate": self._recent_healthy_rate(),
        }

    # ── Internal helpers ──────────────────────────────────────

    def _record(self, result: DiagnosisResult):
        self._history.append(result)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

    def _recommend_action(self, organ: str, result: DiagnosisResult) -> Action:
        """Determine the best recovery action based on symptoms."""
        symptoms_text = " ".join(result.symptoms).lower()

        strategies = _REPAIR_STRATEGIES.get(organ, [])
        for strategy in strategies:
            if self._matches_symptom(result.symptoms, strategy.get("symptom", "")):
                return strategy["action"]

        # Generic fallbacks
        if "timeout" in symptoms_text or "slow" in symptoms_text:
            return Action.CLEAR_CACHE
        if "connection" in symptoms_text:
            return Action.RESTART

        return Action.NONE

    def _matches_symptom(self, symptoms: list[str], pattern: str) -> bool:
        pattern_lower = pattern.lower()
        return any(pattern_lower in s.lower() for s in symptoms)

    async def _execute_repair(self, strategy: dict) -> bool:
        """Execute a repair strategy by calling the endpoint."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                method = strategy.get("method", "POST").upper()
                url = f"{self._base}{strategy['endpoint']}"
                if method == "POST":
                    resp = await client.post(url)
                elif method == "GET":
                    resp = await client.get(url)
                else:
                    return False
                return resp.status_code == 200
        except Exception:
            return False

    async def _try_generic_clear_cache(self, organ: str) -> bool:
        """Try to clear cache for an organ."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{self._base}/api/{organ}/cache/clear")
                return resp.status_code == 200
        except Exception:
            return False

    def _recent_healthy_rate(self) -> float:
        """Calculate healthy rate for last 50 diagnoses."""
        recent = self._history[-50:]
        if not recent:
            return 100.0
        healthy = sum(1 for r in recent if r.healthy)
        return round(healthy / len(recent) * 100, 1)
