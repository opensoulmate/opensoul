"""hippo 扩展：上下文窗口管理（docs/roadmap.md P0 缺失能力"上下文管理"最小切片）。

能力边界（如实，留后续）：
- 给定消息列表 + token 预算，产出适配后的上下文窗口（超预算时保新弃旧+占位摘要，
  system 消息优先保留、超大单条可截断，全部如实标注 dropped/truncated 不静默）；
- 单文本截断（fit_text，头尾保留+截断标记）；
- token 计数是**估算**（见 ESTIMATE_METHOD），非真 tokenizer；不做摘要压缩/语义检索/
  多轮对话持久化（后续拍）。

设计纪律：
- 本层全纯函数，离线可测；API 层（src/api/context_api.py）只做入参校验与 C2 错误形状；
- 截断/丢弃永远带标志位，禁静默裁剪。
"""

import math

DEFAULT_BUDGET = 8_000  # 默认窗口预算（tokens，估算值）
MIN_MESSAGE_BUDGET = 16  # 单条消息至少保留的 token 数，低于此不值得截断保留
MESSAGE_OVERHEAD = 4  # 每条消息的角色/分隔符固定开销（估算）
TRUNCATION_MARKER = "\n…[内容因上下文预算截断]…\n"
OMISSION_TEMPLATE = "[context_manager] 已省略较早的 {n} 条消息（约 {tokens} tokens）"
# 占位摘要尺寸上界（n/tokens 取超大位数估算），溢出时从对话预算预留，保证占位必入
_PLACEHOLDER_RESERVE_MSG = {"content": OMISSION_TEMPLATE.format(n=10**5, tokens=10**8)}
ESTIMATE_METHOD = (
    "tokens≈CJK字符数+其余字符数/4（向上取整）+每条消息固定开销4；估算值，非真tokenizer"
)

# CJK 统一表意文字+扩展A+兼容表意文字+全角/中文标点（按 1 字 1 token 估）
_CJK_RANGES = (
    (0x3000, 0x303F),  # CJK 标点
    (0x3400, 0x4DBF),  # 扩展 A
    (0x4E00, 0x9FFF),  # 统一表意文字
    (0xF900, 0xFAFF),  # 兼容表意文字
    (0xFF00, 0xFFEF),  # 全角字符
)


class ContextFitError(ValueError):
    """入参非法（预算非正/消息形状错），消息直接面向 API 回包。"""


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """估算一段文本的 token 数（ESTIMATE_METHOD 口径，非真 tokenizer）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return cjk + math.ceil(other / 4)


def estimate_message_tokens(message: dict) -> int:
    """单条消息估算 token（内容 + 固定开销）。"""
    return MESSAGE_OVERHEAD + estimate_tokens(message.get("content", ""))


def _validate_messages(messages) -> list:
    if not isinstance(messages, list):
        raise ContextFitError("messages must be a list")
    out = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise ContextFitError(f"messages[{i}] must be an object with role/content")
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise ContextFitError(f"messages[{i}].role/content must be strings")
        out.append({"role": role, "content": content})
    return out


def _take_prefix(text: str, token_budget: int) -> str:
    """按估算口径取文本前缀，使 token 数不超过 token_budget。"""
    if token_budget <= 0:
        return ""
    cost = 0.0
    chars = []
    for ch in text:
        cost += 1.0 if _is_cjk(ch) else 0.25
        if math.ceil(cost) > token_budget:
            break
        chars.append(ch)
    return "".join(chars)


def fit_text(text: str, max_tokens: int) -> dict:
    """把一段文本适配到 max_tokens 内：头尾保留 + 截断标记，如实标 truncated。

    保证 estimate_tokens(结果) ≤ max_tokens（标记+头+尾的估算可加性上界）。
    """
    if not isinstance(text, str):
        raise ContextFitError("text must be a string")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
        raise ContextFitError("max_tokens must be a positive integer")
    tokens = estimate_tokens(text)
    if tokens <= max_tokens:
        return {"text": text, "truncated": False, "tokens": tokens}

    content_budget = max_tokens - estimate_tokens(TRUNCATION_MARKER)
    if content_budget < 2:
        # 预算连标记都放不下：只保留头内容，如实标截断
        head = _take_prefix(text, max_tokens)
        return {"text": head, "truncated": True, "tokens": estimate_tokens(head)}
    head_budget = max(1, int(content_budget * 0.7))
    tail_budget = max(1, content_budget - head_budget)
    head = _take_prefix(text, head_budget)
    tail_src = text[len(head) :]
    # 尾部从后往前取（近似 tail_budget 个 token）
    cost = 0.0
    tail_chars = []
    for ch in reversed(tail_src):
        cost += 1.0 if _is_cjk(ch) else 0.25
        if math.ceil(cost) > tail_budget:
            break
        tail_chars.append(ch)
    tail = "".join(reversed(tail_chars))
    fitted = head + TRUNCATION_MARKER + tail
    return {"text": fitted, "truncated": True, "tokens": estimate_tokens(fitted)}


def fit_messages(messages: list, budget_tokens: int = DEFAULT_BUDGET) -> dict:
    """把消息列表适配到 budget_tokens 内，产出上下文窗口 + 诚实统计。

    策略：
    1. 不超预算→原样返回（快路径，零裁剪）；
    2. system 消息优先占用预算（定义行为的提示不因对话变长被挤出），
       system 也超预算时按序截断/丢弃，如实计数；
    3. 非 system 消息按"保新弃旧"填充（输出保持原始相对顺序），
       最后一个放不下的可截断保留（MIN_MESSAGE_BUDGET 以上才值得截）；
       溢出时对话预算先预留占位摘要上界，保证"有丢弃必有占位摘要"；
    4. stats 全量如实：tokens_before/after、kept/dropped/truncated、省略 token 数。
    """
    msgs = _validate_messages(messages)
    if not isinstance(budget_tokens, int) or isinstance(budget_tokens, bool) or budget_tokens <= 0:
        raise ContextFitError("budget_tokens must be a positive integer")

    tokens_before = sum(estimate_message_tokens(m) for m in msgs)
    if tokens_before <= budget_tokens:
        # 快路径：全放得下，零裁剪零占位
        return {
            "messages": [dict(m) for m in msgs],
            "stats": {
                "budget_tokens": budget_tokens,
                "tokens_before": tokens_before,
                "tokens_after": tokens_before,
                "kept": len(msgs),
                "dropped": 0,
                "truncated": 0,
                "omitted_tokens": 0,
                "placeholder_tokens": 0,
                "estimate_method": ESTIMATE_METHOD,
            },
        }

    placeholder_reserve = estimate_message_tokens(_PLACEHOLDER_RESERVE_MSG)
    kept: dict = {}  # index -> fitted message
    dropped_indices: list = []
    truncated = 0
    remaining = budget_tokens

    system_idx = [i for i, m in enumerate(msgs) if m["role"] == "system"]
    convo_idx = [i for i, m in enumerate(msgs) if m["role"] != "system"]

    # ── ① system 优先（不吃占位预留，定义行为的提示优先级最高） ──
    for i in system_idx:
        cost = estimate_message_tokens(msgs[i])
        if cost <= remaining:
            kept[i] = dict(msgs[i])
            remaining -= cost
        elif remaining >= MIN_MESSAGE_BUDGET:
            fitted = fit_text(msgs[i]["content"], remaining - MESSAGE_OVERHEAD)
            kept[i] = {"role": "system", "content": fitted["text"]}
            truncated += 1
            remaining -= estimate_message_tokens(kept[i])
        else:
            dropped_indices.append(i)
    dropped_indices.extend(i for i in system_idx if i not in kept and i not in dropped_indices)

    # ── ② 非 system 保新弃旧（预算扣掉占位预留） ──────────────
    convo_remaining = max(0, remaining - placeholder_reserve)
    convo_dropped: list = []
    for pos in range(len(convo_idx) - 1, -1, -1):
        i = convo_idx[pos]
        cost = estimate_message_tokens(msgs[i])
        if cost <= convo_remaining:
            kept[i] = dict(msgs[i])
            convo_remaining -= cost
        elif convo_remaining >= MIN_MESSAGE_BUDGET:
            fitted = fit_text(msgs[i]["content"], convo_remaining - MESSAGE_OVERHEAD)
            kept[i] = {"role": msgs[i]["role"], "content": fitted["text"]}
            truncated += 1
            convo_remaining -= estimate_message_tokens(kept[i])
            convo_dropped.extend(convo_idx[:pos])  # i 之前的（更旧的）全丢
            break
        else:
            convo_dropped.extend(convo_idx[: pos + 1])
            break
    dropped_indices.extend(i for i in convo_dropped if i not in dropped_indices)

    # ── ③ 占位摘要（有丢弃才插，自身 token 计入预算） ──────────
    placeholder = None
    placeholder_tokens = 0
    if dropped_indices:
        n_dropped = len(dropped_indices)
        omitted_tokens = sum(estimate_message_tokens(msgs[i]) for i in dropped_indices)
        candidate = OMISSION_TEMPLATE.format(n=n_dropped, tokens=omitted_tokens)
        placeholder_tokens = estimate_message_tokens({"content": candidate})
        used = sum(estimate_message_tokens(kept[i]) for i in kept)
        if placeholder_tokens <= budget_tokens - used:
            # 预留保证常规溢出下必放得下；放不下（system 吃光预算）时只在 stats 如实报
            placeholder = {"role": "system", "content": candidate}
        else:
            placeholder_tokens = 0

    # ── ④ 按原始顺序装配（占位放在缺口处=最旧被保留的非 system 消息之前） ──
    ordered = []
    inserted_placeholder = placeholder is None
    for i in sorted(set(kept) | set(dropped_indices)):
        if not inserted_placeholder and (i in kept and msgs[i]["role"] != "system"):
            ordered.append(placeholder)
            inserted_placeholder = True
        if i in kept:
            ordered.append(kept[i])
    if placeholder is not None and not inserted_placeholder:
        ordered.append(placeholder)

    tokens_after = sum(estimate_message_tokens(m) for m in ordered)
    return {
        "messages": ordered,
        "stats": {
            "budget_tokens": budget_tokens,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "kept": len(kept),
            "dropped": len(dropped_indices),
            "truncated": truncated,
            "omitted_tokens": sum(estimate_message_tokens(msgs[i]) for i in dropped_indices),
            "placeholder_tokens": placeholder_tokens,
            "estimate_method": ESTIMATE_METHOD,
        },
    }
