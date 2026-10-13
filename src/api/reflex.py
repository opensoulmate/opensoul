"""OpenReflex API — 条件反射：高频问答缓存、快速应答。"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.reflex.cache import ReflexCache

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
cache = ReflexCache(max_entries=5000, similarity_threshold=0.80)


# ── Request Schemas ────────────────────────────────────────


class CachePutRequest(BaseModel):
    query: str
    response: str
    category: str = ""
    tags: list[str] = []
    importance: float = 0.5
    ttl_seconds: float | None = None
    source: str = "manual"


class CacheLookupRequest(BaseModel):
    query: str


class CacheUpdateRequest(BaseModel):
    response: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    importance: float | None = None


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def reflex_stats():
    """Get OpenReflex statistics."""
    return {
        "status": "ok",
        "component": "OpenReflex",
        "cache": cache.get_stats(),
    }


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def health():
    """OpenReflex health check."""
    return {
        "status": "ok",
        "component": "OpenReflex",
        "cache": cache.get_stats(),
    }


# ── Cache Operations ──────────────────────────────────────


@router.post("/cache")
async def put_cache(req: CachePutRequest):
    """Add or update a cached response."""
    entry = cache.put(
        query=req.query,
        response=req.response,
        category=req.category,
        tags=req.tags,
        importance=req.importance,
        ttl_seconds=req.ttl_seconds,
        source=req.source,
    )
    return {
        "entry_id": entry.entry_id,
        "query": entry.query,
        "created_at": entry.created_at,
    }


@router.post("/lookup")
async def lookup_cache(req: CacheLookupRequest):
    """Look up a cached response using fuzzy matching."""
    entry = cache.lookup(req.query)
    if not entry:
        return {"hit": False, "query": req.query}
    return {
        "hit": True,
        "entry_id": entry.entry_id,
        "query": entry.query,
        "response": entry.response,
        "category": entry.category,
        "hit_count": entry.hit_count,
        "importance": entry.importance,
    }


@router.get("/cache")
async def list_cache(
    category: str = Query(default=None),
    tag: str = Query(default=None),
    min_hits: int = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    """List cached entries."""
    entries = cache.list_entries(category=category, tag=tag, min_hits=min_hits, limit=limit)
    return {
        "entries": [
            {
                "entry_id": e.entry_id,
                "query": e.query,
                "response": e.response,
                "category": e.category,
                "tags": e.tags,
                "hit_count": e.hit_count,
                "importance": e.importance,
                "source": e.source,
                "created_at": e.created_at,
                "last_hit_at": e.last_hit_at,
                "ttl_seconds": e.ttl_seconds,
            }
            for e in entries
        ],
        "total": len(entries),
    }


@router.get("/cache/{entry_id}")
async def get_cache_entry(entry_id: str):
    """Get a specific cache entry."""
    entry = cache.get(entry_id)
    if not entry:
        raise HTTPException(404, "Entry not found")
    return {
        "entry_id": entry.entry_id,
        "query": entry.query,
        "response": entry.response,
        "category": entry.category,
        "tags": entry.tags,
        "hit_count": entry.hit_count,
        "importance": entry.importance,
        "source": entry.source,
        "created_at": entry.created_at,
        "last_hit_at": entry.last_hit_at,
        "ttl_seconds": entry.ttl_seconds,
    }


@router.patch("/cache/{entry_id}")
async def update_cache_entry(entry_id: str, req: CacheUpdateRequest):
    """Update a cache entry."""
    entry = cache.get(entry_id)
    if not entry:
        raise HTTPException(404, "Entry not found")
    if req.response is not None:
        entry.response = req.response
    if req.category is not None:
        entry.category = req.category
    if req.tags is not None:
        entry.tags = req.tags
    if req.importance is not None:
        entry.importance = req.importance
    return {"entry_id": entry_id, "updated": True}


@router.delete("/cache/{entry_id}")
async def delete_cache_entry(entry_id: str):
    """Delete a cache entry."""
    if not cache.delete(entry_id):
        raise HTTPException(404, "Entry not found")
    return {"deleted": True}


@router.post("/cache/clear")
async def clear_cache_entries():
    """Clear ALL cache entries (load relief; /cleanup only drops expired)."""
    return cache.clear()


@router.post("/cleanup")
async def cleanup_cache():
    """Remove expired entries."""
    return cache.cleanup()


# ── Config ─────────────────────────────────────────────────


@router.get("/config")
async def get_config():
    """Get current cache configuration."""
    return {
        "max_entries": cache.max_entries,
        "similarity_threshold": cache.similarity_threshold,
        "default_ttl_seconds": cache.default_ttl,
    }


@router.put("/config")
async def update_config(
    max_entries: int | None = None,
    similarity_threshold: float | None = None,
    default_ttl: float | None = None,
):
    """Update cache configuration."""
    if max_entries is not None:
        cache.max_entries = max_entries
    if similarity_threshold is not None:
        cache.similarity_threshold = max(0.0, min(1.0, similarity_threshold))
    if default_ttl is not None:
        cache.default_ttl = default_ttl
    return await get_config()
