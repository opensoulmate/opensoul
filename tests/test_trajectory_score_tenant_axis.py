"""trajectory_scores 行租户键加轴测试（DEC-⑦-⑨：⑦-⑥ 留档「scores 面未加轴」）。

修前（HEAD 源码实证）：trajectory_scores 表零租户列、add_score 写点零身份轴
（行归属无从谈起）、get_scores/get_score_summary 读面零过滤=任意租户可见全部
评分（与 sessions/events DEC-⑦-⑥ 已收查询过滤不对称=跨租户泄露）。收口=
score_row_tenant 单一判法（自带键缺失/空白→'default' 兜底，⑦-⑥
session_row_tenant / event_row_tenant 同款；写读同一判法）+
trajectory_scores.tenant_id 行键（DDL+ALTER 双轨幂等迁移回填 'default'，
③c-2① 索引补列之后建坑延续）+写点行归属=父 session 行继承（add_score 单写
点，③b-2/add_event 写点=父行继承同款——eval_loop/llm_judge/human 等外部评分
方随父行归属，会话拥有者可见自己会话的全部评分；父行缺失→'default' 兜底不
掉落库）+get_scores/get_score_summary 恒按行租户键过滤（None=系统视图字节
不变；过滤先于截断；聚合不泄露全局分布）+API list_scores/score_summary
bind_identity 派生租户流入（匿名 'default' 契约不变，禁自报）+加性 tenant_id
键（to_dict 归一，score_row_tenant 写读同源）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB 归 tmp + store.db_pool 换
EphemeralSQLitePool(tmp) + settings.database_url 同 tmp——绝不碰生产
data/opensoul.db 的 trajectory_scores 表）。
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

# 生产 trajectory_scores 表 DEC-⑦-⑨ 迁移前形状（无 tenant_id → 证明自愈补列）
OLD_SCORES_DDL = """CREATE TABLE trajectory_scores (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                trace_id TEXT,
                name TEXT NOT NULL,
                value REAL NOT NULL DEFAULT 0,
                string_value TEXT,
                data_type TEXT NOT NULL DEFAULT 'numeric',
                comment TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'api',
                created_at TEXT NOT NULL
            )"""

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
                ended_at TEXT,
                tenant_id TEXT NOT NULL DEFAULT 'default'
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
    trajectory router 真 bind_identity（store 模块单例同一真源）。"""
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

    import src.api.trajectory as trajectory_api

    app = FastAPI()
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


def _create_session(client, headers, session_id, task_description="alpha probe"):
    r = client.post(
        "/api/trajectory/sessions",
        json={
            "agent_id": "agent-x",
            "task_description": task_description,
            "session_id": session_id,
        },
        headers=headers or {},
    )
    assert r.status_code == 200
    return r.json()


def _add_score(client, headers, session_id, name="quality", value=0.5, source="api"):
    r = client.post(
        f"/api/trajectory/sessions/{session_id}/scores",
        json={"name": name, "value": value, "source": source},
        headers=headers or {},
    )
    return r


def _row_tenant(traj_db, key):
    with sqlite3.connect(traj_db) as c:
        row = c.execute("SELECT tenant_id FROM trajectory_scores WHERE id=?", (key,)).fetchone()
    return row[0] if row else None


def _swap_pool(env):
    import src.trajectory.store as store_mod
    from src.database.postgres import EphemeralSQLitePool

    orig = store_mod.db_pool
    store_mod.db_pool = EphemeralSQLitePool(db_path=env["traj_db"])
    return store_mod, orig


# ── 迁移与单一判法 ─────────────────────────────────────────


class TestMigrationAndRowTenant:
    def test_old_schema_backfill_and_index(self, tmp_path, monkeypatch):
        from src.database.postgres import EphemeralSQLitePool
        from src.trajectory.store import TrajectoryStore

        db = tmp_path / "old" / "opensoul.db"
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as c:
            c.execute(OLD_SESSIONS_DDL)
            c.execute(OLD_SCORES_DDL)
            c.execute(
                "INSERT INTO trajectory_scores (id, session_id, name, value, created_at) "
                "VALUES ('sc1', 's1', 'legacy', 0.5, '2026-01-01T00:00:00+00:00')"
            )
            c.commit()
        import src.trajectory.store as store_mod

        pool = EphemeralSQLitePool(db_path=str(db))
        monkeypatch.setattr(store_mod, "db_pool", pool)

        async def run():
            store = TrajectoryStore()
            scores = await store.get_scores()  # ensure_tables 真跑
            assert len(scores) == 1 and scores[0].tenant_id == "default"  # 存量行回填
            # 幂等：二次 ensure 零报错
            assert len(await store.get_scores(tenant_id="default")) == 1

        asyncio.run(run())
        with sqlite3.connect(db) as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(trajectory_scores)")}
            idx = {r[1] for r in c.execute("PRAGMA index_list(trajectory_scores)")}
        assert "tenant_id" in cols  # ALTER 自愈补列
        assert "idx_traj_scores_tenant" in idx  # 索引补列之后建（③c-2① 坑延续）

    def test_score_row_tenant_fallback(self):
        from src.trajectory.store import TrajectoryScore, score_row_tenant

        assert score_row_tenant(TrajectoryScore()) == "default"
        assert score_row_tenant(TrajectoryScore(tenant_id="   ")) == "default"
        assert score_row_tenant(TrajectoryScore(tenant_id="tenant-a")) == "tenant-a"


# ── 存储层：写点归属/过滤 ──────────────────────────────────


class TestStoreAxis:
    def test_add_score_inherits_parent_and_missing_fallback(self, env):
        from src.trajectory.store import TrajectoryScore, TrajectoryStore

        ids = {}
        store_mod, orig = _swap_pool(env)
        try:

            async def run():
                store = TrajectoryStore()
                await store.create_session(
                    id="s-parent", agent_id="a", task_description="t", tenant_id="tenant-a"
                )
                sc = TrajectoryScore(session_id="s-parent", name="quality", value=0.9)
                # 显式自带键被父行继承顶替（写点=父行继承=单一权威）
                sc.tenant_id = "tenant-b"
                await store.add_score(sc)
                assert sc.tenant_id == "tenant-a"
                # 父行缺失→'default' 兜底不掉落库（键派生故障绝不吃掉落库）
                ghost = TrajectoryScore(session_id="ghost-session", name="quality")
                await store.add_score(ghost)
                assert ghost.tenant_id == "default"
                ids["sc"] = sc.id
                ids["ghost"] = ghost.id

            asyncio.run(run())
        finally:
            store_mod.db_pool = orig
        assert _row_tenant(env["traj_db"], ids["sc"]) == "tenant-a"
        assert _row_tenant(env["traj_db"], ids["ghost"]) == "default"

    def test_get_scores_filter_three_way_and_before_limit(self, env):
        from src.trajectory.store import TrajectoryScore, TrajectoryStore

        store_mod, orig = _swap_pool(env)
        try:

            async def run():
                store = TrajectoryStore()
                await store.create_session(
                    id="s-fa", agent_id="a", task_description="t", tenant_id="tenant-a"
                )
                await store.create_session(
                    id="s-fb", agent_id="a", task_description="t", tenant_id="tenant-b"
                )
                await store.add_score(TrajectoryScore(session_id="s-fa", name="q", value=1.0))
                # B 的行更新（更晚）——过滤先于截断：tenant-a limit=1 必须回 A 行
                await store.add_score(TrajectoryScore(session_id="s-fb", name="q", value=0.0))
                got_a = await store.get_scores(tenant_id="tenant-a", limit=1)
                assert [s.session_id for s in got_a] == ["s-fa"]
                got_b = await store.get_scores(tenant_id="tenant-b", limit=1)
                assert [s.session_id for s in got_b] == ["s-fb"]
                assert len(await store.get_scores()) == 2  # None=系统视图
                # name/source 过滤与租户键组合
                await store.add_score(
                    TrajectoryScore(session_id="s-fa", name="other", source="human")
                )
                combo = await store.get_scores(tenant_id="tenant-a", name="other", source="human")
                assert [s.name for s in combo] == ["other"]
                assert await store.get_scores(tenant_id="tenant-b", name="other") == []

            asyncio.run(run())
        finally:
            store_mod.db_pool = orig

    def test_get_score_summary_filter(self, env):
        from src.trajectory.store import TrajectoryScore, TrajectoryStore

        store_mod, orig = _swap_pool(env)
        try:

            async def run():
                store = TrajectoryStore()
                await store.create_session(
                    id="s-sa", agent_id="a", task_description="t", tenant_id="tenant-a"
                )
                await store.create_session(
                    id="s-sb", agent_id="a", task_description="t", tenant_id="tenant-b"
                )
                for sess, val in (("s-sa", 1.0), ("s-sa", 0.0), ("s-sb", 0.5)):
                    await store.add_score(TrajectoryScore(session_id=sess, name="q", value=val))
                sa = await store.get_score_summary(tenant_id="tenant-a")
                assert sa["total_score_names"] == 1
                assert sa["scores"][0]["count"] == 2  # 只算本租户，禁泄露全局分布
                assert sa["scores"][0]["avg_value"] == 0.5
                sb = await store.get_score_summary(tenant_id="tenant-b")
                assert sb["scores"][0]["count"] == 1
                assert sb["scores"][0]["avg_value"] == 0.5
                assert await store.get_score_summary(tenant_id="missing-tenant") == {
                    "scores": [],
                    "total_score_names": 0,
                }
                sys_view = await store.get_score_summary()  # None=系统视图
                assert sys_view["scores"][0]["count"] == 3
                # session_id 叠加过滤
                one = await store.get_score_summary(session_id="s-sa", tenant_id="tenant-a")
                assert one["scores"][0]["count"] == 2

            asyncio.run(run())
        finally:
            store_mod.db_pool = orig


# ── API 层：派生租户全链/读过滤/契约 ───────────────────────


class TestApiAxis:
    def test_add_score_parent_inheritance_and_additive_key(self, env):
        client = env["client"]
        ha = _token(env["user_tenant_a"])
        _create_session(client, ha, "s-api-a")
        # 外部/匿名评分方落分→随父行归属（③b-2 外部写方回帖语义，add_event 同款）
        r = _add_score(client, None, "s-api-a", name="quality", value=0.9)
        assert r.status_code == 200
        body = r.json()
        assert body["tenant_id"] == "tenant-a"  # 加性 tenant_id 键=父行继承
        assert _row_tenant(env["traj_db"], body["id"]) == "tenant-a"
        # 响应形状逐键不丢（加性键之外契约不变）
        for key in ("id", "session_id", "name", "value", "source", "created_at"):
            assert key in body

    def test_list_scores_three_way(self, env):
        client = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        _create_session(client, ha, "s-ls-a")
        _create_session(client, ha, "s-ls-d")
        for i in range(2):
            assert _add_score(client, ha, "s-ls-a", name=f"q{i}").status_code == 200
        ra = client.get("/api/trajectory/sessions/s-ls-a/scores", headers=ha)
        assert ra.status_code == 200
        assert len(ra.json()["scores"]) == 2  # 拥有者可见自己会话的全部评分
        assert all(s["tenant_id"] == "tenant-a" for s in ra.json()["scores"])
        # B 读 A 的会话评分恒不可见（修前全量泄露）
        rb = client.get("/api/trajectory/sessions/s-ls-a/scores", headers=hb)
        assert rb.status_code == 200 and rb.json()["scores"] == []
        # 匿名='default' 对 tenant-a 同样不可见
        rd = client.get("/api/trajectory/sessions/s-ls-a/scores")
        assert rd.json()["scores"] == []
        # 匿名契约不变：default 租户行匿名可见
        _create_session(client, None, "s-ls-anon")
        assert _add_score(client, None, "s-ls-anon", name="q").status_code == 200
        r_anon = client.get("/api/trajectory/sessions/s-ls-anon/scores")
        assert len(r_anon.json()["scores"]) == 1
        # 形状逐键不变
        assert set(r_anon.json().keys()) == {"session_id", "scores"}

    def test_score_summary_three_way(self, env):
        client = env["client"]
        ha, hb = _token(env["user_tenant_a"]), _token(env["user_tenant_b"])
        _create_session(client, ha, "s-sum")
        for val in (1.0, 0.0):
            assert _add_score(client, ha, "s-sum", name="q", value=val).status_code == 200
        ra = client.get("/api/trajectory/sessions/s-sum/scores/summary", headers=ha)
        assert ra.status_code == 200
        assert ra.json()["scores"][0]["count"] == 2
        assert ra.json()["total_score_names"] == 1
        rb = client.get("/api/trajectory/sessions/s-sum/scores/summary", headers=hb)
        assert rb.status_code == 200
        assert rb.json() == {"scores": [], "total_score_names": 0}  # 聚合禁泄露

    def test_missing_session_404_contract_unchanged(self, env):
        client = env["client"]
        ha = _token(env["user_tenant_a"])
        assert client.get("/api/trajectory/sessions/no-such/scores", headers=ha).status_code == 404
        assert (
            client.get("/api/trajectory/sessions/no-such/scores/summary", headers=ha).status_code
            == 404
        )
        assert _add_score(client, ha, "no-such").status_code == 404

    def test_bad_token_401(self, env):
        client = env["client"]
        bad = {"Authorization": "Bearer not-a-token"}
        assert client.get("/api/trajectory/sessions/x/scores", headers=bad).status_code == 401
        assert (
            client.get("/api/trajectory/sessions/x/scores/summary", headers=bad).status_code == 401
        )
