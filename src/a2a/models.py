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
    stateTransitionHistory: bool = True


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


# === Error Codes ===
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
