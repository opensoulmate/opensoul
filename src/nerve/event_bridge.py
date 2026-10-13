"""Event Bridge — automatically publishes events to the Nerve bus when
organ actions occur.  Called inline from API endpoints so every important
action becomes a bus event that the Activity feed can display.

Usage in any API module:
    from src.nerve.event_bridge import emit
    await emit("vein", "file_uploaded", "📄 File uploaded: report.pdf", {"file_id": "..."})
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "http://127.0.0.1:8090"

# Organ emoji map
ORGAN_EMOJI: dict[str, str] = {
    "soul": "🧠",
    "cortex": "🧩",
    "nerve": "⚡",
    "vein": "🩸",
    "sense": "👁",
    "will": "✨",
    "immune": "🛡",
    "vital": "📊",
    "marrow": "🦴",
    "gland": "🧪",
    "gene": "🧬",
    "echo": "🔊",
    "mirror": "🪞",
    "link": "🔗",
    "hippo": "🧠",
    "reflex": "⚡",
    "heredity": "🔗",
    "pulse": "💓",
    "nest": "🏠",
    "limb": "💪",
    "voice": "🎤",
    "vision": "🎨",
    "mind": "💭",
    "mate": "👤",
    "soma": "🤖",
    "trajectory": "📊",
}


def _tenant_key(tenant_id: str | None) -> dict[str, Any]:
    """F3b-⑨b-①：身份派生租户键注入（事件 dict 自带键→TimelineStore.record 行归属）。

    显式提供（非空白）=注入 {"tenant_id": str(...) }（str() 归一，SQLite 铁律）；
    未提供/空白=返回空 dict 不注入键——调用点不传行为字节不变（'default' 兜底延续）。
    """
    val = str(tenant_id or "").strip()
    return {"tenant_id": val} if val else {}


async def emit(
    organ: str,
    event_type: str,
    summary: str,
    detail: dict[str, Any] | None = None,
    *,
    tenant_id: str | None = None,
    _client: httpx.AsyncClient | None = None,
) -> bool:
    """Publish an event to both the Nerve bus and the in-memory event buffer.

    F3b-⑨b-①：tenant_id=身份派生租户键（bind_identity 派生值由调用点传入），
    随事件 dict 透传到 TimelineStore.record 行归属（record() 消费 event['tenant_id']）。
    未提供/空白=不注入键——调用点不传行为字节不变，record() 'default' 兜底延续
    （③c-2① 语义）。事件 dict 自带键是唯一落库真源，此参数只是构造处的注入口。

    Returns True if at least one publish succeeded.  Never raises —
    failures are logged and swallowed so the caller's main path is
    unaffected.
    """
    emoji = ORGAN_EMOJI.get(organ, "🔔")
    topic = f"organ.{organ}.{event_type}"
    payload = {
        "organ": organ,
        "emoji": emoji,
        "type": event_type,
        "summary": summary,
        "detail": detail or {},
        "timestamp": time.time(),
        **_tenant_key(tenant_id),
    }

    ok = False

    own_client = _client is None
    if own_client:
        _client = httpx.AsyncClient(timeout=3.0)

    try:
        # 1) Publish to Nerve event bus
        try:
            r = await _client.post(
                f"{_BASE}/api/nerve/publish",
                json={"topic": topic, "data": payload, "source": organ},
            )
            if r.status_code == 200:
                ok = True
        except Exception as exc:
            logger.debug("Nerve publish failed for %s: %s", topic, exc)

        # 2) Append to the event-stream buffer so Activity page picks it up
        try:
            from src.api.event_stream import push_event  # type: ignore[attr-defined]

            push_event(
                {
                    "organ": organ,
                    "emoji": emoji,
                    "type": event_type,
                    "summary": summary,
                    "detail": detail or {},
                    "timestamp": time.time(),
                    **_tenant_key(tenant_id),
                }
            )
        except (ImportError, AttributeError):
            # push_event may not exist yet — that's fine
            pass

        # 2b) Record to persistent timeline
        try:
            from src.timeline.store import TimelineStore

            if not hasattr(emit, "_timeline"):
                emit._timeline = TimelineStore()  # type: ignore[attr-defined]
            emit._timeline.record(
                {
                    "id": f"evt_{int(time.time() * 1000)}_{organ}_{event_type}",
                    "organ": organ,
                    "emoji": emoji,
                    "type": event_type,
                    "summary": summary,
                    "detail": detail or {},
                    "timestamp": time.time(),
                    "collected_at": time.time(),
                    **_tenant_key(tenant_id),
                }
            )
        except Exception as exc:
            logging.getLogger(__name__).debug("probe skipped: %s", exc)
        # 3) Push to Notification Center
        try:
            from src.api.notifications import push_notification

            # Only notify for important events (errors, warnings, key actions)
            level = "info"
            if event_type in ("error", "alert", "failure"):
                level = "error"
            elif event_type in ("warning", "degraded"):
                level = "warning"
            elif event_type in ("completed", "success", "uploaded"):
                level = "success"
            push_notification(
                source="event_bridge",
                title=f"{emoji} {organ.upper()}",
                body=summary,
                level=level,
                organ=organ,
                emoji=emoji,
                action_url=f"/{organ}",
                metadata={"event_type": event_type, "detail": detail},
            )
        except (ImportError, AttributeError):
            pass

    finally:
        if own_client:
            await _client.aclose()

    return ok


def push_event(event: dict[str, Any], *, tenant_id: str | None = None) -> None:
    """Append an event to the in-memory ring buffer AND publish to Nerve bus.

    This is the synchronous convenience wrapper used by API endpoints.
    For Nerve bus delivery it fires-and-forgets an async task.

    F3b-⑨b-①：tenant_id=身份派生租户键显式注入口——提供（非空白）则覆盖 dict
    同名键（身份真源优先，防构造处手抄漂移）；未提供不动 dict（事件自带键
    原样透传，调用点不传行为字节不变，record() 'default' 兜底延续）。
    """
    # F3b-⑨b-①：显式 tenant_id=身份真源（覆盖 dict 同名键）；未提供不动 dict。
    val = str(tenant_id or "").strip()
    if val:
        event["tenant_id"] = val

    try:
        from src.api.event_stream import _event_buffer

        event.setdefault("id", f"evt_{int(time.time() * 1000)}")
        event.setdefault("collected_at", time.time())
        _event_buffer.append(event)
    except ImportError:
        pass

    # Also record to persistent timeline
    try:
        from src.timeline.store import TimelineStore

        if not hasattr(push_event, "_timeline"):
            push_event._timeline = TimelineStore()  # type: ignore[attr-defined]
        push_event._timeline.record(event)  # type: ignore[attr-defined]
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)

    # Also publish to Nerve bus (fire-and-forget)
    try:
        import asyncio

        organ = event.get("organ", "unknown")
        event_type = event.get("type", "unknown")
        topic = f"organ.{organ}.{event_type}"
        loop = asyncio.get_running_loop()
        loop.create_task(_publish_to_nerve(topic, event, organ))
    except RuntimeError:
        pass  # No event loop running — skip Nerve


async def _publish_to_nerve(topic: str, payload: dict, source: str) -> None:
    """Background task to publish to Nerve bus."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                f"{_BASE}/api/nerve/publish",
                json={"topic": topic, "data": payload, "source": source},
            )
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)
