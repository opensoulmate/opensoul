import json
import uuid as _uuid
from uuid import UUID

from src.database.meilisearch import meili_client
from src.database.postgres import db_pool
from src.database.qdrant import qdrant_client

try:
    from qdrant_client.models import PointStruct
except ImportError:
    PointStruct = None
from src.models.knowledge import KnowledgeCreate, KnowledgeUpdate, normalize_tags
from src.services.chunking import smart_chunk
from src.services.embedding import get_embeddings_batch
from src.services.meili_indexer import index_knowledge_doc


def _row_out(d: dict) -> dict:
    """行→响应契约归一（单一收口，声明从实现派生）。

    metadata：JSON 文本→dict（既有语义逐字保留，解析失败=空 dict）。
    tags：KnowledgeResponse.tags 契约=list[str]，存储面两形态——knowledge.tags 列
    JSON 文本（DEFAULT '[]'）与链接面 GROUP_CONCAT 逗号串（None=无标签）；修前
    存储行原样回吐，响应校验 Input should be a valid list → 500（create 逐次必炸、
    get/list 有行即炸）。全部归一为 list[str]。
    """
    if isinstance(d.get("metadata"), str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except Exception:
            d["metadata"] = {}
    d["tags"] = normalize_tags(d.get("tags"))
    return d


async def _replace_tags(knowledge_id: str, tag_names: list[str], user_id: UUID) -> None:
    """链接面 tags 写入单一收口（create/update 共用，禁双源漂移）。

    先清旧链接再按名重链：tags 行是用户词表（ON CONFLICT DO NOTHING 保 id 稳定、
    解链不删词表行），knowledge_tags 链接面是条目真源（get/list 的 GROUP_CONCAT
    读这里）。修前 update 对 tags 整个跳过=静默丢+谎报 ok（KN-①）。
    """
    await db_pool.execute("DELETE FROM knowledge_tags WHERE knowledge_id = $1", knowledge_id)
    for tag_name in tag_names:
        await db_pool.execute(
            "INSERT INTO tags (id, name, user_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            str(_uuid.uuid4()),
            tag_name,
            str(user_id),
        )
        tag = await db_pool.fetchrow(
            "SELECT id FROM tags WHERE name = $1 AND user_id = $2", tag_name, str(user_id)
        )
        if tag:
            await db_pool.execute(
                "INSERT INTO knowledge_tags (knowledge_id, tag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                knowledge_id,
                tag["id"],
            )


async def _write_chunk_face(
    knowledge_id: str, content: str, content_type: str, user_id: UUID
) -> None:
    """分块+嵌入落面单一收口（create/update 重分块共用，禁双源漂移）。

    语义与 create 原内联块逐字一致：smart_chunk → get_embeddings_batch →
    knowledge_chunks 行 + qdrant points（point id={knowledge_id}_{chunk.index}）。
    空向量（embedding 降级）不写 qdrant——空 vector 会被 qdrant 拒绝并打断整个
    入库；分块行照落（embedding_id=NULL），关键词面兜底。
    """
    chunks = smart_chunk(content or "", content_type=content_type or "")
    if chunks:
        chunk_texts = [c.content for c in chunks]
        embeddings = await get_embeddings_batch(chunk_texts)
        points = []
        for chunk, embedding in zip(chunks, embeddings):
            point_id = f"{knowledge_id}_{chunk.index}"
            # 空向量（embedding降级）不写qdrant——空vector会被qdrant拒绝并打断整个入库
            if PointStruct is not None and embedding:
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=embedding,
                        payload={
                            "knowledge_id": str(knowledge_id),
                            "chunk_index": chunk.index,
                            "content": chunk.content,
                            "user_id": str(user_id),
                        },
                    )
                )
            chunk_id = str(_uuid.uuid4())
            await db_pool.execute(
                "INSERT INTO knowledge_chunks (id, knowledge_id, chunk_index, content, embedding_id, token_count) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                chunk_id,
                knowledge_id,
                chunk.index,
                chunk.content,
                point_id if embedding else None,
                len(chunk.content.split()),
            )
        if points:
            qdrant_client.upsert_points(points)


async def _drop_chunk_face(knowledge_id: str) -> None:
    """旧分块/向量面清除（update 重分块前置单一收口）。

    与 delete_knowledge 级联块同款语义（按行记录的 embedding_id 删 qdrant 点、
    清 knowledge_chunks 行），但时序刻意不同：update 是"换新前先清旧"（行还在、
    归属闸已过），delete 是随知识行级联删——故不并用一个口，防时序语义漂移。
    """
    rows = await db_pool.fetch(
        "SELECT embedding_id FROM knowledge_chunks WHERE knowledge_id = $1", knowledge_id
    )
    embedding_ids = [c["embedding_id"] for c in rows if c["embedding_id"]]
    if embedding_ids and qdrant_client.AVAILABLE:
        qdrant_client.delete_points(embedding_ids)
    await db_pool.execute("DELETE FROM knowledge_chunks WHERE knowledge_id = $1", knowledge_id)


async def create_knowledge(data: KnowledgeCreate, user_id: UUID) -> dict:
    knowledge_id = str(_uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO knowledge (id, title, content, source, content_type, metadata, user_id) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        knowledge_id,
        data.title,
        data.content,
        data.source,
        data.content_type,
        json.dumps(data.metadata or {}),
        str(user_id),
    )
    await db_pool.fetchrow("SELECT * FROM knowledge WHERE id = $1", knowledge_id)

    # Add tags（链接面写入走 _replace_tags 单一收口，与 update 同源）
    await _replace_tags(knowledge_id, data.tags, user_id)

    # Chunk and embed（分块+嵌入落面走 _write_chunk_face 单一收口，与 update
    # 重分块同源；修前 create 内联块与 update 无共享=重分块若另写一份必双源漂移）
    await _write_chunk_face(knowledge_id, data.content, data.content_type, user_id)

    # Meili 索引写面走 meili_indexer.index_knowledge_doc 单一构造口（doc 从 DB
    # 读真源派生，与 update/全量重索引同形同源；修前 create 内联 doc 与
    # _row_to_doc 双形状双 tags 源=同一索引内容随写入路径漂移）。行为收口如实记：
    # 修前 meili 异常逃逸=行已落库后 create 500 部分写，现按 meili_indexer 既有
    # fire-and-forget 语义失败 logger.warning 不阻断业务主流程。
    await index_knowledge_doc(knowledge_id)

    # 响应走 get_knowledge 同一读真源（链接面 GROUP_CONCAT）：create/get/list
    # 三面 tags 同源禁双源漂移（修前 create 回吐存储行原样=tags 文本触发 500）
    return await get_knowledge(_uuid.UUID(knowledge_id), user_id)


async def get_knowledge(knowledge_id: UUID, user_id: UUID) -> dict | None:
    row = await db_pool.fetchrow(
        "SELECT k.*, GROUP_CONCAT(t.name) as tags "
        "FROM knowledge k "
        "LEFT JOIN knowledge_tags kt ON k.id = kt.knowledge_id "
        "LEFT JOIN tags t ON kt.tag_id = t.id "
        "WHERE k.id = $1 AND k.user_id = $2 GROUP BY k.id",
        knowledge_id,
        str(user_id),
    )
    if not row:
        return None
    return _row_out(dict(row))


async def list_knowledge(
    user_id: UUID,
    offset: int = 0,
    limit: int = 20,
    content_type: str | None = None,
    domain: str | None = None,
    tag: str | None = None,
) -> list[dict]:
    conditions = ["k.user_id = ?"]
    values: list = [str(user_id)]
    idx = 2

    if content_type:
        conditions.append("k.content_type = ?")
        values.append(content_type)
        idx += 1

    if domain:
        conditions.append("k.metadata IS NOT NULL")
        values.append(domain)
        idx += 1

    where_clause = " AND ".join(conditions)

    if tag:
        # Filter by tag via join
        query = (
            "SELECT k.*, GROUP_CONCAT(t.name) as tags FROM knowledge k "
            "LEFT JOIN knowledge_tags kt ON k.id = kt.knowledge_id "
            "LEFT JOIN tags t ON kt.tag_id = t.id "
            "WHERE " + where_clause + " AND EXISTS ("
            "SELECT 1 FROM knowledge_tags kt2 JOIN tags t2 ON kt2.tag_id = t2.id "
            "WHERE kt2.knowledge_id = k.id AND t2.name = ?) "
            "GROUP BY k.id ORDER BY k.created_at DESC LIMIT ? OFFSET ?"
        )
        values.extend([tag, limit, offset])
    else:
        query = (
            "SELECT k.*, GROUP_CONCAT(t.name) as tags FROM knowledge k "
            "LEFT JOIN knowledge_tags kt ON k.id = kt.knowledge_id "
            "LEFT JOIN tags t ON kt.tag_id = t.id "
            "WHERE " + where_clause + " "
            "GROUP BY k.id ORDER BY k.created_at DESC LIMIT ? OFFSET ?"
        )
        values.extend([limit, offset])

    rows = await db_pool.fetch(query, *values)
    result = []
    for r in rows:
        result.append(_row_out(dict(r)))
    return result


async def update_knowledge(knowledge_id: UUID, data: KnowledgeUpdate, user_id: UUID) -> dict | None:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_knowledge(knowledge_id, user_id)

    # 归属闸前置（TL-⑲ delete 同型）：行属他人/缺行=零副作用——tags 写面是
    # 链接表副作用，无闸则他人可用 update 写坏受害者链接面
    owned = await db_pool.fetchval(
        "SELECT 1 FROM knowledge WHERE id = $1 AND user_id = $2", knowledge_id, str(user_id)
    )
    if not owned:
        return None

    set_clauses = []
    values = []
    for field, value in fields.items():
        if field == "tags":
            continue
        if field == "metadata":
            # 铁律：metadata 落库 json.dumps（修前裸 dict 绑参→绑定失败 500）
            value = json.dumps(value or {})
        set_clauses.append(f"{field} = ?")
        values.append(value)

    updated = None
    if set_clauses:
        values.extend([knowledge_id, str(user_id)])
        # RETURNING content/content_type=重分块输入真源（UPDATE 后行值，content_type
        # 可能本次未传而在行上；toggle_star 同款 RETURNING 先例）
        updated = await db_pool.fetchrow(
            f"UPDATE knowledge SET {', '.join(set_clauses)}, updated_at = NOW() "
            f"WHERE id = ? AND user_id = ? RETURNING content, content_type",
            *values,
        )
    else:
        # tags-only：行级列零变化也随改 bump updated_at（tags 是条目状态一部分；
        # 修前此形态空 SET 拼 SQL 炸 'near , syntax error'→500）
        await db_pool.execute(
            "UPDATE knowledge SET updated_at = NOW() WHERE id = ? AND user_id = ?",
            knowledge_id,
            str(user_id),
        )

    if "tags" in fields:
        # 修前整个跳过=静默丢+200 回旧 tags 谎报 ok（KN-①）；现走 create 同一链接面收口
        await _replace_tags(str(knowledge_id), fields["tags"] or [], user_id)

    # KN-③：content/content_type 变更=分块真源变化，旧分块/向量必须随换（KN-② 诚实
    # 边界留档「update 不重分块/重嵌入=向量面旧文留档」收口——检索命中旧文=向量面
    # 谎报）。title/metadata/tags/source 不参与分块构造，不触发（宁窄勿滥）。
    # updated 为 None=行随并发删除消失（归属闸后竞态），无物可分块——get_knowledge
    # 同刻回 None→404，非静默顶替。
    if ("content" in fields or "content_type" in fields) and updated is not None:
        await _drop_chunk_face(str(knowledge_id))
        await _write_chunk_face(
            str(knowledge_id), updated["content"], updated["content_type"], user_id
        )

    # KN-②：写面后真重索引（修 KN-① 留档边界「不重刷 meili 索引=content/title/
    # tags 变更后检索面仍旧文」）。doc 从 DB 读真源派生（tags=链接面，与 get/list
    # 同一读真源），失败 indexer 内 logger.warning 不阻断（fire-and-forget 语义）。
    await index_knowledge_doc(str(knowledge_id))

    return await get_knowledge(knowledge_id, user_id)


async def delete_knowledge(knowledge_id: UUID, user_id: UUID) -> bool:
    # 归属闸前置：行属他人=零副作用（修前 qdrant/meili 在归属检查前先删=他人
    # 向量/索引可被毁，SQL 行留着但检索面已残）
    owned = await db_pool.fetchval(
        "SELECT 1 FROM knowledge WHERE id = $1 AND user_id = $2", knowledge_id, user_id
    )
    if not owned:
        return False

    # Delete chunks from qdrant
    chunks = await db_pool.fetch(
        "SELECT embedding_id FROM knowledge_chunks WHERE knowledge_id = $1", knowledge_id
    )
    embedding_ids = [c["embedding_id"] for c in chunks if c["embedding_id"]]
    if embedding_ids and qdrant_client.AVAILABLE:
        qdrant_client.delete_points(embedding_ids)

    # Delete from meilisearch
    if meili_client.AVAILABLE:
        meili_client.delete_document(str(knowledge_id))

    # Delete from postgres
    result = await db_pool.execute(
        "DELETE FROM knowledge WHERE id = $1 AND user_id = $2", knowledge_id, user_id
    )
    if "DELETE 1" in result:
        # 级联三面（TL-⑥ 删除工具同款语义）：链接/分块行随知识行删——修前只删
        # knowledge 行，knowledge_chunks/knowledge_tags 留孤儿行（孤儿增长）
        await db_pool.execute("DELETE FROM knowledge_tags WHERE knowledge_id = $1", knowledge_id)
        await db_pool.execute("DELETE FROM knowledge_chunks WHERE knowledge_id = $1", knowledge_id)
    return "DELETE 1" in result


async def toggle_star(knowledge_id: UUID, user_id: UUID) -> dict | None:
    """Toggle star status on a knowledge item."""
    row = await db_pool.fetchrow(
        "UPDATE knowledge SET starred = NOT COALESCE(starred, FALSE), updated_at = NOW() "
        "WHERE id = $1 AND user_id = $2 RETURNING starred",
        knowledge_id,
        str(user_id),
    )
    if not row:
        return None
    return {"starred": row["starred"]}


async def toggle_pin(knowledge_id: UUID, user_id: UUID) -> dict | None:
    """Toggle pin status on a knowledge item."""
    row = await db_pool.fetchrow(
        "UPDATE knowledge SET pinned = NOT COALESCE(pinned, FALSE), updated_at = NOW() "
        "WHERE id = $1 AND user_id = $2 RETURNING pinned",
        knowledge_id,
        str(user_id),
    )
    if not row:
        return None
    return {"pinned": row["pinned"]}
