"""F3b-⑨b-② brain 2 调用点接派生——think/verify emit 带 bind_identity 派生租户键。

F3b-⑨b-①（桥本体）已落：emit 可选 tenant_id→record 行归属。本拍=调用点分批
接派生最小件：brain.py think:237/verify:303 两 emit 点位（F3b-⑨ 审计"census 里
brain 2 已 bind_identity=零新参数最小件首选"）。

修前：两调用点不传 tenant_id→事件 dict 无键→record() 'default' 兜底，非 default
租户身份用户的 think/verify 动作事件落 'default'→被自己的租户过滤查询挡掉
=自动作自我不可见（F3b-⑨ live 硬证同型）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp TimelineStore 注入/死端口 _BASE），
绝不写生产 opensoul.db/enterprise.db/timeline.db、不打 live :8090。
"""

import asyncio
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from src.config import settings
from src.nerve import event_bridge
from src.services import tenant_binding as tb
from src.timeline.store import TimelineStore

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


def _rows(db_path: str) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute("SELECT * FROM events ORDER BY timestamp, event_id")]
    con.close()
    return out


@pytest.fixture()
def brain_env(tmp_path, monkeypatch):
    """brain router + tmp 身份库 + tmp TimelineStore + tmp SQLitePool（lifespan 真连接）。

    测试面=真实 router/think/verify/emit/record 全链；数据面全 tmp。
    """
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.brain as brain_api
    import src.database.postgres as pg_mod
    from src.database.postgres import SQLitePool

    # 身份库隔离（bind_identity 真源绝不碰生产）
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    ent_db = tmp_path / "enterprise.db"
    conn = sqlite3.connect(ent_db)
    conn.execute(ENTERPRISE_DDL)
    conn.commit()
    conn.close()

    # 元认知账本/记忆等数据面隔离
    tmp_db = tmp_path / "brain_data.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", tmp_pool)

    # timeline 行归属真源=tmp（emit._timeline 注入，绝不写生产 timeline.db）
    tl_db = str(tmp_path / "timeline.db")
    store = TimelineStore(tl_db)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    # 死端口：emit 自建 client 的 nerve publish 快速连接失败（被 emit 自身吞掉），
    # 绝不打 live :8090
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    from src.api import event_stream

    event_stream._event_buffer.clear()

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(brain_api.router, prefix="/api/brain")
    with TestClient(app) as c:
        yield c, tl_db, ent_db, event_stream
    event_stream._event_buffer.clear()


def _think(c, token=None, body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return c.post("/api/brain/think", json=body or {"user_input": "测试意图"}, headers=headers)


def _verify(c, token=None, body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return c.post(
        "/api/brain/verify",
        json=body or {"task_id": "t1", "action": "do", "result": {"success": True}},
        headers=headers,
    )


# ── ① think/verify 调用点真接派生 → record 行归属 ─────────────────────


class TestBrainEmitTenantWiring:
    def test_think_emit_row_tenant_derived(self, brain_env):
        """认证派生租户→think 事件行落派生租户（修前恒 'default' 自我不可见）。"""
        c, tl_db, ent_db, _es = brain_env
        uid = _insert_ent_user(ent_db, "alice", "tenant-emit-a")
        token = _ent_token(uid, "alice")
        r = _think(c, token)
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "think"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-emit-a"  # 非 'default'=真接派生
        assert rows[0]["organ"] == "brain"

    def test_verify_emit_row_tenant_derived(self, brain_env):
        c, tl_db, ent_db, _es = brain_env
        uid = _insert_ent_user(ent_db, "bob", "tenant-emit-b")
        token = _ent_token(uid, "bob")
        time.sleep(0.01)  # evt id 含 ms 时间戳，防同 ms 去重假阴
        r = _verify(c, token)
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "verify"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-emit-b"

    def test_buffer_dict_carries_tenant_key(self, brain_env):
        """事件 dict 自带键（bus/缓冲/record 同源注入）——F3b-⑨b-① 三处注入真流经调用点。"""
        c, tl_db, ent_db, es = brain_env
        uid = _insert_ent_user(ent_db, "carol", "tenant-emit-c")
        token = _ent_token(uid, "carol")
        _think(c, token)
        buffered = [e for e in es._event_buffer if e.get("type") == "think"]
        assert len(buffered) == 1
        assert buffered[0]["tenant_id"] == "tenant-emit-c"

    def test_dual_tenant_rows_isolated(self, brain_env):
        """双租户各自落行互不串键（think 与 verify 分型避同 ms id 去重）。"""
        c, tl_db, ent_db, _es = brain_env
        uid_a = _insert_ent_user(ent_db, "u_a", "tenant-dual-a")
        uid_b = _insert_ent_user(ent_db, "u_b", "tenant-dual-b")
        time.sleep(0.01)
        assert _think(c, _ent_token(uid_a, "u_a")).status_code == 200
        time.sleep(0.01)
        assert _verify(c, _ent_token(uid_b, "u_b")).status_code == 200
        rows = _rows(tl_db)
        think_rows = [x for x in rows if x["event_type"] == "think"]
        verify_rows = [x for x in rows if x["event_type"] == "verify"]
        assert [x["tenant_id"] for x in think_rows] == ["tenant-dual-a"]
        assert [x["tenant_id"] for x in verify_rows] == ["tenant-dual-b"]


# ── ② 不变面（契约锁死） ──────────────────────────────────────────


class TestBrainEmitTenantInvariants:
    def test_anonymous_default_contract_unchanged(self, brain_env):
        """匿名='default' 行值不变（调用点接派生后匿名真态零变化）。"""
        c, tl_db, _ent, _es = brain_env
        assert _think(c).status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "think"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_self_report_mismatch_403_zero_event(self, brain_env):
        """自报矛盾=403（bind_identity 先于 emit），零事件行落库。"""
        c, tl_db, ent_db, _es = brain_env
        uid = _insert_ent_user(ent_db, "dave", "tenant-emit-d")
        token = _ent_token(uid, "dave")
        r = _think(c, token, {"user_input": "x", "tenant_id": "attacker-tenant"})
        assert r.status_code == 403
        assert _rows(tl_db) == []  # 闸门前置零副作用

    def test_declared_default_is_undeclared(self, brain_env):
        """自报 'default'=未声明→取派生（F3b-② 语义：不误伤不传参调用方）。"""
        c, tl_db, ent_db, _es = brain_env
        uid = _insert_ent_user(ent_db, "eve", "tenant-emit-e")
        token = _ent_token(uid, "eve")
        r = _think(c, token, {"user_input": "x", "tenant_id": "default"})
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "think"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-emit-e"

    def test_think_response_contract_unchanged(self, brain_env):
        """响应契约零变化：think 必有 intent/risk/decision 键（接派生不改响应面）。"""
        c, _tl, _ent, _es = brain_env
        r = _think(c)
        assert r.status_code == 200
        data = r.json()
        for key in ("intent", "risk", "decision"):
            assert key in data
