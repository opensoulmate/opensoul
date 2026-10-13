"""TL-⑩ 测试用户 4+2 删除——按清单精确删+备份+回读（全 tmp，绝不碰生产库）。

覆盖：工件缺键拒删 / 清单词表漂移拒删 / 删前逐行复核拒删（现判非 A=一行不动）/
dry-run 零写入 / 只删清单 A 类两库两面（admin/default/hermes 真实用户一行不动）/
清单外同判据新残留一并删单列 / 清单缺行非致命幂等重跑 / 备份快照 sha256 可回读 /
总数算术对账 / 跨库 FK 引用行如实不动（audit_log/user_roles 只报数）/
分类词表单一源（census.classify_user 运行时真消费）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_test_user_residue.py"
_spec = importlib.util.spec_from_file_location("delete_test_user_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

census = mod.census  # 分类词表单一源（脚本同源 import，禁第二套词表）

# opensoul A 类硬标识用户（census.A_USERNAMES 原语义）+ 真实用户不动组
OS_A = [
    ("u-test", "test", "test@test.com"),
    ("u-admin2", "admin2", "admin2@test.com"),
    ("u-autobot", "autobot", "autobot@local.dev"),
    ("u-uiprobe", "uiprobe", "uiprobe@local.dev"),
]
OS_OTHER = [
    ("u-admin", "admin", "admin@localhost"),
    ("u-default", "default", "default@pipeline.local"),
    ("u-hermes", "hermes", "hermes@local"),
]
# enterprise A 类（同词表；无 email 列）+ 真实用户不动组
ENT_A = [(2, "test"), (4, "admin2")]
ENT_OTHER = [(3, "hermes"), (5, "admin")]


def _seed_opensoul(db: Path, extra=()) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT NOT NULL,"
        " email TEXT NOT NULL DEFAULT '', password_hash TEXT NOT NULL,"
        " role TEXT NOT NULL DEFAULT 'user', is_active INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " updated_at TEXT, tenant_id TEXT NOT NULL DEFAULT 'default')"
    )
    for uid, uname, email in list(OS_A) + list(OS_OTHER) + list(extra):
        conn.execute(
            "INSERT INTO users (id,username,email,password_hash) VALUES (?,?,?,?)",
            (uid, uname, email, "x"),
        )
    conn.commit()
    conn.close()


def _seed_enterprise(db: Path, extra=()) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,"
        " password TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " tenant_id TEXT NOT NULL DEFAULT 'default')"
    )  # AUTOINCREMENT=真 sqlite_sequence
    conn.execute(
        "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, user_id INTEGER,"
        " action TEXT, created_at TEXT)"
    )
    conn.execute("CREATE TABLE user_roles (user_id INTEGER, role_id INTEGER)")
    for uid, uname in list(ENT_A) + list(ENT_OTHER) + list(extra):
        conn.execute("INSERT INTO users (id,username,password) VALUES (?,?,?)", (uid, uname, "x"))
    # FK 引用面：待删用户的审计/角色行（如实不动，只报数）
    conn.execute("INSERT INTO audit_log (user_id,action) VALUES (2,'login')")
    conn.execute("INSERT INTO audit_log (user_id,action) VALUES (2,'login')")
    conn.execute("INSERT INTO audit_log (user_id,action) VALUES (4,'login')")
    conn.execute("INSERT INTO audit_log (user_id,action) VALUES (4,'login')")
    conn.execute("INSERT INTO user_roles (user_id,role_id) VALUES (2,1)")
    conn.execute("INSERT INTO audit_log (user_id,action) VALUES (5,'login')")
    conn.commit()
    conn.close()


def _artifact(tmp_path: Path, os_a=None, ent_a=None) -> Path:
    art = {
        "sqlite": {
            "opensoul_users": {
                "total": 7,
                "A": [{"id": u, "username": n} for u, n, _ in (OS_A if os_a is None else os_a)],
            },
            "enterprise_users": {
                "total": 4,
                "A": [{"id": i, "username": n} for i, n in (ENT_A if ent_a is None else ent_a)],
            },
        }
    }
    p = tmp_path / "census.json"
    p.write_text(json.dumps(art))
    return p


def _db_paths(tmp_path: Path):
    return {"opensoul": tmp_path / "opensoul.db", "enterprise": tmp_path / "enterprise.db"}


def _seed_all(tmp_path: Path, os_extra=(), ent_extra=()) -> dict:
    dbs = _db_paths(tmp_path)
    _seed_opensoul(dbs["opensoul"], os_extra)
    _seed_enterprise(dbs["enterprise"], ent_extra)
    return dbs


def _names(db: Path, table="users"):
    conn = sqlite3.connect(str(db))
    rows = conn.execute(f"SELECT id,username FROM {table} ORDER BY id").fetchall()
    conn.close()
    return rows


def test_artifact_missing_key_refuses(tmp_path):
    dbs = _seed_all(tmp_path)
    art = tmp_path / "census.json"
    art.write_text(json.dumps({"sqlite": {"opensoul_users": {"A": []}}}))  # 缺 enterprise 键
    with pytest.raises(mod.RefuseError, match="工件缺"):
        mod.run(art, apply=True, backup_dir=tmp_path / "backups", db_overrides=dbs)
    assert len(_names(dbs["opensoul"])) == 7  # 一行不动
    assert len(_names(dbs["enterprise"])) == 4


def test_vocab_drift_refuses(tmp_path):
    dbs = _seed_all(tmp_path)
    art = _artifact(tmp_path, os_a=[("u-test", "test", ""), ("u-ghost", "totally-real-person", "")])
    with pytest.raises(mod.RefuseError, match="词表漂移"):
        mod.run(art, apply=True, backup_dir=tmp_path / "backups", db_overrides=dbs)
    assert len(_names(dbs["opensoul"])) == 7  # 拒删=一行不动


def test_recheck_mismatch_refuses(tmp_path):
    dbs = _seed_all(tmp_path)
    conn = sqlite3.connect(str(dbs["opensoul"]))
    # 删前逐行复核语义：待删 id 的行内容现判非 A（清单陈旧/错配/并发改写）=拒删
    conn.execute("UPDATE users SET username='realperson', email='r@x.com' WHERE id='u-test'")
    conn.commit()
    with pytest.raises(mod.RefuseError, match="复核不过"):
        mod.recheck(conn, "opensoul", ["u-test", "u-admin2"])
    conn.close()
    assert len(_names(dbs["opensoul"])) == 7  # 拒删=一行不动


def test_recheck_missing_row_nonfatal(tmp_path):
    dbs = _seed_all(tmp_path)
    conn = sqlite3.connect(str(dbs["opensoul"]))
    conn.execute("DELETE FROM users WHERE id='u-test'")
    conn.commit()
    missing = mod.recheck(conn, "opensoul", ["u-test", "u-admin2"])
    conn.close()
    assert missing == ["u-test"]  # 缺行如实报非致命（幂等重跑语义）


def test_dry_run_zero_write(tmp_path):
    dbs = _seed_all(tmp_path)
    art = _artifact(tmp_path)
    rep = mod.run(art, apply=False, db_overrides=dbs)
    assert rep["mode"] == "dry-run" and rep["deleted_total"] == 0
    assert len(_names(dbs["opensoul"])) == 7
    assert len(_names(dbs["enterprise"])) == 4


def test_apply_deletes_checklist_only(tmp_path):
    dbs = _seed_all(tmp_path)
    art = _artifact(tmp_path)
    rep = mod.run(art, apply=True, backup_dir=tmp_path / "backups", db_overrides=dbs)
    assert rep["deleted_total"] == 6  # 4+2
    os_rows = _names(dbs["opensoul"])
    assert sorted(u for _, u in os_rows) == ["admin", "default", "hermes"]  # 真实用户不动
    ent_rows = _names(dbs["enterprise"])
    assert sorted(u for _, u in ent_rows) == ["admin", "hermes"]
    for face in ("opensoul", "enterprise"):
        assert rep["faces"][face]["total_reconcile"]["ok"] is True
        assert rep["faces"][face]["recheck_missing"] == []


def test_beyond_checklist_same_judged_deleted_and_reported(tmp_path):
    dbs = _seed_all(tmp_path, os_extra=[("u-newtest", "test", "t@t.com")])
    art = _artifact(tmp_path)  # 清单不含 u-newtest
    rep = mod.run(art, apply=True, backup_dir=tmp_path / "backups", db_overrides=dbs)
    assert rep["deleted_total"] == 7  # 6 + 清单外同判据 1
    beyond = rep["faces"]["opensoul"]["checklist"]["beyond_checklist"]
    assert [r["id"] for r in beyond] == ["u-newtest"]  # 单列报告
    assert "u-newtest" not in [u for _, u in _names(dbs["opensoul"])]


def test_missing_checklist_row_nonfatal_idempotent(tmp_path):
    dbs = _seed_all(tmp_path)
    art = _artifact(tmp_path)
    # 先跑一遍真删
    mod.run(art, apply=True, backup_dir=tmp_path / "backups", db_overrides=dbs)
    # 幂等重跑：清单行已缺=如实报非致命，不炸不误删
    rep2 = mod.run(art, apply=True, backup_dir=tmp_path / "backups", db_overrides=dbs)
    assert rep2["deleted_total"] == 0
    missing = rep2["faces"]["opensoul"]["checklist"]["missing_checklist_ids"]
    assert sorted(missing) == sorted(u for u, _, _ in OS_A)
    assert sorted(u for _, u in _names(dbs["opensoul"])) == ["admin", "default", "hermes"]


def test_backup_snapshot_sha256_and_rollback_read(tmp_path):
    dbs = _seed_all(tmp_path)
    art = _artifact(tmp_path)
    bdir = tmp_path / "backups"
    rep = mod.run(art, apply=True, backup_dir=bdir, db_overrides=dbs)
    for face in ("opensoul", "enterprise"):
        b = rep["faces"][face]["backup"]
        p = Path(b["path"])
        assert p.exists()
        assert hashlib.sha256(p.read_bytes()).hexdigest() == b["sha256"]
        # 快照可回读=删前行完好（可整体回滚）
        conn = sqlite3.connect(str(p))
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        assert n == rep["faces"][face]["pre"]["total"]


def test_enterprise_fk_refs_kept_untouched(tmp_path):
    dbs = _seed_all(tmp_path)
    art = _artifact(tmp_path)
    conn = sqlite3.connect(str(dbs["enterprise"]))
    seq_before = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='users'").fetchone()
    conn.close()
    rep = mod.run(art, apply=True, backup_dir=tmp_path / "backups", db_overrides=dbs)
    conn = sqlite3.connect(str(dbs["enterprise"]))
    audit_n = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    ur_n = conn.execute("SELECT COUNT(*) FROM user_roles").fetchone()[0]
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='users'").fetchone()
    conn.close()
    assert audit_n == 5  # 4 条引用待删用户 + 1 条真实用户，一行不动
    assert ur_n == 1  # 引用待删用户的角色行如实不动
    assert seq == seq_before  # 自增计数器不动
    ent = rep["faces"]["enterprise"]
    assert ent["fk_refs_to_deleted"] == {"audit_log": 4, "user_roles": 1}
    assert ent["fk_refs_to_deleted_after"] == {"audit_log": 4, "user_roles": 1}


def test_cross_face_single_run(tmp_path):
    dbs = _seed_all(tmp_path)
    art = _artifact(tmp_path)
    rep = mod.run(art, apply=True, backup_dir=tmp_path / "backups", db_overrides=dbs)
    assert rep["faces"]["opensoul"]["deleted"] == 4
    assert rep["faces"]["enterprise"]["deleted"] == 2
    assert rep["deleted_total"] == 6


def test_classifier_single_source_consumed():
    """分类判法单一源：脚本真消费 census.classify_user（禁第二套词表）。"""
    assert mod.scan.__globals__["census"] is census
    assert census.classify_user("test")[0] == "A"
    assert census.classify_user("admin")[0] == "other"
    assert census.classify_user("nobody", "x@test.com")[0] == "A"  # 邮箱硬标识
    assert census.classify_user("nobody")[0] == "other"  # enterprise 无 email 不猜


def test_artifact_id_typing_roundtrip(tmp_path):
    """enterprise id 是 int、opensoul id 是 str——原样删禁强转。"""
    dbs = _seed_all(tmp_path)
    art = _artifact(tmp_path)
    rep = mod.run(art, apply=True, backup_dir=tmp_path / "backups", db_overrides=dbs)
    assert rep["faces"]["enterprise"]["deleted_ids"] == [2, 4]  # int 原样
    assert all(isinstance(i, str) for i in rep["faces"]["opensoul"]["deleted_ids"])
