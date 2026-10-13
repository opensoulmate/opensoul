"""Reflex cache — fast-path response cache for frequent questions.

Uses fuzzy string matching (normalized Levenshtein ratio) to find
cached responses for similar queries, avoiding redundant LLM calls.
"""

import hashlib
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher


@dataclass
class CacheEntry:
    """A cached Q&A pair."""

    entry_id: str
    query: str
    query_normalized: str
    response: str
    category: str = ""
    tags: list[str] = field(default_factory=list)
    hit_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_hit_at: float = 0.0
    ttl_seconds: float = 86400  # 24h default
    importance: float = 0.5
    source: str = ""  # "manual", "auto", "learned"


class ReflexCache:
    """Thread-safe fuzzy-match response cache."""

    def __init__(
        self,
        max_entries: int = 5000,
        similarity_threshold: float = 0.80,
        default_ttl: float = 86400,
    ):
        self._entries: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self.max_entries = max_entries
        self.similarity_threshold = similarity_threshold
        self.default_ttl = default_ttl
        self._total_queries = 0
        self._total_hits = 0

    def _normalize(self, text: str) -> str:
        """Normalize query for matching: lowercase, strip, collapse whitespace."""
        return " ".join(text.lower().strip().split())

    def _make_id(self, query: str) -> str:
        return hashlib.md5(query.encode()).hexdigest()[:12]

    def put(
        self,
        query: str,
        response: str,
        category: str = "",
        tags: list[str] | None = None,
        importance: float = 0.5,
        ttl_seconds: float | None = None,
        source: str = "manual",
    ) -> CacheEntry:
        """Add or update a cache entry."""
        normalized = self._normalize(query)
        entry_id = self._make_id(normalized)

        entry = CacheEntry(
            entry_id=entry_id,
            query=query,
            query_normalized=normalized,
            response=response,
            category=category,
            tags=tags or [],
            importance=importance,
            ttl_seconds=ttl_seconds or self.default_ttl,
            source=source,
        )

        with self._lock:
            if len(self._entries) >= self.max_entries and entry_id not in self._entries:
                self._evict()
            self._entries[entry_id] = entry

        return entry

    def lookup(self, query: str) -> CacheEntry | None:
        """Look up a cached response for a query using fuzzy matching.

        Returns the best match if similarity >= threshold, else None.
        """
        self._total_queries += 1
        normalized = self._normalize(query)
        now = time.time()

        with self._lock:
            # 1. Exact match (fast path)
            entry_id = self._make_id(normalized)
            entry = self._entries.get(entry_id)
            if entry and not self._is_expired(entry, now):
                entry.hit_count += 1
                entry.last_hit_at = now
                self._total_hits += 1
                return entry

            # 2. Fuzzy match
            best_match = None
            best_score = 0.0

            for e in self._entries.values():
                if self._is_expired(e, now):
                    continue
                score = SequenceMatcher(None, normalized, e.query_normalized).ratio()
                if score > best_score:
                    best_score = score
                    best_match = e

            if best_match and best_score >= self.similarity_threshold:
                best_match.hit_count += 1
                best_match.last_hit_at = now
                self._total_hits += 1
                return best_match

        return None

    def get(self, entry_id: str) -> CacheEntry | None:
        """Get entry by ID."""
        with self._lock:
            return self._entries.get(entry_id)

    def delete(self, entry_id: str) -> bool:
        """Delete an entry."""
        with self._lock:
            return self._entries.pop(entry_id, None) is not None

    def list_entries(
        self,
        category: str | None = None,
        tag: str | None = None,
        min_hits: int | None = None,
        limit: int = 100,
    ) -> list[CacheEntry]:
        """List cache entries with optional filters."""
        results = []
        now = time.time()
        with self._lock:
            for e in self._entries.values():
                if self._is_expired(e, now):
                    continue
                if category and e.category != category:
                    continue
                if tag and tag not in e.tags:
                    continue
                if min_hits is not None and e.hit_count < min_hits:
                    continue
                results.append(e)
        results.sort(key=lambda x: x.hit_count, reverse=True)
        return results[:limit]

    def cleanup(self) -> dict:
        """Remove expired entries."""
        now = time.time()
        removed = 0
        with self._lock:
            expired_ids = [eid for eid, e in self._entries.items() if self._is_expired(e, now)]
            for eid in expired_ids:
                del self._entries[eid]
                removed += 1
        return {"removed": removed, "remaining": len(self._entries)}

    def clear(self) -> dict:
        """Drop ALL entries (expired or not) — overload load-shedding.

        与 cleanup() 语义分界：cleanup=清过期留有效（维护），clear=全清（减负）。
        """
        with self._lock:
            cleared = len(self._entries)
            self._entries.clear()
        return {"cleared": cleared, "remaining": 0}

    def get_stats(self) -> dict:
        """Get cache statistics."""
        now = time.time()
        with self._lock:
            total = len(self._entries)
            expired = sum(1 for e in self._entries.values() if self._is_expired(e, now))
            active = total - expired
            total_hits = sum(e.hit_count for e in self._entries.values())
            by_category = {}
            by_source = {}
            for e in self._entries.values():
                if not self._is_expired(e, now):
                    cat = e.category or "uncategorized"
                    by_category[cat] = by_category.get(cat, 0) + 1
                    by_source[e.source] = by_source.get(e.source, 0) + 1

        hit_rate = (
            round(self._total_hits / self._total_queries * 100, 1) if self._total_queries else 0
        )

        return {
            "total_entries": total,
            "active_entries": active,
            "expired_entries": expired,
            "max_entries": self.max_entries,
            "usage_percent": round(active / self.max_entries * 100, 1) if self.max_entries else 0,
            "total_hits": total_hits,
            "total_queries": self._total_queries,
            "hit_rate_percent": hit_rate,
            "similarity_threshold": self.similarity_threshold,
            "default_ttl_seconds": self.default_ttl,
            "by_category": by_category,
            "by_source": by_source,
        }

    def _is_expired(self, entry: CacheEntry, now: float) -> bool:
        return (now - entry.created_at) > entry.ttl_seconds

    def _evict(self):
        """Evict least-important, least-hit entry (must hold lock)."""
        if not self._entries:
            return
        # Score = importance * log(1 + hits) — lower = evict first
        import math

        worst_id = min(
            self._entries,
            key=lambda eid: (
                self._entries[eid].importance * math.log1p(self._entries[eid].hit_count)
            ),
        )
        del self._entries[worst_id]
