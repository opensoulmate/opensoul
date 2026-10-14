"""TL-⑳ pipe_test.txt 取舍——自述测试探针内容逐字钉判 A + 删除工具真消费（全 tmp，绝不碰生产库）。

覆盖：自述探针内容逐字节命中→A（独立 rule 名）/ 同名真实内容不动（硬反证）/
旧标题调用面字节不变（契约锁死面）/ 近似内容不算命中 / 标题不匹配不误判 /
单一源常量钉死 / 夹具规则不被遮蔽 / 删除工具 scan+apply+recheck 真消费内容。
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_CENSUS = REPO / "scripts" / "census_test_residue.py"
_spec_c = importlib.util.spec_from_file_location("census_test_residue", _CENSUS)
assert _spec_c is not None and _spec_c.loader is not None
census = importlib.util.module_from_spec(_spec_c)
_spec_c.loader.exec_module(census)

_SCRIPT = REPO / "scripts" / "delete_knowledge_test_residue.py"
_spec = importlib.util.spec_from_file_location("delete_knowledge_test_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

SELFDECL = "Hello pipeline test file for knowledge import validation 20260919."
FIXTURE_JSON = '{"key": "value", "nested": {"a": 1}}'

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
    metadata TEXT DEFAULT '{}')
"""
DDL_CHUNKS = """
CREATE TABLE knowledge_chunks (
    id TEXT PRIMARY KEY,
    knowledge_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding_id TEXT,
    chunk_index INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    token_count INT DEFAULT 0)
"""
DDL_LINKS = """
CREATE TABLE knowledge_tags (
    knowledge_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (knowledge_id, tag_id))
"""


# ---------- 纯函数：分类矩阵 ----------


def test_selfdeclared_exact_bytes_is_a():
    cls, rule = census.classify_knowledge("[Pipeline] pipe_test.txt", SELFDECL)
    assert cls == "A"
    assert rule == "A_KNOWLEDGE_SELF_DECLARED_CONTENT"


def test_selfdeclared_twin_real_content_untouched():
    # 硬反证：同名真实用户上传（内容≠探针字面）绝不动（宁窄勿滥）
    assert (
        census.classify_knowledge("[Pipeline] pipe_test.txt", "我的管道测试笔记，真实内容。")[0]
        == "other"
    )


def test_title_only_old_surface_byte_identical():
    # 契约锁死面：不带 content 的旧调用面语义逐字不变
    assert census.classify_knowledge("[Pipeline] pipe_test.txt")[0] == "other"


@pytest.mark.parametrize(
    "content",
    [SELFDECL + "\n", SELFDECL + " ", " " + SELFDECL, SELFDECL[:-1]],
)
def test_selfdeclared_match_is_byte_exact(content):
    # 逐字节严格：近似内容（尾空白/换行/截断）不算命中
    assert census.classify_knowledge("[Pipeline] pipe_test.txt", content)[0] == "other"


@pytest.mark.parametrize(
    "title",
    ["pipe_test.txt", "[Pipeline] pipe_test.txt.txt", "[Pipeline]", "test"],
)
def test_selfdeclared_wrong_title_untouched(title):
    # 探针内容但标题不是 [Pipeline] pipe_test.txt=不误判（双逐字钉缺一不可）
    assert census.classify_knowledge(title, SELFDECL)[0] == "other"


def test_selfdeclared_bytes_pinned():
    # 单一源常量钉死自述探针字面（上游内容改动而词表未跟=本测炸，防双源漂移）
    assert census.A_KNOWLEDGE_SELF_DECLARED_CONTENT == {"pipe_test.txt": SELFDECL}
    # 与仓内夹具（出处 tests/test_pipeline.py）刻意分源、rule 名不混
    assert census.A_KNOWLEDGE_FIXTURE_CONTENT.get("pipe_test.txt") is None


def test_fixture_rules_not_shadowed():
    # TL-⑱ 夹具规则零回归：各自 rule 名独立，互不遮蔽
    cls, rule = census.classify_knowledge("[Pipeline] data.json", FIXTURE_JSON)
    assert (cls, rule) == ("A", "A_KNOWLEDGE_FIXTURE_CONTENT")
    assert census.classify_knowledge("[Pipeline] notes.md", SELFDECL)[0] == "other"


# ---------- 删除工具真消费内容（写了≠接线了） ----------


def _mk_db(path: Path, rows, chunks=(), links=()) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    conn.executescript(DDL_CHUNKS)
    conn.executescript(DDL_LINKS)
    for kid, title, content in rows:
        conn.execute(
            "INSERT INTO knowledge(id,user_id,title,content) VALUES(?,?,?,?)",
            (kid, "u1", title, content),
        )
    for cid, kid, content in chunks:
        conn.execute(
            "INSERT INTO knowledge_chunks(id,knowledge_id,content) VALUES(?,?,?)",
            (cid, kid, content),
        )
    for kid, tag in links:
        conn.execute("INSERT INTO knowledge_tags(knowledge_id,tag_id) VALUES(?,?)", (kid, tag))
    conn.commit()
    conn.close()


def _mk_artifact(path: Path, a: int) -> None:
    path.write_text('{"sqlite": {"opensoul_knowledge": {"A": %d, "B": 0, "A_titles": {}}}}' % a)


def test_tool_scan_consumes_selfdeclared_content(tmp_path):
    # 若 scan 只传 title（不消费 content），两行会同判 other——A 恰 1 即真消费硬证
    db = tmp_path / "opensoul.db"
    _mk_db(
        db,
        [
            ("k_self", "[Pipeline] pipe_test.txt", SELFDECL),
            ("k_twin", "[Pipeline] pipe_test.txt", "我的管道测试笔记，真实内容。"),
        ],
    )
    conn = sqlite3.connect(str(db))
    try:
        pre = mod.scan(conn)
    finally:
        conn.close()
    assert pre["A_ids"] == ["k_self"]
    assert pre["other_ids"] == {"k_twin"}


def test_tool_apply_deletes_selfdeclared_only_with_cascade(tmp_path):
    db = tmp_path / "opensoul.db"
    art = tmp_path / "census.json"
    _mk_db(
        db,
        [
            ("k_self", "[Pipeline] pipe_test.txt", SELFDECL),
            ("k_twin", "[Pipeline] pipe_test.txt", "我的管道测试笔记，真实内容。"),
        ],
        chunks=[("c_self", "k_self", SELFDECL), ("c_twin", "k_twin", "twin content")],
        links=[("k_self", "tag-1"), ("k_twin", "tag-2")],
    )
    _mk_artifact(art, 1)
    rep = mod.run(
        db,
        art,
        apply=True,
        backup_dir=tmp_path / "bk",
        report_path=tmp_path / "report.json",
        meili_url="http://127.0.0.1:7700",
        meili_key="k",
        meili_index="opensoul_knowledge",
    )
    assert rep["deleted"] == {"knowledge": 1, "chunks": 1, "links": 1}
    assert rep["post_A_ids_not_in_deleted"] == []
    assert rep["non_A_missing"] == []
    conn = sqlite3.connect(str(db))
    try:
        kn = {r[0] for r in conn.execute("SELECT id FROM knowledge").fetchall()}
        ch = {r[0] for r in conn.execute("SELECT id FROM knowledge_chunks").fetchall()}
        lk = {
            (r[0], r[1])
            for r in conn.execute("SELECT knowledge_id,tag_id FROM knowledge_tags").fetchall()
        }
    finally:
        conn.close()
    assert kn == {"k_twin"}  # 同名真实内容一行不动
    assert ch == {"c_twin"}  # 级联只清 A 行块面
    assert lk == {("k_twin", "tag-2")}


def test_recheck_refuses_selfdeclared_drift(tmp_path):
    # 删前逐行复核按内容再判：清单行内容已不是探针字面（被改写/清单陈旧）=拒删
    db = tmp_path / "opensoul.db"
    _mk_db(db, [("k_self", "[Pipeline] pipe_test.txt", "被改写后的内容")])
    conn = sqlite3.connect(str(db))
    try:
        with pytest.raises(mod.RefuseError, match="现判非 A"):
            mod.recheck(conn, ["k_self"])
    finally:
        conn.close()
