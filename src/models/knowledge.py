import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


def normalize_tags(raw) -> list[str]:
    """tags 存储形态 -> list[str] 形状契约单一源（KnowledgeResponse.tags 响应归一
    与 Meilisearch doc tags 共用，禁双源漂移）。存储两形态：knowledge.tags 列
    JSON 文本（DEFAULT '[]'）与链接面 GROUP_CONCAT 逗号串（None=无标签）；
    list 形态（请求体/已归一行）逐项 str 归一。"""
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        s = raw.strip()
        parsed = None
        if s.startswith("["):
            try:
                parsed = json.loads(s)
            except Exception:
                parsed = None
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        if s:
            return [t for t in s.split(",") if t]
        return []
    return []


class KnowledgeBase(BaseModel):
    title: str
    content: str
    source: str = ""
    content_type: str = "text"
    metadata: dict = Field(default_factory=dict)


class KnowledgeCreate(KnowledgeBase):
    tags: list[str] = Field(default_factory=list)


class KnowledgeUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    source: str | None = None
    content_type: str | None = None
    metadata: dict | None = None
    tags: list[str] | None = None


class KnowledgeResponse(KnowledgeBase):
    id: UUID
    user_id: UUID
    tags: list[str] = Field(default_factory=list)
    embedding_id: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeChunk(BaseModel):
    id: UUID
    knowledge_id: UUID
    chunk_index: int
    content: str
    embedding_id: str | None = None
    token_count: int = 0
