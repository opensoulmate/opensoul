"""Enhanced Cortex API — GraphRAG, Recommendations, Quality Scoring.

Three advanced intelligence features for the knowledge platform:
- GraphRAG: auto-extract entities and relations from knowledge documents
- Recommendations: find related knowledge entries
- Quality: multi-dimensional quality scoring of knowledge entries
"""

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
    into the knowledge graph."""
    identity = bind_identity(authorization)
    result = await graphrag.build_graph_from_knowledge(db_pool, user_id)

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
async def query_graph(req: GraphQueryRequest, user_id: str = Query(default="default")):
    """Query the knowledge graph: BFS from an entity up to *depth* hops."""
    result = await graphrag.query_graph(db_pool, user_id, req.entity_name, req.depth)
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
):
    """Get trending (newest) knowledge entries."""
    return {"entries": await recommender.get_trending(db_pool, user_id, limit)}


@router.get("/recommend/recent")
async def recent(
    user_id: str = Query(default="default"),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Get most recent knowledge entries."""
    return {"entries": await recommender.get_recent(db_pool, user_id, limit)}


@router.get("/recommend/{knowledge_id}")
async def recommend_related(
    knowledge_id: str,
    user_id: str = Query(default="default"),
    limit: int = Query(default=5, ge=1, le=20),
):
    """Get recommended knowledge entries related to *knowledge_id*."""
    results = await recommender.recommend(db_pool, user_id, knowledge_id, limit)
    return {
        "knowledge_id": knowledge_id,
        "recommendations": results,
        "count": len(results),
    }


# ── Quality Scoring Endpoints ─────────────────────────────────────


@router.get("/quality/report")
async def quality_report(user_id: str = Query(default="default")):
    """Aggregate quality report: distribution, dimension averages, top/bottom."""
    return await scorer.get_quality_report(db_pool, user_id)


@router.get("/quality/batch")
async def score_batch(
    user_id: str = Query(default="default"),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """Batch score knowledge entries, sorted by quality."""
    results = await scorer.score_all(db_pool, user_id, limit)
    return {
        "scores": results,
        "count": len(results),
    }


@router.get("/quality/score/{knowledge_id}")
async def score_knowledge(
    knowledge_id: str,
    user_id: str = Query(default="default"),
):
    """Score a single knowledge entry on multiple quality dimensions."""
    result = await scorer.score_knowledge(db_pool, user_id, knowledge_id)
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
