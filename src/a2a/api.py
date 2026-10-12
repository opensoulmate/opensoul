"""A2A Protocol API endpoints for OpenSoul.

Implements:
- GET /.well-known/agent-card.json - Agent Card discovery（v1.0 注册路径）
- GET /.well-known/agent.json - 旧路径别名（重叠期，同一发布口同一形状）
- POST /a2a - JSON-RPC 2.0 endpoint
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from src.a2a.models import (
    A2AError,
    ErrorCode,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    Task,
)
from src.a2a.task_manager import task_manager

router = APIRouter()


@router.get("/.well-known/agent-card.json")
@router.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card discovery endpoint.

    发布路径双收（重叠期）：v1.0 注册路径 /.well-known/agent-card.json 为主口
    （0.3 起推荐、v1.0 标准位），旧路径 /.well-known/agent.json 保留为别名——
    两个路径走同一发布口同一形状（published() 单真源），禁两套卡。

    capabilities.streaming 由 streaming_supported() 按真实 RPC 方法面派生（单一源）：
    本端点是 agent card 唯一发布口，声明恒等于实现，禁漂移。发布形状=AgentCard
    .published()（v1.0 卡面：supportedInterfaces 一等、0.x 顶层 url/protocolVersion
    只进不出，F4b-⑪）。
    """
    card = task_manager.get_agent_card().model_copy(deep=True)
    card.capabilities.streaming = streaming_supported()
    return card.published()


@router.post("/a2a")
async def a2a_endpoint(request: Request):
    """A2A JSON-RPC 2.0 endpoint.

    包络语义（JSON-RPC 2.0 §5，F4b-⑩）：
    - id 无法检测（Parse error/Invalid Request）→ 响应 id MUST be Null（修前伪造成 0）
    - id 能检测（String/Number/Null）→ 保真回显
    - id 缺席=Notification → MUST NOT reply（修前当 Invalid Request 回错误响应）；
      但照常执行（spec：无兴趣的是 Response 不是执行）
    """
    try:
        body = await request.json()
    except Exception as e:
        # §9 JSONParseError(-32700)：收到的不是合法 JSON（id 无从检测→Null）
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(
                id=None, error={"code": ErrorCode.PARSE_ERROR, "message": f"Parse error: {e}"}
            ).envelope(),
        )

    # Notification 判别（§5：Request 对象不带 id 成员=Notification）——
    # 缺席与显式 null 是两回事，必须在模型解析前按原始 body 键集判别。
    has_id = isinstance(body, dict) and "id" in body

    try:
        rpc_request = JSONRPCRequest(**body)
    except Exception as e:
        # §9 InvalidRequestError(-32600)：JSON 合法但不是合法 Request 对象
        # （修前与解析失败同走 -32700：两种不同规范错被并成一种；
        #   id 无从检测/Request 非法→Null，修前伪造成 0）
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(
                id=None,
                error={"code": ErrorCode.INVALID_REQUEST, "message": f"Invalid Request: {e}"},
            ).envelope(),
        )

    if not has_id:
        # §5 Notification（合法 Request 形状但 id 缺席）：MUST NOT reply to a Notification。
        # 但照常执行——spec 语义是 Client 对 Response 无兴趣，不是对执行无兴趣
        # （fire-and-forget 语义），成败都不回响应（失败也不回=同一条 MUST NOT）。
        try:
            await handle_rpc(rpc_request)
        except Exception:
            # Notification 禁回错误（§5 MUST NOT reply），失败静默是规范要求非瞒报
            pass
        return Response(status_code=204)

    try:
        result = await handle_rpc(rpc_request)
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(id=rpc_request.id, result=result).envelope(),
        )
    except A2AError as e:
        # §5.4 规范码随异常带出（修前所有 handler 错误统一兜底 -32603）
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(
                id=rpc_request.id, error=e.to_error().model_dump(exclude_none=True)
            ).envelope(),
        )
    except ValidationError as e:
        # §9 InvalidParamsError(-32602)：params 结构校验失败（如 message 缺 role/parts）
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(
                id=rpc_request.id,
                error={"code": ErrorCode.INVALID_PARAMS, "message": f"Invalid parameters: {e}"},
            ).envelope(),
        )
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(
                id=rpc_request.id, error={"code": ErrorCode.INTERNAL_ERROR, "message": str(e)}
            ).envelope(),
        )


async def handle_rpc(request: JSONRPCRequest):
    """Route JSON-RPC method to handler."""
    method = request.method
    params = request.params

    handler = RPC_HANDLERS.get(method)
    if not handler:
        # §9 MethodNotFoundError(-32601)（修前 -32603）；未实现的方法也归此码
        # （§9 标准文案："does not exist or is not available"）
        raise A2AError(ErrorCode.METHOD_NOT_FOUND, f"Method not found: {method}")

    return await handler(params)


def _apply_history_limit(task_dump: dict, history_length) -> dict:
    """按 A2A v1.0 §3.2.4 historyLength 语义收口 Task.history 返回面。

    - 未提供：不设限（维持既有全量返回契约，服务端默认量=全部）
    - 0：不返回历史（history 字段 SHOULD 被省略）
    - >0：至多最近 N 条（§3.1.3 MUST NOT 超发——修前忽略参数恒全量=协议违规）
    非法值（负数/bool/非 int）如实拒绝，禁静默当未提供。
    只改返回 dump，不改 task_manager 内存任务本体（history 是真源，禁裁剪污染）。
    """
    if history_length is None:
        return task_dump
    if isinstance(history_length, bool) or not isinstance(history_length, int):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid historyLength: {history_length!r}")
    if history_length < 0:
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid historyLength: {history_length!r}")
    task_dump = dict(task_dump)  # 纯函数：不改调用方 dump，只改返回值
    if history_length == 0:
        task_dump.pop("history", None)
    elif "history" in task_dump:
        task_dump["history"] = list(task_dump["history"])[-history_length:]
    return task_dump


def _history_length_from_params(params: dict):
    """historyLength 取参双源（F4b-⑥ 语义延续）：v1.0 SendMessageConfiguration.historyLength
    优先、旧草案 TaskSendParams 顶层 historyLength 兜底。"""
    configuration = params.get("configuration") or {}
    return configuration.get("historyLength", params.get("historyLength"))


async def _send_message_impl(params: dict) -> Task:
    """SendMessage/tasks/send 共用执行体：建任务或续任务+处理，返回 Task 本体。

    任务续接双源（F4b-⑦）：旧草案 TaskSendParams 顶层 id、v1.0 SendMessageRequest
    无顶层 id —— 续接骑在 message.taskId 上（spec §6.3 多轮示例）。响应形状不在
    本层决定（v1.0 与旧线名各自包装，见 handle_send_message/handle_task_send）。
    """
    message_data = params.get("message", {})
    message = Message(**message_data)
    # A2A metadata → task.metadata（协议标准字段；
    # llm_mode=stub 供集成测试/离线演示走确定性应答，不打真实LLM）
    metadata = params.get("metadata") or {}
    task_id = params.get("id") or message_data.get("taskId")

    if task_id:
        # Continue existing task
        task = await task_manager.process_task(task_id, message, metadata=metadata)
    else:
        # Create new task —— 首条消息已由 create_task 入史（history=[message]），
        # 处理不得重复入史（F4b-⑧ 修前 create_task+process_task 同一消息双 append）
        task = await task_manager.create_task(message, metadata=metadata)
        task = await task_manager.run_processing(task, message)
    return task


async def handle_send_message(params: dict) -> dict:
    """v1.0 SendMessage —— 返回 SendMessageResponse（oneof task|message）。

    spec §9.4.1：result 是 SendMessageResponse 包裹体而非裸 Task；本实现恒产 Task
    故取 task 分支（{"task": {...}}），禁回裸 Task（那会与 v1.0 契约漂移）。
    """
    task = await _send_message_impl(params)
    dump = _apply_history_limit(
        task.model_dump(exclude_none=True), _history_length_from_params(params)
    )
    return {"task": dump}


async def handle_task_send(params: dict) -> dict:
    """旧草案 tasks/send（0.2/0.3 旧线名，Appendix A.2 重叠期服务端 MAY 接受旧形）。

    既有契约=裸 Task dump 保持不变（既有客户端/测试零破坏）；废弃勿新增消费方。
    """
    task = await _send_message_impl(params)
    return _apply_history_limit(
        task.model_dump(exclude_none=True), _history_length_from_params(params)
    )


async def handle_task_get(params: dict) -> dict:
    """Handle tasks/get - retrieve task status."""
    task_id = params.get("id")
    if not task_id:
        raise A2AError(ErrorCode.INVALID_PARAMS, "Missing task id")

    task = await task_manager.get_task(task_id)
    if not task:
        # §5.4 TaskNotFoundError(-32001)（修前 -32603）
        raise A2AError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}")

    return _apply_history_limit(task.model_dump(exclude_none=True), params.get("historyLength"))


async def handle_task_cancel(params: dict) -> dict:
    """Handle tasks/cancel - cancel a task."""
    task_id = params.get("id")
    if not task_id:
        raise A2AError(ErrorCode.INVALID_PARAMS, "Missing task id")

    # §5.4 两型必须分开（修前 cancel_task 双 None 合并成一个"cannot be canceled"）：
    # 任务不存在=TaskNotFoundError(-32001)，已终态不可取消=TaskNotCancelableError(-32002)
    if not await task_manager.get_task(task_id):
        raise A2AError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}")
    task = await task_manager.cancel_task(task_id)
    if not task:
        raise A2AError(ErrorCode.TASK_NOT_CANCELABLE, f"Task cannot be canceled: {task_id}")

    return task.model_dump(exclude_none=True)


# === RPC 方法面（单一源）===
# 已实现的 JSON-RPC 方法注册表。capabilities.streaming 派生自此表（见
# streaming_supported）——加/删方法此处即唯一改动点，agent card 声明自动跟随，
# 不必记得改第二处（否则正是 F4b-⑤ 修的声明漂移 bug 会复发）。
#
# 方法名两代并收（F4b-⑦）：v1.0 JSON-RPC 绑定（spec §9.1）是 PascalCase 方法名
# （§5.3 方法映射表：SendMessage/GetTask/CancelTask）——protocolVersion 声明
# 1.0.0 就必须应答 v1.0 方法名（修前只认 0.2 草案 tasks/* 旧线名，v1.0 客户端按
# 卡面 protocolVersion 调 SendMessage 拿 Method not found=与 1.0.0 漂移）。
# 0.2/0.3 旧线名按 Appendix A.2（重叠期服务端 MAY 接受旧形、新旧并收）保留为
# 别名：返回旧契约形状（裸 Task），既有客户端/测试零破坏；废弃勿新增消费方。
RPC_HANDLERS = {
    # v1.0 正典名（spec §5.3/§9.4）
    "SendMessage": handle_send_message,
    "GetTask": handle_task_get,
    "CancelTask": handle_task_cancel,
    # 0.2/0.3 旧线名（重叠期别名，deprecated）
    "tasks/send": handle_task_send,
    "tasks/get": handle_task_get,
    "tasks/cancel": handle_task_cancel,
}

# A2A 流式 RPC 方法名（多代并收同上）：v1.0 SendStreamingMessage/SubscribeToTask、
# 0.3 message/stream、0.2 tasks/sendSubscribe。命中任一即视为支持流式——派生名单
# 覆盖方法名各代，防"按 v1.0 名实现流式却因旧名单漏认"的二次漂移。
STREAMING_RPC_METHODS = (
    "SendStreamingMessage",
    "SubscribeToTask",
    "message/stream",
    "tasks/sendSubscribe",
)


def streaming_supported() -> bool:
    """capabilities.streaming 的单一真值：仅当流式 RPC 方法真注册才 True。

    声明与实现禁漂移（F4b-⑤ 收口 F4 审计⑨ 对外谎报）：本函数是 agent card
    streaming 位的唯一来源，实现里没有 message/stream 就不会对外宣称流式。
    """
    return any(m in RPC_HANDLERS for m in STREAMING_RPC_METHODS)
