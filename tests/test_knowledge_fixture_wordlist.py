"""TL-⑱ knowledge 夹具词表扩充——标题+内容双逐字钉判 A（全 tmp，绝不碰生产库）。

覆盖：夹具内容逐字节命中→A / 同名真实内容不动（硬反证）/ 旧标题调用面字节不变
（契约锁死面）/ 非夹具标题不误判 / 前缀规则优先级 / 内容匹配逐字节严格 /
夹具字节钉 tests/test_pipeline.py 出处 / 删除工具真消费内容（scan+apply+recheck）。
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

FIXTURE_JSON = '{"key": "value", "nested": {"a": 1}}'
FIXTURE_MD = "# Title\n\nSome content here with enough text for processing."

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


@pytest.mark.parametrize(
    "title,content",
    [("[Pipeline] data.json", FIXTURE_JSON), ("[Pipeline] notes.md", FIXTURE_MD)],
)
def test_fixture_double_verbatim_is_a(title, content):
    cls, rule = census.classify_knowledge(title, content)
    assert cls == "A" and rule == "A_KNOWLEDGE_FIXTURE_CONTENT"


@pytest.mark.parametrize(
    "title,content",
    [
        ("[Pipeline] data.json", '{"key": "user-real-data"}'),
        ("[Pipeline] notes.md", "# 我的笔记\n\n真实用户内容。"),
    ],
)
def test_same_title_real_content_untouched(title, content):
    # 硬反证：同名真实用户上传（内容≠夹具）绝不动（宁窄勿滥）
    assert census.classify_knowledge(title, content)[0] == "other"


def test_title_only_old_surface_byte_identical():
    # 契约锁死面：不带 content 的旧调用面语义逐字不变
    # （tests/test_knowledge_residue_delete.py:158 锁死的「notes.md=other」仍在）
    assert census.classify_knowledge("[Pipeline] notes.md")[0] == "other"
    assert census.classify_knowledge("[Pipeline] data.json")[0] == "other"
    assert census.classify_knowledge("[Pipeline] test.txt")[0] == "A"
    assert census.classify_knowledge("[Pipeline] dup1.txt")[0] == "A"
    assert census.classify_knowledge("[Pipeline] readme.md")[0] == "B"
    assert census.classify_knowledge("test")[0] == "other"


@pytest.mark.parametrize(
    "title",
    ["notes.md", "[Pipeline] other.txt", "[Pipeline]", "[Pipeline] notes.md.txt"],
)
def test_fixture_content_wrong_title_untouched(title):
    # 夹具内容但标题不是 [Pipeline] 夹具文件名=不误判（双逐字钉缺一不可）
    assert census.classify_knowledge(title, FIXTURE_MD)[0] == "other"
    assert census.classify_knowledge(title, FIXTURE_JSON)[0] == "other"


def test_prefix_rules_precedence():
    # 既有前缀词表优先级不变（先判前缀再判内容）
    assert census.classify_knowledge("[Pipeline] readme.md", FIXTURE_MD)[0] == "B"
    assert census.classify_knowledge("[Pipeline] test.txt", FIXTURE_JSON)[0] == "A"


@pytest.mark.parametrize(
    "content",
    [FIXTURE_JSON + "\n", FIXTURE_JSON + " ", " " + FIXTURE_MD, FIXTURE_MD[:-1]],
)
def test_content_match_is_byte_exact(content):
    # 逐字节严格：近似内容（尾空白/换行/截断）不算命中
    assert census.classify_knowledge("[Pipeline] data.json", content)[0] == "other"
    assert census.classify_knowledge("[Pipeline] notes.md", content)[0] == "other"


def test_fixture_bytes_pinned_to_source():
    # 单一源夹具字节必须钉死 tests/test_pipeline.py 上传夹具（出处钉死）：
    # 上游夹具改动而词表未跟=本测炸（防双源漂移）。
    # 源码形态：data.json 由 json.dumps({"key": "value", ...}) 产出（dict 字面即产出字节）、
    # notes.md 以 b"# Title\\n\\n..." 字节字面给出（源码内为转义形）。
    src = (REPO / "tests" / "test_pipeline.py").read_text()
    assert census.A_KNOWLEDGE_FIXTURE_CONTENT == {
        "data.json": FIXTURE_JSON,
        "notes.md": FIXTURE_MD,
    }
    assert FIXTURE_JSON in src
    assert FIXTURE_MD.replace("\n", "\\n") in src


# ---------- 删除工具真消费内容（写了≠接线了） ----------


def _mk_db(path: Path, rows) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    conn.executescript(DDL_CHUNKS)
    conn.executescript(DDL_LINKS)
    for kid, title, content in rows:
        conn.execute(
            "INSERT INTO knowledge(id,user_id,title,content) VALUES(?,?,?,?)",
            (kid, "u1", title, content),
        )
    conn.commit()
    conn.close()


def _mk_artifact(path: Path, a: int) -> None:
    path.write_text('{"sqlite": {"opensoul_knowledge": {"A": %d, "B": 0, "A_titles": {}}}}' % a)


def test_tool_scan_consumes_content(tmp_path):
    # 若 scan 只传 title（不消费 content），两行会同判 other——A=1 即真消费硬证
    db = tmp_path / "opensoul.db"
    _mk_db(
        db,
        [
            ("k_fix", "[Pipeline] data.json", FIXTURE_JSON),
            ("k_twin", "[Pipeline] data.json", '{"key": "user-real-data"}'),
        ],
    )
    conn = sqlite3.connect(str(db))
    try:
        pre = mod.scan(conn)
    finally:
        conn.close()
    assert pre["A_ids"] == ["k_fix"]
    assert pre["other_ids"] == {"k_twin"}
    assert pre["B"] == 0


def test_tool_apply_deletes_content_a_only(tmp_path):
    db = tmp_path / "opensoul.db"
    art = tmp_path / "census.json"
    _mk_db(
        db,
        [
            ("k_fix", "[Pipeline] data.json", FIXTURE_JSON),
            ("k_notes", "[Pipeline] notes.md", FIXTURE_MD),
            ("k_twin", "[Pipeline] notes.md", "# 我的笔记\n\n真实用户内容。"),
        ],
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
    assert rep["deleted"]["knowledge"] == 2
    assert rep["post_A_ids_not_in_deleted"] == []
    assert rep["non_A_missing"] == []
    conn = sqlite3.connect(str(db))
    try:
        left = {r[0] for r in conn.execute("SELECT id FROM knowledge").fetchall()}
    finally:
        conn.close()
    assert left == {"k_twin"}  # 同名真实内容一行不动


def test_recheck_refuses_content_drift(tmp_path):
    # 删前逐行复核按内容再判：清单行内容已不是夹具（被改写/清单陈旧）=拒删
    db = tmp_path / "opensoul.db"
    _mk_db(db, [("k_fix", "[Pipeline] data.json", '{"key": "user-real-data"}')])
    conn = sqlite3.connect(str(db))
    try:
        with pytest.raises(mod.RefuseError, match="现判非 A"):
            mod.recheck(conn, ["k_fix"])
    finally:
        conn.close()
