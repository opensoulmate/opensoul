"""trajectory end_event 写面归属闸测试（DEC-⑦-⑲：⑦-⑪ 诚实边界留档
「end_event 写面无身份轴（另拍）」收口）。

修前（⑦-⑪ 留档+生产实证）：POST /api/trajectory/events/{event_id}/end 零鉴权
零租户闸——持有 event_id 的任意调用方可改写他人 span 行（end_at/duration/status/
token_usage/metadata.termination）并经 token_usage fold-back 改写 session.
total_tokens=跨租户改写面（生产 trajectory_events 12,135 行 3,080 个真收口）。

收口=store.end_event 增 tenant_id 可选闸（改写权威=get_event 同一父 session 行
闸门，⑦-⑪ 单一权威；显式=跨租户/缺行回 None 与 not-found 同契约不差分泄露且
UPDATE 前置零改写，None=系统视图字节不变）+API bind_identity 派生租户流入
（匿名 'default' 契约不变——单机存量行全 'default'，acp-proxy
trajectory_recorder 匿名收口自建 span 的既有链零回归）。

语义收口（既有行为变更如实记）：匿名外部写方写入他租户会话的 span（⑦-⑥
add_event 外部写方语义照旧落行）此后不能再匿名收口——收口需持租户凭据
（wakeup executor 凭据轴同族解法留档）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB 归 tmp + store.db_pool 换
EphemeralSQLitePool(tmp) + settings.database_url 同 tmp——绝不碰生产库）。
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

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_d = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")
    yield {
        "client": client,
        "traj_db": traj_db,
        "ha": _token(user_a),
        "hb": _token(user_b),
        "hd": _token(user_d),
    }


def _create_session(client, headers, session_id="sess-a-1"):
    body = {"agent_id": "agent-x", "task_description": "alpha task", "session_id": session_id}
    r = client.post("/api/trajectory/sessions", json=body, headers=headers or {})
    assert r.status_code == 200
    return r.json()


def _record_event(client, headers, session_id, content="secret payload"):
    r = client.post(
        f"/api/trajectory/sessions/{session_id}/events",
        json={"event_type": "llm_call", "agent_id": "agent-x", "content": content},
        headers=headers or {},
    )
    assert r.status_code == 200
    return r.json()


def _row(traj_db, event_id):
    with sqlite3.connect(traj_db) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT * FROM trajectory_events WHERE id = ?", (event_id,)).fetchone()
        return dict(row) if row else None


def _session_tokens(traj_db, session_id):
    with sqlite3.connect(traj_db) as c:
        return c.execute(
            "SELECT total_tokens FROM trajectory_sessions WHERE id = ?", (session_id,)
        ).fetchone()[0]


def _seed_a(env):
    c = env["client"]
    sess = _create_session(c, env["ha"])
    sid = sess["id"] if "id" in sess else sess["session_id"]
    ev = _record_event(c, env["ha"], sid)
    return sid, ev["id"]


# ── store 层归属闸 ────────────────────────────────────────────


class TestStoreGate:
    def test_system_view_unchanged(self, env):
        """None=系统视图字节不变（既有内部调用/测试零变化，跨租户照关）。"""
        import src.trajectory.store as store_mod
        from src.trajectory.store import TrajectoryEvent

        async def run():
            s = await store_mod.trajectory_store.create_session(
                agent_id="a", task_description="t", tenant_id="tenant-a"
            )
            ev = await store_mod.trajectory_store.add_event(
                TrajectoryEvent(session_id=s.id, event_type="llm_call", content="x", token_usage=0)
            )
            closed = await store_mod.trajectory_store.end_event(ev.id, status="ok", token_usage=7)
            assert closed is not None and closed.end_at is not None
            assert closed.status == "ok" and closed.token_usage == 7
            s2 = await store_mod.trajectory_store.get_session(s.id)
            assert s2.total_tokens == 7  # fold-back 照旧

        asyncio.run(run())

    def test_same_tenant_closes(self, env):
        import src.trajectory.store as store_mod
        from src.trajectory.store import TrajectoryEvent

        async def run():
            s = await store_mod.trajectory_store.create_session(
                agent_id="a", task_description="t", tenant_id="tenant-a"
            )
            ev = await store_mod.trajectory_store.add_event(
                TrajectoryEvent(session_id=s.id, event_type="llm_call", content="x")
            )
            closed = await store_mod.trajectory_store.end_event(
                ev.id, status="error", token_usage=5, termination="max_turns", tenant_id="tenant-a"
            )
            assert closed is not None
            assert closed.end_at is not None and closed.status == "error"
            assert closed.metadata.get("termination") == "max_turns"
            s2 = await store_mod.trajectory_store.get_session(s.id)
            assert s2.total_tokens == 5

        asyncio.run(run())

    def test_cross_tenant_denied_zero_effect(self, env):
        """跨租户改写=回 None 与 not-found 同契约，行零改写硬反证。"""
        import src.trajectory.store as store_mod
        from src.trajectory.store import TrajectoryEvent

        async def run():
            s = await store_mod.trajectory_store.create_session(
                agent_id="a", task_description="t", tenant_id="tenant-a"
            )
            ev = await store_mod.trajectory_store.add_event(
                TrajectoryEvent(session_id=s.id, event_type="llm_call", content="x", token_usage=3)
            )
            denied = await store_mod.trajectory_store.end_event(
                ev.id, status="error", token_usage=99, tenant_id="tenant-b"
            )
            assert denied is None
            row = _row(env["traj_db"], ev.id)
            assert row["end_at"] is None  # 零改写
            assert row["status"] == "ok"  # 原值零改写
            assert row["token_usage"] == 3
            assert _session_tokens(env["traj_db"], s.id) == 3  # fold-back 零改写

        asyncio.run(run())

    def test_missing_same_contract(self, env):
        import src.trajectory.store as store_mod

        async def run():
            # 缺行与跨租户同契约（不差分泄露）
            assert await store_mod.trajectory_store.end_event("nope", tenant_id="tenant-a") is None

        asyncio.run(run())


# ── API 层全链（bind_identity 派生→store 闸） ─────────────────


class TestApiEndGate:
    def test_anonymous_default_contract(self, env):
        """匿名关 'default' 行 200 契约不变（recorder 匿名收口自建 span 零回归钉）。"""
        c = env["client"]
        sess = _create_session(c, env["hd"], session_id="sess-d-1")
        sid = sess["id"] if "id" in sess else sess["session_id"]
        ev = _record_event(c, env["hd"], sid, content="default content")
        r = c.post(
            f"/api/trajectory/events/{ev['id']}/end",
            json={"status": "ok", "token_usage": 11},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["end_at"] is not None and body["status"] == "ok"
        assert body["token_usage"] == 11
        assert _session_tokens(env["traj_db"], sid) == 11

    def test_anonymous_cross_tenant_denied(self, env):
        """匿名（='default'）关他租户 span=404 行零改写（修前 200 真改写）。"""
        c = env["client"]
        sid, eid = _seed_a(env)
        r = c.post(f"/api/trajectory/events/{eid}/end", json={"status": "error"})
        assert r.status_code == 404 and r.json()["detail"] == "Event not found"
        row = _row(env["traj_db"], eid)
        assert row["end_at"] is None and row["status"] == "ok"  # 原值零改写

    def test_cross_tenant_404_same_contract_and_zero_effect(self, env):
        c = env["client"]
        sid, eid = _seed_a(env)
        # B 冒改 A 的 span=404 与缺行逐字同契约（不差分泄露）
        r_b = c.post(
            f"/api/trajectory/events/{eid}/end",
            json={"status": "error", "token_usage": 999},
            headers=env["hb"],
        )
        r_missing = c.post(
            "/api/trajectory/events/no-such/end", json={"status": "error"}, headers=env["hb"]
        )
        assert r_b.status_code == r_missing.status_code == 404
        assert r_b.json()["detail"] == r_missing.json()["detail"] == "Event not found"
        row = _row(env["traj_db"], eid)
        assert (
            row["end_at"] is None and row["status"] == "ok"
        )  # 原值零改写 and row["token_usage"] == 0
        assert _session_tokens(env["traj_db"], sid) == 0  # fold-back 零改写
        # A 正对照照常收口
        r = c.post(
            f"/api/trajectory/events/{eid}/end",
            json={"status": "ok", "token_usage": 4},
            headers=env["ha"],
        )
        assert r.status_code == 200 and r.json()["end_at"] is not None
        assert _session_tokens(env["traj_db"], sid) == 4

    def test_default_user_three_way(self, env):
        """default 租户认证用户：关 default 行 200、关 tenant-a 行 404。"""
        c = env["client"]
        sid, eid = _seed_a(env)
        assert (
            c.post(f"/api/trajectory/events/{eid}/end", json={}, headers=env["hd"]).status_code
            == 404
        )
        sess = _create_session(c, env["hd"], session_id="sess-d-2")
        sid_d = sess["id"] if "id" in sess else sess["session_id"]
        ev_d = _record_event(c, env["hd"], sid_d)
        assert (
            c.post(
                f"/api/trajectory/events/{ev_d['id']}/end", json={}, headers=env["hd"]
            ).status_code
            == 200
        )

    def test_bad_token_401(self, env):
        c = env["client"]
        _, eid = _seed_a(env)
        r = c.post(
            f"/api/trajectory/events/{eid}/end",
            json={},
            headers={"Authorization": "Bearer bad.token"},
        )
        assert r.status_code == 401

    def test_external_writer_semantics_change(self, env):
        """语义收口锁（既有行为变更如实记）：匿名外部写方写入他租户会话的 span
        仍照常落行（⑦-⑥ add_event 外部写方语义零变化），但匿名收口该 span 此后
        =404（修前 200 真改写）——收口需持租户凭据。"""
        c = env["client"]
        sid, _ = _seed_a(env)
        # ⑦-⑥ 契约照旧：匿名写方落事件，行=父行继承
        r_add = c.post(
            f"/api/trajectory/sessions/{sid}/events",
            json={"event_type": "tool_call", "agent_id": "agent-x", "content": "ext write"},
        )
        assert r_add.status_code == 200 and r_add.json()["tenant_id"] == "tenant-a"
        ext_id = r_add.json()["id"]
        # 修前可匿名关（控制组反证面）；修后=404 行零改写
        r_end = c.post(f"/api/trajectory/events/{ext_id}/end", json={"status": "ok"})
        assert r_end.status_code == 404
        assert _row(env["traj_db"], ext_id)["end_at"] is None
        # 持租户凭据照常收口
        assert (
            c.post(
                f"/api/trajectory/events/{ext_id}/end", json={"status": "ok"}, headers=env["ha"]
            ).status_code
            == 200
        )
