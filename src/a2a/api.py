"""A2A Protocol API endpoints for OpenSoul.

Implements:
- GET /.well-known/agent.json - Agent Card discovery
- POST /a2a - JSON-RPC 2.0 endpoint
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.a2a.models import (
    ErrorCode,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
)
from src.a2a.task_manager import task_manager

router = APIRouter()


@router.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card discovery endpoint.

    capabilities.streaming 由 streaming_supported() 按真实 RPC 方法面派生（单一源）：
    本端点是 agent card 唯一发布口，声明恒等于实现，禁漂移。
    """
    card = task_manager.get_agent_card().model_copy(deep=True)
    card.capabilities.streaming = streaming_supported()
    return card.model_dump()


@router.post("/a2a")
async def a2a_endpoint(request: Request):
    """A2A JSON-RPC 2.0 endpoint."""
    try:
        body = await request.json()
        rpc_request = JSONRPCRequest(**body)
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(
                id=0, error={"code": ErrorCode.PARSE_ERROR, "message": f"Parse error: {e}"}
            ).model_dump(exclude_none=True),
        )

    try:
        result = await handle_rpc(rpc_request)
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(id=rpc_request.id, result=result).model_dump(exclude_none=True),
        )
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(
                id=rpc_request.id, error={"code": ErrorCode.INTERNAL_ERROR, "message": str(e)}
            ).model_dump(exclude_none=True),
        )


async def handle_rpc(request: JSONRPCRequest):
    """Route JSON-RPC method to handler."""
    method = request.method
    params = request.params

    handler = RPC_HANDLERS.get(method)
    if not handler:
        raise ValueError(f"Method not found: {method}")

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
        raise ValueError(f"Invalid historyLength: {history_length!r}")
    if history_length < 0:
        raise ValueError(f"Invalid historyLength: {history_length!r}")
    task_dump = dict(task_dump)  # 纯函数：不改调用方 dump，只改返回值
    if history_length == 0:
        task_dump.pop("history", None)
    elif "history" in task_dump:
        task_dump["history"] = list(task_dump["history"])[-history_length:]
    return task_dump


async def handle_task_send(params: dict) -> dict:
    """Handle tasks/send - create or continue a task."""
    task_id = params.get("id")
    message_data = params.get("message", {})
    message = Message(**message_data)
    # A2A TaskSendParams.metadata → task.metadata（协议标准字段；
    # llm_mode=stub 供集成测试/离线演示走确定性应答，不打真实LLM）
    metadata = params.get("metadata") or {}
    # historyLength 双源收口：v1.0 SendMessageConfiguration.historyLength 优先，
    # 旧草案 TaskSendParams 顶层 historyLength 兜底（同 §3.2.4 语义）
    configuration = params.get("configuration") or {}
    history_length = configuration.get("historyLength", params.get("historyLength"))

    if task_id:
        # Continue existing task
        task = await task_manager.process_task(task_id, message, metadata=metadata)
    else:
        # Create new task
        task = await task_manager.create_task(message, metadata=metadata)
        task = await task_manager.process_task(task.id, message)

    return _apply_history_limit(task.model_dump(exclude_none=True), history_length)


async def handle_task_get(params: dict) -> dict:
    """Handle tasks/get - retrieve task status."""
    task_id = params.get("id")
    if not task_id:
        raise ValueError("Missing task id")

    task = await task_manager.get_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    return _apply_history_limit(task.model_dump(exclude_none=True), params.get("historyLength"))


async def handle_task_cancel(params: dict) -> dict:
    """Handle tasks/cancel - cancel a task."""
    task_id = params.get("id")
    if not task_id:
        raise ValueError("Missing task id")

    task = await task_manager.cancel_task(task_id)
    if not task:
        raise ValueError(f"Task cannot be canceled: {task_id}")

    return task.model_dump(exclude_none=True)


# === RPC 方法面（单一源）===
# 已实现的 JSON-RPC 方法注册表。capabilities.streaming 派生自此表（见
# streaming_supported）——加/删方法此处即唯一改动点，agent card 声明自动跟随，
# 不必记得改第二处（否则正是 F4b-⑤ 修的声明漂移 bug 会复发）。
RPC_HANDLERS = {
    "tasks/send": handle_task_send,
    "tasks/get": handle_task_get,
    "tasks/cancel": handle_task_cancel,
}

# A2A 流式 RPC 方法名（message/stream=v1.0、tasks/sendSubscribe=旧草案）。
# 命中任一即视为支持流式。
STREAMING_RPC_METHODS = ("message/stream", "tasks/sendSubscribe")


def streaming_supported() -> bool:
    """capabilities.streaming 的单一真值：仅当流式 RPC 方法真注册才 True。

    声明与实现禁漂移（F4b-⑤ 收口 F4 审计⑨ 对外谎报）：本函数是 agent card
    streaming 位的唯一来源，实现里没有 message/stream 就不会对外宣称流式。
    """
    return any(m in RPC_HANDLERS for m in STREAMING_RPC_METHODS)
