"""Capture schema漂移自愈 + /stats PG方言修复 + 失败可见 行为钉。

数据面全隔离（conftest.capture_client：tmp库以生产旧版漂移schema开局），
零生产 data/opensoul.db 接触。背景见 fixture docstring 与
src/api/capture.py _ensure_table docstring（2026-10-06 capture复活轮）。
"""

import sqlite3
import time
from pathlib import Path

_NEW_COLS = {
    "id",
    "capture_type",
    "title",
    "url",
    "description",
    "keywords",
    "content",
    "content_hash",
    "status",
    "created_at",
    "user_id",
}


def _db_path(client) -> str:
    return client.app.state.capture_client_db


def _tables(db: str) -> set[str]:
    with sqlite3.connect(db) as con:
        return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(db: str, table: str = "captures") -> set[str]:
    with sqlite3.connect(db) as con:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


class TestLegacyDriftSelfHeal:
    """生产实况形态（旧版漂移schema）→ 请求即自愈，旧行按列映射不丢。"""

    def test_legacy_rows_preserved_and_queries_work(self, capture_client):
        db = _db_path(capture_client)
        # 趁还是旧版漂移schema时按旧列种一行（生产形态原样）
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT INTO captures (id, user_id, type, content, metadata, created_at) "
                "VALUES ('old-1', 'u1', 'page', 'hello world', '{\"url\": \"https://x.example\"}', "
                "'2026-10-06 10:00:00')"
            )
            con.commit()

        r = capture_client.get("/api/capture/health")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "ok"
        # 修复前：恒 total=0 + note:"table not yet initialized" 谎报健康
        assert d["total_captures"] == 1
        assert d["page_captures"] == 1
        assert "note" not in d
        assert "error" not in d

        # 新列可查询（修复前 WHERE capture_type 恒报 no such column）
        r2 = capture_client.get("/api/capture/list?capture_type=page")
        assert r2.status_code == 200
        items = r2.json()["captures"]
        assert len(items) == 1
        row = items[0]
        assert row["capture_type"] == "page"  # type→capture_type 映射
        assert row["content"] == "hello world"
        assert row["description"] == '{"url": "https://x.example"}'  # metadata→description 原文保留
        assert row["status"] == "migrated"
        assert row["user_id"] == "u1"
        assert isinstance(row["id"], int)  # id重排为新自增整型

        # schema已换新系 + 备份表清场
        assert _NEW_COLS <= _cols(db)
        assert "captures_legacy_backup" not in _tables(db)

    def test_page_insert_works_after_migration(self, capture_client):
        # 修复前：INSERT INTO captures (capture_type,...) 恒500（no such column）
        payload = {"url": "https://ex.example/a", "title": "A", "content": "body A"}
        r = capture_client.post("/api/capture/page", json=payload)
        assert r.status_code == 200
        assert r.json()["status"] == "captured"

        # 内容去重语义不因迁移破坏
        r2 = capture_client.post("/api/capture/page", json=payload)
        assert r2.status_code == 200
        assert r2.json()["status"] == "duplicate"

        stats = capture_client.get("/api/capture/stats").json()
        assert stats["status"] == "ok"
        assert stats["total_captures"] == 1
        assert stats["page_captures"] == 1

    def test_selection_insert_works_after_migration(self, capture_client):
        r = capture_client.post(
            "/api/capture/selection",
            json={"text": "selected words", "url": "https://ex.example/s", "title": "S"},
        )
        assert r.status_code == 200
        assert r.json()["type"] == "selection"
        stats = capture_client.get("/api/capture/stats").json()
        assert stats["selection_captures"] == 1


class TestStatsDialectFix:
    """/stats 的 PG 方言残留（EXTRACT/INTERVAL）在 SQLite 必炸→修复后真实计数。"""

    def test_recent_24h_window_excludes_old_rows(self, capture_client):
        # 先触发一次迁移，拿到新schema再种新列行
        assert capture_client.get("/api/capture/health").status_code == 200
        db = _db_path(capture_client)
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT INTO captures (capture_type, title, content, created_at) VALUES ('page', 'old', 'c1', ?)",
                (time.time() - 2 * 86400,),
            )
            con.execute(
                "INSERT INTO captures (capture_type, title, content, created_at) VALUES ('selection', 'new', 'c2', ?)",
                (time.time(),),
            )
            con.commit()

        stats = capture_client.get("/api/capture/stats").json()
        assert stats["status"] == "ok"
        # 修复前：EXTRACT(EPOCH...)在SQLite异常→整个响应被吞成全0
        assert stats["total_captures"] == 2
        assert stats["selection_captures"] == 1
        assert stats["recent_24h"] == 1  # 只算24h内


class TestSchemaGuardIdempotence:
    def test_repeated_requests_do_not_duplicate_or_fail(self, capture_client):
        assert (
            capture_client.post(
                "/api/capture/page",
                json={"url": "https://ex.example/idem", "title": "T", "content": "C"},
            ).status_code
            == 200
        )
        for _ in range(3):
            r = capture_client.get("/api/capture/health")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"
        assert capture_client.get("/api/capture/stats").json()["total_captures"] == 1

    def test_fresh_db_creates_new_schema_directly(self, capture_client):
        db = _db_path(capture_client)
        with sqlite3.connect(db) as con:
            con.execute("DROP TABLE captures")
            con.commit()
        r = capture_client.post(
            "/api/capture/page",
            json={"url": "https://ex.example/fresh", "title": "F", "content": "FC"},
        )
        assert r.status_code == 200
        assert _NEW_COLS <= _cols(db)

    def test_leftover_backup_recovered_after_crash(self, capture_client):
        """重建中途崩溃（备份表残留+新表空）→ 下次请求补回收再清场。"""
        assert capture_client.get("/api/capture/health").status_code == 200  # 先迁移到新系
        db = _db_path(capture_client)
        with sqlite3.connect(db) as con:
            con.execute("DROP TABLE captures")
            con.execute(
                "CREATE TABLE captures (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "capture_type TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', "
                "url TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', "
                "keywords TEXT NOT NULL DEFAULT '[]', content TEXT NOT NULL DEFAULT '', "
                "content_hash TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'captured', "
                "created_at REAL NOT NULL, user_id TEXT NOT NULL DEFAULT 'browser-extension')"
            )
            con.execute(
                "CREATE TABLE captures_legacy_backup (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
                "type TEXT DEFAULT '', content TEXT DEFAULT '', metadata TEXT DEFAULT '{}', "
                "created_at TEXT DEFAULT (datetime('now')))"
            )
            con.execute(
                "INSERT INTO captures_legacy_backup (id, user_id, type, content) "
                "VALUES ('b1', 'u1', 'selection', 'rescued')"
            )
            con.commit()

        r = capture_client.get("/api/capture/health")
        assert r.status_code == 200
        assert r.json()["total_captures"] == 1
        items = capture_client.get("/api/capture/list").json()["captures"]
        assert items[0]["content"] == "rescued"
        assert items[0]["capture_type"] == "selection"
        assert "captures_legacy_backup" not in _tables(db)


class TestFailureVisible:
    """失败必须可见：检查失败≠健康（修复前吞异常返回status:"ok"）。"""

    class _BrokenPool:
        async def execute(self, *a, **k):
            raise RuntimeError("db exploded")

        async def fetch(self, *a, **k):
            raise RuntimeError("db exploded")

        async def fetchval(self, *a, **k):
            raise RuntimeError("db exploded")

    def test_health_failure_returns_503_degraded(self, capture_client, monkeypatch):
        import src.api.capture as capture_api

        monkeypatch.setattr(capture_api, "db_pool", self._BrokenPool())
        r = capture_client.get("/api/capture/health")
        assert r.status_code == 503
        d = r.json()
        assert d["status"] == "degraded"
        assert "db exploded" in d["error"]
        assert "note" not in d  # 旧的"table not yet initialized"谎报文案已废

    def test_stats_failure_returns_503_degraded(self, capture_client, monkeypatch):
        import src.api.capture as capture_api

        monkeypatch.setattr(capture_api, "db_pool", self._BrokenPool())
        r = capture_client.get("/api/capture/stats")
        assert r.status_code == 503
        d = r.json()
        assert d["status"] == "degraded"
        assert "db exploded" in d["error"]


class TestRuntimeWiring:
    """接线钉：router真实挂载 + 自愈入口在每个端点的必经路径。"""

    def test_capture_router_included_in_main(self):
        root = Path(__file__).resolve().parents[1]
        main_src = (root / "src" / "main.py").read_text()
        assert "app.include_router(capture_router" in main_src
        assert 'prefix="/api/capture"' in main_src

    def test_ensure_table_on_every_endpoint_path(self):
        root = Path(__file__).resolve().parents[1]
        cap_src = (root / "src" / "api" / "capture.py").read_text()
        # 每个端点入口都过 _ensure_table（自愈在真实请求路径，非死代码）
        assert cap_src.count("await _ensure_table()") >= 8
        assert "def _migrate_legacy_captures" in cap_src
        assert "await _migrate_legacy_captures(cols)" in cap_src
