import asyncio
import logging

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 256
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0
# 单次尝试超时：embedding请求不允许长时间挂起（此前60s×3次重试≈3分钟挂死调用方）
EMBEDDING_ATTEMPT_TIMEOUT = 15.0
# 整批调用的总墙钟预算：超时即降级返回空向量+可见日志，调用方永不挂死
EMBEDDING_TOTAL_BUDGET = 12.0
# 本地 provider（MEM-②）：CPU 推理慢于托管 API，批量语义回填的墙钟预算独立
# 如实标注（不共享托管口径 12s——本地模型批量 embed 超预算=假降级）。
OLLAMA_TOTAL_BUDGET = 60.0
# provider 单一源（MEM-②）："openai"=OpenAI 兼容 /embeddings（既有路径字节零
# 变化）；"ollama"=本地开源 embedding 模型（POST /api/embed）。未知 provider
# 如实抛 ValueError（禁静默当 openai 硬打错误端点）。
SUPPORTED_PROVIDERS = ("openai", "ollama")


def _provider() -> str:
    raw = settings.embedding_provider if settings.embedding_provider else "openai"
    p = raw.strip().lower()
    if p not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"unsupported embedding provider: {settings.embedding_provider!r} "
            f"(supported: {', '.join(SUPPORTED_PROVIDERS)})"
        )
    return p


def _total_budget() -> float:
    """整批调用总墙钟预算：按 provider 单一源分口径。"""
    return OLLAMA_TOTAL_BUDGET if _provider() == "ollama" else EMBEDDING_TOTAL_BUDGET


def _get_api_key() -> str:
    return settings.embedding_api_key or settings.llm_api_key


async def _call_embedding_api(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    """Call the embedding API with retry logic."""
    api_key = _get_api_key()
    url = f"{settings.embedding_base_url}/embeddings"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "input": texts,
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
    }

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.post(
                url, headers=headers, json=payload, timeout=EMBEDDING_ATTEMPT_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Embedding API error (attempt %d/%d): %s, retrying in %.1fs",
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

    raise RuntimeError(f"Embedding API failed after {MAX_RETRIES} retries: {last_exc}")


async def _call_ollama_api(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    """本地开源 embedding 模型（Ollama POST /api/embed）带重试。

    失败契约与 openai 路径同族：重试耗尽 raise（get_embedding→调用方 degraded/
    503；get_embeddings_batch→空向量降级）。响应形状不符（embeddings 缺失/条数
    不齐）如实抛禁 fabricate 向量、禁静默截断对齐。
    """
    url = f"{settings.embedding_base_url.rstrip('/')}/api/embed"
    payload = {"model": settings.embedding_model, "input": texts}

    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.post(url, json=payload, timeout=EMBEDDING_ATTEMPT_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings")
            if not isinstance(embeddings, list) or len(embeddings) != len(texts):
                got = len(embeddings) if isinstance(embeddings, list) else type(embeddings).__name__
                raise RuntimeError(
                    f"Ollama /api/embed unexpected response: embeddings={got} for {len(texts)} texts"
                )
            return [list(v) for v in embeddings]
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "Ollama embedding API error (attempt %d/%d): %s, retrying in %.1fs",
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

    raise RuntimeError(f"Ollama embedding API failed after {MAX_RETRIES} retries: {last_exc}")


async def _call_embed(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    """provider 单一派发口（声明从实现派生：真走哪个上游由配置唯一决定）。"""
    if _provider() == "ollama":
        return await _call_ollama_api(client, texts)
    return await _call_embedding_api(client, texts)


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector for a single text (bounded by provider total budget)."""
    budget = _total_budget()  # 先取预算（未知 provider 如实抛在建协程之前）
    async with httpx.AsyncClient() as client:
        results = await asyncio.wait_for(_call_embed(client, [text]), timeout=budget)
        return results[0]


async def _embed_all(texts: list[str]) -> list[list[float]]:
    """Call embedding API for all batches. Raises on failure (caller handles)."""
    all_embeddings: list[list[float]] = []
    async with httpx.AsyncClient() as client:
        for i in range(0, len(texts), MAX_BATCH_SIZE):
            batch = texts[i : i + MAX_BATCH_SIZE]
            batch_embeddings = await _call_embed(client, batch)
            all_embeddings.extend(batch_embeddings)
    return all_embeddings


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Get embedding vectors for multiple texts.

    失败契约（mem0 §1.1"失败可见，禁止静默降级"）：
    - 永不raise、永不挂死——超时/异常一律降级为空向量；
    - 降级必须写warning日志（可见），调用方（knowledge入库/搜索）继续工作，
      向量缺失由调用方跳过qdrant写入，meilisearch关键词索引兜底。
    """
    if not texts:
        return [[] for _ in texts]
    try:
        provider = _provider()
    except ValueError as exc:
        # 未知 provider：批量契约永不 raise——降级空向量+可见日志禁静默
        logger.warning("Embedding batch degraded to empty vectors: %s", exc)
        return [[] for _ in texts]
    if provider == "openai" and not _get_api_key():
        return [[] for _ in texts]
    budget = _total_budget()
    try:
        return await asyncio.wait_for(_embed_all(texts), timeout=budget)
    except TimeoutError:
        logger.warning(
            "Embedding batch timed out after %.0fs (%d texts) — degraded to empty vectors, "
            "vector index skipped, keyword index unaffected",
            budget,
            len(texts),
        )
        return [[] for _ in texts]
    except Exception as exc:
        logger.warning(
            "Embedding batch failed (%d texts): %s — degraded to empty vectors",
            len(texts),
            exc,
        )
        return [[] for _ in texts]
