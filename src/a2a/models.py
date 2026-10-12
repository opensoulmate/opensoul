"""A2A (Agent-to-Agent) Protocol Implementation for OpenSoul.

Based on Google A2A v1.0 specification:
- Agent Card discovery (/.well-known/agent.json)
- JSON-RPC 2.0 task management
- Task lifecycle: submitted → working → completed/failed
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, field_validator

# === Agent Card ===


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str] = []
    examples: list[str] = []


class AgentInterface(BaseModel):
    """v1.0 §4.4.6 AgentInterface —— 协议绑定一等声明（supportedInterfaces 元素）。

    v1.0 卡面形状（a2aproject/A2A docs/whats-new-v1.md BREAKING）：supportedInterfaces[]
    取代 0.x 顶层 url/preferredTransport/additionalInterfaces，protocolVersion 移入
    每个接口（Major.Minor 形如 "1.0"，spec §3.6 卡面版本号不带 patch）。每个元素必须
    如实声明该 url 上真实提供的绑定（spec：MUST accurately declare）。
    """

    url: str
    protocolBinding: str = "JSONRPC"
    protocolVersion: str = "1.0"
    tenant: str | None = None


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
    # 0.x 顶层 url/protocolVersion：只进不出（accepted on input, never emitted）。
    # v1.0 卡面 schema 无此二键（whats-new-v1.md：url→supportedInterfaces[0].url、
    # protocolVersion→AgentInterface.protocolVersion）；继续吐出=卡片不过 v1.0 schema
    # （官方 TCK CARD-EXT-001 同型违规）。发布形状唯一出口=published()。
    url: str = ""
    version: str = "1.0.0"
    protocolVersion: str = "1.0.0"
    capabilities: AgentCapabilities = AgentCapabilities()
    supportedInterfaces: list[AgentInterface] = []
    skills: list[AgentSkill] = []
    defaultInputModes: list[str] = ["text"]
    defaultOutputModes: list[str] = ["text"]

    def published(self) -> dict[str, Any]:
        """v1.0 卡面唯一发布形状（声明从实现派生，F4b-⑤/envelope 同哲学）。

        - url/protocolVersion（0.x 顶层字段）只进不出，见字段注释
        - supportedInterfaces 未显式声明的旧形自定义卡（set_agent_card 只带 url 进来）
          fold 成单接口行：url→supportedInterfaces[0].url。本实现 JSON-RPC 端点恒在
          <base>/a2a（src/a2a/api.py POST /a2a），故 fold 目标补 /a2a 后缀——接口
          url 必须是绑定真实所在（spec：MUST accurately declare）。url 已带 /a2a
          后缀不重复补；url 为空不 fabricate 接口行（空声明如实留空，禁编造 URL）。
        """
        d = self.model_dump(exclude_none=True)
        d.pop("url", None)
        d.pop("protocolVersion", None)
        if not d.get("supportedInterfaces") and self.url:
            base = self.url.rstrip("/")
            endpoint = base if base.endswith("/a2a") else f"{base}/a2a"
            d["supportedInterfaces"] = [AgentInterface(url=endpoint).model_dump(exclude_none=True)]
        return d


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
    # spec §5：id MUST contain a String, Number, or NULL value。
    # 缺省 None=成员缺席（Notification 判别在端点按 "id" in body——缺席与显式 null 是两回事）；
    # 显式 null 合法且响应回显 null。值必须保真回显（MUST be the same value）。
    id: str | int | float | None = None
    method: str
    params: dict[str, Any] = {}

    @field_validator("id", mode="before")
    @classmethod
    def _id_string_number_or_null(cls, v):
        # bool 在 Python 是 int 子类（True 会被 lax 联合静默改写成 1=id 值被篡改），
        # 但 JSON true/false 不是 Number——before 校验拒之，保 id 保真（spec §5）。
        if isinstance(v, bool):
            raise ValueError("id must be a string, number, or null")
        return v


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    # spec §5：id 是 REQUIRED 成员——无法检测时 MUST be Null（键必须在场值为 null，
    # 禁缺席禁伪造成 0）；能检测时保真回显。
    id: str | int | float | None = None
    result: Any = None
    error: dict[str, Any] | None = None

    def envelope(self) -> dict[str, Any]:
        """JSON-RPC 2.0 响应包络单一构造口（F4b-⑩ 成员语义收口）。

        修前 model_dump(exclude_none=True)：id=None 被吞=id 键缺席（规范要求在场为 Null）、
        成功响应 result=None 也被吞=既无 result 也无 error 的非法包络。本方法按规范收口：
        - id 恒在场（Null 也序列化为 "id": null）
        - 成员互斥：error 在场只回 error（result MUST NOT be included）；
          成功=result 恒在场（REQUIRED on success，值为 None 也是 "result": null）
        """
        env: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            env["error"] = self.error
        else:
            env["result"] = self.result
        return env


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
