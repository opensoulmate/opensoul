import logging
from difflib import SequenceMatcher

from src.database.postgres import db_pool
from src.services.knowledge import delete_knowledge

logger = logging.getLogger(__name__)


async def deduplicate_knowledge(user_id: str | None = None) -> dict:
    """去重知识库条目。基于标题+内容相似度检测重复，保留最早的一条。

    Args:
        user_id: 指定用户去重，None则全库去重
    Returns:
        去重统计结果
    """
    if user_id:
        rows = await db_pool.fetch(
            "SELECT id, user_id, title, content, created_at FROM knowledge WHERE user_id = ? ORDER BY created_at ASC",
            user_id,
        )
    else:
        rows = await db_pool.fetch(
            "SELECT id, user_id, title, content, created_at FROM knowledge ORDER BY created_at ASC"
        )

    items = [dict(r) for r in rows]
    if len(items) <= 1:
        return {
            "total": len(items),
            "duplicates_found": 0,
            "duplicates_removed": 0,
            "removed_ids": [],
        }

    # 分组：同用户内去重
    user_groups: dict[str, list] = {}
    for item in items:
        uid = item["user_id"]
        if uid not in user_groups:
            user_groups[uid] = []
        user_groups[uid].append(item)

    duplicates = []
    for uid, group in user_groups.items():
        for i in range(len(group)):
            if group[i]["id"] in [d["id"] for d in duplicates]:
                continue
            for j in range(i + 1, len(group)):
                if group[j]["id"] in [d["id"] for d in duplicates]:
                    continue
                sim = _similarity(group[i], group[j])
                if sim >= 0.85:  # 85%相似度视为重复
                    duplicates.append(
                        {
                            "id": group[j]["id"],
                            "title": group[j]["title"],
                            "duplicate_of": group[i]["id"],
                            "similarity": round(sim, 2),
                        }
                    )

    # 删除重复条目（H1-① 收口）：复用主删除面 delete_knowledge 唯一删除路径——
    # 修前此处自开第二套删除路径只删 knowledge/knowledge_tags/knowledge_chunks
    # 三表行，不清 meili 全文索引与 qdrant 向量点，而 fulltext_search/semantic_search
    # 直接回索引命中不回查 DB → 去重删掉的条目仍可被搜索命中（幽灵搜索结果）。
    # 第二套删除路径=漂移根因，收口为与主删除面同一路径（两面都清+三表级联）。
    # 语义收口（如实记）：检索面清理失败=该条不删（与主删除面同语义），宁留重复行
    # 禁造幽灵命中；行缺失/归属闸 False 不计 removed（计数如实）。
    removed_ids = []
    owner_of = {item["id"]: item["user_id"] for item in items}
    for dup in duplicates:
        try:
            if await delete_knowledge(dup["id"], owner_of[dup["id"]]):
                removed_ids.append(dup["id"])
            else:
                logger.warning(
                    f"Duplicate {dup['id']} not removed (row vanished or ownership gate)"
                )
        except Exception as e:
            logger.warning(f"Failed to remove duplicate {dup['id']}: {e}")

    result = {
        "total": len(items),
        "duplicates_found": len(duplicates),
        "duplicates_removed": len(removed_ids),
        "removed_ids": removed_ids,
        "details": duplicates,
    }
    logger.info(f"Deduplication: {result}")
    return result


def _similarity(a: dict, b: dict) -> float:
    """计算两条知识的相似度（标题40% + 内容60%）"""
    title_sim = SequenceMatcher(None, a["title"] or "", b["title"] or "").ratio()
    content_sim = SequenceMatcher(
        None, (a["content"] or "")[:2000], (b["content"] or "")[:2000]
    ).ratio()
    return title_sim * 0.4 + content_sim * 0.6


async def get_duplicate_report(user_id: str | None = None) -> list[dict]:
    """获取重复报告（不删除，仅报告）"""
    if user_id:
        rows = await db_pool.fetch(
            "SELECT id, user_id, title, content, created_at FROM knowledge WHERE user_id = ? ORDER BY created_at ASC",
            user_id,
        )
    else:
        rows = await db_pool.fetch(
            "SELECT id, user_id, title, content, created_at FROM knowledge ORDER BY created_at ASC"
        )

    items = [dict(r) for r in rows]
    if len(items) <= 1:
        return []

    user_groups: dict[str, list] = {}
    for item in items:
        uid = item["user_id"]
        if uid not in user_groups:
            user_groups[uid] = []
        user_groups[uid].append(item)

    report = []
    for uid, group in user_groups.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                sim = _similarity(group[i], group[j])
                if sim >= 0.85:
                    report.append(
                        {
                            "item_a": {"id": group[i]["id"], "title": group[i]["title"]},
                            "item_b": {"id": group[j]["id"], "title": group[j]["title"]},
                            "similarity": round(sim, 2),
                        }
                    )
    return report
