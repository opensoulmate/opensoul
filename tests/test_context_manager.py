"""Context manager 能力测试（docs/roadmap.md P0"上下文管理"最小切片）。

① token 估算层（纯函数）：CJK/ASCII 口径、消息固定开销
② fit_text：零截断恒等、头尾保留+标记、输出估算不超预算、非法入参拒绝
③ fit_messages：快路径零裁剪、保新弃旧+占位摘要、system 优先保留、
   system/单条超大截断、预算吃光时诚实统计、非法入参拒绝
④ API 层真 router 挂载：/health、/fit、/fit-text、/estimate、失败 status=error 禁谎报 ok、422
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.hippo.context_manager import (
    MESSAGE_OVERHEAD,
    TRUNCATION_MARKER,
    ContextFitError,
    estimate_message_tokens,
    estimate_tokens,
    fit_messages,
    fit_text,
)


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def _ascii_msg(role: str, n_chars: int, tag: str = "0") -> dict:
    """构造估算开销确定且内容唯一的消息：纯 ASCII 内容（tag 前缀防同串不可分辨）。"""
    assert len(tag) <= n_chars
    return _msg(role, tag + "a" * (n_chars - len(tag)))


# ── ① token 估算层（纯函数） ─────────────────────────────────


def test_estimate_tokens_empty_ascii_cjk_mixed():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 8) == 2  # 8 ASCII → 8/4
    assert estimate_tokens("上下文管理") == 5  # 5 个 CJK 字 → 5
    assert estimate_tokens("ab上下") == 1 + 2  # 2 ASCII→1（向上取整）+ 2 CJK→2
    assert estimate_tokens("abc") == 1  # 不足 4 字符也向上取整


def test_estimate_message_tokens_counts_overhead():
    assert estimate_message_tokens({"content": ""}) == MESSAGE_OVERHEAD
    assert estimate_message_tokens({"content": "a" * 8}) == MESSAGE_OVERHEAD + 2
    assert estimate_message_tokens(_msg("user", "a" * 8)) == MESSAGE_OVERHEAD + 2


# ── ② fit_text ──────────────────────────────────────────────


def test_fit_text_no_truncation_identity():
    out = fit_text("short text", 100)
    assert out["text"] == "short text" and out["truncated"] is False
    assert out["tokens"] == estimate_tokens("short text")


def test_fit_text_truncates_keeps_head_and_tail():
    text = "H" * 400 + "TAILMARK"
    out = fit_text(text, 40)
    assert out["truncated"] is True
    assert TRUNCATION_MARKER in out["text"]
    assert out["text"].startswith("H")  # 头保留
    assert out["text"].endswith("TAILMARK")  # 尾保留
    assert out["tokens"] <= 40  # 输出估算不超预算（真保证，非尽力而为）


def test_fit_text_tiny_budget_still_honest():
    out = fit_text("x" * 400, 3)
    assert out["truncated"] is True
    assert out["tokens"] <= 3


def test_fit_text_rejects_bad_input():
    for bad_budget in (0, -1, 1.5, True):
        try:
            fit_text("x", bad_budget)  # type: ignore[arg-type]
        except ContextFitError:
            pass
        else:
            raise AssertionError(f"max_tokens={bad_budget!r} 必须拒绝")
    try:
        fit_text(123, 10)  # type: ignore[arg-type]
    except ContextFitError:
        pass
    else:
        raise AssertionError("text 非 str 必须拒绝")


# ── ③ fit_messages ──────────────────────────────────────────


def test_fit_messages_fast_path_zero_cut():
    msgs = [_msg("system", "sys"), _ascii_msg("user", 24), _ascii_msg("assistant", 24)]
    total = sum(estimate_message_tokens(m) for m in msgs)
    out = fit_messages(msgs, budget_tokens=total)  # 恰好放得下
    assert out["messages"] == msgs  # 原样恒等
    st = out["stats"]
    assert (st["kept"], st["dropped"], st["truncated"]) == (3, 0, 0)
    assert st["tokens_before"] == st["tokens_after"] == total
    assert st["placeholder_tokens"] == 0


def test_fit_messages_drops_oldest_keeps_newest_with_placeholder():
    msgs = [_ascii_msg("system", 24, "s")] + [_ascii_msg("user", 24, f"{i:02d}") for i in range(10)]
    total = sum(estimate_message_tokens(m) for m in msgs)
    out = fit_messages(msgs, budget_tokens=total // 2)  # 必然溢出
    st = out["stats"]
    assert st["dropped"] > 0 and st["kept"] > 0
    assert st["kept"] + st["dropped"] == len(msgs)
    assert st["tokens_after"] <= st["budget_tokens"]  # 真不超预算
    assert st["omitted_tokens"] > 0
    # 占位摘要必入（溢出时预留保证）且计数与 stats 一致
    placeholders = [m for m in out["messages"] if "已省略较早的" in m["content"]]
    assert len(placeholders) == 1
    assert st["placeholder_tokens"] == estimate_message_tokens(placeholders[0])
    assert f"已省略较早的 {st['dropped']} 条消息" in placeholders[0]["content"]
    # 保新弃旧：被保留的对话消息必须是原对话列表的连续后缀
    convo_kept = [
        m for m in out["messages"] if m["role"] != "system" and "已省略" not in m["content"]
    ]
    assert convo_kept == msgs[-len(convo_kept) :]
    assert msgs[-1] in convo_kept and msgs[1] not in out["messages"]  # 保最新弃最旧
    # 占位在缺口处=最旧被保留的对话消息之前
    first_kept_convo_pos = out["messages"].index(convo_kept[0])
    assert "已省略" in out["messages"][first_kept_convo_pos - 1]["content"]


def test_fit_messages_system_pinned_survives_tight_budget():
    sys_msg = _msg("system", "You are OpenSoul. Follow policy.")
    msgs = [sys_msg] + [_ascii_msg("user", 24) for _ in range(4)]
    budget = estimate_message_tokens(sys_msg) + 30
    out = fit_messages(msgs, budget_tokens=budget)
    assert out["messages"][0] == sys_msg  # system 原样保留
    st = out["stats"]
    assert st["dropped"] == 4 and st["kept"] == 1  # 对话全丢，system 留
    assert st["tokens_after"] <= budget
    assert any("已省略较早的 4 条消息" in m["content"] for m in out["messages"])


def test_fit_messages_oversized_system_truncated_not_dropped():
    msgs = [_msg("system", "S" * 800), _ascii_msg("user", 24)]
    out = fit_messages(msgs, budget_tokens=60)
    sys_kept = [m for m in out["messages"] if m["role"] == "system"]
    assert len(sys_kept) == 1 and TRUNCATION_MARKER in sys_kept[0]["content"]
    assert out["stats"]["truncated"] >= 1
    assert out["stats"]["tokens_after"] <= 60


def test_fit_messages_huge_newest_truncated_kept():
    msgs = [_ascii_msg("system", 24), _ascii_msg("user", 24), _msg("assistant", "B" * 800)]
    out = fit_messages(msgs, budget_tokens=60)
    st = out["stats"]
    assert st["truncated"] >= 1
    assert st["tokens_after"] <= 60
    # 最新消息被截断保留而非丢弃
    last = out["messages"][-1]
    assert last["role"] == "assistant" and TRUNCATION_MARKER in last["content"]


def test_fit_messages_budget_eaten_by_system_honest_stats():
    msgs = [_msg("system", "S" * 800), _ascii_msg("user", 24)]
    out = fit_messages(msgs, budget_tokens=10)  # 连截断保留门槛都不够
    st = out["stats"]
    assert st["kept"] == 0 and st["dropped"] == 2
    assert st["placeholder_tokens"] == 0  # 占位放不下时不硬塞，stats 如实报 dropped
    assert st["tokens_after"] == 0 <= 10


def test_fit_messages_rejects_bad_input():
    for bad_budget in (0, -5, 2.5, True):
        try:
            fit_messages([_msg("user", "x")], budget_tokens=bad_budget)  # type: ignore[arg-type]
        except ContextFitError:
            pass
        else:
            raise AssertionError(f"budget_tokens={bad_budget!r} 必须拒绝")
    for bad_msgs in ("nope", [{"role": "user"}], [{"content": "x"}], [42], [["user", "x"]]):
        try:
            fit_messages(bad_msgs, budget_tokens=100)  # type: ignore[arg-type]
        except ContextFitError:
            pass
        else:
            raise AssertionError(f"messages={bad_msgs!r} 必须拒绝")


# ── ④ API 层（真 router 挂载） ───────────────────────────────


def _api_client():
    app = FastAPI()
    from src.api.context_api import router

    app.include_router(router, prefix="/api/context")
    return TestClient(app)


def test_api_health_and_fit_ok():
    c = _api_client()
    assert c.get("/api/context/health").json() == {"status": "ok", "component": "ContextManager"}
    msgs = [_ascii_msg("system", 24, "s")] + [_ascii_msg("user", 24, f"{i:02d}") for i in range(10)]
    resp = c.post("/api/context/fit", json={"messages": msgs, "budget_tokens": 80})
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "ok" and body["component"] == "ContextManager"
    assert body["messages"] and body["stats"]["dropped"] > 0
    assert body["stats"]["tokens_after"] <= 80
    assert body["stats"]["estimate_method"]


def test_api_fit_error_not_fake_ok():
    c = _api_client()
    resp = c.post("/api/context/fit", json={"messages": [], "budget_tokens": 0})
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "error" and body["component"] == "ContextManager"
    assert body["error"]  # C2：禁谎报 ok


def test_api_fit_text_ok_and_error():
    c = _api_client()
    body = c.post("/api/context/fit-text", json={"text": "T" * 400, "budget_tokens": 30}).json()
    assert body["status"] == "ok" and body["truncated"] is True
    assert TRUNCATION_MARKER in body["text"] and body["tokens"] <= 30

    body = c.post("/api/context/fit-text", json={"text": "x", "budget_tokens": -1}).json()
    assert body["status"] == "error" and body["error"]


def test_api_estimate_text_and_messages():
    c = _api_client()
    body = c.post("/api/context/estimate", json={"text": "a" * 8}).json()
    assert body["status"] == "ok" and body["tokens"] == 2
    assert body["estimate_method"]
    body = c.post(
        "/api/context/estimate",
        json={"messages": [{"role": "user", "content": "a" * 8}]},
    ).json()
    assert body["status"] == "ok" and body["tokens"] == MESSAGE_OVERHEAD + 2


def test_api_estimate_requires_exactly_one_input():
    c = _api_client()
    for payload in ({}, {"text": "x", "messages": [{"role": "user", "content": "y"}]}):
        body = c.post("/api/context/estimate", json=payload).json()
        assert body["status"] == "error" and body["error"]


def test_api_missing_fields_422():
    c = _api_client()
    assert c.post("/api/context/fit", json={}).status_code == 422
    assert c.post("/api/context/fit-text", json={}).status_code == 422
