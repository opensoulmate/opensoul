"""H1-②b-⑤ clear_session 授权清理面——会话清理真消费 + 出口端点。

修前（H1-② 定性「clear_session 授权行永不清」）：
- SessionToolGate.clear_session 全仓零生产消费（wired-but-not-functional）——
  mcp_session_grants 写入/校验活，但会话结束后授权行永不清。
- 撤一个会话的全部授权只能逐 (server, tool) revoke 循环
  （test_mcp_grants._cleanup 的「兜底：清掉该session全部grant」循环即现状实证）。

本文件覆盖：
- 出口面：DELETE /api/mcp/sessions/{session_id}/grants 整批清理（幂等，
  无授权行 = cleared 0 如实计数非 404——批量清理无单资源身份）
- 生命周期接线面：DELETE /api/hippo/sessions/{session_id} 会话删除 =
  该会话全部工具授权随之清理（fail-closed：会话没了授权不得残留）；
  旁路纪律——清理故障不吃删除主路径
- 不变面：他会话授权不动、404 契约零副作用、删除响应契约逐键不变
"""

import pytest

from src.mcp.session_grants import SessionToolGate


@pytest.fixture()
def gate(tmp_path):
    return SessionToolGate(db_path=str(tmp_path / "grants.db"))


@pytest.fixture()
def mcp_client(gate, monkeypatch):
    """mcp 路由进程内测试面：真实 router/handler，gate 换 tmp 实例（绝不写
    生产 mcp.db——src.api.mcp import 单例碰生产目录为既有先例边界，本 fixture
    把消费面真源换 tmp）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.mcp as mcp_api

    monkeypatch.setattr(mcp_api, "gate", gate)
    app = FastAPI()
    app.include_router(mcp_api.router, prefix="/api/mcp")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def hippo_env(will_client, gate, monkeypatch):
    """hippo 路由进程内测试面（test_session_touch_wiring env 同款路线）：
    真实 router/handler，sessions 换全新实例，mcp 授权清理面接 tmp gate。"""
    import src.api.hippo as hippo_api
    from src.hippo.session import SessionManager

    monkeypatch.setattr(hippo_api, "sessions", SessionManager())
    monkeypatch.setattr(hippo_api, "default_gate", lambda: gate, raising=False)
    return will_client, hippo_api


class TestClearSessionEndpoint:
    """DELETE /api/mcp/sessions/{session_id}/grants 出口面（clear_session 首个真消费）。"""

    def test_clear_endpoint_clears_all_grants_of_session(self, mcp_client, gate):
        gate.grant("sess-a", "mcp-github", "*")
        gate.grant("sess-a", "mcp-github", "list_issues")
        gate.grant("sess-a", "mcp-memory", "*")
        gate.grant("sess-b", "mcp-github", "*")

        resp = mcp_client.delete("/api/mcp/sessions/sess-a/grants")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"session_id": "sess-a", "cleared": 3}  # 恰 2 键如实计数

        assert gate.list_grants("sess-a") == []  # 本会话全部授权行清除
        assert len(gate.list_grants("sess-b")) == 1  # 他会话授权不动
        assert gate.is_tool_allowed("sess-a", "mcp-github", "list_issues") is False

    def test_clear_endpoint_idempotent_zero(self, mcp_client):
        """幂等清理：无授权行 = cleared 0 如实计数（批量清理无单资源身份，非 404）。"""
        resp = mcp_client.delete("/api/mcp/sessions/no-such-sess/grants")
        assert resp.status_code == 200
        assert resp.json() == {"session_id": "no-such-sess", "cleared": 0}

    def test_clear_endpoint_twice_second_zero(self, mcp_client, gate):
        gate.grant("sess-a", "mcp-github", "*")
        first = mcp_client.delete("/api/mcp/sessions/sess-a/grants").json()
        second = mcp_client.delete("/api/mcp/sessions/sess-a/grants").json()
        assert first["cleared"] == 1
        assert second["cleared"] == 0  # 二次清理如实 0，禁虚报

    def test_existing_revoke_face_unchanged(self, mcp_client, gate):
        """不变面：逐 (server, tool) revoke 契约零变化（404 语义照旧）。"""
        gate.grant("sess-a", "mcp-github", "*")
        resp = mcp_client.delete("/api/mcp/sessions/sess-a/grants/mcp-github")
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True
        resp2 = mcp_client.delete("/api/mcp/sessions/sess-a/grants/mcp-github")
        assert resp2.status_code == 404


class TestSessionDeleteWiring:
    """会话删除 → clear_session 生命周期接线（授权行永不清收口）。"""

    def test_delete_session_clears_mcp_grants(self, hippo_env, gate):
        client, _ = hippo_env
        sid = client.post("/api/hippo/sessions", json={"user_id": "w"}).json()["session_id"]
        gate.grant(sid, "mcp-github", "*")
        gate.grant(sid, "mcp-memory", "recall")
        gate.grant("other-sess", "mcp-github", "*")

        resp = client.delete(f"/api/hippo/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}  # 响应契约逐键不变

        assert gate.list_grants(sid) == []  # 会话删除=授权行随之清除（修前永不清）
        assert len(gate.list_grants("other-sess")) == 1  # 他会话授权不动

    def test_delete_missing_session_404_no_side_effect(self, hippo_env, gate):
        client, _ = hippo_env
        gate.grant("ghost-sess", "mcp-github", "*")
        resp = client.delete("/api/hippo/sessions/ghost-sess")
        assert resp.status_code == 404  # 缺行 404 契约不变
        assert len(gate.list_grants("ghost-sess")) == 1  # 零副作用（清理不越权执行）

    def test_bridge_failure_does_not_eat_delete(self, hippo_env, gate, monkeypatch):
        """旁路纪律（G1-⑤ 同族）：清理故障可见但绝不吃掉删除主路径。

        spy 直证清理被真调用（不是只看删除成功——修前删除也成功但从不清理）。
        """
        client, _ = hippo_env
        sid = client.post("/api/hippo/sessions", json={"user_id": "w"}).json()["session_id"]
        gate.grant(sid, "mcp-github", "*")

        calls = []

        def _boom(_session_id):
            calls.append(_session_id)
            raise RuntimeError("gate down")

        monkeypatch.setattr(gate, "clear_session", _boom)
        resp = client.delete(f"/api/hippo/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}  # 删除主路径存活
        assert calls == [sid]  # 清理真被调用（修前零消费=空列表）

    def test_delete_then_regrant_isolated(self, hippo_env, gate):
        """删除清理后同 id 重新授权=全新授权面（不被旧清理误伤）。"""
        client, _ = hippo_env
        sid = client.post("/api/hippo/sessions", json={"user_id": "w"}).json()["session_id"]
        gate.grant(sid, "mcp-github", "*")
        client.delete(f"/api/hippo/sessions/{sid}")
        gate.grant(sid, "mcp-memory", "*")
        assert [g["server_id"] for g in gate.list_grants(sid)] == ["mcp-memory"]


class TestStaticWiring:
    def test_clear_session_first_real_consumers(self):
        """静态接线普查：clear_session 修前全仓零消费 → 两个真消费点在场。"""
        from pathlib import Path

        root = Path(__file__).parent.parent
        mcp_src = (root / "src" / "api" / "mcp.py").read_text()
        hippo_src = (root / "src" / "api" / "hippo.py").read_text()
        assert "gate.clear_session(session_id)" in mcp_src  # 出口端点消费
        assert "default_gate().clear_session(session_id)" in hippo_src  # 生命周期接线消费
        assert "_clear_mcp_grants(session_id)" in hippo_src  # delete 真调用桥
