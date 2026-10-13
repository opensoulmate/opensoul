"""TL-⑪ 探针组 2 删除——按清单精确删 + delete_group 级联 + 备份 + 回读（全 tmp，
绝不碰生产库）。

覆盖：工件缺键拒删 / 清单组名错配拒删 / 删前逐行复核拒删（现判非 A=拒删）/
dry-run 零写入 / 级联只删 (group_id, 组行租户) 双键目标（他租户外来行+非目标组+
悬挂行一行不动）/ 改名非 A 组如实不动报缺行 / 清单外同判据新残留一并删单列 /
清单缺行非致命幂等重跑 / 备份快照 sha256 可回读 / 总数算术对账 /
悬挂行与跨组 task_id 引用只报数不删 / 分类词表单一源（census.classify_group
运行时真消费）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_ai_group_probe_residue.py"
_spec = importlib.util.spec_from_file_location("delete_ai_group_probe_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

census = mod.census  # 分类词表单一源（脚本同源 import，禁第二套词表）

# 探针组（A 类硬标识，census.A_GROUP_NAMES 原语义）+ 真实组不动组
A_GROUPS = [("g-probe-a", "A群组"), ("g-probe-b", "B群组")]
OTHER_GROUPS = [("g-real", "真实运维组")]

CHILD_TABLES = mod.CHILD_TABLES


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

    def g(gid, name, tenant="default"):
        conn.execute(
            "INSERT INTO ai_groups (id,name,tenant_id) VALUES (?,?,?)", (gid, name, tenant)
        )

    def row(table, rid, gid, tenant="default", task_id="t-1"):
        if table == "ai_group_agents":
            conn.execute(
                "INSERT INTO ai_group_agents (id,group_id,agent_id,name,tenant_id)"
                " VALUES (?,?,?,?,?)",
                (rid, gid, "a1", rid, tenant),
            )
        elif table == "ai_group_tasks":
            conn.execute(
                "INSERT INTO ai_group_tasks (id,group_id,goal,tenant_id) VALUES (?,?,?,?)",
                (rid, gid, "goal-" + rid, tenant),
            )
        elif table == "discussion_messages":
            conn.execute(
                "INSERT INTO discussion_messages (id,group_id,task_id,tenant_id) VALUES (?,?,?,?)",
                (rid, gid, task_id, tenant),
            )
        elif table == "task_execution_events":
            conn.execute(
                "INSERT INTO task_execution_events (id,group_id,task_id,tenant_id)"
                " VALUES (?,?,?,?)",
                (rid, gid, task_id, tenant),
            )
        elif table == "agent_scores":
            conn.execute(
                "INSERT INTO agent_scores (id,group_id,task_id,tenant_id) VALUES (?,?,?,?)",
                (rid, gid, task_id, tenant),
            )
        elif table == "agent_capabilities":
            conn.execute(
                "INSERT INTO agent_capabilities (id,group_id,agent_id,tenant_id) VALUES (?,?,?,?)",
                (rid, gid, "a1", tenant),
            )

    for gid, name in A_GROUPS + OTHER_GROUPS:
        g(gid, name)
    # 级联目标：每子表 2 行（探针组各 1）——随组删
    for i, (gid, _n) in enumerate(A_GROUPS):
        for t in CHILD_TABLES:
            row(t, f"{t}-tgt-{i}", gid)
    # 非目标组行（真实组）每子表 1 行——一行不动
    for t in CHILD_TABLES:
        row(t, f"{t}-real", "g-real")
    # 他租户外来行（探针组 id 异租户）——delete_group 语义只报数不删
    row("discussion_messages", "msg-foreign", "g-probe-a", tenant="tenant-x")
    # 悬挂行（组行已不在场）——只报数不删
    row("task_execution_events", "evt-orphan", "g-gone", task_id="t-gone")
    row("discussion_messages", "msg-orphan", "test-group-123", task_id="ws-x")
    conn.commit()
    conn.close()


def _artifact(tmp_path: Path, entries=None) -> Path:
    if entries is None:
        entries = [{"group_id": g, "name": n} for g, n in A_GROUPS]
    art = {
        "sqlite": {
            "ai_groups": {
                "groups_total": len(entries),
                "groups": entries,
                "tasks_total": 3,
                "tasks_A": 1,
                "tasks_B": 1,
                "tasks_U": 1,
            }
        }
    }
    p = tmp_path / "census.json"
    p.write_text(json.dumps(art, ensure_ascii=False))
    return p


def _counts(db: Path) -> dict:
    conn = sqlite3.connect(str(db))
    out = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("ai_groups",) + tuple(CHILD_TABLES)
    }
    conn.close()
    return out


def _run(tmp_path: Path, apply: bool, entries=None, seed=True):
    db = tmp_path / "ai_groups.db"
    if seed:
        _seed(db)
    art = _artifact(tmp_path, entries)
    return mod.run(art, apply=apply, backup_dir=tmp_path / "backups", db_override=db), db


def test_dry_run_zero_writes(tmp_path):
    _seed(tmp_path / "ai_groups.db")
    before = _counts(tmp_path / "ai_groups.db")
    rep, db = _run(tmp_path, apply=False, seed=False)
    assert rep["mode"] == "dry-run"
    assert rep["deleted"]["total"] == 0
    assert _counts(db) == before


def test_apply_cascades_checklist_only(tmp_path):
    rep, db = _run(tmp_path, apply=True)
    conn = sqlite3.connect(str(db))
    # 探针组组行+同租户子行全 gone
    assert conn.execute("SELECT COUNT(*) FROM ai_groups").fetchone()[0] == 1
    for t in CHILD_TABLES:
        left = conn.execute(f"SELECT id FROM {t} WHERE id LIKE '%-tgt-%'").fetchall()
        assert left == [], (t, left)
        real = conn.execute(f"SELECT id FROM {t} WHERE id LIKE '%-real'").fetchall()
        assert len(real) == 1, (t, real)
    conn.close()
    assert rep["deleted"]["ai_groups"] == 2
    assert rep["deleted"]["total"] == 2 + 2 * len(CHILD_TABLES)
    for t in CHILD_TABLES:
        assert rep["deleted"][t] == 2
        assert rep["post_children"][t]["total_reconcile"]["ok"] is True
    assert rep["post_groups"]["total_reconcile"]["ok"] is True


def test_foreign_tenant_rows_survive(tmp_path):
    rep, db = _run(tmp_path, apply=True)
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM discussion_messages WHERE id='msg-foreign'").fetchone()[
        0
    ]
    conn.close()
    assert n == 1
    foreign = rep["pre_children"]["discussion_messages"]["foreign_under_target"]
    assert [f["id"] for f in foreign] == ["msg-foreign"]
    assert [
        f["id"] for f in rep["post_children"]["discussion_messages"]["foreign_under_target"]
    ] == ["msg-foreign"]
    assert rep["post_children"]["discussion_messages"]["total_reconcile"]["ok"] is True


def test_orphan_rows_reported_not_deleted(tmp_path):
    rep, db = _run(tmp_path, apply=True)
    conn = sqlite3.connect(str(db))
    for rid, tbl in (
        ("evt-orphan", "task_execution_events"),
        ("msg-orphan", "discussion_messages"),
    ):
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE id=?", (rid,)).fetchone()[0]
        assert n == 1, rid
    conn.close()
    assert rep["orphan_rows_before"]["task_execution_events"] == {"g-gone": 1}
    assert rep["orphan_rows_after"]["task_execution_events"] == {"g-gone": 1}
    assert rep["orphan_rows_before"]["discussion_messages"] == {"test-group-123": 1}
    # 删组后 msg-foreign（他租户不动行）组行随之悬挂，如实进悬挂报告
    assert rep["orphan_rows_after"]["discussion_messages"] == {"test-group-123": 1, "g-probe-a": 1}


def test_cross_group_task_refs_reported(tmp_path):
    # 存活行引用待删任务（group_id 异组）= 级联后新悬挂，只报数
    db = tmp_path / "ai_groups.db"
    _seed(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO discussion_messages (id,group_id,task_id) VALUES "
        "('msg-xref','g-real','ai_group_tasks-tgt-0')"
    )
    conn.commit()
    conn.close()
    rep, _ = _run(tmp_path, apply=True, seed=False)
    assert rep["cross_group_task_refs"]["discussion_messages"] == 1
    assert rep["cross_group_task_refs"]["task_execution_events"] == 0
    assert rep["cross_group_task_refs"]["agent_scores"] == 0


def test_name_mismatch_refuses(tmp_path):
    # 清单组名与现库错配（换成另一个 A 名）=拒删一行不动
    db = tmp_path / "ai_groups.db"
    _seed(db)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE ai_groups SET name='B群组' WHERE id='g-probe-a'")
    conn.commit()
    conn.close()
    before = _counts(db)
    with pytest.raises(mod.RefuseError, match="错配"):
        _run(tmp_path, apply=True, seed=False)
    assert _counts(db) == before


def test_renamed_non_A_left_alone_reported(tmp_path):
    # 现判非 A（改名出词表）=一行不动，如实报缺行（非致命）
    db = tmp_path / "ai_groups.db"
    _seed(db)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE ai_groups SET name='真实运维组2' WHERE id='g-probe-a'")
    conn.commit()
    conn.close()
    rep, db = _run(tmp_path, apply=True, seed=False)
    assert rep["checklist"]["missing_checklist_ids"] == ["g-probe-a"]
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM ai_groups WHERE id='g-probe-a'").fetchone()[0]
    conn.close()
    assert n == 1
    assert rep["deleted"]["ai_groups"] == 1


def test_recheck_unit_refuses_non_A(tmp_path):
    db = tmp_path / "ai_groups.db"
    _seed(db)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE ai_groups SET name='真实运维组2' WHERE id='g-probe-a'")
    conn.commit()
    with pytest.raises(mod.RefuseError, match="复核不过"):
        mod.recheck(conn, [{"group_id": "g-probe-a", "name": "A群组", "tenant_id": "default"}])
    with pytest.raises(mod.RefuseError, match="错配"):
        mod.recheck(conn, [{"group_id": "g-probe-b", "name": "A群组", "tenant_id": "default"}])
    assert mod.recheck(conn, [{"group_id": "g-gone", "name": "A群组", "tenant_id": "default"}]) == [
        "g-gone"
    ]
    conn.close()


def test_vocab_single_source_runtime_consumed(tmp_path):
    # 词表单一源运行时真消费：换词表→判法随之变（零 A=零删除）
    db = tmp_path / "ai_groups.db"
    _seed(db)
    real_names = set(census.A_GROUP_NAMES)
    census.A_GROUP_NAMES = {"其他组"}
    try:
        rep = mod.run(
            _artifact(tmp_path), apply=True, backup_dir=tmp_path / "backups", db_override=db
        )
        assert rep["artifact_checklist_n"] == 0
        assert rep["live_A"] == 0
        assert rep["deleted"]["total"] == 0
    finally:
        census.A_GROUP_NAMES = real_names


def test_artifact_missing_key_refuses(tmp_path):
    p = tmp_path / "census.json"
    p.write_text(json.dumps({"sqlite": {"ai_groups": {"groups_total": 2}}}))
    _seed(tmp_path / "ai_groups.db")
    before = _counts(tmp_path / "ai_groups.db")
    with pytest.raises(mod.RefuseError, match="工件不可信"):
        mod.run(
            p, apply=True, backup_dir=tmp_path / "backups", db_override=tmp_path / "ai_groups.db"
        )
    assert _counts(tmp_path / "ai_groups.db") == before


def test_missing_checklist_row_nonfatal(tmp_path):
    db = tmp_path / "ai_groups.db"
    _seed(db)
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM ai_groups WHERE id='g-probe-b'")
    conn.commit()
    conn.close()
    rep, db = _run(tmp_path, apply=True, seed=False)
    assert rep["checklist"]["missing_checklist_ids"] == ["g-probe-b"]
    assert rep["deleted"]["ai_groups"] == 1
    # 幂等重跑：全 gone 后清单全缺，零删除不炸
    rep2 = mod.run(_artifact(tmp_path), apply=True, backup_dir=tmp_path / "backups", db_override=db)
    assert rep2["deleted"]["total"] == 0
    assert sorted(rep2["checklist"]["missing_checklist_ids"]) == ["g-probe-a", "g-probe-b"]


def test_beyond_checklist_deleted_and_listed(tmp_path):
    db = tmp_path / "ai_groups.db"
    _seed(db)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO ai_groups (id,name) VALUES ('g-probe-c','A群组')")
    conn.execute(
        "INSERT INTO ai_group_tasks (id,group_id,goal) VALUES ('t-beyond','g-probe-c','goal')"
    )
    conn.commit()
    conn.close()
    rep, db = _run(tmp_path, apply=True, seed=False)
    beyond = [r["group_id"] for r in rep["checklist"]["beyond_checklist"]]
    assert beyond == ["g-probe-c"]
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM ai_groups WHERE id='g-probe-c'").fetchone()[0]
    t = conn.execute("SELECT COUNT(*) FROM ai_group_tasks WHERE id='t-beyond'").fetchone()[0]
    conn.close()
    assert n == 0 and t == 0
    assert rep["deleted"]["ai_groups"] == 3


def test_backup_snapshot_contains_pre_rows(tmp_path):
    rep, db = _run(tmp_path, apply=True)
    b = rep["backup"]
    assert Path(b["path"]).exists()
    assert hashlib.sha256(Path(b["path"]).read_bytes()).hexdigest() == b["sha256"]
    conn = sqlite3.connect(b["path"])
    groups = [g[0] for g in conn.execute("SELECT id FROM ai_groups ORDER BY id").fetchall()]
    tasks = conn.execute("SELECT COUNT(*) FROM ai_group_tasks").fetchone()[0]
    conn.close()
    assert groups == ["g-probe-a", "g-probe-b", "g-real"]
    assert tasks == 3  # 目标 2 + 真实组 1（删前全量快照）


def test_report_keys(tmp_path):
    rep, _ = _run(tmp_path, apply=True)
    for key in (
        "tool",
        "mode",
        "artifact",
        "db",
        "artifact_checklist",
        "artifact_excluded",
        "checklist",
        "recheck_missing",
        "pre_children",
        "orphan_rows_before",
        "orphan_rows_after",
        "cross_group_task_refs",
        "backup",
        "deleted",
        "post_children",
        "post_groups",
        "deleted_by_rule",
        "deleted_group_ids",
    ):
        assert key in rep, key
    assert rep["deleted_by_rule"] == {"A_GROUP_NAMES": 2}
    assert sorted(rep["deleted_group_ids"]) == ["g-probe-a", "g-probe-b"]


def test_non_A_artifact_entries_excluded(tmp_path):
    # 工件里非 A 组如实排除不动
    entries = [{"group_id": g, "name": n} for g, n in A_GROUPS] + [
        {"group_id": "g-real", "name": "真实运维组"}
    ]
    rep, db = _run(tmp_path, apply=True, entries=entries)
    assert [e["group_id"] for e in rep["artifact_excluded"]] == ["g-real"]
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM ai_groups WHERE id='g-real'").fetchone()[0]
    conn.close()
    assert n == 1
