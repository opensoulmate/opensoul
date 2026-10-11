"""F3b-③c-2②a proactive_events 表数据面隔离接线测试。

覆盖四层（全部 tmp 独立库 + NEST_DB/USERS_DB/ENTERPRISE_DB 环境隔离，测试行
绝不写生产 data/proactive.db）：
①proactive_events.tenant_id 列迁移（老库补列+存量行回填 'default'+租户索引）
  与落行归属（手动触发=派生租户/心跳调度=引擎租户/缺失空白→'default' 兜底）
②查询过滤（recent_events 恒按租户，DB/内存双数据面 + DB 故障降级路径同样
  恒带租户键禁跨租户泄露）
③IsolationEngine.check_access 真执行（bulk 审计留痕 total_access_checks>0；
  系统视图 tenant_id=None 不闸零审计噪音）
④API 层派生租户全链（auth token → bind_identity → 行归属落库 → 跨租户不可见，
  真实 proactive router；RunResponse 契约不变、匿名 default 契约不变）

注：src.will.proactive 模块级 proactive_engine 单例（import 即构造，DB 懒连接
零生产接触）——本文件 fixture monkeypatch 换 tmp 实例，测试行零生产接触。
"""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb
from src.will.proactive import DEFAULT_TENANT, ProactiveEngine

# 生产 proactive.db proactive_events 表 F3b-③c-2②a 迁移前形状（无 tenant_id）
OLD_EVENTS_DDL = """CREATE TABLE proactive_events (
                    id TEXT PRIMARY KEY, watcher TEXT NOT NULL,
                    ok INTEGER NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL, duration_ms REAL NOT NULL,
                    result TEXT DEFAULT '{}', error TEXT DEFAULT '')"""

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _ent_token(uid, username="u"):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _insert_ent_user(db, username, tenant, is_active=1):
    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, is_active, tenant_id) VALUES (?, ?, ?, ?)",
            (username, "hashed", is_active, tenant),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _engine(tmp_path):
    eng = ProactiveEngine(db_path=str(tmp_path / "proactive.db"))
    eng.register_watcher("w", lambda: {"n": 1}, interval_seconds=3600)
    return eng


def _run(eng, tenant=None):
    return asyncio.run(eng.run_watcher("w", tenant_id=tenant))


# ── ① 迁移 + 落行归属 ─────────────────────────────────────────────────


class TestMigrationAndAttribution:
    def test_tenant_column_added_and_backfilled(self, tmp_path):
        db = tmp_path / "proactive.db"
        conn = sqlite3.connect(db)
        conn.execute(OLD_EVENTS_DDL)
        conn.execute(
            "INSERT INTO proactive_events (id, watcher, ok, started_at, finished_at, duration_ms)"
            " VALUES ('evt_old', 'w', 1, '2026-01-01T00:00:00', '2026-01-01T00:00:00', 1.0)"
        )
        conn.commit()
        conn.close()
        eng = ProactiveEngine(db_path=str(db))
        got = eng.recent_events()  # 触发 _ensure_db 迁移
        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(proactive_events)").fetchall()]
        row = conn.execute("SELECT tenant_id FROM proactive_events WHERE id = 'evt_old'").fetchone()
        idx = [r[1] for r in conn.execute("PRAGMA index_info(idx_pe_tenant)").fetchall()]
        conn.close()
        assert "tenant_id" in cols
        assert row[0] == "default"  # 存量行回填 default（与生产数据分布一致）
        assert idx  # idx_pe_tenant 真建（且在补列之后建——老库形态不炸）
        assert got[0]["tenant_id"] == "default"
        assert [e["id"] for e in eng.recent_events(tenant_id="default")] == ["evt_old"]

    def test_manual_run_row_attributed(self, tmp_path):
        eng = _engine(tmp_path)
        _run(eng, tenant="tenant-a")
        conn = sqlite3.connect(tmp_path / "proactive.db")
        rows = conn.execute("SELECT tenant_id FROM proactive_events").fetchall()
        conn.close()
        assert rows == [("tenant-a",)]  # 手动触发行归属=显式派生租户
        got = eng.recent_events(tenant_id="tenant-a")
        assert got[0]["tenant_id"] == "tenant-a"  # 回读键可观测

    def test_heartbeat_run_defaults_engine_tenant(self, tmp_path):
        # 心跳调度路径（run_watcher 不带 tenant_id）=系统行为→引擎租户
        eng = _engine(tmp_path)
        asyncio.run(eng.run_watcher("w"))
        conn = sqlite3.connect(tmp_path / "proactive.db")
        assert conn.execute("SELECT tenant_id FROM proactive_events").fetchall() == [("default",)]
        conn.close()

    def test_blank_tenant_fallback_default(self, tmp_path):
        # 键缺失/空白→'default' 兜底，绝不吃掉落库（③b-2/③c-2① 旁路纪律）
        eng = _engine(tmp_path)
        _run(eng, tenant="   ")
        eng2 = ProactiveEngine(db_path=str(tmp_path / "proactive.db"), tenant_id="")
        eng2.register_watcher("w", lambda: {}, interval_seconds=3600)
        asyncio.run(eng2.run_watcher("w"))
        conn = sqlite3.connect(tmp_path / "proactive.db")
        rows = conn.execute("SELECT DISTINCT tenant_id FROM proactive_events").fetchall()
        conn.close()
        assert rows == [("default",)]

    def test_engine_tenant_used_when_explicit_missing(self, tmp_path):
        eng = ProactiveEngine(db_path=str(tmp_path / "proactive.db"), tenant_id="tenant-e")
        eng.register_watcher("w", lambda: {}, interval_seconds=3600)
        asyncio.run(eng.run_watcher("w"))
        assert eng.recent_events(tenant_id="tenant-e")[0]["tenant_id"] == "tenant-e"


# ── ② 查询过滤 ────────────────────────────────────────────────────────


class TestQueryFilter:
    def test_recent_events_scoped_per_tenant(self, tmp_path):
        eng = _engine(tmp_path)
        _run(eng, tenant="tenant-a")
        _run(eng, tenant="tenant-a")
        _run(eng, tenant="tenant-b")
        la = eng.recent_events(tenant_id="tenant-a")
        lb = eng.recent_events(tenant_id="tenant-b")
        ld = eng.recent_events(tenant_id="default")
        allx = eng.recent_events()  # None=系统视图
        assert len(la) == 2 and {e["tenant_id"] for e in la} == {"tenant-a"}
        assert len(lb) == 1 and lb[0]["tenant_id"] == "tenant-b"
        assert ld == []  # 匿名 default 租户看不到他租户行
        assert len(allx) == 3  # 系统视图全量（心跳/维护用）

    def test_memory_path_scoped_per_tenant(self):
        # 无DB内存留痕（db_path=""）同样恒按租户键
        eng = ProactiveEngine(db_path="")
        eng.register_watcher("w", lambda: {}, interval_seconds=3600)
        asyncio.run(eng.run_watcher("w", tenant_id="tenant-a"))
        asyncio.run(eng.run_watcher("w", tenant_id="tenant-b"))
        assert [e["tenant_id"] for e in eng.recent_events(tenant_id="tenant-a")] == ["tenant-a"]
        assert [e["tenant_id"] for e in eng.recent_events(tenant_id="tenant-b")] == ["tenant-b"]
        assert len(eng.recent_events()) == 2

    def test_degrade_fallback_still_filtered(self, tmp_path, monkeypatch):
        # DB 故障降级内存回读同样恒带租户键（禁降级变跨租户泄露）
        eng = _engine(tmp_path)
        _run(eng, tenant="tenant-a")
        _run(eng, tenant="tenant-b")
        real_connect = sqlite3.connect

        def boom(path, *a, **kw):
            if str(path) == str(tmp_path / "proactive.db"):
                raise RuntimeError("db down")
            return real_connect(path, *a, **kw)

        monkeypatch.setattr("src.will.proactive.sqlite3.connect", boom)
        la = eng.recent_events(tenant_id="tenant-a")
        assert [e["tenant_id"] for e in la] == ["tenant-a"]
        assert all(e["tenant_id"] != "tenant-b" for e in la)
        assert len(eng.recent_events()) == 2  # 系统视图不受影响


# ── ③ IsolationEngine.check_access 真执行 ────────────────────────────


class TestIsolationGate:
    def test_bulk_read_audited(self, tmp_path):
        eng = _engine(tmp_path)
        _run(eng, tenant="tenant-a")
        eng.recent_events(tenant_id="tenant-a")
        log = eng.isolation.get_access_log(tenant_id="ns-tenant-a")
        hit = [
            x
            for x in log
            if x["resource_type"] == "proactive_event"
            and x["action"] == "list"
            and x["resource_id"] == "ns-tenant-a:*"
        ]
        assert hit, f"bulk audit entry missing: {log}"
        assert hit[0]["allowed"] is True
        stats = eng.isolation.stats()
        assert stats["total_access_checks"] >= 1  # 数据面真咨询隔离引擎
        assert "proactive_event" in stats["by_resource_type"]

    def test_bulk_read_audited_per_tenant_namespace(self, tmp_path):
        # 审计按访问者派生租户 namespace 记（A/B 各自留痕，不混）
        eng = _engine(tmp_path)
        _run(eng, tenant="tenant-a")
        _run(eng, tenant="tenant-b")
        eng.recent_events(tenant_id="tenant-a")
        eng.recent_events(tenant_id="tenant-b")
        log = eng.isolation.get_access_log(action="list")
        ns = {x["tenant_id"] for x in log}
        assert ns == {"ns-tenant-a", "ns-tenant-b"}

    def test_system_view_no_gate(self, tmp_path):
        # tenant_id=None=系统视图（心跳/维护/health）：不过滤不闸门零审计噪音
        eng = _engine(tmp_path)
        _run(eng, tenant="tenant-b")
        assert len(eng.recent_events()) == 1
        assert eng.isolation.stats()["total_access_checks"] == 0


# ── ④ API 层：认证派生租户 → 行归属 → 跨租户不可见 全链 ────────────────


class TestAPI:
    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        ent_db = tmp_path / "enterprise.db"
        monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
        monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
        conn = sqlite3.connect(ent_db)
        conn.execute(ENTERPRISE_DDL)
        conn.commit()
        conn.close()
        return ent_db

    @pytest.fixture()
    def api(self, tmp_path, monkeypatch):
        from src.api import proactive as proactive_api

        tmp_engine = ProactiveEngine(db_path=str(tmp_path / "proactive.db"))
        tmp_engine.register_watcher("hello", lambda: {"greet": "hi"}, interval_seconds=3600)
        monkeypatch.setattr(proactive_api.proactive_mod, "proactive_engine", tmp_engine)
        app = FastAPI()
        app.include_router(proactive_api.router, prefix="/api/proactive")
        with TestClient(app) as c:
            yield c

    def test_auth_to_visibility_full_chain(self, api, env, tmp_path):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}

        # A 手动触发：事件行归属从认证身份派生落库（无自报字段）
        resp = api.post("/api/proactive/run/hello", headers=tok_a)
        assert resp.status_code == 200 and resp.json()["ok"] is True
        conn = sqlite3.connect(tmp_path / "proactive.db")
        rows = conn.execute("SELECT tenant_id FROM proactive_events").fetchall()
        conn.close()
        assert rows == [("tenant-a",)]

        # A 可见自己的事件（tenant_id 键可观测）；B/匿名恒不可见
        ev_a = api.get("/api/proactive/events", headers=tok_a).json()["events"]
        assert [e["tenant_id"] for e in ev_a] == ["tenant-a"]
        assert api.get("/api/proactive/events", headers=tok_b).json()["events"] == []
        assert api.get("/api/proactive/events").json()["events"] == []

        # B 的事件 A 也看不到（双向隔离）
        assert api.post("/api/proactive/run/hello", headers=tok_b).status_code == 200
        assert len(api.get("/api/proactive/events", headers=tok_a).json()["events"]) == 1
        assert len(api.get("/api/proactive/events", headers=tok_b).json()["events"]) == 1

        # 引擎审计真留痕（数据面咨询 IsolationEngine）
        conn = sqlite3.connect(str(__import__("os").environ["NEST_DB"]))
        n = conn.execute(
            "SELECT COUNT(*) FROM access_log WHERE resource_type = 'proactive_event'"
        ).fetchone()[0]
        conn.close()
        assert n >= 2

        # /status 引擎全局状态不闸不滤（watcher 调度态是系统视图）
        st = api.get("/api/proactive/status", headers=tok_b)
        assert st.status_code == 200 and len(st.json()["watchers"]) == 1

    def test_anonymous_default_contract_unchanged(self, api, env):
        # 匿名写→'default' 契约不变；RunResponse 形状逐键不变（修前契约）
        resp = api.post("/api/proactive/run/hello")
        assert resp.status_code == 200
        assert resp.json() == {
            "watcher": "hello",
            "ok": True,
            "duration_ms": resp.json()["duration_ms"],
            "result": {"greet": "hi"},
            "error": "",
        }
        ev = api.get("/api/proactive/events").json()["events"]
        assert [e["tenant_id"] for e in ev] == [DEFAULT_TENANT]
        # 未知 watcher 404 契约不变
        assert api.post("/api/proactive/run/nope").status_code == 404

    def test_bad_token_401(self, api, env):
        bad = {"Authorization": "Bearer not-a-token"}
        assert api.get("/api/proactive/events", headers=bad).status_code == 401
        assert api.post("/api/proactive/run/hello", headers=bad).status_code == 401
