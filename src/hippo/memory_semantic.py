"""MEM-① /recall 语义检索面——embedding 相似度排序（F3b-㉜ 边界①留档件
「字面子串匹配非语义检索……语义检索另拍」执行件）。

加性契约：mode=keyword（缺省）行为字节零变化（F3b-㉜ 契约逐字）；mode=semantic=
向量相似度排序（cosine 降序，limit 在排序后截断——与 keyword「过滤先于截断」
同一纪律：排名先于截断，top-N 在全候选排序上取）。

依赖 src/services/embedding 既有抽象（provider 由 EMBEDDING_* 配置，与
knowledge 语义检索同源）。诚实边界：
- query 向量失败（embedding 上游不可达/未配置/超时）=degraded，调用方
  （api/brain /recall）如实 503 禁静默降级成关键词结果（F4b-② 纪律：客户端
  要语义被静默给关键词=「想要流式被静默降级」同型）；
- 行向量失败（get_embeddings_batch 既有「永不 raise、空向量=失败」降级契约）
  =该行 unranked 如实计数：禁 fabricate 相似度、禁静默丢行不报数；
- 行向量查询期懒计算+缓存（Memory.embedding），内容更新即失效（store.update
  失效缓存防陈旧向量）；首次语义查询为无缓存行付批量 embedding 成本（如实记）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.hippo.memory_store import Memory, MemoryStore
from src.services.embedding import get_embedding, get_embeddings_batch


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """cosine 相似度纯函数。

    零范数向量→0.0 如实（禁除零炸也禁 fabricate 1.0）；维度不齐→ValueError
    （zip 静默截断=假相似度，禁——失败必须可见）。
    """
    if len(a) != len(b):
        raise ValueError(f"vector dimension mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass
class SemanticRank:
    """semantic_rank 产出：排序结果+可观测计数（unranked/degraded 如实禁谎报）。"""

    ranked: list[tuple[Memory, float]] = field(default_factory=list)
    candidates: int = 0  # 进入排序前的候选行数（过滤后、排名前）
    embedded: int = 0  # 真参与排序的行数（缓存命中+本查询新算）
    unranked: int = 0  # 行向量缺失/失败不参与排序（如实计数禁静默丢）
    degraded: bool = False  # query 向量失败=整体不可用（调用方 503）
    reason: str | None = None


async def semantic_rank(
    query: str,
    rows: list[Memory],
    store: MemoryStore | None = None,
) -> SemanticRank:
    """按 query 语义相似度给 rows 排序（cosine 降序；tie-break=retention 降序
    →memory_id 升序=确定性，禁 dict 序不确定性）。

    - query 向量：get_embedding（失败→degraded+reason，调用方 503 禁静默降级）；
    - 行向量：缓存命中直用；缺失行批量 get_embeddings_batch（空向量=失败→
      unranked），成功向量回填缓存（store 在场=apply_embeddings 锁内落行）。
    """
    result = SemanticRank(candidates=len(rows))
    if not rows:
        return result
    try:
        query_vec = await get_embedding(query)
    except Exception as exc:  # 上游任意失败=语义检索不可用，如实归口
        result.degraded = True
        result.reason = f"{type(exc).__name__}: {exc}"
        return result

    missing = [m for m in rows if not m.embedding]
    to_apply: dict[str, list[float]] = {}
    if missing:
        vecs = await get_embeddings_batch([m.content for m in missing])
        for mem, vec in zip(missing, vecs):
            if vec:
                to_apply[mem.memory_id] = vec
        if to_apply:
            if store is not None:
                store.apply_embeddings(to_apply)
            for mem in missing:
                if mem.memory_id in to_apply and not mem.embedding:
                    mem.embedding = list(to_apply[mem.memory_id])

    scored: list[tuple[Memory, float]] = []
    for mem in rows:
        if not mem.embedding:
            result.unranked += 1
            continue
        scored.append((mem, cosine_similarity(query_vec, mem.embedding)))
    scored.sort(key=lambda t: (-t[1], -t[0].retention, t[0].memory_id))
    result.ranked = scored
    result.embedded = len(scored)
    return result
