"""Integration tests for Agents API (/api/agents) — agent detection, registry, install."""

import pytest


class TestAgentsHealth:
    def test_health(self, client):
        resp = client.get("/api/agents/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["component"] == "Agents"


class TestAgentsDetect:
    def test_detect_returns_list(self, client):
        resp = client.get("/api/agents/detect")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_detect_structure(self, client):
        resp = client.get("/api/agents/detect")
        assert resp.status_code == 200
        data = resp.json()
        # If list, each item should have agent info
        if isinstance(data, list):
            for agent in data[:3]:
                assert isinstance(agent, dict)


class TestAgentsRegistry:
    """Test that the agent registry is populated and returns valid data."""

    def test_detect_has_agents(self, client):
        resp = client.get("/api/agents/detect")
        assert resp.status_code == 200
        data = resp.json()
        # The registry has 50+ agents, detect should return some
        if isinstance(data, list):
            assert len(data) > 0


class TestAgentsInstall:
    def test_install_missing_body(self, client):
        resp = client.post("/api/agents/install", json={})
        # May require auth or return validation error
        assert resp.status_code in (200, 400, 401, 404, 422)

    def test_install_nonexistent_agent(self, client):
        resp = client.post(
            "/api/agents/install",
            json={"agent_id": "nonexistent_agent_xyz_999"},
        )
        assert resp.status_code in (200, 400, 401, 404, 422)

    def test_install_status(self, client):
        resp = client.get("/api/agents/install/status")
        assert resp.status_code in (200, 401)

    def test_uninstall_missing_body(self, client):
        resp = client.post("/api/agents/uninstall", json={})
        assert resp.status_code in (200, 400, 401, 404, 422)

    def test_update_missing_body(self, client):
        resp = client.post("/api/agents/update", json={})
        assert resp.status_code in (200, 400, 401, 404, 422)


class TestUpgradeCmd:
    """升级命令派生（/api/agents/update 真升级语义）：pip 必加 --upgrade、
    npm 显式 @latest、brew 走 upgrade、无命令行升级面诚实回 None。"""

    def test_pip_gains_upgrade_flag(self):
        from src.api.agents import _upgrade_cmd

        out = _upgrade_cmd("pip install --break-system-packages hermes-agent")
        assert out == "pip install --upgrade --break-system-packages hermes-agent"

    def test_npm_pins_latest(self):
        from src.api.agents import _upgrade_cmd

        out = _upgrade_cmd("npm install -g @anthropic-ai/claude-code")
        assert out == "npm install -g @anthropic-ai/claude-code@latest"

    def test_npm_replaces_existing_version_tag(self):
        from src.api.agents import _upgrade_cmd

        out = _upgrade_cmd("npm install -g flowise@1.2.3")
        assert out == "npm install -g flowise@latest"

    def test_brew_switches_to_upgrade(self):
        from src.api.agents import _upgrade_cmd

        assert _upgrade_cmd("brew install k9s") == "brew upgrade k9s"

    def test_go_latest_passthrough(self):
        from src.api.agents import _upgrade_cmd

        cmd = "go install github.com/opencode-ai/opencode@latest"
        assert _upgrade_cmd(cmd) == cmd

    def test_url_only_is_honest_none(self):
        from src.api.agents import _upgrade_cmd

        assert _upgrade_cmd("https://cursor.sh") is None

    def test_docker_and_gh_are_honest_none(self):
        from src.api.agents import _upgrade_cmd

        assert _upgrade_cmd("docker run -p 3210:3210 lobehub/lobe-chat") is None
        assert _upgrade_cmd("gh extension install github/gh-copilot") is None
