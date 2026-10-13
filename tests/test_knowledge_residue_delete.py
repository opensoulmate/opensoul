"""TL-⑥ 删除执行件②a——knowledge A 类+级联精确删语义（全 tmp，绝不碰生产库）。

覆盖：dry-run 零写入零外部调用 / 级联三面精确删（非 A 含 B 一行不动、tags 本体不动）/
删前逐行复核拒删（现判非 A=一行不动）/ 工件不可信拒删 / 备份快照可回读 /
orphan 不增长 / meili delete-batch 真 socket 桩（请求体=待删 id 全清单）/
meili 失败如实报不阻断 DB 删除。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_knowledge_test_residue.py"
_spec = importlib.util.spec_from_file_location("delete_knowledge_test_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

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

# 行样本：分类判法单一源=census.classify_knowledge
KNOWLEDGE = [
    ("k_a1", "[Pipeline] test.txt"),  # A
    ("k_a2", "[Pipeline] dup1.txt"),  # A
    ("k_b1", "[Pipeline] readme.md"),  # B=疑似，不动
    ("k_o1", "[Pipeline] notes.md"),  # other，不动
    ("k_o2", "反馈层测试"),  # other，不动
]
CHUNKS = [
    ("c_a1", "k_a1"),
    ("c_a2", "k_a1"),
    ("c_a3", "k_a2"),  # A 级联
    ("c_o1", "k_o1"),
    ("c_b1", "k_b1"),  # 非 A 级联
    ("c_orphan", "k_gone"),  # 删前既有 orphan
]
LINKS = [
    ("k_a1", "t1"),
    ("k_a1", "t2"),
    ("k_a2", "t1"),  # A 级联
    ("k_o1", "t1"),
    ("k_b1", "t3"),  # 非 A 级联
    ("k_gone", "t1"),  # 删前既有 orphan
]
A_IDS = ["k_a1", "k_a2"]


def _mk_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    conn.executescript(DDL_CHUNKS)
    conn.executescript(DDL_LINKS)
    for kid, title in KNOWLEDGE:
        conn.execute(
            "INSERT INTO knowledge(id,user_id,title,content) VALUES(?,?,?,?)",
            (kid, "u1", title, "content-" + kid),
        )
    for cid, kid in CHUNKS:
        conn.execute(
            "INSERT INTO knowledge_chunks(id,knowledge_id,content) VALUES(?,?,?)",
            (cid, kid, "chunk-" + cid),
        )
    for kid, tid in LINKS:
        conn.execute("INSERT INTO knowledge_tags(knowledge_id,tag_id) VALUES(?,?)", (kid, tid))
    conn.commit()
    conn.close()


def _mk_artifact(path: Path, a: int = 2, with_key: bool = True) -> None:
    payload: dict = {"sqlite": {}}
    if with_key:
        payload["sqlite"]["opensoul_knowledge"] = {
            "A": a,
            "B": 1,
            "A_titles": {"[Pipeline] test.txt": 1, "[Pipeline] dup1.txt": 1},
        }
    path.write_text(json.dumps(payload))


def _q(path: Path, sql: str) -> list:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


@pytest.fixture()
def scene(tmp_path):
    db = tmp_path / "opensoul.db"
    artifact = tmp_path / "census.json"
    _mk_db(db)
    _mk_artifact(artifact)
    return {
        "db": db,
        "artifact": artifact,
        "backup_dir": tmp_path / "bk",
        "report": tmp_path / "report.json",
    }


def _run(
    scene, apply: bool, meili_url: str = "http://127.0.0.1:7700", artifact: Path | None = None
):
    return mod.run(
        scene["db"],
        artifact or scene["artifact"],
        apply=apply,
        backup_dir=scene["backup_dir"],
        report_path=scene["report"],
        meili_url=meili_url,
        meili_key="k",
        meili_index="opensoul_knowledge",
    )


def test_classify_single_source():
    assert mod.census.classify_knowledge("[Pipeline] test.txt")[0] == "A"
    assert mod.census.classify_knowledge("[Pipeline] dup1.txt")[0] == "A"
    assert mod.census.classify_knowledge("[Pipeline] timeline-test.txt")[0] == "A"
    assert mod.census.classify_knowledge("[Pipeline] readme.md")[0] == "B"
    assert mod.census.classify_knowledge("[Pipeline] notes.md")[0] == "other"
    assert mod.census.classify_knowledge("test")[0] == "other"
    # 工具消费的就是 census 单一源（禁手抄判法）
    assert mod.census.classify_knowledge.__module__.endswith("census_test_residue")


def test_dry_run_zero_writes(scene):
    rep = _run(scene, apply=False)
    assert rep["mode"] == "dry-run"
    assert rep["deleted"] == {"knowledge": 0, "chunks": 0, "links": 0}
    assert rep["live_A"] == 2 and rep["matched"] == 2
    assert rep["meili"]["note"] == "dry-run 零调用"
    # 三面一行不动 + 不建备份
    assert len(_q(scene["db"], "SELECT id FROM knowledge")) == 5
    assert len(_q(scene["db"], "SELECT id FROM knowledge_chunks")) == 6
    assert len(_q(scene["db"], "SELECT * FROM knowledge_tags")) == 6
    assert not scene["backup_dir"].exists()


def test_apply_cascade_exact(scene):
    rep = _run(scene, apply=True)
    assert rep["deleted"] == {"knowledge": 2, "chunks": 3, "links": 3}
    # A 三面全清
    assert _q(scene["db"], "SELECT id FROM knowledge WHERE id LIKE 'k_a%'") == []
    assert _q(scene["db"], "SELECT id FROM knowledge_chunks WHERE id LIKE 'c_a%'") == []
    assert _q(scene["db"], "SELECT * FROM knowledge_tags WHERE knowledge_id LIKE 'k_a%'") == []
    # 非 A（含 B）一行不动、tags 本体不动、orphan 不动不增长
    assert {r[0] for r in _q(scene["db"], "SELECT id FROM knowledge")} == {"k_b1", "k_o1", "k_o2"}
    assert {r[0] for r in _q(scene["db"], "SELECT id FROM knowledge_chunks")} == {
        "c_o1",
        "c_b1",
        "c_orphan",
    }
    assert {tuple(r) for r in _q(scene["db"], "SELECT * FROM knowledge_tags")} == {
        ("k_o1", "t1"),
        ("k_b1", "t3"),
        ("k_gone", "t1"),
    }
    assert rep["orphan_chunks"] == {"pre": 1, "post": 1}
    assert rep["orphan_links"] == {"pre": 1, "post": 1}
    assert rep["post"]["A"] == 0 and rep["post"]["other"] == 3
    assert rep["non_A_missing"] == []
    assert rep["post_A_ids_not_in_deleted"] == []


def test_refuse_when_row_now_not_A(scene, monkeypatch):
    # 删前逐行复核：scan 后 fresh 重读判非 A → 拒删一行不动
    real = mod.census.classify_knowledge
    calls = {"n": 0}

    def fake(title):
        calls["n"] += 1
        if calls["n"] > len(KNOWLEDGE):  # scan 5 次之后=复核段
            return "other", "flipped"
        return real(title)

    monkeypatch.setattr(mod.census, "classify_knowledge", fake)
    with pytest.raises(mod.RefuseError, match="现判非 A"):
        _run(scene, apply=True)
    assert len(_q(scene["db"], "SELECT id FROM knowledge")) == 5
    assert len(_q(scene["db"], "SELECT id FROM knowledge_chunks")) == 6
    assert not scene["backup_dir"].exists()


def test_refuse_when_artifact_untrusted(scene):
    _mk_artifact(scene["artifact"], with_key=False)
    with pytest.raises(mod.RefuseError, match="工件不可信"):
        _run(scene, apply=True)
    assert len(_q(scene["db"], "SELECT id FROM knowledge")) == 5


def test_artifact_drift_reported_not_silent(scene):
    _mk_artifact(scene["artifact"], a=99)  # 工件计数与 live 不符
    rep = _run(scene, apply=True)
    assert rep["artifact_A"] == 99 and rep["live_A"] == 2
    assert rep["artifact_drift"] is True
    assert rep["deleted"]["knowledge"] == 2


def test_backup_snapshot_contains_pre_rows(scene):
    rep = _run(scene, apply=True)
    backup = Path(rep["backup"]["path"])
    assert backup.exists()
    assert len(rep["backup"]["sha256"]) == 64
    # 备份=删前全库快照（可整体回滚）
    assert len(_q(backup, "SELECT id FROM knowledge")) == 5
    assert len(_q(backup, "SELECT id FROM knowledge_chunks")) == 6
    assert len(_q(scene["db"], "SELECT id FROM knowledge")) != len(
        _q(backup, "SELECT id FROM knowledge")
    )


def test_report_artifact_written(scene):
    rep = _run(scene, apply=True)
    on_disk = json.loads(scene["report"].read_text())
    assert on_disk["deleted"] == rep["deleted"] == {"knowledge": 2, "chunks": 3, "links": 3}
    assert on_disk["pre"] == {
        "knowledge_total": 5,
        "A": 2,
        "other": 3,
        "B": 1,
        "chunks_A": 3,
        "chunks_other": 3,
        "links_A": 3,
        "links_other": 3,
    }
    assert on_disk["backup"]["path"] == rep["backup"]["path"]
    assert on_disk["deleted_ids"] == A_IDS


class _MeiliStub:
    """真 socket Meilisearch 桩：记录 delete-batch 请求，任务回 succeeded。"""

    def __init__(self):
        self.requests: list[tuple[str, str, str | None, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append(
                    (self.command, self.path, self.headers.get("Authorization"), body.decode())
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"taskUid": 1}')

            def do_GET(self):
                outer.requests.append(
                    (self.command, self.path, self.headers.get("Authorization"), "")
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "succeeded"}')

            def log_message(self, *args):  # 静默
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_meili_delete_batch_stub(scene):
    stub = _MeiliStub()
    try:
        rep = _run(scene, apply=True, meili_url=stub.url)
    finally:
        stub.close()
    assert rep["meili"]["ok"] is True
    assert rep["meili"]["task_status"] == "succeeded"
    assert rep["meili"]["attempted"] == 2
    batch = [r for r in stub.requests if "delete-batch" in r[1]]
    assert len(batch) == 1
    # 请求体=待删 id 全清单（handler 真回读）、鉴权头真送达
    assert batch[0][2] == "Bearer k"
    assert json.loads(batch[0][3]) == A_IDS
    # task 轮询同样必须带鉴权头（曾漏带→401，验证步自伤）
    polls = [r for r in stub.requests if r[1].startswith("/tasks/")]
    assert polls and all(p[2] == "Bearer k" for p in polls)


def test_meili_dry_run_no_call(scene):
    stub = _MeiliStub()
    try:
        rep = _run(scene, apply=False, meili_url=stub.url)
    finally:
        stub.close()
    assert rep["meili"]["attempted"] == 0
    assert stub.requests == []


def test_meili_failure_reported_not_blocking(scene):
    rep = _run(scene, apply=True, meili_url="http://127.0.0.1:9")
    assert rep["meili"]["ok"] is False
    assert "error" in rep["meili"] and rep["meili"]["error"]
    # 外部索引失败不阻断 DB 删除（禁静默：失败如实进报告）
    assert rep["deleted"]["knowledge"] == 2
    assert len(_q(scene["db"], "SELECT id FROM knowledge")) == 3
