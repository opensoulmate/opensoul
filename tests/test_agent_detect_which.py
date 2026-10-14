"""检测层/代理层 which 盲区回归（soulmate#53 e2e 边界③收口）。

根因同 737bd079：systemd 服务 PATH 不含 ~/.local/bin，裸 shutil.which 把
已安装 agent 误报 available=False / "Agent not installed" / 400 binary not found。
统一走 ws_chat.which_agent（PATH+用户安装目录+绝对路径）。
"""

import asyncio
import stat
import uuid

from src.api import agent_proxy, agents, ws_chat


def _fake_bin(tmp_path, name="mimo", content='#!/bin/sh\necho "9.9.9"\n'):
    """在类 ~/.local/bin 的目录里放一个可执行假 agent（PATH 全盲时唯一安装点）。"""
    d = tmp_path / ".local" / "bin"
    d.mkdir(parents=True)
    f = d / name
    f.write_text(content)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return d, f


def _blind(monkeypatch, extra_dir):
    """模拟 systemd 服务环境：PATH 里找不到任何 agent，只认用户安装目录。"""
    monkeypatch.setattr(ws_chat, "_EXTRA_BIN_DIRS", (str(extra_dir),))
    monkeypatch.setattr(ws_chat.shutil, "which", lambda name: None)


def _fake_registry(bid="mimo"):
    return {
        bid: {
            "name": "MiMo",
            "binary": bid,
            "description": "test",
            "icon": "x",
            "category": "other",
            "install": {},
            "args": [],
        }
    }


# ── /api/agents/detect：available 禁假阴性（边界③主症） ──


def test_detect_available_true_for_user_local_bin(monkeypatch, tmp_path):
    d, f = _fake_bin(tmp_path)
    _blind(monkeypatch, d)
    monkeypatch.setattr(agents, "AGENT_REGISTRY", _fake_registry())
    monkeypatch.setattr(agents, "_detect_cache", None)
    monkeypatch.setattr(agents, "_detect_cache_time", 0.0)
    monkeypatch.setattr(agents, "list_registered_agents", lambda **_: [])

    res = asyncio.run(agents.detect_agents())
    mimo = [a for a in res["agents"] if a["id"] == "mimo"][0]
    assert mimo["available"] is True
    assert mimo["path"] == str(f)


def test_detect_available_false_when_truly_missing(monkeypatch, tmp_path):
    _blind(monkeypatch, tmp_path)
    monkeypatch.setattr(agents, "AGENT_REGISTRY", _fake_registry())
    monkeypatch.setattr(agents, "_detect_cache", None)
    monkeypatch.setattr(agents, "_detect_cache_time", 0.0)
    monkeypatch.setattr(agents, "list_registered_agents", lambda **_: [])

    res = asyncio.run(agents.detect_agents())
    assert res["agents"][0]["available"] is False
    assert res["agents"][0]["path"] is None


def test_detect_version_probe_uses_abs_path(monkeypatch, tmp_path):
    """版本探测跑绝对路径：裸名在 PATH 盲区 FileNotFoundError→version 假 None。"""
    d, f = _fake_bin(tmp_path)  # 假 CLI 输出 9.9.9
    _blind(monkeypatch, d)
    monkeypatch.setattr(agents, "AGENT_REGISTRY", _fake_registry())
    monkeypatch.setattr(agents, "_detect_cache", None)
    monkeypatch.setattr(agents, "_detect_cache_time", 0.0)
    monkeypatch.setattr(agents, "list_registered_agents", lambda **_: [])

    res = asyncio.run(agents.detect_agents())
    assert res["agents"][0]["version"] == "9.9.9"


# ── /api/agents/uninstall：禁把已安装误报 "Agent not installed" ──


def test_uninstall_not_blind_denied(monkeypatch, tmp_path):
    d, f = _fake_bin(tmp_path)
    _blind(monkeypatch, d)
    monkeypatch.setattr(agents, "AGENT_REGISTRY", _fake_registry())

    class _Proc:
        returncode = 1

        async def communicate(self):
            return (b"", b"")

    async def _fake_shell(*a, **kw):
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _fake_shell)

    req = agents.UninstallRequest(agent_id="mimo")
    res = asyncio.run(agents.uninstall_agent(req, user_id=uuid.uuid4()))
    # 旧版在此被盲 which 挡回 "Agent not installed"；现在走到卸载尝试并回显真实绝对路径
    assert res["success"] is False
    assert "Agent not installed" not in res["error"]
    assert str(f) in res["error"]


# ── /api/agent-proxy：available 标志 + 真跑绝对路径 ──


def test_agent_proxy_list_available(monkeypatch, tmp_path):
    d, f = _fake_bin(tmp_path)
    _blind(monkeypatch, d)
    monkeypatch.setattr(agent_proxy, "AGENT_REGISTRY", _fake_registry())

    res = asyncio.run(agent_proxy.list_agents())
    assert res[0]["available"] is True


def test_agent_proxy_send_uses_abs_path(monkeypatch, tmp_path):
    d, f = _fake_bin(tmp_path)
    _blind(monkeypatch, d)
    monkeypatch.setattr(agent_proxy, "AGENT_REGISTRY", _fake_registry())

    captured = {}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return (b"ok", b"")

    async def _fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    msg = agent_proxy.AgentMessage(agent_id="mimo", message="hi")
    res = asyncio.run(agent_proxy.send_to_agent(msg, user_id=uuid.uuid4()))
    assert res.success
    assert captured["cmd"][0] == str(f)  # 绝对路径真跑，不依赖 env PATH
