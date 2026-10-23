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

    # 内存窗（GR-①b-①）：_records 只保留最近 N 条作展示缓存——缓存尺寸非数据
    # 阈值（行真源恒全表落库，读面回落 DB 零可观测变化，任何 N 值读面输出不变）。
    # 仅 persist 在场才裁（无真源可回落时裁=丢唯一副本）；N<=0=不裁。
    records_window: int = 1000

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

            # GR-①b-① 内存窗裁剪（delitem 形态=普查判定单一源可认）：窗=展示缓存，
            # 行真源恒在库；计数不随窗裁剪（计数真源=行聚合，_counter_truth）。
            if self.records_window > 0 and len(self._records) > self.records_window:
                del self._records[: len(self._records) - self.records_window]

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
        """启动回载：内存窗回填（GR-①b-①——只回载最近 records_window 行作展示缓存，
        修前 load_all 全量进内存=重启即无界）+ 聚合折叠重算（缓存近似值：窗外行
        不进缓存，真源恒行聚合 _counter_truth MEM-⑥ 纪律：禁第二套聚合列）。
        回载失败不阻断构造（记账旁路纪律同写穿口：记账面故障绝不吃掉 gateway
        import/service 启动），logger.error 可见+空账起步，行真源仍在库下次启动重试。"""
        if self._persist is None:
            return
        try:
            rows = self._persist.load_recent(self._reload_limit())
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

    def _reload_limit(self) -> int:
        """回载行数上界=内存窗（N<=0=不裁窗语义，回载上界如实放开）。"""
        return self.records_window if self.records_window > 0 else 2**63 - 1

    def _cache_counters(self) -> dict:
        """内存计数缓存视图（_counter_truth 回落面；persist=None 纯内存真源面）。
        与 UsagePersistence.counters() 逐键同形状（单一形状禁两套）。"""
        return {
            "call_count": len(self._records),
            "total_tokens": self._total,
            "total_prompt_tokens": self._total_prompt,
            "total_completion_tokens": self._total_completion,
            "by_model": dict(self._by_model),
            "by_user": dict(self._by_user),
            "by_provider": {name: dict(counts) for name, counts in self._by_provider.items()},
        }

    def _counter_truth(self) -> dict:
        """计数真源（GR-①b-①「计数真源化」MEM-⑥ 纪律）：persist 在场=恒对账
        gland_usage 行聚合（计数器=行缓存视图，值读面读真源禁信第二套聚合列；
        聚合恒算全表=内存窗裁剪/回载窗不漏计）；缺省=纯内存计数器既有语义字节
        不变。对账失败=logger.error 可见+回落内存计数缓存（缓存本职，禁静默假 0）。"""
        if self._persist is None:
            return self._cache_counters()
        try:
            return self._persist.counters()
        except Exception as exc:  # noqa: BLE001 — 对账失败回落缓存（禁假 0）
            logger.error(
                "token usage counter reconciliation failed (falling back to cache): %s", exc
            )
            return self._cache_counters()

    # ── budget ───────────────────────────────────────────────────

    def _check_budget(self, incoming: int) -> None:
        if self._budget_limit <= 0:
            return
        # 预算判定读计数真源（GR-①b-①：内存窗裁剪后缓存不全量，恒对账行聚合——
        # 窗外用量照样计入预算，禁裁窗变相放大预算）。
        used = self._counter_truth()["total_tokens"]
        if used + incoming > self._budget_limit:
            raise BudgetExceededError(
                f"Budget exhausted: {used}/{self._budget_limit} tokens used, "
                f"request needs {incoming} more."
            )

    def _maybe_alert(self) -> None:
        if self._budget_limit <= 0:
            return
        used = self._counter_truth()["total_tokens"]
        ratio = used / self._budget_limit
        if ratio >= self._alert_threshold:
            logger.warning(
                "Token budget alert: %.1f%% used (%d/%d tokens)",
                ratio * 100,
                used,
                self._budget_limit,
            )

    def set_budget(self, limit: int) -> None:
        self._budget_limit = limit

    @property
    def remaining_budget(self) -> int | None:
        if self._budget_limit <= 0:
            return None
        return max(0, self._budget_limit - self._counter_truth()["total_tokens"])

    # ── queries ──────────────────────────────────────────────────

    @property
    def total_tokens(self) -> int:
        return self._counter_truth()["total_tokens"]

    def usage_by_model(self) -> dict[str, int]:
        return dict(self._counter_truth()["by_model"])

    def usage_by_user(self) -> dict[str, int]:
        return dict(self._counter_truth()["by_user"])

    def usage_by_provider(self) -> dict[str, dict[str, int]]:
        """Per-provider breakdown. Element shape (DEC-㉖-① 真源补现):
        {prompt, completion, total, requests}."""
        return {name: dict(counts) for name, counts in self._counter_truth()["by_provider"].items()}

    def summary(self) -> dict:
        # 计数面全部读真源（GR-①b-① 计数真源化）；键序照旧（契约零变化）。
        truth = self._counter_truth()
        return {
            "total_tokens": truth["total_tokens"],
            "total_prompt_tokens": truth["total_prompt_tokens"],
            "total_completion_tokens": truth["total_completion_tokens"],
            "budget_limit": self._budget_limit or None,
            "remaining_budget": self.remaining_budget,
            "call_count": truth["call_count"],
            "by_model": dict(truth["by_model"]),
            "by_user": dict(truth["by_user"]),
            "by_provider": {name: dict(counts) for name, counts in truth["by_provider"].items()},
        }

    def recent_records(self, limit: int = 50) -> list[dict]:
        # 元素形含 task 轴（DEC-㉖B2-① 真源补现：调用点带 task 语义；未提供=None
        # 如实，键恒在场禁静默缺席）。
        # GR-①b-① 读面回落：内存窗不足 limit=回落行真源（窗=纯缓存、真源=全表，
        # 读面输出与裁窗前逐字一致=零可观测变化）；回落失败降级内存窗可见非静默。
        if self._persist is not None and limit > len(self._records):
            try:
                rows = self._persist.load_recent(limit)
            except Exception as exc:  # noqa: BLE001 — 读故障降级缓存（禁静默空）
                logger.error("token usage recent read failed (falling back to window): %s", exc)
                rows = None
            if rows is not None:
                return [
                    {
                        "provider": row["provider"],
                        "model": row["model"],
                        "prompt_tokens": row["prompt_tokens"],
                        "completion_tokens": row["completion_tokens"],
                        "total_tokens": row["total_tokens"],
                        "user_id": row["user_id"],
                        "task": row["task"],
                        "timestamp": row["timestamp"],
                    }
                    for row in rows
                ]
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
