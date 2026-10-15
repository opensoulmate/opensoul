"""trajectory-v2 整表行租户键加轴测试（DEC-⑦-⑳）。

修前（HEAD 源码+生产库实证）：trajectory_v2_events 表零租户列、POST /events
写点零身份轴、/sessions 零租户过滤（任意调用方可见全部租户会话聚合）、
/sessions/{id} 与 /replay by-ID 读面零租户闸（持有 session_id 的跨租户方可读
他人会话完整轨迹/回放）——与 timeline ③c-2① / trajectory ⑦-⑥⑪ 已收的查询
过滤/归属闸不对称；生产表 0 行=非活面（收口=活面前置，DEC-⑦-⑮ 非活面先例）。

收口=v2_event_row_tenant 单一判法（自带键缺失/空白→'default' 兜底，⑦-①
_event_row_tenant 同款）+tenant_id 行键（DDL+ALTER 双轨幂等回填 'default'+
索引补列之后建）+写点行归属（bind_identity 派生租户流入，匿名 'default'
契约不变、请求体自报被 pydantic 忽略不进落行、坏 token 401 先于写入零副作用）
+读面恒过滤（/sessions 过滤先于计数）+by-ID 读面跨租户 404 与 not-found 同
契约不差分泄露（③c-1 纪律）+输出加性键 tenant_id。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/settings.database_url/db_pool 全归
tmp），绝不写生产 data/opensoul.db。
"""

import re
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)

# 修前老表形态（无 tenant_id 列）——迁移双轨的"老库"轨道
OLD_TABLE_DDL = (
    "CREATE TABLE trajectory_v2_events ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "session_id TEXT NOT NULL,"
    "agent_id TEXT NOT NULL DEFAULT '',"
    "event_type TEXT NOT NULL,"
    "content TEXT NOT NULL DEFAULT '',"
    "metadata TEXT NOT NULL DEFAULT '{}',"
    "timestamp TEXT NOT NULL)"
)


def _seed_user(db_path: Path, uid: str, username: str, tenant: str = "default") -> str:
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
        )
        c.commit()
    return uid


def _token(uid: str) -> dict:
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + tmp trajectory_v2 表 + 真 router 真 bind_identity。

    数据面：settings.database_url→tmp（SQLitePool 缺省路径跟随）+ trajectory_api
    模块 db_pool 换 tmp SQLitePool（lifespan startup 连接）。
    """
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_db = tmp_path / "trajv2.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()

    import src.api.trajectory_api as traj_api

    monkeypatch.setattr(traj_api, "db_pool", tmp_pool)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(traj_api.router, prefix="/api/trajectory-v2")
    client = TestClient(app)

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")

    with client:
        yield {
            "client": client,
            "tmp_db": tmp_db,
            "traj_api": traj_api,
            "user_a": user_a,
            "user_b": user_b,
            "user_default": user_default,
        }


def _post(client, session_id: str, headers=None, marker: str = "m"):
    resp = client.post(
        "/api/trajectory-v2/events",
        json={"session_id": session_id, "event_type": "step", "content": marker},
        headers=headers or {},
    )
    return resp


# ── 判法纯函数 ──────────────────────────────────────────────────────────


class TestRowTenantJudgment:
    def test_fallback_shapes(self, env):
        fn = env["traj_api"].v2_event_row_tenant
        assert fn(None) == "default"
        assert fn("") == "default"
        assert fn("   ") == "default"
        assert fn({}) == "default"
        assert fn({"tenant_id": ""}) == "default"
        assert fn({"tenant_id": "  "}) == "default"

    def test_value_and_dict_and_attr(self, env):
        fn = env["traj_api"].v2_event_row_tenant

        class Row:
            tenant_id = "tenant-x"

        assert fn("tenant-x") == "tenant-x"
        assert fn({"tenant_id": "tenant-x"}) == "tenant-x"
        assert fn(Row()) == "tenant-x"


# ── 迁移双轨（DDL+ALTER 幂等回填+索引补列之后建） ────────────────────────


class TestMigration:
    def test_alter_backfill_and_index(self, env):
        client, tmp_db = env["client"], env["tmp_db"]
        # 老表形态（无 tenant_id 列）+存量行
        with sqlite3.connect(tmp_db) as c:
            c.execute(OLD_TABLE_DDL)
            c.execute(
                "INSERT INTO trajectory_v2_events (session_id, event_type, timestamp) "
                "VALUES ('legacy-1', 'step', '2026-01-01T00:00:00+00:00')"
            )
            c.execute(
                "INSERT INTO trajectory_v2_events (session_id, event_type, timestamp) "
                "VALUES ('legacy-2', 'step', '2026-01-02T00:00:00+00:00')"
            )
            c.commit()
        # 任一请求触发 _ensure_table 双轨自愈
        resp = client.get("/api/trajectory-v2/sessions")
        assert resp.status_code == 200
        with sqlite3.connect(tmp_db) as c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(trajectory_v2_events)")]
            assert "tenant_id" in cols
            backfilled = c.execute("SELECT DISTINCT tenant_id FROM trajectory_v2_events").fetchall()
            assert backfilled == [("default",)]  # 存量行随 DEFAULT 回填
            idx = [r[1] for r in c.execute("PRAGMA index_list(trajectory_v2_events)")]
            assert "idx_traj_v2_tenant" in idx  # 补列之后建（③c-2① 坑）
        # 幂等重跑：第二次请求不炸不重复补列
        resp2 = client.get("/api/trajectory-v2/sessions")
        assert resp2.status_code == 200
        assert resp2.json()["total"] == 2  # 匿名面见 default 存量行

    def test_fresh_ddl_has_column(self, env):
        client, tmp_db = env["client"], env["tmp_db"]
        assert _post(client, "s-fresh").status_code == 200
        with sqlite3.connect(tmp_db) as c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(trajectory_v2_events)")]
        assert "tenant_id" in cols


# ── 写点行归属 ──────────────────────────────────────────────────────────


class TestWriteAttribution:
    def test_derived_tenant_lands_in_row(self, env):
        client = env["client"]
        resp = _post(client, "s-a", headers=_token(env["user_a"]), marker="A")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tenant_id"] == "tenant-a"  # 加性键=归一值
        with sqlite3.connect(env["tmp_db"]) as c:
            rows = c.execute(
                "SELECT tenant_id FROM trajectory_v2_events WHERE session_id = 's-a'"
            ).fetchall()
        assert rows == [("tenant-a",)]  # 落行=派生租户（修前恒无键/无列）

    def test_self_report_in_body_ignored(self, env):
        client = env["client"]
        resp = client.post(
            "/api/trajectory-v2/events",
            json={
                "session_id": "s-a",
                "event_type": "step",
                "content": "x",
                "tenant_id": "attacker-tenant",  # 自报被 pydantic 忽略
            },
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "tenant-a"

    def test_anonymous_default_contract(self, env):
        client = env["client"]
        resp = _post(client, "s-anon")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tenant_id"] == "default"
        # 形状逐键不变+加性键
        assert set(data.keys()) == {
            "id",
            "session_id",
            "agent_id",
            "event_type",
            "content",
            "metadata",
            "timestamp",
            "tenant_id",
        }

    def test_bad_token_401_zero_rows(self, env):
        client = env["client"]
        resp = _post(client, "s-bad", headers={"Authorization": "Bearer not-a-token"})
        assert resp.status_code == 401
        with sqlite3.connect(env["tmp_db"]) as c:
            has_table = c.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='trajectory_v2_events'"
            ).fetchone()[0]
            # 坏 token 在 _ensure_table/写入之前即 401——表都可能未建=零副作用硬证
            n = (
                c.execute(
                    "SELECT COUNT(*) FROM trajectory_v2_events WHERE session_id = 's-bad'"
                ).fetchone()[0]
                if has_table
                else 0
            )
        assert n == 0  # 401 先于写入零副作用


# ── 读面恒过滤 ──────────────────────────────────────────────────────────


class TestReadFaces:
    def test_sessions_three_way_isolation_and_total(self, env):
        client = env["client"]
        _post(client, "s-a", headers=_token(env["user_a"]), marker="A")
        _post(client, "s-a2", headers=_token(env["user_a"]), marker="A2")
        _post(client, "s-b", headers=_token(env["user_b"]), marker="B")
        _post(client, "s-anon", marker="N")

        ra = client.get("/api/trajectory-v2/sessions", headers=_token(env["user_a"]))
        rb = client.get("/api/trajectory-v2/sessions", headers=_token(env["user_b"]))
        rn = client.get("/api/trajectory-v2/sessions")
        assert ra.status_code == rb.status_code == rn.status_code == 200
        a_sessions = {s["session_id"] for s in ra.json()["sessions"]}
        b_sessions = {s["session_id"] for s in rb.json()["sessions"]}
        n_sessions = {s["session_id"] for s in rn.json()["sessions"]}
        assert a_sessions == {"s-a", "s-a2"}  # 修前三方互见全部 4 行
        assert b_sessions == {"s-b"}
        assert n_sessions == {"s-anon"}
        # total 只计可见会话（过滤先于计数，禁泄露全局分布）
        assert ra.json()["total"] == 2
        assert rb.json()["total"] == 1
        assert rn.json()["total"] == 1

    def test_sessions_additive_key_and_shape(self, env):
        client = env["client"]
        _post(client, "s-a", headers=_token(env["user_a"]), marker="A")
        rows = client.get("/api/trajectory-v2/sessions", headers=_token(env["user_a"])).json()[
            "sessions"
        ]
        assert len(rows) == 1
        row = rows[0]
        assert row["tenant_id"] == "tenant-a"
        for key in ("session_id", "event_count", "first_event", "last_event", "agents"):
            assert key in row  # 既有键逐键不变

    def test_get_byid_cross_tenant_same_contract_as_missing(self, env):
        client = env["client"]
        _post(client, "s-a", headers=_token(env["user_a"]), marker="A")
        own = client.get("/api/trajectory-v2/sessions/s-a", headers=_token(env["user_a"]))
        assert own.status_code == 200
        assert own.json()["events"][0]["tenant_id"] == "tenant-a"

        cross = client.get("/api/trajectory-v2/sessions/s-a", headers=_token(env["user_b"]))
        missing = client.get(
            "/api/trajectory-v2/sessions/no-such-session", headers=_token(env["user_b"])
        )
        assert cross.status_code == missing.status_code == 404
        assert cross.json()["detail"] == missing.json()["detail"]  # 不差分泄露
        # 匿名 default 面同样不可见
        anon = client.get("/api/trajectory-v2/sessions/s-a")
        assert anon.status_code == 404

    def test_replay_byid_cross_tenant_same_contract(self, env):
        client = env["client"]
        _post(client, "s-a", headers=_token(env["user_a"]), marker="A1")
        _post(client, "s-a", headers=_token(env["user_a"]), marker="A2")
        own = client.get("/api/trajectory-v2/sessions/s-a/replay", headers=_token(env["user_a"]))
        assert own.status_code == 200
        steps = own.json()["steps"]
        assert [s["content"] for s in steps] == ["A1", "A2"]
        assert all(s["tenant_id"] == "tenant-a" for s in steps)  # 加性键

        cross = client.get("/api/trajectory-v2/sessions/s-a/replay", headers=_token(env["user_b"]))
        missing = client.get(
            "/api/trajectory-v2/sessions/no-such/replay", headers=_token(env["user_b"])
        )
        assert cross.status_code == missing.status_code == 404
        assert cross.json()["detail"] == missing.json()["detail"]

    def test_health_system_view(self, env):
        resp = env["client"].get("/api/trajectory-v2/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "component": "trajectory-v2"}


# ── 单一判法真消费 + 静态接线普查 ────────────────────────────────────────


class TestSingleSource:
    def test_judgment_consumed_write_and_output(self, env, monkeypatch):
        """monkeypatch 翻转判法→落行值与输出键全跟走=消费单一源非行内私抄。"""
        client, traj_api = env["client"], env["traj_api"]
        monkeypatch.setattr(traj_api, "v2_event_row_tenant", lambda raw: "flipped-tenant")
        resp = _post(client, "s-flip", headers=_token(env["user_a"]), marker="F")
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "flipped-tenant"
        with sqlite3.connect(env["tmp_db"]) as c:
            rows = c.execute(
                "SELECT tenant_id FROM trajectory_v2_events WHERE session_id = 's-flip'"
            ).fetchall()
        assert rows == [("flipped-tenant",)]  # 落行跟着判法走

    def test_static_census(self, env):
        src = (Path(__file__).parent.parent / "src" / "api" / "trajectory_api.py").read_text(
            encoding="utf-8"
        )
        pipe = chr(124)
        # 4 个数据面端点各恰 1 个 bind_identity 派生点（health 系统视图不接）
        assert src.count("= bind_identity(") == 4
        assert src.count(f"authorization: str {pipe} None = Header(None)") == 4
        # 判法单一源：定义恰 1 处，消费点（写点+读面加性键）
        assert src.count("def v2_event_row_tenant") == 1
        assert src.count("v2_event_row_tenant(") >= 6
        # 读面 SQL 恒带租户键（2 bulk + 4 by-ID/写点回读）
        assert src.count("WHERE tenant_id = ?") == 2
        assert src.count("AND tenant_id = ?") == 4
