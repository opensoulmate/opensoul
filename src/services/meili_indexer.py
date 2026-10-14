"""Meilisearch知识库索引管道 — knowledge表→全文索引同步。

接线点：
- main.py lifespan启动时：index_all_knowledge()（空索引首次填充）
- api/search.py POST /api/search/reindex：手动全量重建
- api/vein.py + api/capture.py写入点：index_knowledge_doc()增量索引
- services/knowledge.py create/update 写面：index_knowledge_doc()增量索引（KN-②）
- api/vein.py删除knowledge时：delete_knowledge_doc()

设计约束：
- 全部操作fire-and-forget语义：索引失败不阻塞业务主流程（mem0 §1.1失败可见=logger.warning）
- 文档content截断5000字：meilisearch单文档合理上限，全文可搜
- doc tags 真源=链接面（knowledge_tags/tags GROUP_CONCAT，与 get/list 同一读真源；
  修前读 knowledge.tags 列=该列无写点恒 '[]'，全量重索引把已索引条目 tags 洗空）
"""

import logging

from src.database.meilisearch import meili_client
from src.models.knowledge import normalize_tags

logger = logging.getLogger(__name__)

# 读真源=链接面 GROUP_CONCAT tags（get/list 同源），knowledge.tags 列无写点不作源
_SELECT_JOIN = (
    "SELECT k.id, k.user_id, k.title, k.content, k.source, k.content_type, k.created_at, "
    "GROUP_CONCAT(t.name) AS tags "
    "FROM knowledge k "
    "LEFT JOIN knowledge_tags kt ON k.id = kt.knowledge_id "
    "LEFT JOIN tags t ON kt.tag_id = t.id "
)


def _row_to_doc(row: dict) -> dict:
    """knowledge表行→Meilisearch文档。tags 形态归一走 normalize_tags 单一源。"""
    tags = normalize_tags(row.get("tags"))
    created_at = row.get("created_at")
    if created_at is None:
        created_at = 0
    elif hasattr(created_at, "timestamp"):
        created_at = created_at.timestamp()
    return {
        "id": str(row["id"]),
        "title": row.get("title") or "",
        "content": (row.get("content") or "")[:5000],
        "tags": tags or [],
        "user_id": str(row.get("user_id") or "default"),
        "content_type": row.get("content_type") or "text/plain",
        "source": row.get("source") or "",
        "created_at": created_at,
    }


async def index_all_knowledge() -> dict:
    """全量索引：knowledge表→Meilisearch。返回{indexed, total, error?}。"""
    if not meili_client.AVAILABLE:
        return {"indexed": 0, "error": "meilisearch python client not installed"}
    try:
        from src.database.postgres import db_pool

        meili_client.ensure_index()
        rows = await db_pool.fetch(f"{_SELECT_JOIN} GROUP BY k.id")
        docs = [_row_to_doc(dict(r)) for r in rows]
        if docs:
            meili_client.add_documents(docs)
        logger.info("meili reindex: %d docs queued (index=%s)", len(docs), "opensoul_knowledge")
        return {"indexed": len(docs), "total": len(docs)}
    except Exception as e:
        logger.warning("meili reindex failed: %s", e)
        return {"indexed": 0, "error": str(e)}


async def index_knowledge_doc(knowledge_id: str) -> bool:
    """单条增量索引：knowledge写入点调用（fire-and-forget安全）。"""
    if not meili_client.AVAILABLE:
        return False
    try:
        from src.database.postgres import db_pool

        row = await db_pool.fetchrow(f"{_SELECT_JOIN} WHERE k.id = $1 GROUP BY k.id", knowledge_id)
        if not row:
            return False
        meili_client.ensure_index()
        meili_client.add_documents([_row_to_doc(dict(row))])
        return True
    except Exception as e:
        logger.warning("meili index doc %s failed: %s", knowledge_id, e)
        return False


def schedule_index(knowledge_id: str) -> None:
    """写入点fire-and-forget调度：运行中loop内create_task，无loop环境静默跳过。

    索引失败仅logger.warning，绝不阻塞knowledge写入主流程。
    """
    try:
        import asyncio

        loop = asyncio.get_running_loop()
        if loop and loop.is_running():
            loop.create_task(index_knowledge_doc(knowledge_id))
    except RuntimeError:
        # 无运行中event loop（脚本/测试环境）→跳过，下次启动全量索引补上
        pass
    except Exception as e:
        logger.warning("meili schedule_index %s failed: %s", knowledge_id, e)


def delete_knowledge_doc(knowledge_id: str) -> bool:
    """删除索引条目（knowledge删除时调用）。"""
    if not meili_client.AVAILABLE:
        return False
    try:
        meili_client.delete_document(str(knowledge_id))
        return True
    except Exception as e:
        logger.warning("meili delete %s failed: %s", knowledge_id, e)
        return False


def get_index_stats() -> dict:
    """索引统计：文档数+索引状态。"""
    if not meili_client.AVAILABLE:
        return {"available": False}
    try:
        stats = meili_client.get_stats()
        # meilisearch python客户端新版get_stats()返回IndexStats对象（非dict），
        # 属性为snake_case：number_of_documents
        doc_count = getattr(stats, "number_of_documents", None)
        if doc_count is None:
            doc_count = getattr(stats, "numberOfDocuments", None)
        if doc_count is None and hasattr(stats, "get"):
            doc_count = stats.get("numberOfDocuments", 0)
        return {
            "available": True,
            "indexed_documents": doc_count or 0,
            "index": "opensoul_knowledge",
        }
    except Exception as e:
        return {"available": True, "indexed_documents": 0, "error": str(e)}
