"""Alert manager — 告警规则、通知与历史记录。"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import httpx

from src.config import settings

from .collector import MetricsCollector, MetricsSnapshot

if TYPE_CHECKING:
    from src.vital.alert_persist import AlertPersistence

logger = logging.getLogger(__name__)


@dataclass
class AlertRule:
    name: str
    metric: str  # dot-path, e.g. "system.cpu_percent"
    threshold: float
    op: str = ">"  # ">" or "<"
    severity: str = "warning"
    message_tpl: str = ""


@dataclass
class AlertRecord:
    rule_name: str
    severity: str
    message: str
    value: float
    threshold: float
    ts: float = field(default_factory=time.time)
    resolved: bool = False
    # GR-①b-⑤ 行标识（落盘真源主键；默认生成，测试/回载可显式给）。
    alert_id: str = field(default_factory=lambda: f"alert_{uuid.uuid4().hex[:12]}")

    def to_row(self) -> dict:
        """行形状 dict（AlertPersistence._COLUMNS 逐键同形，单一形状禁两套）。"""
        return {
            "alert_id": self.alert_id,
            "rule_name": self.rule_name,
            "severity": self.severity,
            "message": self.message,
            "value": self.value,
            "threshold": self.threshold,
            "ts": self.ts,
            "resolved": self.resolved,
        }

    @classmethod
    def from_row(cls, row: dict) -> "AlertRecord":
        """行 → 记录（与 to_row 往返逐键保真，读面零可观测变化）。"""
        return cls(
            rule_name=str(row.get("rule_name", "")),
            severity=str(row.get("severity", "")),
            message=str(row.get("message", "")),
            value=float(row.get("value", 0.0)),
            threshold=float(row.get("threshold", 0.0)),
            ts=float(row.get("ts", 0.0)),
            resolved=bool(row.get("resolved", False)),
            alert_id=str(row.get("alert_id", "")),
        )


DEFAULT_RULES: list[AlertRule] = [
    AlertRule(
        name="high_cpu",
        metric="system.cpu_percent",
        threshold=80.0,
        message_tpl="CPU 使用率 {value:.1f}% 超过阈值 {threshold}%",
    ),
    AlertRule(
        name="high_memory",
        metric="system.memory_percent",
        threshold=90.0,
        message_tpl="内存使用率 {value:.1f}% 超过阈值 {threshold}%",
    ),
    AlertRule(
        name="high_disk",
        metric="system.disk_percent",
        threshold=95.0,
        message_tpl="磁盘使用率 {value:.1f}% 超过阈值 {threshold}%",
    ),
    AlertRule(
        name="high_error_rate",
        metric="app.error_rate",
        threshold=0.05,
        message_tpl="错误率 {value:.2%} 超过阈值 {threshold:.0%}",
    ),
]


def _resolve_path(snap: MetricsSnapshot, dotpath: str) -> float:
    obj = snap
    for part in dotpath.split("."):
        obj = getattr(obj, part)
    return float(obj)


class AlertManager:
    """评估告警规则并发送通知。"""

    # GR-①b-⑤ 内存窗（缓存尺寸非数据阈值——行真源恒全表落库、任何 N 值读面输出
    # 不变=零可观测变化零新阈值；persist 在场才裁=无真源裁=丢唯一副本；N<=0 不裁）
    history_window: int = 1000

    def __init__(
        self,
        collector: MetricsCollector,
        rules: list[AlertRule] | None = None,
        persist: Optional["AlertPersistence"] = None,
    ) -> None:
        self._collector = collector
        self._rules = rules or list(DEFAULT_RULES)
        self._history: list[AlertRecord] = []
        self._firing: dict[str, AlertRecord] = {}  # rule_name -> active alert
        self._task: asyncio.Task | None = None
        self._persist = persist
        if persist is not None:
            self._reload_from_persist()

    @property
    def history(self) -> list[AlertRecord]:
        return list(self._history)

    # ── Alert history（GR-①b-⑤ 内存窗+行真源+计数真源化+解决态写穿） ──

    def _record_alert(self, record: AlertRecord) -> None:
        """告警史写点单一收口（append 点共用，禁两套写史逻辑）。

        内存窗 append + persist 写穿 + 窗裁剪（delitem 形态=普查判定单一源可认）：
        窗=展示缓存，行真源恒在库；计数不随窗裁剪（计数真源=行聚合，_counter_truth）。
        """
        self._history.append(record)
        if self._persist is None:
            return
        try:
            self._persist.insert(record.to_row())
        except Exception as exc:  # noqa: BLE001 — 记账旁路纪律（告警判定/通知已发出，落盘失败可见不阻断）
            logger.error("alert history persist failed (in-memory record intact): %s", exc)

        # GR-①b-⑤ 内存窗裁剪（persist 在场才裁=无真源裁=丢唯一副本；N<=0 不裁）
        if self.history_window > 0 and len(self._history) > self.history_window:
            del self._history[: len(self._history) - self.history_window]

    def _resolve_alert(self, record: AlertRecord) -> None:
        """解决点单一收口：内存 resolved=True + 行影终值写穿（healer 状态快照同款）。

        落盘失败=记账旁路（告警解决本体已完成，logger.error 可见；该行行影停在
        resolved=0=非谎报 durable 的对应面，见 alert_persist 诚实边界④）。
        """
        record.resolved = True
        if self._persist is None:
            return
        try:
            self._persist.mark_resolved(record.alert_id)
        except Exception as exc:  # noqa: BLE001 — 记账旁路纪律
            logger.error("alert resolve persist failed (in-memory state intact): %s", exc)

    def _reload_from_persist(self) -> None:
        """启动回载：内存窗回填（GR-①b-⑤——只回载最近 history_window 行作展示缓存，
        修前无落盘口；全量回载=重启即无界）+ firing 状态恢复（未解决行=该规则仍在
        firing：解决只发生在「不再触发」评估上，重启前未解决=最后所见仍在触发；恢复
        防重启后同条件重复告警+重复通知+同事件双行=数据对账「无重复行」纪律）。
        回载失败不阻断构造（记账旁路纪律：告警面故障绝不吃掉 service 启动），
        logger.error 可见+空史起步。
        """
        if self._persist is None:
            return
        try:
            rows = self._persist.load_recent(self._reload_limit())
        except Exception as exc:  # noqa: BLE001 — 记账旁路纪律
            logger.error("alert history reload failed (starting with empty history): %s", exc)
            return
        for row in rows:
            rec = AlertRecord.from_row(row)
            self._history.append(rec)
            if not rec.resolved:
                self._firing[rec.rule_name] = rec

    def _reload_limit(self) -> int:
        """回载行数上界=内存窗（N<=0=不裁窗语义，回载上界如实放开）。"""
        return self.history_window if self.history_window > 0 else 2**63 - 1

    def _cache_counters(self) -> dict:
        """内存计数缓存视图（_counter_truth 回落面；persist=None 纯内存真源面）。
        与 AlertPersistence.counters() 逐键同形状（单一形状禁两套）。"""
        return {
            "total": len(self._history),
            "active": sum(1 for a in self._history if not a.resolved),
        }

    def _counter_truth(self) -> dict:
        """计数真源（GR-①b-⑤「计数真源化」MEM-⑥ 纪律）：persist 在场=恒对账
        alert_history 行聚合（计数器=行缓存视图，值读面读真源禁信第二套聚合；
        聚合恒算全表=内存窗裁剪/回载窗不漏计=历史总量语义不变，active=解决态
        写穿后的真未解决数）；缺省=纯内存计数器既有语义字节不变。对账失败=
        logger.error 可见+回落内存计数缓存（缓存本职，禁静默假 0）。"""
        if self._persist is None:
            return self._cache_counters()
        try:
            return self._persist.counters()
        except Exception as exc:  # noqa: BLE001 — 记账旁路纪律
            logger.error("alert counter truth failed (falling back to cache): %s", exc)
            return self._cache_counters()

    def counters(self) -> dict:
        """计数读面（/api/vital/stats alerts.total/active 真源口）：{total, active}。"""
        return self._counter_truth()

    def full_history(self) -> list[AlertRecord]:
        """全量史读面（/api/vital/alerts；GR-①b-① recent_records 同款回落纪律）：
        窗覆盖全史=窗快路径（含写失败行窗内可见=修前面）；窗不足回落行真源
        load_all（任何 N 值读面输出不变）；回落失败降级窗可见非静默。"""
        covers_all = (
            self._persist is None
            or self.history_window <= 0
            or len(self._history) < self.history_window
        )
        if covers_all:
            return list(self._history)
        try:
            return [AlertRecord.from_row(r) for r in self._persist.load_all()]
        except Exception as exc:  # noqa: BLE001 — 记账旁路纪律
            logger.error("alert full history fallback failed (window shown): %s", exc)
            return list(self._history)

    async def start(self, interval: float = 30.0) -> None:
        self._task = asyncio.create_task(self._loop(interval))
        logger.info("AlertManager started (interval=%.1fs)", interval)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def add_rule(self, rule: AlertRule) -> None:
        self._rules.append(rule)

    async def _loop(self, interval: float) -> None:
        while True:
            try:
                await self.evaluate()
            except Exception:
                logger.exception("Alert evaluation failed")
            await asyncio.sleep(interval)

    async def evaluate(self) -> list[AlertRecord]:
        snap = self._collector.snapshot
        new_alerts: list[AlertRecord] = []

        for rule in self._rules:
            try:
                value = _resolve_path(snap, rule.metric)
            except (AttributeError, TypeError):
                continue

            triggered = value > rule.threshold if rule.op == ">" else value < rule.threshold

            if triggered and rule.name not in self._firing:
                record = AlertRecord(
                    rule_name=rule.name,
                    severity=rule.severity,
                    message=rule.message_tpl.format(value=value, threshold=rule.threshold),
                    value=value,
                    threshold=rule.threshold,
                )
                self._firing[rule.name] = record
                self._record_alert(record)
                new_alerts.append(record)
                logger.warning("ALERT [%s] %s", rule.severity, record.message)
                await self._notify(record)

            elif not triggered and rule.name in self._firing:
                old = self._firing.pop(rule.name)
                self._resolve_alert(old)
                logger.info("RESOLVED %s", rule.name)

        return new_alerts

    async def _notify(self, record: AlertRecord) -> None:
        # Push to Notification Center
        try:
            from src.api.notifications import push_notification

            emoji = (
                "🔴"
                if record.severity == "critical"
                else "🟡"
                if record.severity == "warning"
                else "🔵"
            )
            push_notification(
                source="vital",
                title=f"{emoji} 系统告警: {record.rule_name}",
                body=record.message,
                level="error" if record.severity == "critical" else "warning",
                organ="vital",
                emoji=emoji,
                action_url="/vital",
                metadata={
                    "rule": record.rule_name,
                    "value": record.value,
                    "threshold": record.threshold,
                },
            )
        except (ImportError, AttributeError):
            pass

        if settings.alert_webhook_url:
            await self._send_webhook(record)

    async def _send_webhook(self, record: AlertRecord) -> None:
        payload = {
            "rule": record.rule_name,
            "severity": record.severity,
            "message": record.message,
            "value": record.value,
            "threshold": record.threshold,
            "ts": record.ts,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(settings.alert_webhook_url, json=payload)
                resp.raise_for_status()
        except Exception:
            logger.exception("Webhook notification failed for %s", record.rule_name)
