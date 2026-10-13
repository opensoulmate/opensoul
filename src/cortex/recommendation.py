"""Knowledge Recommendation Engine.

Recommends related knowledge entries based on keyword similarity (Jaccard),
shared entities, shared tags, and domain/type matching.
"""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _ts(value: Any) -> str | None:
    """created_at 出线归一：datetime（PG/asyncpg）走 isoformat，TEXT（SQLite
    时间戳恒为 text，生产 typeof 实证）原样透传。修前 .isoformat() 硬调对
    SQLite TEXT 时间戳必炸——任何有知识行的用户 GET /recommend/trending|recent
    恒 500（2026-10-13 live 实证），空结果面把它掩成 200。"""
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if iso else str(value)


class RecommendationEngine:
    """Suggest related knowledge entries for a given item."""

    # ── Similarity Weights ──────────────────────────────────────────

    WEIGHT_KEYWORD = 0.45
    WEIGHT_ENTITY = 0.25
    WEIGHT_TAG = 0.15
    WEIGHT_DOMAIN = 0.10
    WEIGHT_TYPE = 0.05

    # ── Public API ──────────────────────────────────────────────────

    async def recommend(
        self, db_pool: Any, user_id: str, knowledge_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Return top-N related knowledge entries for *knowledge_id*."""
        target = await db_pool.fetchrow(
            "SELECT id, title, content, metadata FROM knowledge WHERE id = $1 AND user_id = $2",
            knowledge_id,
            user_id,
        )
        if not target:
            return []

        target_text = f"{target['title'] or ''} {target['content'] or ''}"
        target_kw = self._extract_keywords(target_text)
        target_meta = self._parse_meta(target["metadata"])
        target_tags = set(target_meta.get("tags", []))
        target_domain = target_meta.get("domain", "")
        target_type = target_meta.get("type", "")

        # Target entities
        target_entities = await self._get_linked_entities(db_pool, knowledge_id)
        target_entity_names = {e["name"] for e in target_entities}

        # All other knowledge
        rows = await db_pool.fetch(
            "SELECT id, title, content, metadata FROM knowledge WHERE user_id = $1 AND id != $2",
            user_id,
            knowledge_id,
        )

        scored: list[dict[str, Any]] = []
        for row in rows:
            item_text = f"{row['title'] or ''} {row['content'] or ''}"
            item_kw = self._extract_keywords(item_text)
            item_meta = self._parse_meta(row["metadata"])
            item_tags = set(item_meta.get("tags", []))
            item_domain = item_meta.get("domain", "")
            item_type = item_meta.get("type", "")

            # Keyword similarity (Jaccard)
            kw_sim = self._jaccard(target_kw, item_kw)

            # Entity overlap
            item_entities = await self._get_linked_entities(db_pool, str(row["id"]))
            item_entity_names = {e["name"] for e in item_entities}
            entity_sim = self._jaccard(target_entity_names, item_entity_names)

            # Tag overlap
            tag_sim = self._jaccard(target_tags, item_tags)

            # Domain match
            domain_sim = 1.0 if target_domain and target_domain == item_domain else 0.0

            # Type match
            type_sim = 1.0 if target_type and target_type == item_type else 0.0

            total = (
                kw_sim * self.WEIGHT_KEYWORD
                + entity_sim * self.WEIGHT_ENTITY
                + tag_sim * self.WEIGHT_TAG
                + domain_sim * self.WEIGHT_DOMAIN
                + type_sim * self.WEIGHT_TYPE
            )

            if total > 0.05:
                scored.append(
                    {
                        "id": str(row["id"]),
                        "title": row["title"] or "",
                        "similarity": round(total, 4),
                        "breakdown": {
                            "keyword": round(kw_sim, 4),
                            "entity": round(entity_sim, 4),
                            "tag": round(tag_sim, 4),
                            "domain": round(domain_sim, 4),
                            "type": round(type_sim, 4),
                        },
                    }
                )

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:limit]

    async def get_trending(
        self, db_pool: Any, user_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Most-accessed knowledge entries."""
        rows = await db_pool.fetch(
            "SELECT id, title, metadata, created_at FROM knowledge "
            "WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
            user_id,
            limit,
        )
        return [
            {
                "id": str(r["id"]),
                "title": r["title"] or "",
                "created_at": _ts(r["created_at"]),
            }
            for r in rows
        ]

    async def get_recent(self, db_pool: Any, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Newest knowledge entries."""
        rows = await db_pool.fetch(
            "SELECT id, title, metadata, created_at FROM knowledge "
            "WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
            user_id,
            limit,
        )
        return [
            {
                "id": str(r["id"]),
                "title": r["title"] or "",
                "created_at": _ts(r["created_at"]),
            }
            for r in rows
        ]

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """Extract Chinese 2+ char tokens and English 3+ char tokens."""
        chinese = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
        english = set(w.lower() for w in re.findall(r"[a-zA-Z]{3,}", text))
        return chinese | english

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        inter = a & b
        union = a | b
        return len(inter) / len(union) if union else 0.0

    @staticmethod
    def _parse_meta(metadata: Any) -> dict:
        if isinstance(metadata, dict):
            return metadata
        if isinstance(metadata, str):
            try:
                return json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    async def _get_linked_entities(self, db_pool: Any, knowledge_id: str) -> list[dict]:
        """Get entities linked to a knowledge entry via knowledge_entity_link
        or by name matching if the link table doesn't exist."""
        try:
            rows = await db_pool.fetch(
                "SELECT e.name, e.type FROM entities e "
                "JOIN knowledge_entity_link kel ON e.id = kel.entity_id "
                "WHERE kel.knowledge_id = $1",
                knowledge_id,
            )
            return [{"name": r["name"], "type": r["type"]} for r in rows]
        except Exception:
            return []
