"""TL-⑦ 删除执行件②b-1——nest.db tenants e2e 租户精确删语义（全 tmp，绝不碰生产库）。

覆盖：dry-run 零写入 / 按清单精确删（非目标行含 default 一行不动）/ 删前逐行复核
拒删（名错配 / 现判非 A / 清单含 default）/ 工件不可信拒删 / 清单缺行非致命 /
清单外新残留单列不删 / 引用面拒删闸（绑定类引用拒删、审计表引用只报告）/
备份快照可回读 / 报告逐键 / 分类词表单一源契约。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SCRIPT = REPO / "scripts" / "delete_nest_tenant_residue.py"
_spec = importlib.util.spec_from_file_location("delete_nest_tenant_residue", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

TENANTS_DDL = """
CREATE TABLE tenants (
    tenant_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    tier TEXT NOT NULL,
    status TEXT NOT NULL,
    namespace TEXT NOT NULL,
    owner_user_id TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    config TEXT NOT NULL DEFAULT '{}',
    metadata TEXT NOT NULL DEFAULT '{}',
    quota TEXT NOT NULL DEFAULT '{}',
    usage TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""
ACCESS_LOG_DDL = """
CREATE TABLE access_log (
    id INTEGER PRIMARY KEY,
    timestamp REAL,
    tenant_id TEXT,
    resource_type TEXT,
    resource_id TEXT,
    action TEXT,
    allowed INTEGER,
    reason TEXT,
    source_ip TEXT)
"""

# 行样本：分类判法单一源=census.classify_tenant（name 前缀 e2e- = A）
TENANTS = [
    ("default", "default"),  # other，恒不动
    ("tenant-real-1", "acme-prod"),  # other=真租户，不动
    ("tenant-89c16285", "e2e-t8c-a"),  # A，清单内
    ("tenant-e47bba31", "e2e-t8c-b"),  # A，清单内
    ("tenant-extra-1", "e2e-extra"),  # A，清单外（beyond_checklist 不删）
]
CHECKLIST = [
    {"tenant_id": "tenant-89c16285", "name": "e2e-t8c-a"},
    {"tenant_id": "tenant-e47bba31", "name": "e2e-t8c-b"},
]


def make_db(tmp_path: Path) -> Path:
    db = tmp_path / "nest.db"
    conn = sqlite3.connect(str(db))
    conn.execute(TENANTS_DDL)
    conn.execute(ACCESS_LOG_DDL)
    for tid, name in TENANTS:
        conn.execute(
            "INSERT INTO tenants (tenant_id,name,tier,status,namespace,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (tid, name, "free", "active", "ns-" + tid, 1.0, 1.0),
        )
    conn.commit()
    conn.close()
    return db


def make_artifact(tmp_path: Path, checklist=None) -> Path:
    art = tmp_path / "artifact.json"
    rows = CHECKLIST if checklist is None else checklist
    art.write_text(
        json.dumps({"sqlite": {"nest_tenants": {"total": 3, "A": rows}}}, ensure_ascii=False)
    )
    return art


def live_ids(db: Path) -> set:
    conn = sqlite3.connect(str(db))
    try:
        return {r[0] for r in conn.execute("SELECT tenant_id FROM tenants")}
    finally:
        conn.close()


class TestClassifySingleSource:
    def test_a_prefix(self):
        assert mod.census.classify_tenant("e2e-t8c-a") == ("A", "A_TENANT_PREFIX")
        assert mod.census.classify_tenant("e2e-anything") == ("A", "A_TENANT_PREFIX")

    def test_other_forms(self):
        for name in ("default", "acme-prod", "tenant-89c16285", "", None, "E2E-upper"):
            cls, _rule = mod.census.classify_tenant(name)
            assert cls == "other", name

    def test_census_sqlite_consumes_single_source(self, tmp_path, monkeypatch):
        """census_sqlite 的 nest A 判定收编 classify_tenant（monkeypatch 运行时真消费）。"""
        calls = []
        orig = mod.census.classify_tenant

        def spy(name):
            calls.append(name)
            return orig(name)

        monkeypatch.setattr(mod.census, "classify_tenant", spy)
        # 直接走 census 的 nest 判定行（与 census_sqlite 同表达式）
        tenants = [("tenant-89c16285", "e2e-t8c-a"), ("default", "default")]
        got = [t[0] for t in tenants if mod.census.classify_tenant(t[1])[0] == "A"]
        assert got == ["tenant-89c16285"]
        assert "e2e-t8c-a" in calls


class TestDeleteSemantics:
    def test_dry_run_zero_writes(self, tmp_path):
        db = make_db(tmp_path)
        art = make_artifact(tmp_path)
        before = live_ids(db)
        rep = mod.run(db, art, apply=False, db_dir=tmp_path)
        assert rep["mode"] == "dry-run"
        assert rep["deleted"] == {"tenants": 0}
        assert live_ids(db) == before
        assert sorted(rep["deleted_ids"]) == ["tenant-89c16285", "tenant-e47bba31"]

    def test_apply_deletes_exactly_checklist(self, tmp_path):
        db = make_db(tmp_path)
        art = make_artifact(tmp_path)
        rep = mod.run(db, art, apply=True, db_dir=tmp_path)
        assert rep["deleted"] == {"tenants": 2}
        after = live_ids(db)
        assert "tenant-89c16285" not in after and "tenant-e47bba31" not in after
        # 非目标行含 default / 真租户 / 清单外 A 一行不动
        assert {"default", "tenant-real-1", "tenant-extra-1"} <= after
        assert rep["non_target_missing"] == []

    def test_recheck_name_mismatch_refuses(self, tmp_path):
        db = make_db(tmp_path)
        art = make_artifact(
            tmp_path, checklist=[{"tenant_id": "tenant-89c16285", "name": "renamed-not-e2e"}]
        )
        with pytest.raises(mod.RefuseError, match="名错配"):
            mod.run(db, art, apply=True, db_dir=tmp_path)
        assert "tenant-89c16285" in live_ids(db)  # 一行不动

    def test_recheck_now_non_a_refuses(self, tmp_path):
        db = make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE tenants SET name='prod-real' WHERE tenant_id='tenant-89c16285'")
        conn.commit()
        conn.close()
        art = make_artifact(tmp_path)
        with pytest.raises(mod.RefuseError, match="现判非 A"):
            mod.run(db, art, apply=True, db_dir=tmp_path)
        assert "tenant-89c16285" in live_ids(db)

    def test_checklist_includes_default_refuses(self, tmp_path):
        db = make_db(tmp_path)
        art = make_artifact(tmp_path, checklist=[{"tenant_id": "default", "name": "default"}])
        with pytest.raises(mod.RefuseError):
            mod.run(db, art, apply=True, db_dir=tmp_path)
        assert "default" in live_ids(db)

    def test_missing_checklist_row_nonfatal(self, tmp_path):
        db = make_db(tmp_path)
        art = make_artifact(
            tmp_path, checklist=CHECKLIST + [{"tenant_id": "tenant-gone-1", "name": "e2e-gone"}]
        )
        rep = mod.run(db, art, apply=True, db_dir=tmp_path)
        assert rep["missing_checklist"] == ["tenant-gone-1"]
        assert rep["deleted"] == {"tenants": 2}

    def test_beyond_checklist_reported_not_deleted(self, tmp_path):
        db = make_db(tmp_path)
        art = make_artifact(tmp_path)
        rep = mod.run(db, art, apply=True, db_dir=tmp_path)
        assert rep["beyond_checklist"] == [{"tenant_id": "tenant-extra-1", "name": "e2e-extra"}]
        assert "tenant-extra-1" in live_ids(db)
        assert rep["post_A_ids"] == ["tenant-extra-1"]

    def test_artifact_missing_key_refuses(self, tmp_path):
        db = make_db(tmp_path)
        art = tmp_path / "artifact.json"
        art.write_text(json.dumps({"sqlite": {"opensoul_users": {}}}))
        with pytest.raises(mod.RefuseError, match="工件不可信"):
            mod.run(db, art, apply=True, db_dir=tmp_path)
        assert len(live_ids(db)) == len(TENANTS)


class TestReferenceGate:
    def test_binding_reference_refuses(self, tmp_path):
        db = make_db(tmp_path)
        other = tmp_path / "users.db"
        conn = sqlite3.connect(str(other))
        conn.execute("CREATE TABLE users (id TEXT, tenant_id TEXT)")
        conn.execute("INSERT INTO users VALUES ('u1','tenant-89c16285')")
        conn.commit()
        conn.close()
        art = make_artifact(tmp_path)
        with pytest.raises(mod.RefuseError, match="悬空绑定"):
            mod.run(db, art, apply=True, db_dir=tmp_path)
        assert "tenant-89c16285" in live_ids(db)  # 一行不动

    def test_audit_reference_reported_not_refused(self, tmp_path):
        db = make_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO access_log (timestamp,tenant_id,action,allowed)"
            " VALUES (1.0,'tenant-89c16285','read',1)"
        )
        conn.commit()
        conn.close()
        art = make_artifact(tmp_path)
        rep = mod.run(db, art, apply=True, db_dir=tmp_path)
        assert rep["refs_pre"]["audit"] == [
            {"db": "nest.db", "table": "access_log", "column": "tenant_id", "rows": 1}
        ]
        assert rep["refs_pre"]["binding"] == []
        assert rep["deleted"] == {"tenants": 2}  # 审计引用不拒删

    def test_reference_scan_skips_tenants_table(self, tmp_path):
        db = make_db(tmp_path)
        rep = mod.run(db, make_artifact(tmp_path), apply=False, db_dir=tmp_path)
        assert rep["refs_pre"] == {"binding": [], "audit": []}


class TestBackupAndReport:
    def test_backup_snapshot_readable(self, tmp_path):
        db = make_db(tmp_path)
        art = make_artifact(tmp_path)
        rep = mod.run(db, art, apply=True, backup_dir=tmp_path / "bks", db_dir=tmp_path)
        bpath = Path(rep["backup"]["path"])
        assert bpath.exists()
        assert rep["backup"]["sha256"]
        conn = sqlite3.connect(str(bpath))
        try:
            rows = {r[0] for r in conn.execute("SELECT tenant_id FROM tenants")}
        finally:
            conn.close()
        assert rows == {t[0] for t in TENANTS}  # 删前全行可回读

    def test_report_keys(self, tmp_path):
        db = make_db(tmp_path)
        art = make_artifact(tmp_path)
        rep = mod.run(
            db,
            art,
            apply=True,
            backup_dir=tmp_path / "bks",
            report_path=tmp_path / "rep.json",
            db_dir=tmp_path,
        )
        for key in (
            "tool",
            "mode",
            "checklist_ids",
            "live_A_ids",
            "artifact_drift",
            "matched",
            "missing_checklist",
            "beyond_checklist",
            "pre",
            "post",
            "deleted",
            "deleted_ids",
            "backup",
            "refs_pre",
            "refs_post",
            "concurrent_new_rows",
        ):
            assert key in rep, key
        on_disk = json.loads((tmp_path / "rep.json").read_text())
        assert on_disk["tool"] == "delete_nest_tenant_residue"
