"""Pulse Timer — High-precision timer with sub-second polling and signal dispatch."""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from src.pulse.signal_persist import SignalPersistence

logger = logging.getLogger(__name__)


class SignalType(StrEnum):
    TICK = "tick"  # Regular heartbeat tick
    INTERVAL = "interval"  # Custom interval signal
    CRON = "cron"  # Cron-style scheduled signal
    ONCE = "once"  # One-shot delayed signal


class SignalStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class PulseSignal:
    signal_id: str
    signal_type: SignalType
    name: str
    interval_ms: float  # milliseconds
    status: SignalStatus = SignalStatus.ACTIVE
    callback_url: str = ""  # webhook URL to notify
    payload: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_fired_at: float | None = None
    next_fire_at: float | None = None
    fire_count: int = 0
    max_fires: int = 0  # 0 = unlimited
    precision_ms: float = 1.0  # actual achieved precision
    drift_correction: float = 0.0  # accumulated drift in ms


@dataclass
class TickRecord:
    tick_id: str
    signal_id: str
    timestamp: float
    expected_at: float
    drift_ms: float  # actual - expected
    latency_ms: float  # processing latency


def _signal_to_row(sig: "PulseSignal") -> dict:
    """PulseSignal → 行 dict（落盘形=PulseSignal 字段集逐键，payload dict 由
    persist 侧整体 json.dumps）。"""
    return {
        "signal_id": sig.signal_id,
        "signal_type": sig.signal_type.value,
        "name": sig.name,
        "interval_ms": sig.interval_ms,
        "status": sig.status.value,
        "callback_url": sig.callback_url,
        "payload": sig.payload,
        "created_at": sig.created_at,
        "last_fired_at": sig.last_fired_at,
        "next_fire_at": sig.next_fire_at,
        "fire_count": sig.fire_count,
        "max_fires": sig.max_fires,
        "precision_ms": sig.precision_ms,
        "drift_correction": sig.drift_correction,
    }


def _row_to_signal(row: dict) -> "PulseSignal":
    """行 dict → PulseSignal（往返形）。枚举值不可解如实抛（坏行跳过责任在回载方，
    SignalType/SignalStatus 单一源禁在 persist 侧手抄合法值）。"""
    return PulseSignal(
        signal_id=str(row["signal_id"]),
        signal_type=SignalType(row["signal_type"]),
        name=str(row["name"]),
        interval_ms=float(row["interval_ms"]),
        status=SignalStatus(row["status"]),
        callback_url=str(row.get("callback_url") or ""),
        payload=dict(row.get("payload") or {}),
        created_at=float(row["created_at"]),
        last_fired_at=row.get("last_fired_at"),
        next_fire_at=row.get("next_fire_at"),
        fire_count=int(row.get("fire_count") or 0),
        max_fires=int(row.get("max_fires") or 0),
        precision_ms=float(1.0 if row.get("precision_ms") is None else row["precision_ms"]),
        drift_correction=float(row.get("drift_correction") or 0.0),
    )


class PulseEngine:
    """High-precision timer engine with drift correction.

    RV-①b-⑨：persist 在场时信号随变更落盘+首触懒回载（重启信号不丢），
    缺省 persist=None=纯内存既有语义字节不变。
    """

    def __init__(self, persist: SignalPersistence | None = None):
        self._signals: dict[str, PulseSignal] = {}
        self._tick_history: list[TickRecord] = []
        self._start_time = time.time()
        self._total_ticks = 0
        self._running = False
        # RV-①b-⑨ 持久化接线（缺省 None=纯内存零变化）：写穿+首触懒回载。
        self._persist = persist
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """首触懒回载（RV-①b-⑤-1 先例）：只补缺不覆盖（内存在册更真禁回载洗
        内存新态）；回载失败不置位下次触碰重试（诚实边界②）。每变更点/读点
        先载后录=回载时注册表恒空生产不变量。"""
        if self._persist is None or self._loaded:
            return
        try:
            rows = self._persist.load_all()
        except Exception as e:
            logger.error(f"pulse 信号回载失败（空注册表起步，下次触碰重试）: {e}")
            return
        for row in rows:
            try:
                sig = _row_to_signal(row)
            except Exception as e:
                logger.error(
                    f"pulse 信号坏行跳过（signal_id={row.get('signal_id')}，枚举值不可解）: {e}"
                )
                continue
            if sig.signal_id not in self._signals:
                self._signals[sig.signal_id] = sig
        # total_ticks 对账下限（诚实边界：tick 史 ring 不落盘=无行真源；重启后
        # 以在册信号 fire_count 合计为下限恢复可见一致性，删行即删其 tick 计数，
        # 只保在册合计——如实记不 fabricate 删行/旧次数）。
        self._total_ticks = max(
            self._total_ticks, sum(s.fire_count for s in self._signals.values())
        )
        self._loaded = True

    def _sync_signal(self, sig: PulseSignal) -> None:
        """写穿单一点（全变更点共用，RV-①b-② _sync_entry 纪律禁散落写点）：
        记账旁路——写失败 logger.error 可见不阻断信号动作本体（诚实边界①）。"""
        if self._persist is None:
            return
        try:
            self._persist.upsert(_signal_to_row(sig))
        except Exception as e:
            logger.error(f"pulse 信号落盘失败（该行只活内存重启丢，非谎报 durable）: {e}")

    def _drop_signal(self, signal_id: str) -> None:
        """删行单一点（delete_signal 真消费）：删信号=删行，失败旁路同 _sync_signal。"""
        if self._persist is None:
            return
        try:
            self._persist.delete(signal_id)
        except Exception as e:
            logger.error(f"pulse 信号删行失败（内存已删，行残留重启会复活）: {e}")

    def create_signal(
        self,
        name: str,
        signal_type: str,
        interval_ms: float,
        callback_url: str = "",
        payload: dict = None,
        max_fires: int = 0,
    ) -> PulseSignal:
        """Create a new pulse signal."""
        self._ensure_loaded()
        sig = PulseSignal(
            signal_id=f"pulse_{uuid.uuid4().hex[:12]}",
            signal_type=SignalType(signal_type),
            name=name,
            interval_ms=interval_ms,
            callback_url=callback_url,
            payload=payload or {},
            max_fires=max_fires,
            next_fire_at=time.time() + interval_ms / 1000.0,
        )
        self._signals[sig.signal_id] = sig
        self._sync_signal(sig)
        return sig

    def tick(self, signal_id: str) -> TickRecord | None:
        """Process a tick for a signal. Returns tick record."""
        self._ensure_loaded()
        sig = self._signals.get(signal_id)
        if not sig or sig.status != SignalStatus.ACTIVE:
            return None

        now = time.time()
        expected_at = sig.next_fire_at or now
        drift_ms = (now - expected_at) * 1000

        record = TickRecord(
            tick_id=f"tick_{uuid.uuid4().hex[:12]}",
            signal_id=signal_id,
            timestamp=now,
            expected_at=expected_at,
            drift_ms=drift_ms,
            latency_ms=0,  # measured externally
        )
        self._tick_history.append(record)
        if len(self._tick_history) > 10000:
            self._tick_history = self._tick_history[-5000:]

        # Update signal state
        sig.last_fired_at = now
        sig.fire_count += 1
        sig.drift_correction = drift_ms * 0.1  # EMA correction

        # Calculate next fire with drift correction
        corrected_interval = sig.interval_ms - sig.drift_correction
        sig.next_fire_at = now + max(1.0, corrected_interval) / 1000.0

        # Auto-complete one-shot signals
        if sig.signal_type == SignalType.ONCE:
            sig.status = SignalStatus.COMPLETED

        # Check max fires
        if sig.max_fires > 0 and sig.fire_count >= sig.max_fires:
            sig.status = SignalStatus.COMPLETED

        self._sync_signal(sig)
        self._total_ticks += 1
        return record

    def get_signal(self, signal_id: str) -> PulseSignal | None:
        self._ensure_loaded()
        return self._signals.get(signal_id)

    def list_signals(self, status: str = None) -> list[PulseSignal]:
        self._ensure_loaded()
        signals = list(self._signals.values())
        if status:
            signals = [s for s in signals if s.status.value == status]
        return sorted(signals, key=lambda s: s.created_at, reverse=True)

    def pause_signal(self, signal_id: str) -> bool:
        self._ensure_loaded()
        sig = self._signals.get(signal_id)
        if sig and sig.status == SignalStatus.ACTIVE:
            sig.status = SignalStatus.PAUSED
            self._sync_signal(sig)
            return True
        return False

    def resume_signal(self, signal_id: str) -> bool:
        self._ensure_loaded()
        sig = self._signals.get(signal_id)
        if sig and sig.status == SignalStatus.PAUSED:
            sig.status = SignalStatus.ACTIVE
            sig.next_fire_at = time.time() + sig.interval_ms / 1000.0
            self._sync_signal(sig)
            return True
        return False

    def cancel_signal(self, signal_id: str) -> bool:
        self._ensure_loaded()
        sig = self._signals.get(signal_id)
        if sig and sig.status in (SignalStatus.ACTIVE, SignalStatus.PAUSED):
            sig.status = SignalStatus.CANCELLED
            self._sync_signal(sig)
            return True
        return False

    def delete_signal(self, signal_id: str) -> bool:
        self._ensure_loaded()
        if signal_id in self._signals:
            del self._signals[signal_id]
            self._drop_signal(signal_id)
            return True
        return False

    def get_tick_history(self, signal_id: str = None, limit: int = 100) -> list[dict]:
        history = self._tick_history
        if signal_id:
            history = [t for t in history if t.signal_id == signal_id]
        history = history[-limit:]
        return [
            {
                "tick_id": t.tick_id,
                "signal_id": t.signal_id,
                "timestamp": t.timestamp,
                "expected_at": t.expected_at,
                "drift_ms": round(t.drift_ms, 3),
                "latency_ms": round(t.latency_ms, 3),
            }
            for t in history
        ]

    def get_stats(self) -> dict:
        self._ensure_loaded()
        uptime = time.time() - self._start_time
        signals = list(self._signals.values())
        by_type = {}
        for s in signals:
            by_type[s.signal_type.value] = by_type.get(s.signal_type.value, 0) + 1
        by_status = {}
        for s in signals:
            by_status[s.status.value] = by_status.get(s.status.value, 0) + 1

        # Precision stats from recent ticks
        recent = self._tick_history[-100:] if self._tick_history else []
        avg_drift = sum(abs(t.drift_ms) for t in recent) / len(recent) if recent else 0
        max_drift = max((abs(t.drift_ms) for t in recent), default=0)

        return {
            "uptime_seconds": round(uptime, 1),
            "total_ticks": self._total_ticks,
            "total_signals": len(signals),
            "by_type": by_type,
            "by_status": by_status,
            "precision": {
                "avg_drift_ms": round(avg_drift, 3),
                "max_drift_ms": round(max_drift, 3),
                "sample_size": len(recent),
            },
        }
