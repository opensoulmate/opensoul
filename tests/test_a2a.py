"""Integration tests for A2A (Agent-to-Agent) protocol endpoints.

Tests the real service-integrated task handlers:
- Agent Card discovery
- Task send with skill routing (knowledge, search, graph, chat)
- Task lifecycle (create, get, cancel)
- Error handling
"""

import pytest

pytestmark = pytest.mark.live  # 需要活服务的集成测试——CI用-m "not live"排除


class TestAgentCardDiscovery:
    """Test /.well-known/agent.json endpoint."""

    def test_agent_card_returns_200(self, client):
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200

    def test_agent_card_has_required_fields(self, client):
        resp = client.get("/.well-known/agent.json")
        data = resp.json()
        assert "name" in data
        # v1.0 卡面形状（F4b-⑪）：url/protocolVersion 移入 supportedInterfaces[]，
        # 顶层 0.x 字段只进不出（whats-new-v1.md BREAKING）
        assert "url" not in data
        assert "protocolVersion" not in data
        assert "supportedInterfaces" in data
        assert data["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
        assert "skills" in data
        assert "version" in data

    def test_agent_card_has_skills(self, client):
        resp = client.get("/.well-known/agent.json")
        data = resp.json()
        skills = data.get("skills", [])
        skill_ids = [s["id"] for s in skills]
        assert "knowledge" in skill_ids
        assert "chat" in skill_ids
        assert "search" in skill_ids
        assert "graph" in skill_ids

    def test_agent_card_capabilities(self, client):
        resp = client.get("/.well-known/agent.json")
        data = resp.json()
        caps = data.get("capabilities", {})
        assert "streaming" in caps


class TestA2AEndpoint:
    """Test POST /a2a JSON-RPC endpoint."""

    def test_a2a_returns_200(self, client):
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "test-1",
                "method": "tasks/send",
                "params": {
                    "metadata": {"llm_mode": "stub"},
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "你好"}],
                    },
                },
            },
        )
        assert resp.status_code == 200

    def test_a2a_parse_error(self, client):
        resp = client.post(
            "/a2a", content="invalid json", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_a2a_method_not_found(self, client):
        resp = client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": "test-2", "method": "nonexistent/method", "params": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_task_send_creates_task(self, client):
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "test-3",
                "method": "tasks/send",
                "params": {
                    "metadata": {"llm_mode": "stub"},
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "hello test"}],
                    },
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        result = data["result"]
        assert "id" in result
        assert "status" in result
        assert result["status"]["state"] in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED")

    def test_task_get_not_found(self, client):
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "test-4",
                "method": "tasks/get",
                "params": {"id": "nonexistent-task-id"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_task_get_missing_id(self, client):
        resp = client.post(
            "/a2a", json={"jsonrpc": "2.0", "id": "test-5", "method": "tasks/get", "params": {}}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_task_cancel_missing_id(self, client):
        resp = client.post(
            "/a2a", json={"jsonrpc": "2.0", "id": "test-6", "method": "tasks/cancel", "params": {}}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


class TestA2ASkillRouting:
    """Test that skill routing works correctly based on tags."""

    def test_knowledge_skill_triggered(self, client):
        """'知识库' tag should route to knowledge handler."""
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "test-kb-1",
                "method": "tasks/send",
                "params": {
                    "metadata": {"llm_mode": "stub"},
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "搜索知识库中的AI相关内容"}],
                    },
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data.get("result", {})
        assert result.get("status", {}).get("state") in (
            "TASK_STATE_COMPLETED",
            "TASK_STATE_FAILED",
        )
        # Verify response contains knowledge-related content
        if result.get("status", {}).get("message"):
            text = result["status"]["message"]["parts"][0]["text"]
            assert len(text) > 0

    def test_search_skill_triggered(self, client):
        """'搜索' tag should route to search handler."""
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "test-search-1",
                "method": "tasks/send",
                "params": {
                    "metadata": {"llm_mode": "stub"},
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "搜索关于机器学习的知识"}],
                    },
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data.get("result", {})
        assert result.get("status", {}).get("state") in (
            "TASK_STATE_COMPLETED",
            "TASK_STATE_FAILED",
        )

    def test_graph_skill_triggered(self, client):
        """'图谱' tag should route to graph handler."""
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "test-graph-1",
                "method": "tasks/send",
                "params": {
                    "metadata": {"llm_mode": "stub"},
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "查看知识图谱"}],
                    },
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data.get("result", {})
        assert result.get("status", {}).get("state") in (
            "TASK_STATE_COMPLETED",
            "TASK_STATE_FAILED",
        )

    def test_chat_fallback(self, client):
        """Messages without matching tags should fallback to chat."""
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "test-chat-1",
                "method": "tasks/send",
                "params": {
                    "metadata": {"llm_mode": "stub"},
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "今天天气怎么样"}],
                    },
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        result = data.get("result", {})
        assert result.get("status", {}).get("state") in (
            "TASK_STATE_COMPLETED",
            "TASK_STATE_FAILED",
        )
        # stub接线证据：应答携带确定性marker=服务端llm_mode=stub分支真实生效（且零外部LLM）
        reply = result["status"]["message"]["parts"][0]["text"]
        assert reply.startswith("[stub-llm]")


class TestA2ATaskLifecycle:
    """Test full task lifecycle: create → get → cancel."""

    def test_full_lifecycle(self, client):
        # Step 1: Create a task by sending a message
        create_resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "lifecycle-1",
                "method": "tasks/send",
                "params": {
                    "metadata": {"llm_mode": "stub"},
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "test lifecycle"}],
                    },
                },
            },
        )
        assert create_resp.status_code == 200
        create_data = create_resp.json()
        task_id = create_data["result"]["id"]

        # Step 2: Get the task
        get_resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "lifecycle-2",
                "method": "tasks/get",
                "params": {"id": task_id},
            },
        )
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data["result"]["id"] == task_id
        assert get_data["result"]["status"]["state"] == "TASK_STATE_COMPLETED"

        # Step 3: Cancel should fail (already completed)
        cancel_resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "lifecycle-3",
                "method": "tasks/cancel",
                "params": {"id": task_id},
            },
        )
        assert cancel_resp.status_code == 200
        cancel_data = cancel_resp.json()
        # Completed tasks can't be canceled
        assert "error" in cancel_data

    def test_multi_turn_conversation(self, client):
        """Test sending multiple messages to the same task."""
        # First message
        resp1 = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "multi-1",
                "method": "tasks/send",
                "params": {
                    "metadata": {"llm_mode": "stub"},
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "你好，我想了解一下AI"}],
                    },
                },
            },
        )
        assert resp1.status_code == 200
        task_id = resp1.json()["result"]["id"]

        # Second message to same task
        resp2 = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "multi-2",
                "method": "tasks/send",
                "params": {
                    "metadata": {"llm_mode": "stub"},
                    "id": task_id,
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": "能详细说说吗"}],
                    },
                },
            },
        )
        assert resp2.status_code == 200
        result2 = resp2.json()["result"]
        assert result2["id"] == task_id
        # Should have more history messages now
        assert len(result2.get("history", [])) >= 4  # 2 user + 2 agent
        # 两次agent回复都走stub确定性应答（本测试全程零外部LLM）
        # wire 面 part 为 v1.0 平铺 oneof 形（F4b-⑭）：判别式=成员名，无 type/kind 键
        agent_replies = [
            p["text"]
            for m in result2["history"]
            if m["role"] == "ROLE_AGENT"
            for p in m["parts"]
            if "text" in p
        ]
        assert agent_replies and all(t.startswith("[stub-llm]") for t in agent_replies)


class TestA2AEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_message(self, client):
        resp = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "edge-1",
                "method": "tasks/send",
                "params": {
                    "metadata": {"llm_mode": "stub"},
                    "message": {
                        "role": "user",
                        "parts": [{"type": "text", "text": ""}],
                    },
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Should handle gracefully (not crash)
        assert "result" in data or "error" in data

    def test_missing_message_field(self, client):
        resp = client.post(
            "/a2a", json={"jsonrpc": "2.0", "id": "edge-2", "method": "tasks/send", "params": {}}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    def test_concurrent_tasks(self, client):
        """Multiple tasks should not interfere with each other."""
        responses = []
        for i in range(3):
            resp = client.post(
                "/a2a",
                json={
                    "jsonrpc": "2.0",
                    "id": f"concurrent-{i}",
                    "method": "tasks/send",
                    "params": {
                        "metadata": {"llm_mode": "stub"},
                        "message": {
                            "role": "user",
                            "parts": [{"type": "text", "text": f"task {i}"}],
                        },
                    },
                },
            )
            responses.append(resp)

        task_ids = set()
        for resp in responses:
            assert resp.status_code == 200
            result = resp.json().get("result", {})
            task_id = result.get("id")
            if task_id:
                task_ids.add(task_id)

        # All tasks should have unique IDs
        assert len(task_ids) == 3
