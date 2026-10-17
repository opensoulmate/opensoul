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


# ── Symptom strings produced by diagnose() (single source, HEAL-②b-②) ─
# 产生侧字面单一源：produce 点与 _symptom_kind 分类共用，禁两侧手抄
# （F4b-㉗ 纪律）。

_SYMPTOM_CONNECTION = "connection refused"
_SYMPTOM_TIMEOUT = "timeout"
_SYMPTOM_HTTP_PREFIX = "HTTP "
_SYMPTOM_EXCEPTION_PREFIX = "exception: "
_SYMPTOM_SLOW_PREFIX = "slow response:"
_SYMPTOM_UNRECOGNIZED_PREFIX = "service reports unrecognized status:"

# ── Known organ repair strategies ─────────────────────────
# 触发对齐（HEAL-②b-②）：triggers = diagnose() 在不健康路径真能产生的规范症状类。
# 修前 trigger 是自由文本模式（"cache miss or expired" 等器官内部状态描述），
# 与产生面词表零交集=0/6 可达（HEAL-② 审计，推荐与修复双双走不到）。
# 映射依据=动作语义（诚实边界：仓内无器官自报内部状态的产生面，本映射
# 是策略选择可另拍）：
#   degraded（自报降级=积累/磨损）→ 清理型维护（cleanup 系：清过期留有效）
#   timeout（健康口超时=过载）→ 清缓存（快速减负）
# down/error/unrecognized/HTTP/exception/connection 无对症维护动作，不触发
# （宁缺勿滥）。同器官多策略按症状类分工，防首匹配遮蔽（可达性测试 6/6 锁死）。

_REPAIR_STRATEGIES: dict[str, list[dict]] = {
    "vein": [
        {
            "triggers": (_SYMPTOM_TIMEOUT,),
            "action": Action.CLEAR_CACHE,
            "endpoint": "/api/vein/cache/clear",
            "method": "POST",
            "description": "Clear Vein cache to relieve overload (timeout)",
        },
        {
            "triggers": ("degraded",),
            "action": Action.CLEANUP,
            "endpoint": "/api/vein/cache/cleanup",
            "method": "POST",
            "description": "Cleanup expired cache entries (degraded self-report)",
        },
    ],
    "reflex": [
        {
            "triggers": ("degraded", _SYMPTOM_TIMEOUT),
            "action": Action.CLEAR_CACHE,
            "endpoint": "/api/reflex/cache/clear",
            "method": "POST",
            "description": "Clear Reflex response cache",
        },
    ],
    "mirror": [
        {
            "triggers": ("degraded", _SYMPTOM_TIMEOUT),
            "action": Action.CLEANUP,
            "endpoint": "/api/mirror/cleanup",
            "method": "POST",
            "description": "Cleanup expired sandboxes",
        },
    ],
    "hippo": [
        {
            "triggers": ("degraded", _SYMPTOM_TIMEOUT),
            "action": Action.CLEANUP,
            "endpoint": "/api/hippo/sessions/lifecycle-check",
            "method": "POST",
            "description": "Run session lifecycle check (mark idle/expired sessions)",
        },
    ],
    "immune": [
        {
            "triggers": ("degraded", _SYMPTOM_TIMEOUT),
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
        f"{_SYMPTOM_UNRECOGNIZED_PREFIX} {shown}",
        f"component reports unrecognized status '{shown}'",
    )


_STATUS_SYMPTOM_KINDS: dict[str, str] = {
    verdict.symptom: word for word, verdict in _STATUS_VERDICTS.items()
}


def _symptom_kind(symptom: str) -> str | None:
    """观测症状串 → 规范症状类（策略触发词汇）。识别不了 → None，不硬凑。

    状态类从 _STATUS_VERDICTS 反查派生（禁手抄字面）；传输类按产生串
    常量归类。触发词汇=产生面词汇：测试构造症状也必须用产生面形态，
    禁再造第二词表。
    """
    s = symptom.strip()
    kind = _STATUS_SYMPTOM_KINDS.get(s)
    if kind is not None:
        return kind
    if s == _SYMPTOM_CONNECTION:
        return _SYMPTOM_CONNECTION
    if s == _SYMPTOM_TIMEOUT:
        return _SYMPTOM_TIMEOUT
    if s.startswith(_SYMPTOM_HTTP_PREFIX):
        return "http error"
    if s.startswith(_SYMPTOM_EXCEPTION_PREFIX):
        return "exception"
    if s.startswith(_SYMPTOM_SLOW_PREFIX):
        return "slow response"
    if s.startswith(_SYMPTOM_UNRECOGNIZED_PREFIX):
        return "unrecognized"
    return None


def _matches_trigger(symptoms: list[str], triggers) -> bool:
    """症状命中策略触发类：观测症状串归类后与 triggers 求交（词表同源对齐）。"""
    return any(_symptom_kind(s) in triggers for s in symptoms)


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
                    result.symptoms.append(f"{_SYMPTOM_HTTP_PREFIX}{resp.status_code}")
                    result.root_cause = f"health endpoint returned {resp.status_code}"
                    result.detail = {"status_code": resp.status_code}

        except httpx.ConnectError:
            result.healthy = False
            result.severity = Severity.CRITICAL
            result.symptoms.append(_SYMPTOM_CONNECTION)
            result.root_cause = "service not running or port not listening"
            result.response_time_ms = round((time.time() - start) * 1000, 1)

        except httpx.TimeoutException:
            result.healthy = False
            result.severity = Severity.WARNING
            result.symptoms.append(_SYMPTOM_TIMEOUT)
            result.root_cause = "health check timed out — service may be overloaded"
            result.response_time_ms = round((time.time() - start) * 1000, 1)

        except Exception as e:
            result.healthy = False
            result.severity = Severity.CRITICAL
            result.symptoms.append(f"{_SYMPTOM_EXCEPTION_PREFIX}{type(e).__name__}")
            result.root_cause = str(e)
            result.response_time_ms = round((time.time() - start) * 1000, 1)

        # Check response time thresholds
        if result.healthy and result.response_time_ms > 5000:
            result.symptoms.append(f"{_SYMPTOM_SLOW_PREFIX} {result.response_time_ms}ms")
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
            if _matches_trigger(result.symptoms, strategy.get("triggers", ())):
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
        """推荐=可执行（声明从实现派生，HEAL-②b-②）：只推荐 heal() 真有执行路径的动作。

        修前两类假承诺（HEAL-② 审计连带死路）：① 策略触发词表与产生面零交集，
        推荐的策略动作 heal() 永远匹配不上=推荐即死；② connection refused 推荐
        RESTART 而 heal()/全仓无 restart 执行路径=推荐即死。现 ① 触发同源对齐、
        ② RESTART 仅在该器官注册了自定义处理器（restart 的真实实现）时推荐，
        否则如实 NONE（root_cause 已说明服务未监听，无进程内修复手段）。
        """
        strategies = _REPAIR_STRATEGIES.get(organ, [])
        for strategy in strategies:
            if _matches_trigger(result.symptoms, strategy.get("triggers", ())):
                return strategy["action"]

        kinds = {_symptom_kind(s) for s in result.symptoms}
        # 通用兑底：超时（过载）→ 清缓存（generic /api/{organ}/cache/clear）
        if _SYMPTOM_TIMEOUT in kinds:
            return Action.CLEAR_CACHE
        # connection refused：进程内无可执行修复（服务未监听，重启=进程外动作），
        # 仅当有自定义处理器（restart 实现）才推荐 RESTART（修前恒推荐=假承诺）
        if _SYMPTOM_CONNECTION in kinds and organ in self._custom_handlers:
            return Action.RESTART
        # "slow response" 推荐分支已清（HEAL-② 死路 (c)）：该症状只在
        # healthy=True 产生面出现，recommend 只跑不健康路径=死分支
        return Action.NONE

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
