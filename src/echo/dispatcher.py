"""Message dispatcher — multi-channel push with queue."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum


class Channel(StrEnum):
    WEBHOOK = "webhook"
    EMAIL = "email"
    DINGTALK = "dingtalk"
    WECHAT_WORK = "wechat_work"
    TELEGRAM = "telegram"
    FEISHU = "feishu"
    SLACK = "slack"
    DISCORD = "discord"
    CONSOLE = "console"


@dataclass
class ChannelConfig:
    channel: Channel
    enabled: bool = True
    endpoint: str = ""
    token: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Message:
    msg_id: str
    channel: Channel
    title: str
    content: str
    timestamp: float
    status: str = "pending"  # "pending", "sent", "failed"
    error: str = ""
    target: str = ""
    priority: int = 5  # 1=highest, 10=lowest
    tenant_id: str = "default"


def message_row_tenant(raw: object) -> str:
    """行租户键单一判法（DEC-⑦-① _event_row_tenant 同款语义：自带键缺失/空白
    →'default' 兜底）。raw=Message 对象（读侧过滤）或原始租户值（写侧落行），
    写读同一判法禁双源漂移（F4b-㉗ 纪律）。
    """
    if hasattr(raw, "tenant_id"):
        raw = getattr(raw, "tenant_id")
    return str(raw or "").strip() or "default"


@dataclass
class SendResult:
    success: bool
    msg_id: str
    channel: str
    error: str = ""


class MessageDispatcher:
    """Multi-channel message dispatcher with queue and retry."""

    def __init__(self):
        self._channels: dict[Channel, ChannelConfig] = {}
        self._queue: deque[Message] = deque(maxlen=10000)
        self._history: deque[Message] = deque(maxlen=1000)
        self._lock = threading.Lock()
        self._counter = 0

        # Register console channel by default
        self._channels[Channel.CONSOLE] = ChannelConfig(channel=Channel.CONSOLE, enabled=True)

    def configure_channel(
        self,
        channel: Channel,
        endpoint: str = "",
        token: str = "",
        enabled: bool = True,
        extra: dict | None = None,
    ):
        with self._lock:
            self._channels[channel] = ChannelConfig(
                channel=channel,
                enabled=enabled,
                endpoint=endpoint,
                token=token,
                extra=extra or {},
            )

    def send(
        self,
        channel: Channel,
        title: str,
        content: str,
        target: str = "",
        priority: int = 5,
        tenant_id: str = "",
    ) -> SendResult:
        """Send a message via specified channel.

        tenant_id=行租户键（DEC-⑦-② echo 面存储层加轴）：显式提供=发送者身份
        派生租户落行；未提供/空白→'default' 兜底（系统发送方=DEC-① 系统事件
        语义，键派生故障绝不吃掉落库）。
        """
        self._counter += 1
        msg_id = f"msg_{int(time.time())}_{self._counter}"

        msg = Message(
            msg_id=msg_id,
            channel=channel,
            title=title,
            content=content,
            timestamp=time.time(),
            target=target,
            priority=priority,
            tenant_id=message_row_tenant(tenant_id),
        )

        config = self._channels.get(channel)
        if not config or not config.enabled:
            msg.status = "failed"
            msg.error = f"Channel '{channel.value}' not configured or disabled"
            with self._lock:
                self._history.append(msg)
            return SendResult(success=False, msg_id=msg_id, channel=channel.value, error=msg.error)

        # Dispatch
        result = self._dispatch(msg, config)

        msg.status = "sent" if result.success else "failed"
        msg.error = result.error

        with self._lock:
            self._history.append(msg)

        return result

    def send_all(
        self, title: str, content: str, priority: int = 5, tenant_id: str = ""
    ) -> list[SendResult]:
        """Broadcast to all enabled channels."""
        results = []
        for channel, config in self._channels.items():
            if config.enabled:
                results.append(
                    self.send(channel, title, content, priority=priority, tenant_id=tenant_id)
                )
        return results

    def _dispatch(self, msg: Message, config: ChannelConfig) -> SendResult:
        if config.channel == Channel.CONSOLE:
            print(f"[OpenEcho] {msg.title}: {msg.content}")
            return SendResult(success=True, msg_id=msg.msg_id, channel=config.channel.value)

        elif config.channel == Channel.WEBHOOK:
            return self._send_webhook(msg, config)

        elif config.channel == Channel.DINGTALK:
            return self._send_dingtalk(msg, config)

        elif config.channel == Channel.FEISHU:
            return self._send_feishu(msg, config)

        elif config.channel == Channel.TELEGRAM:
            return self._send_telegram(msg, config)

        elif config.channel == Channel.EMAIL:
            return self._send_email(msg, config)

        elif config.channel == Channel.WECHAT_WORK:
            return self._send_wechat_work(msg, config)

        elif config.channel == Channel.SLACK:
            return self._send_slack(msg, config)

        elif config.channel == Channel.DISCORD:
            return self._send_discord(msg, config)

        else:
            return SendResult(
                success=False,
                msg_id=msg.msg_id,
                channel=config.channel.value,
                error=f"Channel '{config.channel.value}' not implemented yet",
            )

    def _send_webhook(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via generic webhook (POST JSON)."""
        try:
            payload = json.dumps(
                {
                    "msg_id": msg.msg_id,
                    "title": msg.title,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                    "priority": msg.priority,
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                config.endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            if config.token:
                req.add_header("Authorization", f"Bearer {config.token}")

            with urllib.request.urlopen(req, timeout=10):
                return SendResult(success=True, msg_id=msg.msg_id, channel="webhook")

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="webhook", error=str(e))

    def _send_dingtalk(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via DingTalk robot webhook."""
        try:
            payload = json.dumps(
                {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": msg.title,
                        "text": f"## {msg.title}\n\n{msg.content}",
                    },
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                config.endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10):
                return SendResult(success=True, msg_id=msg.msg_id, channel="dingtalk")

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="dingtalk", error=str(e))

    def _send_feishu(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via Feishu/Lark robot webhook."""
        try:
            payload = json.dumps(
                {
                    "msg_type": "interactive",
                    "card": {
                        "header": {"title": {"tag": "plain_text", "content": msg.title}},
                        "elements": [{"tag": "markdown", "content": msg.content}],
                    },
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                config.endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10):
                return SendResult(success=True, msg_id=msg.msg_id, channel="feishu")

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="feishu", error=str(e))

    def _send_telegram(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via Telegram Bot API."""
        try:
            token = config.token
            chat_id = config.extra.get("chat_id", msg.target)
            if not token or not chat_id:
                return SendResult(
                    success=False,
                    msg_id=msg.msg_id,
                    channel="telegram",
                    error="Missing token or chat_id",
                )

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps(
                {
                    "chat_id": chat_id,
                    "text": f"*{msg.title}*\n\n{msg.content}",
                    "parse_mode": "Markdown",
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=10):
                return SendResult(success=True, msg_id=msg.msg_id, channel="telegram")

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="telegram", error=str(e))

    def _send_email(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via SMTP email."""
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        try:
            smtp_host = config.endpoint or config.extra.get("smtp_host", "")
            smtp_port = int(config.extra.get("smtp_port", 587))
            username = config.extra.get("username", "")
            password = config.token or config.extra.get("password", "")
            from_addr = config.extra.get("from", username)
            to_addr = msg.target or config.extra.get("to", "")

            if not smtp_host or not to_addr:
                return SendResult(
                    success=False,
                    msg_id=msg.msg_id,
                    channel="email",
                    error="Missing smtp_host or recipient address",
                )

            # Build email
            email_msg = MIMEMultipart("alternative")
            email_msg["Subject"] = msg.title
            email_msg["From"] = from_addr
            email_msg["To"] = to_addr

            # Plain text body
            text_body = f"{msg.title}\n\n{msg.content}"
            email_msg.attach(MIMEText(text_body, "plain", "utf-8"))

            # HTML body
            html_body = f"""<html><body>
<h2>{msg.title}</h2>
<p>{msg.content.replace(chr(10), "<br>")}</p>
<hr><small>OpenEcho · Priority: {msg.priority}</small>
</body></html>"""
            email_msg.attach(MIMEText(html_body, "html", "utf-8"))

            # Send
            use_ssl = config.extra.get("use_ssl", False) or smtp_port == 465
            if use_ssl:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
                    if username and password:
                        server.login(username, password)
                    server.sendmail(from_addr, [to_addr], email_msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                    server.ehlo()
                    if smtp_port == 587:
                        server.starttls()
                        server.ehlo()
                    if username and password:
                        server.login(username, password)
                    server.sendmail(from_addr, [to_addr], email_msg.as_string())

            return SendResult(success=True, msg_id=msg.msg_id, channel="email")

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="email", error=str(e))

    def _send_wechat_work(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via WeChat Work (企业微信) robot webhook."""
        try:
            webhook_url = config.endpoint
            if not webhook_url:
                return SendResult(
                    success=False,
                    msg_id=msg.msg_id,
                    channel="wechat_work",
                    error="Missing webhook URL",
                )

            # WeChat Work robot supports markdown format
            payload = json.dumps(
                {
                    "msgtype": "markdown",
                    "markdown": {
                        "content": f"## {msg.title}\n\n{msg.content}\n\n> Priority: {msg.priority}",
                    },
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("errcode", 0) != 0:
                    return SendResult(
                        success=False,
                        msg_id=msg.msg_id,
                        channel="wechat_work",
                        error=f"WeChat API error: {result.get('errmsg', 'unknown')}",
                    )
                return SendResult(success=True, msg_id=msg.msg_id, channel="wechat_work")

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="wechat_work", error=str(e))

    def _send_slack(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via Slack Incoming Webhook."""
        try:
            webhook_url = config.endpoint
            if not webhook_url:
                return SendResult(
                    success=False,
                    msg_id=msg.msg_id,
                    channel="slack",
                    error="Missing Slack webhook URL",
                )

            # Slack Block Kit message with header and section
            payload = json.dumps(
                {
                    "blocks": [
                        {
                            "type": "header",
                            "text": {"type": "plain_text", "text": msg.title},
                        },
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": msg.content},
                        },
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"Priority: {msg.priority} | OpenEcho",
                                }
                            ],
                        },
                    ],
                    "text": f"{msg.title}: {msg.content[:200]}",  # fallback text
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8").strip()
                if body == "ok" or resp.status == 200:
                    return SendResult(success=True, msg_id=msg.msg_id, channel="slack")
                return SendResult(
                    success=False,
                    msg_id=msg.msg_id,
                    channel="slack",
                    error=f"Slack returned: {body}",
                )

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="slack", error=str(e))

    def _send_discord(self, msg: Message, config: ChannelConfig) -> SendResult:
        """Send via Discord Webhook."""
        try:
            webhook_url = config.endpoint
            if not webhook_url:
                return SendResult(
                    success=False,
                    msg_id=msg.msg_id,
                    channel="discord",
                    error="Missing Discord webhook URL",
                )

            # Discord embed message
            payload = json.dumps(
                {
                    "embeds": [
                        {
                            "title": msg.title,
                            "description": msg.content,
                            "color": 0x7C3AED,  # purple accent
                            "footer": {"text": f"OpenEcho · Priority: {msg.priority}"},
                        }
                    ]
                }
            ).encode("utf-8")

            # Discord webhooks support ?wait=true for confirmation
            url = webhook_url + ("&" if "?" in webhook_url else "?") + "wait=true"
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 204):
                    return SendResult(success=True, msg_id=msg.msg_id, channel="discord")
                return SendResult(
                    success=False,
                    msg_id=msg.msg_id,
                    channel="discord",
                    error=f"Discord returned HTTP {resp.status}",
                )

        except Exception as e:
            return SendResult(success=False, msg_id=msg.msg_id, channel="discord", error=str(e))

    def history(
        self, limit: int = 50, channel: Channel | None = None, tenant_id: str | None = None
    ) -> list[dict]:
        """读面加轴（DEC-⑦-②）：tenant_id=None=系统视图全量（既有调用面字节不变）
        ，显式租户=恒按行租户键过滤（判法=message_row_tenant 单一源）；过滤先于
        截断=limit 只计可见命中（DEC-⑦-① 同款）。
        """
        with self._lock:
            entries = list(self._history)
        if channel:
            entries = [e for e in entries if e.channel == channel]
        if tenant_id is not None:
            key = message_row_tenant(tenant_id)
            entries = [e for e in entries if message_row_tenant(e) == key]
        return [
            {
                "msg_id": e.msg_id,
                "channel": e.channel.value,
                "title": e.title,
                "content": e.content[:200],
                "status": e.status,
                "error": e.error,
                "timestamp": e.timestamp,
                "priority": e.priority,
            }
            for e in sorted(entries, key=lambda x: x.timestamp, reverse=True)[:limit]
        ]

    def list_channels(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "channel": c.channel.value,
                    "enabled": c.enabled,
                    "has_endpoint": bool(c.endpoint),
                    "has_token": bool(c.token),
                }
                for c in self._channels.values()
            ]

    def stats(self) -> dict:
        with self._lock:
            total = len(self._history)
            sent = sum(1 for m in self._history if m.status == "sent")
            failed = sum(1 for m in self._history if m.status == "failed")
            return {
                "total_messages": total,
                "sent": sent,
                "failed": failed,
                "channels_configured": len(self._channels),
                "channels_enabled": sum(1 for c in self._channels.values() if c.enabled),
            }
