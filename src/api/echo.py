"""OpenEcho API — 回声系统：多渠道消息推送、消息模板。"""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from src.echo.dispatcher import Channel, MessageDispatcher
from src.echo.templates import TemplateEngine, is_builtin, template_row_tenant
from src.nerve.event_bridge import push_event
from src.services.tenant_binding import bind_identity

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
dispatcher = MessageDispatcher()
template_engine = TemplateEngine()


# ── Request Schemas ────────────────────────────────────────


class SendRequest(BaseModel):
    channel: str
    title: str
    content: str
    target: str = ""
    priority: int = 5


class BroadcastRequest(BaseModel):
    title: str
    content: str
    priority: int = 5


class ChannelConfigRequest(BaseModel):
    channel: str
    endpoint: str = ""
    token: str = ""
    enabled: bool = True
    extra: dict = {}


# ── Message Endpoints ──────────────────────────────────────


@router.post("/send")
async def send_message(
    req: SendRequest,
    authorization: str | None = Header(None),
):
    """Send a message via specified channel."""
    identity = bind_identity(authorization)
    try:
        channel = Channel(req.channel)
    except ValueError:
        raise HTTPException(400, f"Invalid channel. Valid: {[c.value for c in Channel]}")

    result = dispatcher.send(
        channel=channel,
        title=req.title,
        content=req.content,
        target=req.target,
        priority=req.priority,
        tenant_id=identity.tenant_id,
    )

    # Emit event
    if result.success:
        push_event(
            {
                "organ": "echo",
                "emoji": "🔊",
                "type": "message_sent",
                "summary": f"📨 [{req.channel}] {req.title}",
                "detail": {"channel": req.channel, "title": req.title, "msg_id": result.msg_id},
            },
            tenant_id=identity.tenant_id,
        )

    return {
        "success": result.success,
        "msg_id": result.msg_id,
        "channel": result.channel,
        "error": result.error,
    }


@router.post("/broadcast")
async def broadcast_message(
    req: BroadcastRequest,
    authorization: str | None = Header(None),
):
    """Broadcast a message to all enabled channels.

    身份轴（DEC-⑦-② 行归属）：bind_identity 派生租户落消息行（修前零身份轴
    =广播消息恒落 'default'，租户用户搜不到自己的广播=自我不可见）；坏 token
    401 先于任何发送（F3b-⑤ 纪律）。
    """
    identity = bind_identity(authorization)
    results = dispatcher.send_all(
        title=req.title, content=req.content, priority=req.priority, tenant_id=identity.tenant_id
    )
    return {
        "results": [
            {"success": r.success, "msg_id": r.msg_id, "channel": r.channel, "error": r.error}
            for r in results
        ],
        "total": len(results),
        "sent": sum(1 for r in results if r.success),
    }


# ── Channel Configuration ──────────────────────────────────


@router.post("/channels/configure")
async def configure_channel(req: ChannelConfigRequest):
    """Configure a message channel."""
    try:
        channel = Channel(req.channel)
    except ValueError:
        raise HTTPException(400, f"Invalid channel. Valid: {[c.value for c in Channel]}")

    dispatcher.configure_channel(
        channel=channel,
        endpoint=req.endpoint,
        token=req.token,
        enabled=req.enabled,
        extra=req.extra,
    )
    return {"message": f"Channel '{req.channel}' configured", "enabled": req.enabled}


@router.get("/channels")
async def list_channels():
    """List configured channels."""
    return {"channels": dispatcher.list_channels()}


# ── Channel Health Monitoring ────────────────────────────────

import time as _time
import urllib.error
import urllib.request

_channel_health: dict[str, dict] = {}


def _test_channel_health(channel: str, config: dict) -> dict:
    """Test if a channel endpoint is reachable."""
    start = _time.time()
    result = {
        "channel": channel,
        "status": "unknown",
        "latency_ms": 0,
        "last_check": _time.time(),
        "error": "",
    }
    try:
        if channel == "console":
            result["status"] = "ok"
            result["latency_ms"] = 0.1
            return result

        endpoint = config.get("endpoint", "")
        if not endpoint:
            result["status"] = "unconfigured"
            result["error"] = "No endpoint configured"
            return result

        # For webhook-based channels, try a HEAD/GET to the endpoint
        if channel in ("webhook", "dingtalk", "feishu", "wechat_work"):
            req = urllib.request.Request(endpoint, method="HEAD")
            with urllib.request.urlopen(req, timeout=5):
                result["status"] = "ok"
        elif channel == "telegram":
            token = config.get("token", "")
            if token:
                url = f"https://api.telegram.org/bot{token}/getMe"
                with urllib.request.urlopen(url, timeout=5):
                    result["status"] = "ok"
            else:
                result["status"] = "unconfigured"
                result["error"] = "No bot token"
        elif channel == "email":
            import smtplib

            smtp_host = config.get("endpoint", "")
            port = int(config.get("extra", {}).get("smtp_port", 587))
            if smtp_host:
                with smtplib.SMTP(smtp_host, port, timeout=5):
                    result["status"] = "ok"
            else:
                result["status"] = "unconfigured"
        else:
            result["status"] = "ok"
            result["error"] = "No health check for this channel type"

        result["latency_ms"] = round((_time.time() - start) * 1000, 1)
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)[:200]
        result["latency_ms"] = round((_time.time() - start) * 1000, 1)
    return result


@router.get("/channels/health")
async def channels_health():
    """Test health of all configured channels."""
    global _channel_health
    results = []
    for ch_info in dispatcher.list_channels():
        ch_name = ch_info["channel"]
        # Get full config from dispatcher
        config = {
            "endpoint": ch_info.get("has_endpoint", False),
            "token": ch_info.get("has_token", False),
            "enabled": ch_info.get("enabled", False),
        }
        # Get actual config from dispatcher internals
        for _ch, _cfg in dispatcher._channels.items():
            if _ch.value == ch_name:
                config = {
                    "endpoint": _cfg.endpoint,
                    "token": _cfg.token,
                    "enabled": _cfg.enabled,
                    "extra": _cfg.extra,
                }
                break

        health = _test_channel_health(ch_name, config)
        results.append(health)
        _channel_health[ch_name] = health

    ok_count = sum(1 for r in results if r["status"] == "ok")
    return {
        "status": "ok" if ok_count == len(results) else "degraded",
        "channels": results,
        "healthy": ok_count,
        "total": len(results),
    }


# ── History ────────────────────────────────────────────────


@router.get("/history")
async def message_history(
    limit: int = Query(default=50, ge=1, le=500),
    channel: str = Query(default=None),
    authorization: str | None = Header(None),
):
    """Get message sending history.

    读面归属闸（DEC-⑦-⑬）：tenant 从认证身份派生（bind_identity）禁自报，恒按
    行租户键过滤（匿名='default' 面=DEC-⑦-①② 匿名面同款，key=message_row_tenant
    单一源）；坏 token 401 先于读取（F3b-⑤ 身份证明先于一切）。history(tenant_id=
    None) 系统视图仍为内部调用面保留（DEC-⑦-② 边界③，消费方自查身份）。
    """
    identity = bind_identity(authorization)
    ch = None
    if channel:
        try:
            ch = Channel(channel)
        except ValueError:
            raise HTTPException(400, f"Invalid channel: {channel}")
    return {"messages": dispatcher.history(limit=limit, channel=ch, tenant_id=identity.tenant_id)}


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def echo_stats(
    authorization: str | None = Header(None),
):
    """Get OpenEcho statistics.

    聚合面归属闸（DEC-⑦-⑭）：tenant 从认证身份派生（bind_identity）禁自报（/stats
    无 tenant_id 自报参数），total/sent/failed 恒按行租户键聚合（匿名='default' 面=
    DEC-⑦-⑬ 同款，key=message_row_tenant 单一源）；坏 token 401 先于读取（F3b-⑤
    身份证明先于一切）。dispatcher.stats(tenant_id=None) 系统视图仍为 /health 监控面
    与内部调用面保留（字节不变）。templates 聚合随 DEC-⑦-㉑ 加轴（收口 ⑦-⑭
    诚实边界「echo 模板存储层无轴」：恒按 template_visible_to 可见集聚合）；
    tenant_id 加性键=聚合作用域可观测（⑦-③④⑤ 同款）。
    """
    identity = bind_identity(authorization)
    return {
        "status": "ok",
        "component": "OpenEcho",
        "tenant_id": identity.tenant_id,
        **dispatcher.stats(tenant_id=identity.tenant_id),
        "templates": template_engine.stats(tenant_id=identity.tenant_id),
    }


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def echo_health():
    """OpenEcho health check — includes channel connectivity status."""
    # Quick channel health check
    ch_health = {"ok": 0, "total": 0, "degraded": []}
    for ch_info in dispatcher.list_channels():
        ch_health["total"] += 1
        if ch_info["enabled"]:
            if ch_info["channel"] == "console":
                ch_health["ok"] += 1
            elif ch_info["has_endpoint"]:
                ch_health["ok"] += 1  # assume ok until tested
            else:
                ch_health["degraded"].append(ch_info["channel"])
        else:
            ch_health["degraded"].append(ch_info["channel"])

    overall = "ok" if ch_health["ok"] == ch_health["total"] else "degraded"
    return {
        "status": overall,
        "component": "OpenEcho",
        **dispatcher.stats(),
        "templates": template_engine.stats(),
        "channel_health": ch_health,
    }


# ── Template Schemas ───────────────────────────────────────


class TemplateCreateRequest(BaseModel):
    name: str
    title_template: str
    content_template: str
    description: str = ""
    channel: str = "any"
    category: str = "custom"
    icon: str = "📨"


class TemplateUpdateRequest(BaseModel):
    name: str | None = None
    title_template: str | None = None
    content_template: str | None = None
    description: str | None = None
    channel: str | None = None
    category: str | None = None
    icon: str | None = None


class TemplateSendRequest(BaseModel):
    variables: dict = {}
    channel: str | None = None  # override template's preferred channel
    target: str = ""
    priority: int = 5


# ── Template Endpoints ─────────────────────────────────────


@router.get("/templates")
async def list_templates(
    category: str = Query(default=None),
    authorization: str | None = Header(None),
):
    """List all message templates.（DEC-⑦-㉑ 读面加轴：tenant 从认证身份派生
    禁自报，恒按可见集过滤（builtin 共享目录+行归属，⑩-① 语义）；坏 token
    401 先于读取 F3b-⑤。）"""
    identity = bind_identity(authorization)
    return {
        "templates": template_engine.list_templates(category=category, tenant_id=identity.tenant_id)
    }


@router.get("/templates/{template_id}")
async def get_template(template_id: str, authorization: str | None = Header(None)):
    """Get a specific template.（DEC-⑦-㉑ by-ID 归属闸：跨租户 404 与 not-found
    同契约不差分泄露行存在性（③c-1）；坏 token 401 先于读取 F3b-⑤。）"""
    identity = bind_identity(authorization)
    tpl = template_engine.get(template_id, tenant_id=identity.tenant_id)
    if not tpl:
        raise HTTPException(404, "Template not found")
    return {
        "template_id": tpl.template_id,
        "name": tpl.name,
        "description": tpl.description,
        "channel": tpl.channel,
        "title_template": tpl.title_template,
        "content_template": tpl.content_template,
        "variables": tpl.variables,
        "category": tpl.category,
        "icon": tpl.icon,
        "usage_count": tpl.usage_count,
        "last_used": tpl.last_used,
        "created_at": tpl.created_at,
        "tenant_id": template_row_tenant(tpl),
        "builtin": is_builtin(tpl.template_id),
    }


@router.post("/templates")
async def create_template(
    req: TemplateCreateRequest,
    authorization: str | None = Header(None),
):
    """Create a new message template."""
    identity = bind_identity(authorization)
    tpl = template_engine.create(
        name=req.name,
        title_template=req.title_template,
        content_template=req.content_template,
        description=req.description,
        channel=req.channel,
        category=req.category,
        icon=req.icon,
        tenant_id=identity.tenant_id,
    )
    push_event(
        {
            "organ": "echo",
            "emoji": "🔊",
            "type": "template_created",
            "summary": f"📝 Template created: {tpl.name}",
            "detail": {"template_id": tpl.template_id, "name": tpl.name},
        },
        tenant_id=identity.tenant_id,
    )
    return {
        "template_id": tpl.template_id,
        "name": tpl.name,
        "variables": tpl.variables,
    }


@router.patch("/templates/{template_id}")
async def update_template(
    template_id: str,
    req: TemplateUpdateRequest,
    authorization: str | None = Header(None),
):
    """Update an existing template.（DEC-⑦-㉑ 写面归属闸：跨租户/builtin 404 与
    not-found 同契约零改写；坏 token 401 先于写 F3b-⑤。）"""
    identity = bind_identity(authorization)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not template_engine.update(template_id, tenant_id=identity.tenant_id, **updates):
        raise HTTPException(404, "Template not found or no changes")
    return {"message": "updated", "template_id": template_id}


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, authorization: str | None = Header(None)):
    """Delete a template.（DEC-⑦-㉑ 写面归属闸：跨租户 404 零副作用与 not-found
    同契约、builtin 恒拒（共享≠可写）；坏 token 401 先于写 F3b-⑤。）"""
    identity = bind_identity(authorization)
    if not template_engine.delete(template_id, tenant_id=identity.tenant_id):
        raise HTTPException(404, "Template not found")
    return {"message": "deleted", "template_id": template_id}


@router.post("/templates/{template_id}/send")
async def send_from_template(
    template_id: str,
    req: TemplateSendRequest,
    authorization: str | None = Header(None),
):
    """Render a template and send the message immediately."""
    identity = bind_identity(authorization)
    rendered = template_engine.render_template(
        template_id, req.variables, tenant_id=identity.tenant_id
    )
    if not rendered:
        raise HTTPException(404, "Template not found")

    # Determine channel
    channel_name = req.channel or rendered["channel"]
    if channel_name == "any":
        channel_name = "console"  # default fallback

    try:
        channel = Channel(channel_name)
    except ValueError:
        raise HTTPException(400, f"Invalid channel: {channel_name}")

    result = dispatcher.send(
        channel=channel,
        title=rendered["title"],
        content=rendered["content"],
        target=req.target,
        priority=req.priority,
        tenant_id=identity.tenant_id,
    )

    push_event(
        {
            "organ": "echo",
            "emoji": "🔊",
            "type": "template_sent",
            "summary": f"📨 [{channel_name}] {rendered['title']}",
            "detail": {
                "template_id": template_id,
                "channel": channel_name,
                "msg_id": result.msg_id,
            },
        },
        tenant_id=identity.tenant_id,
    )

    return {
        "success": result.success,
        "msg_id": result.msg_id,
        "channel": result.channel,
        "error": result.error,
        "rendered_title": rendered["title"],
        "rendered_content": rendered["content"],
    }


@router.post("/templates/{template_id}/preview")
async def preview_template(
    template_id: str,
    req: TemplateSendRequest,
    authorization: str | None = Header(None),
):
    """Preview a rendered template without sending.（DEC-⑦-㉑ 读可见性闸同 get。）"""
    identity = bind_identity(authorization)
    rendered = template_engine.render_template(
        template_id, req.variables, tenant_id=identity.tenant_id
    )
    if not rendered:
        raise HTTPException(404, "Template not found")
    return rendered
