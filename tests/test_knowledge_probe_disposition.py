"""TL-㉑ 「反馈层测试」4/「test」1 取舍——(标题,内容) 双逐字钉探针对判 A + 删除工具真消费（全 tmp，绝不碰生产库）。

覆盖：4 对探针逐字节命中→A（独立 rule 名）/ 同题真实内容不动（硬反证）/
同内容异题不动 / 旧标题调用面字节不变（契约锁死面）/ 近似内容不算命中 /
单一源常量钉死 / 既有规则不被遮蔽 / 删除工具 scan+apply+recheck 真消费。
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

FB1 = "这是自动提取的知识条目"
FB2 = "这是自动提取的知识条目，用于验证反馈层写入机制"
FB3 = "自动提取的知识条目"
T_CONTENT = "test content"
SELFDECL = "Hello pipeline test file for knowledge import validation 20260919."
FIXTURE_JSON = '{"key": "value", "nested": {"a": 1}}'

PROBE_PAIRS = [
    ("反馈层测试", FB1),
    ("反馈层测试", FB2),
    ("反馈层测试", FB3),
    ("test", T_CONTENT),
]

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


@pytest.mark.parametrize("title,content", PROBE_PAIRS)
def test_probe_pair_exact_bytes_is_a(title, content):
    cls, rule = census.classify_knowledge(title, content)
    assert cls == "A"
    assert rule == "A_KNOWLEDGE_PROBE_TITLE_CONTENT"


def test_probe_twin_real_content_untouched():
    # 硬反证：同题真实用户内容（内容≠探针字面）绝不动（宁窄勿滥）
    assert census.classify_knowledge("反馈层测试", "我的反馈笔记，真实内容。")[0] == "other"
    assert census.classify_knowledge("test", "用户真实测试记录")[0] == "other"


def test_probe_same_content_wrong_title_untouched():
    # 同内容异题=不误判（双逐字钉缺一不可）
    assert census.classify_knowledge("我的知识", FB1)[0] == "other"
    assert census.classify_knowledge("测试内容", T_CONTENT)[0] == "other"


@pytest.mark.parametrize("title", ["反馈层测试", "test"])
def test_probe_title_only_old_surface_byte_identical(title):
    # 契约锁死面：不带 content 的旧调用面语义逐字不变
    assert census.classify_knowledge(title)[0] == "other"


@pytest.mark.parametrize(
    "title,content",
    [
        ("反馈层测试", FB1 + "\n"),
        ("反馈层测试", " " + FB2),
        ("反馈层测试", FB3[:-1]),
        ("test", T_CONTENT + " "),
        ("test", T_CONTENT.upper()),
    ],
)
def test_probe_match_is_byte_exact(title, content):
    # 逐字节严格：近似内容（尾空白/换行/截断/大小写）不算命中
    assert census.classify_knowledge(title, content)[0] == "other"


def test_probe_bytes_pinned():
    # 单一源常量钉死 4 对探针字面（上游改动而词表未跟=本测炸，防双源漂移）
    assert census.A_KNOWLEDGE_PROBE_TITLE_CONTENT == set(PROBE_PAIRS)
    # 刻意独立于 TL-⑱ 夹具/TL-⑳ 自述词表，rule 名不混
    assert census.A_KNOWLEDGE_FIXTURE_CONTENT.get("反馈层测试") is None
    assert census.A_KNOWLEDGE_SELF_DECLARED_CONTENT.get("test") is None


def test_prior_rules_not_shadowed():
    # TL-⑱/⑳ 夹具与自述规则零回归：各自 rule 名独立，互不遮蔽
    assert census.classify_knowledge("[Pipeline] data.json", FIXTURE_JSON) == (
        "A",
        "A_KNOWLEDGE_FIXTURE_CONTENT",
    )
    assert census.classify_knowledge("[Pipeline] pipe_test.txt", SELFDECL) == (
        "A",
        "A_KNOWLEDGE_SELF_DECLARED_CONTENT",
    )
    assert census.classify_knowledge("[Pipeline] pipe_test.txt", FB1)[0] == "other"


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


def test_tool_scan_consumes_probe_content(tmp_path):
    # 若 scan 只传 title（不消费 content），两行会同判 other——A 恰 1 即真消费硬证
    db = tmp_path / "opensoul.db"
    _mk_db(
        db,
        [
            ("k_probe", "反馈层测试", FB2),
            ("k_twin", "反馈层测试", "我的反馈笔记，真实内容。"),
        ],
    )
    conn = sqlite3.connect(str(db))
    try:
        pre = mod.scan(conn)
    finally:
        conn.close()
    assert pre["A_ids"] == ["k_probe"]
    assert pre["other_ids"] == {"k_twin"}


def test_tool_apply_deletes_probe_only_with_cascade(tmp_path):
    db = tmp_path / "opensoul.db"
    art = tmp_path / "census.json"
    _mk_db(
        db,
        [
            ("k_fb", "反馈层测试", FB1),
            ("k_t", "test", T_CONTENT),
            ("k_twin", "反馈层测试", "我的反馈笔记，真实内容。"),
        ],
        chunks=[("c_fb", "k_fb", FB1), ("c_twin", "k_twin", "twin content")],
        links=[("k_fb", "tag-1"), ("k_t", "tag-2"), ("k_twin", "tag-3")],
    )
    _mk_artifact(art, 2)
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
    assert rep["deleted"] == {"knowledge": 2, "chunks": 1, "links": 2}
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
    assert kn == {"k_twin"}  # 同题真实内容一行不动
    assert ch == {"c_twin"}  # 级联只清 A 行块面
    assert lk == {("k_twin", "tag-3")}


def test_recheck_refuses_probe_drift(tmp_path):
    # 删前逐行复核按 (标题,内容) 再判：内容已不是探针字面（被改写/清单陈旧）=拒删
    db = tmp_path / "opensoul.db"
    _mk_db(db, [("k_probe", "反馈层测试", "被改写后的内容")])
    conn = sqlite3.connect(str(db))
    try:
        with pytest.raises(mod.RefuseError, match="现判非 A"):
            mod.recheck(conn, ["k_probe"])
    finally:
        conn.close()
