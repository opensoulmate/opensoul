"""MCP会话级工具隔离 + 发布侧auth + 消费侧白名单 测试。

两层：
1. TestSessionToolGate / TestCheckToolScope —— 纯离线（tmp SQLite），零网络。
2. TestMcpSessionEndpoints —— live API（conftest client → :8090），
   验证新端点在运行进程中注册并真实执行gate逻辑；自带teardown清理（测试卫生）。
"""

import sqlite3

import pytest

from src.mcp.session_grants import (
    SessionToolGate,
    check_tool_scope,
    published_tool_scope,
)


@pytest.fixture()
def gate(tmp_path):
    return SessionToolGate(db_path=str(tmp_path / "grants.db"))


class TestSessionGrants:
    def test_grant_and_allow(self, gate):
        gate.grant("sess-1", "mcp-github", "*", granted_by="admin")
        assert gate.is_tool_allowed("sess-1", "mcp-github", "create_pr") is True

    def test_tool_level_granularity(self, gate):
        gate.grant("sess-1", "mcp-github", "list_issues")
        assert gate.is_tool_allowed("sess-1", "mcp-github", "list_issues") is True
        # 同server的其他工具不可用（tool粒度隔离）
        assert gate.is_tool_allowed("sess-1", "mcp-github", "create_pr") is False

    def test_fail_closed_default_deny(self, gate):
        # 零grant = 零可见（Letta memory-confinement fail-closed）
        assert gate.is_tool_allowed("sess-unknown", "mcp-github", "list_issues") is False

    def test_cross_session_isolation(self, gate):
        gate.grant("sess-1", "mcp-github", "*")
        # 另一个会话不受sess-1授权影响（会话级隔离）
        assert gate.is_tool_allowed("sess-2", "mcp-github", "list_issues") is False

    def test_revoke(self, gate):
        gate.grant("sess-1", "mcp-github", "*")
        assert gate.revoke("sess-1", "mcp-github", "*") is True
        assert gate.is_tool_allowed("sess-1", "mcp-github", "list_issues") is False
        assert gate.revoke("sess-1", "mcp-github", "*") is False  # 二次revoke诚实False

    def test_clear_session(self, gate):
        gate.grant("sess-1", "a", "*")
        gate.grant("sess-1", "b", "*")
        assert gate.clear_session("sess-1") == 2
        assert gate.list_grants("sess-1") == []

    def test_empty_ids_rejected(self, gate):
        with pytest.raises(ValueError):
            gate.grant("", "mcp-github", "*")
        with pytest.raises(ValueError):
            gate.grant("s", "", "*")
        with pytest.raises(ValueError):
            gate.grant("s", "mcp-github", "")

    def test_allowed_server_ids(self, gate):
        gate.grant("sess-1", "a", "*")
        gate.grant("sess-1", "b", "t1")
        assert gate.allowed_server_ids("sess-1") == {"a", "b"}

    def test_list_grants_all(self, gate):
        gate.grant("sess-1", "a", "*")
        gate.grant("sess-2", "b", "*")
        assert len(gate.list_grants()) == 2
        assert len(gate.list_grants("sess-1")) == 1

    def test_stats(self, gate):
        gate.grant("sess-1", "a", "*")
        gate.grant("sess-1", "b", "*")
        gate.grant("sess-2", "a", "*")
        stats = gate.stats()
        assert stats["session_grants"] == 3
        assert stats["isolated_sessions"] == 2
        assert stats["active_consumers"] == 0


class TestConsumerTokens:
    def test_issue_and_authenticate(self, gate):
        token, record = gate.issue_consumer("agent-1", ["mcp-github"])
        assert token.startswith("mcp_")
        assert record["scopes"] == ["mcp-github"]
        consumer = gate.authenticate(token)
        assert consumer is not None
        assert consumer["consumer_id"] == "agent-1"

    def test_token_stored_hashed_only(self, gate):
        """落库仅sha256摘要——泄库不泄token（GitHub PAT语义）。"""
        token, _ = gate.issue_consumer("agent-1", ["*"])
        db = sqlite3.connect(gate._db.execute("PRAGMA database_list").fetchone()[2])
        try:
            rows = db.execute("SELECT token_hash FROM mcp_consumers").fetchall()
        finally:
            db.close()
        assert len(rows) == 1
        assert token not in rows[0][0]  # 明文token绝不落库
        assert len(rows[0][0]) == 64  # sha256 hexdigest

    def test_authenticate_rejects(self, gate):
        gate.issue_consumer("agent-1", ["*"])
        assert gate.authenticate(None) is None
        assert gate.authenticate("") is None
        assert gate.authenticate("mcp_bogus") is None

    def test_revoke_consumer_invalidates_token(self, gate):
        token, _ = gate.issue_consumer("agent-1", ["*"])
        assert gate.revoke_consumer("agent-1") is True
        assert gate.authenticate(token) is None  # 已签发token立即失效
        assert gate.revoke_consumer("agent-1") is False

    def test_list_consumers_no_token_echo(self, gate):
        token, _ = gate.issue_consumer("agent-1", ["*"])
        consumers = gate.list_consumers()
        assert consumers[0]["consumer_id"] == "agent-1"
        serialized = str(consumers)
        assert token not in serialized
        assert "token_hash" not in consumers[0]

    def test_scope_wildcard(self, gate):
        token, _ = gate.issue_consumer("agent-1", ["*"])
        consumer = gate.authenticate(token)
        assert SessionToolGate.consumer_may_use(consumer, "anything") is True

    def test_scope_exact_match_only(self, gate):
        token, _ = gate.issue_consumer("agent-1", ["mcp-github"])
        consumer = gate.authenticate(token)
        assert SessionToolGate.consumer_may_use(consumer, "mcp-github") is True
        assert SessionToolGate.consumer_may_use(consumer, "mcp-memory") is False

    def test_scopes_validation(self, gate):
        with pytest.raises(ValueError):
            gate.issue_consumer("", ["*"])
        with pytest.raises(ValueError):
            gate.issue_consumer("agent-1", "not-a-list")
        with pytest.raises(ValueError):
            gate.issue_consumer("agent-1", [1, 2])

    def test_reissue_rotates_token(self, gate):
        token1, _ = gate.issue_consumer("agent-1", ["*"])
        token2, _ = gate.issue_consumer("agent-1", ["*"])
        assert token1 != token2
        assert gate.authenticate(token1) is None  # 旧token被替换失效
        assert gate.authenticate(token2) is not None

    def test_stats_counts_active_consumers(self, gate):
        gate.issue_consumer("agent-1", ["*"])
        gate.issue_consumer("agent-2", ["*"])
        gate.revoke_consumer("agent-2")
        assert gate.stats()["active_consumers"] == 1


class TestCheckToolScope:
    def test_published_scope_format(self):
        assert published_tool_scope("recall") == "tool:recall"

    def test_missing_token_denied(self, gate):
        ok, reason = check_tool_scope(None, "tool:recall", gate=gate)
        assert ok is False
        assert "missing token" in reason

    def test_valid_token_scoped(self, gate):
        token, _ = gate.issue_consumer("agent-1", ["tool:recall"])
        ok, reason = check_tool_scope(token, "tool:recall", gate=gate)
        assert ok is True
        assert reason == "ok"
        # scope未含的工具拒绝（发布侧白名单）
        ok2, reason2 = check_tool_scope(token, "tool:remember", gate=gate)
        assert ok2 is False
        assert "tool:remember" in reason2

    def test_wildcard_token_all_tools(self, gate):
        token, _ = gate.issue_consumer("agent-1", ["*"])
        for tool in ("remember", "recall", "ask", "search", "list_memories"):
            ok, _ = check_tool_scope(token, published_tool_scope(tool), gate=gate)
            assert ok is True

    def test_revoked_token_denied(self, gate):
        token, _ = gate.issue_consumer("agent-1", ["*"])
        gate.revoke_consumer("agent-1")
        ok, reason = check_tool_scope(token, "tool:recall", gate=gate)
        assert ok is False
        assert "invalid or revoked" in reason

    def test_server_tools_all_guarded(self):
        """静态完整性：src/mcp/server.py 每个发布工具都过 _require_auth（无漏网工具）。"""
        import re
        from pathlib import Path

        src = (Path(__file__).parent.parent / "src" / "mcp" / "server.py").read_text()
        tool_defs = re.findall(r"async def (\w+)\(", src)
        guards = re.findall(r'_require_auth\(auth_token, "(\w+)"\)', src)
        published = [t for t in tool_defs if t != "main"]
        assert published, "expected published tools in server.py"
        assert sorted(guards) == sorted(published), (
            f"unguarded published tools: {set(published) - set(guards)}"
        )


# ── live API（需服务重启后运行；自带teardown零残留） ──────────


class TestMcpSessionEndpoints:
    SESSION = "cron_gate_probe"

    def _cleanup(self, client, server_id=None, consumer_id="cron_gate_probe"):
        client.delete(f"/api/mcp/sessions/{self.SESSION}/grants/{server_id or 'any'}")
        client.delete(f"/api/mcp/consumers/{consumer_id}")
        # 兜底：清掉该session全部grant
        grants = client.get(f"/api/mcp/sessions/{self.SESSION}/grants").json().get("grants", [])
        for g in grants:
            client.delete(
                f"/api/mcp/sessions/{self.SESSION}/grants/{g['server_id']}",
                params={"tool_name": g["tool_name"]},
            )

    def test_full_cycle(self, client):
        server = client.post(
            "/api/mcp/servers",
            json={
                "name": "gate-test-server",
                "url": "stdio://gate-test",
                "tools": [
                    {"name": "alpha", "description": "a"},
                    {"name": "beta", "description": "b"},
                ],
            },
        ).json()
        server_id = server["id"]
        token = None
        try:
            # ① 签发consumer（scope只含本server）
            resp = client.post(
                "/api/mcp/consumers",
                json={"consumer_id": "cron_gate_probe", "scopes": [server_id]},
            )
            assert resp.status_code == 200
            token = resp.json()["token"]
            assert token.startswith("mcp_")

            # ② 发布侧auth：无token → 401
            r = client.get(f"/api/mcp/sessions/{self.SESSION}/tools")
            assert r.status_code == 401
            r = client.get(
                f"/api/mcp/sessions/{self.SESSION}/tools", headers={"X-MCP-Token": "mcp_bogus"}
            )
            assert r.status_code == 401

            # ③ fail-closed：有token但零grant → 零可见
            r = client.get(
                f"/api/mcp/sessions/{self.SESSION}/tools", headers={"X-MCP-Token": token}
            )
            assert r.status_code == 200
            assert r.json()["total"] == 0

            # ④ check拒绝带reason
            r = client.post(
                f"/api/mcp/sessions/{self.SESSION}/tools/check",
                json={"server_id": server_id, "tool_name": "alpha"},
                headers={"X-MCP-Token": token},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["allowed"] is False
            assert "fail-closed" in body["reason"]

            # ⑤ 授权tool粒度：只给alpha
            r = client.post(
                f"/api/mcp/sessions/{self.SESSION}/grants",
                json={"server_id": server_id, "tool_name": "alpha", "granted_by": "cron"},
            )
            assert r.status_code == 200

            # ⑥ 隔离endpoint只回alpha
            r = client.get(
                f"/api/mcp/sessions/{self.SESSION}/tools", headers={"X-MCP-Token": token}
            )
            body = r.json()
            assert body["total"] == 1
            assert body["tools"][0]["name"] == "alpha"
            assert body["granted_servers"] == [server_id]

            # ⑦ check：alpha放行 / beta拒绝
            r = client.post(
                f"/api/mcp/sessions/{self.SESSION}/tools/check",
                json={"server_id": server_id, "tool_name": "alpha"},
                headers={"X-MCP-Token": token},
            )
            assert r.json()["allowed"] is True
            r = client.post(
                f"/api/mcp/sessions/{self.SESSION}/tools/check",
                json={"server_id": server_id, "tool_name": "beta"},
                headers={"X-MCP-Token": token},
            )
            assert r.json()["allowed"] is False

            # ⑧ scope白名单：其他server被scope拦截（403）
            r = client.post(
                f"/api/mcp/sessions/{self.SESSION}/tools/check",
                json={"server_id": "mcp-memory", "tool_name": "search_nodes"},
                headers={"X-MCP-Token": token},
            )
            assert r.status_code == 403

            # ⑨ 收回授权 → 立即拒绝
            r = client.delete(
                f"/api/mcp/sessions/{self.SESSION}/grants/{server_id}",
                params={"tool_name": "alpha"},
            )
            assert r.status_code == 200
            r = client.post(
                f"/api/mcp/sessions/{self.SESSION}/tools/check",
                json={"server_id": server_id, "tool_name": "alpha"},
                headers={"X-MCP-Token": token},
            )
            assert r.json()["allowed"] is False

            # ⑩ 吊销consumer → 401
            r = client.delete("/api/mcp/consumers/cron_gate_probe")
            assert r.status_code == 200
            r = client.get(
                f"/api/mcp/sessions/{self.SESSION}/tools", headers={"X-MCP-Token": token}
            )
            assert r.status_code == 401

            # ⑪ consumer列表不回显token
            r = client.get("/api/mcp/consumers")
            assert token not in r.text

            # ⑫ stats携带gate观测键
            r = client.get("/api/mcp/stats")
            assert "session_grants" in r.json()
            assert "active_consumers" in r.json()
        finally:
            self._cleanup(client, server_id=server_id)
            client.delete(f"/api/mcp/servers/{server_id}")

    def test_grant_unknown_fields_400(self, client):
        r = client.post(
            f"/api/mcp/sessions/{self.SESSION}/grants",
            json={"server_id": "", "tool_name": "*"},
        )
        assert r.status_code == 400
        self._cleanup(client)

    def test_revoke_missing_grant_404(self, client):
        r = client.delete(
            f"/api/mcp/sessions/{self.SESSION}/grants/no-such-server", params={"tool_name": "*"}
        )
        assert r.status_code == 404

    def test_revoke_missing_consumer_404(self, client):
        r = client.delete("/api/mcp/consumers/no-such-consumer")
        assert r.status_code == 404

    def test_legacy_admin_surface_unchanged(self, client):
        """既有管理面（/tools无token）不破坏——升级增量不回归。"""
        r = client.get("/api/mcp/tools")
        assert r.status_code == 200
        assert "tools" in r.json()
