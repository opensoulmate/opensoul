import json
import logging
import os
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from src.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")


@router.get("/health")
async def llm_health():
    """LLM proxy health check."""
    return {"status": "ok", "component": "LLMProxy"}


class LLMRequest(BaseModel):
    messages: list[dict]
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int = 2048
    # F4b-②：stream=true 真流式透传（修前此字段缺失，客户端带 stream:true 被
    # pydantic 静默忽略=想要流式被静默降级，F4 审计④）；缺省 False 时请求体与
    # 既有字节恒等（payload 不带 stream 键，零行为漂移）。
    stream: bool = False


class LLMConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class LLMConfigUpdate(BaseModel):
    provider: str | None = None
    variant: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


_PROFILES_PATH = Path(__file__).parent.parent.parent / "data" / "llm_profiles.json"


def _load_profiles() -> dict:
    if _PROFILES_PATH.exists():
        try:
            return json.loads(_PROFILES_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_profiles(profiles: dict):
    _PROFILES_PATH.parent.mkdir(exist_ok=True)
    _PROFILES_PATH.write_text(json.dumps(profiles, ensure_ascii=False, indent=2))


def _mask_key(key: str) -> str:
    return ("***" + key[-4:]) if key and len(key) > 4 else ("***" if key else "")


def _migrate_env_to_profiles() -> dict:
    """首次：把env现有双槽位配置迁入per-provider profiles（归到mimo名下）"""
    if not _llm_overrides:
        _load_overrides_from_env()
    profiles = _load_profiles()
    if not profiles:
        profiles = {
            "mimo": {
                "standard": {
                    "url": _llm_overrides.get("standard_base_url", "")
                    or _llm_overrides.get("base_url", ""),
                    "api_key": _llm_overrides.get("standard_api_key", "")
                    or _llm_overrides.get("api_key", ""),
                    "model": _llm_overrides.get("standard_model", "")
                    or _llm_overrides.get("model", ""),
                },
                "subscription": {
                    "url": _llm_overrides.get("subscription_base_url", ""),
                    "api_key": _llm_overrides.get("subscription_api_key", ""),
                    "model": _llm_overrides.get("subscription_model", ""),
                },
            }
        }
        _save_profiles(profiles)
    return profiles


class LLMModelsRequest(BaseModel):
    base_url: str
    api_key: str | None = None


@router.post("/models")
async def list_models(body: LLMModelsRequest):
    """List available models from an OpenAI-compatible API endpoint."""
    base_url = body.base_url.rstrip("/")
    api_key = body.api_key or _active_slot("api_key") or settings.llm_api_key

    headers = {"Content-Type": "application/json", **_auth_headers(api_key)}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{base_url}/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            models = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
            models.sort()
            return {"models": models}
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Models API error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch models: {str(e)}")


# In-memory config override, synced to .env on save.
_llm_overrides: dict[str, str] = {}


def _load_overrides_from_env():
    """Load LLM overrides from .env on startup."""
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                _SLOT_MAP = {
                    "LLM_API_KEY": "api_key",
                    "LLM_BASE_URL": "base_url",
                    "LLM_MODEL": "model",
                    "LLM_ACTIVE_VARIANT": "active_variant",
                    "LLM_STANDARD_API_KEY": "standard_api_key",
                    "LLM_STANDARD_BASE_URL": "standard_base_url",
                    "LLM_STANDARD_MODEL": "standard_model",
                    "LLM_SUBSCRIPTION_API_KEY": "subscription_api_key",
                    "LLM_SUBSCRIPTION_BASE_URL": "subscription_base_url",
                    "LLM_SUBSCRIPTION_MODEL": "subscription_model",
                }
                slot = _SLOT_MAP.get(k)
                if slot and v:
                    _llm_overrides[slot] = v


def _sync_acp_proxy_env(url: str, model: str, api_key: str):
    """打通soulmate配置：激活配置同步写入acp-proxy/.env（soulmate agent实际配置源）"""
    acp_env = os.environ.get(
        "ACP_PROXY_ENV",
        os.path.join(
            os.environ.get("OPENMATE_ROOT", "/home/climbing/openmate"), "acp-proxy", ".env"
        ),
    )
    try:
        lines = open(acp_env).read().splitlines() if os.path.exists(acp_env) else []
        updates = {"LLM_BASE_URL": url, "LLM_MODEL": model, "LLM_API_KEY": api_key}
        out, seen = [], set()
        for line in lines:
            k = line.split("=")[0].strip() if "=" in line else ""
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
            else:
                out.append(line)
        for k, v in updates.items():
            if k not in seen and v:
                out.append(f"{k}={v}")
        with open(acp_env, "w") as f:
            f.write("\n".join(out) + "\n")
    except Exception:
        pass


def _save_overrides_to_env():
    """Persist LLM overrides to .env file."""
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        lines = f.readlines()

    keys_to_save = {
        "LLM_API_KEY": "api_key",
        "LLM_BASE_URL": "base_url",
        "LLM_MODEL": "model",
        "LLM_ACTIVE_VARIANT": "active_variant",
        "LLM_STANDARD_API_KEY": "standard_api_key",
        "LLM_STANDARD_BASE_URL": "standard_base_url",
        "LLM_STANDARD_MODEL": "standard_model",
        "LLM_SUBSCRIPTION_API_KEY": "subscription_api_key",
        "LLM_SUBSCRIPTION_BASE_URL": "subscription_base_url",
        "LLM_SUBSCRIPTION_MODEL": "subscription_model",
    }
    updated_keys = set()

    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in keys_to_save:
                override_key = keys_to_save[k]
                val = _llm_overrides.get(override_key, "")
                new_lines.append(f"{k}={val}\n")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    # Append any keys not already in .env
    for env_key, override_key in keys_to_save.items():
        if env_key not in updated_keys and override_key in _llm_overrides:
            new_lines.append(f"{env_key}={_llm_overrides[override_key]}\n")

    with open(ENV_PATH, "w") as f:
        f.writelines(new_lines)


# Load on module import
_load_overrides_from_env()


def _active_variant() -> str:
    return _llm_overrides.get("active_variant", "standard") or "standard"


def _active_slot(field: str) -> str:
    prefix = f"{_active_variant()}_"
    return _llm_overrides.get(prefix + field) or _llm_overrides.get(field, "")


def alternate_variant_config() -> dict | None:
    """激活变体之外的另一个已配置变体（fallback链真实备胎）。

    双体系（标准API/订阅制）两套base_url+api_key并存：激活变体端点级故障
    （402余额耗尽/宕机）时，另一个变体是现成的真实备胎——2026-09-25 live实证：
    standard=low_balance(402) 时 subscription(token-plan) 仍然 ok，但此前
    备胎从未进过 ModelRouter 的fallback链（链上只有激活端点+死掉的ollama），
    dream/Phase1 全链饿死。

    返回 {"variant", "base_url", "api_key", "model"} 或 None（无备胎/同端点）。
    fail-safe：任何异常→None（配置解析绝不反噬调用方）。
    """
    try:
        active = _active_variant()
        other = "subscription" if active == "standard" else "standard"
        url = _llm_overrides.get(f"{other}_base_url", "")
        key = _llm_overrides.get(f"{other}_api_key", "")
        model = _llm_overrides.get(f"{other}_model", "")
        if not url or not key:
            return None
        active_url = _llm_overrides.get("base_url", "") or settings.llm_base_url
        if str(url).rstrip("/") == str(active_url or "").rstrip("/"):
            return None  # 同端点=非备胎（避免自我failover空转）
        return {"variant": other, "base_url": url, "api_key": key, "model": model}
    except Exception:  # noqa: BLE001 — 配置解析绝不反噬
        return None


def register_variant_backup(router, fallback_model: str) -> str | None:
    """把变体备胎注册进任意ModelRouter的fallback链（priority=5）——注册块单一真源。

    此前memory_model._build_router / api/gland._ensure_bootstrapped /
    trajectory/branch_summary._call_llm_router三处各自手搓provider注册块，第三处
    （branch_summary）漏掉变体备胎：primary额度耗尽（402）时branch摘要直接降级
    extractive-fallback，与dream/Phase1修复前同款饿死形态）。收敛后三处同规：
    primary(p0) → 变体备胎(p5) → ollama本地兜底(p10，register_local_backup探活
    豁免——不可达不入链)（CowAgent有序降级链语义）。

    router需有add_provider()与key_manager（ModelRouter及其gateway同构实例）。
    返回注册的provider名（"variant-<name>"）或None（无备胎/同端点/解析失败）。
    fail-safe：任何异常→None绝不反噬调用方。
    """
    try:
        alt = alternate_variant_config()
    except Exception:  # noqa: BLE001 — 备胎注册绝不反噬调用方
        alt = None
    if not alt:
        return None
    name = f"variant-{alt['variant']}"
    router.add_provider(
        name=name,
        base_url=alt["base_url"],
        models={"chat": alt.get("model") or fallback_model},
        priority=5,
    )
    if alt.get("api_key"):
        router.key_manager.add_key(name, alt["api_key"])
    return name


# ── 本地兜底备胎：探活入链（litellm health_check「探活结果喂路由决策」最小切片）──

LOCAL_PROBE_TTL = 30.0  # 探活结果缓存秒数（health_state_cache语义：定期探活非逐调用探活）
_LOCAL_PROBE_CACHE: dict[str, tuple[float, bool]] = {}


def _tcp_reachable(base_url: str, timeout: float = 0.35) -> bool:
    """TCP connect探活：端口可建连=服务在（Ollama等本地OpenAI兼容端点最低共同面）。

    litellm health_check.py「全provider探活，结果喂路由决策」的最小切片——探活
    结果只影响链构建（可达才入链），不影响运行期failover/cooldown既有语义。
    fail-safe：任何异常→False（探活失败按不可达处理，宁可少一个备胎也不放幻影）。
    """
    try:
        url = base_url if "://" in base_url else "http://" + base_url
        u = urlparse(url)
        host = u.hostname or "localhost"
        port = u.port or (443 if u.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001 — 探活绝不反噬调用方
        return False


def _probe_cached(base_url: str, timeout: float = 0.35) -> tuple[bool, bool]:
    """带TTL缓存的探活——fresh-per-call router（memory_model每次调用新建router）
    防止逐调用探活开销；TTL过期后重探，ollama后启动最迟TTL秒内回到链上。
    返回 (可达, 是否新探)——fresh供豁免日志去重（每TTL窗口记一次，防请求路径
    逐请求刷屏）。"""
    now = time.monotonic()
    hit = _LOCAL_PROBE_CACHE.get(base_url)
    if hit is not None and now - hit[0] < LOCAL_PROBE_TTL:
        return hit[1], False
    ok = _tcp_reachable(base_url, timeout=timeout)
    _LOCAL_PROBE_CACHE[base_url] = (now, ok)
    return ok, True


def register_local_backup(
    router,
    base_url: str,
    models: dict,
    *,
    name: str = "ollama",
    priority: int = 10,
    probe: bool = True,
) -> str | None:
    """本地兜底备胎入链（priority=10）单一真源 + 探活豁免。

    此前memory_model._build_router / api/gland._ensure_bootstrapped /
    trajectory/branch_summary._call_llm_router三处各自手搓ollama注册块且无条件
    入链：ollama没在跑时链尾永远挂着幻影备胎，全链失败时白打一次ConnectError
    （01:35遗留#2 / 08:48遗留#3同款；fresh-per-call router让router自身cooldown
    机制跨调用失效，幻影每次都在）。收敛后语义：

    - 探活可达→照常注册（CowAgent有序降级链语义不变：primary(p0)→变体备胎(p5)
      →本地兜底(p10)）
    - 不可达→不入链（链上只有真实可用候选，失败必须是真失败不是ConnectError噪音
      ——evolution-engine-patterns mem0 §1.1「失败必须可见」的噪音面收口）
    - 已注册→幂等返回（gateway单例请求路径可安全重复调用）

    probe=False跳过探活强制注册（测试/确定性场景）。
    返回注册的provider名或None（不可达/探活失败/异常）。
    fail-safe：任何异常→None绝不反噬调用方。
    """
    try:
        if name in getattr(router, "providers", {}):
            return name  # 幂等：重复注册不重建ProviderConfig（不清failure计数）
        if probe:
            ok, fresh = _probe_cached(base_url)
            if not ok:
                if fresh:
                    # warning级=qdrant「unreachable—功能降级」同款可见约定（root无
                    # handler时lastResort只放行WARNING+，info会被吞——探活豁免必须
                    # 可见不可静默）；仅新探时记（每TTL窗口一次，防逐请求刷屏）
                    logger.warning(
                        "local backup %s unreachable (%s) — skipped from fallback chain",
                        name,
                        base_url,
                    )
                return None
        router.add_provider(name=name, base_url=base_url, models=models, priority=priority)
        return name
    except Exception:  # noqa: BLE001 — 备胎注册绝不反噬调用方
        return None


def _mask(k: str, masked: bool) -> str:
    return ("***" if masked and k else k) if k else ""


def _get_config(masked: bool = False) -> dict:
    profiles = _migrate_env_to_profiles()
    masked_profiles = {}
    for pid, variants in profiles.items():
        masked_profiles[pid] = {}
        for vid, prof in variants.items():
            key = prof.get("api_key", "")
            masked_profiles[pid][vid] = {
                "url": prof.get("url", ""),
                "api_key": _mask_key(key),
                "has_key": bool(key),
                "model": prof.get("model", ""),
            }
    active_variant = _active_variant()
    main_key = _llm_overrides.get("api_key", settings.llm_api_key)
    return {
        "profiles": masked_profiles,
        "active_variant": active_variant,
        "base_url": _llm_overrides.get("base_url", settings.llm_base_url),
        "api_key": _mask(main_key, masked),
        "model": _llm_overrides.get("model", settings.llm_model),
    }


@router.get("/config")
async def get_config(masked: bool = False):
    """Get current LLM configuration. Default: real key for admin UI."""
    return _get_config(masked=masked)


@router.post("/config")
async def save_config(data: LLMConfigUpdate):
    """Save per-provider×variant config to profiles JSON + sync active to .env."""
    # 空请求体幂等：不做任何变更，不清空现有配置
    if all(
        getattr(data, f, None) is None
        for f in ("provider", "variant", "base_url", "api_key", "model")
    ):
        return await get_config()
    profiles = _migrate_env_to_profiles()
    provider = (data.provider or "mimo").strip() or "mimo"
    variant = (data.variant or "standard").strip() or "standard"
    if variant not in ("standard", "subscription"):
        variant = "standard"
    if provider not in profiles:
        profiles[provider] = {}
    prof = dict(profiles[provider].get(variant, {}))
    if data.base_url is not None:
        prof["url"] = data.base_url
    if data.api_key is not None and data.api_key and not data.api_key.startswith("***"):
        prof["api_key"] = data.api_key
    if data.model is not None:
        prof["model"] = data.model
    if not prof.get("url"):
        for v in PRESET_VARIANTS.get(provider, []):
            if v["id"] == variant and v.get("base_url"):
                prof["url"] = v["base_url"]
    profiles[provider][variant] = prof
    _save_profiles(profiles)
    # 同步overrides：profile槽位字段为空时保留现有生效值，不清空
    cur_url = _llm_overrides.get("base_url", settings.llm_base_url)
    cur_key = _llm_overrides.get("api_key", settings.llm_api_key)
    cur_model = _llm_overrides.get("model", settings.llm_model)
    eff_url = prof.get("url", "") or cur_url
    eff_key = prof.get("api_key", "") or cur_key
    eff_model = prof.get("model", "") or cur_model
    _llm_overrides[f"{variant}_base_url"] = eff_url
    _llm_overrides[f"{variant}_api_key"] = eff_key
    _llm_overrides[f"{variant}_model"] = eff_model
    _llm_overrides["base_url"] = eff_url
    _llm_overrides["api_key"] = eff_key
    _llm_overrides["model"] = eff_model
    _llm_overrides["active_variant"] = variant
    _save_overrides_to_env()
    # 打通soulmate：激活配置同步到acp-proxy/.env（仅用生效值，空值不清.env）
    _sync_acp_proxy_env(eff_url, eff_model, eff_key)
    return await get_config()


class LLMTestRequest(BaseModel):
    """Optional overrides for test — lets the frontend test unsaved settings."""

    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


# ── 大模型预设双体系（标准API / Token Plan）状态指示灯 ──────────
def _std(url: str, prefix: str = "sk-") -> dict:
    return {"id": "standard", "label": "标准API", "base_url": url, "key_prefix": prefix}


def _sub(url: str = "", prefix: str = "") -> dict:
    return {"id": "subscription", "label": "订阅制", "base_url": url, "key_prefix": prefix}


# 所有云端大模型统一双体系：标准API + 订阅制（订阅地址为空=用户自填；小米订阅制=官方Token Plan地址）
PRESET_VARIANTS: dict[str, list[dict]] = {
    "mimo": [
        _std("https://api.xiaomimimo.com/v1"),
        _sub("https://token-plan-cn.xiaomimimo.com/v1", "tp-"),
    ],
    "openai": [_std("https://api.openai.com/v1"), _sub()],
    "claude": [_std("https://api.anthropic.com/v1", "sk-ant-"), _sub()],
    "gemini": [_std("https://generativelanguage.googleapis.com/v1beta", "AIza"), _sub()],
    "deepseek": [_std("https://api.deepseek.com/v1"), _sub()],
    "qwen": [_std("https://dashscope.aliyuncs.com/compatible-mode/v1"), _sub()],
    "zhipu": [_std("https://open.bigmodel.cn/api/paas/v4", ""), _sub()],
    "moonshot": [_std("https://api.moonshot.cn/v1"), _sub()],
    "baichuan": [_std("https://api.baichuan-ai.com/v1"), _sub()],
    "yi": [_std("https://api.lingyiwanwu.com/v1"), _sub()],
    "minimax": [_std("https://api.minimax.chat/v1", "eyJ"), _sub()],
    "stepfun": [_std("https://api.stepfun.com/v1"), _sub()],
    "doubao": [_std("https://ark.cn-beijing.volces.com/api/v3", ""), _sub()],
    "ollama": [
        {
            "id": "standard",
            "label": "本地",
            "base_url": "http://localhost:11434/v1",
            "key_prefix": "",
        }
    ],
    "lmstudio": [
        {
            "id": "standard",
            "label": "本地",
            "base_url": "http://localhost:1234/v1",
            "key_prefix": "",
        }
    ],
    "vllm": [
        {
            "id": "standard",
            "label": "本地",
            "base_url": "http://localhost:8000/v1",
            "key_prefix": "",
        }
    ],
    "custom": [_std("", ""), _sub("", "")],
}


def _auth_headers(api_key: str | None) -> dict:
    """Provider auth headers（与 gland/router.py `_auth_headers` 同一约定，收敛手搓）。

    keyless（本地 Ollama/LM Studio）不带认证头；``tp-`` 订阅制 key 走 ``api-key``
    头（token-plan 约定，live 200 实证）；其余 ``Authorization: Bearer``。
    """
    if not api_key:
        return {}
    if api_key.startswith("tp-"):
        return {"api-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


def _probe_client(timeout: float) -> httpx.AsyncClient:
    """探测用 HTTP client 工厂（测试 seam：tests monkeypatch 换 MockTransport）。"""
    return httpx.AsyncClient(timeout=timeout)


async def _probe_variant(base_url: str, api_key: str, model: str = "") -> str:
    """探测API端点可用性→全量状态分类（禁弱断言：探针响应状态必须逐类判定）。

    状态：ok / unsupported_model / low_balance / invalid_key / unreachable /
    unknown / not_configured / error_<code>。

    2026-09-25 实锤的弱断言缺陷（dev-report遗留#3）：chat探针只判402，400
    「Unsupported model」和401都被误判成ok（partial-test占位符配置的绿灯是假绿，
    差点误导fallback链备胎判断）。现在探针200才算ok：
    - 400/404/422 → unsupported_model（模型名不被服务，端点本身可能好）
    - 401/403 → invalid_key
    - 402 → low_balance
    - 其余 → error_<code>
    - 探针异常（/models已通但chat超时/断连）→ unknown（不许冒充ok）

    model为空时用/models列表首个模型做chat探针（绿灯必须是真实chat调用成功）；
    列表不可解析→unknown（无法验证真实可用性，不冒充ok）。
    """
    if not api_key or not base_url:
        return "not_configured"
    root = base_url.rstrip("/")
    try:
        async with _probe_client(8.0) as client:
            resp = await client.get(f"{root}/models", headers=_auth_headers(api_key))
    except Exception:
        return "unreachable"
    if resp.status_code in (401, 403):
        return "invalid_key"
    if resp.status_code == 402:
        return "low_balance"
    if resp.status_code != 200:
        return f"error_{resp.status_code}"
    probe_model = model
    if not probe_model:
        try:
            entries = resp.json().get("data") or []
            probe_model = entries[0].get("id", "") if isinstance(entries[0], dict) else ""
        except Exception:
            probe_model = ""
    if not probe_model:
        return "unknown"
    try:
        async with _probe_client(10.0) as client:
            probe = await client.post(
                f"{root}/chat/completions",
                headers={"Content-Type": "application/json", **_auth_headers(api_key)},
                json={
                    "model": probe_model,
                    "messages": [{"role": "user", "content": "."}],
                    "max_tokens": 1,
                },
            )
    except Exception:
        return "unknown"
    if probe.status_code == 200:
        return "ok"
    if probe.status_code in (401, 403):
        return "invalid_key"
    if probe.status_code == 402:
        return "low_balance"
    if probe.status_code in (400, 404, 422):
        return "unsupported_model"
    return f"error_{probe.status_code}"


@router.get("/presets/status")
async def presets_status():
    """每个provider×每个API体系独立探测（状态全量分类，见 _probe_variant）：
    ok=真实chat可用 / unsupported_model=模型不支持 / low_balance=余额不足 /
    invalid_key=Key无效 / unreachable=不可达 / unknown=未确认 / not_configured=未配置
    """
    profiles = _migrate_env_to_profiles()
    presets: dict[str, dict] = {}
    for pid, variants in PRESET_VARIANTS.items():
        presets[pid] = {}
        for v in variants:
            prof = profiles.get(pid, {}).get(v["id"], {})
            url = prof.get("url", "")
            key = prof.get("api_key", "")
            model = prof.get("model", "")
            status = "not_configured"
            if key and url:
                status = await _probe_variant(url, key, model)
            presets[pid][v["id"]] = {
                "status": status,
                "label": v["label"],
                "base_url": url,
                "key_prefix": v.get("key_prefix", ""),
            }
    return {
        "active_variant": _active_variant(),
        "presets": presets,
    }


@router.post("/test")
async def test_connection(body: LLMTestRequest | None = None):
    """Test LLM connection with a simple prompt.
    Accepts optional overrides so the frontend can test settings
    the user has edited but not yet saved.
    """
    api_key = (
        (body.api_key if body and body.api_key else None)
        or _active_slot("api_key")
        or settings.llm_api_key
    )
    base_url = (
        (body.base_url if body and body.base_url else None)
        or _active_slot("base_url")
        or settings.llm_base_url
    )
    model = (
        (body.model if body and body.model else None) or _active_slot("model") or settings.llm_model
    )

    import logging

    logging.getLogger("llm-test").warning(
        "TEST REQUEST: body=%s, resolved: base_url=%s, model=%s, key_len=%d, key_source=%s",
        body.model_dump() if body else None,
        base_url,
        model,
        len(api_key) if api_key else 0,
        "body" if (body and body.api_key) else "override/env",
    )

    if not api_key:
        raise HTTPException(status_code=400, detail="LLM API key not configured")

    # Mask key for display
    masked_key = api_key[:8] + "***" + api_key[-4:] if len(api_key) > 12 else "***"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    **_auth_headers(api_key),
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Say 'pong' and nothing else."}],
                    "max_tokens": 16,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]
            return {
                "status": "ok",
                "model": model,
                "base_url": base_url,
                "key_preview": masked_key,
                "reply": reply.strip(),
            }
    except httpx.HTTPStatusError as e:
        import logging

        logging.getLogger("llm-test").warning(
            "LLM API error %d: %s", e.response.status_code, e.response.text[:300]
        )
        raise HTTPException(
            status_code=502,
            detail=f"LLM API error: {e.response.status_code} — {e.response.text[:200]}",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM connection failed: {str(e)}")


@router.post("/completions")
async def completions(req: LLMRequest):
    # 双体系槽位解析：variant槽位优先，回退legacy槽位，再回退settings
    api_key = _active_slot("api_key") or settings.llm_api_key
    if not api_key:
        raise HTTPException(status_code=400, detail="LLM API key not configured")

    base_url = _active_slot("base_url") or settings.llm_base_url
    model = req.model or _active_slot("model") or settings.llm_model

    # 失败必须typed可见（mem0 §1.1）：上游错误→502，超时→504，不可达→502；
    # 禁止裸raise导致ASGI 500纯文本（此前connect失败直接500，客户端无法区分原因）
    if req.stream:
        # F4b-②：stream=true 真流式透传（StreamingResponse），非流式路径字节零变化
        return await _completions_stream(base_url, api_key, model, req)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    **_auth_headers(api_key),
                },
                json={
                    "model": model,
                    "messages": req.messages,
                    "temperature": req.temperature,
                    "max_tokens": req.max_tokens,
                },
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM API error: {e.response.status_code} — {e.response.text[:200]}",
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="LLM request timed out (120s)")
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502, detail=f"LLM upstream unreachable: {type(e).__name__}: {e}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"LLM completion failed: {type(e).__name__}: {e}"
        )


async def _completions_stream(base_url: str, api_key: str, model: str, req: LLMRequest):
    """F4b-②：stream=true 真流式透传（生态通用 LLM 直通 API，透传代理语义）。

    - 上游回 text/event-stream → StreamingResponse 逐块转发上游原始帧（字节级
      透传不改写不聚合——直通 API 的契约就是上游真实响应，逐 token 交付由此达成）
    - 上游忽略 stream 回 JSON（真实存在）→ 按真实形态整段回，content-type 如实
      告诉客户端（不硬造 SSE 帧把整段响应伪装成流式）
    - 其余形态 → 原样透传带上游真实 content-type（透传代理不猜形态不硬造）
    首字节前的失败沿用与非流式路径同款 typed 映射（502/504）；流开始后中途断联
    无法再改状态码，连接由客户端侧感知（如实边界）。
    """
    client = httpx.AsyncClient(timeout=120)
    try:
        upstream_req = client.build_request(
            "POST",
            f"{base_url}/chat/completions",
            headers={
                "Content-Type": "application/json",
                **_auth_headers(api_key),
            },
            json={
                "model": model,
                "messages": req.messages,
                "temperature": req.temperature,
                "max_tokens": req.max_tokens,
                "stream": True,
            },
        )
        resp = await client.send(upstream_req, stream=True)
    except httpx.TimeoutException:
        await client.aclose()
        raise HTTPException(status_code=504, detail="LLM request timed out (120s)")
    except httpx.RequestError as e:
        await client.aclose()
        raise HTTPException(
            status_code=502, detail=f"LLM upstream unreachable: {type(e).__name__}: {e}"
        )
    except Exception as e:
        await client.aclose()
        raise HTTPException(
            status_code=502, detail=f"LLM completion failed: {type(e).__name__}: {e}"
        )

    if resp.status_code >= 400:
        try:
            err_body = (await resp.aread()).decode("utf-8", "replace")[:200]
        finally:
            await resp.aclose()
            await client.aclose()
        raise HTTPException(
            status_code=502,
            detail=f"LLM API error: {resp.status_code} — {err_body}",
        )

    content_type = resp.headers.get("content-type", "")

    if "text/event-stream" in content_type:

        async def _sse_relay():
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(_sse_relay(), media_type="text/event-stream")

    body = await resp.aread()
    await resp.aclose()
    await client.aclose()
    if "application/json" in content_type:
        return json.loads(body)
    return Response(content=body, media_type=content_type or "application/octet-stream")
