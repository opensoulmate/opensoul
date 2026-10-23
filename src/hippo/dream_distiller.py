"""P0-6: Dream记忆蒸馏 — CowAgent Deep Dream五步蒸馏 + nanobot archive-as-tool-call

功能：
- 从对话历史 + 现有记忆蒸馏结构化记忆操作
- CowAgent五步法：合并提炼/新增萃取/冲突更新/清理无效/删除冗余
- 防幻觉条款："只能基于提供的材料整理，严禁编造推测"
- nanobot archive模式：LLM显式确认每个记忆操作（非后台启发式）
- kilocode记忆回声阻断：recall命中过的回合跳过digest防自我污染

参照：
- CowAgent agent/memory/summarizer.py 34KB（五步蒸馏prompt+防编造条款）
- nanobot memory.py（archive-as-tool-call: LLM显式确认记忆检查点）
- kilocode recalledMemory()（15行防记忆回声）
"""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from src.gland.router import extract_chat_text
from src.hippo.memory_model import call_memory_llm
from src.hippo.memory_redact import redact_for_memory, redact_message_bodies

logger = logging.getLogger("opensoul.hippo.dream")


@dataclass
class DreamAction:
    """A single memory operation produced by Dream distillation.

    Mirrors nanobot's archive-as-tool-call: the LLM explicitly confirms
    each memory action rather than a background heuristic deciding.
    """

    action: str  # ADD / UPDATE / DELETE / SKIP
    content: str = ""  # new memory content (for ADD)
    memory_id: str = ""  # target memory (for UPDATE/DELETE)
    new_content: str = ""  # updated content (for UPDATE)
    memory_type: str = "semantic"
    importance: float = 0.5
    tags: list[str] = field(default_factory=list)
    reason: str = ""  # why this action was chosen
    # DeerMem #11 安全标签（抽取提议必须带三标签；空=未提供，由store确定性推断）
    scope: str = ""  # user / task / project / session
    durability: str = ""  # durable / transient
    authority: str = ""  # descriptive / prescriptive / contradiction


@dataclass
class DreamResult:
    """Result of a Dream distillation run."""

    dream_id: str
    actions: list[DreamAction] = field(default_factory=list)
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    echo_blocked: bool = False  # True if digest was skipped due to memory echo
    raw_response: str = ""
    error: str = ""
    # kilocode MemoryRedact收口：进入Dream蒸馏LLM前脱敏的敏感span数（0=无命中，
    # 处理结果必须可见——mem0 §1.1）
    redacted_spans: int = 0
    created_at: float = field(default_factory=time.time)

    @property
    def counts(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for a in self.actions:
            c[a.action] = c.get(a.action, 0) + 1
        return c

    def to_dict(self) -> dict:
        return {
            "dream_id": self.dream_id,
            "total_actions": len(self.actions),
            "counts": self.counts,
            "applied": self.applied,
            "skipped": self.skipped,
            "failed": self.failed,
            "echo_blocked": self.echo_blocked,
            "error": self.error,
            # 失败可见：0 actions时调用方可审计LLM原始返回（截断500字）
            "raw_response": (self.raw_response or "")[:500],
            "redacted_spans": self.redacted_spans,
            "created_at": self.created_at,
        }


# CowAgent Deep Dream 5-step distillation prompt
# Source: CowAgent agent/memory/summarizer.py (34KB, supplement #1)
# Anti-hallucination clause: "只能基于提供的材料整理，严禁编造推测"
DREAM_SYSTEM_PROMPT = """你是一个记忆蒸馏专家。你的任务是从对话历史和现有记忆中，提炼出结构化的记忆操作。

## 五步蒸馏法

请按以下五个步骤分析，输出JSON格式的记忆操作列表：

1. **合并提炼**：如果现有记忆中有与对话内容相关的条目，考虑是否需要合并提炼（UPDATE）
2. **新增萃取**：从对话历史中提取新的值得记住的信息（ADD）
3. **冲突更新**：如果对话中出现了与现有记忆矛盾的信息，更新旧记忆（UPDATE）
4. **清理无效**：识别已过时或不再准确的记忆（DELETE）
5. **删除冗余**：识别重复或已被其他记忆涵盖的条目（DELETE）

## 输出格式

输出一个JSON数组，每个元素是一个记忆操作：
```json
[
  {"action": "ADD", "content": "记忆内容", "memory_type": "semantic", "importance": 0.7, "tags": ["tag1"], "scope": "user", "durability": "durable", "authority": "descriptive", "reason": "为什么添加"},
  {"action": "UPDATE", "memory_id": "ltm_xxx", "new_content": "更新后的内容", "reason": "为什么更新"},
  {"action": "DELETE", "memory_id": "ltm_xxx", "reason": "为什么删除"},
  {"action": "SKIP", "memory_id": "ltm_xxx", "reason": "为什么保留不动"}
]
```

## 铁律

- **只能基于提供的材料整理，严禁编造推测**——不得添加对话和现有记忆中没有的信息
- 每个操作必须给出reason（为什么做这个决定）
- importance评分范围0.0-1.0（0.3以下=低价值，0.5=中等，0.7+=高价值）
- memory_type只能选：episodic（事件）/ semantic（知识）/ procedural（技能）/ working（工作记忆）
- ADD必须带三标签（DeerMem安全标签）：scope=user/task/project/session、
  durability=durable/transient、authority=descriptive/prescriptive/contradiction；
  自动入库只接受scope=user+durability=durable+authority=descriptive，
  其他组合会被安全门拒绝并计skipped
- 与现有记忆近重复的ADD会被自动并入既有记忆（保留原id、importance取max），
  不会产生重复条目
- 如果对话中没有值得记住的新信息，返回空数组[]
- 不确定的记忆操作用SKIP而非DELETE（保守原则）
- task/project域记忆与矛盾事实的DELETE会被安全门拦截（fail-closed），优先用UPDATE
"""


def _parse_dream_actions(response: str) -> list[DreamAction]:
    """Parse LLM response into DreamAction list.

    Implements nanobot's tolerant parsing: look for JSON array in response,
    handle fenced code blocks, fall back to raw text extraction.
    """
    actions = []
    text = response.strip()

    # Try to find JSON array (fenced or bare)
    json_str = ""
    # Look for ```json ... ``` fence
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        json_str = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        json_str = text[start:end].strip()
    elif "[" in text and "]" in text:
        start = text.index("[")
        end = text.rindex("]") + 1
        json_str = text[start:end]
    else:
        return actions

    try:
        items = json.loads(json_str)
        if not isinstance(items, list):
            return actions
    except json.JSONDecodeError:
        return actions

    valid_actions = {"ADD", "UPDATE", "DELETE", "SKIP"}
    valid_types = {"episodic", "semantic", "procedural", "working"}

    for item in items:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).upper().strip()
        if action not in valid_actions:
            continue

        da = DreamAction(
            action=action,
            content=str(item.get("content", "")),
            memory_id=str(item.get("memory_id", "")),
            new_content=str(item.get("new_content", "")),
            memory_type=str(item.get("memory_type", "semantic")),
            importance=max(0.0, min(1.0, float(item.get("importance", 0.5)))),
            tags=[str(t) for t in item.get("tags", [])],
            reason=str(item.get("reason", "")),
            # DeerMem三标签（宽容解析：缺失=空串→store侧确定性推断）
            scope=str(item.get("scope", "") or "").strip().lower(),
            durability=str(item.get("durability", "") or "").strip().lower(),
            authority=str(item.get("authority", "") or "").strip().lower(),
        )
        # Validate memory_type
        if da.memory_type not in valid_types:
            da.memory_type = "semantic"
        # ADD requires content
        if action == "ADD" and not da.content:
            continue
        # UPDATE/DELETE require memory_id
        if action in ("UPDATE", "DELETE") and not da.memory_id:
            continue
        actions.append(da)

    return actions


class DreamDistiller:
    """Dream memory distillation pipeline.

    Combines:
    - CowAgent 5-step distillation prompt (merge/extract/conflict/cleanup/dedupe)
    - nanobot archive-as-tool-call (LLM explicitly confirms each action)
    - kilocode memory echo blocker (skip digest if recall was used this turn)
    """

    # GR-①b-④ 内存窗（缓存尺寸非数据阈值——行真源恒全表落库、任何 N 值读面输出
    # 不变=零可观测变化零新阈值；persist 在场才裁=无真源裁=丢唯一副本；N<=0 不裁）
    history_window: int = 1000

    def __init__(self, ltm_store, llm_call: Callable | None = None, persist=None):
        """Initialize with a LongTermMemoryStore and optional LLM caller.

        Args:
            ltm_store: LongTermMemoryStore instance for CRUD operations
            llm_call: async callable(system_prompt, user_prompt) -> str
                      If None, uses the gland router.
            persist: DreamPersistence 梦史行真源（GR-①b-④）。缺省 None=纯内存
                     既有语义字节不变（无真源不裁窗=不丢唯一副本）。
        """
        self._store = ltm_store
        self._llm_call = llm_call
        # Memory echo tracking (kilocode pattern)
        self._recalled_memory_ids: set[str] = set()
        self._recall_count_this_turn: int = 0
        # Dream history
        self._dream_history: list[dict] = []
        self._persist = persist
        if persist is not None:
            self._reload_from_persist()

    # ── Memory echo blocker (kilocode recalledMemory, 15-line pattern) ──

    def mark_recall(self, memory_ids: list[str]):
        """Record that memories were recalled this turn.

        kilocode pattern: "本轮若跑过kilo_memory_recall且count>0→跳过digest"
        — "答案来自记忆的回合不能再蒸馏回记忆"（记忆自我污染闭环的阻断器）
        """
        self._recalled_memory_ids.update(memory_ids)
        self._recall_count_this_turn += len(memory_ids)

    def should_skip_digest(self) -> bool:
        """Check if digest should be skipped due to memory echo.

        Returns True if any memories were recalled this turn —
        the answer came from memory, so distilling it back would
        create a self-pollution loop.
        """
        return self._recall_count_this_turn > 0

    def reset_turn(self):
        """Reset per-turn tracking (call at turn boundary)."""
        self._recalled_memory_ids.clear()
        self._recall_count_this_turn = 0

    @property
    def echo_stats(self) -> dict:
        return {
            "recalled_this_turn": self._recall_count_this_turn,
            "unique_recalled": len(self._recalled_memory_ids),
            "digest_blocked": self.should_skip_digest(),
        }

    # ── Dream history（GR-①b-④ 内存窗+行真源+计数真源化） ──

    def _record_dream(self, record: dict) -> None:
        """梦史写点单一收口（5 个 append 点共用，禁两套写史逻辑）。

        内存窗 append + persist 写穿 + 窗裁剪（delitem 形态=普查判定单一源可认）：
        窗=展示缓存，行真源恒在库；计数不随窗裁剪（计数真源=行聚合，_counter_truth）。
        """
        self._dream_history.append(record)
        if self._persist is None:
            return
        try:
            self._persist.insert(record)
        except Exception as exc:  # noqa: BLE001 — 记账旁路纪律（蒸馏本体已跑完，落盘失败可见不阻断）
            logger.error("dream history persist failed (in-memory record intact): %s", exc)

        # GR-①b-④ 内存窗裁剪（persist 在场才裁=无真源裁=丢唯一副本；N<=0 不裁）
        if self.history_window > 0 and len(self._dream_history) > self.history_window:
            del self._dream_history[: len(self._dream_history) - self.history_window]

    def _reload_from_persist(self) -> None:
        """启动回载：内存窗回填（GR-①b-④——只回载最近 history_window 行作展示缓存，
        修前无落盘口；全量回载=重启即无界）。回载失败不阻断构造（记账旁路纪律：
        梦史面故障绝不吃掉 import/service 启动），logger.error 可见+空史起步。
        """
        if self._persist is None:
            return
        try:
            rows = self._persist.load_recent(self._reload_limit())
        except Exception as exc:  # noqa: BLE001 — 记账旁路纪律
            logger.error("dream history reload failed (starting with empty history): %s", exc)
            return
        self._dream_history.extend(rows)

    def _reload_limit(self) -> int:
        """回载行数上界=内存窗（N<=0=不裁窗语义，回载上界如实放开）。"""
        return self.history_window if self.history_window > 0 else 2**63 - 1

    def _cache_counters(self) -> dict:
        """内存计数缓存视图（_counter_truth 回落面；persist=None 纯内存真源面）。
        与 DreamPersistence.counters() 逐键同形状（单一形状禁两套）。"""
        return {
            "total_dreams": len(self._dream_history),
            "total_applied": sum(h.get("applied", 0) for h in self._dream_history),
            "echo_blocked": sum(1 for h in self._dream_history if h.get("echo_blocked")),
        }

    def _counter_truth(self) -> dict:
        """计数真源（GR-①b-④「计数真源化」MEM-⑥ 纪律）：persist 在场=恒对账
        dream_history 行聚合（计数器=行缓存视图，值读面读真源禁信第二套聚合列；
        聚合恒算全表=内存窗裁剪/回载窗不漏计=历史总量语义不变）；缺省=纯内存
        计数器既有语义字节不变。对账失败=logger.error 可见+回落内存计数缓存
        （缓存本职，禁静默假 0）。"""
        if self._persist is None:
            return self._cache_counters()
        try:
            return self._persist.counters()
        except Exception as exc:  # noqa: BLE001 — 记账旁路纪律
            logger.error("dream counter truth failed (falling back to cache): %s", exc)
            return self._cache_counters()

    def _recent_dreams(self, limit: int = 5) -> list[dict]:
        """recent_dreams 读面（GR-①b-① recent_records 同款回落纪律）：窗足够=窗尾
        快路径；窗不足回落行真源（任何 N 值读面输出不变）；回落失败降级窗可见非静默。"""
        if self._persist is None or len(self._dream_history) >= limit:
            return self._dream_history[-limit:]
        try:
            return self._persist.load_recent(limit)
        except Exception as exc:  # noqa: BLE001 — 记账旁路纪律
            logger.error("dream recent fallback failed (window tail shown): %s", exc)
            return self._dream_history[-limit:]

    # ── Dream distillation pipeline ──

    async def dream(
        self,
        messages: list[dict],
        force: bool = False,
        max_existing: int = 50,
    ) -> DreamResult:
        """Run Dream distillation from conversation history + existing memories.

        Args:
            messages: Conversation messages [{"role": "user"/"assistant", "content": "..."}]
            force: If True, bypass the memory echo blocker (for manual triggers)
            max_existing: Max existing memories to include in the prompt

        Returns:
            DreamResult with parsed actions and execution stats.
        """
        dream_id = (
            f"dream_{hashlib.sha256(f'{time.time()}:{len(messages)}'.encode()).hexdigest()[:12]}"
        )
        result = DreamResult(dream_id=dream_id)

        # Memory echo blocker (kilocode)
        if not force and self.should_skip_digest():
            result.echo_blocked = True
            result.error = (
                f"Digest skipped: {self._recall_count_this_turn} memories were recalled "
                "this turn — answers from memory must not be distilled back (echo blocker)"
            )
            logger.info(f"Dream {dream_id}: echo blocked ({self.echo_stats})")
            self._record_dream(result.to_dict())
            return result

        if not messages:
            result.error = "No messages to distill"
            self._record_dream(result.to_dict())
            return result

        # Gather existing memories
        existing = self._store.list_memories(limit=max_existing)
        # ── kilocode MemoryRedact存量收口（22:57轮遗留#2同源）：现有记忆块出
        # Dream蒸馏prompt前逐条先脱敏后截断（_format_memories内）——库侧store()
        # 已脱敏，但历史存量行（脱敏功能上线前写入/外部直写/旧版本）可能含凭据
        # 原文，绝不抵达记忆蒸馏LLM；span计数并入result.redacted_spans显式透出。
        existing_text, existing_spans = self._format_memories(existing)

        # Format conversation history
        # ── kilocode MemoryRedact收口（supplement3 #6）：进入Dream蒸馏LLM的对话
        # 文本先过redact（ports.ts text()源端语义）——凭据绝不进记忆蒸馏prompt
        # （memory_model可能是第三方小模型）；先脱敏后截断（_format_messages的
        # token预算截断在脱敏之后），命中span数在result.redacted_spans显式透出。
        messages, result.redacted_spans = redact_message_bodies(messages)
        result.redacted_spans += existing_spans
        conv_text = self._format_messages(messages)

        # Build user prompt
        user_prompt = f"""## 现有记忆（{len(existing)}条）
{existing_text if existing else "（无现有记忆）"}

## 对话历史
{conv_text}

请按五步蒸馏法分析以上材料，输出JSON格式的记忆操作列表。"""

        # Call LLM
        try:
            if self._llm_call:
                response = await self._llm_call(DREAM_SYSTEM_PROMPT, user_prompt)
            else:
                response = await self._call_gland_llm(DREAM_SYSTEM_PROMPT, user_prompt)
        except Exception as e:
            result.error = f"LLM call failed: {e}"
            logger.error(f"Dream {dream_id}: LLM error: {e}")
            self._record_dream(result.to_dict())
            return result

        # 非str响应（OpenAI风格dict等）统一经权威解包点（router.extract_chat_text）——
        # 此前gland路径把整个响应体str()喂给解析器→0 actions且无error（live实证bug）
        if not isinstance(response, str):
            response = extract_chat_text(response)
        result.raw_response = response[:2000]

        # Parse actions
        actions = _parse_dream_actions(response)
        result.actions = actions

        if not actions:
            logger.info(f"Dream {dream_id}: no actions parsed from response")
            self._record_dream(result.to_dict())
            return result

        # Execute actions against the store
        for action in actions:
            try:
                ok = self._execute_action(action)
                if ok:
                    result.applied += 1
                else:
                    result.skipped += 1
            except Exception as e:
                result.failed += 1
                logger.warning(f"Dream {dream_id}: action {action.action} failed: {e}")

        logger.info(
            f"Dream {dream_id}: {result.applied} applied, "
            f"{result.skipped} skipped, {result.failed} failed "
            f"(actions: {result.counts})"
        )
        self._record_dream(result.to_dict())
        return result

    def _execute_action(self, action: DreamAction) -> bool:
        """Execute a single DreamAction against the LTM store.

        nanobot pattern: each action is an explicit confirmed operation.
        """
        if action.action == "ADD":
            # DeerMem #11：抽取提议带三标签则显式传入（部分提供→fail-closed拒绝）；
            # 三者全空→None，由store确定性推断
            safety_tags = None
            if action.scope or action.durability or action.authority:
                safety_tags = {
                    "scope": action.scope,
                    "durability": action.durability,
                    "authority": action.authority,
                }
            mem = self._store.store(
                content=action.content,
                memory_type=action.memory_type,
                importance=action.importance,
                tags=action.tags,
                metadata={"source": "dream_distillation", "reason": action.reason},
                source_session="dream",
                safety_tags=safety_tags,
                write_mode="auto",
                # DeerMem #10 fact_dedup：dream是自动抽取管线，近重复→并入既有fact
                dup_policy="merge",
            )
            if mem is None:
                # gatekeeper/标签门拒绝：计入skipped（可见），不静默当成功（mem0 §1.1）
                outcome = getattr(self._store, "last_write_outcome", "") or "gate-rejected"
                logger.info("Dream ADD %s: %s", outcome, (action.content or "")[:80])
            return mem is not None

        elif action.action == "UPDATE":
            result = self._store.update_memory(
                memory_id=action.memory_id,
                content=action.new_content if action.new_content else None,
                reason=f"dream_update: {action.reason}",
            )
            return result is not None

        elif action.action == "DELETE":
            # DeerMem #11：dream是自动路径，删除过安全门（task/project域+矛盾事实fail-closed）
            return self._store.delete_memory(
                memory_id=action.memory_id,
                reason=f"dream_delete: {action.reason}",
                hard_delete=False,
                delete_mode="auto",
            )

        elif action.action == "SKIP":
            return True  # Explicit no-op, counted as applied

        return False

    async def _call_gland_llm(self, system_prompt: str, user_prompt: str) -> str:
        """记忆模型独立解析链（kilocode supplement3 #8 MemoryModel.port）。

        委托 src.hippo.memory_model.call_memory_llm：独立记忆模型（settings.
        memory_model，无效/不可用warn回退会话模型）+ timeout与调用方取消双闸 +
        temperature/topP/topK按模型解析 + 记忆模型故障一次性回退会话模型。原此处
        ~40行手搓ModelRouter块与memory_pipeline._call_llm逐字重复，已收敛到
        memory_model单一真源（fresh ModelRouter/显式provider注册/extract_chat_text
        权威解包语义原样保留）。dream显式采样temperature=0.3（蒸馏稳定复现）。
        超时/取消是显式契约原样上抛；其余异常保持"Gland router call failed"包装
        （既有调用方错误文案契约不变）。
        """
        try:
            return await call_memory_llm(system_prompt, user_prompt, temperature=0.3)
        except (TimeoutError, asyncio.CancelledError):
            raise  # 超时/取消=显式契约，不伪装成router故障（失败必须可见）
        except Exception as e:
            raise RuntimeError(f"Gland router call failed: {e}") from e

    @staticmethod
    def _format_memories(memories: list[dict]) -> tuple[str, int]:
        """Format existing memories for the prompt.
        Token预算控制：每条截断100字+最多20条+总预算1200字。

        kilocode MemoryRedact存量收口（22:57轮遗留#2同源）：每条content**先脱敏
        后截断**（防token预算把密钥拦腰截成不再匹配规则的半个密钥漏出）——历史
        存量行（脱敏功能上线前写入/外部直写/旧版本）可能含凭据原文。命中span数
        随文本返回（调用方显式透出，mem0 §1.1处理必须可见）。
        """
        MAX_CHARS_PER_MEM = 100
        MAX_MEMS = 20
        MAX_TOTAL_CHARS = 1200
        lines = []
        total = 0
        spans = 0
        for m in memories[:MAX_MEMS]:
            mid = m.get("memory_id", "")
            content, findings = redact_for_memory(str(m.get("content", "")))
            spans += len(findings)
            content = content[:MAX_CHARS_PER_MEM]
            mtype = m.get("memory_type", "semantic")
            importance = m.get("importance", 0.5)
            line = f"[{mid}] ({mtype}, imp={importance:.1f}) {content}"
            if total + len(line) > MAX_TOTAL_CHARS:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines), spans

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        """Format conversation messages for the prompt.
        Token预算控制：deepseek-r1 ollama context=4096 tokens，中文token率高。
        每条截断300字+最多20条+总预算1800字，防止context overflow→400。
        """
        MAX_CHARS_PER_MSG = 300
        MAX_MSGS = 20
        MAX_TOTAL_CHARS = 1800
        lines = []
        total = 0
        for msg in messages[-MAX_MSGS:]:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))[:MAX_CHARS_PER_MSG]
            if content:
                line = f"[{role}] {content}"
                if total + len(line) > MAX_TOTAL_CHARS:
                    lines.append(f"[{role}] (消息截断——超出token预算)")
                    break
                lines.append(line)
                total += len(line)
        return "\n".join(lines)

    def get_stats(self) -> dict:
        """Get Dream distillation statistics.

        GR-①b-④ 计数真源化（MEM-⑥）：total_dreams/total_actions_applied/
        echo_blocked_count 恒对账 dream_history 行聚合（历史总量语义不变——修前
        即全量遍历派生的总量，窗裁剪不漏计）；recent_dreams 窗不足回落行真源。
        """
        counters = self._counter_truth()
        return {
            "total_dreams": counters["total_dreams"],
            "total_actions_applied": counters["total_applied"],
            "echo_blocked_count": counters["echo_blocked"],
            "current_turn_echo": self.echo_stats,
            "recent_dreams": self._recent_dreams(),
        }
