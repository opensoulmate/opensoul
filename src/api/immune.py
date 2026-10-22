"""OpenImmune API — 免疫系统：内容风控、限流、IP管控、安全审计。"""

import logging

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

from src.immune.access_control import IPAccessControl
from src.immune.access_persist import AccessPersistence
from src.immune.audit import AuditAction, AuditLogger
from src.immune.audit_persist import AuditPersistence
from src.immune.intrusion import IntrusionDetector
from src.immune.moderator import ContentModerator
from src.immune.permission_engine import PermissionMode, PermissionService
from src.immune.rate_limiter import RateLimitConfig, RateLimiter
from src.nerve.event_bridge import push_event
from src.services.tenant_binding import bind_identity

logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)
router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
rate_limiter = RateLimiter()
moderator = ContentModerator()
# RV-①b-② 生产接线：黑白名单持久化（构造期回载+增删/命中写穿，重启封禁不丢）。
ip_control = IPAccessControl(persist=AccessPersistence())
# RV-①b-⑧ 生产接线：安全审计留痕持久化（log() 写穿+首触懒回载，重启审计史不丢）。
audit = AuditLogger(persist=AuditPersistence())
intrusion = IntrusionDetector()
permission_service = PermissionService()  # P0-3 工具级权限引擎


# ── Request Schemas ────────────────────────────────────────


class ModerateRequest(BaseModel):
    text: str


class IPActionRequest(BaseModel):
    ip: str
    reason: str = ""
    ttl_seconds: int | None = None


class RateLimitCheckRequest(BaseModel):
    key: str


# ── Content Moderation ─────────────────────────────────────


@router.post("/moderate")
async def moderate_text(
    req: ModerateRequest,
    authorization: str | None = Header(None),
):
    """Scan text for sensitive data (PII, secrets, etc.)."""
    identity = bind_identity(authorization)
    result = moderator.moderate(req.text)

    if not result.is_safe:
        audit.log(
            AuditAction.CONTENT_BLOCKED,
            detail=f"risk={result.risk_level}, findings={len(result.findings)}",
            risk_level=result.risk_level,
        )
        push_event(
            {
                "organ": "immune",
                "emoji": "🛡",
                "type": "content_blocked",
                "summary": f"⚠️ Content blocked: risk={result.risk_level}, {len(result.findings)} finding(s)",
                "detail": {"risk_level": result.risk_level, "findings_count": len(result.findings)},
            },
            tenant_id=identity.tenant_id,
        )
        # Push notification for high-risk content
        if result.risk_level in ("high", "critical"):
            try:
                from src.api.notifications import push_notification

                push_notification(
                    source="immune",
                    title=f"🛡 Content Blocked: {result.risk_level}",
                    body=f"{len(result.findings)} sensitive data finding(s) detected",
                    level="warning" if result.risk_level == "high" else "error",
                    organ="immune",
                    emoji="🛡",
                    action_url="/immune",
                    metadata={
                        "risk_level": result.risk_level,
                        "findings_count": len(result.findings),
                    },
                )
            except Exception as exc:
                logging.getLogger(__name__).debug("probe skipped: %s", exc)
    return {
        "is_safe": result.is_safe,
        "risk_level": result.risk_level,
        "findings": [
            {"type": f["type"], "label": f["label"], "risk": f["risk"]} for f in result.findings
        ],
        "redacted_text": result.redacted_text,
        "original_length": result.original_length,
    }


class ScanTextRequest(BaseModel):
    text: str
    min_risk: str = "low"  # low / medium / high / critical — only return findings >= this level


@router.post("/scan-text")
async def scan_text_for_display(req: ScanTextRequest):
    """P1流式输出脱敏展示：为前端内联显示扫描文本中的敏感数据。

    与/moderate的区别：
    - 返回position（start/end偏移量），前端可精确高亮/替换匹配片段
    - 不返回matched原文（前端只需位置+类型即可渲染脱敏徽章）
    - 支持min_risk过滤（只关注高风险项，避免PII噪声）
    - 不触发审计日志/事件推送（只读扫描，不改变系统状态）

    参照：Warp secret_redaction（hover点击揭示UX）— 前端渲染层消费此API。
    """
    result = moderator.moderate(req.text)
    risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    threshold = risk_order.get(req.min_risk, 0)

    findings = []
    for f in result.findings:
        if risk_order.get(f["risk"], 0) < threshold:
            continue
        findings.append(
            {
                "type": f["type"],
                "label": f["label"],
                "risk": f["risk"],
                "start": f["position"][0],
                "end": f["position"][1],
            }
        )

    return {
        "risk_level": result.risk_level,
        "total_findings": len(findings),
        "findings": findings,
        "original_length": result.original_length,
    }


# ── Rate Limiting ──────────────────────────────────────────


@router.post("/rate-limit/check")
async def check_rate_limit(req: RateLimitCheckRequest, request: Request):
    """Check rate limit for a given key."""
    result = rate_limiter.check(req.key)

    if not result["allowed"]:
        audit.log(
            AuditAction.RATE_LIMITED,
            client_ip=request.client.host if request.client else "",
            endpoint=req.key,
            detail=f"minute={result['minute_count']}, hour={result['hour_count']}",
            risk_level="medium",
        )

    return result


@router.get("/rate-limit/stats")
async def rate_limit_stats():
    """Get rate limiter statistics."""
    return rate_limiter.stats()


@router.post("/rate-limit/reset")
async def reset_rate_limit(key: str = Query(default=None)):
    """Reset rate limit counters."""
    rate_limiter.reset(key)
    return {"message": "reset", "key": key or "all"}


@router.put("/rate-limit/config")
async def update_rate_limit_config(
    requests_per_minute: int = Query(default=60),
    requests_per_hour: int = Query(default=1000),
    burst_size: int = Query(default=20),
):
    """Update rate limit configuration."""
    rate_limiter.config = RateLimitConfig(
        requests_per_minute=requests_per_minute,
        requests_per_hour=requests_per_hour,
        burst_size=burst_size,
    )
    return {"message": "config updated", "config": rate_limiter.config.__dict__}


# ── IP Access Control ──────────────────────────────────────


@router.post("/ip/blacklist")
async def blacklist_ip(
    req: IPActionRequest,
    request: Request,
    authorization: str | None = Header(None),
):
    """Add IP to blacklist."""
    identity = bind_identity(authorization)
    ip_control.blacklist_add(req.ip, req.reason, req.ttl_seconds)
    audit.log(
        AuditAction.CONFIG_CHANGE,
        client_ip=request.client.host if request.client else "",
        detail=f"blacklisted {req.ip}: {req.reason}",
        risk_level="medium",
    )
    push_event(
        {
            "organ": "immune",
            "emoji": "🛡",
            "type": "ip_blacklisted",
            "summary": f"🚫 IP blacklisted: {req.ip} — {req.reason}",
            "detail": {"ip": req.ip, "reason": req.reason},
        },
        tenant_id=identity.tenant_id,
    )
    # Push notification for IP blacklist events
    try:
        from src.api.notifications import push_notification

        push_notification(
            source="immune",
            title=f"🚫 IP Blacklisted: {req.ip}",
            body=req.reason or "No reason provided",
            level="warning",
            organ="immune",
            emoji="🛡",
            action_url="/immune",
            metadata={"ip": req.ip, "reason": req.reason, "ttl_seconds": req.ttl_seconds},
        )
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)
    return {"message": f"IP {req.ip} blacklisted", "reason": req.reason}


@router.delete("/ip/blacklist/{ip}")
async def unblacklist_ip(ip: str):
    """Remove IP from blacklist."""
    ip_control.blacklist_remove(ip)
    return {"message": f"IP {ip} removed from blacklist"}


@router.post("/ip/whitelist")
async def whitelist_ip(req: IPActionRequest):
    """Add IP to whitelist."""
    ip_control.whitelist_add(req.ip, req.reason)
    return {"message": f"IP {req.ip} whitelisted"}


@router.delete("/ip/whitelist/{ip}")
async def unwhitelist_ip(ip: str):
    """Remove IP from whitelist."""
    ip_control.whitelist_remove(ip)
    return {"message": f"IP {ip} removed from whitelist"}


@router.get("/ip/lists")
async def list_ip_lists():
    """Get blacklist and whitelist."""
    return {
        "blacklist": ip_control.list_blacklist(),
        "whitelist": ip_control.list_whitelist(),
    }


@router.get("/ip/check/{ip}")
async def check_ip(ip: str):
    """Check if an IP is allowed."""
    result = ip_control.is_allowed(ip)
    if not result["allowed"]:
        audit.log(AuditAction.IP_BLOCKED, client_ip=ip, detail=result["reason"], risk_level="high")
    return result


# ── Audit Log ──────────────────────────────────────────────


@router.get("/audit/log")
async def get_audit_log(
    action: str = Query(default=None),
    risk_level: str = Query(default=None),
    client_ip: str = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    since: float = Query(default=None),
):
    """Query security audit log."""
    action_enum = None
    if action:
        try:
            action_enum = AuditAction(action)
        except ValueError:
            raise HTTPException(400, f"Invalid action. Valid: {[a.value for a in AuditAction]}")

    return {
        "entries": audit.query(
            action=action_enum,
            risk_level=risk_level,
            client_ip=client_ip,
            limit=limit,
            since=since,
        )
    }


@router.get("/audit/stats")
async def audit_stats():
    """Get audit log statistics."""
    return audit.stats()


class AuditCleanupRequest(BaseModel):
    keep_last: int | None = None


@router.post("/audit/cleanup")
async def cleanup_audit_log(req: AuditCleanupRequest | None = None):
    """Drop oldest audit entries, keeping the newest keep_last (default: half capacity)."""
    try:
        return audit.cleanup(keep_last=req.keep_last if req else None)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def immune_health():
    """OpenImmune health check."""
    return {
        "status": "ok",
        "component": "OpenImmune",
        "modules": {
            "rate_limiter": rate_limiter.stats(),
            "moderator": {"patterns": len(moderator.patterns)},
            "access_control": ip_control.stats(),
            "audit": audit.stats(),
            "intrusion_detection": intrusion.stats(),
        },
    }


@router.get("/stats")
async def immune_stats():
    """Get OpenImmune statistics."""
    return {
        "status": "ok",
        "component": "OpenImmune",
        "rate_limiter": rate_limiter.stats(),
        "moderator": {"patterns": len(moderator.patterns)},
        "access_control": ip_control.stats(),
        "audit": audit.stats(),
        "intrusion_detection": intrusion.stats(),
    }


# ── Intrusion Detection ───────────────────────────────────


class InspectRequest(BaseModel):
    ip: str
    method: str = "GET"
    path: str = "/"
    query: str = ""
    body: str = ""
    headers: dict = {}
    user_agent: str = ""


class LoginAttemptRequest(BaseModel):
    ip: str
    success: bool = False


class IPBlockRequest(BaseModel):
    ip: str


@router.post("/intrusion/inspect")
async def inspect_request(
    req: InspectRequest,
    authorization: str | None = Header(None),
):
    """Inspect a request for intrusion patterns (SQL injection, XSS, etc.)."""
    identity = bind_identity(authorization)
    threats = intrusion.inspect_request(
        ip=req.ip,
        method=req.method,
        path=req.path,
        query=req.query,
        body=req.body,
        headers=req.headers,
        user_agent=req.user_agent,
    )

    # Emit events for critical threats
    for threat in threats:
        if threat.threat_level.value in ("high", "critical"):
            push_event(
                {
                    "organ": "immune",
                    "emoji": "🛡",
                    "type": "intrusion_detected",
                    "summary": f"🚨 {threat.attack_type.value} from {threat.source_ip}: {threat.detail}",
                    "detail": threat.to_dict(),
                },
                tenant_id=identity.tenant_id,
            )
            # Push notification for critical threats
            if threat.threat_level.value == "critical":
                try:
                    from src.api.notifications import push_notification

                    push_notification(
                        source="immune",
                        title=f"🚨 Intrusion: {threat.attack_type.value}",
                        body=f"From {threat.source_ip}: {threat.detail}",
                        level="error",
                        organ="immune",
                        emoji="🛡",
                        action_url="/immune",
                        metadata=threat.to_dict(),
                    )
                except Exception as exc:
                    logging.getLogger(__name__).debug("probe skipped: %s", exc)
    return {
        "threats_found": len(threats),
        "threats": [t.to_dict() for t in threats],
    }


@router.post("/intrusion/login-attempt")
async def record_login_attempt(
    req: LoginAttemptRequest,
    authorization: str | None = Header(None),
):
    """Record a login attempt for brute-force detection."""
    identity = bind_identity(authorization)
    threat = intrusion.record_login_attempt(req.ip, req.success)
    if threat:
        push_event(
            {
                "organ": "immune",
                "emoji": "🛡",
                "type": "brute_force",
                "summary": f"🔒 Brute force detected from {req.ip}",
                "detail": threat.to_dict(),
            },
            tenant_id=identity.tenant_id,
        )
        try:
            from src.api.notifications import push_notification

            push_notification(
                source="immune",
                title=f"🔒 Brute Force: {req.ip}",
                body=threat.detail,
                level="error",
                organ="immune",
                emoji="🛡",
                action_url="/immune",
                metadata=threat.to_dict(),
            )
        except Exception as exc:
            logging.getLogger(__name__).debug("probe skipped: %s", exc)
        # Also auto-blacklist the IP via access control
        ip_control.blacklist_add(req.ip, reason="brute_force_auto_block", ttl_seconds=3600)
        return {"blocked": True, "threat": threat.to_dict()}
    return {"blocked": False}


@router.get("/intrusion/threats")
async def get_threats(
    ip: str = Query(default=None),
    attack_type: str = Query(default=None),
    level: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Query intrusion threat history."""
    return {
        "threats": intrusion.get_threats(ip=ip, attack_type=attack_type, level=level, limit=limit)
    }


@router.get("/intrusion/blocked")
async def get_blocked_ips():
    """List auto-blocked IPs from intrusion detection."""
    return {"blocked_ips": intrusion.get_blocked_ips()}


@router.post("/intrusion/block")
async def block_ip_intrusion(
    req: IPBlockRequest,
    authorization: str | None = Header(None),
):
    """Manually block an IP via intrusion detection."""
    identity = bind_identity(authorization)
    intrusion.block_ip(req.ip, reason="manual")
    ip_control.blacklist_add(req.ip, reason="manual_intrusion_block")
    push_event(
        {
            "organ": "immune",
            "emoji": "🛡",
            "type": "ip_blocked",
            "summary": f"🚫 IP manually blocked: {req.ip}",
            "detail": {"ip": req.ip},
        },
        tenant_id=identity.tenant_id,
    )
    return {"message": f"IP {req.ip} blocked"}


@router.delete("/intrusion/block/{ip}")
async def unblock_ip_intrusion(ip: str):
    """Unblock an IP from intrusion detection."""
    intrusion.unblock_ip(ip)
    ip_control.blacklist_remove(ip)
    return {"message": f"IP {ip} unblocked"}


@router.get("/intrusion/stats")
async def intrusion_stats() -> dict:
    """Get intrusion detection statistics."""
    return intrusion.stats()


# ── P0-3 工具级权限引擎（AgentScope PermissionEngine × kilocode分层） ──


class PermissionCheckRequest(BaseModel):
    tool_name: str
    arguments: dict = {}
    session_id: str = ""
    working_dir: str = ""


class PermissionRuleRequest(BaseModel):
    tool_name: str
    rule_content: str | None = None
    behavior: str = "allow"  # allow / deny / ask
    source: str = "user"  # user / session
    hard: bool = False
    risk: str = "medium"


class PermissionOutcomeRequest(BaseModel):
    outcome: str  # approved / denied / timeout
    comment: str = ""


class PermissionModeRequest(BaseModel):
    mode: str  # default / accept_edits / explore / bypass / dont_ask


@router.post("/permission/check")
async def permission_check(
    req: PermissionCheckRequest,
    authorization: str | None = Header(None),
):
    """工具调用前的权限判定 — 返回allow/deny/ask + provenance。

    acp-proxy SoulMateAgent在执行每个tool_call前调用此端点；
    deny/ask时决策带rule_source/rule_content（"为什么被拦"）。
    """
    identity = bind_identity(authorization)
    decision = permission_service.check(
        req.tool_name,
        req.arguments,
        session_id=req.session_id,
        working_dir=req.working_dir,
    )
    if decision.behavior.value == "deny":
        audit.log(
            AuditAction.CONTENT_BLOCKED,
            detail=f"permission deny: {req.tool_name} — {decision.decision_reason}",
            risk_level="high" if decision.rule_id else "medium",
        )
        push_event(
            {
                "organ": "immune",
                "emoji": "🛡",
                "type": "permission_denied",
                "summary": f"⛔ 工具调用被权限引擎拦截: {req.tool_name} — {decision.decision_reason}",
                "detail": {
                    "tool": req.tool_name,
                    "reason": decision.decision_reason,
                    "rule_source": decision.rule_source,
                    "mode": decision.mode,
                },
            },
            tenant_id=identity.tenant_id,
        )
    return decision.to_dict()


@router.get("/permission/rules")
async def permission_rules(
    include_deleted: bool = Query(default=False),
    behavior: str = Query(default=""),
    tool_name: str = Query(default=""),
):
    """列出权限规则（含builtin基线；hard=true的规则为硬否决）"""
    return {
        "rules": permission_service.store.list_rules(
            include_deleted=include_deleted, behavior=behavior, tool_name=tool_name
        )
    }


@router.post("/permission/rules")
async def permission_add_rule(req: PermissionRuleRequest):
    """新增权限规则。hard规则=任何模式不可覆盖（删除需人工且被审计）"""
    if req.behavior not in ("allow", "deny", "ask"):
        raise HTTPException(400, f"Invalid behavior: {req.behavior}")
    result = permission_service.store.add_rule(
        req.tool_name,
        req.rule_content,
        req.behavior,
        source=req.source,
        hard=req.hard,
        risk=req.risk,
    )
    return {"ok": True, **result}


@router.delete("/permission/rules/{rule_id}")
async def permission_delete_rule(rule_id: str):
    """删除规则（软删，审计保留）"""
    ok = permission_service.store.delete_rule(rule_id)
    if not ok:
        raise HTTPException(404, f"Rule not found: {rule_id}")
    return {"ok": True, "rule_id": rule_id}


@router.get("/permission/mode")
async def permission_get_mode():
    return {"mode": permission_service.store.get_mode()}


@router.put("/permission/mode")
async def permission_set_mode(req: PermissionModeRequest):
    """设置权限模式。explore=只读（售前'先看不动'）；dont_ask=无人值守安全档"""
    try:
        permission_service.store.set_mode(req.mode)
    except ValueError:
        raise HTTPException(
            400, f"Invalid mode: {req.mode}. Valid: {[m.value for m in PermissionMode]}"
        )
    return {"ok": True, "mode": req.mode}


@router.get("/permission/audit")
async def permission_audit(
    limit: int = Query(default=50, ge=1, le=1000),
    behavior: str = Query(default=""),
    tool_name: str = Query(default=""),
    outcome: str = Query(default=""),
):
    """决策审计轨迹 — 每次判定的behavior+provenance，ASK决策的人工结果回写"""
    return {
        "entries": permission_service.store.audit_query(
            limit=limit, behavior=behavior, tool_name=tool_name, outcome=outcome
        )
    }


@router.post("/permission/approvals/{decision_id}")
async def permission_record_outcome(decision_id: str, req: PermissionOutcomeRequest):
    """人工审批结果回写（acp-proxy收到前端session/request_permission响应后调用）"""
    ok = permission_service.store.record_outcome(decision_id, req.outcome, req.comment)
    if not ok:
        raise HTTPException(
            404, f"Decision not found or invalid outcome: {decision_id} / {req.outcome}"
        )
    return {"ok": True, "decision_id": decision_id, "outcome": req.outcome}


@router.get("/permission/stats")
async def permission_stats():
    """权限引擎统计 — decisions总数/按行为分布/pending审批/规则数/当前模式"""
    return permission_service.store.stats()
