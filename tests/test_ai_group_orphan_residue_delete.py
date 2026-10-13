"""TL-⑫ ai_groups 悬挂残留行删除——按清单精确删 + 备份 + 回读（全 tmp，绝不碰
生产库）。

覆盖：分类词表单一源（census.classify_orphan_group 运行时真消费）/ 组 id 硬标识
（exact+prefix）/ 行文本探针字面 / 宁窄勿滥（泛化短语不误杀）/ dry-run 零写入 /
apply 只删 A 悬挂组行（无硬标识悬挂行+活组行一行不动）/ 删前逐组复核拒删
（现判非 A / 组行回场）/ 工件缺键拒删 / 清单词表漂移拒删 / 清单缺组非致命幂等 /
清单外同判据新残留一并删单列 / 备份快照 sha256 可回读 / 总数算术对账。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_ai_group_orphan_residue.py"
_spec = importlib.util.spec_from_file_location("delete_ai_group_orphan_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

census = mod.census  # 分类词表单一源（脚本同源 import，禁第二套词表）

SCAN_TABLES = census.ORPHAN_SCAN_TABLES


def _seed(db: Path) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE ai_groups (id TEXT PRIMARY KEY, name TEXT, description TEXT,"
        " status TEXT, created_at TEXT, tenant_id TEXT NOT NULL DEFAULT 'default')"
    )
    conn.execute(
        "CREATE TABLE ai_group_agents (id TEXT PRIMARY KEY, group_id TEXT,"
        " agent_id TEXT, name TEXT, role TEXT, model TEXT, status TEXT,"
        " temperature REAL, capabilities TEXT,"
        " tenant_id TEXT NOT NULL DEFAULT 'default')"
    )
    conn.execute(
        "CREATE TABLE ai_group_tasks (id TEXT PRIMARY KEY, group_id TEXT,"
        " parent_task_id TEXT, goal TEXT, status TEXT,"
        " tenant_id TEXT NOT NULL DEFAULT 'default')"
    )
    conn.execute(
        "CREATE TABLE discussion_messages (id TEXT PRIMARY KEY, group_id TEXT,"
        " task_id TEXT, agent_id TEXT, agent_name TEXT, intent TEXT, content TEXT,"
        " tenant_id TEXT NOT NULL DEFAULT 'default')"
    )
    conn.execute(
        "CREATE TABLE task_execution_events (id TEXT PRIMARY KEY, group_id TEXT,"
        " task_id TEXT, agent_id TEXT, event_type TEXT, content TEXT,"
        " tenant_id TEXT NOT NULL DEFAULT 'default')"
    )
    conn.execute(
        "CREATE TABLE agent_scores (id TEXT PRIMARY KEY, group_id TEXT,"
        " task_id TEXT, scored_agent_id TEXT, scorer_agent_id TEXT, score REAL,"
        " reason TEXT, capability TEXT,"
        " tenant_id TEXT NOT NULL DEFAULT 'default')"
    )
    conn.execute(
        "CREATE TABLE agent_capabilities (id TEXT PRIMARY KEY, group_id TEXT,"
        " agent_id TEXT, capability TEXT, avg_score REAL, task_count INTEGER,"
        " trend TEXT, updated_at TEXT,"
        " tenant_id TEXT NOT NULL DEFAULT 'default')"
    )
    # 悬挂 A 组（行文本探针字面）：消息 2 + 事件 1 + 评分 1
    conn.execute(
        "INSERT INTO discussion_messages (id,group_id,task_id,agent_id,agent_name,"
        "intent,content) VALUES ('m1','orph-a','t1','soulmate','soulmate','result',"
        "'广播探针A→B ACC999')"
    )
    conn.execute(
        "INSERT INTO discussion_messages (id,group_id,task_id,agent_id,agent_name,"
        "intent,content) VALUES ('m2','orph-a','t1','soulmate','SoulMate',"
        "'agent_reply','普通讨论内容')"
    )
    conn.execute(
        "INSERT INTO task_execution_events (id,group_id,task_id,agent_id,event_type,"
        "content) VALUES ('e1','orph-a','t1','soulmate','output','防缓存标记 ACC999')"
    )
    conn.execute(
        "INSERT INTO agent_scores (id,group_id,task_id,scored_agent_id,"
        "scorer_agent_id,score,reason) VALUES ('s1','orph-a','t1','soulmate',"
        "'hermes',6,'结果有说明')"
    )
    # 悬挂 A 组（组 id 硬标识 exact）
    conn.execute(
        "INSERT INTO agent_capabilities (id,group_id,agent_id,capability)"
        " VALUES ('c1','test-group-123','echo','testing')"
    )
    # 悬挂 A 组（组 id 硬标识 prefix）
    conn.execute(
        "INSERT INTO discussion_messages (id,group_id,task_id,agent_id,agent_name,"
        "intent,content) VALUES ('m3','test-broadcast-177','ws_x','user','',"
        "'comment','Hello broadcast test')"
    )
    # 悬挂 other 组（无硬标识：泛化短语刻意不收）——一行不动
    conn.execute(
        "INSERT INTO discussion_messages (id,group_id,task_id,agent_id,agent_name,"
        "intent,content) VALUES ('m4','orph-real','t2','soulmate','soulmate',"
        "'result','请分三句话总结这篇文章的内容')"
    )
    # 活组（组行在场）+ 其子表行——一行不动
    conn.execute("INSERT INTO ai_groups (id,name) VALUES ('g-live','真实运维组')")
    conn.execute(
        "INSERT INTO discussion_messages (id,group_id,task_id,agent_id,agent_name,"
        "intent,content) VALUES ('m5','g-live','t3','soulmate','soulmate',"
        "'result','真实组消息 防缓存标记 ACC111（同文本也绝不误删活组行）')"
    )
    conn.commit()
    conn.close()


def _artifact(path: Path, group_ids) -> Path:
    data = {
        "sqlite": {
            "ai_groups": {
                "orphan_groups_A": [
                    {"group_id": g, "rule": "A_ORPHAN_TEXT_MARKERS"} for g in group_ids
                ]
            }
        }
    }
    p = path / "artifact.json"
    p.write_text(json.dumps(data, ensure_ascii=False))
    return p


@pytest.fixture()
def env(tmp_path):
    db = tmp_path / "ai_groups.db"
    _seed(db)
    art = _artifact(tmp_path, ["orph-a", "test-group-123", "test-broadcast-177"])
    return {"db": db, "art": art, "tmp": tmp_path}


def _count(db, table):
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_classify_word_list_single_source(env, monkeypatch):
    """词表单一源运行时真消费：monkeypatch 词表→分类结果随之变。"""
    assert census.classify_orphan_group("x", ["广播探针A→B"])[0] == "A"
    monkeypatch.setattr(census, "A_ORPHAN_TEXT_MARKERS", ("完全不同词表",))
    monkeypatch.setattr(census, "A_GOAL_MARKERS", ())
    cls, _ = census.classify_orphan_group("x", ["广播探针A→B"])
    assert cls == "other"


def test_classify_id_markers_and_narrow():
    assert census.classify_orphan_group("test-group-123", [""])[1] == "A_ORPHAN_GROUP_ID_EXACT"
    assert census.classify_orphan_group("a1proof", [""])[1] == "A_ORPHAN_GROUP_ID_EXACT"
    assert census.classify_orphan_group("test-broadcast-1", [""])[1] == "A_ORPHAN_GROUP_ID_PREFIX"
    assert census.classify_orphan_group("4a27372f", ["口算17加25的结果"])[0] == "A"
    assert census.classify_orphan_group("f3d87d5f", ["e2e隔离验证任务"])[0] == "A"
    assert (
        census.classify_orphan_group("cb20b48e", ["请分三句话介绍你自己：一句说你是谁"])[0] == "A"
    )


def test_classify_narrow_no_overmatch():
    """宁窄勿滥：泛化短语/无关文本如实 other（禁误杀真用户同款提问）。"""
    assert census.classify_orphan_group("g", ["请分三句话总结这篇文章"])[0] == "other"
    assert census.classify_orphan_group("g", ["介绍一下Python的GIL"])[0] == "other"
    assert census.classify_orphan_group("g", ["真实运维讨论"])[0] == "other"
    assert census.classify_orphan_group("real-group-id", [""])[0] == "other"


def test_dry_run_zero_write(env):
    before = {t: _count(env["db"], t) for t in SCAN_TABLES}
    rep = mod.run(env["art"], apply=False, db_override=env["db"])
    assert rep["mode"] == "dry-run"
    assert rep["deleted"]["total"] == 0
    assert {t: _count(env["db"], t) for t in SCAN_TABLES} == before


def test_apply_deletes_exact_and_keeps_rest(env):
    rep = mod.run(env["art"], apply=True, db_override=env["db"], backup_dir=env["tmp"] / "back")
    assert rep["deleted"]["total"] == 6  # m1,m2,m3,e1,s1,c1
    # 精确删：A 悬挂组行全 gone
    conn = sqlite3.connect(str(env["db"]))
    try:
        left = conn.execute(
            "SELECT id FROM discussion_messages WHERE group_id IN"
            " ('orph-a','test-group-123','test-broadcast-177')"
        ).fetchall()
        assert left == []
        assert conn.execute("SELECT COUNT(*) FROM agent_capabilities").fetchone()[0] == 0
        # 无硬标识悬挂行 + 活组行一行不动（活组行同文本含探针字面也不误删）
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM discussion_messages WHERE id IN ('m4','m5')"
            ).fetchone()[0]
            == 2
        )
    finally:
        conn.close()
    assert rep["post_orphan_groups"]["other"] == ["orph-real"]


def test_apply_totals_reconcile(env):
    rep = mod.run(env["art"], apply=True, db_override=env["db"], backup_dir=env["tmp"] / "back")
    for table, info in rep["post_totals"].items():
        assert info["total_reconcile"]["ok"] is True
    assert rep["post_orphan_groups"]["A"] == 0


def test_recheck_non_a_refuses(env):
    conn = sqlite3.connect(str(env["db"]))
    try:
        with pytest.raises(mod.RefuseError):
            mod.recheck(
                conn, [{"group_id": "orph-real", "rows_by_table": {"discussion_messages": ["m4"]}}]
            )
    finally:
        conn.close()


def test_recheck_group_back_alive_refuses(env):
    conn = sqlite3.connect(str(env["db"]))
    try:
        conn.execute("INSERT INTO ai_groups (id,name) VALUES ('orph-a','回场组')")
        conn.commit()
        with pytest.raises(mod.RefuseError):
            mod.recheck(
                conn, [{"group_id": "orph-a", "rows_by_table": {"discussion_messages": ["m1"]}}]
            )
    finally:
        conn.close()


def test_recheck_missing_rows_nonfatal(env):
    conn = sqlite3.connect(str(env["db"]))
    try:
        out = mod.recheck(
            conn,
            [{"group_id": "orph-a", "rows_by_table": {"discussion_messages": ["m1", "m-gone"]}}],
        )
        assert out["recheck_missing_rows"] == [{"table": "discussion_messages", "id": "m-gone"}]
    finally:
        conn.close()


def test_artifact_missing_key_refuses(env):
    bad = env["tmp"] / "bad.json"
    bad.write_text(json.dumps({"sqlite": {"ai_groups": {"groups": []}}}))
    with pytest.raises(mod.RefuseError):
        mod.run(bad, apply=True, db_override=env["db"], backup_dir=env["tmp"] / "back")


def test_checklist_drift_refuses(env):
    """清单组现判非 A（词表漂移/陈旧清单）=拒删一行不动。"""
    art = _artifact(env["tmp"], ["orph-real"])
    before = {t: _count(env["db"], t) for t in SCAN_TABLES}
    with pytest.raises(mod.RefuseError):
        mod.run(art, apply=True, db_override=env["db"], backup_dir=env["tmp"] / "back")
    assert {t: _count(env["db"], t) for t in SCAN_TABLES} == before


def test_missing_checklist_group_nonfatal_idempotent(env):
    art = _artifact(env["tmp"], ["orph-a", "test-group-123", "test-broadcast-177", "orph-gone"])
    rep = mod.run(art, apply=True, db_override=env["db"], backup_dir=env["tmp"] / "back")
    assert rep["checklist"]["missing_checklist_groups"] == ["orph-gone"]
    # 幂等重跑：零目标如实报，不再炸
    rep2 = mod.run(art, apply=True, db_override=env["db"], backup_dir=env["tmp"] / "back2")
    assert rep2["deleted"]["total"] == 0
    assert rep2["live_A_groups"] == 0


def test_beyond_checklist_deleted_and_reported(env):
    """清单外同判据新残留一并删、单列报告。"""
    art = _artifact(env["tmp"], ["orph-a", "test-group-123"])
    rep = mod.run(art, apply=True, db_override=env["db"], backup_dir=env["tmp"] / "back")
    beyond = rep["checklist"]["beyond_checklist"]
    assert [b["group_id"] for b in beyond] == ["test-broadcast-177"]
    assert rep["deleted"]["total"] == 6
    assert _count(env["db"], "discussion_messages") == 2  # m4/m5 存活


def test_backup_snapshot_and_sha256(env):
    rep = mod.run(env["art"], apply=True, db_override=env["db"], backup_dir=env["tmp"] / "back")
    b = rep["backup"]
    dest = Path(b["path"])
    assert dest.exists()
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == b["sha256"]
    conn = sqlite3.connect(str(dest))
    try:
        # 快照含删前全行（待删行可在备份回读）
        assert conn.execute("SELECT COUNT(*) FROM discussion_messages").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM task_execution_events").fetchone()[0] == 1
    finally:
        conn.close()
    assert rep["mode"] == "apply"
