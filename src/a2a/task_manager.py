"""A2A Task Manager - manages task lifecycle and agent execution.

Connects to real OpenSoul services: knowledge, search, graph, LLM.
"""

import asyncio
import base64
import json
import logging
from datetime import UTC, datetime, timezone
from uuid import UUID

from src.a2a.models import (
    DEFAULT_AGENT_CARD,
    A2AError,
    AgentCard,
    ErrorCode,
    Message,
    Task,
    TaskStatus,
)

logger = logging.getLogger(__name__)


def _resolve_user_id(user_id: str = "default") -> UUID:
    """Convert user_id to UUID — accept both UUID strings and plain usernames."""
    try:
        return UUID(user_id)
    except ValueError:
        import hashlib

        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


# === ListTasks 查询面辅助（A2A v1.0 §3.1.4）===

# §4.1.3 TaskState 全集（proto enum 去前缀转小写连字符形）。输入双形兼容：
# v1.0 JSON 形 "TASK_STATE_WORKING" 与 0.x JSON 形 "working" 同收（本实现 Task.status
# .state 是 0.x 小写形，序列化面全量迁移另拍——见 roadmap 诚实边界）。
_TASK_STATES = {
    "unspecified",
    "submitted",
    "working",
    "input-required",
    "completed",
    "failed",
    "canceled",
    "rejected",
    "auth-required",
}

_MIN_TIME = datetime(1970, 1, 1, tzinfo=UTC)


def _normalize_task_state(value) -> str:
    """TaskState 过滤值归一（TASK_STATE_* 与小写形同收）；未知值如实 -32602。"""
    if not isinstance(value, str):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid status: {value!r}")
    v = value.strip()
    if v.upper().startswith("TASK_STATE_"):
        v = v[len("TASK_STATE_") :]
    v = v.lower().replace("_", "-")
    if v not in _TASK_STATES:
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid status: {value!r}")
    return v


def _parse_iso8601(value, field: str) -> datetime:
    """ISO 8601 → aware UTC datetime；非法值如实 -32602 禁静默当缺省。"""
    if not isinstance(value, str) or not value.strip():
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid {field}: {value!r}")
    v = value.strip()
    if v.endswith(("Z", "z")):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid {field}: {value!r}")
    if dt.tzinfo is None:
        # 无时区按 UTC（与 TaskStatus.timestamp 同基准，utcnow+Z）
        dt = dt.replace(tzinfo=UTC)
    return dt


def _task_status_time(task: Task) -> datetime:
    """任务最后更新时间=status.timestamp（§3.1.4 排序键）。

    内部时间戳无法解析时按最早排序（生产数据由 TaskStatus 自动生成必可解析），
    单条坏数据不阻断列表面。
    """
    try:
        return _parse_iso8601(task.status.timestamp, "status.timestamp")
    except A2AError:
        return _MIN_TIME


def _encode_page_token(task: Task) -> str:
    """游标=末条排序键（keyset 分页，spec 明文禁 offset）；对客户端是不透明串。"""
    payload = json.dumps(
        {"t": task.status.timestamp, "i": task.id}, separators=(",", ":"), ensure_ascii=False
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_page_token(token) -> tuple[datetime, str]:
    """游标解码；非本端点发出的串如实 -32602 禁静默回第一页。"""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        obj = json.loads(raw)
        ts = _parse_iso8601(obj["t"], "pageToken.t")
        tid = obj["i"]
        if not isinstance(tid, str):
            raise ValueError("pageToken.i must be a string")
    except Exception:
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid pageToken: {token!r}")
    return ts, tid


class TaskManager:
    """In-memory task manager for A2A protocol with real service integration."""

    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self.agent_card: AgentCard = DEFAULT_AGENT_CARD
        # 扩展 Agent Card（§3.1.11）：None=未配置（能力位随之 False 不虚报）。
        self.extended_agent_card: AgentCard | None = None

    def get_agent_card(self) -> AgentCard:
        return self.agent_card

    def set_agent_card(self, card: AgentCard):
        self.agent_card = card

    def get_extended_agent_card(self) -> AgentCard | None:
        return self.extended_agent_card

    def has_extended_agent_card(self) -> bool:
        """扩展卡能力真值：真配置了才 True（§4.4.3 "supports providing"）。"""
        return self.extended_agent_card is not None

    def set_extended_agent_card(self, card: AgentCard | None):
        """配置/清除扩展卡。配置后 agent card 的 capabilities.extendedAgentCard
        自动跟随为 True（声明从实现派生，api.extended_agent_card_supported）。"""
        self.extended_agent_card = card

    def list_tasks(
        self,
        *,
        context_id: str | None = None,
        status: str | None = None,
        status_after: str | None = None,
        page_size: int | None = 50,
        page_token: str | None = None,
    ) -> tuple[list[Task], str, int]:
        """ListTasks 查询面（A2A v1.0 §3.1.4）：过滤 + keyset 游标分页 + 排序。

        - 排序：status.timestamp 降序（spec MUST：most recently updated first），
          同刻以 id 降序补全序（分页确定性，spec 未规定 tiebreak）
        - 分页：游标 pageToken/nextPageToken（spec 明文禁 offset 式）——游标=末条
          排序键，续页取排序键严格小于游标的后续项（keyset：中途新建任务不会挪动
          已翻过的项）；pageToken 空串/缺省=第一页（协议自身"无下页"哨兵就是空串）
        - totalSize=过滤后、分页前总数（spec：before pagination）
        - 非法参数（status/statusTimestampAfter/pageSize/pageToken）如实 -32602，
          禁静默当缺省
        """
        state = _normalize_task_state(status) if status is not None else None
        after = (
            _parse_iso8601(status_after, "statusTimestampAfter")
            if status_after is not None
            else None
        )
        if page_size is None:
            page_size = 50
        # spec：min 1 / max 100 / 缺省 50；bool 是 int 子类须先挡
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 100
        ):
            raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid pageSize: {page_size!r}")

        rows: list[tuple[Task, datetime]] = []
        for task in self.tasks.values():
            if context_id is not None and task.contextId != context_id:
                continue
            if state is not None and task.status.state != state:
                continue
            t = _task_status_time(task)
            if after is not None and t < after:
                continue
            rows.append((task, t))
        rows.sort(key=lambda r: (r[1], r[0].id), reverse=True)
        total = len(rows)

        start = 0
        if page_token:
            cur_t, cur_id = _decode_page_token(page_token)
            while start < total and (rows[start][1], rows[start][0].id) >= (cur_t, cur_id):
                start += 1

        page = [task for task, _ in rows[start : start + page_size]]
        has_more = start + page_size < total
        # nextPageToken 恒在场：末页必须是空串（spec MUST always be present）
        next_token = _encode_page_token(page[-1]) if has_more and page else ""
        return page, next_token, total

    async def create_task(self, message: Message, metadata: dict | None = None) -> Task:
        """Create a new task from a user message. metadata=A2A TaskSendParams.metadata。"""
        task = Task(
            status=TaskStatus(state="submitted"),
            history=[message],
            metadata=dict(metadata or {}),
        )
        self.tasks[task.id] = task
        logger.info(f"Task created: {task.id}")
        return task

    async def process_task(
        self, task_id: str, message: Message, metadata: dict | None = None
    ) -> Task:
        """Process a task - send message and get response.

        续接语义：新消息入史（恰一次）+处理。新任务首条消息不走本口（create_task
        已入史），走 run_processing —— 见 _send_message_impl。
        """
        task = self.tasks.get(task_id)
        if not task:
            # §5.4 TaskNotFoundError(-32001)：SendMessage 续接指向不存在任务
            # 如实报此码（修前 -32603），端点归口见 api.a2a_endpoint
            raise A2AError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}")
        if metadata:
            task.metadata.update(metadata)

        # Add user message to history（每条消息恰入史一次，F4b-⑧）
        task.history.append(message)
        return await self.run_processing(task, message)

    async def run_processing(self, task: Task, message: Message) -> Task:
        """执行处理并把 agent 回复入史；message 不入史——入史责任在调用方。

        新任务首条消息已由 create_task 入史（history=[message]，A2A §3.1.2 任务
        历史以用户消息开头），处理不得再入史。修前 _send_message_impl 新任务路径
        create_task+process_task 同一消息双 append（live 实证 hist=2 user+1 agent
        =首条用户消息重复记录，多轮上下文也把首条喂两遍）。
        """
        task.status = TaskStatus(state="working")

        try:
            # Extract text from message parts
            user_text = self._extract_text(message)

            # Process based on skills — 端到端总预算25s（失败三级降级/kilocode）：
            # 任一处理层挂死都在客户端30s预算内优雅收场，不再有无界等待
            try:
                response_text = await asyncio.wait_for(
                    self._process_message(user_text, task), timeout=25.0
                )
            except TimeoutError:
                response_text = (
                    f"收到您的消息：{user_text}\n\nAI处理超时（25s总预算耗尽），请稍后重试。"
                )

            # Create agent response
            agent_msg = Message(
                role="agent",
                parts=[{"type": "text", "text": response_text}],
                taskId=task.id,
                contextId=task.contextId,
            )
            task.history.append(agent_msg)
            task.status = TaskStatus(state="completed", message=agent_msg)

        except Exception as e:
            logger.error(f"Task processing failed: {e}")
            task.status = TaskStatus(
                state="failed",
                message=Message(
                    role="agent",
                    parts=[{"type": "text", "text": f"Error: {str(e)}"}],
                    taskId=task.id,
                ),
            )

        return task

    async def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def cancel_task(self, task_id: str) -> Task | None:
        task = self.tasks.get(task_id)
        if not task:
            return None
        if task.status.state in ("completed", "failed", "canceled"):
            return None
        task.status = TaskStatus(state="canceled")
        return task

    def _extract_text(self, message: Message) -> str:
        """Extract text from message parts."""
        texts = []
        for part in message.parts:
            if part.get("type") == "text":
                texts.append(part.get("text", ""))
        return "\n".join(texts)

    async def _process_message(self, text: str, task: Task) -> str:
        """Process a message and return response using skill routing."""
        # Default: echo back with skill routing
        for skill in self.agent_card.skills:
            for tag in skill.tags:
                if tag.lower() in text.lower():
                    return await self._handle_skill(skill.id, text, task)

        # No skill match — use LLM chat as fallback
        return await self._handle_chat(text, task)

    async def _handle_skill(self, skill_id: str, text: str, task: Task) -> str:
        """Handle a specific skill by routing to the appropriate service."""
        handlers = {
            "knowledge": self._handle_knowledge,
            "chat": self._handle_chat,
            "search": self._handle_search,
            "graph": self._handle_graph,
        }
        handler = handlers.get(skill_id, self._handle_default)
        return await handler(text, task)

    async def _handle_knowledge(self, text: str, task: Task) -> str:
        """Query knowledge base using real OpenSoul knowledge service."""
        try:
            from src.services.search import hybrid_search

            user_id = task.metadata.get("user_id", "default")
            uid = _resolve_user_id(user_id)
            results = await asyncio.wait_for(hybrid_search(text, uid, limit=5), timeout=10.0)

            if not results:
                return f"知识库查询：「{text}」\n\n未找到相关知识条目。请确保知识库已配置且包含相关内容。"

            lines = [f"知识库搜索结果（共 {len(results)} 条）：\n"]
            for i, r in enumerate(results, 1):
                title = r.get("title", "无标题")
                content = r.get("content", "")[:200]
                source = r.get("source", "unknown")
                score = r.get("score", 0)
                lines.append(f"{i}. **{title}** (来源: {source}, 相关度: {score:.2f})")
                if content:
                    lines.append(f"   {content}...")
                lines.append("")

            return "\n".join(lines)

        except TimeoutError:
            logger.warning("Knowledge handler timed out")
            return f"知识库查询超时：「{text}」\n\n请确保数据库和向量搜索服务已启动。"
        except Exception as e:
            logger.error(f"Knowledge handler error: {e}")
            return f"知识库查询出错：{str(e)}\n\n请确保数据库和向量搜索服务已启动。"

    async def _handle_search(self, text: str, task: Task) -> str:
        """Search using real OpenSoul search service (hybrid semantic + fulltext)."""
        try:
            from src.services.search import fulltext_search, semantic_search

            user_id = task.metadata.get("user_id", "default")
            uid = _resolve_user_id(user_id)

            # Try hybrid search first with timeout
            semantic_results, fulltext_results = await asyncio.gather(
                asyncio.wait_for(semantic_search(text, uid, limit=5), timeout=10.0),
                asyncio.wait_for(fulltext_search(text, uid, limit=5), timeout=10.0),
                return_exceptions=True,
            )

            # Handle exceptions from gather
            sem_list: list[dict] = []
            ft_list: list[dict] = []
            if isinstance(semantic_results, BaseException):
                logger.warning(f"Semantic search failed: {semantic_results}")
            elif isinstance(semantic_results, list):
                sem_list = semantic_results
            if isinstance(fulltext_results, BaseException):
                logger.warning(f"Fulltext search failed: {fulltext_results}")
            elif isinstance(fulltext_results, list):
                ft_list = fulltext_results

            lines = [f"搜索结果：「{text}」\n"]

            if sem_list:
                lines.append("📐 语义搜索：")
                for i, r in enumerate(sem_list[:3], 1):
                    chunk = r.get("chunk", "")[:150]
                    score = r.get("score", 0)
                    lines.append(f"  {i}. [{score:.2f}] {chunk}")
                lines.append("")

            if ft_list:
                lines.append("📝 全文搜索：")
                for i, r in enumerate(ft_list[:3], 1):
                    title = r.get("title", "无标题")
                    content = r.get("content", "")[:100]
                    lines.append(f"  {i}. **{title}**: {content}")
                lines.append("")

            if not sem_list and not ft_list:
                lines.append("未找到匹配结果。请检查搜索服务配置。")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Search handler error: {e}")
            return f"搜索出错：{str(e)}\n\n请确保搜索服务已配置。"

    async def _handle_graph(self, text: str, task: Task) -> str:
        """Query knowledge graph using real OpenSoul graph service."""
        try:
            from src.services.graph import get_graph

            user_id = task.metadata.get("user_id", "default")
            uid = _resolve_user_id(user_id)

            # Get graph overview with timeout
            graph_data = await asyncio.wait_for(get_graph(uid, depth=1), timeout=10.0)

            lines = ["🕸️ 知识图谱概览：\n"]

            nodes = graph_data.nodes if graph_data else []
            edges = graph_data.edges if graph_data else []

            lines.append(f"实体数量：{len(nodes)}")
            lines.append(f"关系数量：{len(edges)}")
            lines.append("")

            if nodes:
                lines.append("最近实体：")
                for node in nodes[:10]:
                    lines.append(f"  • [{node.node_type}] {node.label}")
                lines.append("")

            if edges:
                lines.append("最近关系：")
                for edge in edges[:10]:
                    lines.append(f"  • {edge.source} --[{edge.relation_type}]--> {edge.target}")

            if not nodes and not edges:
                lines.append("图谱为空。请先添加实体和关系。")

            return "\n".join(lines)

        except TimeoutError:
            logger.warning("Graph handler timed out")
            return f"图谱查询超时：「{text}」\n\n请确保图谱数据库已启动。"
        except Exception as e:
            logger.error(f"Graph handler error: {e}")
            return f"图谱查询出错：{str(e)}\n\n请确保图谱数据库已启动。"

    async def _handle_chat(self, text: str, task: Task) -> str:
        """AI chat using real LLM proxy or ACP fallback."""
        # 离线stub模型（agno全隔离测试范式 + CAMEL"能程序化验证的绝不靠LLM"）：
        # metadata.llm_mode=stub 时零网络确定性应答——集成测试/离线演示不打真实LLM
        if task.metadata.get("llm_mode") == "stub":
            return f"[stub-llm] 收到您的消息：{text}\n\n（确定性离线应答，未调用真实LLM）"
        try:
            import httpx

            from src.config import settings

            # Gather config
            api_key = settings.llm_api_key
            base_url = settings.llm_base_url
            model = settings.llm_model

            if not api_key or not base_url:
                # Fallback to ACP/hermes if no LLM configured
                return await asyncio.wait_for(self._handle_acp_chat(text, task), timeout=10.0)

            # Build conversation history from task
            messages = [
                {
                    "role": "system",
                    "content": "你是OpenSoul智能助手，基于知识库回答用户问题。请用简洁专业的中文回复。",
                }
            ]
            for msg in task.history[-10:]:  # Last 10 messages for context
                role = "user" if msg.role == "user" else "assistant"
                text_parts = [p.get("text", "") for p in msg.parts if p.get("type") == "text"]
                if text_parts:
                    messages.append({"role": role, "content": "\n".join(text_parts)})

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 2048,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    logger.warning(f"LLM API error {resp.status_code}, falling back to ACP")
                    # 与异常路径同款wait_for——此前这里裸await无界（httpx 120s），
                    # 外部LLM故障时拖穿客户端30s预算（test_chat_fallback ReadTimeout根因）
                    return await asyncio.wait_for(self._handle_acp_chat(text, task), timeout=10.0)

        except Exception as e:
            logger.error(f"Chat handler error: {e}")
            try:
                return await asyncio.wait_for(self._handle_acp_chat(text, task), timeout=10.0)
            except TimeoutError:
                return f"收到您的消息：{text}\n\nAI服务超时，请检查服务配置。"

    async def _handle_acp_chat(self, text: str, task: Task) -> str:
        """Fallback chat using ACP proxy service."""
        try:
            import httpx

            # socket超时与调用方10s wait_for预算对齐（原120s让取消与socket互相打架）
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "http://localhost:8092/acp/send",
                    json={"text": text, "session_id": str(task.id)},
                )
                data = resp.json()
                response = data.get("content", "")
                if response:
                    return response
            return f"收到您的消息：{text}\n\nAI服务暂时不可用，请检查LLM配置或hermes服务。"

        except Exception as e:
            logger.error(f"ACP proxy error: {e}")
            return f"收到您的消息：{text}\n\nAI服务暂时不可用（{str(e)}），请检查服务配置。"

    async def _handle_default(self, text: str, task: Task) -> str:
        return await self._handle_chat(text, task)


# Global task manager instance
task_manager = TaskManager()
