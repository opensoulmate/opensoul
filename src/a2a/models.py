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

# === v1.0 Enum wire shape (F4b-⑬) ===
#
# A2A v1.0 序列化面（proto-first / ProtoJSON，spec §5.5 JSON 字段命名 + §6.1 响应示例）：
# TaskState / Role 的 JSON 值是 SCREAMING_SNAKE_CASE（"TASK_STATE_WORKING" / "ROLE_USER"），
# 非 0.x 小写连字符形（"working" / "user"）——a2a-python v1.0 迁移文档「Enum values:
# snake_case → SCREAMING_SNAKE_CASE」硬证（TaskState.submitted → TASK_STATE_SUBMITTED、
# Role.user → ROLE_USER，ProtoJSON 按 enum 名序列化）。修前本实现 Task.status.state 与
# Message.role 以 0.x 小写形直接 model_dump 进 wire = 全域序列化面漂移（卡面声明
# protocolVersion 1.0.0 却吐 0.x 枚举形）。
#
# 设计（声明从实现派生 + 单点出口，F4b-⑤/envelope 同哲学）：内部表示保留归一小写形
# （内部逻辑与测试稳定），wire() 单点派生 v1.0 形，全响应面唯一出口；输入双形兼容
# （"user"/"ROLE_USER" 同收）。枚举值逐键照抄规范（§4.1.3 TaskState 全集 / Role），
# 未知值不 fabricate 前缀——原样透传（off-spec 如实暴露，禁静默改写成不存在的枚举）。

# §4.1.3 TaskState 全集：内部归一小写连字符形 → v1.0 SCREAMING_SNAKE 枚举值。
_TASK_STATE_TO_WIRE = {
    "unspecified": "TASK_STATE_UNSPECIFIED",
    "submitted": "TASK_STATE_SUBMITTED",
    "working": "TASK_STATE_WORKING",
    "input-required": "TASK_STATE_INPUT_REQUIRED",
    "completed": "TASK_STATE_COMPLETED",
    "failed": "TASK_STATE_FAILED",
    "canceled": "TASK_STATE_CANCELED",
    "rejected": "TASK_STATE_REJECTED",
    "auth-required": "TASK_STATE_AUTH_REQUIRED",
}

# Role 枚举：内部 'user'/'agent' → v1.0 'ROLE_USER'/'ROLE_AGENT'（§6.1）。
_ROLE_TO_WIRE = {
    "user": "ROLE_USER",
    "agent": "ROLE_AGENT",
}


def _state_to_wire(state: str) -> str:
    """内部归一小写形 → v1.0 TASK_STATE_*。已是 TASK_STATE_* 原样回（幂等禁双前缀）。

    未知值（不在 §4.1.3 全集）不 fabricate 前缀——原样透传，如实暴露 off-spec。
    """
    if state.startswith("TASK_STATE_"):
        return state
    return _TASK_STATE_TO_WIRE.get(state, state)


def _role_to_wire(role: str) -> str:
    """内部 'user'/'agent' → v1.0 ROLE_*。已是 ROLE_* 原样回（幂等）。

    A2A Role 仅 USER/AGENT/UNSPECIFIED；未知 role 不 fabricate ROLE_* 前缀——原样透传。
    """
    if role.startswith("ROLE_"):
        return role
    return _ROLE_TO_WIRE.get(role, role)


def _normalize_role(value: Any) -> Any:
    """Message.role 输入双形归一（v1.0 形 'ROLE_USER' → 内部 'user'）。

    保证内部消费（_process_message 的 msg.role == \"user\" LLM 角色映射）对 v1.0 客户端
    发的 ROLE_USER 仍正确；未知值原样保留（禁静默改写）。
    """
    if isinstance(value, str):
        if value in ("ROLE_USER", "USER", "user"):
            return "user"
        if value in ("ROLE_AGENT", "AGENT", "agent"):
            return "agent"
    return value


# === v1.0 Part wire shape (F4b-⑭) ===
#
# A2A v1.0 Part（spec §1.4：spec/a2a.proto 是唯一规范定义；§4.1.6 字段表）：oneof content
# {text | raw | url | data} + metadata/filename/mediaType —— ProtoJSON 下 oneof 成员名
# 即判别式，wire 上无 kind 无 type 判别字段（kind 是 0.3 形，v1.0 §A.2.1「Kind
# Discriminator Removed」已移除；type 是 0.2 代/文档混用误形，a2aproject/A2A#1365）。
# 修前本实现出线 part 用 {"type":"text","text":...}=两代都不是的第三形（v1.0 客户端无从
# 识别），且文本提取只认 type=="text"——v1.0 客户端发 {"text":...} 被静默丢弃（真断点：
# prompt 变空）。
#
# 设计（声明从实现派生 + 单点出口，F4b-⑤/envelope/wire() 同哲学）：wire() 唯一出口把
# 出线 part 派生成 v1.0 平铺 oneof 形；输入三形兼容统一走 part_text 读取；未知判别值
# 不 fabricate——原样透传（off-spec 如实暴露，⑬ 未知枚举同款纪律）。
_PART_ONEOF_KEYS = ("text", "raw", "url", "data")
_PART_EXTRA_KEYS = ("metadata", "filename", "mediaType")


def _part_to_wire(part: Any) -> Any:
    """单个 part → v1.0 平铺 oneof 形（返回新 dict 不就地改，真源保护同 _apply_history_limit）。

    - 已是 v1.0 形（无 type/kind 判别键）→ 原样浅拷贝（含只带 metadata 的 part）
    - 带判别键（0.3 kind / legacy type）→ 按判别值派生成 v1.0 形，判别键不出线：
      text→{"text"}、data→{"data"}、file→{"raw"|"url", mediaType, filename}
      （0.3→1.0 字段映射照抄 a2a-js migration-guide 对照表：FilePart.file.mimeType→
       Part.mediaType、file.name→Part.filename、file.uri→content url、file.bytes→content raw）
    - 未知判别值 / 非 dict / 已知判别值但无可映射内容：原样透传（off-spec 如实暴露，
      禁 fabricate 内容成员，⑬ 未知枚举同款纪律）
    """
    if not isinstance(part, dict):
        return part
    disc = part.get("type", part.get("kind"))
    if disc is None:
        return dict(part)
    out: dict[str, Any] = {}
    if disc == "text":
        if "text" in part:
            out["text"] = part["text"]
    elif disc == "data":
        if "data" in part:
            out["data"] = part["data"]
    elif disc == "file":
        file = part.get("file")
        if isinstance(file, dict):
            if "bytes" in file:
                out["raw"] = file["bytes"]
            elif "uri" in file:
                out["url"] = file["uri"]
            if "mimeType" in file:
                out["mediaType"] = file["mimeType"]
            if "name" in file:
                out["filename"] = file["name"]
    else:
        return dict(part)
    for k in _PART_EXTRA_KEYS:
        if k in part:
            out[k] = part[k]
    if not out:
        return dict(part)
    return out


def _parts_to_wire(parts: Any) -> Any:
    """parts 列表逐项派生 v1.0 形（非列表原样透传不猜形态）。"""
    if isinstance(parts, list):
        return [_part_to_wire(p) for p in parts]
    return parts


def part_text(part: Any) -> str | None:
    """从任意受理形态的 part 提取文本（v1.0 平铺 / 0.3 kind / legacy type 三形统一读取）。

    - v1.0（spec §4.1.6/proto Part）：判别式=oneof 成员名本身，{"text": "..."} 即文本 part
    - 0.3：{"kind": "text", "text": "..."}；legacy（本实现内部形）：{"type": "text", ...}
    - 非文本 part（file/data/未知判别）与非 dict 返回 None——不是文本就不是文本，
      禁把 data part 的 data 当文本；文本值非 str（off-spec 输入）同样 None（无法入文）。
    """
    if not isinstance(part, dict):
        return None
    disc = part.get("type", part.get("kind"))
    if disc is not None:
        if disc != "text":
            return None
        v = part.get("text", "")
        return v if isinstance(v, str) else None
    v = part.get("text")
    return v if isinstance(v, str) else None


def _message_wire(d: dict[str, Any]) -> dict[str, Any]:
    """单条 Message dump 的 role → v1.0 形 + parts → v1.0 平铺 oneof 形（就地改返回 dict，不动内存本体）。"""
    if "role" in d:
        d["role"] = _role_to_wire(d["role"])
    if "parts" in d:
        d["parts"] = _parts_to_wire(d["parts"])
    return d


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
    # capabilities.streaming = 支持流式 RPC（SendStreamingMessage/SubscribeToTask 等）。
    # 默认 False=不虚报：实际发布值由 api.streaming_supported() 按真实 RPC 方法面派生
    # （见 api.py），流式方法真注册进 RPC_HANDLERS 才会 True（F4b-⑯ 已真注册）——
    # 声明与实现禁漂移（F4b-⑤）。
    streaming: bool = False
    # pushNotifications（§4.4.3）：是否支持推送通知配置（§3.1.7-3.1.10 四操作）。
    # 默认 False=不虚报：实际发布值由 api.push_notifications_supported() 按真实 RPC
    # 方法面派生（四方法全部真注册才 True，F4b-⑤ streaming 同哲学）——声明与实现禁漂移。
    pushNotifications: bool = False
    # extendedAgentCard（§4.4.3）：是否支持在认证后提供扩展 Agent Card。默认 False=
    # 不虚报：实际发布值由 api.extended_agent_card_supported() 按"扩展卡真配置"派生
    # （§4.4.3 语义=supports providing，真能给才宣称——F4b-⑤ streaming 同哲学），
    # 未配置即 False，配置后自动 True，声明与实现禁漂移。
    extendedAgentCard: bool = False
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
    role: str  # 内部归一 "user"/"agent"；wire 面以 ROLE_* 呈现（见 _role_to_wire）
    parts: list[dict[str, Any]]
    messageId: str = ""
    taskId: str | None = None
    contextId: str | None = None
    # v1.0 proto Message 可选成员（F4b-⑭）：metadata(Struct) / extensions /
    # referenceTaskIds。缺省 None=未设置（wire 面整键省略，proto3 JSON 未设置成员
    # 不出现）。修前 pydantic 默认 extra=ignore 把 v1.0 客户端发的这三键静默丢弃
    # （存不进也回不出，F4b-② LLMRequest.stream 静默忽略同型）。
    metadata: dict[str, Any] | None = None
    extensions: list[str] | None = None
    referenceTaskIds: list[str] | None = None

    @field_validator("role", mode="before")
    @classmethod
    def _role_dual_shape(cls, v):
        # v1.0 客户端发 "ROLE_USER" 归一到内部 "user"（输入双形兼容，F4b-⑬），
        # 保证 _process_message 的 LLM 角色映射与内部消费正确。
        return _normalize_role(v)

    def __init__(self, **data):
        if not data.get("messageId"):
            data["messageId"] = str(uuid4())
        super().__init__(**data)


class Artifact(BaseModel):
    name: str | None = None
    description: str | None = None
    parts: list[dict[str, Any]]
    artifactId: str = ""
    # v1.0 proto Artifact 可选成员 metadata/extensions（F4b-⑭），语义同 Message 三键
    metadata: dict[str, Any] | None = None
    extensions: list[str] | None = None

    def __init__(self, **data):
        if not data.get("artifactId"):
            data["artifactId"] = str(uuid4())
        super().__init__(**data)


# === Push Notification Objects（v1.0 §4.3，形状逐键照抄 spec/a2a.proto）===


class AuthenticationInfo(BaseModel):
    """§4.3.2 AuthenticationInfo —— webhook 投递鉴权。

    scheme=IANA HTTP 鉴权方案（Bearer/Basic/Digest…，大小写不敏感 RFC 9110 §11.1，
    REQUIRED）；credentials 随方案而定（可选）。
    """

    scheme: str
    credentials: str | None = None


class TaskPushNotificationConfig(BaseModel):
    """§4.3.1 TaskPushNotificationConfig —— 推送通知配置（wire 字段名 §5.5 camelCase）。

    字段逐键照抄 proto：tenant（opaque routing identifier，收下不解释）/ id（配置
    唯一 ID；创建时未给由服务端指派——§3.1.7 "Created configuration with assigned
    ID"）/ taskId（归属任务）/ url（REQUIRED，webhook 地址）/ token（任务/会话级
    唯一令牌，随配置往返）/ authentication（§4.3.2）。
    """

    tenant: str | None = None
    id: str = ""
    taskId: str = ""
    url: str
    token: str | None = None
    authentication: AuthenticationInfo | None = None


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

    def wire(self) -> dict[str, Any]:
        """v1.0 序列化面唯一出口（F4b-⑬）：Task/Message/TaskStatus 的枚举值以 v1.0
        SCREAMING_SNAKE_CASE（TASK_STATE_*/ROLE_*）呈现（spec §5.5/§6.1）。

        只改返回 dict 不改内存本体（内部保留归一小写形，F4b-⑫ _apply_history_limit 同款
        「只改返回 dump 不污染真源」纪律）。全响应面（SendMessage/tasks_send/tasks_get/
        tasks_cancel/ListTasks）唯一经此口出，禁再有 model_dump 直出枚举值的第二条路。
        """
        d = self.model_dump(exclude_none=True)
        st = d.get("status")
        if isinstance(st, dict):
            if "state" in st:
                st["state"] = _state_to_wire(st["state"])
            if isinstance(st.get("message"), dict):
                _message_wire(st["message"])
        for m in d.get("history", []) or []:
            if isinstance(m, dict):
                _message_wire(m)
        for a in d.get("artifacts", []) or []:
            if isinstance(a, dict) and "parts" in a:
                a["parts"] = _parts_to_wire(a["parts"])
        return d


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
