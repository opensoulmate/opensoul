"""9.4 观测面修复：started 事件的执行通道标签必须如实（soulmate 走 ACP 不是 CLI）。"""

from src.api.ai_groups import _channel_desc


def test_soulmate_channel_is_acp_not_binary():
    # 独立性铁律：soulmate 子任务走自己的 ACP WS，禁显示 binary=hermes（信息位推断误导）
    assert _channel_desc("soulmate", "hermes") == "channel=soulmate-acp"
    assert _channel_desc("SoulMate", "hermes") == "channel=soulmate-acp"


def test_cli_agents_keep_binary_label():
    assert _channel_desc("hermes", "hermes") == "binary=hermes"
    assert _channel_desc("mimo", "mimo") == "binary=mimo"
    assert _channel_desc("verifier-1", "hermes") == "binary=hermes"


def test_no_binary_fallback_label_for_soulmate():
    for binary in ("hermes", "mimo", "unknown"):
        assert "binary=" not in _channel_desc("soulmate", binary)
