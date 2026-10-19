from __future__ import annotations

import asyncio
import enum
import json
import logging
import time
from dataclasses import dataclass, field

import httpx

from src.gland.harness_profiles import (
    ModelRole,
    model_key_for,
    profile_for,
    resolve_task,
)
from src.gland.key_manager import KeyManager
from src.gland.token_meter import TokenMeter

logger = logging.getLogger(__name__)


def _outbound_redactor():
    """Lazy singleton ContentModerator for outbound LLM redaction.

    Fail-safe: any import/compile failure disables redaction (logged once)
    instead of breaking every LLM call.
    """
    global _REDACTOR, _REDACTOR_INIT
    if not _REDACTOR_INIT:
        _REDACTOR_INIT = True
        try:
            from src.immune.moderator import ContentModerator

            _REDACTOR = ContentModerator()
        except Exception as exc:
            logger.warning("Outbound secret redaction disabled: %s", exc)
            _REDACTOR = None
    return _REDACTOR


_REDACTOR = None


def extract_chat_text(result) -> str:
    """从chat()返回的OpenAI风格响应体提取文本content（唯一权威解包点）。

    背景（live实证bug）：chat()/_call_chat返回provider原始响应体resp.json()——
    choices[0].message.content结构；调用方此前各自猜测形状
    （result.get("content", result.get("text", str(result)))），真实provider上
    顶层无content键→回退str(整个响应体)→下游JSON解析全部失败且无error
    （Phase1提取count=0、dream gland路径0 actions——"写了≠接线了≠能跑了"标本）。
    解包优先级：str直通 → 顶层content/text（扁平测试桩/个别provider）→
    choices[0].message.content → choices[0].text → 无法识别时str(result)
    （保留repr进调用方raw_response，失败必须可见而非静默空串）。
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if isinstance(result.get("content"), str):
            return result["content"]
        if isinstance(result.get("text"), str):
            return result["text"]
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] or {}
            msg = first.get("message") or {}
            if isinstance(msg.get("content"), str) and msg["content"]:
                return msg["content"]
            if isinstance(first.get("text"), str) and first["text"]:
                return first["text"]
    return str(result)


def _chat_payload(
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    stream: bool,
    top_p: float | None = None,
    top_k: int | None = None,
) -> dict:
    """chat/completions请求体 — top_p/top_k仅在显式声明时进payload（kilocode #8
    "temperature/topP/topK按模型解析"）；None时请求体与既有字节恒等（零行为漂移）。"""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if top_p is not None:
        payload["top_p"] = top_p
    if top_k is not None:
        payload["top_k"] = top_k
    return payload


_REDACTOR_INIT = False


class StreamingProtocolError(Exception):
    """上游流式响应形态不可消费（空帧流/坏帧/未知 content-type）。

    必须可见：抛出后由 _walk_chain 按节点失败走 provider 链降级，
    禁静默回空串把协议故障伪装成"模型没说话"。
    """


def _aggregate_sse_frames(lines) -> dict:
    """把 OpenAI 风格 chat-completion SSE 帧流聚合成单个 completion dict（真流式消费）。

    F4b-① 收口对象：stream=True 时上游回 text/event-stream，旧代码 resp.json()
    整段解析必抛 JSONDecodeError（wired 但不 functional）。本函数逐帧读
    delta.content 拼接，id/model/created/usage/finish_reason/role 如实透传上游值
    ——上游没发的键不硬造（finish_reason 缺失即 None、usage 缺失即无键；role 缺失
    默认 assistant，回复方角色无歧义）。`[DONE]` 即止，注释/心跳/空行忽略。
    返回形状与 stream=False 的 resp.json() 同一契约（chat() 返回整段 dict），
    多 choice 按 index 各自聚合不静默丢弃；逐 token 交付面不在此层。
    """
    per_choice: dict[int, dict] = {}
    frames_seen = 0
    resp_id = None
    model = None
    created = None
    usage = None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue  # 心跳/注释帧、event/id 等非数据行
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as exc:
            raise StreamingProtocolError(
                f"SSE data frame is not valid JSON: {data[:120]!r}"
            ) from exc
        if not isinstance(obj, dict):
            raise StreamingProtocolError(
                f"SSE data frame is not a JSON object: {type(obj).__name__}"
            )
        frames_seen += 1
        if isinstance(obj.get("id"), str):
            resp_id = obj["id"]
        if isinstance(obj.get("model"), str):
            model = obj["model"]
        if isinstance(obj.get("created"), int):
            created = obj["created"]
        if isinstance(obj.get("usage"), dict):
            usage = obj["usage"]
        choices = obj.get("choices")
        if not isinstance(choices, list):
            continue
        for ch in choices:
            if not isinstance(ch, dict):
                continue
            raw_idx = ch.get("index")
            idx = raw_idx if isinstance(raw_idx, int) else 0
            entry = per_choice.setdefault(idx, {"parts": [], "role": None, "finish_reason": None})
            delta = ch.get("delta")
            if not isinstance(delta, dict):
                delta = {}
            if isinstance(delta.get("role"), str):
                entry["role"] = delta["role"]
            piece = delta.get("content")
            if isinstance(piece, str) and piece:
                entry["parts"].append(piece)
            if isinstance(ch.get("finish_reason"), str):
                entry["finish_reason"] = ch["finish_reason"]
    if frames_seen == 0:
        raise StreamingProtocolError("streaming response contained no SSE data frames")
    result: dict = {
        "choices": [
            {
                "index": idx,
                "message": {
                    "role": per_choice[idx]["role"] or "assistant",
                    "content": "".join(per_choice[idx]["parts"]),
                },
                "finish_reason": per_choice[idx]["finish_reason"],
            }
            for idx in sorted(per_choice)
        ]
    }
    if resp_id is not None:
        result["id"] = resp_id
    if model is not None:
        result["model"] = model
    if created is not None:
        result["created"] = created
    if usage is not None:
        result["usage"] = usage
    return result


# Minimum risk level redacted before text leaves the machine toward an LLM
# provider. "critical" = API keys/tokens/passwords only; set to "low" to also
# redact PII (phone/id/email/IP). Configurable per-deployment.
OUTBOUND_REDACT_MIN_RISK = "critical"


def _is_rate_limited(exc: BaseException) -> bool:
    """HTTP 429 — the provider is overloaded, not broken."""
    import httpx

    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


def _is_transient_error(exc: BaseException) -> bool:
    """Error class that plausibly recovers on its own (429/5xx/transport).

    Used to decide whether the CowAgent wrap-around pass is worthwhile:
    permanent errors (401/404/400) will still be permanent a second later.
    """
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        from src.cortex.llm_retry import is_retryable_status

        return is_retryable_status(exc.response.status_code)
    return isinstance(exc, httpx.TransportError)


# Request-level statuses that mean "this MODEL is not served here" (400/404/422),
# as opposed to endpoint/key/account-level failures (401/402/429/5xx/transport).
# Only model-rejected errors fall through to the link's next model candidate;
# endpoint-level errors move on to the next LINK (another model on a dead
# endpoint is pointless). Live evidence 2026-09-25: token-plan returns
# 400 "Unsupported model partial-test" for a placeholder model config while
# the endpoint serves the requested model fine — exactly this class.
_MODEL_REJECTED_STATUSES = frozenset({400, 404, 422})


def _is_model_rejected(exc: BaseException) -> bool:
    """True when the endpoint rejected the MODEL name specifically."""
    import httpx

    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in _MODEL_REJECTED_STATUSES
    )


def _model_candidates(
    provider_models: dict[str, str],
    own: str | None,
    explicit: str | None,
    *,
    is_primary: bool = False,
    chain_fallback: str | None = None,
) -> tuple[str, ...]:
    """Ordered per-link model candidates (CowAgent 备胎降级语义).

    显式model不再是链上所有link的唯一候选（2026-09-25 live实锤：`ollama/mimo-v2.5-pro`
    ——显式model劫持备胎link，备胎永远拿不到自己声明的模型，fallback链形同虚设）。
    每个link按声明关系排序候选：

    - 主link（is_primary）→ 显式model恒赢优先（调用方点名、"不许被role/task
      路由顶掉"契约保持），自己声明的role/task模型作后备
    - 备胎link声明了显式model（model in provider.models.values()）→ 显式优先
      （同模型多网关failover），声明模型作后备
    - 备胎link没声明显式model → 自己声明的模型优先（它声明了它服务什么），
      显式model作后备（模型目录失真/占位配置时的自愈路径——token-plan
      `partial-test`占位符400后接住显式 `mimo-v2.5-pro` 实证场景）
    - 无显式model → 备胎link尾随chain_fallback（主link解析出的模型）作最后候选
      ——2026-09-25 live实锤：role驱动调用（chat(model=None, role=...)，branch
      摘要/eval_loop等）没有显式model，占位声明`partial-test`成单候选直接死，
      自愈路径对整类调用失效；chain_fallback补齐后同输入partial-test 400 →
      mimo-v2.5-pro接住。主link无显式model时行为不变（字节恒等）
    - 无显式model且无chain_fallback → 单候选（既有行为字节恒等）

    Candidate内模型级拒绝（400/404/422）滑到下一候选；端点级错误整link放弃。
    """
    out: list[str] = []
    if explicit:
        declared = explicit in set(provider_models.values())
        if is_primary or declared:
            order = (explicit, own)
        else:
            order = (own, explicit)
    else:
        order = (own,) if is_primary else (own, chain_fallback)
    for m in order:
        if m and m not in out:
            out.append(m)
    return tuple(out)


def _auth_headers(api_key: str | None) -> dict:
    """Provider auth headers.

    Keyless providers (local Ollama) get no Authorization header;
    ``tp-`` subscription keys use the ``api-key`` header (the in-repo
    convention shared with api/llm.py list_models/test_connection — live
    verified 200 against token-plan-cn.xiaomimimo.com), everything else
    uses ``Authorization: Bearer``.
    """
    if not api_key:
        return {}
    if api_key.startswith("tp-"):
        return {"api-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


class TaskType(enum.StrEnum):
    CHAT = "chat"
    COMPLETION = "completion"
    EMBEDDING = "embedding"
    VISION = "vision"
    CODE = "code"


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    models: dict[str, str] = field(default_factory=dict)
    # models maps TaskType -> model name, e.g. {"chat": "gpt-4o", "embedding": "text-embedding-3-small"}
    enabled: bool = True
    priority: int = 0  # lower = higher priority
    _consecutive_failures: int = field(default=0, repr=False)
    _last_failure: float = field(default=0.0, repr=False)
    _cooldown_until: float = field(default=0.0, repr=False)


class ModelRouter:
    """Multi-provider LLM gateway with smart routing, load-balancing, and failover."""

    COOLDOWN_SECONDS = 60  # disable a provider for this long after repeated failures
    MAX_FAILURES = 3

    def __init__(self) -> None:
        self.providers: dict[str, ProviderConfig] = {}
        self.key_manager = KeyManager()
        self.token_meter = TokenMeter()
        self._http_client: httpx.AsyncClient | None = None
        # CowAgent chain trace: ordered {provider, model, pass, outcome/error}
        # log of the most recent chat()/embed() failover walk. Exposed via
        # get_chain_trace() and /api/gland endpoints — the chain walk must be
        # inspectable ("我都不知道他们在干嘛").
        self._last_chain_trace: list[dict] = []

    # ── lifecycle ────────────────────────────────────────────────

    async def startup(self) -> None:
        self._http_client = httpx.AsyncClient(timeout=180)
        self.key_manager.load_from_env()

    async def shutdown(self) -> None:
        if self._http_client:
            await self._http_client.aclose()

    # ── provider management ──────────────────────────────────────

    def add_provider(
        self,
        name: str,
        base_url: str,
        models: dict[str, str] | None = None,
        priority: int = 0,
    ) -> ProviderConfig:
        cfg = ProviderConfig(
            name=name,
            base_url=base_url.rstrip("/"),
            models=models or {},
            priority=priority,
        )
        self.providers[name] = cfg
        logger.info("Registered provider=%s base_url=%s priority=%d", name, base_url, priority)
        return cfg

    def has_provider(self, name: str) -> bool:
        """Whether *name* is currently registered（注册面冲突判定单一口，GLAND-②）。

        HTTP 注册面 create 语义的冲突判定真源（同名已注册=409 拒，禁静默覆盖）；
        引擎层 add_provider 的 upsert 语义（内部备胎幂等重注册）不经此口——
        register_local_backup「已注册→幂等返回不重建ProviderConfig」即其真依赖。
        """
        return name in self.providers

    def remove_provider(self, name: str) -> int | None:
        """Remove a provider and cascade-remove its keys（整 provider 移除含钥）。

        GLAND-①（DEC-㉑ 取 B）：provider 注册与其凭据同生共死——移除注册即撤销其
        钥（防凭据滞留：旧钥留驻 = 同名再注册即复用旧钥）。返回移除钥数（0=该
        provider 本无钥，注册照删）；未命中（provider 不存在）= None 零副作用
        ——绝不顺带清同名钥（env bootstrap 钥可合法存在于未注册 provider，
        DELETE 不得变相清钥）。
        """
        if self.providers.pop(name, None) is None:
            return None
        return self.key_manager.remove_provider_keys(name)

    def update_provider(self, name: str, changes: dict) -> ProviderConfig | None:
        """In-place provider config update, keys untouched（就地改面，GLAND-③）。

        只改给出的字段（整值替换非合并）：base_url（rstrip "/" 归一同 add_provider）/
        models（整表替换，{}=清空）/priority/enabled；**keys 一律不动**（不触钥——凭据
        增删只属 POST /keys、DELETE /keys/{provider}/{index} 与 DELETE provider
        （GLAND-① 级联）三条显式动作）。未注册 = None 零副作用。

        凭据端点绑定（DEC-㉒「旧钥不应打向新 base_url」原则）：base_url 实际变更且该
        provider 持钥 ≥1 → ProviderRetargetError 原子拒（先全量校验后一次性落写，
        任一字段非法/被拒 = 零写入）——旧钥不得被静默改道打向新端点；换端点流程 =
        先移除钥（或 DELETE+POST 整体换）再改。base_url 给出但归一后与现值相同
        = 非改道，照常放行（不误伤）。
        """
        cfg = self.providers.get(name)
        if cfg is None:
            return None
        allowed = {"base_url", "models", "priority", "enabled"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unknown provider field(s): {sorted(unknown)}")
        # 先全量校验（原子：任一字段非法 = 零写入），后一次性落写
        normalized: dict[str, object] = {}
        for key, value in changes.items():
            if key == "base_url":
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("base_url must be a non-empty string")
                normalized[key] = value.rstrip("/")
            elif key == "models":
                if not isinstance(value, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in value.items()
                ):
                    raise ValueError("models must be a dict[str, str]")
                normalized[key] = dict(value)
            elif key == "priority":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError("priority must be an int")
                normalized[key] = value
            elif key == "enabled":
                if not isinstance(value, bool):
                    raise ValueError("enabled must be a bool")
                normalized[key] = value
        new_base = normalized.get("base_url")
        if new_base is not None and new_base != cfg.base_url:
            held = len(self.key_manager.status().get(name, []))
            if held > 0:
                raise ProviderRetargetError(
                    f"Provider '{name}' has {held} key(s) bound to the current "
                    "endpoint; old keys must not hit a new base_url — remove the "
                    "keys first (or DELETE+re-register the provider) to retarget"
                )
        for key, value in normalized.items():
            setattr(cfg, key, value)
        logger.info(
            "Updated provider=%s fields=%s keys_untouched=%d",
            name,
            sorted(normalized),
            len(self.key_manager.status().get(name, [])),
        )
        return cfg

    def list_providers(self) -> list[dict]:
        out = []
        for p in self.providers.values():
            out.append(
                {
                    "name": p.name,
                    "base_url": p.base_url,
                    "models": p.models,
                    "enabled": p.enabled and not self._is_cooling_down(p),
                    "priority": p.priority,
                    "consecutive_failures": p._consecutive_failures,
                }
            )
        return out

    def get_chain_trace(self) -> list[dict]:
        """Return the failover-chain trace of the most recent request."""
        return list(self._last_chain_trace)

    # ── smart routing ────────────────────────────────────────────

    def _resolve_model(
        self,
        provider: ProviderConfig,
        task: TaskType,
        model: str | None,
        role: ModelRole | str | None = None,
    ) -> str | None:
        """Determine the concrete model name for a request.

        Harness Profiles (continue Model Roles): a role resolves to its own model
        key first (chat/summarize/embedding/rerank each配独立模型), then the task
        key, then the generic "chat" model.
        """
        if model:
            return model
        if role is not None:
            rk = model_key_for(role)
            if rk in provider.models:
                return provider.models[rk]
        return provider.models.get(task.value) or provider.models.get("chat")

    def _candidate_providers(self, task: TaskType) -> list[ProviderConfig]:
        """Return providers that can handle *task*, sorted by route-policy mode + priority.

        模型路由4按钮接线：cost模式本地provider优先，intelligence模式在线优先，
        balance/auto按priority（auto细粒度决策在chat层route_policy.resolve_target）。
        """
        candidates = []
        for p in self.providers.values():
            if not p.enabled:
                continue
            if self._is_cooling_down(p):
                continue
            # Provider must have at least a model mapping for this task or a generic "chat" fallback
            if task.value in p.models or "chat" in p.models:
                candidates.append(p)

        def _is_local(p: ProviderConfig) -> bool:
            return "localhost" in p.base_url or "127.0.0.1" in p.base_url

        mode = None
        try:
            from src.gland.route_policy import get_mode

            mode = get_mode()
        except Exception:
            pass
        if mode == "cost":
            candidates.sort(key=lambda c: (not _is_local(c), c.priority))
        elif mode == "intelligence":
            candidates.sort(key=lambda c: (_is_local(c), c.priority))
        else:
            candidates.sort(key=lambda c: c.priority)
        return candidates

    def _is_cooling_down(self, p: ProviderConfig) -> bool:
        return p._cooldown_until > time.time()

    def _mark_failure(self, p: ProviderConfig) -> None:
        p._consecutive_failures += 1
        p._last_failure = time.time()
        if p._consecutive_failures >= self.MAX_FAILURES:
            p._cooldown_until = time.time() + self.COOLDOWN_SECONDS
            logger.warning(
                "Provider=%s cooling down for %ds after %d failures",
                p.name,
                self.COOLDOWN_SECONDS,
                p._consecutive_failures,
            )

    def _mark_success(self, p: ProviderConfig) -> None:
        p._consecutive_failures = 0
        p._cooldown_until = 0.0

    # ── per-provider retry (kilocode retry.ts policy) ────────────

    # Max attempts against ONE provider before giving up and letting the
    # router fail over to the next candidate. Annotated as int: tests and
    # deployments override per-instance (e.g. router.RETRY_MAX_ATTEMPTS = 2).
    RETRY_MAX_ATTEMPTS: int = 3

    async def _with_retry(self, provider: ProviderConfig, fn, *, has_backup: bool = False):
        """Run a single-provider HTTP call with the cortex retry policy.

        Retryable errors (429/5xx/timeouts/connection resets) are retried
        in place — honoring the server's Retry-After header when present —
        so one transient blip no longer burns a failure mark and triggers
        failover. Non-retryable errors (401/400/404/...) raise immediately
        so the next provider is tried at once.

        CowAgent rate-limit fast-switch (38-CowAgent-source-supplement3 #12):
        when the provider is rate-limited (429) AND a backup link remains in
        the chain, raise immediately instead of waiting — "限流有备胎立即切换".
        With no backup ("没备胎干等"), the normal in-place retry applies.
        """
        from src.cortex.llm_retry import retry_delay_for

        attempt = 0
        while True:
            try:
                return await fn()
            except Exception as exc:
                if has_backup and _is_rate_limited(exc):
                    logger.info(
                        "Provider=%s rate-limited with backup available — "
                        "switching immediately (CowAgent fast-switch)",
                        provider.name,
                    )
                    raise
                delay = retry_delay_for(exc, attempt)
                if delay is None or attempt + 1 >= self.RETRY_MAX_ATTEMPTS:
                    raise
                attempt += 1
                logger.warning(
                    "Provider=%s attempt %d/%d failed (%s), retrying in %.1fs",
                    provider.name,
                    attempt,
                    self.RETRY_MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

    # ── CowAgent ordered fallback chain ─────────────────────────

    def _build_links(
        self,
        candidates: list[ProviderConfig],
        task: TaskType,
        model: str | None,
        role: ModelRole | str | None = None,
    ) -> list[tuple[ProviderConfig, tuple[str, ...], str]]:
        """Build the ordered {provider, models, api_key} chain.
        Keyless providers (e.g. local Ollama) stay in the chain with an
        empty key — _call_chat/_call_embedding omit the Authorization
        header in that case. A provider that rejects keyless calls will
        401 and the chain moves on (fail-safe, no worse than skipping).
        """
        links: list[tuple[ProviderConfig, tuple[str, ...], str]] = []
        primary_model: str | None = None
        for idx, provider in enumerate(candidates):
            own_model = self._resolve_model(provider, task, None, role=role)
            if idx == 0:
                primary_model = own_model
            call_models = _model_candidates(
                provider.models,
                own_model,
                model,
                is_primary=(idx == 0),
                chain_fallback=primary_model if idx > 0 else None,
            )
            if not call_models:
                logger.debug(
                    "Skipping provider=%s: no model for task=%s", provider.name, task.value
                )
                continue
            api_key = self.key_manager.next_key(provider.name) or ""
            links.append((provider, call_models, api_key))
        return links

    async def _walk_chain(self, links, tried: list[dict], invoke):
        """Walk the CowAgent ordered fallback chain.

        Pass 1 — each link in priority order; within a link the model
        candidates are tried in order (备胎降级：模型级拒绝→下一候选) and the
        kilocode retry policy applies, except rate-limits when a backup link
        remains (fast-switch). Each link failure is recorded in *tried*.

        Pass 2 (wrap-around) — only when the chain has ≥2 links and pass-1
        saw at least one transient error: "瞬时限流已恢复不该废掉整个turn".
        Single attempt per candidate, no in-place retry — a fast re-probe.

        On exhaustion raises AllProvidersFailedError listing every tried
        {provider, model, pass, error} (CowAgent: 链耗尽时报错列出所有试过的模型).

        Returns ``(result, provider, model)`` of the successful link.
        """
        last_error: Exception | None = None
        transient_seen = False

        async def _try_link(provider, call_models, call_key, pass_no, *, retry, has_backup=False):
            """Try one link's model candidates in order.

            Success → (result, provider, model). Model-rejected (400/404/422)
            → next candidate (备胎自愈：占位/失真模型名滑到下一候选).
            Endpoint-level failure → link abandoned (None), one failure mark
            per link regardless of candidate count.
            """
            nonlocal last_error, transient_seen
            for mi, call_model in enumerate(call_models):
                try:
                    if retry:
                        result = await self._with_retry(
                            provider,
                            lambda p=provider, k=call_key, m=call_model: invoke(p, k, m),
                            has_backup=has_backup,
                        )
                    else:
                        result = await invoke(provider, call_key, call_model)
                    self._mark_success(provider)
                    tried.append(
                        {
                            "provider": provider.name,
                            "model": call_model,
                            "pass": pass_no,
                            "outcome": "success",
                        }
                    )
                    return result, provider, call_model
                except Exception as exc:
                    last_error = exc
                    if _is_model_rejected(exc) and mi + 1 < len(call_models):
                        tried.append(
                            {
                                "provider": provider.name,
                                "model": call_model,
                                "pass": pass_no,
                                "outcome": "model_rejected",
                                "error": str(exc),
                            }
                        )
                        logger.warning(
                            "Chain pass %d: provider=%s model=%s rejected by endpoint — "
                            "falling back to %s",
                            pass_no,
                            provider.name,
                            call_model,
                            call_models[mi + 1],
                        )
                        continue
                    self._mark_failure(provider)
                    tried.append(
                        {
                            "provider": provider.name,
                            "model": call_model,
                            "pass": pass_no,
                            "error": str(exc),
                        }
                    )
                    if _is_transient_error(exc):
                        transient_seen = True
                    logger.warning(
                        "Chain pass %d: provider=%s model=%s failed: %s",
                        pass_no,
                        provider.name,
                        call_model,
                        exc,
                    )
                    return None
            return None

        # ── pass 1: ordered walk with per-link retry ─────────────
        for i, (provider, call_models, call_key) in enumerate(links):
            has_backup = i < len(links) - 1
            got = await _try_link(
                provider, call_models, call_key, 1, retry=True, has_backup=has_backup
            )
            if got is not None:
                return got

        # ── pass 2: wrap-around re-probe (CowAgent) ──────────────
        if len(links) >= 2 and transient_seen:
            logger.info(
                "Chain exhausted with transient errors — wrap-around pass 2 (%d links)",
                len(links),
            )
            for provider, call_models, call_key in links:
                got = await _try_link(provider, call_models, call_key, 2, retry=False)
                if got is not None:
                    return got

        # ── chain exhausted: enumerate everything tried ───────────
        tried_desc = "; ".join(
            f"{t['provider']}/{t['model']} (pass {t['pass']}): "
            f"{t.get('error', t.get('outcome', '?'))}"
            for t in tried
        )
        raise AllProvidersFailedError(
            f"All providers failed after {len(tried)} chain attempt(s). Tried: {tried_desc}",
            tried=tried,
        ) from last_error

    # ── API calls ────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        task: TaskType | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        user_id: str | None = None,
        stream: bool = False,
        role: ModelRole | str | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
    ) -> dict:
        """Route a chat request through the CowAgent ordered fallback chain.

        Harness Profiles (deepagents + continue): when *role* is set the request is
        routed to that role's own model and the model's :class:`HarnessProfile`
        supplies generation defaults (temperature/max_tokens) + a tail-preserving
        context clamp tuned to the model tier — the structural fix for "多模型共用
        一套prompt/工具面是小模型效果差的结构性原因". Explicit ``temperature`` /
        ``max_tokens`` / ``model`` always win over the profile; ``role=None`` keeps
        the pre-profile behaviour byte-identical.
        """
        prof = None
        if role is not None:
            role = ModelRole(role)
            task = TaskType(resolve_task(role))
        elif task is None:
            task = TaskType.CHAT

        candidates = self._candidate_providers(task)
        if not candidates:
            raise NoProviderError(f"No provider available for task={task.value}")

        links = self._build_links(candidates, task, model, role=role)
        if not links:
            raise NoProviderError(f"No provider with a usable model for task={task.value}")

        async def _invoke(provider: ProviderConfig, api_key: str, model_name: str) -> dict:
            nonlocal prof
            eff_temp, eff_max, eff_msgs = temperature, max_tokens, messages
            if role is not None:
                prof = profile_for(model_name, role)
                if eff_temp is None:
                    eff_temp = prof.temperature
                if eff_max is None:
                    eff_max = prof.max_tokens or 2048
                eff_msgs, _ = prof.clamp_messages(messages)
            if eff_temp is None:
                eff_temp = 0.7
            if eff_max is None:
                eff_max = 2048
            return await self._call_chat(
                provider,
                api_key,
                model_name,
                eff_msgs,
                temperature=eff_temp,
                max_tokens=eff_max,
                stream=stream,
                top_p=top_p,
                top_k=top_k,
            )

        tried: list[dict] = []
        self._last_chain_trace = tried
        result, succ_provider, succ_model = await self._walk_chain(links, tried, _invoke)
        if prof is not None:
            # Surface the applied harness profile on the chain trace (observability:
            # "我都不知道他们在干嘛" — which budget/role/tier actually ran).
            tried.append({"harness_profile": prof.describe(), "resolved_model": succ_model})

        # Record token usage on the winning link.
        usage = result.get("usage", {})
        self.token_meter.record(
            provider=succ_provider.name,
            model=succ_model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            user_id=user_id,
            # DEC-㉖B2-①：调用点带 task 语义（role 路由=resolve_task 派生、
            # 缺省=chat，此处 task 恒为已解析 TaskType）。
            task=task.value,
        )
        return result

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        user_id: str | None = None,
    ) -> list[list[float]]:
        """Route an embedding request through the CowAgent ordered fallback chain."""
        candidates = self._candidate_providers(TaskType.EMBEDDING)
        if not candidates:
            raise NoProviderError("No provider available for embedding")

        # Embedding model resolution follows the same 备胎降级 candidate rules:
        # own "embedding" mapping and the explicit model arg become the link's
        # ordered candidates (see _model_candidates).
        links: list[tuple[ProviderConfig, tuple[str, ...], str]] = []
        primary_embed_model: str | None = None
        for idx, provider in enumerate(candidates):
            own_model = provider.models.get("embedding")
            if idx == 0:
                primary_embed_model = own_model
            call_models = _model_candidates(
                provider.models,
                own_model,
                model,
                is_primary=(idx == 0),
                chain_fallback=primary_embed_model if idx > 0 else None,
            )
            if not call_models:
                continue
            api_key = self.key_manager.next_key(provider.name) or ""
            links.append((provider, call_models, api_key))
        if not links:
            raise NoProviderError("No provider with a usable embedding model")

        async def _invoke(provider: ProviderConfig, api_key: str, model_name: str):
            return await self._call_embedding(provider, api_key, model_name, texts)

        tried: list[dict] = []
        self._last_chain_trace = tried
        result, succ_provider, succ_model = await self._walk_chain(links, tried, _invoke)

        # Rough token estimate for embeddings: 1 token per 4 chars
        est_tokens = sum(len(t) // 4 for t in texts)
        self.token_meter.record(
            provider=succ_provider.name,
            model=succ_model,
            prompt_tokens=est_tokens,
            completion_tokens=0,
            user_id=user_id,
            task=TaskType.EMBEDDING.value,
        )
        return result

    # ── HTTP layer ───────────────────────────────────────────────

    async def _call_chat(
        self,
        provider: ProviderConfig,
        api_key: str,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        stream: bool,
        top_p: float | None = None,
        top_k: int | None = None,
    ) -> dict:
        # Outbound secret guard (Warp blocklist pattern): redact API keys /
        # tokens in message content before it leaves the machine toward the
        # provider. Fail-safe — redaction errors never block the LLM call.
        try:
            redactor = _outbound_redactor()
            if redactor is not None:
                messages, findings = redactor.redact_messages(
                    messages, min_risk=OUTBOUND_REDACT_MIN_RISK
                )
                if findings:
                    types = sorted({f["type"] for f in findings})
                    logger.warning(
                        "Redacted %d secret(s) before provider=%s: %s",
                        len(findings),
                        provider.name,
                        ", ".join(types),
                    )
        except Exception as exc:
            logger.debug("Outbound redaction skipped: %s", exc)

        client = self._http_client or httpx.AsyncClient(timeout=180)
        headers = _auth_headers(api_key)
        payload = _chat_payload(model, messages, temperature, max_tokens, stream, top_p, top_k)
        url = f"{provider.base_url}/chat/completions"
        if not stream:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()
        # stream=True：按上游真实响应形态真流式消费（F4b-① 收口）。旧代码
        # resp.json() 整段解析，对回 text/event-stream 的上游必抛 JSONDecodeError
        # （wired 但不 functional，全仓 stream=True 调用点=0 故为潜在断点）。
        # SSE 帧流→_aggregate_sse_frames 逐帧聚合；忽略 stream 回 JSON 的上游走
        # json 解析；其余形态如实抛 StreamingProtocolError（链降级可见）。
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").lower()
            if "event-stream" in ctype:
                return _aggregate_sse_frames([line async for line in resp.aiter_lines()])
            if "json" in ctype:
                return json.loads(await resp.aread())
            raise StreamingProtocolError(
                f"unrecognized content-type for stream=True response: {ctype or '<missing>'!r}"
            )

    async def _call_embedding(
        self,
        provider: ProviderConfig,
        api_key: str,
        model: str,
        texts: list[str],
    ) -> list[list[float]]:
        client = self._http_client or httpx.AsyncClient(timeout=60)
        headers = _auth_headers(api_key)
        resp = await client.post(
            f"{provider.base_url}/embeddings",
            headers=headers,
            json={"model": model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]

    # ── testing ──────────────────────────────────────────────────

    async def test_provider(self, provider_name: str) -> dict:
        """Send a minimal request to verify a provider is reachable."""
        provider = self.providers.get(provider_name)
        if not provider:
            return {"status": "error", "detail": f"Provider '{provider_name}' not found"}

        api_key = self.key_manager.next_key(provider_name)
        model = provider.models.get("chat") or next(iter(provider.models.values()), "")
        if not model:
            return {"status": "error", "detail": "No model configured for provider"}

        try:
            client = self._http_client or httpx.AsyncClient(timeout=30)
            resp = await client.post(
                f"{provider.base_url}/chat/completions",
                headers=_auth_headers(api_key),
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Say 'pong'."}],
                    "max_tokens": 16,
                },
            )
            resp.raise_for_status()
            reply = resp.json()["choices"][0]["message"]["content"]
            self._mark_success(provider)
            return {
                "status": "ok",
                "provider": provider_name,
                "model": model,
                "reply": reply.strip(),
            }
        except Exception as exc:
            self._mark_failure(provider)
            return {"status": "error", "provider": provider_name, "detail": str(exc)}


class NoProviderError(Exception):
    """No provider is available for the requested task type."""


class AllProvidersFailedError(Exception):
    """Every candidate provider failed.

    ``tried`` carries the full CowAgent chain trace (provider/model/pass/
    error per attempt) so callers and logs can see exactly what was tried.
    """

    def __init__(self, message: str, tried: list[dict] | None = None) -> None:
        super().__init__(message)
        self.tried: list[dict] = tried or []


class ProviderRetargetError(ValueError):
    """base_url 就地改道被凭据端点绑定原则拒绝（GLAND-③，DEC-㉓）。

    持钥 provider 的 base_url 变更 = 旧钥打向新端点，原子拒零写入；
    继承 ValueError（调用方可按既有宽泛契约捕获，A2AError 先例）。
    """
