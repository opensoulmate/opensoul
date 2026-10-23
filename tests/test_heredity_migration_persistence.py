"""RV-①b-⑭-2 MigrationEngine 两面持久化测试——注册 schema / 迁移脚本 applied 状态
重启不丢（RV-①c §5.1 藏活事实 ⑭ 拆件第②件；⑭-1 VersionRegistry 三面已落）。

数据面全 tmp（显式 MigrationPersistence(db_path=tmp) 新实例=重启模拟；模块单例面走
HEREDITY_MIGRATION_DB autouse 隔离），绝不写生产 data/heredity_migration.db。
"""

import os
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.heredity.migration import MigrationEngine, MigrationType
from src.heredity.migration_persist import MigrationPersistence


def _engine(db_path):
    """重启模拟=全新实例同库（内存真源归零、行影子回载）。"""
    return MigrationEngine(persist=MigrationPersistence(db_path=str(db_path)))


def _count(db_path, table):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


FIELDS_V1 = [
    {"name": "title", "field_type": "string", "required": True},
    {
        "name": "score",
        "field_type": "number",
        "required": False,
        "default": 0,
        "description": "评分",
    },
]


# ── A 注册 schema 面 ─────────────────────────────────────────────


class TestSchemas:
    def test_schema_roundtrip_after_restart(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)
        sv = a.register_schema("comp-a", "1.0.0", FIELDS_V1)
        b = _engine(db)  # 重启
        got = b.get_schema("comp-a")
        assert got is not None
        assert got.schema_id == sv.schema_id
        assert got.version == "1.0.0"
        assert [f.name for f in got.fields] == ["title", "score"]
        assert got.fields[0].field_type == "string"
        assert got.fields[0].required is True
        assert got.fields[1].required is False
        assert got.fields[1].default == 0
        assert got.fields[1].description == "评分"

    def test_latest_and_by_version_after_restart(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)
        a.register_schema("comp-l", "1.0.0", FIELDS_V1)
        a.register_schema("comp-l", "2.0.0", [{"name": "x", "field_type": "boolean"}])
        b = _engine(db)
        latest = b.get_schema("comp-l")
        assert latest is not None and latest.version == "2.0.0"  # latest=注册序末
        v1 = b.get_schema("comp-l", "1.0.0")
        assert v1 is not None and v1.version == "1.0.0"
        assert b.get_schema("comp-l", "9.9.9") is None
        assert b.get_schema("comp-none") is None

    def test_diff_schemas_after_restart(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)
        a.register_schema("comp-d", "1.0.0", FIELDS_V1)
        a.register_schema(
            "comp-d",
            "2.0.0",
            [{"name": "title", "field_type": "number"}, {"name": "extra", "field_type": "list"}],
        )
        b = _engine(db)
        diff = b.diff_schemas("comp-d", "1.0.0", "2.0.0")
        assert [f.name for f in diff.added] == ["extra"]
        assert diff.removed == ["score"]
        assert diff.modified == [{"name": "title", "old_type": "string", "new_type": "number"}]


# ── B 迁移脚本 applied 状态面 ────────────────────────────────────


class TestScripts:
    def test_script_roundtrip_after_restart(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)
        s = a.create_migration_script(
            "kb_schema",
            "comp-s",
            "1.0.0",
            "2.0.0",
            up_sql="ALTER TABLE t ADD c TEXT",
            down_sql="ALTER TABLE t DROP c",
            transform="migrate_up",
        )
        b = _engine(db)
        rows = b.list_scripts(component_id="comp-s")
        assert len(rows) == 1
        row = rows[0]
        assert row["script_id"] == s.script_id
        assert row["migration_type"] == MigrationType.KB_SCHEMA.value
        assert row["from_version"] == "1.0.0"
        assert row["to_version"] == "2.0.0"
        assert row["applied"] is False
        assert row["applied_at"] is None
        # 脚本内容随行保真（公共读面不出 SQL 列=既有契约零变化，直接对状态真源）
        internal = {x.script_id: x for x in b._scripts}[s.script_id]
        assert internal.up_sql == "ALTER TABLE t ADD c TEXT"
        assert internal.down_sql == "ALTER TABLE t DROP c"
        assert internal.transform == "migrate_up"

    def test_applied_state_survives_restart(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)
        s1 = a.create_migration_script("config", "comp-p", "1.0.0", "2.0.0")
        s2 = a.create_migration_script("data", "comp-p", "2.0.0", "3.0.0")
        a.apply_script(s1.script_id)
        b = _engine(db)  # 重启
        applied = {r["script_id"]: r for r in b.list_scripts(applied=True)}
        assert set(applied) == {s1.script_id}  # 修前重启后 applied 归零=白标
        assert applied[s1.script_id]["applied"] is True
        assert applied[s1.script_id]["applied_at"] is not None
        pending = {r["script_id"] for r in b.list_scripts(applied=False)}
        assert pending == {s2.script_id}
        assert {r["script_id"] for r in b.list_scripts(component_id="comp-p")} == {
            s1.script_id,
            s2.script_id,
        }

    def test_apply_not_found_contract_unchanged(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)
        a.create_migration_script("plugin", "comp-n", "1.0.0", "2.0.0")
        try:
            a.apply_script("ms_nonexistent")
            raise AssertionError("应抛 ValueError")
        except ValueError as e:
            assert "not found" in str(e)  # 既有契约零变化


# ── C 计数真源（MEM-⑥）──────────────────────────────────────────


class TestCounters:
    def test_stats_shape_and_truth_after_restart(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)
        a.register_schema("comp-c", "1.0.0", FIELDS_V1)
        a.register_schema("comp-c2", "1.0.0", FIELDS_V1)
        s = a.create_migration_script("kb_schema", "comp-c", "1.0.0", "2.0.0")
        a.apply_script(s.script_id)
        b = _engine(db)
        stats = b.get_stats()
        assert set(stats.keys()) == {
            "total_components",
            "total_schema_versions",
            "total_scripts",
            "applied_scripts",
            "pending_scripts",
        }
        assert stats == {
            "total_components": 2,
            "total_schema_versions": 2,
            "total_scripts": 1,
            "applied_scripts": 1,
            "pending_scripts": 0,
        }

    def test_counters_row_truth_not_memory(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)  # 内存空
        persist = MigrationPersistence(db_path=str(db))
        persist.upsert_schema(
            {
                "schema_id": "sch_row",
                "component_id": "comp-r",
                "version": "1.0.0",
                "fields": [],
                "created_at": 1.0,
            }
        )
        # 内存零条但计数恒对账行聚合（行真源，禁内存缓存假 0）
        assert a.get_stats()["total_schema_versions"] == 1

    def test_counters_failure_falls_back_memory(self, tmp_path, caplog):
        class Broken(MigrationPersistence):
            def counters(self):
                raise RuntimeError("db locked")

        db = tmp_path / "heredity_mig.db"
        a = MigrationEngine(persist=Broken(db_path=str(db)))
        a.register_schema("comp-f", "1.0.0", FIELDS_V1)
        with caplog.at_level("ERROR"):
            stats = a.get_stats()
        assert stats["total_schema_versions"] == 1  # 回落内存派生禁假 0
        assert any("计数对账失败" in r.message for r in caplog.records)

    def test_counters_shape_matches_get_stats(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)
        a.register_schema("comp-shape", "1.0.0", FIELDS_V1)
        persist = MigrationPersistence(db_path=str(db))
        assert set(persist.counters().keys()) == set(a.get_stats().keys())


# ── D 纯内存零变化 + 回载/写穿纪律 ───────────────────────────────


class TestPersistDiscipline:
    def test_pure_memory_default_zero_persist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HEREDITY_MIGRATION_DB", str(tmp_path / "should_not_exist.db"))
        e = MigrationEngine()  # persist 缺省 None
        e.register_schema("comp-m", "1.0.0", FIELDS_V1)
        s = e.create_migration_script("config", "comp-m", "1.0.0", "2.0.0")
        e.apply_script(s.script_id)
        assert e.get_schema("comp-m") is not None
        assert e.list_scripts(applied=True)[0]["applied"] is True
        assert not (tmp_path / "should_not_exist.db").exists()  # 零落盘字节不变

    def test_load_failure_retries_next_touch(self, tmp_path, caplog):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)
        a.register_schema("pre-comp", "1.0.0", FIELDS_V1)

        class Flaky(MigrationPersistence):
            fail_next = 1

            def load_schemas(self):
                if Flaky.fail_next > 0:
                    Flaky.fail_next -= 1
                    raise RuntimeError("simulated read failure")
                return super().load_schemas()

        b = MigrationEngine(persist=Flaky(db_path=str(db)))
        with caplog.at_level("ERROR"):
            assert b.get_schema("pre-comp") is None  # 首触回载失败=空起步
        assert b.get_schema("pre-comp") is not None  # 下次触碰重试真回载
        assert len(b._schemas["pre-comp"]) == 1  # 重试不双行
        assert any("回载失败" in r.message for r in caplog.records)

    def test_write_failure_does_not_block_action(self, tmp_path, caplog):
        class Broken(MigrationPersistence):
            def upsert_schema(self, row):
                raise RuntimeError("disk full")

            def upsert_script(self, row):
                raise RuntimeError("disk full")

        e = MigrationEngine(persist=Broken(db_path=str(tmp_path / "heredity_mig.db")))
        with caplog.at_level("ERROR"):
            sv = e.register_schema("w-comp", "1.0.0", FIELDS_V1)
            s = e.create_migration_script("config", "w-comp", "1.0.0", "2.0.0")
            e.apply_script(s.script_id)
        assert sv is not None and s is not None  # 动作本体不被落盘失败炸掉
        assert e.get_schema("w-comp") is not None  # 内存已生效
        assert e.list_scripts(applied=True)[0]["applied"] is True
        assert any("落盘失败" in r.message for r in caplog.records)  # 故障可见

    def test_write_first_load_before_record(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)
        a.register_schema("pre-row", "1.0.0", FIELDS_V1)
        b = _engine(db)
        b.register_schema("new-row", "1.0.0", FIELDS_V1)  # 写首触：先载后录
        assert b.get_schema("pre-row") is not None  # 回载旧行不被写丢
        assert b.get_schema("new-row") is not None

    def test_only_fill_missing_no_overwrite(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        a = _engine(db)
        sv = a.register_schema("comp-of", "1.0.0", FIELDS_V1)
        # 库外改行（模拟双源漂移）后重触：内存在册更真，禁回载覆盖
        persist = MigrationPersistence(db_path=str(db))
        persist.upsert_schema(
            {
                "schema_id": sv.schema_id,
                "component_id": "comp-of",
                "version": "9.9.9",
                "fields": [],
                "created_at": 2.0,
            }
        )
        a._migration_loaded = False
        a._ensure_loaded()
        kept = a.get_schema("comp-of")
        assert kept is not None and kept.version == "1.0.0"  # 只补缺不覆盖

    def test_lazy_load_once(self, tmp_path):
        db = tmp_path / "heredity_mig.db"
        calls = {"n": 0}

        class Spy(MigrationPersistence):
            def load_schemas(self):
                calls["n"] += 1
                return super().load_schemas()

        e = MigrationEngine(persist=Spy(db_path=str(db)))
        e.register_schema("comp-once", "1.0.0", FIELDS_V1)
        e.get_schema("comp-once")
        e.get_stats()
        e.list_scripts()
        assert calls["n"] == 1  # 首触懒回载恰一次

    def test_bad_rows_skipped_visible(self, tmp_path, caplog):
        db = tmp_path / "heredity_mig.db"
        persist = MigrationPersistence(db_path=str(db))
        persist.upsert_schema(
            {
                "schema_id": "sch_ok",
                "component_id": "comp-bad",
                "version": "1.0.0",
                "fields": [],
                "created_at": 1.0,
            }
        )
        # 坏行 1：fields JSON 不可解（persist 层跳过）
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO schema_versions (schema_id, component_id, version, fields, created_at)"
            " VALUES ('sch_bad_json', 'comp-bad', '2.0.0', 'not-json', 1.0)"
        )
        # 坏行 2：fields 元素缺字段（engine 层 SchemaField(**f) 跳过）
        conn.execute(
            "INSERT INTO schema_versions (schema_id, component_id, version, fields, created_at)"
            " VALUES ('sch_bad_field', 'comp-bad', '3.0.0', '[{\"nope\": 1}]', 1.0)"
        )
        # 坏行 3：migration_type 枚举不可解（engine 层跳过）
        conn.execute(
            "INSERT INTO migration_scripts (script_id, migration_type, component_id, from_version,"
            " to_version, created_at, applied) VALUES ('ms_bad_type', 'ghost_type', 'comp-bad',"
            " '1.0.0', '2.0.0', 1.0, 0)"
        )
        conn.commit()
        conn.close()
        e = _engine(db)
        with caplog.at_level("ERROR"):
            good = e.get_schema("comp-bad")
            assert good is not None and good.schema_id == "sch_ok"  # 好行照常
            assert e.list_scripts() == []  # 坏行不进
        assert any("坏行跳过" in r.message for r in caplog.records)  # 跳过必留真因


# ── E API 全链（真 router + tmp 库；重启=新实例回载）──────────────


class TestApiFace:
    def _client(self):
        app = FastAPI()
        from src.api.heredity import router as heredity_router

        app.include_router(heredity_router, prefix="/api/heredity")
        return TestClient(app)

    def test_api_schema_survives_restart(self):
        client = self._client()
        resp = client.post(
            "/api/heredity/schemas",
            json={"component_id": "rv1b142-api", "version": "1.0.0", "fields": FIELDS_V1},
        )
        assert resp.status_code == 200
        got = client.get("/api/heredity/schemas/rv1b142-api")
        assert got.status_code == 200
        assert got.json()["version"] == "1.0.0"
        assert got.json()["fields"][0]["name"] == "title"
        # 重启模拟：新实例同 env 库回载（模块单例 persist 路径=HEREDITY_MIGRATION_DB tmp）
        b = _engine(os.environ["HEREDITY_MIGRATION_DB"])
        assert b.get_schema("rv1b142-api") is not None  # 修前重启 404

    def test_api_script_apply_state_survives_restart(self):
        client = self._client()
        resp = client.post(
            "/api/heredity/scripts",
            json={
                "migration_type": "kb_schema",
                "component_id": "rv1b142-mig",
                "from_version": "1.0.0",
                "to_version": "2.0.0",
                "up_sql": "ALTER TABLE t ADD c TEXT",
            },
        )
        assert resp.status_code == 200
        sid = resp.json()["script_id"]
        assert client.post(f"/api/heredity/scripts/{sid}/apply").status_code == 200
        listed = client.get("/api/heredity/scripts").json()["scripts"]
        row = {s["script_id"]: s for s in listed}[sid]
        assert row["applied"] is True  # 修前重启后 applied 归零
        b = _engine(os.environ["HEREDITY_MIGRATION_DB"])
        row2 = {s["script_id"]: s for s in b.list_scripts()}[sid]
        assert row2["applied"] is True
        assert row2["applied_at"] is not None
