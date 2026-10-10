"""9.4 独立性铁律回归：_resolve_agent_binary / which_agent。

红线（2026-10-10 用户原话"每个agent都应该是独立的"）：真实身份 agent 的任务
绝不变身成别的 agent 大脑；"默认用hermes"式兜底已删除。"""

import os
import stat

from src.api import ai_groups, ws_chat

# ── which_agent：systemd PATH 盲区回归 ──


def test_which_agent_finds_user_local_bin(monkeypatch, tmp_path):
    """装在 ~/.local/bin 的 agent，即使 PATH 里没有也必须找得到（周期#53 根因）。"""
    d = tmp_path / ".local" / "bin"
    d.mkdir(parents=True)
    f = d / "mimo"
    f.write_text("#!/bin/sh\n")
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(ws_chat, "_EXTRA_BIN_DIRS", (str(d),))
    monkeypatch.setattr(ws_chat.shutil, "which", lambda name: None)  # PATH 全盲
    hit = ws_chat.which_agent("mimo")
    assert hit == str(f)
    assert hit is not None and os.path.isabs(hit)


def test_which_agent_not_found_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(ws_chat, "_EXTRA_BIN_DIRS", (str(tmp_path),))
    monkeypatch.setattr(ws_chat.shutil, "which", lambda name: None)
    assert ws_chat.which_agent("no-such-agent") is None


# ── _resolve_agent_binary：真实身份绝不变身 ──


def _blind_which(monkeypatch, found: dict):
    """模拟服务环境 PATH 盲区：只认 found 里的名字。"""
    monkeypatch.setattr(ai_groups, "which_agent", lambda name: found.get(name))


def test_real_identity_never_morphs_into_hermes(monkeypatch):
    # 周期#53 事故形态：mimo 在服务 PATH 里"找不到"→ 旧版兜底变身 hermes
    _blind_which(monkeypatch, {"hermes": "/usr/bin/hermes"})
    assert ai_groups._resolve_agent_binary("mimo", "") == "mimo"
    assert ai_groups._resolve_agent_binary("mimo", "") != "hermes"


def test_soulmate_never_resolves_to_cli(monkeypatch):
    _blind_which(monkeypatch, {"hermes": "/usr/bin/hermes", "mimo": "/x/mimo"})
    assert ai_groups._resolve_agent_binary("soulmate", "") == "soulmate"


def test_real_identity_resolves_to_self_when_installed(monkeypatch):
    _blind_which(monkeypatch, {"mimo": "/home/u/.local/bin/mimo"})
    assert ai_groups._resolve_agent_binary("mimo", "") == "mimo"


def test_instance_suffix_identity_keeps_own_brain(monkeypatch):
    # claude-2 = claude 的实例，不是别人
    _blind_which(monkeypatch, {"claude": "/usr/bin/claude", "hermes": "/usr/bin/hermes"})
    assert ai_groups._resolve_agent_binary("claude-2", "") == "claude"


def test_instance_suffix_unknown_fails_loud(monkeypatch):
    _blind_which(monkeypatch, {"hermes": "/usr/bin/hermes"})
    # 找不到本体 → 原样返回（下游显式"Agent未安装"），禁止兜底变身
    assert ai_groups._resolve_agent_binary("pi-1", "") == "pi-1"


# ── 角色槽位：保留既有映射语义 ──


def test_role_slot_maps_via_model(monkeypatch):
    _blind_which(monkeypatch, {"mimo": "/x/mimo"})
    assert ai_groups._resolve_agent_binary("executor-1", "mimo-v2.5-pro") == "mimo"


def test_role_slot_maps_via_role_keyword(monkeypatch):
    _blind_which(monkeypatch, {"hermes": "/usr/bin/hermes"})
    assert ai_groups._resolve_agent_binary("verifier-2", "") == "hermes"


def test_role_slot_no_mapping_fails_loud(monkeypatch):
    _blind_which(monkeypatch, {})
    assert ai_groups._resolve_agent_binary("executor-1", "") == "executor-1"


def test_empty_agent_id_unchanged(monkeypatch):
    _blind_which(monkeypatch, {"hermes": "/usr/bin/hermes"})
    assert ai_groups._resolve_agent_binary("", "") == ""
