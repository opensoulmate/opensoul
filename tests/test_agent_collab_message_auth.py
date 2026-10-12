"""F3b-⑤ HTTP /message agent 身份轴收口测试。

修前（控制组实证）：POST /message 的 from_agent 是客户端自报——任意人可冒名注册
agent 落消息+以其名义 WS 投递（F3b-④ 留档「HTTP /message from_agent 自报（冒名）
未动」；register 签发的 agents.token 在 HTTP 面仍无消费）。

覆盖（全部 tmp 独立库 + AGENT_COLLAB_DB 环境隔离，绝不写生产 data/agent_collaboration.db）：
①claim 注册身份发消息必须持 register 签发 token（X-Agent-Token 头消费点：
  register 响应 token == agents.token 行 == /message 真放行凭证）
②冒名面收口：缺 token / 错 token / 他人真 token → 403 零副作用（不落库、不投递）
③未注册 from_agent 既有语义零变化（自报照旧、行归调用方租户——②d 契约锁死）
④token 轮换旧 token 失效；两身份轴分离（agent token 进 Authorization 槽=JWT 轴 401，
  互不顶替）；租户闸门零回归（持真 token 跨租户仍 403）

注：src.api.agent_collaboration 模块级 DB_PATH 在 import 期计算——fixture 内延迟
import + monkeypatch DB_PATH + 幂等 _init_db()，无论模块此前是否已 import 都落 tmp。
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings


def _rows(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _tok(uid):
    payload = {
        "sub": str(uid),
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _auth(uid):
    return {"Authorization": "Bearer " + _tok(uid)}


ENT_USERS_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


@pytest.fixture()
def collab_env(tmp_path, monkeypatch):
    """agent_collaboration 隔离数据面：tmp agent_collaboration.db、enterprise.db
    （NEST_DB 由 conftest autouse 隔离）。import 延迟+路径双保险，绝不碰生产库。
    uid 1=tenant-a、uid 2=tenant-b（bind_identity 派生租户需 users 真源行）。"""
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    conn = sqlite3.connect(str(tmp_path / "enterprise.db"))
    conn.execute(ENT_USERS_DDL)
    conn.execute("INSERT INTO users (username, password, tenant_id) VALUES ('ua', 'h', 'tenant-a')")
    conn.execute("INSERT INTO users (username, password, tenant_id) VALUES ('ub', 'h', 'tenant-b')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("AGENT_COLLAB_DB", str(tmp_path / "agent_collaboration.db"))
    import src.api.agent_collaboration as collab

    monkeypatch.setattr(collab, "DB_PATH", tmp_path / "agent_collaboration.db")
    collab._init_db()  # 幂等：tmp 库建表+迁移
    return collab


@pytest.fixture()
def client(collab_env):
    app = FastAPI()
    app.include_router(collab_env.router, prefix="/api/collab")
    with TestClient(app) as c:
        yield c


def _register(client, agent_id, headers=None):
    r = client.post(
        "/api/collab/register",
        json={"agent_id": agent_id, "name": agent_id},
        headers=headers or {},
    )
    assert r.status_code == 200
    return r.json()["token"]


def _msg(from_agent, to_agent="ghost-target", typ="task"):
    return {"from_agent": from_agent, "to_agent": to_agent, "type": typ, "payload": {"k": 1}}


class _Recorder:
    """WS 投递记录器：替代 _ws_connections 目标，证明拒绝=不投递（不借时序间接）。"""

    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class TestMessageAgentAuth:
    """① token 消费点 + ② 冒名面收口。"""

    def test_registered_from_with_token_delivers(self, collab_env, client):
        tok = _register(client, "m1")
        # register 响应 token == agents.token 行（凭证单一真源，Gate2 硬证）
        rows = _rows(collab_env.DB_PATH, "SELECT token FROM agents WHERE agent_id = 'm1'")
        assert rows and rows[0]["token"] == tok
        r = client.post("/api/collab/message", json=_msg("m1"), headers={"X-Agent-Token": tok})
        assert r.status_code == 200
        assert _rows(
            collab_env.DB_PATH, "SELECT from_agent FROM agent_messages WHERE from_agent = 'm1'"
        )

    def test_missing_token_rejected_zero_side_effects(self, collab_env, client):
        _register(client, "victim")
        rec = _Recorder()
        collab_env._ws_connections["ghost-target"] = rec
        try:
            r = client.post("/api/collab/message", json=_msg("victim"))
        finally:
            collab_env._ws_connections.pop("ghost-target", None)
        assert r.status_code == 403
        assert "victim" in r.json()["detail"]
        assert (
            _rows(collab_env.DB_PATH, "SELECT id FROM agent_messages WHERE from_agent = 'victim'")
            == []
        )
        assert rec.sent == []  # 拒绝=不投递

    def test_wrong_token_rejected_zero_side_effects(self, collab_env, client):
        _register(client, "victim")
        r = client.post(
            "/api/collab/message", json=_msg("victim"), headers={"X-Agent-Token": "wrong-token"}
        )
        assert r.status_code == 403
        assert (
            _rows(collab_env.DB_PATH, "SELECT id FROM agent_messages WHERE from_agent = 'victim'")
            == []
        )

    def test_impersonation_with_other_agents_real_token_rejected(self, collab_env, client):
        tok_attacker = _register(client, "attacker")
        _register(client, "victim")
        # 顶号攻击：持 attacker 自己的真 token 冒 victim（token 真实但身份不符）
        r = client.post(
            "/api/collab/message", json=_msg("victim"), headers={"X-Agent-Token": tok_attacker}
        )
        assert r.status_code == 403
        assert (
            _rows(collab_env.DB_PATH, "SELECT id FROM agent_messages WHERE from_agent = 'victim'")
            == []
        )
        # 正对照：attacker 持自己 token 发自己身份照常 200
        r2 = client.post(
            "/api/collab/message", json=_msg("attacker"), headers={"X-Agent-Token": tok_attacker}
        )
        assert r2.status_code == 200

    def test_token_rotation_invalidates_old_token(self, collab_env, client):
        tok1 = _register(client, "r1")
        tok2 = _register(client, "r1")  # 同 agent_id 重注册=token 轮换（②d 既有语义）
        assert tok1 != tok2
        r_old = client.post("/api/collab/message", json=_msg("r1"), headers={"X-Agent-Token": tok1})
        assert r_old.status_code == 403  # 旧 token 失效
        r_new = client.post("/api/collab/message", json=_msg("r1"), headers={"X-Agent-Token": tok2})
        assert r_new.status_code == 200


class TestContractUnchanged:
    """③ 未注册语义 + ④ 身份轴分离/租户闸门零回归。"""

    def test_unregistered_from_agent_contract_unchanged(self, collab_env, client):
        # 未注册 from_agent 无注册身份可顶：自报照旧（②d 契约锁死）
        r = client.post("/api/collab/message", json=_msg("ghost-1"))
        assert r.status_code == 200
        rows = _rows(
            collab_env.DB_PATH, "SELECT tenant_id FROM agent_messages WHERE from_agent = 'ghost-1'"
        )
        assert rows and rows[0]["tenant_id"] == "default"

    def test_registered_to_agent_needs_no_token(self, collab_env, client):
        # to_agent 是收方非身份 claim：注册收方不需 token（只收 from 轴）
        tok = _register(client, "m2")
        _register(client, "target")
        r = client.post(
            "/api/collab/message",
            json=_msg("m2", to_agent="target"),
            headers={"X-Agent-Token": tok},
        )
        assert r.status_code == 200

    def test_agent_token_in_authorization_slot_is_jwt_axis(self, collab_env, client):
        # 两身份轴互不顶替：agent token 进 Authorization 槽=坏 JWT 401，不是 agent 身份
        tok = _register(client, "m3")
        r = client.post(
            "/api/collab/message",
            json=_msg("m3"),
            headers={"Authorization": "Bearer " + tok},
        )
        assert r.status_code == 401
        assert (
            _rows(collab_env.DB_PATH, "SELECT id FROM agent_messages WHERE from_agent = 'm3'") == []
        )

    def test_cross_tenant_with_valid_token_still_denied(self, collab_env, client):
        # 身份证明（token）≠授权（租户闸门）：真 token 跨租户仍 403，闸门零回归
        tok = _register(client, "agent-a1", headers=_auth(1))
        r = client.post(
            "/api/collab/message",
            json=_msg("agent-a1"),
            headers={**_auth(2), "X-Agent-Token": tok},
        )
        assert r.status_code == 403
        assert (
            _rows(collab_env.DB_PATH, "SELECT id FROM agent_messages WHERE from_agent = 'agent-a1'")
            == []
        )
        # 同租户正对照
        r2 = client.post(
            "/api/collab/message",
            json=_msg("agent-a1"),
            headers={**_auth(1), "X-Agent-Token": tok},
        )
        assert r2.status_code == 200
