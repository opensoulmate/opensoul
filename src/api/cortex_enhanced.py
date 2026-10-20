"""Enhanced Cortex API — GraphRAG, Recommendations, Quality Scoring.

Three advanced intelligence features for the knowledge platform:
- GraphRAG: auto-extract entities and relations from knowledge documents
- Recommendations: find related knowledge entries
- Quality: multi-dimensional quality scoring of knowledge entries
"""

import hashlib
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from src.cortex.graphrag import GraphRAGEngine
from src.cortex.quality import QualityScorer
from src.cortex.recommendation import RecommendationEngine
from src.database.postgres import db_pool
from src.nerve.event_bridge import push_event
from src.services.tenant_binding import bind_identity

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────────────
graphrag = GraphRAGEngine()
recommender = RecommendationEngine()
scorer = QualityScorer()


# ── 身份派生 ─────────────────────────────────────────────────────────


def _resolve_user_id(user_id: str) -> UUID:
    """Convert user_id to UUID — accept both UUID strings and plain usernames."""
    try:
        return UUID(user_id)
    except ValueError:
        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


# ── Request Schemas ────────────────────────────────────────────────


class GraphQueryRequest(BaseModel):
    entity_name: str
    depth: int = 2


# ── GraphRAG Endpoints ────────────────────────────────────────────


@router.post("/graphrag/build")
async def build_graph(
    user_id: str = Query(default="default"),
    authorization: str | None = Header(None),
):
    """Scan all knowledge entries and auto-extract entities + relations
    into the knowledge graph.

    身份轴收口（F3b-⑥ 已裁决语义同族既有面延伸，F3b-④b 先例）：
    user_id 从认证派生（bind_identity），自报值只可确认派生绑定——
    认证请求自报他人档案=403（冒写面收口）；'default'/空=未声明→取派生；
    匿名保留自报（既有本地多档案语义，F3b-⑥ 同款）；坏 token=401 零副作用。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = str(_resolve_user_id(identity.user_id))
    result = await graphrag.build_graph_from_knowledge(db_pool, uid)

    push_event(
        {
            "organ": "cortex",
            "emoji": "🧩",
            "type": "graphrag_build",
            "summary": f"🔮 GraphRAG built: {result['entities_new']} new entities, {result['relations_new']} new relations",
            "detail": result,
        },
        tenant_id=identity.tenant_id,
    )

    return result


@router.post("/graphrag/query")
async def query_graph(
    req: GraphQueryRequest,
    user_id: str = Query(default="default"),
    authorization: str | None = Header(None),
):
    """Query the knowledge graph: BFS from an entity up to *depth* hops.

    身份轴同 build（F3b-⑥ 同族语义，禁自报冒读）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = str(_resolve_user_id(identity.user_id))
    result = await graphrag.query_graph(db_pool, uid, req.entity_name, req.depth)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.post("/graphrag/extract")
async def extract_from_text(
    text: str = Query(..., description="Text to extract entities and relations from"),
):
    """Extract entities and relations from arbitrary text (no DB write)."""
    entities = graphrag.extract_entities(text)
    relations = graphrag.extract_relations(text, entities)
    return {
        "entities": entities,
        "relations": relations,
        "entity_count": len(entities),
        "relation_count": len(relations),
    }


# ── Recommendation Endpoints ──────────────────────────────────────


@router.get("/recommend/trending")
async def trending(
    user_id: str = Query(default="default"),
    limit: int = Query(default=10, ge=1, le=50),
    authorization: str | None = Header(None),
):
    """Get trending (newest) knowledge entries. 身份轴同 build（禁自报冒读）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = str(_resolve_user_id(identity.user_id))
    return {"entries": await recommender.get_trending(db_pool, uid, limit)}


@router.get("/recommend/recent")
async def recent(
    user_id: str = Query(default="default"),
    limit: int = Query(default=10, ge=1, le=50),
    authorization: str | None = Header(None),
):
    """Get most recent knowledge entries. 身份轴同 build（禁自报冒读）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = str(_resolve_user_id(identity.user_id))
    return {"entries": await recommender.get_recent(db_pool, uid, limit)}


@router.get("/recommend/{knowledge_id}")
async def recommend_related(
    knowledge_id: str,
    user_id: str = Query(default="default"),
    limit: int = Query(default=5, ge=1, le=20),
    authorization: str | None = Header(None),
):
    """Get recommended knowledge entries related to *knowledge_id*.

    身份轴同 build（禁自报冒读）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = str(_resolve_user_id(identity.user_id))
    results = await recommender.recommend(db_pool, uid, knowledge_id, limit)
    return {
        "knowledge_id": knowledge_id,
        "recommendations": results,
        "count": len(results),
    }


# ── Quality Scoring Endpoints ─────────────────────────────────────


@router.get("/quality/report")
async def quality_report(
    user_id: str = Query(default="default"),
    authorization: str | None = Header(None),
):
    """Aggregate quality report: distribution, dimension averages, top/bottom.

    身份轴同 build（禁自报冒读）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = str(_resolve_user_id(identity.user_id))
    return await scorer.get_quality_report(db_pool, uid)


@router.get("/quality/batch")
async def score_batch(
    user_id: str = Query(default="default"),
    limit: int = Query(default=100, ge=1, le=1000),
    authorization: str | None = Header(None),
):
    """Batch score knowledge entries, sorted by quality. 身份轴同 build（禁自报冒读）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = str(_resolve_user_id(identity.user_id))
    results = await scorer.score_all(db_pool, uid, limit)
    return {
        "scores": results,
        "count": len(results),
    }


@router.get("/quality/score/{knowledge_id}")
async def score_knowledge(
    knowledge_id: str,
    user_id: str = Query(default="default"),
    authorization: str | None = Header(None),
):
    """Score a single knowledge entry on multiple quality dimensions.

    身份轴同 build（禁自报冒读）。"""
    identity = bind_identity(authorization, "", user_id)
    uid = str(_resolve_user_id(identity.user_id))
    result = await scorer.score_knowledge(db_pool, uid, knowledge_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


# ── Health ─────────────────────────────────────────────────────────


@router.get("/enhanced/health")
async def enhanced_health():
    """Cortex enhanced features health check."""
    return {
        "status": "ok",
        "component": "OpenCortex-Enhanced",
        "features": {
            "graphrag": {"available": True, "engine": "pattern-based"},
            "recommendation": {"available": True, "engine": "jaccard+entity"},
            "quality": {"available": True, "engine": "multi-dimensional"},
        },
    }
