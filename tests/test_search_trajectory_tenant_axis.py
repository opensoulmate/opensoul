"""unified trajectory 子搜索存储层加轴测试（DEC-⑦-⑥：trajectory 面最小件）。

修前（HEAD 源码实证）两断点：①_search_trajectory 读 trajectory_sessions/
trajectory_events 零租户过滤（list_sessions(limit=50)/search_events(keyword=)）
=任意租户可见全部会话与事件（与 timeline ③c-2① / events DEC-⑦-① / echo ⑦-②
/ gene ⑦-③ / files ⑦-④ / agents ⑦-⑤ 已收的查询过滤不对称=跨租户泄露）
②session/事件行无租户键、写点（create_session/add_event）零身份轴=行归属无从
谈起，create/end/delete 按 session_id 跨租户改删零闸。收口=session_row_tenant /
event_row_tenant 单一判法（自带键缺失/空白→'default' 兜底，⑦-① _event_row_tenant
/ ② message_row_tenant / ③ template_row_tenant / ④ file_row_tenant / ⑤
agent_row_tenant 同款；写读同一判法）+trajectory_sessions/trajectory_events
tenant_id 行键（DDL+ALTER 双轨幂等迁移回填 'default'，③c-2① 索引补列之后建
坑延续）+写点行归属（create_session(tenant_id=) =bind_identity 派生租户、
缺省→'default' 系统写方；事件行=父 session 行继承，③b-2 写点=父行继承同款）
+劫持闸（跨租户同 session_id ensure/end=SessionTenantDenied→403 行零改写，
⑦-③④⑤ 同款）+delete 归属闸（跨租户=零副作用与 not-found 同契约 404）
+list/count/search 恒按行租户键过滤（None=系统视图字节不变；过滤先于截断）
+_search_trajectory 必选 tenant_id 恒按派生租户过滤+读真源=src.trajectory.store
模块单例（修前函数内 new TrajectoryStore() 每调用新实例，⑦-③④ 收口同款）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB 归 tmp + store.db_pool 换
EphemeralSQLitePool(tmp) + settings.database_url 同 tmp——绝不碰生产
data/opensoul.db 的 trajectory_* 表）。
"""

import asyncio
import sqlite3
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)

# 生产 trajectory_* 表 DEC-⑦-⑥ 迁移前形状（无 tenant_id → 证明自愈补列）
OLD_SESSIONS_DDL = """CREATE TABLE trajectory_sessions (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL DEFAULT '',
                task_description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                forked_from TEXT,
                fork_point_event_id TEXT,
                total_events INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                total_duration_ms REAL NOT NULL DEFAULT 0,
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                ended_at TEXT
            )"""

OLD_EVENTS_DDL = """CREATE TABLE trajectory_events (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_event_id TEXT,
                event_type TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                token_usage INTEGER NOT NULL DEFAULT 0,
                duration_ms REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok',
                created_at TEXT NOT NULL,
                end_at TEXT
            )"""


def _seed_user(db_path, uid, username, tenant="default"):
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
        )
        c.commit()
    return uid


def _token(uid):
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": "Bearer " + tok}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + tmp trajectory 库（EphemeralSQLitePool 随查询开合）+ 真
    search/trajectory router 真 bind_identity。搜索与管理面写口读同一真源实例
    （src.trajectory.store 模块单例 trajectory_store）。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    traj_db = str(tmp_path / "opensoul.db")
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    from src.config import settings
    from src.database.postgres import EphemeralSQLitePool

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{traj_db}")
    import src.trajectory.store as store_mod

    monkeypatch.setattr(store_mod, "db_pool", EphemeralSQLitePool(db_path=traj_db))

    import src.api.search as search_api
    import src.api.trajectory as trajectory_api

    app = FastAPI()
    app.include_router(search_api.router, prefix="/api/search")
    app.include_router(trajectory_api.router, prefix="/api/trajectory")
    client = TestClient(app)

    user_tenant_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_tenant_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")

    yield {
        "client": client,
        "traj_db": traj_db,
        "user_tenant_a": user_tenant_a,
        "user_tenant_b": user_tenant_b,
        "user_default": user_default,
    }


def _create_session(client, headers, **kw):
    body = {"agent_id": "agent-x", "task_description": kw.pop("task_description", "alpha task")}
    body.update(kw)
    r = client.post("/api/trajectory/sessions", json=body, headers=headers or {})
    assert r.status_code == 200
    return r.json()


def _record_event(client, headers, session_id, content="probe hit"):
    r = client.post(
        f"/api/trajectory/sessions/{session_id}/events",
        json={"event_type": "tool_call", "agent_id": "agent-x", "content": content},
        headers=headers or {},
    )
    assert r.status_code == 200
    return r.json()


def _set_row_tenant(traj_db, table, key_col, key, tenant):
    """直写行键（写点归属另有专测；⑦-③ setattr 直写同款控制组纪律）。

    旧代码无租户列→OperationalError：行即「无键」=修前真态，让泄露暴露在
    行为断言而非 fixture TypeError。
    """
    try:
        with sqlite3.connect(traj_db) as c:
            c.execute(f"UPDATE {table} SET tenant_id=? WHERE {key_col}=?", (tenant, key))
            c.commit()
    except sqlite3.OperationalError:
        pass


def _row_tenant(traj_db, table, key_col, key):
    with sqlite3.connect(traj_db) as c:
        row = c.execute(f"SELECT tenant_id FROM {table} WHERE {key_col}=?", (key,)).fetchone()
    return row[0] if row else None


def _row_alive(traj_db, table, key_col, key):
    with sqlite3.connect(traj_db) as c:
        row = c.execute(f"SELECT COUNT(*) FROM {table} WHERE {key_col}=?", (key,)).fetchone()
    return row[0] == 1


def _search(client, headers, params=None):
    p = {"q": "probe", "sources": "trajectory", "limit": 10}
    p.update(params or {})
    r = client.get("/api/search/unified", params=p, headers=headers or {})
    assert r.status_code == 200
    return r.json()["by_source"]["trajectory"]


# ── 迁移与单一判法 ─────────────────────────────────────────


class TestMigrationAndRowTenant:
    def test_old_schema_backfill_and_index(self, tmp_path, monkeypatch):
        from src.database.postgres import EphemeralSQLitePool
        from src.trajectory.store import TrajectoryStore

        db = tmp_path / "old" / "opensoul.db"
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as c:
            c.execute(OLD_SESSIONS_DDL)
            c.execute(OLD_EVENTS_DDL)
            c.execute(
                "INSERT INTO trajectory_sessions (id, agent_id, task_description, created_at) "
                "VALUES ('s1', 'a', 'legacy', '2026-01-01T00:00:00+00:00')"
            )
            c.execute(
                "INSERT INTO trajectory_events (id, session_id, event_type, content, created_at) "
                "VALUES ('e1', 's1', 'tool_call', 'legacy', '2026-01-01T00:00:00+00:00')"
            )
            c.commit()
        import src.trajectory.store as store_mod

        pool = EphemeralSQLitePool(db_path=str(db))
        monkeypatch.setattr(store_mod, "db_pool", pool)

        async def run():
            store = TrajectoryStore()
            assert await store.count_sessions() == 1  # ensure_tables 真跑
            s = await store.get_session("s1")
            assert s is not None and s.tenant_id == "default"  # 存量行回填 'default'
            evs = await store.search_events(limit=10)
            assert len(evs) == 1 and evs[0].tenant_id == "default"
            # 幂等：二次 ensure 零报错（count_sessions 内部再跑 ensure_tables）
            assert await store.count_sessions(tenant_id="default") == 1

        asyncio.run(run())
        with sqlite3.connect(db) as c:
            sess_cols = {r[1] for r in c.execute("PRAGMA table_info(trajectory_sessions)")}
            ev_cols = {r[1] for r in c.execute("PRAGMA table_info(trajectory_events)")}
            idx = {r[1] for r in c.execute("PRAGMA index_list(trajectory_sessions)")}
            idx_ev = {r[1] for r in c.execute("PRAGMA index_list(trajectory_events)")}
        assert "tenant_id" in sess_cols and "tenant_id" in ev_cols  # ALTER 自愈补列
        assert "idx_traj_sessions_tenant" in idx  # 索引补列之后建（③c-2① 坑延续）
        assert "idx_traj_events_tenant" in idx_ev

    def test_session_row_tenant_fallback(self):
        from src.trajectory.store import TrajectorySession, session_row_tenant

        assert session_row_tenant(TrajectorySession()) == "default"
        assert session_row_tenant(TrajectorySession(tenant_id="   ")) == "default"
        assert session_row_tenant(TrajectorySession(tenant_id="tenant-a")) == "tenant-a"

    def test_event_row_tenant_fallback(self):
        from src.trajectory.store import TrajectoryEvent, event_row_tenant

        assert event_row_tenant(TrajectoryEvent()) == "default"
        assert event_row_tenant(TrajectoryEvent(tenant_id="   ")) == "default"
        assert event_row_tenant(TrajectoryEvent(tenant_id="tenant-a")) == "tenant-a"


# ── 存储层：写点归属/劫持闸/归属闸/过滤 ─────────────────────


class TestStoreAxis:
    def test_event_inherits_parent_and_missing_fallback(self, env):
        from src.database.postgres import EphemeralSQLitePool
        from src.trajectory.store import TrajectoryEvent, TrajectoryStore

        pool = EphemeralSQLitePool(db_path=env["traj_db"])
        import src.trajectory.store as store_mod

        orig = store_mod.db_pool
        store_mod.db_pool = pool
        try:

            async def run():
                store = TrajectoryStore()
                s = await store.create_session(
                    id="s-inherit", agent_id="a", task_description="t", tenant_id="tenant-a"
                )
                ev = TrajectoryEvent(session_id=s.id, event_type="tool_call", content="c")
                await store.add_event(ev)
                assert ev.tenant_id == "tenant-a"  # 事件行=父 session 行继承
                # 父行缺失→'default' 兜底不掉落库（键派生故障绝不吃掉落库）
                ghost = TrajectoryEvent(session_id="ghost-session", event_type="tool_call")
                await store.add_event(ghost)
                assert ghost.tenant_id == "default"

            asyncio.run(run())
        finally:
            store_mod.db_pool = orig
        ev_id = _first_event(env)
        assert ev_id != "missing"
        assert _row_tenant(env["traj_db"], "trajectory_events", "id", ev_id) == "tenant-a"

    def test_create_hijack_denied_row_untouched(self, env):
        import src.trajectory.store as store_mod
        from src.database.postgres import EphemeralSQLitePool
        from src.trajectory.store import SessionTenantDenied, TrajectoryStore

        orig = store_mod.db_pool
        store_mod.db_pool = EphemeralSQLitePool(db_path=env["traj_db"])
        try:

            async def run():
                store = TrajectoryStore()
                await store.create_session(
                    id="s-hijack", agent_id="a", task_description="original", tenant_id="tenant-a"
                )
                with pytest.raises(SessionTenantDenied):
                    await store.create_session(
                        id="s-hijack", agent_id="b", task_description="stolen", tenant_id="tenant-b"
                    )

            asyncio.run(run())
        finally:
            store_mod.db_pool = orig
        assert _row_tenant(env["traj_db"], "trajectory_sessions", "id", "s-hijack") == "tenant-a"
        with sqlite3.connect(env["traj_db"]) as c:
            row = c.execute(
                "SELECT task_description, agent_id FROM trajectory_sessions WHERE id='s-hijack'"
            ).fetchone()
        assert row == ("original", "a")  # 行零改写

    def test_end_gate_and_delete_gate(self, env):
        import src.trajectory.store as store_mod
        from src.database.postgres import EphemeralSQLitePool
        from src.trajectory.store import SessionTenantDenied, TrajectoryStore

        orig = store_mod.db_pool
        store_mod.db_pool = EphemeralSQLitePool(db_path=env["traj_db"])
        try:

            async def run():
                store = TrajectoryStore()
                await store.create_session(
                    id="s-gate", agent_id="a", task_description="t", tenant_id="tenant-a"
                )
                with pytest.raises(SessionTenantDenied):
                    await store.end_session("s-gate", tenant_id="tenant-b")
                assert await store.delete_session("s-gate", tenant_id="tenant-b") is False

            asyncio.run(run())
        finally:
            store_mod.db_pool = orig
        assert _row_alive(env["traj_db"], "trajectory_sessions", "id", "s-gate")
        assert _row_tenant(env["traj_db"], "trajectory_sessions", "id", "s-gate") == "tenant-a"

    def test_list_count_filter_system_view(self, env):
        import src.trajectory.store as store_mod
        from src.database.postgres import EphemeralSQLitePool
        from src.trajectory.store import TrajectoryStore

        orig = store_mod.db_pool
        store_mod.db_pool = EphemeralSQLitePool(db_path=env["traj_db"])
        try:

            async def run():
                store = TrajectoryStore()
                for i, t in enumerate(["tenant-a", "tenant-b", "default"]):
                    await store.create_session(
                        id=f"s-l{i}", agent_id="a", task_description=f"t{i}", tenant_id=t
                    )
                assert len(await store.list_sessions(tenant_id="tenant-a")) == 1
                assert await store.count_sessions(tenant_id="tenant-a") == 1
                assert len(await store.list_sessions()) == 3  # None=系统视图
                assert await store.count_sessions() == 3

            asyncio.run(run())
        finally:
            store_mod.db_pool = orig


def _first_event(env):
    with sqlite3.connect(env["traj_db"]) as c:
        row = c.execute("SELECT id FROM trajectory_events WHERE session_id='s-inherit'").fetchone()
    return row[0] if row else "missing"


# ── API 层：派生租户全链/闸门/过滤 ──────────────────────────


class TestApiAxis:
    def test_create_row_attribution_and_additive_keys(self, env):
        client = env["client"]
        h = _token(env["user_tenant_a"])
        s = _create_session(client, h, session_id="s-api-a", task_description="alpha probe")
        assert s["tenant_id"] == "tenant-a"  # 加性 tenant_id 键=写点派生
        assert _row_tenant(env["traj_db"], "trajectory_sessions", "id", "s-api-a") == "tenant-a"
        # 匿名='default'（契约不变）
        s2 = _create_session(client, None, session_id="s-api-anon", task_description="alpha probe")
        assert s2["tenant_id"] == "default"
        assert _row_tenant(env["traj_db"], "trajectory_sessions", "id", "s-api-anon") == "default"

    def test_ensure_cross_tenant_403_row_untouched(self, env):
        client = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        _create_session(client, ha, session_id="s-ensure", task_description="owner desc")
        r = client.post(
            "/api/trajectory/sessions",
            json={"session_id": "s-ensure", "agent_id": "b", "task_description": "stolen"},
            headers=hb,
        )
        assert r.status_code == 403  # 跨租户 ensure=劫持
        assert _row_tenant(env["traj_db"], "trajectory_sessions", "id", "s-ensure") == "tenant-a"
        with sqlite3.connect(env["traj_db"]) as c:
            row = c.execute(
                "SELECT task_description FROM trajectory_sessions WHERE id='s-ensure'"
            ).fetchone()
        assert row[0] == "owner desc"  # 行零改写
        # 同租户 ensure 契约不变（幂等返回既有行）
        r2 = client.post(
            "/api/trajectory/sessions",
            json={"session_id": "s-ensure", "agent_id": "a", "task_description": "owner desc"},
            headers=ha,
        )
        assert r2.status_code == 200 and r2.json()["id"] == "s-ensure"

    def test_event_inherits_parent_via_api(self, env):
        client = env["client"]
        h = _token(env["user_tenant_a"])
        s = _create_session(client, h, session_id="s-ev", task_description="alpha probe")
        # 外部/匿名写方落事件→随父行归属（③b-2 外部写方回帖语义）
        ev = _record_event(client, None, s["id"])
        assert ev["tenant_id"] == "tenant-a"
        assert _row_tenant(env["traj_db"], "trajectory_events", "id", ev["id"]) == "tenant-a"

    def test_list_and_total_three_way(self, env):
        client = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        _create_session(client, ha, session_id="s-ls-a", task_description="alpha probe")
        _create_session(client, hb, session_id="s-ls-b", task_description="alpha probe")
        _create_session(client, None, session_id="s-ls-d", task_description="alpha probe")
        ra = client.get("/api/trajectory/sessions", headers=ha)
        assert ra.status_code == 200
        assert [x["id"] for x in ra.json()["sessions"]] == ["s-ls-a"]
        assert ra.json()["total"] == 1  # 总数不泄露全局分布
        rb = client.get("/api/trajectory/sessions", headers=hb)
        assert [x["id"] for x in rb.json()["sessions"]] == ["s-ls-b"]
        rd = client.get("/api/trajectory/sessions")
        assert [x["id"] for x in rd.json()["sessions"]] == ["s-ls-d"]

    def test_delete_cross_tenant_404_row_alive_owner_ok(self, env):
        client = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        s = _create_session(client, ha, session_id="s-del", task_description="alpha probe")
        _record_event(client, ha, s["id"])
        r = client.delete(f"/api/trajectory/sessions/{s['id']}", headers=hb)
        assert r.status_code == 404  # 跨租户=与 not-found 同契约
        assert _row_alive(env["traj_db"], "trajectory_sessions", "id", "s-del")
        assert _row_alive(env["traj_db"], "trajectory_events", "session_id", s["id"])
        r2 = client.delete(f"/api/trajectory/sessions/{s['id']}", headers=ha)
        assert r2.status_code == 200
        assert not _row_alive(env["traj_db"], "trajectory_sessions", "id", "s-del")
        assert not _row_alive(env["traj_db"], "trajectory_events", "session_id", s["id"])

    def test_end_cross_tenant_403_owner_ok(self, env):
        client = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        _create_session(client, ha, session_id="s-end", task_description="alpha probe")
        r = client.post("/api/trajectory/sessions/s-end/end", headers=hb)
        assert r.status_code == 403  # 劫持闸
        with sqlite3.connect(env["traj_db"]) as c:
            row = c.execute(
                "SELECT status, ended_at FROM trajectory_sessions WHERE id='s-end'"
            ).fetchone()
        assert row[0] == "running" and row[1] is None  # 行零改写
        r2 = client.post("/api/trajectory/sessions/s-end/end", headers=ha)
        assert r2.status_code == 200
        with sqlite3.connect(env["traj_db"]) as c:
            row2 = c.execute("SELECT status FROM trajectory_sessions WHERE id='s-end'").fetchone()
        assert row2[0] == "completed"

    def test_fork_attribution_to_cloner(self, env):
        client = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        s = _create_session(client, ha, session_id="s-fork-src", task_description="alpha probe")
        ev = _record_event(client, ha, s["id"])
        r = client.post(
            f"/api/trajectory/sessions/{s['id']}/fork",
            json={"fork_point_event_id": ev["id"], "new_agent_id": "cloner"},
            headers=hb,  # 克隆者=B → 新行归克隆者（⑦-③ clone 同款）
        )
        assert r.status_code == 200
        new_id = r.json()["id"]
        assert r.json()["tenant_id"] == "tenant-b"
        # 复制事件随新 session 行继承落键
        with sqlite3.connect(env["traj_db"]) as c:
            rows = c.execute(
                "SELECT DISTINCT tenant_id FROM trajectory_events WHERE session_id=?", (new_id,)
            ).fetchall()
        assert [x[0] for x in rows] == ["tenant-b"]

    def test_bad_token_401(self, env):
        client = env["client"]
        r = client.get("/api/trajectory/sessions", headers={"Authorization": "Bearer not-a-token"})
        assert r.status_code == 401


# ── unified trajectory 子搜索 + 轨迹搜索端点 ────────────────


class TestUnifiedTrajectorySearch:
    def test_cross_tenant_hidden(self, env):
        client = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        sa = _create_session(client, ha, session_id="s-us-a", task_description="alpha probe")
        _record_event(client, ha, sa["id"], content="probe in tenant-a event")
        sb = _create_session(client, hb, session_id="s-us-b", task_description="alpha probe")
        _record_event(client, hb, sb["id"], content="probe in tenant-b event")
        ids_a = {m.get("session_id") for m in _search(client, ha)}
        ids_b = {m.get("session_id") for m in _search(client, hb)}
        assert ids_a and "s-us-a" in ids_a and "s-us-b" not in ids_a
        assert ids_b and "s-us-b" in ids_b and "s-us-a" not in ids_b

    def test_anon_sees_default_rows_only(self, env):
        client = env["client"]
        ha = _token(env["user_tenant_a"])
        sa = _create_session(client, ha, session_id="s-us-a2", task_description="alpha probe")
        _record_event(client, ha, sa["id"], content="probe in tenant-a event")
        sd = _create_session(client, None, session_id="s-us-d2", task_description="alpha probe")
        _record_event(client, None, sd["id"], content="probe in default event")
        ids_d = {m.get("session_id") for m in _search(client, None)}
        assert ids_d and "s-us-d2" in ids_d and "s-us-a2" not in ids_d

    def test_filter_before_limit(self, env):
        client = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        # B 造 5 组命中占名额，A 造 1 组——修前 A 连一条都拿不到（limit 被 B 占光）
        for i in range(5):
            s = _create_session(client, hb, task_description=f"alpha probe b{i}")
            _record_event(client, hb, s["id"], content=f"probe b{i}")
        sa = _create_session(client, ha, task_description="alpha probe only-a")
        _record_event(client, ha, sa["id"], content="probe only-a")
        ma = _search(client, ha, {"limit": 3})
        assert ma and all("only-a" in m["title"] for m in ma)

    def test_result_shape_unchanged(self, env):
        client = env["client"]
        ha = _token(env["user_tenant_a"])
        s = _create_session(client, ha, session_id="s-shape", task_description="alpha probe")
        _record_event(client, ha, s["id"])
        r = client.get(
            "/api/search/unified",
            params={"q": "alpha", "sources": "trajectory", "limit": 10},
            headers=ha,
        )
        assert r.status_code == 200
        hits = r.json()["by_source"]["trajectory"]
        assert hits
        for m in hits:
            assert set(m) >= {"source", "icon", "title", "snippet"}
            assert m["source"] == "trajectory"

    def test_trajectory_search_endpoint_filtered(self, env):
        client = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        sa = _create_session(client, ha, session_id="s-se-a", task_description="alpha probe")
        _record_event(client, ha, sa["id"], content="probe unique-a")
        sb = _create_session(client, hb, session_id="s-se-b", task_description="alpha probe")
        _record_event(client, hb, sb["id"], content="probe unique-b")
        ra = client.get("/api/trajectory/search", params={"keyword": "probe"}, headers=ha)
        assert ra.status_code == 200
        contents = [e["content"] for e in ra.json()["events"]]
        assert contents and all("unique-b" not in c for c in contents)
        rg = client.get("/api/trajectory/search/sessions", params={"keyword": "probe"}, headers=ha)
        assert rg.status_code == 200
        sids = [g["session_id"] for g in rg.json()["sessions"]]
        assert sids and "s-se-b" not in sids

    def test_search_reads_module_singleton(self, env):
        """接线钉：_search_trajectory 消费 src.trajectory.store 模块单例（读真源收口，
        防脱线自比/双源漂移）。"""
        import inspect

        import src.api.search as search_api

        src_text = inspect.getsource(search_api._search_trajectory)
        assert "from src.trajectory.store import trajectory_store" in src_text
        # 只查代码体（docstring 含「修前 new TrajectoryStore()」勘误文字不算）
        code_body = src_text.split('"""', 2)[2]
        assert "TrajectoryStore()" not in code_body  # 修前函数内 new 每调用新实例
        assert "trajectory_store." in code_body
