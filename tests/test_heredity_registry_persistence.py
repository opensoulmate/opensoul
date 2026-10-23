"""RV-①b-⑭-1 VersionRegistry 三面持久化测试——组件版本/迁移计划（含 execute·rollback
状态）/changelog + platform_version 重启不丢（RV-①c §5.1 藏活事实 ⑭ 拆件第①件；
migration.py 注册 schema/迁移脚本 applied 状态两面=⑭-2 留待拍）。

数据面全 tmp（显式 RegistryPersistence(db_path=tmp) 新实例=重启模拟；模块单例面走
HEREDITY_REGISTRY_DB autouse 隔离），绝不写生产 data/heredity_registry.db。
"""

import os
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.heredity.registry_persist import RegistryPersistence
from src.heredity.version_registry import (
    MigrationStatus,
    VersionRegistry,
    VersionStatus,
)

SEEDS = [
    ("seed-a", "SeedA", "0.1.0", {}),
    ("opensoul", "OpenSoul", "0.1.0", {"opennerve": ">=0.1.0"}),
]


def _registry(db_path, seeds=None):
    """重启模拟=全新实例同库（内存真源归零、行影子回载）。"""
    return VersionRegistry(
        persist=RegistryPersistence(db_path=str(db_path)),
        seed_components=list(seeds or []),
    )


def _count(db_path, table):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# ── A 组件版本面 ──────────────────────────────────────────────────


class TestComponentVersions:
    def test_component_roundtrip_after_restart(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        a.register(
            "probe-comp",
            "ProbeComp",
            "1.0.0",
            dependencies={"opensoul": ">=0.1.0"},
            config_schema={"k": {"type": "string"}},
            release_notes="first",
            breaking_changes=["renamed x"],
        )
        b = _registry(db)  # 重启
        current = b.get_current("probe-comp")
        assert current is not None
        assert current.version == "1.0.0"
        assert current.component_name == "ProbeComp"
        assert current.status == VersionStatus.ACTIVE
        assert current.release_notes == "first"
        assert current.dependencies == {"opensoul": ">=0.1.0"}
        assert current.config_schema == {"k": {"type": "string"}}
        assert current.breaking_changes == ["renamed x"]
        assert current.retired_at is None
        assert len(b.get_all_versions("probe-comp")) == 1
        listed = {c["component_id"]: c for c in b.list_components()}
        assert listed["probe-comp"]["current_version"] == "1.0.0"
        assert listed["probe-comp"]["total_versions"] == 1

    def test_deprecated_status_survives_restart(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        a.register("comp-x", "CompX", "1.0.0")
        a.register("comp-x", "CompX", "2.0.0")  # 1.0.0 → deprecated
        b = _registry(db)
        versions = {v.version: v.status for v in b.get_all_versions("comp-x")}
        assert versions == {"1.0.0": VersionStatus.DEPRECATED, "2.0.0": VersionStatus.ACTIVE}
        current = b.get_current("comp-x")
        assert current is not None and current.version == "2.0.0"  # 不被旧版顶回

    def test_seed_only_fills_missing(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db, seeds=SEEDS)
        assert len(a.list_components()) == 2  # 首触播种
        a.register("opensoul", "OpenSoul", "0.2.0")  # API 注册新版本
        rows_before = _count(db, "component_versions")
        assert rows_before == 3  # 2 seed + 1 API
        b = _registry(db, seeds=SEEDS)  # 重启+同种子
        current = b.get_current("opensoul")
        assert current is not None and current.version == "0.2.0"  # 种子不顶掉 API 版本
        versions = {v.version: v.status for v in b.get_all_versions("opensoul")}
        assert versions["0.1.0"] == VersionStatus.DEPRECATED
        assert len(b.get_all_versions("opensoul")) == 2
        seed_current = b.get_current("seed-a")
        assert seed_current is not None and seed_current.version == "0.1.0"
        assert _count(db, "component_versions") == rows_before  # 种子不重复写行

    def test_first_run_seed_rows_landed(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db, seeds=[("seed-a", "SeedA", "0.1.0", {})])
        a.list_components()  # 首触
        assert _count(db, "component_versions") == 1  # 种子行真落盘
        assert _count(db, "changelog_entries") == 1  # 自动 changelog 随行

    def test_bad_status_row_skipped_visible(self, tmp_path, caplog):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        a.register("good-comp", "Good", "1.0.0")
        a.register("bad-comp", "Bad", "1.0.0")
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "UPDATE component_versions SET status = 'bogus' WHERE component_id = 'bad-comp'"
            )
            conn.commit()
        finally:
            conn.close()
        b = _registry(db)
        with caplog.at_level("ERROR"):
            assert b.get_current("bad-comp") is None  # 坏枚举行跳过
            assert b.get_current("good-comp") is not None
        assert any("坏行跳过" in r.message for r in caplog.records)

    def test_pure_memory_zero_change(self, tmp_path):
        reg = VersionRegistry()  # persist 缺省 None=修前纯内存
        reg.register("mem-comp", "Mem", "1.0.0")
        assert reg.get_current("mem-comp").version == "1.0.0"
        assert reg.get_stats()["total_components"] == 1  # 内存派生
        assert not (tmp_path / "heredity.db").exists()  # 零库写入


# ── B 迁移计划面（含 execute/rollback 状态）──────────────────────


class TestMigrationPlans:
    def test_migration_roundtrip_after_restart(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        plan = a.create_migration(
            "comp-m",
            "1.0.0",
            "2.0.0",
            steps=[{"action": "custom", "description": "step one"}],
            dry_run=True,
        )
        b = _registry(db)
        loaded = {m.migration_id: m for m in b.list_migrations()}
        m = loaded[plan.migration_id]
        assert m.component_id == "comp-m"
        assert (m.from_version, m.to_version) == ("1.0.0", "2.0.0")
        assert m.steps == [{"action": "custom", "description": "step one"}]
        assert m.dry_run is True
        assert m.status == MigrationStatus.PENDING
        assert m.rollback_plan  # 默认回滚计划随行

    def test_execute_state_survives_restart(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        a.register("comp-e", "CompE", "1.0.0")
        plan = a.create_migration("comp-e", "1.0.0", "2.0.0")
        a.execute_migration(plan.migration_id)
        assert plan.status == MigrationStatus.COMPLETED
        b = _registry(db)
        m = {x.migration_id: x for x in b.list_migrations()}[plan.migration_id]
        assert m.status == MigrationStatus.COMPLETED
        assert m.started_at is not None and m.completed_at is not None
        assert all(s.get("status") == "completed" for s in m.steps)
        assert all("executed_at" in s for s in m.steps)
        current = b.get_current("comp-e")
        assert current is not None and current.version == "2.0.0"  # execute 自动注册随行
        cl = b.get_changelog(component_id="comp-e")
        assert any("Migration completed" in e["description"] for e in cl)

    def test_rollback_state_survives_restart(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        plan = a.create_migration("comp-r", "1.0.0", "2.0.0")
        a.rollback_migration(plan.migration_id)
        b = _registry(db)
        m = {x.migration_id: x for x in b.list_migrations()}[plan.migration_id]
        assert m.status == MigrationStatus.ROLLED_BACK
        assert any(
            "rolled back" in e["description"] for e in b.get_changelog(component_id="comp-r")
        )

    def test_execute_not_found_contract(self):
        reg = VersionRegistry()
        with pytest.raises(ValueError, match="not found"):
            reg.execute_migration("mig_missing")

    def test_migration_stats_statuses(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        p1 = a.create_migration("comp-s", "1.0.0", "2.0.0")
        a.execute_migration(p1.migration_id)
        p2 = a.create_migration("comp-s", "2.0.0", "3.0.0")
        a.rollback_migration(p2.migration_id)
        stats = a.get_stats()
        assert stats["total_migrations"] == 2
        assert stats["migration_statuses"] == {
            MigrationStatus.COMPLETED.value: 1,
            MigrationStatus.ROLLED_BACK.value: 1,
        }


# ── C changelog 面（追加型）──────────────────────────────────────


class TestChangelog:
    def test_changelog_roundtrip_no_dup(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        a.register("comp-c", "CompC", "1.0.0", release_notes="first")
        a.register("comp-c", "CompC", "1.1.0", release_notes="second")
        b = _registry(db)
        entries = b.get_changelog(component_id="comp-c")
        assert len(entries) == 2  # 一注册一条史
        assert entries[0]["description"] == "second"  # 时间倒序
        assert len(b.get_changelog(component_id="comp-c")) == 2  # 重复读不重复

    def test_append_conflict_raises_no_replace(self, tmp_path):
        persist = RegistryPersistence(db_path=str(tmp_path / "heredity.db"))
        row = {
            "entry_id": "cl_dup",
            "component_id": "c",
            "version": "1",
            "change_type": "fix",
            "description": "d",
            "author": "system",
            "timestamp": 1.0,
        }
        persist.append_changelog(row)
        with pytest.raises(sqlite3.IntegrityError):  # append 表禁 REPLACE 撞行可见
            persist.append_changelog(row)

    def test_changelog_filter_limit_contract(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        for i in range(5):
            a.register("comp-l", "CompL", f"1.0.{i}")
        assert len(a.get_changelog(component_id="comp-l", limit=3)) == 3
        assert len(a.get_changelog(component_id="other")) == 0


# ── D platform_version（meta kv 标量态）─────────────────────────


class TestPlatformVersion:
    def test_platform_version_survives_restart(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        assert a.bump_platform_version("minor") == "1.1.0"
        b = _registry(db)
        assert b.get_platform_version()["platform_version"] == "1.1.0"  # 修前回 1.0.0
        assert any("Platform bumped to 1.1.0" in e["description"] for e in b.get_changelog())

    def test_platform_default_zero_change(self, tmp_path):
        b = _registry(tmp_path / "heredity.db")
        assert b.get_platform_version()["platform_version"] == "1.0.0"


# ── E 回载/写穿纪律 ─────────────────────────────────────────────


class TestPersistDiscipline:
    def test_load_failure_retries_next_touch(self, tmp_path, caplog):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        a.register("pre-comp", "Pre", "1.0.0")

        class Flaky(RegistryPersistence):
            fail_next = 1

            def load_component_versions(self):
                if Flaky.fail_next > 0:
                    Flaky.fail_next -= 1
                    raise RuntimeError("simulated read failure")
                return super().load_component_versions()

        b = VersionRegistry(
            persist=Flaky(db_path=str(db)),
            seed_components=[("seed-a", "SeedA", "0.1.0", {})],
        )
        with caplog.at_level("ERROR"):
            assert b.get_current("pre-comp") is None  # 首触回载失败=空起步
        assert b.get_current("seed-a") is not None  # SEED 照常落地（注册表不空转）
        assert b.get_current("pre-comp") is not None  # 下次触碰重试真回载
        assert len(b.get_all_versions("seed-a")) == 1  # 重试不双播
        assert any("回载失败" in r.message for r in caplog.records)

    def test_write_failure_does_not_block_action(self, tmp_path, caplog):
        class Broken(RegistryPersistence):
            def upsert_component(self, row):
                raise RuntimeError("disk full")

        reg = VersionRegistry(persist=Broken(db_path=str(tmp_path / "heredity.db")))
        with caplog.at_level("ERROR"):
            cv = reg.register("w-comp", "W", "1.0.0")
        assert cv is not None  # 动作本体不被落盘失败炸掉
        assert reg.get_current("w-comp") is not None  # 内存已生效
        assert any("落盘失败" in r.message for r in caplog.records)  # 故障可见

    def test_counters_shape_matches_get_stats(self, tmp_path):
        db = tmp_path / "heredity.db"
        a = _registry(db)
        a.register("comp-n", "CompN", "1.0.0")
        persist = RegistryPersistence(db_path=str(db))
        assert set(persist.counters().keys()) == set(a.get_stats().keys())


# ── F API 全链（真 router + tmp 库；重启=新实例回载）──────────────


class TestApiFace:
    def test_api_register_survives_restart(self, tmp_path):
        app = FastAPI()
        from src.api.heredity import router as heredity_router

        app.include_router(heredity_router, prefix="/api/heredity")
        client = TestClient(app)
        resp = client.post(
            "/api/heredity/components",
            json={"component_id": "rv1b14-api", "component_name": "Rv1b14", "version": "1.0.0"},
        )
        assert resp.status_code == 200
        got = client.get("/api/heredity/components/rv1b14-api")
        assert got.status_code == 200
        assert got.json()["current"]["version"] == "1.0.0"
        # 重启模拟：新实例同 env 库回载（模块单例 persist 路径=HEREDITY_REGISTRY_DB tmp）
        b = _registry(os.environ["HEREDITY_REGISTRY_DB"])
        current = b.get_current("rv1b14-api")
        assert current is not None and current.version == "1.0.0"

    def test_api_migration_execute_state_visible(self):
        app = FastAPI()
        from src.api.heredity import router as heredity_router

        app.include_router(heredity_router, prefix="/api/heredity")
        client = TestClient(app)
        resp = client.post(
            "/api/heredity/migrations",
            json={"component_id": "rv1b14-mig", "from_version": "1.0.0", "to_version": "2.0.0"},
        )
        assert resp.status_code == 200
        mid = resp.json()["migration_id"]
        assert client.post(f"/api/heredity/migrations/{mid}/execute").status_code == 200
        listed = client.get("/api/heredity/migrations").json()["migrations"]
        row = {m["migration_id"]: m for m in listed}[mid]
        assert row["status"] == MigrationStatus.COMPLETED.value
        b = _registry(os.environ["HEREDITY_REGISTRY_DB"])
        m = {x.migration_id: x for x in b.list_migrations()}[mid]
        assert m.status == MigrationStatus.COMPLETED  # 重启后 execute 状态不丢
