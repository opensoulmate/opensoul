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
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.ws_chat as ws_chat_api
from src.services.auth import create_access_token, decode_token


def _ws_env():
    app = FastAPI()
    app.include_router(ws_chat_api.router, prefix="/api/ws")
    return TestClient(app)


class TestConnectedUserIdPure:
    """_connected_user_id 纯函数：载荷单一判法。"""

    def test_uuid_value_pure_str(self):
        uid = uuid4()
        assert ws_chat_api._connected_user_id({"user_id": uid, "role": "user"}) == str(uid)

    def test_str_value_as_is(self):
        assert ws_chat_api._connected_user_id({"user_id": "user-abc"}) == "user-abc"

    def test_role_not_in_payload(self):
        val = ws_chat_api._connected_user_id({"user_id": uuid4(), "role": "admin"})
        assert "role" not in val
        assert "admin" not in val

    def test_missing_key_empty_not_dict_repr(self):
        val = ws_chat_api._connected_user_id({"role": "user"})
        assert val == ""
        assert "{" not in val  # 禁 fabricate dict repr 怪形

    def test_none_value_empty(self):
        assert ws_chat_api._connected_user_id({"user_id": None}) == ""

    def test_non_dict_input_empty(self):
        assert ws_chat_api._connected_user_id(None) == ""
        assert ws_chat_api._connected_user_id("{'user_id': 1}") == ""


class TestConnectedFrameApi:
    """API 全链：真 JWT 握手 → connected 帧纯 id。"""

    def test_connected_frame_pure_user_id(self):
        tok = create_access_token(uuid4())
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
        tok = create_access_token(uuid4())
        with _ws_env() as c:
            with c.websocket_connect(f"/api/ws/chat?token={tok}") as ws:
                frame = ws.receive_json()
                assert set(frame.keys()) == {"type", "user_id"}

    def test_standard_token_id_matches_user_row_contract(self):
        """标准 token sub=UUID → 载荷=该 UUID 纯字符串（身份可直接当 id 用）。"""
        uid = uuid4()
        tok = create_access_token(uid)
        with _ws_env() as c:
            with c.websocket_connect(f"/api/ws/chat?token={tok}") as ws:
                frame = ws.receive_json()
                assert frame["user_id"] == str(uid)


class TestSingleSourceCensus:
    """单一判法静态普查：connected 发送点禁手抄 str(user)。"""

    def test_send_point_uses_helper(self):
        src = open(ws_chat_api.__file__, encoding="utf-8").read()
        # 修前形态：str(user) 直接进载荷——禁复活
        assert '"user_id": str(user)' not in src
        assert '"user_id": _connected_user_id(user)' in src

    def test_helper_single_definition(self):
        src = open(ws_chat_api.__file__, encoding="utf-8").read()
        assert src.count("def _connected_user_id") == 1
