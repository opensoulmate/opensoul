"""ws_chat connected 帧 user_id 载荷收口（DEC-⑰：dict repr 怪形→纯用户 id）。

修前（live 修前实证）：connected 帧 user_id=str(decode_token(token)) =
"{'user_id': UUID('...'), 'role': 'user'}" 的 Python dict repr——载荷不是纯
用户 id，客户端不可直接当 id 用；且与生态参照形（acp-proxy ws_chat.py connected
帧 user_id=纯 UUID 字符串）漂移。

收口：_connected_user_id 单一判法（user_id 键→str() 纯 id；缺失/None→"" 空串
如实禁 fabricate dict repr）+connected 发送点真消费。

全部 in-process 真 WS 往返（TestClient），不依赖 live :8090。
"""

import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.ws_chat as ws_chat_api
from src.services.auth import create_access_token, decode_token
from src.services.tenant_binding import BoundIdentity, users_db_path

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, "
    "is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)


@pytest.fixture(autouse=True)
def _identity_env(monkeypatch, tmp_path):
    """DEC-⑭-④ 判口换 bind_identity 后握手要求真实活跃身份行：身份真源归 tmp
    （USERS_DB/ENTERPRISE_DB），绝不写生产库。"""
    users_db = tmp_path / "users.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()


def _seed_user(uid):
    with sqlite3.connect(users_db_path()) as c:
        c.execute(
            "INSERT OR REPLACE INTO users (id, username, is_active, tenant_id) "
            "VALUES (?, ?, 1, 'default')",
            (str(uid), f"user-{uid}"),
        )
        c.commit()
    return str(uid)


def _tok(uid) -> str:
    _seed_user(uid)
    return create_access_token(uid)


def _ws_env():
    app = FastAPI()
    app.include_router(ws_chat_api.router, prefix="/api/ws")
    return TestClient(app)


class TestConnectedUserIdPure:
    """_connected_user_id 纯函数：载荷单一判法（DEC-⑭-④ 判口换 bind_identity 后
    输入形=BoundIdentity；断言意图=DEC-⑰ 契约逐字保留：纯 id/role 不外泄/缺失
    空串禁 fabricate dict repr）。"""

    def test_uuid_value_pure_str(self):
        uid = uuid4()
        ident = BoundIdentity("default", str(uid), True, "token:user")
        assert ws_chat_api._connected_user_id(ident) == str(uid)

    def test_str_value_as_is(self):
        ident = BoundIdentity("default", "user-abc", True, "token:user")
        assert ws_chat_api._connected_user_id(ident) == "user-abc"

    def test_role_not_in_payload(self):
        ident = BoundIdentity("default", str(uuid4()), True, "token:enterprise")
        val = ws_chat_api._connected_user_id(ident)
        assert "role" not in val
        assert "admin" not in val

    def test_missing_key_empty_not_dict_repr(self):
        val = ws_chat_api._connected_user_id(SimpleNamespace(role="user"))
        assert val == ""
        assert "{" not in val  # 禁 fabricate dict repr 怪形

    def test_none_value_empty(self):
        assert ws_chat_api._connected_user_id(SimpleNamespace(user_id=None)) == ""

    def test_non_identity_input_empty(self):
        assert ws_chat_api._connected_user_id(None) == ""
        assert ws_chat_api._connected_user_id("{'user_id': 1}") == ""
        assert ws_chat_api._connected_user_id({"user_id": uuid4()}) == ""


class TestConnectedFrameApi:
    """API 全链：真 JWT 握手 → connected 帧纯 id。"""

    def test_connected_frame_pure_user_id(self):
        uid = uuid4()
        tok = _tok(uid)
        payload = decode_token(tok)
        assert payload is not None
        pure = str(payload["user_id"])
        with _ws_env() as c:
            with c.websocket_connect(f"/api/ws/chat?token={tok}") as ws:
                frame = ws.receive_json()
                assert frame["type"] == "connected"
                assert frame["user_id"] == pure
                # 硬反证：禁 dict repr 怪形（修前 str(dict) 含 'user_id'/'UUID(' 字样）
                assert "user_id" not in frame["user_id"]
                assert "UUID(" not in frame["user_id"]
                assert "role" not in frame["user_id"]

    def test_connected_frame_keys_exactly_two(self):
        uid = uuid4()
        tok = _tok(uid)
        with _ws_env() as c:
            with c.websocket_connect(f"/api/ws/chat?token={tok}") as ws:
                frame = ws.receive_json()
                assert set(frame.keys()) == {"type", "user_id"}

    def test_standard_token_id_matches_user_row_contract(self):
        """标准 token sub=UUID → 载荷=users.id 纯字符串（身份可直接当 id 用）。"""
        uid = uuid4()
        tok = _tok(uid)
        with _ws_env() as c:
            with c.websocket_connect(f"/api/ws/chat?token={tok}") as ws:
                frame = ws.receive_json()
                assert frame["user_id"] == str(uid)


class TestSingleSourceCensus:
    """单一判法静态普查：connected 发送点禁手抄 str(user)。"""

    def test_send_point_uses_helper(self):
        src = open(ws_chat_api.__file__, encoding="utf-8").read()
        # 修前形态：str(user) 直接进载荷——禁复活
        assert '"user_id": str(' not in src
        assert '"user_id": _connected_user_id(identity)' in src

    def test_helper_single_definition(self):
        src = open(ws_chat_api.__file__, encoding="utf-8").read()
        assert src.count("def _connected_user_id") == 1
