"""A2A Protocol API endpoints for OpenSoul.

Implements:
- GET /.well-known/agent-card.json - Agent Card discovery（v1.0 注册路径）
- GET /.well-known/agent.json - 旧路径别名（重叠期，同一发布口同一形状）
- POST /a2a - JSON-RPC 2.0 endpoint
"""

import asyncio
import base64
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from src.a2a.models import (
    A2AError,
    ErrorCode,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    Task,
    TaskPushNotificationConfig,
)
from src.a2a.task_manager import task_manager

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/.well-known/agent-card.json")
@router.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card discovery endpoint.

    发布路径双收（重叠期）：v1.0 注册路径 /.well-known/agent-card.json 为主口
    （0.3 起推荐、v1.0 标准位），旧路径 /.well-known/agent.json 保留为别名——
    两个路径走同一发布口同一形状（published() 单真源），禁两套卡。

    capabilities.streaming 由 streaming_supported() 按真实 RPC 方法面派生（单一源）：
    capabilities.extendedAgentCard 由 extended_agent_card_supported() 按扩展卡真配置
    派生（§4.4.3 "supports providing"，真能给才宣称）——本端点是 agent card 唯一
    发布口，声明恒等于实现，禁漂移。发布形状=AgentCard
    .published()（v1.0 卡面：supportedInterfaces 一等、0.x 顶层 url/protocolVersion
    只进不出，F4b-⑪）。
    """
    card = task_manager.get_agent_card().model_copy(deep=True)
    card.capabilities.streaming = streaming_supported()
    card.capabilities.pushNotifications = push_notifications_supported()
    card.capabilities.extendedAgentCard = extended_agent_card_supported()
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
            result = await handle_rpc(rpc_request)
            if isinstance(result, _StreamSpec):
                # 流式方法的 Notification：照常执行（处理已在后台跑）但 MUST NOT
                # reply——回收未消费的订阅队列防泄漏（事件照发、无人收）
                task_manager.unsubscribe_stream(result.task_id, result.queue)
        except Exception:
            # Notification 禁回错误（§5 MUST NOT reply），失败静默是规范要求非瞒报
            pass
        return Response(status_code=204)

    try:
        result = await handle_rpc(rpc_request)
        if isinstance(result, _StreamSpec):
            # 流式方法（SendStreamingMessage/SubscribeToTask）：响应体即 SSE 流
            # （§9.4.2 HTTP 200 text/event-stream），非普通 JSON-RPC 单响应
            return _sse_response(result, rpc_request.id)
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

    # §3.3.4 Capability Validation（先于派发也先于 Method not found）：能力位
    # false/缺席时，流式操作 MUST 回 UnsupportedOperationError(-32004)、推送配置
    # 操作 MUST 回 PushNotificationNotSupportedError(-32003)——而非 -32601（修前
    # SubscribeToTask/SendStreamingMessage 恒 -32601=live 规范违规）。声明从实现
    # 派生的另一半：能力 False（未实现）时未实现方法的错误面也随真实能力走。
    if method in STREAMING_RPC_METHODS and not streaming_supported():
        raise A2AError(ErrorCode.UNSUPPORTED_OPERATION, f"Streaming is not supported: {method}")
    if method in PUSH_NOTIFICATION_RPC_METHODS and not push_notifications_supported():
        raise A2AError(
            ErrorCode.PUSH_NOTIFICATION_NOT_SUPPORTED,
            f"Push notifications are not supported: {method}",
        )

    handler = RPC_HANDLERS.get(method)
    if not handler:
        # §9 MethodNotFoundError(-32601)（修前 -32603）；未实现的方法也归此码
        # （§9 标准文案："does not exist or is not available"）
        raise A2AError(ErrorCode.METHOD_NOT_FOUND, f"Method not found: {method}")

    # §5 params 结构化校验的第二段（Request 校验面）：Array（by-position）是合法
    # Request 形状——不是 -32600 Invalid Request（修前被误判，Request 对象本身合法）；
    # 但 A2A v1.0（spec/a2a.proto）每个 RPC 方法的 params 都是单个请求消息对象=
    # by-name，by-position 形不被任何方法接受→归 -32602 Invalid params（§9 "Invalid
    # method parameter(s)"），与 -32600（Request 对象非法）分开禁混码。
    if isinstance(params, list):
        raise A2AError(
            ErrorCode.INVALID_PARAMS,
            "Invalid params: A2A methods take by-name (object) parameters",
        )

    return await handler(params)


def _check_history_length(history_length):
    """historyLength 合法性（未提供=None/合法值原样）；非法值（负数/bool/非 int）
    如实 -32602 禁静默当未提供。流式口在开流前校验（开流后已无法改状态码回错误），
    非流式口在 _apply_history_limit 内校验——同一校验单一源。
    """
    if history_length is None:
        return None
    if isinstance(history_length, bool) or not isinstance(history_length, int):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid historyLength: {history_length!r}")
    if history_length < 0:
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid historyLength: {history_length!r}")
    return history_length


def _apply_history_limit(task_dump: dict, history_length) -> dict:
    """按 A2A v1.0 §3.2.4 historyLength 语义收口 Task.history 返回面。

    - 未提供：不设限（维持既有全量返回契约，服务端默认量=全部）
    - 0：不返回历史（history 字段 SHOULD 被省略）
    - >0：至多最近 N 条（§3.1.3 MUST NOT 超发——修前忽略参数恒全量=协议违规）
    非法值（负数/bool/非 int）如实拒绝，禁静默当未提供。
    只改返回 dump，不改 task_manager 内存任务本体（history 是真源，禁裁剪污染）。
    """
    history_length = _check_history_length(history_length)
    if history_length is None:
        return task_dump
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


def _inline_push_config(params: dict):
    """随发送注册推送配置的取参双源（proto SendMessageConfiguration
    .task_push_notification_config；§3.1.7 "or later by invoking
    CreateTaskPushNotificationConfig"）：v1.0 configuration 内字段优先、旧草案
    TaskSendParams 顶层 pushNotificationConfig 兜底——两代都收下，禁静默丢。"""
    configuration = params.get("configuration") or {}
    return configuration.get("taskPushNotificationConfig", params.get("pushNotificationConfig"))


def _register_inline_push(task_id: str, inline_push) -> None:
    """登记随发送携带的推送配置（处理前登记，处理中的更新才投得到）。"""
    if not inline_push:
        return
    config = TaskPushNotificationConfig(**inline_push)  # url REQUIRED → -32602
    task_manager.create_push_config(task_id, config)


def _encode_push_page_token(offset: int) -> str:
    """配置列表游标=不透明串（base64 偏移；分页 MAY，本实现真支持）。"""
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def _decode_push_page_token(token) -> int:
    """游标解码；非本端点发出的串如实 -32602 禁静默回第一页（⑫ 同纪律）。"""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        offset = int(raw.decode("ascii"))
        if offset < 0:
            raise ValueError("negative offset")
    except Exception:
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid pageToken: {token!r}")
    return offset


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
    inline_push = _inline_push_config(params)

    if task_id:
        # Continue existing task —— 续接准入单一收口（存在性/contextId 一致性/推断/
        # 终态拒收，见 task_manager.ensure_accepts_message）；inline 推送配置须在
        # 处理前登记，处理中的状态更新才投得到（§3.1.7 随发送注册）
        task = await task_manager.ensure_accepts_message(task_id, message)
        _register_inline_push(task.id, inline_push)
        task = await task_manager.process_task(task_id, message, metadata=metadata)
    else:
        # Create new task —— 首条消息已由 create_task 入史（history=[message]），
        # 处理不得重复入史（F4b-⑧ 修前 create_task+process_task 同一消息双 append）
        task = await task_manager.create_task(message, metadata=metadata)
        _register_inline_push(task.id, inline_push)
        task = await task_manager.run_processing(task, message)
    return task


# === 真流式方法（A2A v1.0 §5.3 SendStreamingMessage / SubscribeToTask，§9.4）===
# JSON-RPC-over-SSE 传输层（§9.4.2）：HTTP 200 text/event-stream，每个 SSE data 帧
# =一个 JSON-RPC 2.0 响应包络（id 保真回显请求 id），result=StreamResponse 单键
# （{"statusUpdate"}|{"artifactUpdate"}|{"task"}，判别式=oneof 成员名本身——
# whats-new-v1.md）。v1.0 无 final 字段（0.x 已移除，「Leverage protocol binding
# specific stream closure mechanism instead」）：终态由**收流**表示——帧序=Task
# 全量快照首帧（§3.1.2「the stream MUST begin with the Task object」）→ 零或多
# 状态迁移/artifact 事件 → 终态全量快照（§11.7「MAY optionally resend a final
# Task snapshot before closing」明文许可的可选帧）后收流。收尾快照非「Final
# completion indicator」——完成指示=收流本身（F4b-⑯f 两读判定收口+⑯c 归因勘误）。


class _StreamSpec:
    """流式方法返回哨兵：handle_rpc 正常返回，端点检测到哨兵改走 SSE 传输层。

    错误仍在开流前以普通 JSON-RPC 错误响应如实回（错误面契约不变，禁伪 SSE）；
    queue=订阅队列（元素=(载荷, 是否收尾帧)），收流/Notification 丢弃时由调用方
    退订防泄漏。
    """

    def __init__(self, task_id: str, queue, history_length=None):
        self.task_id = task_id
        self.queue = queue
        self.history_length = history_length


async def _stream_run(task_id: str, coro) -> None:
    """流式处理执行体：处理在后台跑（流消费与处理并行），失败也必发收尾快照——
    禁悬挂打开的流（run_processing 内部已吞异常置 failed 并自发达收尾；本口兜底
    其外的异常面，如续接指向任务恰消失）。
    """
    try:
        await coro
    except Exception as e:
        logger.error(f"stream processing failed: {e}")
        task = task_manager.tasks.get(task_id)
        if task is not None:
            task_manager.emit_stream_end(task)


def _sse_response(spec: _StreamSpec, rpc_id) -> StreamingResponse:
    """SSE 传输层（§9.4.2）：逐帧 data: <JSON-RPC 包络>，终态快照（end）后收流。"""

    async def gen():
        try:
            while True:
                payload, end = await spec.queue.get()
                if (
                    spec.history_length is not None
                    and isinstance(payload, dict)
                    and "task" in payload
                ):
                    # 收尾快照=返回的 Task：historyLength 同 _apply_history_limit 语义
                    payload = {"task": _apply_history_limit(payload["task"], spec.history_length)}
                env = JSONRPCResponse(id=rpc_id, result=payload).envelope()
                yield f"data: {json.dumps(env, ensure_ascii=False)}\n\n"
                if end:
                    break
        finally:
            # 收流/客户端断联都退订（空订阅表随删防泄漏）
            task_manager.unsubscribe_stream(spec.task_id, spec.queue)

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


async def handle_send_streaming_message(params: dict) -> _StreamSpec:
    """v1.0 SendStreamingMessage（§5.3/§9.4.2）—— 发消息并订阅实时更新（SSE）。

    Request: Same as SendMessage（§9.4.2）——任务续接双源（v1.0 message.taskId /
    旧草案顶层 id）、随发送推送配置双源、historyLength 双源（收尾快照 history 收口）
    全部走与 SendMessage 同一取参口（单一源）。与非流式口的差别只在交付面：
    帧序随 §3.1.2 Task lifecycle stream——首帧=Task 全量快照（「MUST begin with
    the Task object」，订阅时点真态；续接首帧与新任务首帧对称：均含刚发出的
    新消息——入史先于订阅，F4b-⑯e），随后状态迁移/artifact 事件逐帧推，终态
    快照后收流（v1.0 收流=终态指示）。
    """
    message_data = params.get("message", {})
    message = Message(**message_data)  # params 校验失败 → -32602（端点归口）
    metadata = params.get("metadata") or {}
    task_id = params.get("id") or message_data.get("taskId")
    inline_push = _inline_push_config(params)
    # 开流前校验（开流后已无法改状态码回错误）——与 _apply_history_limit 同一校验口
    history_length = _check_history_length(_history_length_from_params(params))

    if task_id:
        # Continue existing task —— 续接准入在开流前单一收口（存在性/contextId 一致性/
        # 推断/终态拒收，与 _send_message_impl 同一 ensure_accepts_message；错误如实
        # JSON-RPC 回禁伪 SSE）；入史先于订阅（F4b-⑯e 续接首帧对称化：首帧快照含刚
        # 发出的新消息，与新任务首帧 create_task 已入史对称——修前订阅先于入史，
        # 续接首帧 history 缺新消息=不对称），订阅先于启动处理：处理中的迁移事件
        # 一帧不丢
        task = await task_manager.ensure_accepts_message(task_id, message)
        _register_inline_push(task.id, inline_push)
        task = task_manager.record_message(task.id, message, metadata=metadata)
        queue = task_manager.subscribe_stream(task.id, send_current=True, first_frame_ends=False)
        coro = task_manager.run_processing(task, message)
    else:
        task = await task_manager.create_task(message, metadata=metadata)
        _register_inline_push(task.id, inline_push)
        queue = task_manager.subscribe_stream(task.id, send_current=True, first_frame_ends=False)
        coro = task_manager.run_processing(task, message)
    asyncio.create_task(_stream_run(task.id, coro))
    return _StreamSpec(task.id, queue, history_length=history_length)


async def handle_subscribe_to_task(params: dict) -> _StreamSpec:
    """v1.0 SubscribeToTask（§5.3/§9.4.6）—— 订阅既有任务的更新流（同 SSE 形）。

    首帧=当前状态全量快照（已收束态——终态/中断态（§11.7）——即收尾帧=订阅即收流），
    随后状态迁移事件，收尾快照后收流。params=SubscribeToTaskRequest 形（id REQUIRED，
    tenant 收下不解释）。
    """
    task_id = params.get("id")
    if not task_id or not isinstance(task_id, str):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid id: {task_id!r}")
    if not await task_manager.get_task(task_id):
        raise A2AError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}")
    queue = task_manager.subscribe_stream(task_id, send_current=True)
    return _StreamSpec(task_id, queue)


async def handle_send_message(params: dict) -> dict:
    """v1.0 SendMessage —— 返回 SendMessageResponse（oneof task|message）。

    spec §9.4.1：result 是 SendMessageResponse 包裹体而非裸 Task；本实现恒产 Task
    故取 task 分支（{"task": {...}}），禁回裸 Task（那会与 v1.0 契约漂移）。
    """
    task = await _send_message_impl(params)
    dump = _apply_history_limit(task.wire(), _history_length_from_params(params))
    return {"task": dump}


async def handle_task_send(params: dict) -> dict:
    """旧草案 tasks/send（0.2/0.3 旧线名，Appendix A.2 重叠期服务端 MAY 接受旧形）。

    既有契约=裸 Task dump 保持不变（既有客户端/测试零破坏）；废弃勿新增消费方。
    """
    task = await _send_message_impl(params)
    return _apply_history_limit(task.wire(), _history_length_from_params(params))


async def handle_task_get(params: dict) -> dict:
    """Handle tasks/get - retrieve task status."""
    task_id = params.get("id")
    if not task_id:
        raise A2AError(ErrorCode.INVALID_PARAMS, "Missing task id")

    task = await task_manager.get_task(task_id)
    if not task:
        # §5.4 TaskNotFoundError(-32001)（修前 -32603）
        raise A2AError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}")

    return _apply_history_limit(task.wire(), params.get("historyLength"))


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

    return task.wire()


async def handle_list_tasks(params: dict) -> dict:
    """v1.0 ListTasks（spec §3.1.4 / §9.4.4）—— 任务列表：过滤 + 游标分页 + 倒序。

    响应形状逐键照抄 ListTasksResponse：tasks / nextPageToken（恒在场，末页空串
    ""，spec MUST always be present）/ pageSize（本响应用的页大小=生效上限，非
    实际条数）/ totalSize（分页前总数）。查询语义在 task_manager.list_tasks
    （过滤/排序/keyset 游标），本层只做 wire 形状收口：
    - historyLength：逐任务收口（与 GetTask 同一 _apply_history_limit 语义）
    - includeArtifacts 缺省 False 时 artifacts 必须整键省略（spec 明文：不得以
      空数组或 null 出现）；True 时带真实内容
    - params.tenant 收下不解释（§3.1.4 opaque routing identifier，本部署单端点
      无路由选择，AgentInterface.tenant 未设=无匹配约束）
    """
    include_artifacts = params.get("includeArtifacts")
    if include_artifacts is None:
        include_artifacts = False
    if not isinstance(include_artifacts, bool):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid includeArtifacts: {include_artifacts!r}")
    history_length = params.get("historyLength")
    page_size = params.get("pageSize")
    if page_size is None:
        page_size = 50  # spec 缺省；合法性（1..100）由 list_tasks 如实校验

    tasks, next_token, total = task_manager.list_tasks(
        context_id=params.get("contextId"),
        status=params.get("status"),
        status_after=params.get("statusTimestampAfter"),
        page_size=page_size,
        page_token=params.get("pageToken"),
    )

    dumps = []
    for task in tasks:
        d = _apply_history_limit(task.wire(), history_length)
        if not include_artifacts:
            d.pop("artifacts", None)
        dumps.append(d)
    return {
        "tasks": dumps,
        "nextPageToken": next_token,
        "pageSize": page_size,
        "totalSize": total,
    }


async def handle_get_extended_agent_card(params: dict) -> dict:
    """v1.0 GetExtendedAgentCard（spec §3.1.11 / §9.4.8）—— 扩展 Agent Card。

    §3.3.4 能力校验（逐条 MUST）：能力位 false/缺席 → UnsupportedOperationError
    (-32004)；宣称支持但未配置 → ExtendedAgentCardNotConfiguredError(-32007)；
    否则返回完整 AgentCard（published() 卡面唯一发布形状，与 well-known 同形，
    禁两套卡）。params.tenant 收下不解释（同 ListTasks）。
    """
    if not extended_agent_card_supported():
        raise A2AError(ErrorCode.UNSUPPORTED_OPERATION, "Extended agent card is not supported")
    card = task_manager.get_extended_agent_card()
    if card is None:
        # §3.3.4：宣称支持但未配置（配置在发布卡之后被清除的竞态）
        raise A2AError(
            ErrorCode.EXTENDED_AGENT_CARD_NOT_CONFIGURED, "Extended agent card is not configured"
        )
    return card.published()


async def handle_create_task_push_notification_config(params: dict) -> dict:
    """v1.0 CreateTaskPushNotificationConfig（spec §3.1.7 / §5.3）。

    params=TaskPushNotificationConfig 对象本体（proto rpc 签名如此）。url=REQUIRED
    （缺失 → pydantic ValidationError → 端点归口 -32602）；taskId 必填且任务必须
    存在（§3.1.7 TaskNotFoundError=-32001）；id 未给由服务端指派（"Created
    configuration with assigned ID"）。配置生命周期："MUST persist until task
    completion or explicit deletion"——本实现保留到显式删除（MUST 的超集）。
    """
    task_id = params.get("taskId")
    if not task_id or not isinstance(task_id, str):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid taskId: {task_id!r}")
    if not await task_manager.get_task(task_id):
        raise A2AError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}")
    config = TaskPushNotificationConfig(**params)  # url REQUIRED → ValidationError→-32602
    return task_manager.create_push_config(task_id, config)


async def handle_get_task_push_notification_config(params: dict) -> dict:
    """v1.0 GetTaskPushNotificationConfig（spec §3.1.8）。"""
    task_id, config_id = params.get("taskId"), params.get("id")
    if not task_id or not isinstance(task_id, str):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid taskId: {task_id!r}")
    if not config_id or not isinstance(config_id, str):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid id: {config_id!r}")
    cfg = task_manager.get_push_config(task_id, config_id)
    if cfg is None:
        # §3.1.8 错误表原文："TaskNotFoundError: The push notification configuration
        # does not exist"——规范此型复用 TaskNotFound 码(-32001)，message 如实说明
        # 缺的是配置，禁谎报成缺任务。
        raise A2AError(
            ErrorCode.TASK_NOT_FOUND,
            f"Push notification config not found: {config_id} (task {task_id})",
        )
    return cfg


async def handle_list_task_push_notification_configs(params: dict) -> dict:
    """v1.0 ListTaskPushNotificationConfigs（spec §3.1.9）。

    响应形状逐键照抄 ListTaskPushNotificationConfigsResponse：configs / nextPageToken
    （恒在场，末页空串——⑫ ListTasksResponse 同纪律）。分页是 MAY，本实现真支持：
    pageSize=生效上限（正整数，非法值如实 -32602 禁静默当缺省）、pageToken=不透明
    偏移游标（坏游标禁静默回第一页）。
    """
    task_id = params.get("taskId")
    if not task_id or not isinstance(task_id, str):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid taskId: {task_id!r}")
    if not await task_manager.get_task(task_id):
        # §3.1.9：TaskNotFoundError —— 任务不存在
        raise A2AError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}")

    page_size = params.get("pageSize")
    if page_size is not None and (
        isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1
    ):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid pageSize: {page_size!r}")
    offset = 0
    token = params.get("pageToken")
    if token:
        offset = _decode_push_page_token(token)

    configs = task_manager.list_push_configs(task_id)
    if page_size is None:
        window = configs[offset:]
        next_token = ""
    else:
        window = configs[offset : offset + page_size]
        next_offset = offset + page_size
        next_token = _encode_push_page_token(next_offset) if next_offset < len(configs) else ""
    return {"configs": window, "nextPageToken": next_token}


async def handle_delete_task_push_notification_config(params: dict) -> dict:
    """v1.0 DeleteTaskPushNotificationConfig（spec §3.1.10）。

    幂等（"MUST be idempotent"）：配置不存在=同一效果（成功不报错）；任务不存在
    如实 TaskNotFoundError(-32001)。返回 google.protobuf.Empty 的 JSON 形={}。
    """
    task_id, config_id = params.get("taskId"), params.get("id")
    if not task_id or not isinstance(task_id, str):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid taskId: {task_id!r}")
    if not config_id or not isinstance(config_id, str):
        raise A2AError(ErrorCode.INVALID_PARAMS, f"Invalid id: {config_id!r}")
    if not await task_manager.get_task(task_id):
        raise A2AError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}")
    task_manager.delete_push_config(task_id, config_id)  # 幂等：False=本来就没有
    return {}


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
    "ListTasks": handle_list_tasks,
    "GetExtendedAgentCard": handle_get_extended_agent_card,
    # 真流式两方法（§9.4.2/§9.4.6，F4b-⑯）——JSON-RPC-over-SSE 传输层真落地；
    # 注册即 streaming_supported() 自动 True（声明从实现派生，能力门自动放行）。
    # 无 0.x 旧线名别名（message/stream、tasks/sendSubscribe 本实现从未有过，F4b-⑮
    # 同纪律：无历史契约可保）——旧名仍回 -32601 如实不冒充。
    "SendStreamingMessage": handle_send_streaming_message,
    "SubscribeToTask": handle_subscribe_to_task,
    # 推送配置四方法（§3.1.7-3.1.10 / §5.3）——v1.0 名一等注册；无 0.x 旧线名
    # 别名（本实现此前从未有过推送面，无历史契约可保，deprecated 勿新增消费方）
    "CreateTaskPushNotificationConfig": handle_create_task_push_notification_config,
    "GetTaskPushNotificationConfig": handle_get_task_push_notification_config,
    "ListTaskPushNotificationConfigs": handle_list_task_push_notification_configs,
    "DeleteTaskPushNotificationConfig": handle_delete_task_push_notification_config,
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


# A2A 推送配置四方法（§5.3 名，proto rpc 名逐字照抄）——capabilities.pushNotifications
# 派生自此名单（见 push_notifications_supported），加/删方法此处即唯一改动点。
PUSH_NOTIFICATION_RPC_METHODS = (
    "CreateTaskPushNotificationConfig",
    "GetTaskPushNotificationConfig",
    "ListTaskPushNotificationConfigs",
    "DeleteTaskPushNotificationConfig",
)


def push_notifications_supported() -> bool:
    """capabilities.pushNotifications 的单一真值：四方法全部真注册才 True。

    四个操作是一体的能力面（§3.1.7-3.1.10），缺一即不完整禁虚报——真能做才宣称
    （F4b-⑤ streaming / ⑫ extendedAgentCard 同哲学：声明从实现派生，禁硬编码 bool）。
    """
    return all(m in RPC_HANDLERS for m in PUSH_NOTIFICATION_RPC_METHODS)


def extended_agent_card_supported() -> bool:
    """capabilities.extendedAgentCard 的单一真值：仅当扩展卡真配置才 True。

    §4.4.3 语义="supports providing an extended agent card when authenticated"
    ——真能给才宣称（默认未配置=不虚报，配置后自动 True）。与 streaming 的派生
    刻意不同源：streaming 的"实现"=流式方法真注册（注册即可流），扩展卡的
    "实现"=扩展卡真存在（注册了方法但无卡可给≠能提供扩展卡）；两者同一条原则
    ——声明从实现派生，禁硬编码 bool。
    """
    return task_manager.has_extended_agent_card()


# === 能力门收口（§3.3.4，禁冒充）===
# v1.0 §5.3 方法映射表至此全数有真实现（F4b-⑯ 真流式两方法收口最后一块）：
# SendStreamingMessage/SubscribeToTask = JSON-RPC-over-SSE 真传输层（§9.4.2：
# data 帧=JSON-RPC 包络 + StreamResponse 单键；v1.0 收流=终态指示，无 final 字段），
# streaming_supported() 随注册自动 True（声明从实现派生）。0.x 旧线名 message/stream、
# tasks/sendSubscribe 未注册（本实现从未有过，无历史契约可保）——归 -32601 如实
# 不冒充；能力门在流式能力 False 时仍拦 -32004（未来拆方法时自动恢复拦截）。
# 未实现方法面仅剩：SendMessageConfiguration.acceptedOutputModes·returnImmediately
# 等收下不解释字段（语义未定义面另拍）与 gRPC/REST binding（本仓 JSONRPC only）。
