from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from src.config import settings
from src.gland.harness_profiles import (
    ModelRole,
    filter_tools_for,
    profile_for,
)
from src.gland.router import (
    AllProvidersFailedError,
    ModelRouter,
    NoProviderError,
    ProviderRetargetError,
    TaskType,
)

router = APIRouter()

# ── singleton gateway ───────────────────────────────────────────
# Bootstraps with env-based config; providers can be added/removed at runtime.
gateway = ModelRouter()


def _register_local_backup() -> None:
    """Ollama本地兜底入链 — register_local_backup单一真源+探活豁免。

    不可达→不入链（litellm「探活结果喂路由决策」：链上不留幻影备胎，全链失败
    时不再白打ConnectError）。幂等+探活TTL缓存，请求路径可安全重复调用。
    """
    ollama_url = getattr(settings, "ollama_base_url", "http://localhost:11434/v1")
    try:
        from src.api.llm import register_local_backup

        register_local_backup(
            gateway,
            ollama_url,
            {"chat": "deepseek-r1:latest", "embedding": "nomic-embed-text"},
        )
    except Exception:  # noqa: BLE001 — 备胎注册绝不反噬bootstrap/请求路径
        pass


def _ensure_bootstrapped() -> None:
    """Register default providers from settings on first use."""
    if gateway.providers:
        # 补口：bootstrap时ollama不可达被探活豁免→未入链；ollama后启动时经探活
        # TTL重探可达即补注册——gateway单例不因启动时序永久丢备胎（幂等安全）。
        _register_local_backup()
        return

    # OpenAI-compatible (covers OpenAI, custom endpoints, MiMo, etc.)
    if settings.llm_base_url:
        gateway.add_provider(
            name="openai",
            base_url=settings.llm_base_url,
            models={
                "chat": settings.llm_model,
                "embedding": settings.embedding_model,
            },
            priority=0,
        )
        if settings.llm_api_key:
            gateway.key_manager.add_key("openai", settings.llm_api_key)
        if settings.embedding_api_key and settings.embedding_api_key != settings.llm_api_key:
            gateway.key_manager.add_key("openai", settings.embedding_api_key)

    # 变体备胎入链（priority=5，单一真源register_variant_backup）：激活变体之外
    # 的另一已配置变体（标准API/订阅制双体系）——激活端点402/宕机时的真实备胎
    # （2026-09-25 live实证standard low_balance时subscription仍ok）。fail-safe：
    # 解析失败→不注册。
    try:
        from src.api.llm import register_variant_backup

        register_variant_backup(gateway, settings.llm_model)
    except Exception:  # noqa: BLE001 — 备胎注册绝不反噬bootstrap
        pass

    # Ollama (local, no key needed) — register_local_backup单一真源+探活豁免
    # （CowAgent有序降级链priority=10本地兜底；不可达不入链，见helper docstring）。
    _register_local_backup()


# ── request / response schemas ──────────────────────────────────


class ChatRequest(BaseModel):
    messages: list[dict]
    model: str | None = None
    provider: str | None = None
    task: str = "chat"
    role: str | None = None  # Harness Profile ModelRole (reasoning/summarize/...)
    tools: list[str] | None = None  # tool-face input — filtered per model/role
    temperature: float | None = None  # None → HarnessProfile default (0.7 w/o role)
    max_tokens: int | None = None  # None → HarnessProfile default (2048 w/o role)
    user_id: str | None = None


class EmbedRequest(BaseModel):
    texts: list[str]
    model: str | None = None
    provider: str | None = None
    user_id: str | None = None


class ProviderCreate(BaseModel):
    name: str
    base_url: str
    models: dict[str, str] | None = None
    api_key: str | None = None
    priority: int = 0


class ProviderUpdate(BaseModel):
    """PATCH 就地改载荷（GLAND-③）：只收可改字段，整值替换非合并。

    extra="forbid"：name（身份不可改，rename=DELETE+POST）等未知字段如实 422，
    禁静默忽略请求字段（F4b-②「字段被 pydantic 静默忽略=静默降级」同纪律）。
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    models: dict[str, str] | None = None
    priority: int | None = None
    enabled: bool | None = None


class KeyAdd(BaseModel):
    provider: str
    api_key: str


class BudgetUpdate(BaseModel):
    limit: int


# ── endpoints ───────────────────────────────────────────────────


@router.get("/health")
async def gland_health():
    """OpenGland health check."""
    _ensure_bootstrapped()
    providers = gateway.list_providers()
    total_keys = sum(len(gateway.key_manager._slots.get(p["name"], [])) for p in providers)
    return {
        "status": "ok",
        "component": "OpenGland",
        "providers": {
            "total": len(providers),
            "enabled": sum(1 for p in providers if p["enabled"]),
            "unhealthy": sum(1 for p in providers if not p["enabled"]),
        },
        "keys": {"total": total_keys},
        "token_meter": gateway.token_meter.summary(),
        "last_chain_trace": gateway.get_chain_trace(),
    }


@router.get("/models")
async def list_models():
    """List all available models grouped by provider."""
    _ensure_bootstrapped()
    result = {}
    for name, p in gateway.providers.items():
        result[name] = {
            "models": p.models,
            "enabled": p.enabled,
            "priority": p.priority,
        }
    return {"providers": result}


@router.post("/test")
async def test_connection(body: dict | None = None):
    """Test a provider connection. Defaults to the highest-priority provider."""
    _ensure_bootstrapped()
    provider_name = (body or {}).get("provider")
    if not provider_name:
        # Pick the first enabled provider
        candidates = [p for p in gateway.providers.values() if p.enabled]
        if not candidates:
            raise HTTPException(status_code=404, detail="No providers configured")
        provider_name = min(candidates, key=lambda p: p.priority).name

    result = await gateway.test_provider(provider_name)
    if result["status"] == "error":
        raise HTTPException(status_code=502, detail=result["detail"])
    return result


@router.get("/usage")
async def get_usage():
    """Return token usage statistics."""
    _ensure_bootstrapped()
    return gateway.token_meter.summary()


@router.get("/usage/recent")
async def get_recent_usage(limit: int = 50):
    """Return recent token usage records."""
    _ensure_bootstrapped()
    return {"records": gateway.token_meter.recent_records(limit)}


@router.post("/chat")
async def chat(req: ChatRequest):
    """Route a chat request through the gateway with automatic failover."""
    _ensure_bootstrapped()
    try:
        task = TaskType(req.task)
    except ValueError:
        task = TaskType.CHAT

    if req.role is not None:
        try:
            ModelRole(req.role)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown model role: {req.role}") from None

    try:
        result = await gateway.chat(
            messages=req.messages,
            model=req.model,
            task=task,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            user_id=req.user_id,
            role=req.role,
        )
        out = dict(result)
        if req.role or req.tools:
            # Harness Profile surfaced to the caller (observability + tool-face):
            # which budget/tier ran, and which tools this model should see.
            hp_role = req.role or ModelRole.REASONING
            prof = profile_for(req.model, hp_role)
            out["harness_profile"] = prof.describe()
            if req.tools:
                out["tools_shown"] = filter_tools_for(req.model, hp_role, req.tools)
        return out
    except AllProvidersFailedError as exc:
        # CowAgent: the exhaustion message must reach the caller — it lists
        # every {provider, model, pass, error} tried, not a generic 500.
        raise HTTPException(status_code=502, detail={"error": str(exc), "tried": exc.tried})
    except NoProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/embed")
async def embed(req: EmbedRequest):
    """Route an embedding request through the gateway."""
    _ensure_bootstrapped()
    try:
        embeddings = await gateway.embed(
            texts=req.texts,
            model=req.model,
            user_id=req.user_id,
        )
        return {"embeddings": embeddings, "count": len(embeddings)}
    except AllProvidersFailedError as exc:
        raise HTTPException(status_code=502, detail={"error": str(exc), "tried": exc.tried})
    except NoProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/providers")
async def list_providers():
    """List all registered providers with health status + last chain trace."""
    _ensure_bootstrapped()
    return {
        "providers": gateway.list_providers(),
        "last_chain_trace": gateway.get_chain_trace(),
    }


@router.post("/providers")
async def add_provider(body: ProviderCreate):
    """Register a new provider at runtime（create 面：同名已注册=409 拒，禁静默覆盖）。

    GLAND-②（DEC-㉒ 自决取 A）：注册面=create 语义——同名注册=身份冲突如实 409
    零副作用（既有注册与钥一字不动）；替换=先 DELETE /api/gland/providers/{name}
    （GLAND-① 级联清钥=旧凭据随旧注册撤销）再 POST 新注册——「同名再注册即复用
    旧钥」在注册面构造上不可达（DEC-㉑ 命名的残留面收口）。引擎层
    ModelRouter.add_provider 保持 upsert（内部备胎幂等重注册语义：
    register_local_backup「已注册→幂等返回不重建ProviderConfig」/variant 备胎注册），
    本闸只收外部注册面（冲突判定单一口 has_provider）。
    """
    _ensure_bootstrapped()
    if gateway.has_provider(body.name):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Provider '{body.name}' already registered; "
                f"DELETE /api/gland/providers/{body.name} first (removes its keys) "
                "to replace it"
            ),
        )
    gateway.add_provider(
        name=body.name,
        base_url=body.base_url,
        models=body.models,
        priority=body.priority,
    )
    if body.api_key:
        gateway.key_manager.add_key(body.name, body.api_key)
    return {"status": "ok", "provider": body.name}


@router.delete("/providers/{name}")
async def remove_provider(name: str):
    """Remove a provider and its keys（整 provider 移除级联清钥，GLAND-①）。

    未命中 404 零副作用（不触任何钥）；命中=注册与其钥一并移除，keys_removed 如实
    计数（0=该 provider 本无钥，注册照删）。
    """
    _ensure_bootstrapped()
    keys_removed = gateway.remove_provider(name)
    if keys_removed is None:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return {"status": "ok", "provider": name, "keys_removed": keys_removed}


@router.patch("/providers/{name}")
async def update_provider(name: str, body: ProviderUpdate):
    """就地改 provider 配置（不触钥，GLAND-③ / DEC-㉓）。

    只改给出的字段（整值替换非合并）：base_url/models/priority/enabled；keys 一律
    不动（凭据增删只属 POST /keys、DELETE /keys/{provider}/{index}、DELETE provider
    级联三条显式动作）。就地改面恢复（GLAND-② 诚实边界③ 留档件「PATCH 就地改
    （不触钥）另拍」）：修前改 priority/models 只能 DELETE（GLAND-① 级联清钥）+
    POST（重录钥）=改配置必丢钥。语义（DEC-㉓）：①持钥 provider 改 base_url =
    旧钥打向新端点 → 409 原子拒零写入（凭据端点绑定原则，换端点=先移钥或
    DELETE+POST）；无钥照常改（无钥可误道）②base_url 归一后与现值相同=非改道
    放行③未注册 404 零副作用④空载荷 400（无可改字段，禁空转假成功）。
    """
    _ensure_bootstrapped()
    provided = {k: getattr(body, k) for k in body.model_fields_set}
    if not provided:
        raise HTTPException(status_code=400, detail="No updatable fields provided")
    try:
        cfg = gateway.update_provider(name, provided)
    except ProviderRetargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return {
        "status": "ok",
        "provider": name,
        "updated": [f for f in ("base_url", "models", "priority", "enabled") if f in provided],
        "keys_untouched": len(gateway.key_manager.status().get(name, [])),
    }


@router.post("/keys")
async def add_key(body: KeyAdd):
    """Add an API key for a provider."""
    _ensure_bootstrapped()
    slot = gateway.key_manager.add_key(body.provider, body.api_key)
    return {"status": "ok", "masked": slot.key[:4] + "..." + slot.key[-4:]}


@router.get("/keys")
async def list_keys():
    """List all keys (secrets masked)."""
    _ensure_bootstrapped()
    return gateway.key_manager.status()


@router.delete("/keys/{provider}/{index}")
async def remove_key(provider: str, index: int):
    """Remove a single API key — 单钥移除面（泄漏密钥应急摘除）。

    index = GET /api/gland/keys 返回列表中的位置序号（0 起）。只删该槽位一把钥，
    provider 与其余钥不动（整 provider 移除走 DELETE /providers/{name}，语义分界）。
    未命中（未知 provider / 越界 / 负 index）= 404 如实拒，不谎报 ok。
    """
    _ensure_bootstrapped()
    if not gateway.key_manager.remove_key(provider, index):
        raise HTTPException(
            status_code=404,
            detail=f"Key not found: provider='{provider}' index={index}",
        )
    remaining = len(gateway.key_manager.status().get(provider, []))
    return {"status": "ok", "provider": provider, "index": index, "remaining": remaining}


def _validate_base_url(provider: str) -> str:
    """验证用 base_url 单一取源=provider 注册表（ProviderConfig.base_url）。

    keys 可存在于未注册 provider（load_from_env 的 mimo 等），但验证必须知道打哪个
    /models 端点；无真源=如实 404，禁猜 URL 硬验。
    """
    cfg = gateway.providers.get(provider)
    if cfg is None:
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{provider}' not found: 无 base_url 可验证",
        )
    return cfg.base_url


@router.post("/keys/{provider}/{index}/validate")
async def validate_one_key(provider: str, index: int):
    """单钥有效性验证面——真打 provider /models 验证该槽位 API key 是否可用。

    index = GET /api/gland/keys 返回列表中的位置序号（0 起），与 DELETE 单钥移除面
    同寻址语义。未命中（未知 provider / 越界 / 负 index）= 404 如实拒零副作用，
    不谎报 ok。响应无 status 包装键——``ok`` 即本轮验证结果（True=该钥可用），
    ``is_valid/fail_count/last_checked`` 为验证后的槽位真态（ok 与 is_valid 可不同：
    瞬时网络故障 ok=False 而槽位要累计 3 次失败才标 invalid）。
    """
    _ensure_bootstrapped()
    rows = gateway.key_manager.status().get(provider) or []
    if not (0 <= index < len(rows)):
        raise HTTPException(
            status_code=404,
            detail=f"Key not found: provider='{provider}' index={index}",
        )
    base_url = _validate_base_url(provider)
    ok = await gateway.key_manager.validate_key(provider, base_url, index)
    row = gateway.key_manager.status()[provider][index]
    return {
        "provider": provider,
        "index": index,
        "ok": ok,
        "masked_key": row["masked_key"],
        "is_valid": row["is_valid"],
        "fail_count": row["fail_count"],
        "last_checked": row["last_checked"],
    }


@router.post("/keys/{provider}/validate")
async def validate_provider_keys(provider: str):
    """单 provider 全钥有效性验证面——逐钥真打 /models（validate_all 委托）。

    provider 未注册（无 base_url 真源）= 404 如实拒；已注册但零钥=total 0 如实回
    （幂等：没有可验的钥≠错误）。响应顶层恰 4 键 {provider,total,valid,results}，
    results 逐行恰 6 键按槽位序（=GET /keys 位置序）。
    """
    _ensure_bootstrapped()
    base_url = _validate_base_url(provider)
    outcomes = await gateway.key_manager.validate_all(provider, base_url)
    rows = gateway.key_manager.status().get(provider) or []
    results = [
        {
            "index": i,
            "ok": outcomes[i],
            "masked_key": rows[i]["masked_key"],
            "is_valid": rows[i]["is_valid"],
            "fail_count": rows[i]["fail_count"],
            "last_checked": rows[i]["last_checked"],
        }
        for i in range(len(outcomes))
    ]
    return {
        "provider": provider,
        "total": len(outcomes),
        "valid": sum(1 for o in outcomes if o),
        "results": results,
    }


@router.post("/budget")
async def set_budget(body: BudgetUpdate):
    """Set the token budget limit (0 = unlimited)."""
    _ensure_bootstrapped()
    gateway.token_meter.set_budget(body.limit)
    return {"status": "ok", "budget_limit": body.limit or "unlimited"}


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def gland_stats():
    """Get OpenGland statistics."""
    _ensure_bootstrapped()
    return {
        "status": "ok",
        "component": "OpenGland",
        **gateway.token_meter.summary(),
        "providers": gateway.list_providers(),
        "keys": gateway.key_manager.status(),
        "last_chain_trace": gateway.get_chain_trace(),
    }
