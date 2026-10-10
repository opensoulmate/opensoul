"""B3 探测即注册：/api/agents/detect 与 acp-proxy /agents/probe 打通。

测试面=真实 router 代码 + tmp 独立数据面（gene_client 同款路线），
acp-proxy 侧（握手/会话/注册表）打桩在 service 函数上——被测的是 opensoul 的
注册语义本身：探测即注册、未注册不进列表、检测成功=必能开会话、禁冒名兜底、
失败留痕、fail-closed。
"""

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.agents as agents_api
import src.services.agent_registry as reg


@pytest.fixture()
def probe_client(tmp_path, monkeypatch):
    """真实 agents router + tmp agent_registry.db，绝不写生产 data/agent_registry.db。"""
    monkeypatch.setenv("AGENT_REGISTRY_DB", str(tmp_path / "agent_registry.db"))
    app = FastAPI()
    app.include_router(agents_api.router, prefix="/api/agents")
    with TestClient(app) as c:
        yield c


def _patch_proxy(
    monkeypatch,
    *,
    known=("soulmate", "hermes"),
    handshake_ok=True,
    session_ok=True,
    handshake_detail="ACP handshake OK",
    session_detail="session/new OK",
):
    async def fake_known():
        return {a: {"id": a, "cmd": [a, "acp"], "installed": True} for a in known}

    async def fake_handshake(agent_id, cmd=None):
        return {
            "ok": handshake_ok,
            "detail": handshake_detail,
            "cmd": list(cmd or []),
        }

    async def fake_session(agent_id, timeout=90.0):
        return {
            "ok": session_ok,
            "session_id": "om-test-1" if session_ok else "",
            "detail": session_detail,
        }

    monkeypatch.setattr(reg, "known_agents", fake_known)
    monkeypatch.setattr(reg, "handshake_probe", fake_handshake)
    monkeypatch.setattr(reg, "session_probe", fake_session)


def _db_rows(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "agent_registry.db"))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM acp_agent_registry")]
    finally:
        conn.close()


class TestProbeRegisters:
    def test_probe_success_registers(self, probe_client, tmp_path, monkeypatch):
        """探测即注册：L1握手+L2会话全过 → registered=1，进列表。"""
        _patch_proxy(monkeypatch)
        resp = probe_client.post("/api/agents/probe", json={"agent_id": "soulmate"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["registered"] is True
        assert body["session"]["session_id"] == "om-test-1"
        # 列表=一条真源
        lst = probe_client.get("/api/agents/registry").json()
        assert lst["total"] == 1
        assert lst["agents"][0]["agent_id"] == "soulmate"
        assert lst["agents"][0]["registered"] is True
        # DB回读取证
        rows = _db_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["registered"] == 1 and rows[0]["session_ready"] == 1
        assert rows[0]["session_id"] == "om-test-1"
        assert rows[0]["probe_count"] == 1

    def test_unregistered_not_listed(self, probe_client, tmp_path, monkeypatch):
        """未注册不进列表：握手失败 → 列表为空，但失败留痕。"""
        _patch_proxy(monkeypatch, handshake_ok=False, handshake_detail="spawn failed")
        resp = probe_client.post("/api/agents/probe", json={"agent_id": "hermes"})
        body = resp.json()
        assert body["ok"] is False and body["registered"] is False
        lst = probe_client.get("/api/agents/registry").json()
        assert lst["total"] == 0
        rows = _db_rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["registered"] == 0
        assert "spawn failed" in rows[0]["probe_detail"]

    def test_handshake_ok_session_fail_not_registered(self, probe_client, tmp_path, monkeypatch):
        """检测成功=必能开会话：只过L1不过L2 → 不算检测成功、不注册。"""
        _patch_proxy(monkeypatch, session_ok=False, session_detail="session/new未返回sessionId")
        body = probe_client.post("/api/agents/probe", json={"agent_id": "soulmate"}).json()
        assert body["ok"] is False
        assert body["registered"] is False
        assert body["probe"]["ok"] is True  # L1过了
        assert body["session"]["ok"] is False
        assert probe_client.get("/api/agents/registry").json()["total"] == 0
        rows = _db_rows(tmp_path)
        assert rows[0]["registered"] == 0 and rows[0]["session_ready"] == 0
        assert "L2会话" in rows[0]["probe_detail"]


class TestImpersonationGuard:
    def test_unknown_agent_rejected_without_cmd(self, probe_client, monkeypatch):
        """禁冒名兜底：不在 acp-proxy 注册表且无cmd → 拒绝探测。"""
        _patch_proxy(monkeypatch, known=("soulmate",))
        body = probe_client.post("/api/agents/probe", json={"agent_id": "ghost-agent"}).json()
        assert body["ok"] is False
        assert "禁冒名兜底" in body["probe"]["detail"]
        assert probe_client.get("/api/agents/registry").json()["total"] == 0

    def test_unknown_agent_with_cmd_cannot_register(self, probe_client, tmp_path, monkeypatch):
        """未注册agent可显式cmd验握手，但ws侧会冒名兜底 → 会话不可证，永不注册。"""
        _patch_proxy(monkeypatch, known=("soulmate",))
        body = probe_client.post(
            "/api/agents/probe",
            json={"agent_id": "ghost-agent", "cmd": ["ghost", "acp"]},
        ).json()
        assert body["probe"]["ok"] is True  # L1握手可以验
        assert body["ok"] is False  # 但不构成检测成功
        assert body["registered"] is False
        assert "禁冒名兜底" in body["session"]["detail"]
        rows = _db_rows(tmp_path)
        assert rows[0]["registered"] == 0


class TestHandshakeOnlyMode:
    def test_handshake_only_keeps_registration(self, probe_client, monkeypatch):
        """verify_session=false 连通性体检：不动注册状态、不谎报检测成功。"""
        _patch_proxy(monkeypatch)
        assert (
            probe_client.post("/api/agents/probe", json={"agent_id": "soulmate"}).json()[
                "registered"
            ]
            is True
        )

        async def no_session_call(agent_id, timeout=90.0):
            raise AssertionError("verify_session=false 不该做会话探测")

        monkeypatch.setattr(reg, "session_probe", no_session_call)
        body = probe_client.post(
            "/api/agents/probe",
            json={"agent_id": "soulmate", "verify_session": False},
        ).json()
        assert body["probe"]["ok"] is True
        assert body["ok"] is False  # 未验证会话≠检测成功
        assert body["registered"] is True  # 既有注册保持
        lst = probe_client.get("/api/agents/registry").json()
        assert lst["agents"][0]["session_ready"] is True  # 未验证≠不可用


class TestBadgeNeverLies:
    def test_failed_reprobe_demotes_ready_but_keeps_row(self, probe_client, monkeypatch):
        """徽章不说谎：注册过≠永远ok——复测失败留在列表但 session_ready=0。"""
        _patch_proxy(monkeypatch)
        probe_client.post("/api/agents/probe", json={"agent_id": "soulmate"})
        _patch_proxy(monkeypatch, handshake_ok=False, handshake_detail="spawn failed")
        body = probe_client.post("/api/agents/probe", json={"agent_id": "soulmate"}).json()
        assert body["ok"] is False
        assert body["registered"] is True  # 不除名
        lst = probe_client.get("/api/agents/registry").json()
        assert lst["total"] == 1
        assert lst["agents"][0]["session_ready"] is False
        assert lst["agents"][0]["probe_ok"] is False
        assert lst["agents"][0]["probe_count"] == 2


class TestOneTruthSource:
    def test_detect_session_agents_shares_registry(self, probe_client, monkeypatch):
        """/detect.session_agents 与 /registry 同表一条真源。"""
        _patch_proxy(monkeypatch)
        probe_client.post("/api/agents/probe", json={"agent_id": "soulmate"})
        detect = probe_client.get("/api/agents/detect").json()
        lst = probe_client.get("/api/agents/registry").json()
        assert "session_agents" in detect
        assert detect["session_agents"] == lst["agents"]
        assert detect["session_agents"][0]["agent_id"] == "soulmate"
        # 安装目录与会话注册分层：catalog 不被注册表污染
        assert isinstance(detect["agents"], list) and detect["total"] >= 50


class TestFailClosed:
    def test_acp_proxy_down_never_claims_ok(self, probe_client, tmp_path, monkeypatch):
        """acp-proxy 不可达 → fail-closed ok=False，禁谎报。"""
        monkeypatch.setenv("AGENT_REGISTRY_DB", str(tmp_path / "agent_registry.db"))
        monkeypatch.setattr(reg, "ACPPROXY_HTTP", "http://127.0.0.1:1")
        body = probe_client.post(
            "/api/agents/probe",
            json={"agent_id": "soulmate", "cmd": ["soulmate", "acp"]},
        ).json()
        assert body["ok"] is False
        assert "不可达" in body["probe"]["detail"]
        assert probe_client.get("/api/agents/registry").json()["total"] == 0


class TestProbeCmdCwdFix:
    """acp-proxy _probe_agent chdir bug 的绕行：模块路由补真实 cwd，其余原样。"""

    def test_module_route_wrapped_with_real_cwd(self):
        cmd = ["/usr/bin/python3", "-m", "agent.start", "--stdio"]
        wrapped = reg._probe_cmd(cmd)
        assert isinstance(wrapped, list)
        assert wrapped[0] == "sh"
        assert f"cd {reg.ACPPROXY_DIR}" in wrapped[2]
        assert "-m agent.start --stdio" in wrapped[2]

    def test_binary_routes_untouched(self):
        for cmd in (["hermes", "acp"], ["npx", "-y", "pi-acp@0.0.34"], None):
            assert reg._probe_cmd(cmd) is cmd


class TestRoutesWired:
    def test_new_routes_not_404(self, probe_client, monkeypatch):
        _patch_proxy(monkeypatch)
        assert (
            probe_client.post("/api/agents/probe", json={"agent_id": "soulmate"}).status_code == 200
        )
        assert probe_client.get("/api/agents/registry").status_code == 200
        assert probe_client.get("/api/agents/detect").status_code == 200
        assert probe_client.get("/api/agents/health").status_code == 200

    def test_probe_missing_body_422(self, probe_client):
        assert probe_client.post("/api/agents/probe", json={}).status_code == 422
