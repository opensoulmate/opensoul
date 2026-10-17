import logging
from uuid import UUID

from src.config import settings

logger = logging.getLogger(__name__)

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        MatchValue,
        PointStruct,
        VectorParams,
    )
except ImportError:
    QdrantClient = None
    Distance = FieldCondition = Filter = MatchValue = PointStruct = VectorParams = None
    logger.warning("qdrant_client not installed — vector search disabled")


class QdrantStore:
    AVAILABLE = QdrantClient is not None

    def __init__(self):
        self._client = None
        self._connected = False

    @property
    def client(self):
        if not self.AVAILABLE:
            return None
        if not self._client:
            self._client = QdrantClient(url=settings.qdrant_url)
        return self._client

    def _check_connection(self) -> bool:
        """Try a lightweight call; cache result so we only warn once."""
        if self._connected:
            return True
        try:
            self.client.get_collections()
            self._connected = True
            return True
        except Exception as exc:
            logger.warning(
                "Qdrant unreachable at %s — vector search disabled: %s", settings.qdrant_url, exc
            )
            return False

    def ensure_collection(self):
        if not self.AVAILABLE:
            logger.debug("Qdrant unavailable, skipping ensure_collection")
            return
        if not self._check_connection():
            return
        collections = [c.name for c in self.client.get_collections().collections]
        if settings.qdrant_collection not in collections:
            self.client.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=VectorParams(
                    size=settings.embedding_dimensions,
                    distance=Distance.COSINE,
                ),
            )

    def upsert_points(self, points):
        if not self.AVAILABLE:
            logger.debug("Qdrant unavailable, skipping upsert_points")
            return
        if not self._check_connection():
            return
        self.client.upsert(
            collection_name=settings.qdrant_collection,
            points=points,
        )

    def search(
        self,
        query_vector: list[float],
        limit: int = 10,
        user_id: UUID | None = None,
        knowledge_id: UUID | None = None,
        score_threshold: float | None = None,
    ) -> list:
        if not self.AVAILABLE:
            logger.debug("Qdrant unavailable, skipping search")
            return []
        if not self._check_connection():
            return []
        query_filter = None
        conditions = []

        if user_id:
            conditions.append(FieldCondition(key="user_id", match=MatchValue(value=str(user_id))))
        if knowledge_id:
            conditions.append(
                FieldCondition(key="knowledge_id", match=MatchValue(value=str(knowledge_id)))
            )

        if conditions:
            query_filter = Filter(must=conditions)

        return self.client.query_points(
            collection_name=settings.qdrant_collection,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            score_threshold=score_threshold,
        )

    def delete_points(self, ids: list[str]):
        if not self.AVAILABLE:
            logger.debug("Qdrant unavailable, skipping delete_points")
            return
        if not self._check_connection():
            return
        self.client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=ids,
        )


qdrant_client = QdrantStore()
