from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from src.gland.usage_persist import UsagePersistence

logger = logging.getLogger(__name__)


@dataclass
class UsageRecord:
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    user_id: str | None = None
    task: str | None = None
    timestamp: float = field(default_factory=time.time)


class TokenMeter:
    """Tracks per-call token consumption, per-user/model aggregation, and budget enforcement."""

    def __init__(
        self,
        budget_limit: int = 0,
        alert_threshold: float = 0.8,
        persist: UsagePersistence | None = None,
    ) -> None:
        """
        Args:
            budget_limit: Maximum total tokens allowed. 0 = unlimited.
            alert_threshold: Fraction of budget at which to log a warning (0.0–1.0).
            persist: RV-①b-① 用量行持久化真源（缺省 None=纯内存，既有语义零变化）。
                给定即 record() 写穿 + 构造期回载（重启用量不丢）。
        """
        self._budget_limit = budget_limit
        self._alert_threshold = alert_threshold
        self._records: list[UsageRecord] = []

        # Aggregated counters（DEC-㉖-①：prompt/completion 两轴真源补现）
        self._by_user: dict[str, int] = defaultdict(int)
        self._by_model: dict[str, int] = defaultdict(int)
        self._by_provider: dict[str, dict[str, int]] = defaultdict(
            lambda: {"prompt": 0, "completion": 0, "total": 0, "requests": 0}
        )
        self._total: int = 0
        self._total_prompt: int = 0
        self._total_completion: int = 0

        self._persist = persist
        if persist is not None:
            self._reload_from_persist()

    # ── recording ────────────────────────────────────────────────

    def record(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        user_id: str | None = None,
        task: str | None = None,
    ) -> UsageRecord:
        """Record a single API call's token usage. Raises if budget exhausted."""
        total = prompt_tokens + completion_tokens
        self._check_budget(total)

        rec = UsageRecord(
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            user_id=user_id,
            task=task,
        )
        self._records.append(rec)
        self._fold(rec)

        # RV-①b-① 写穿：记账落盘失败不阻断已完成的 LLM 调用，但 logger.error
        # 可见（F4b-㉕ 记账写点先例）——故障窗口内该行只活内存，如实记非谎报 durable。
        if self._persist is not None:
            try:
                self._persist.insert(
                    provider=rec.provider,
                    model=rec.model,
                    prompt_tokens=rec.prompt_tokens,
                    completion_tokens=rec.completion_tokens,
                    total_tokens=rec.total_tokens,
                    user_id=rec.user_id,
                    task=rec.task,
                    timestamp=rec.timestamp,
                )
            except Exception as exc:  # noqa: BLE001 — 记账旁路纪律（失败可见不阻断）
                logger.error("token usage persist failed (in-memory accounting intact): %s", exc)

        self._maybe_alert()
        return rec

    def _fold(self, rec: UsageRecord) -> None:
        """聚合折叠（单一源：record() 写入与持久化回载共用，禁两套聚合逻辑）。"""
        self._total += rec.total_tokens
        self._total_prompt += rec.prompt_tokens
        self._total_completion += rec.completion_tokens
        self._by_model[rec.model] += rec.total_tokens
        prov = self._by_provider[rec.provider]
        prov["prompt"] += rec.prompt_tokens
        prov["completion"] += rec.completion_tokens
        prov["total"] += rec.total_tokens
        prov["requests"] += 1
        if rec.user_id:
            self._by_user[rec.user_id] += rec.total_tokens

    def _reload_from_persist(self) -> None:
        """启动回载：用量行真源回载 + 聚合折叠重算（聚合是行的派生值，MEM-⑥ 纪律：
        禁第二套聚合列真源）。回载失败不阻断构造（记账旁路纪律同写穿口：记账面
        故障绝不吃掉 gateway import/service 启动），logger.error 可见+空账起步，
        行真源仍在库下次启动重试。"""
        if self._persist is None:
            return
        try:
            rows = self._persist.load_all()
        except Exception as exc:  # noqa: BLE001 — 记账旁路纪律（失败可见不阻断）
            logger.error("token usage reload failed (starting with empty accounting): %s", exc)
            return
        for row in rows:
            rec = UsageRecord(
                provider=row["provider"],
                model=row["model"],
                prompt_tokens=row["prompt_tokens"],
                completion_tokens=row["completion_tokens"],
                total_tokens=row["total_tokens"],
                user_id=row["user_id"],
                task=row["task"],
                timestamp=row["timestamp"],
            )
            self._records.append(rec)
            self._fold(rec)

    # ── budget ───────────────────────────────────────────────────

    def _check_budget(self, incoming: int) -> None:
        if self._budget_limit <= 0:
            return
        if self._total + incoming > self._budget_limit:
            raise BudgetExceededError(
                f"Budget exhausted: {self._total}/{self._budget_limit} tokens used, "
                f"request needs {incoming} more."
            )

    def _maybe_alert(self) -> None:
        if self._budget_limit <= 0:
            return
        ratio = self._total / self._budget_limit
        if ratio >= self._alert_threshold:
            logger.warning(
                "Token budget alert: %.1f%% used (%d/%d tokens)",
                ratio * 100,
                self._total,
                self._budget_limit,
            )

    def set_budget(self, limit: int) -> None:
        self._budget_limit = limit

    @property
    def remaining_budget(self) -> int | None:
        if self._budget_limit <= 0:
            return None
        return max(0, self._budget_limit - self._total)

    # ── queries ──────────────────────────────────────────────────

    @property
    def total_tokens(self) -> int:
        return self._total

    def usage_by_model(self) -> dict[str, int]:
        return dict(self._by_model)

    def usage_by_user(self) -> dict[str, int]:
        return dict(self._by_user)

    def usage_by_provider(self) -> dict[str, dict[str, int]]:
        """Per-provider breakdown. Element shape (DEC-㉖-① 真源补现):
        {prompt, completion, total, requests}."""
        return {name: dict(counts) for name, counts in self._by_provider.items()}

    def summary(self) -> dict:
        return {
            "total_tokens": self._total,
            "total_prompt_tokens": self._total_prompt,
            "total_completion_tokens": self._total_completion,
            "budget_limit": self._budget_limit or None,
            "remaining_budget": self.remaining_budget,
            "call_count": len(self._records),
            "by_model": self.usage_by_model(),
            "by_user": self.usage_by_user(),
            "by_provider": self.usage_by_provider(),
        }

    def recent_records(self, limit: int = 50) -> list[dict]:
        # 元素形含 task 轴（DEC-㉖B2-① 真源补现：调用点带 task 语义；未提供=None
        # 如实，键恒在场禁静默缺席）。
        return [
            {
                "provider": r.provider,
                "model": r.model,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens,
                "user_id": r.user_id,
                "task": r.task,
                "timestamp": r.timestamp,
            }
            for r in self._records[-limit:]
        ]


class BudgetExceededError(Exception):
    """Raised when a request would exceed the configured token budget."""
