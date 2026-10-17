from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from src.api._errors import stats_error
from src.database.postgres import db_pool
from src.models.entity import GraphData, RelationCreate, RelationResponse
from src.services.entity import get_entity_with_relations, list_entities
from src.services.graph import (
    create_relation,
    find_path,
    get_entity_neighbors,
    get_graph,
    list_relations,
)

router = APIRouter()


@router.get("/health")
async def graph_health():
    """OpenGraph health check."""
    return {"status": "ok", "component": "OpenGraph", "module": "knowledge-graph"}


@router.get("/stats")
async def graph_stats():
    """Get knowledge graph statistics."""
    try:
        entities = await db_pool.fetchval("SELECT COUNT(*) FROM entities") or 0
        relations = await db_pool.fetchval("SELECT COUNT(*) FROM relations") or 0
        by_type = await db_pool.fetch(
            # C2根因修复：entities正典列是type，原SQL误写entity_type必炸（同entity.py）
            "SELECT type, COUNT(*) as cnt FROM entities GROUP BY type ORDER BY cnt DESC LIMIT 10"
        )
        return {
            "status": "ok",
            "component": "OpenGraph",
            "total_entities": entities,
            "total_relations": relations,
            "by_type": {r["type"]: r["cnt"] for r in (by_type or [])},
        }
    except Exception as exc:
        # C2统一错误响应：失败说失败、说原因，禁谎报ok（假0冒充"没数据"）
        return stats_error("OpenGraph", exc, total_entities=0, total_relations=0, by_type={})


def _resolve_user_id(user_id: str) -> UUID:
    """Convert user_id to UUID — accept both UUID strings and plain usernames."""
    try:
        return UUID(user_id)
    except ValueError:
        import hashlib

        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


@router.get("/entities", response_model=list)
async def get_entities(
    user_id: str = Query("default", description="User ID (UUID or username)"),
    entity_type: str | None = Query(None, description="Filter by entity type"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Entity list, optionally filtered by type."""
    uid = _resolve_user_id(user_id)
    return await list_entities(uid, entity_type, offset, limit)


@router.get("/entities/{entity_id}")
async def get_entity_detail(entity_id: UUID, user_id: str = Query("default")):
    """Entity detail with its relations."""
    uid = _resolve_user_id(user_id)
    entity = await get_entity_with_relations(entity_id, uid)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@router.get("/entities/{entity_id}/neighbors")
async def get_neighbors(
    entity_id: UUID,
    user_id: str = Query("default", description="User ID (UUID or username)"),
):
    """Entity neighbors: directly connected entities + relation types/direction.

    H1-②b-⑦a 查询面出口（get_entity_neighbors 首个 API 消费点，修前零出口）。
    """
    uid = _resolve_user_id(user_id)
    result = await get_entity_neighbors(uid, entity_id)
    if result["entity"] is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return result


@router.get("/paths")
async def get_paths(
    source_id: UUID,
    target_id: UUID,
    user_id: str = Query("default", description="User ID (UUID or username)"),
    max_depth: int = Query(5, ge=1, le=10, description="Max path length in nodes"),
):
    """All paths between two entities (BFS). H1-②b-⑦a 查询面出口（find_path 首个
    API 消费点，修前零出口）。两端实体都须可见，否则 404（缺/跨用户同形=无存在性
    探测面）；无路可走=空 paths 如实回，不谎报错误。
    """
    uid = _resolve_user_id(user_id)
    if (await get_entity_with_relations(source_id, uid)) is None or (
        await get_entity_with_relations(target_id, uid)
    ) is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    paths = await find_path(uid, source_id, target_id, max_depth)
    return {
        "source_id": source_id,
        "target_id": target_id,
        "max_depth": max_depth,
        "paths": paths,
        "count": len(paths),
    }


@router.post("/relations", response_model=RelationResponse)
async def add_relation(data: RelationCreate):
    return await create_relation(data)


@router.get("/relations")
async def get_relations(
    user_id: str = Query("default", description="User ID (UUID or username)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Relation list for a user."""
    uid = _resolve_user_id(user_id)
    return await list_relations(uid, offset, limit)


@router.get("/full", response_model=GraphData)
async def get_full_graph(
    user_id: str = Query("default", description="User ID (UUID or username)"),
    depth: int = Query(2, ge=1, le=5),
):
    """Full graph data (nodes + edges) for frontend G6 rendering."""
    uid = _resolve_user_id(user_id)
    return await get_graph(uid, depth)


@router.get("/", response_model=GraphData)
async def get_graph_data(
    user_id: str = Query("default", description="User ID (UUID or username)"),
    depth: int = 2,
    entity_id: UUID | None = None,
):
    uid = _resolve_user_id(user_id)
    return await get_graph(uid, depth, entity_id)
