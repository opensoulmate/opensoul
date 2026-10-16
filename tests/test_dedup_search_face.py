"""H1-① 去重删除清检索面收口——dedup 删除走主删除面 delete_knowledge 唯一删除路径。

修前（H0 T1 源码级复核证实，控制组实证）：services/deduplication.py 删除循环自开
第二套删除路径只删 knowledge/knowledge_tags/knowledge_chunks 三表行，不清 meili
全文索引 doc 也不删 qdrant 向量点，而 services/search.py fulltext_search/
semantic_search 直接回索引命中不回查 DB → 去重删掉的条目仍可被全文/语义搜索命中
（幽灵搜索结果）。主删除面 services/knowledge.py delete_knowledge 两面都清=对比
鲜明；第二套删除路径=漂移根因，收口为同一路径（检索面清理+三表级联随行）。

覆盖：检索面真清（meili doc id 逐值+qdrant embedding_id 逐值，NULL 向量行不
fabricate 删除）/保留条目检索面零触碰/三表级联 parity/检索面失败=不删禁造幽灵
（与主删除面同语义）+计数如实/行缺失不计 removed/委托真调用（单一路径接线）/
单一删除路径静态锁死（禁第二套删除路径复发）/结果契约逐键不变/保留最早语义零
变化/跨用户不去重语义零变化。

数据面全 tmp（SQLitePool tmp 库），检索面=spy（AVAILABLE=True 真进清理分支）。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

import src.services.deduplication as dedup_mod
import src.services.knowledge as knowledge_mod
from src.database.postgres import SQLitePool

DDL = """
CREATE TABLE knowledge (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    source TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_type TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}');
CREATE TABLE knowledge_chunks (
    id TEXT PRIMARY KEY,
    knowledge_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding_id TEXT,
    chunk_index INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    token_count INT DEFAULT 0);
CREATE TABLE knowledge_tags (
    knowledge_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (knowledge_id, tag_id));
"""


class _MeiliSpy:
    """meili 检索面 spy：AVAILABLE=True 让主删除面真进清理分支，逐值记账。"""

    def __init__(self, fail: bool = False):
        self.AVAILABLE = True
        self.fail = fail
        self.deleted: list[str] = []

    def delete_document(self, doc_id: str):
        if self.fail:
            raise RuntimeError("meili unavailable (simulated)")
        self.deleted.append(doc_id)


class _QdrantSpy:
    """qdrant 检索面 spy：delete_points 逐批记账（不 fabricate 空批）。"""

    def __init__(self):
        self.AVAILABLE = True
        self.deleted: list[list[str]] = []

    def delete_points(self, ids: list[str]):
        self.deleted.append(list(ids))


def _seed(conn: sqlite3.Connection, rows, chunks, links) -> None:
    for rid, uid, title, content, created in rows:
        conn.execute(
            "INSERT INTO knowledge (id, user_id, title, content, created_at) VALUES (?,?,?,?,?)",
            (rid, uid, title, content, created),
        )
    for cid, kid, content, emb in chunks:
        conn.execute(
            "INSERT INTO knowledge_chunks (id, knowledge_id, content, embedding_id) VALUES (?,?,?,?)",
            (cid, kid, content, emb),
        )
    for kid, tid in links:
        conn.execute("INSERT INTO knowledge_tags (knowledge_id, tag_id) VALUES (?,?)", (kid, tid))
    conn.commit()


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    db = tmp_path / "opensoul.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(DDL)
    pool = SQLitePool(db_path=str(db))
    meili = _MeiliSpy()
    qdrant = _QdrantSpy()
    monkeypatch.setattr(dedup_mod, "db_pool", pool)
    monkeypatch.setattr(knowledge_mod, "db_pool", pool)
    monkeypatch.setattr(knowledge_mod, "meili_client", meili)
    monkeypatch.setattr(knowledge_mod, "qdrant_client", qdrant)
    return {"db": db, "pool": pool, "meili": meili, "qdrant": qdrant}


def _run_dedup(env, user_id=None) -> dict:
    async def _go():
        await env["pool"].connect()
        try:
            return await dedup_mod.deduplicate_knowledge(user_id)
        finally:
            await env["pool"].disconnect()

    return asyncio.run(_go())


def _rows(env, sql: str, params=()) -> list[dict]:
    with sqlite3.connect(env["db"]) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _seed_two_dupes(env):
    """同用户两条完全相同（sim=1.0≥0.85）+一条无关行；dup 带 2 个真向量行+1 个
    NULL 向量行（embedding 降级形态：不写 qdrant、删时不得 fabricate 删除）。"""
    with sqlite3.connect(env["db"]) as conn:
        _seed(
            conn,
            rows=[
                ("k_keep", "u1", "季度报告", "完全一致的正文内容。", "2026-01-01 00:00:00"),
                ("k_dup", "u1", "季度报告", "完全一致的正文内容。", "2026-01-02 00:00:00"),
                (
                    "k_other",
                    "u1",
                    "毫不相关的主题",
                    "另一段完全不同的文本内容。",
                    "2026-01-03 00:00:00",
                ),
            ],
            chunks=[
                ("c_keep", "k_keep", "chunk", "e_keep"),
                ("c_dup1", "k_dup", "chunk", "e_dup1"),
                ("c_dup2", "k_dup", "chunk", "e_dup2"),
                ("c_dup3", "k_dup", "chunk", None),
            ],
            links=[("k_keep", "t1"), ("k_dup", "t1"), ("k_other", "t1")],
        )


class TestSearchFaceCleared:
    def test_dedup_clears_meili_and_qdrant(self, env):
        """去重删除真清检索面（修前：meili/qdrant 零触达=幽灵搜索结果）。"""
        _seed_two_dupes(env)
        result = _run_dedup(env)
        assert result["duplicates_found"] == 1
        assert result["removed_ids"] == ["k_dup"]
        # meili 全文面：doc id=knowledge id（meili_indexer._row_to_doc 同键）
        assert env["meili"].deleted == ["k_dup"]
        # qdrant 向量面：按行记录的 embedding_id 逐值删；NULL 向量行不 fabricate
        flat = [i for batch in env["qdrant"].deleted for i in batch]
        assert sorted(flat) == ["e_dup1", "e_dup2"]

    def test_kept_rows_search_faces_untouched(self, env):
        """保留条目检索面零触碰（清理只及被删条目）。"""
        _seed_two_dupes(env)
        _run_dedup(env)
        flat = [i for batch in env["qdrant"].deleted for i in batch]
        assert "e_keep" not in flat
        assert "k_keep" not in env["meili"].deleted
        assert "k_other" not in env["meili"].deleted

    def test_cascade_three_faces_parity(self, env):
        """三表级联 parity（与主删除面同终点）：被删条目三表全清、保留条目一行不动。"""
        _seed_two_dupes(env)
        _run_dedup(env)
        assert _rows(env, "SELECT id FROM knowledge ORDER BY id") == [
            {"id": "k_keep"},
            {"id": "k_other"},
        ]
        assert _rows(env, "SELECT id FROM knowledge_chunks ORDER BY id") == [{"id": "c_keep"}]
        assert _rows(env, "SELECT knowledge_id FROM knowledge_tags ORDER BY knowledge_id") == [
            {"knowledge_id": "k_keep"},
            {"knowledge_id": "k_other"},
        ]


class TestFailureSemantics:
    def test_index_failure_blocks_removal_no_ghost(self, env, caplog):
        """检索面清理失败=该条不删（与主删除面同语义）：宁留重复行禁造幽灵命中。
        修前：行照删而检索面不清=幽灵命中（本拍根因）。"""
        _seed_two_dupes(env)
        env["meili"].fail = True
        with caplog.at_level("WARNING"):
            result = _run_dedup(env)
        assert result["duplicates_found"] == 1
        assert result["removed_ids"] == []  # 计数如实，禁谎报 removed
        # 行全在（未半删）
        assert [r["id"] for r in _rows(env, "SELECT id FROM knowledge ORDER BY id")] == [
            "k_dup",
            "k_keep",
            "k_other",
        ]
        assert len(_rows(env, "SELECT id FROM knowledge_chunks WHERE knowledge_id='k_dup'")) == 3
        assert any("k_dup" in r.message for r in caplog.records)

    def test_delete_false_not_counted(self, env, monkeypatch, caplog):
        """主删除面返回 False（行缺失/归属闸）不计 removed，计数如实禁谎报。"""
        _seed_two_dupes(env)

        async def _no_delete(knowledge_id, user_id):
            return False

        monkeypatch.setattr(dedup_mod, "delete_knowledge", _no_delete)
        with caplog.at_level("WARNING"):
            result = _run_dedup(env)
        assert result["duplicates_found"] == 1
        assert result["removed_ids"] == []
        assert any("k_dup" in r.message for r in caplog.records)

    def test_delete_exception_not_counted(self, env, monkeypatch, caplog):
        """主删除面异常：该条跳过（既有旁路语义）不计 removed。"""
        _seed_two_dupes(env)

        async def _boom(knowledge_id, user_id):
            raise RuntimeError("db down")

        monkeypatch.setattr(dedup_mod, "delete_knowledge", _boom)
        with caplog.at_level("WARNING"):
            result = _run_dedup(env)
        assert result["removed_ids"] == []
        assert any("Failed to remove duplicate" in r.message for r in caplog.records)


class TestSingleDeletionPath:
    def test_delegates_to_main_delete_path(self, env, monkeypatch):
        """委托真调用：删除经主删除面 delete_knowledge（行原值 id+行归属 user_id）。"""
        _seed_two_dupes(env)
        calls = []

        async def _recorder(knowledge_id, user_id):
            calls.append((knowledge_id, user_id))
            return True

        monkeypatch.setattr(dedup_mod, "delete_knowledge", _recorder)
        result = _run_dedup(env)
        assert result["removed_ids"] == ["k_dup"]
        assert calls == [("k_dup", "u1")]  # 行原值直通（SQLite UUID 参数 str() 铁律）

    def test_no_second_delete_path_static(self):
        """静态锁死：deduplication 不得再持有任何 DELETE 语句（第二套删除路径
        =漂移根因，H0 T1 真缺口来源）。删除语句只许存在于主删除面。"""
        src = Path(dedup_mod.__file__).read_text(encoding="utf-8")
        assert "DELETE FROM" not in src, "第二套删除路径复发：deduplication 又持有 DELETE 语句"
        assert "delete_knowledge" in src, "去重删除必须委托主删除面 delete_knowledge"
        main_src = Path(knowledge_mod.__file__).read_text(encoding="utf-8")
        # 主删除面仍是唯一删除路径持有者（级联三表+检索面）
        assert "DELETE FROM knowledge WHERE id" in main_src
        assert "meili_client.delete_document" in main_src
        assert "qdrant_client.delete_points" in main_src


class TestExistingContract:
    def test_result_keys_unchanged(self, env):
        """结果契约逐键不变（加性键都算契约漂移——本拍零新增）。"""
        _seed_two_dupes(env)
        result = _run_dedup(env)
        assert set(result.keys()) == {
            "total",
            "duplicates_found",
            "duplicates_removed",
            "removed_ids",
            "details",
        }
        assert result["duplicates_removed"] == 1
        assert result["details"][0]["duplicate_of"] == "k_keep"

    def test_keep_earliest_semantics(self, env):
        """保留最早语义零变化：同用户 3 条相同→删后两条、最早留。"""
        with sqlite3.connect(env["db"]) as conn:
            _seed(
                conn,
                rows=[
                    (f"k{i}", "u1", "同一标题", "同一内容。", f"2026-01-0{i} 00:00:00")
                    for i in range(1, 4)
                ],
                chunks=[(f"c{i}", f"k{i}", "chunk", f"e{i}") for i in range(1, 4)],
                links=[],
            )
        result = _run_dedup(env)
        assert result["removed_ids"] == ["k2", "k3"]
        assert [r["id"] for r in _rows(env, "SELECT id FROM knowledge")] == ["k1"]
        flat = [i for batch in env["qdrant"].deleted for i in batch]
        assert sorted(flat) == ["e2", "e3"]

    def test_cross_user_not_deduped(self, env):
        """跨用户不去重（既有语义零变化）：同文不同用户=各自保留，检索面零触达。"""
        with sqlite3.connect(env["db"]) as conn:
            _seed(
                conn,
                rows=[
                    ("k_a", "u1", "同一标题", "同一内容。", "2026-01-01 00:00:00"),
                    ("k_b", "u2", "同一标题", "同一内容。", "2026-01-02 00:00:00"),
                ],
                chunks=[],
                links=[],
            )
        result = _run_dedup(env)
        assert result["duplicates_found"] == 0
        assert result["removed_ids"] == []
        assert env["meili"].deleted == []
        assert env["qdrant"].deleted == []

    def test_single_row_short_circuit(self, env):
        """≤1 行短路契约（既有）：不触达任何删除面。"""
        with sqlite3.connect(env["db"]) as conn:
            _seed(
                conn,
                rows=[("k_only", "u1", "t", "c", "2026-01-01 00:00:00")],
                chunks=[],
                links=[],
            )
        result = _run_dedup(env)
        assert result == {
            "total": 1,
            "duplicates_found": 0,
            "duplicates_removed": 0,
            "removed_ids": [],
        }
        assert env["meili"].deleted == []
        assert env["qdrant"].deleted == []
