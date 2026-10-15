"""trajectory 面 by-ID 读面归属闸测试（DEC-⑦-⑪：DEC-⑦ 留档「by-ID 读面」拆件）。

修前（⑦-⑥ 诚实边界①留档+控制组实证）：GET /sessions/{id}、GET /sessions/
{session_id}/events、GET /events/{event_id}、GET /sessions/{session_id}/replay、
GET /sessions/{session_id}/trace 五读面零租户闸——持有 id 的跨租户方可读单行/
全事件/回放/trace 树（list/search 已滤、by-ID 未收）；fork_session 源读零闸=
拿他人会话内容跑克隆（⑦-⑩ promote/auto-process 同型）。

收口=get_session/get_events/get_event/get_trace_tree 增 tenant_id 可选闸（读取
权威=父 session 行闸门，③b-2 读取权威=父行同款；显式=跨租户与 not-found 同契
约不差分泄露，None=系统视图字节不变）+API 五读面 bind_identity 派生租户流入
（匿名 'default' 契约不变——单机存量行全 'default'）+fork 源读闸（跨租户=与
缺源同一 ValueError 同契约，克隆零副作用）。

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


def _create_session(client, headers, **kw):
    body = {"agent_id": "agent-x", "task_description": kw.pop("task_description", "alpha task")}
    body.update(kw)
    r = client.post("/api/trajectory/sessions", json=body, headers=headers or {})
    assert r.status_code == 200
    return r.json()


def _record_event(client, headers, session_id, content="secret payload"):
    r = client.post(
        f"/api/trajectory/sessions/{session_id}/events",
        json={"event_type": "tool_call", "agent_id": "agent-x", "content": content},
        headers=headers or {},
    )
    assert r.status_code == 200
    return r.json()


def _count_sessions(traj_db):
    with sqlite3.connect(traj_db) as c:
        return c.execute("SELECT COUNT(*) FROM trajectory_sessions").fetchone()[0]


# ── store 层归属闸 ────────────────────────────────────────────


class TestStoreGate:
    def test_get_session_gate(self, env):
        import src.trajectory.store as store_mod

        async def run():
            s = await store_mod.trajectory_store.create_session(
                agent_id="a", task_description="t", tenant_id="tenant-a"
            )
            # 系统视图不变（None 不闸）
            assert await store_mod.trajectory_store.get_session(s.id) is not None
            # 自租户可见
            own = await store_mod.trajectory_store.get_session(s.id, tenant_id="tenant-a")
            assert own is not None and own.id == s.id
            # 跨租户=与 not-found 同契约
            assert await store_mod.trajectory_store.get_session(s.id, tenant_id="tenant-b") is None
            # 缺行同契约
            assert (
                await store_mod.trajectory_store.get_session("nope", tenant_id="tenant-a") is None
            )

        asyncio.run(run())

    def test_get_events_gate(self, env):
        import src.trajectory.store as store_mod

        async def run():
            s = await store_mod.trajectory_store.create_session(
                agent_id="a", task_description="t", tenant_id="tenant-a"
            )
            from src.trajectory.store import TrajectoryEvent

            for i in range(2):
                await store_mod.trajectory_store.add_event(
                    TrajectoryEvent(session_id=s.id, event_type="tool_call", content=f"e{i}")
                )
            assert len(await store_mod.trajectory_store.get_events(s.id)) == 2  # None 系统视图
            assert len(await store_mod.trajectory_store.get_events(s.id, tenant_id="tenant-a")) == 2
            # 跨租户=与空事件同契约
            assert await store_mod.trajectory_store.get_events(s.id, tenant_id="tenant-b") == []

        asyncio.run(run())

    def test_get_event_gate(self, env):
        import src.trajectory.store as store_mod
        from src.trajectory.store import TrajectoryEvent

        async def run():
            s = await store_mod.trajectory_store.create_session(
                agent_id="a", task_description="t", tenant_id="tenant-a"
            )
            ev = await store_mod.trajectory_store.add_event(
                TrajectoryEvent(session_id=s.id, event_type="tool_call", content="secret payload")
            )
            assert await store_mod.trajectory_store.get_event(ev.id) is not None  # None 系统视图
            own = await store_mod.trajectory_store.get_event(ev.id, tenant_id="tenant-a")
            assert own is not None and own.content == "secret payload"
            assert await store_mod.trajectory_store.get_event(ev.id, tenant_id="tenant-b") is None
            assert await store_mod.trajectory_store.get_event("nope", tenant_id="tenant-a") is None

        asyncio.run(run())

    def test_get_trace_tree_gate(self, env):
        import src.trajectory.store as store_mod
        from src.trajectory.store import TrajectoryEvent

        async def run():
            s = await store_mod.trajectory_store.create_session(
                agent_id="a", task_description="t", tenant_id="tenant-a"
            )
            await store_mod.trajectory_store.add_event(
                TrajectoryEvent(session_id=s.id, event_type="tool_call", content="x")
            )
            own = await store_mod.trajectory_store.get_trace_tree(s.id, tenant_id="tenant-a")
            assert own["total_events"] == 1 and own["roots"]
            cross = await store_mod.trajectory_store.get_trace_tree(s.id, tenant_id="tenant-b")
            # 跨租户=与空树同契约（API 层另有 get_session 404 前置）
            assert cross["total_events"] == 0 and cross["roots"] == []
            assert (await store_mod.trajectory_store.get_trace_tree(s.id))["total_events"] == 1

        asyncio.run(run())

    def test_fork_source_gate(self, env):
        import src.trajectory.store as store_mod
        from src.trajectory.store import TrajectoryEvent

        async def run():
            s = await store_mod.trajectory_store.create_session(
                agent_id="a", task_description="t", tenant_id="tenant-a"
            )
            ev = await store_mod.trajectory_store.add_event(
                TrajectoryEvent(session_id=s.id, event_type="tool_call", content="secret payload")
            )
            # 跨租户 fork=与缺源同一 ValueError 同契约（禁差分泄露），克隆零副作用
            with pytest.raises(ValueError, match="not found"):
                await store_mod.trajectory_store.fork_session(
                    source_session_id=s.id,
                    fork_point_event_id=ev.id,
                    tenant_id="tenant-b",
                )
            # 缺源契约逐字一致（同契约硬证）
            with pytest.raises(ValueError) as exc_cross:
                await store_mod.trajectory_store.fork_session(
                    source_session_id=s.id, fork_point_event_id=ev.id, tenant_id="tenant-b"
                )
            with pytest.raises(ValueError) as exc_missing:
                await store_mod.trajectory_store.fork_session(
                    source_session_id="no-such", fork_point_event_id="", tenant_id="tenant-b"
                )
            assert str(exc_cross.value).replace(s.id, "SID") == str(exc_missing.value).replace(
                "no-such", "SID"
            )
            assert await store_mod.trajectory_store.count_sessions() == 1  # 零克隆副作用
            # 自租户 fork 正对照照常
            forked = await store_mod.trajectory_store.fork_session(
                source_session_id=s.id,
                fork_point_event_id=ev.id,
                tenant_id="tenant-a",
            )
            assert forked.tenant_id == "tenant-a"
            copied = await store_mod.trajectory_store.get_events(forked.id, tenant_id="tenant-a")
            assert any("secret payload" in e.content for e in copied)

        asyncio.run(run())


# ── API 层全链（bind_identity 派生→store 闸） ─────────────────


class TestApiByidGate:
    def _seed_a(self, env):
        c = env["client"]
        sess = _create_session(c, env["ha"], session_id="sess-a-1")
        ev = _record_event(c, env["ha"], sess["id"] if "id" in sess else sess["session_id"])
        sid = sess["id"] if "id" in sess else sess["session_id"]
        return sid, ev["id"]

    def test_get_session_three_way(self, env):
        c = env["client"]
        sid, _ = self._seed_a(env)
        # A 正对照
        r = c.get(f"/api/trajectory/sessions/{sid}", headers=env["ha"])
        assert r.status_code == 200 and r.json()["tenant_id"] == "tenant-a"
        # B 冒读=404 与 not-found 同契约（detail 逐字与缺行一致）
        r_b = c.get(f"/api/trajectory/sessions/{sid}", headers=env["hb"])
        r_missing = c.get("/api/trajectory/sessions/no-such", headers=env["hb"])
        assert r_b.status_code == 404 and r_missing.status_code == 404
        assert r_b.json()["detail"] == r_missing.json()["detail"] == "Session not found"
        assert "alpha task" not in r_b.text  # 零内容泄露

    def test_get_events_three_way(self, env):
        c = env["client"]
        sid, _ = self._seed_a(env)
        r = c.get(f"/api/trajectory/sessions/{sid}/events", headers=env["ha"])
        assert r.status_code == 200 and r.json()["count"] == 1
        r_b = c.get(f"/api/trajectory/sessions/{sid}/events", headers=env["hb"])
        r_missing = c.get("/api/trajectory/sessions/no-such/events", headers=env["hb"])
        assert r_b.status_code == 404 and r_missing.status_code == 404
        assert r_b.json()["detail"] == r_missing.json()["detail"]
        assert "secret payload" not in r_b.text

    def test_get_event_three_way(self, env):
        c = env["client"]
        _, eid = self._seed_a(env)
        r = c.get(f"/api/trajectory/events/{eid}", headers=env["ha"])
        assert r.status_code == 200 and r.json()["content"] == "secret payload"
        r_b = c.get(f"/api/trajectory/events/{eid}", headers=env["hb"])
        r_missing = c.get("/api/trajectory/events/no-such", headers=env["hb"])
        assert r_b.status_code == 404 and r_missing.status_code == 404
        assert r_b.json()["detail"] == r_missing.json()["detail"] == "Event not found"
        assert "secret payload" not in r_b.text

    def test_replay_and_trace_three_way(self, env):
        c = env["client"]
        sid, _ = self._seed_a(env)
        for path in ("replay", "trace"):
            r = c.get(f"/api/trajectory/sessions/{sid}/{path}", headers=env["ha"])
            assert r.status_code == 200
            r_b = c.get(f"/api/trajectory/sessions/{sid}/{path}", headers=env["hb"])
            r_missing = c.get(f"/api/trajectory/sessions/no-such/{path}", headers=env["hb"])
            assert r_b.status_code == 404 and r_missing.status_code == 404
            assert r_b.json()["detail"] == r_missing.json()["detail"]
            assert "secret payload" not in r_b.text

    def test_fork_cross_tenant_same_contract_and_zero_effect(self, env):
        c = env["client"]
        sid, eid = self._seed_a(env)
        before = _count_sessions(env["traj_db"])
        r_b = c.post(
            f"/api/trajectory/sessions/{sid}/fork",
            json={"fork_point_event_id": eid},
            headers=env["hb"],
        )
        r_missing = c.post(
            "/api/trajectory/sessions/no-such/fork",
            json={"fork_point_event_id": ""},
            headers=env["hb"],
        )
        assert r_b.status_code == r_missing.status_code == 400  # 同契约（缺源既有形态）
        detail_cross = r_b.json().get("detail", "").replace(sid, "SID")
        detail_missing = r_missing.json().get("detail", "").replace("no-such", "SID")
        assert detail_cross == detail_missing and "not found" in detail_cross
        assert "secret payload" not in r_b.text
        assert _count_sessions(env["traj_db"]) == before  # 零克隆副作用
        # 自租户 fork 正对照
        r = c.post(
            f"/api/trajectory/sessions/{sid}/fork",
            json={"fork_point_event_id": eid},
            headers=env["ha"],
        )
        assert r.status_code == 200

    def test_anonymous_default_contract(self, env):
        c = env["client"]
        sid, eid = self._seed_a(env)
        # 匿名读租户行=404（与 not-found 同契约）
        assert c.get(f"/api/trajectory/sessions/{sid}").status_code == 404
        assert c.get(f"/api/trajectory/events/{eid}").status_code == 404
        # 匿名 default 行契约不变（⑦-⑥ 匿名语义钉）
        sess_d = _create_session(c, env["hd"], session_id="sess-d-1")
        sid_d = sess_d["id"] if "id" in sess_d else sess_d["session_id"]
        ev_d = _record_event(c, env["hd"], sid_d, content="default content")
        r = c.get(f"/api/trajectory/sessions/{sid_d}")
        assert r.status_code == 200 and r.json()["tenant_id"] == "default"
        assert c.get(f"/api/trajectory/events/{ev_d['id']}").status_code == 200

    def test_bad_token_401(self, env):
        c = env["client"]
        sid, _ = self._seed_a(env)
        r = c.get(f"/api/trajectory/sessions/{sid}", headers={"Authorization": "Bearer bad.token"})
        assert r.status_code == 401

    def test_write_face_contract_unchanged(self, env):
        """写闸契约零变化回归钉（⑦-⑥ 边界②：add_event 跨租户可落事件=文档化语义）。"""
        c = env["client"]
        sid, _ = self._seed_a(env)
        # 跨租户 POST 事件（匿名写方）仍照常落（父行继承，⑦-⑥ 边界②）
        r = c.post(
            f"/api/trajectory/sessions/{sid}/events",
            json={"event_type": "tool_call", "agent_id": "agent-x", "content": "ext write"},
        )
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "tenant-a"  # 父行继承
        # delete 跨租户 404 契约零变化（⑦-⑥ delete 闸）
        r = c.delete(f"/api/trajectory/sessions/{sid}", headers=env["hb"])
        assert r.status_code == 404
