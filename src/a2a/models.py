"""A2A (Agent-to-Agent) Protocol Implementation for OpenSoul.

Based on Google A2A v1.0 specification:
- Agent Card discovery (/.well-known/agent.json)
- JSON-RPC 2.0 task management
- Task lifecycle: submitted → working → completed/failed
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

# === Agent Card ===


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str] = []
    examples: list[str] = []


class AgentCapabilities(BaseModel):
    # capabilities.streaming = 支持 message/stream 等流式 RPC。默认 False=不虚报：
    # 实际发布值由 api.streaming_supported() 按真实 RPC 方法面派生（见 api.py），
    # 加了 message/stream 进 RPC_HANDLERS 才会 True —— 声明与实现禁漂移（F4b-⑤）。
    streaming: bool = False
    pushNotifications: bool = False
    # stateTransitionHistory（0.x 字段）已移除（F4b-⑥）：A2A v1.0 §4.4.3 的
    # AgentCapabilities 只有 streaming/pushNotifications/extensions/extendedAgentCard，
    # stateTransitionHistory 是 0.x 废弃字段——协议自身无任何 API 可取回状态迁移
    # 历史（a2aproject/A2A#1228，v1.0 落实移除）；且本实现从未暴露状态迁移历史
    # （Task.history 是消息历史非状态迁移历史），修前默认 True=对外谎报+废弃协议
    # 字段。按"协议必须用最新标准版、拒绝废弃协议"移除，禁复活。


class AgentCard(BaseModel):
    name: str
    description: str
    url: str
    version: str = "1.0.0"
    protocolVersion: str = "1.0.0"
    capabilities: AgentCapabilities = AgentCapabilities()
    skills: list[AgentSkill] = []
    defaultInputModes: list[str] = ["text"]
    defaultOutputModes: list[str] = ["text"]


# === Task Models ===


class Message(BaseModel):
    role: str  # "user" or "agent"
    parts: list[dict[str, Any]]
    messageId: str = ""
    taskId: str | None = None
    contextId: str | None = None

    def __init__(self, **data):
        if not data.get("messageId"):
            data["messageId"] = str(uuid4())
        super().__init__(**data)


class Artifact(BaseModel):
    name: str | None = None
    description: str | None = None
    parts: list[dict[str, Any]]
    artifactId: str = ""

    def __init__(self, **data):
        if not data.get("artifactId"):
            data["artifactId"] = str(uuid4())
        super().__init__(**data)


class TaskStatus(BaseModel):
    state: str  # submitted, working, input-required, completed, failed, canceled
    message: Message | None = None
    timestamp: str = ""

    def __init__(self, **data):
        if not data.get("timestamp"):
            data["timestamp"] = datetime.utcnow().isoformat() + "Z"
        super().__init__(**data)


class Task(BaseModel):
    id: str
    contextId: str = ""
    status: TaskStatus
    artifacts: list[Artifact] = []
    history: list[Message] = []
    metadata: dict[str, Any] = {}

    def __init__(self, **data):
        if not data.get("id"):
            data["id"] = str(uuid4())
        if not data.get("contextId"):
            data["contextId"] = str(uuid4())
        super().__init__(**data)


# === JSON-RPC Models ===


class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int
    method: str
    params: dict[str, Any] = {}


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int
    result: Any = None
    error: dict[str, Any] | None = None


class JSONRPCError(BaseModel):
    code: int
    message: str
    data: Any = None


# === Error Codes（A2A v1.0 §5.4 Error Code Mappings + §9 标准 JSON-RPC 码）===
# §9 标准码：JSONParseError -32700 / InvalidRequestError -32600 / MethodNotFoundError
# -32601 / InvalidParamsError -32602 / InternalError -32603；§5.4 A2A 专有码区间
# -32001..-32099 逐型映射。本表=规范全集逐值照抄，测试逐值对照锁定（声明恒等于规范）。
class ErrorCode:
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    TASK_NOT_FOUND = -32001
    TASK_NOT_CANCELABLE = -32002
    PUSH_NOTIFICATION_NOT_SUPPORTED = -32003
    UNSUPPORTED_OPERATION = -32004
    CONTENT_TYPE_NOT_SUPPORTED = -32005
    INVALID_AGENT_RESPONSE = -32006
    EXTENDED_AGENT_CARD_NOT_CONFIGURED = -32007
    EXTENSION_SUPPORT_REQUIRED = -32008
    VERSION_NOT_SUPPORTED = -32009


class A2AError(ValueError):
    """带 §5.4 规范错误码的协议错误 —— raise 点=语义条件已知处，端点归口成 error 对象。

    修前所有 handler 错误统一 raise ValueError→端点兜底 INTERNAL_ERROR(-32603)：
    TaskNotFound 被吞成 -32603、Method not found 被吞成 -32603，客户端按规范码分流
    （重试/提示/建任务）无从谈起。本类把"是哪个错"的判断放回条件已知的 raise 点
    （声明从实现派生，F4b-⑤ 同哲学），端点只搬运不猜码。
    继承 ValueError：修前全部 raise ValueError，既有调用点/测试按 ValueError 捕获的
    契约不变（test_a2a_history_face pytest.raises(ValueError) 锁死面）；本类只给同一
    异常带上规范错误码。
    """

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_error(self) -> JSONRPCError:
        """→ JSON-RPC error 对象（§9 error.code/message[/data] 契约）。"""
        return JSONRPCError(code=self.code, message=self.message, data=self.data)


# === Default Agent Card ===

DEFAULT_AGENT_CARD = AgentCard(
    name="OpenSoul Agent",
    description="OpenSoul知识大脑 - A2A兼容的AI Agent服务",
    url="http://localhost:8090",
    skills=[
        AgentSkill(
            id="knowledge",
            name="知识库管理",
            description="创建、查询、管理知识库和知识条目",
            tags=["knowledge", "kb", "知识库"],
            examples=["创建一个新知识库", "搜索知识条目"],
        ),
        AgentSkill(
            id="chat",
            name="智能对话",
            description="基于知识库的AI对话",
            tags=["chat", "对话", "AI"],
            examples=["帮我分析这个问题", "总结一下这个文档"],
        ),
        AgentSkill(
            id="search",
            name="统一搜索",
            description="跨知识库的语义搜索",
            tags=["search", "搜索"],
            examples=["搜索关于森林防火的知识"],
        ),
        AgentSkill(
            id="graph",
            name="知识图谱",
            description="构建和查询知识图谱",
            tags=["graph", "图谱", "知识图谱"],
            examples=["查看知识图谱", "添加实体关系"],
        ),
    ],
)
