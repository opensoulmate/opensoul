"""F3b-③c-2① timeline events 表数据面隔离接线测试。

覆盖五层（全部 tmp 独立库 + NEST_DB/USERS_DB/ENTERPRISE_DB 环境隔离，测试行
绝不写生产 data/timeline.db）：
①events.tenant_id 列迁移（老库补列+存量行回填 'default'）与落行/兜底
②查询过滤（query/clear/stats/organs/event_types 按租户，跨租户行互不可见/不可清）
③IsolationEngine.check_access 真执行（by-ID 闸门：跨租户 TimelineEventAccessDenied
  +引擎审计留痕，total_access_checks>0）
④API 层派生租户全链（auth token → bind_identity → 行归属落库 → 闸门 403，真实
  timeline router）
⑤既有契约回归（缺行 404/匿名 default/系统内部通道不闸）

注：api/timeline.py 模块级 store 单例在 import 时构造 TimelineStore()（既有行为，
src.main 的既有测试同样触发）——本文件 fixture 延迟 import 后立刻把单例换成 tmp
实例，测试行零生产接触；import 时的生产库触点仅幂等迁移 DDL（服务重启本会执行）。
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb
from src.timeline.store import (
    DEFAULT_TENANT,
    TimelineEventAccessDenied,
    TimelineStore,
)

# 生产 timeline.db events 表 F3b-③c-2① 迁移前形状（无 tenant_id → 证明自愈补列）
OLD_EVENTS_DDL = """CREATE TABLE events (
                event_id    TEXT PRIMARY KEY,
                organ       TEXT NOT NULL,
                emoji       TEXT DEFAULT '',
                event_type  TEXT NOT NULL,
                summary     TEXT NOT NULL,
                detail      TEXT DEFAULT '{}',
                timestamp   REAL NOT NULL,
                collected_at REAL NOT NULL
            )"""

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


def _evt(event_id="evt_1", tenant=None, organ="test", ts=1000.0):
    e = {
        "id": event_id,
        "organ": organ,
        "emoji": "🧪",
        "type": "test_event",
        "summary": f"summary-{event_id}",
        "detail": {"k": "v"},
        "timestamp": ts,
        "collected_at": ts,
    }
    if tenant is not None:
        e["tenant_id"] = tenant
    return e


@pytest.fixture()
def store(tmp_path):
    return TimelineStore(str(tmp_path / "timeline.db"))


# ── ① 迁移 + 落行/兜底 ─────────────────────────────────────────────────


class TestMigrationAndRoundtrip:
    def test_tenant_column_added_and_backfilled(self, tmp_path):
        db = tmp_path / "timeline.db"
        conn = sqlite3.connect(db)
        conn.execute(OLD_EVENTS_DDL)
        conn.execute(
            "INSERT INTO events (event_id, organ, event_type, summary, timestamp, collected_at) "
            "VALUES ('evt_old', 'test', 't', 'old row', 1000.0, 1000.0)"
        )
        conn.commit()
        conn.close()

        s = TimelineStore(str(db))

        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
        row = conn.execute("SELECT tenant_id FROM events WHERE event_id = 'evt_old'").fetchone()
        idx = [r[1] for r in conn.execute("PRAGMA index_info(idx_events_tenant)").fetchall()]
        conn.close()
        assert "tenant_id" in cols
        assert row[0] == "default"  # 存量行回填 default（与生产数据分布一致）
        assert idx  # idx_events_tenant 真建
        got = s.get_event("evt_old", tenant_id="default")
        assert got is not None and got.tenant_id == "default"

    def test_record_roundtrip_tenant(self, store, tmp_path):
        assert store.record(_evt(tenant="tenant-a")) is True
        conn = sqlite3.connect(tmp_path / "timeline.db")
        row = conn.execute("SELECT tenant_id FROM events WHERE event_id = 'evt_1'").fetchone()
        conn.close()
        assert row[0] == "tenant-a"  # 写点真落行归属
        got = store.get_event("evt_1", tenant_id="tenant-a")
        assert got is not None and got.tenant_id == "tenant-a"
        assert got.to_dict()["tenant_id"] == "tenant-a"

    def test_record_fallback_default(self, store, tmp_path):
        # 键缺失/空白→'default' 兜底，绝不吃掉落库（③b-2 旁路纪律）
        assert store.record(_evt("evt_nokey")) is True
        assert store.record(_evt("evt_blank", tenant="   ")) is True
        conn = sqlite3.connect(tmp_path / "timeline.db")
        rows = conn.execute("SELECT event_id, tenant_id FROM events ORDER BY event_id").fetchall()
        conn.close()
        assert rows == [("evt_blank", "default"), ("evt_nokey", "default")]

    def test_dedup_does_not_launder_tenant(self, store):
        # INSERT OR IGNORE 去重契约：同 id 二写不换租户归属（禁洗键）
        store.record(_evt("evt_d", tenant="tenant-a"))
        store.record(_evt("evt_d", tenant="tenant-b"))
        got = store.get_event("evt_d", tenant_id="tenant-a")
        assert got is not None and got.tenant_id == "tenant-a"


# ── ② 查询过滤 ────────────────────────────────────────────────────────


class TestQueryFilter:
    def test_query_scoped_per_tenant(self, store):
        store.record(_evt("evt_a1", tenant="tenant-a"))
        store.record(_evt("evt_a2", tenant="tenant-a"))
        store.record(_evt("evt_b1", tenant="tenant-b"))
        la = {e.event_id for e in store.query(tenant_id="tenant-a")}
        lb = {e.event_id for e in store.query(tenant_id="tenant-b")}
        ld = {e.event_id for e in store.query(tenant_id="default")}
        allx = {e.event_id for e in store.query()}  # None=系统扫描
        assert la == {"evt_a1", "evt_a2"}
        assert lb == {"evt_b1"}
        assert ld == set()  # 匿名 default 租户看不到他租户行
        assert allx == {"evt_a1", "evt_a2", "evt_b1"}

    def test_clear_scoped_per_tenant(self, store):
        store.record(_evt("evt_a1", tenant="tenant-a"))
        store.record(_evt("evt_a2", tenant="tenant-a"))
        store.record(_evt("evt_b1", tenant="tenant-b"))
        # 全清恒按行租户键：A 清 2，B 行一律不动（防"只给租户"变相清全史）
        assert store.clear(tenant_id="tenant-a") == 2
        remaining = {e.event_id for e in store.query()}
        assert remaining == {"evt_b1"}
        # older_than_days 分支同样恒带租户键
        store.record(_evt("evt_a3", tenant="tenant-a", ts=1.0))
        assert store.clear(older_than_days=1, tenant_id="tenant-a") == 1
        assert {e.event_id for e in store.query()} == {"evt_b1"}

    def test_stats_organs_types_scoped(self, store):
        store.record(_evt("evt_a1", tenant="tenant-a", organ="liver"))
        store.record(_evt("evt_b1", tenant="tenant-b", organ="heart", ts=2000.0))
        sa = store.stats(tenant_id="tenant-a")
        assert sa["total_events"] == 1
        assert sa["by_organ"] == {"liver": 1}  # 他租户行不进计数
        assert sa["time_range"]["earliest"] == 1000.0
        sb = store.stats(tenant_id="tenant-b")
        assert sb["total_events"] == 1 and sb["by_organ"] == {"heart": 1}
        assert store.stats()["total_events"] == 2  # None=系统全局（health 用）
        organs_a = {x["organ"] for x in store.organs(tenant_id="tenant-a")}
        assert organs_a == {"liver"}
        types_a = {x["event_type"]: x["count"] for x in store.event_types(tenant_id="tenant-a")}
        assert types_a == {"test_event": 1}
        assert {x["organ"] for x in store.organs()} == {"liver", "heart"}


# ── ③ IsolationEngine.check_access 真执行 ────────────────────────────


class TestIsolationGate:
    def test_cross_tenant_get_denied_with_audit(self, store):
        store.record(_evt("evt_x", tenant="tenant-b"))
        with pytest.raises(TimelineEventAccessDenied) as e:
            store.get_event("evt_x", tenant_id="tenant-a")
        assert "namespace" in e.value.reason

        stats = store.isolation.stats()
        assert stats["total_access_checks"] >= 1
        assert stats["blocked_attempts"] >= 1
        blocked = store.isolation.get_access_log(allowed=False)
        hit = [
            x for x in blocked if x["resource_id"] == "ns-tenant-b:evt_x" and x["action"] == "read"
        ]
        assert hit, f"blocked audit entry missing: {blocked}"
        assert "namespace mismatch" in hit[0]["reason"]
        assert hit[0]["resource_type"] == "timeline_event"

    def test_same_tenant_get_allowed_and_logged(self, store):
        store.record(_evt("evt_x", tenant="tenant-a"))
        assert store.get_event("evt_x", tenant_id="tenant-a") is not None
        allowed = store.isolation.get_access_log(allowed=True)
        assert any(
            x["resource_id"] == "ns-tenant-a:evt_x" and x["action"] == "read" for x in allowed
        )

    def test_cross_tenant_delete_denied_row_survives(self, store):
        store.record(_evt("evt_x", tenant="tenant-a"))
        with pytest.raises(TimelineEventAccessDenied):
            store.delete_event("evt_x", tenant_id="tenant-b")
        assert store.get_event("evt_x", tenant_id="tenant-a") is not None  # 行存活
        assert store.delete_event("evt_x", tenant_id="tenant-a") is True  # 本租户照常删
        assert store.get_event("evt_x") is None

    def test_delete_missing_row_contract_unchanged(self, store):
        assert store.delete_event("evt_missing", tenant_id="tenant-a") is False  # 缺行=404 契约
        assert store.get_event("evt_missing", tenant_id="tenant-a") is None

    def test_bulk_calls_audited(self, store):
        store.record(_evt("evt_x", tenant="tenant-a"))
        store.query(tenant_id="tenant-a")
        store.clear(tenant_id="tenant-a")
        store.stats(tenant_id="tenant-a")
        store.organs(tenant_id="tenant-a")
        store.event_types(tenant_id="tenant-a")
        log = store.isolation.get_access_log(tenant_id="ns-tenant-a")
        actions = {x["action"] for x in log}
        assert {"list", "delete", "stats"} <= actions
        assert store.isolation.stats()["total_access_checks"] >= 5

    def test_system_paths_no_gate(self, store):
        # None=系统内部读/清（sync/health/维护）：不过滤不闸门，且不产生审计噪音
        store.record(_evt("evt_x", tenant="tenant-b"))
        assert store.get_event("evt_x") is not None
        assert store.clear() == 1
        assert store.isolation.stats()["total_access_checks"] == 0


# ── ④ API 层：认证派生租户 → 行归属 → 闸门 403 全链 ───────────────────


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
        # 延迟 import（单例构造副作用）后立刻换 tmp 数据面——测试行零生产接触
        import src.api.timeline as timeline_api

        tmp_store = TimelineStore(str(tmp_path / "timeline.db"))
        monkeypatch.setattr(timeline_api, "store", tmp_store)
        app = FastAPI()
        app.include_router(timeline_api.router, prefix="/api/timeline")
        with TestClient(app) as c:
            yield c

    def test_auth_to_gate_full_chain(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}

        # A 写：行归属从认证身份派生落库（RecordEventRequest 无自报字段）
        resp = api.post(
            "/api/timeline/record",
            json={"organ": "test", "event_type": "e", "summary": "a-event"},
            headers=tok_a,
        )
        assert resp.status_code == 200
        evt_a = resp.json()["event_id"]

        # A 读自己的：200 且归属可见
        got = api.get(f"/api/timeline/events/{evt_a}", headers=tok_a)
        assert got.status_code == 200 and got.json()["tenant_id"] == "tenant-a"

        # B 冒读 A 的事件：403（闸门真拒绝带真因，不是 404 假 not_found）
        denied = api.get(f"/api/timeline/events/{evt_a}", headers=tok_b)
        assert denied.status_code == 403
        assert "access denied" in denied.json()["detail"]
        assert "different tenant" in denied.json()["detail"]  # verdict 真因如实报

        # B 冒删 A 的事件：403 且行存活（禁跨租户破坏）
        assert api.delete(f"/api/timeline/events/{evt_a}", headers=tok_b).status_code == 403
        assert api.get(f"/api/timeline/events/{evt_a}", headers=tok_a).status_code == 200

        # 列表/stats 三面隔离：B/匿名看不到 A 的事件、计数不混
        assert api.get("/api/timeline/events", headers=tok_b).json()["events"] == []
        assert api.get("/api/timeline/events").json()["events"] == []
        assert api.get("/api/timeline/stats", headers=tok_b).json()["total_events"] == 0
        assert api.get("/api/timeline/stats").json()["total_events"] == 0
        assert api.get("/api/timeline/stats", headers=tok_a).json()["total_events"] == 1

    def test_anonymous_default_contract_unchanged(self, api, env):
        # 匿名写→'default' 契约不变（修前语义：无身份调用照常可用）
        resp = api.post(
            "/api/timeline/record",
            json={"organ": "test", "event_type": "e", "summary": "anon-event"},
        )
        assert resp.status_code == 200
        evt = resp.json()["event_id"]
        got = api.get(f"/api/timeline/events/{evt}")
        assert got.status_code == 200 and got.json()["tenant_id"] == "default"
        listed = api.get("/api/timeline/events").json()["events"]
        assert [x["event_id"] for x in listed] == [evt]
        # 匿名清理通道照常 + 缺行 404 契约不变
        assert api.get("/api/timeline/events/nope").status_code == 404
        assert api.delete("/api/timeline/events/nope").status_code == 404
        assert api.delete(f"/api/timeline/events/{evt}").status_code == 200

    def test_clear_scoped_at_api(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        for tok in (tok_a, tok_b):
            assert (
                api.post(
                    "/api/timeline/record",
                    json={"organ": "test", "event_type": "e", "summary": "x"},
                    headers=tok,
                ).status_code
                == 200
            )

        # A 清理：只清 A 的行，B 的行存活
        resp = api.post("/api/timeline/clear", json={}, headers=tok_a)
        assert resp.status_code == 200 and resp.json()["cleared"] == 1
        assert api.get("/api/timeline/events", headers=tok_b).json()["count"] == 1
        assert api.get("/api/timeline/events", headers=tok_a).json()["events"] == []
