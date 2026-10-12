"""A2A Task Manager - manages task lifecycle and agent execution.

Connects to real OpenSoul services: knowledge, search, graph, LLM.
"""

import asyncio
import base64
import json
import logging
from datetime import UTC, datetime, timezone
from uuid import UUID, uuid4

import httpx

from src.a2a.models import (
    DEFAULT_AGENT_CARD,
    A2AError,
    AgentCard,
    Artifact,
    ErrorCode,
    Message,
    Task,
    TaskPushNotificationConfig,
    TaskStatus,
    part_text,
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


# 终态全集：进入终态即交互周期收束（流式收尾快照 + 推送 {"task"} 全量，同一真源）。
_TERMINAL_STATES = ("completed", "failed", "canceled", "rejected")


def _status_update_payload(task: Task) -> dict:
    """状态迁移事件（StreamResponse 单键 {"statusUpdate"}，§4.1.5 TaskStatusUpdateEvent）。

    v1.0 形（whats-new-v1.md）：判别式=oneof 成员名本身，无 kind 判别键、无 final
    字段（0.x final 已移除——v1.0 由传输层收流表示终态）；status 走 Task.wire()
    单点派生（TASK_STATE_*/ROLE_*，禁第二套枚举）。推送 webhook 与 SSE 流共用本
    载荷（F4b-⑮ §4.3.3 同形，单点防两套）。
    """
    return {
        "statusUpdate": {
            "taskId": task.id,
            "contextId": task.contextId,
            "status": task.wire()["status"],
        }
    }


def _task_snapshot_payload(task: Task) -> dict:
    """全量任务快照（StreamResponse 单键 {"task"}）。"""
    return {"task": task.wire()}


def _artifact_update_payload(
    task: Task, artifact: Artifact, part: dict, *, append: bool, last_chunk: bool
) -> dict:
    """任务 artifact 分块事件（StreamResponse 单键 {"artifactUpdate"}，§4.2.2）。

    字段逐键照抄 spec §4.2.2 TaskArtifactUpdateEvent（camelCase §5.5）：
    taskId/contextId/artifact（REQUIRED）+ append/lastChunk（optional bool——本实现
    每块显式给出真值，无"未设置"歧义）；metadata 未设置整键省略（proto3 JSON 语义）。
    artifact 帧内只带本块增量 part（§4.2.2 append 原文："the content of this artifact
    should be appended to a previously sent artifact with the same ID"——增量语义，
    非全量重发），part=v1.0 平铺 oneof 形（{"text": ...}，F4b-⑭ 纪律）。
    """
    wire_artifact: dict = {"artifactId": artifact.artifactId, "parts": [part]}
    if artifact.name:
        wire_artifact["name"] = artifact.name
    return {
        "artifactUpdate": {
            "taskId": task.id,
            "contextId": task.contextId,
            "artifact": wire_artifact,
            "append": append,
            "lastChunk": last_chunk,
        }
    }


class LLMStreamError(RuntimeError):
    """LLM 流式生成中途故障（F4b-⑯b）：已发分块不可撤回——如实让任务失败可见。

    刻意不是 TimeoutError 子类：run_processing 的 25s 超时降级（把"AI处理超时"文本
    当成功回复）只适用于零分块路径；一旦已对外发过 artifactUpdate 分块，再把降级文本
    当成功回复=流与终稿互相矛盾（谎报 ok，F1 语义收口同款纪律）。发块后故障必须走
    failed（错误进 status.message 不进 history，F4b-⑧）。
    """


class _ArtifactChunkStream:
    """LLM 生成文本 → TaskArtifactUpdateEvent token 级分块发射（F4b-⑯b）。

    - 延迟一拍发射（pending buffer）：下一块到来（或收尾）才知道当前块是否最后一块
      ——lastChunk 语义精确（§4.2.2 "the final chunk of the artifact"），禁先发后翻转。
    - append 语义精确：首块 append=False（新建同 ID artifact），后续块 True（追加）。
    - 发射时机=累积时机：块进 task.artifacts 真源与订阅广播同刻——SubscribeToTask
      中途订阅的首帧快照（§3.1.6 防信息丢失）恰含已生成部分，后续 append 块续上。
    - history 契约零变化（F4b-⑧ 消息入史恰一次）：分块只走 artifacts/流广播。
    """

    def __init__(self, manager: "TaskManager", task: Task, *, name: str = "response"):
        self._manager = manager
        self._task = task
        self._name = name
        self._artifact: Artifact | None = None
        self._pending: str | None = None
        self._emitted = 0

    @property
    def committed(self) -> bool:
        """已对外发过块（不可撤回）——中途故障的降级/失败分流依据。"""
        return self._emitted > 0

    @property
    def artifact(self) -> Artifact | None:
        return self._artifact

    def push(self, delta: str) -> None:
        """收一个 token 增量（空串不 fabricate 块）。"""
        if not delta:
            return
        if self._pending is not None:
            self._emit(self._pending, first=self._emitted == 0, last=False)
        self._pending = delta

    def close(self) -> None:
        """收尾：剩余块以 lastChunk=True 发出；零块则不 fabricate artifact。"""
        if self._pending is not None:
            self._emit(self._pending, first=self._emitted == 0, last=True)
            self._pending = None

    def _emit(self, delta: str, *, first: bool, last: bool) -> None:
        part = {"text": delta}
        if self._artifact is None:
            self._artifact = Artifact(name=self._name, parts=[part])
            self._task.artifacts.append(self._artifact)
        else:
            self._artifact.parts.append(part)
        self._emitted += 1
        self._manager._emit_stream(
            self._task,
            _artifact_update_payload(
                self._task, self._artifact, part, append=not first, last_chunk=last
            ),
        )


class _ChoiceChunkStreams:
    """多 choice 分块发射面（F4b-⑰）：每 choice 一条独立 _ArtifactChunkStream。

    - 主 choice（index 0）artifact name="response"（既有契约零变化）；非主 choice
      name=f"response-{index}"——非主 choice 一律物化为独立 artifact 不静默丢
      （修前 _sse_delta_text 只取 choices[0]，choices[i>0] 增量被静默丢弃，与
      F4b-① gland"多 choice 按 index 各自聚合不静默丢"纪律相悖）。
    - 各 artifact 的 append/lastChunk 语义与主 choice 同款精确（每 artifact 独立
      收尾：首块 append=False、末块 lastChunk=True）。
    - 懒创建：该 choice 首个非空增量到来才建流（不为缺席的 choice fabricate artifact）。
    - committed/close 与单流同语义（失败分流"已发块不可撤回"=任一 choice 已发块）。
    """

    def __init__(self, manager: "TaskManager", task: Task):
        self._streams: dict[int, _ArtifactChunkStream] = {}
        self._manager = manager
        self._task = task

    def push(self, index: int, delta: str) -> None:
        """收一个 choice 的 token 增量（空串不 fabricate 块）。"""
        if not delta:
            return
        s = self._streams.get(index)
        if s is None:
            s = _ArtifactChunkStream(
                self._manager,
                self._task,
                name="response" if index == 0 else f"response-{index}",
            )
            self._streams[index] = s
        s.push(delta)

    def close(self) -> None:
        """收尾：逐 choice 各自 lastChunk 收束（不悬挂任一 artifact）。"""
        for s in self._streams.values():
            s.close()

    @property
    def committed(self) -> bool:
        """任一 choice 已对外发过块（不可撤回）——失败分流依据。"""
        return any(s.committed for s in self._streams.values())


def _sse_choice_deltas(frame: dict) -> dict[int, str]:
    """OpenAI 形 SSE 帧 → 逐 choice 文本增量 {choice_index: delta}（F4b-⑰）。

    修前 _sse_delta_text 只取 choices[0]，非主 choice 的 delta 被静默丢弃——现逐
    choice 归集（每 choice 走自己的 artifact 分块流，_ChoiceChunkStreams）。
    - index 取 choices[].index；缺失/非法（bool/非 int）按帧内位置兜底（单 choice
      帧恒 0=既有语义零变化）。
    - 非文本帧（usage 收尾帧/无 content/形状不符/空串）不 fabricate 块；content
      非 str（如 None）同忽略；畸形帧同 index 撞键按序累加（禁静默丢）。
    """
    out: dict[int, str] = {}
    try:
        choices = frame.get("choices") or []
    except AttributeError:
        return out
    for pos, choice in enumerate(choices):
        try:
            idx = choice.get("index")
            if isinstance(idx, bool) or not isinstance(idx, int):
                idx = pos
            content = (choice.get("delta") or {}).get("content")
        except AttributeError:
            continue
        if isinstance(content, str) and content:
            out[idx] = out.get(idx, "") + content
    return out


def _message_choice_texts(choices: list) -> dict[int, str]:
    """非流式 choices[]（message.content 形）→ {choice_index: content}。

    index 兜底/空 content 过滤与 _sse_choice_deltas 同纪律；供非流式/JSON 整段
    路径把非主 choice 物化为独立 artifact（F4b-⑰ 不静默丢）。
    """
    out: dict[int, str] = {}
    for pos, choice in enumerate(choices):
        try:
            idx = choice.get("index")
            if isinstance(idx, bool) or not isinstance(idx, int):
                idx = pos
            content = (choice.get("message") or {}).get("content")
        except AttributeError:
            continue
        if isinstance(content, str) and content:
            out[idx] = out.get(idx, "") + content
    return out


def _materialize_extra_choices(task: Task, choices: list) -> None:
    """非主 choice 物化为独立 artifact（F4b-⑰，非流式路径不静默丢）。

    主 choice（index 0，位置兜底）契约零变化：回复文本照旧、非流式路径不为主
    choice 建 artifact（F4b-⑯b 零变化面延续）；仅非主 choice 补 artifact——单块
    append=False/lastChunk=True（无流式过程可回放，不硬造伪增量）。
    """
    texts = _message_choice_texts(choices)
    texts.pop(0, None)
    for idx in sorted(texts):
        task.artifacts.append(Artifact(name=f"response-{idx}", parts=[{"text": texts[idx]}]))


class TaskManager:
    """In-memory task manager for A2A protocol with real service integration."""

    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self.agent_card: AgentCard = DEFAULT_AGENT_CARD
        # 扩展 Agent Card（§3.1.11）：None=未配置（能力位随之 False 不虚报）。
        self.extended_agent_card: AgentCard | None = None
        # 推送通知配置（§3.1.7-3.1.10）：task_id → config_id → config dict（插入序）。
        # 与 tasks 同为进程内真源（重启即清，与既有 in-memory 任务边界一致）。
        self.push_configs: dict[str, dict[str, dict]] = {}
        # webhook 投递留痕（失败也留痕禁静默）：tests/live 取证回读口。
        self.push_delivery_log: list[dict] = []
        # 真流式事件总线（F4b-⑯）：task_id → 订阅队列列表。多并发流同收同序事件
        # （v1.0「Multiple concurrent streams allowed; all receive same ordered
        # events」）；队列元素=(StreamResponse 载荷 dict, 是否收尾帧)。与 tasks 同为
        # 进程内真源（重启即清，与既有 in-memory 任务边界一致）。
        self._stream_subs: dict[str, list[asyncio.Queue]] = {}

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

    # === 推送通知配置（A2A v1.0 §3.1.7-3.1.10 + §4.3）===

    def create_push_config(self, task_id: str, config: TaskPushNotificationConfig) -> dict:
        """登记推送配置：id 未给由服务端指派（§3.1.7 "with assigned ID"），
        taskId 恒绑真任务（调用方查过任务存在）。返回 wire 形 dict（camelCase）。"""
        cfg = config.model_dump(exclude_none=True)
        cfg["taskId"] = task_id
        if not cfg.get("id"):
            cfg["id"] = str(uuid4())
        self.push_configs.setdefault(task_id, {})[cfg["id"]] = cfg
        return dict(cfg)

    def get_push_config(self, task_id: str, config_id: str) -> dict | None:
        cfg = self.push_configs.get(task_id, {}).get(config_id)
        return dict(cfg) if cfg else None

    def list_push_configs(self, task_id: str) -> list[dict]:
        """按登记序返回（翻页稳定序）。"""
        return [dict(c) for c in self.push_configs.get(task_id, {}).values()]

    def delete_push_config(self, task_id: str, config_id: str) -> bool:
        """§3.1.10 幂等删除：配置不存在=同一效果（返回 False=本来就没有，不报错）。"""
        return self.push_configs.get(task_id, {}).pop(config_id, None) is not None

    async def _dispatch_push(self, task: Task, payload: dict) -> None:
        """§4.3.3 webhook 投递：POST 单键 StreamResponse 载荷到该任务的每个已配置
        webhook（"MUST attempt delivery at least once for each configured webhook"，
        每次任务更新对每个配置各投一次）。

        - Authorization: {scheme} {credentials}（§4.3.3；无 authentication 不带）
        - 失败只留痕 push_delivery_log + warning，绝不影响任务本体（尽力投递）
        """
        configs = self.push_configs.get(task.id)
        if not configs:
            return
        async with httpx.AsyncClient(timeout=5.0) as client:
            for cfg in list(configs.values()):
                auth = cfg.get("authentication") or {}
                headers = {"Content-Type": "application/json"}
                if auth.get("scheme"):
                    headers["Authorization"] = (
                        f"{auth['scheme']} {auth.get('credentials') or ''}".rstrip()
                    )
                entry = {
                    "task_id": task.id,
                    "config_id": cfg.get("id"),
                    "url": cfg.get("url"),
                    "payload_key": next(iter(payload), None),
                }
                try:
                    resp = await client.post(cfg["url"], json=payload, headers=headers)
                    entry["ok"] = 200 <= resp.status_code < 300
                    entry["status_code"] = resp.status_code
                except Exception as e:  # 投递失败禁静默：留痕可见，任务本体不受累
                    entry["ok"] = False
                    entry["error"] = str(e)
                self.push_delivery_log.append(entry)
                if not entry.get("ok"):
                    logger.warning(f"push delivery failed: {entry}")

    async def _notify_push(self, task: Task) -> None:
        """任务状态迁移 → webhook 载荷（§4.3.3 恰一键 StreamResponse）：

        - 中间迁移（working 等非终态）={"statusUpdate"}（TaskStatusUpdateEvent：
          taskId/contextId/status，状态 wire 面与 Task.wire() 同源禁两套枚举）
        - 终态（completed/failed/canceled/rejected）={"task"}（全量 Task wire，
          含 artifacts/history 最新全貌）
        """
        if not self.push_configs.get(task.id):
            return
        if task.status.state in _TERMINAL_STATES:
            payload = _task_snapshot_payload(task)
        else:
            payload = _status_update_payload(task)
        await self._dispatch_push(task, payload)

    # === 真流式事件总线（A2A v1.0 SendStreamingMessage/SubscribeToTask，§9.4）===

    def has_stream_subscribers(self, task_id: str) -> bool:
        """该任务是否有活跃流订阅者（F4b-⑯b token 级分块的启用条件）。

        有订阅者=有人实时观看 → LLM 走真流式逐块发射 artifactUpdate；无订阅者走既有
        整段路径（字节零变化）。SendStreamingMessage 先订阅后启动处理，处理期间恒有
        订阅者；SendMessage（非流式）无人观看即无分块。
        """
        return bool(self._stream_subs.get(task_id))

    def subscribe_stream(
        self, task_id: str, *, send_current: bool = False, first_frame_ends: bool = True
    ) -> asyncio.Queue:
        """订阅任务事件流：返回队列（元素=(StreamResponse 载荷, 是否收尾帧)）。

        - 多个订阅者并发共存，同收同序事件（v1.0 明文允许并发流）
        - send_current=True：入队订阅时点全量快照作**首帧**（v1.0 §3.1.2 Task
          lifecycle stream「the stream MUST begin with the Task object」/§3.1.6
          「MUST return a Task object as the first event in the stream, representing
          the current state of the task at the time of subscription」——首帧即订阅
          时点真态，防 GetTask→订阅间丢信息）
        - first_frame_ends：首帧可否单独收流——SubscribeToTask 订阅已终态任务=恰一帧
          即收流（True）；SendStreamingMessage 首帧恒信息帧（False）——处理还在后头，
          收流由处理的终态收尾快照驱动，禁提前收流
        """
        q: asyncio.Queue = asyncio.Queue()
        self._stream_subs.setdefault(task_id, []).append(q)
        if send_current:
            task = self.tasks.get(task_id)
            if task is not None:
                terminal = task.status.state in _TERMINAL_STATES
                q.put_nowait((_task_snapshot_payload(task), first_frame_ends and terminal))
        return q

    def unsubscribe_stream(self, task_id: str, q: asyncio.Queue) -> None:
        """退订（调用方收流/断联时收尾）；空订阅表随删防泄漏。"""
        subs = self._stream_subs.get(task_id)
        if not subs:
            return
        try:
            subs.remove(q)
        except ValueError:
            return
        if not subs:
            self._stream_subs.pop(task_id, None)

    def _emit_stream(self, task: Task, payload: dict, *, end: bool = False) -> None:
        """向该任务全部订阅队列广播（put_nowait 非阻塞，处理本体不受订阅方拖累）。"""
        for q in list(self._stream_subs.get(task.id, ())):
            q.put_nowait((payload, end))

    def emit_stream_end(self, task: Task) -> None:
        """收尾快照（end=True）：流式交互周期收束帧（v1.0 由收流表示终态）。

        失败路径也必发（api._stream_run 兜底）——禁悬挂打开的流。
        """
        self._emit_stream(task, _task_snapshot_payload(task), end=True)

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
        """Create a new task from a user message. metadata=A2A TaskSendParams.metadata。

        contextId 采纳（§3.4.3 Context Continuity「Clients MAY use contextId without
        taskId to start a new task within an existing conversation context」）：客户端
        带 contextId 不带 taskId=在既有会话上下文内开新任务，任务取该 contextId；
        未带则生成新上下文并回填入史消息（与 ensure_accepts_message 推断同语义：
        入史消息携带其任务的 contextId）。
        """
        task = Task(
            status=TaskStatus(state="submitted"),
            history=[message],
            metadata=dict(metadata or {}),
            contextId=message.contextId or "",
        )
        if not message.contextId:
            message.contextId = task.contextId
        self.tasks[task.id] = task
        logger.info(f"Task created: {task.id}")
        return task

    async def ensure_accepts_message(self, task_id: str, message: Message) -> Task:
        """续接准入单一收口（SendMessage/SendStreamingMessage/旧线名共用路由缝）：

        1. 存在性（§3.4.2）：不存在 → TaskNotFoundError(-32001)（与 record_message
           续接 raise 点同码同文案）
        2. contextId 一致性（§3.4.3「Agents MUST reject messages containing
           mismatching contextId and taskId」）：双给且不等 → InvalidParamsError
           (-32602)——修前静默接受越上下文续接（live 修前实证）
        3. contextId 推断（§3.4.3「Agents MUST infer contextId from the task if
           only taskId is provided」）：只给 taskId → 回填 message.contextId=任务
           上下文（入史消息携带其任务的 contextId，与 create_task 回填同语义）
        4. 终态拒收（§3.1.1/§3.1.2 Errors「Messages sent to Tasks that are in a
           terminal state (COMPLETED/FAILED/CANCELED/REJECTED) cannot accept
           further messages」）→ UnsupportedOperationError(-32004)——修前终态任务
           被照常续接重跑（live 修前实证 history 3→4），a2a-python 参考实现
           (#215 "raise error for tasks in terminal states") 同款。

        检查序=存在性→一致性→推断→终态（请求参数一致性先于目标态，同 historyLength
        开流前校验纪律）。终态拒收后多轮正典=§6.3 input-required 续接 / §3.4.3
        contextId 新任务续上下文。
        """
        task = self.tasks.get(task_id)
        if not task:
            raise A2AError(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}")
        if message.contextId and task.contextId and message.contextId != task.contextId:
            raise A2AError(
                ErrorCode.INVALID_PARAMS,
                f"Message contextId {message.contextId!r} does not match task "
                f"contextId {task.contextId!r}",
            )
        if not message.contextId:
            message.contextId = task.contextId
        if task.status.state in _TERMINAL_STATES:
            raise A2AError(
                ErrorCode.UNSUPPORTED_OPERATION,
                f"Task {task.id} is in terminal state: {task.status.state}",
            )
        return task

    def record_message(self, task_id: str, message: Message, metadata: dict | None = None) -> Task:
        """续接消息入史（恰一次）——入史权与处理权分离（F4b-⑧ 入史权拆分，F4b-⑯e）。

        流式续接路由缝须在「入史」与「处理」之间订阅：首帧快照才含刚发出的新
        消息，续接首帧与新任务首帧对称（F4b-⑯e——修前订阅先于入史，续接首帧
        history 缺新消息，而新任务首帧 create_task 已入史=不对称）。非流式续接
        走 process_task=本口+run_processing（契约不变）。agent 回复入史在
        run_processing；错误进 status.message 不入史（F4b-⑧）。
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
        return task

    async def process_task(
        self, task_id: str, message: Message, metadata: dict | None = None
    ) -> Task:
        """Process a task - send message and get response.

        续接语义：新消息入史（恰一次，record_message）+处理（run_processing）。
        新任务首条消息不走本口（create_task 已入史）；流式续接拆开走
        record_message→订阅→run_processing（F4b-⑯e 续接首帧对称化）。
        """
        task = self.record_message(task_id, message, metadata=metadata)
        return await self.run_processing(task, message)

    async def run_processing(self, task: Task, message: Message) -> Task:
        """执行处理并把 agent 回复入史；message 不入史——入史责任在调用方。

        新任务首条消息已由 create_task 入史（history=[message]，A2A §3.1.2 任务
        历史以用户消息开头），处理不得再入史。修前 _send_message_impl 新任务路径
        create_task+process_task 同一消息双 append（live 实证 hist=2 user+1 agent
        =首条用户消息重复记录，多轮上下文也把首条喂两遍）。
        """
        task.status = TaskStatus(state="working")
        await self._notify_push(task)  # 状态迁移即任务更新 → webhook（§4.3.3）
        self._emit_stream(task, _status_update_payload(task))  # 流式：working 迁移事件

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

        await self._notify_push(task)  # 终态（completed/failed）→ {"task"} 全量投递
        self._emit_stream(task, _status_update_payload(task))  # 流式：终态迁移事件
        self.emit_stream_end(task)  # 流式：收尾快照（end）→ 订阅流收束
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
        await self._notify_push(task)  # 取消=终态迁移，同样走推送（§4.3.3）
        self._emit_stream(task, _status_update_payload(task))  # 流式：canceled 迁移事件
        self.emit_stream_end(task)  # 流式：取消即交互周期收束，订阅流收尾
        return task

    def _extract_text(self, message: Message) -> str:
        """Extract text from message parts.

        三形统一读取（part_text，F4b-⑭）：v1.0 平铺 {"text":...} / 0.3 {"kind":"text"} /
        legacy {"type":"text"} 同收。修前只认 type=="text"——v1.0 客户端按 §4.1.6 发
        {"text":...} 被静默丢弃（提取文本为空、prompt 变空=真断点）。
        """
        texts = []
        for part in message.parts:
            text = part_text(part)
            if text is not None:
                texts.append(text)
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
        """AI chat using real LLM proxy or ACP fallback.

        token 级增量（F4b-⑯b）：有活跃流订阅者（has_stream_subscribers）时真流式消费
        上游 SSE，逐 delta 发 TaskArtifactUpdateEvent 分块；无订阅者走既有整段路径
        （字节零变化）。多 choice（F4b-⑰）：主 choice（index 0）=回复文本（history
        契约零变化），非主 choice 一律物化为独立 artifact（name="response-{index}"）
        不静默丢。失败分流以"已发块不可撤回"为界：
        - 发块前失败 → 既有 ACP 降级语义零变化（无痕可撤，连 pending 块也不出）
        - 发块后故障 → LLMStreamError 让任务 failed 可见（禁降级文本与已发流互相矛盾）
        """
        # 离线stub模型（agno全隔离测试范式 + CAMEL"能程序化验证的绝不靠LLM"）：
        # metadata.llm_mode=stub 时零网络确定性应答——集成测试/离线演示不打真实LLM
        if task.metadata.get("llm_mode") == "stub":
            return f"[stub-llm] 收到您的消息：{text}\n\n（确定性离线应答，未调用真实LLM）"
        stream = _ChoiceChunkStreams(self, task) if self.has_stream_subscribers(task.id) else None
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
                # 三形统一读取（part_text，F4b-⑭）：v1.0 客户端消息历史进 LLM 上下文
                # 修前被 type=="text" 过滤吞掉（同 _extract_text 断点）
                text_parts = [t for p in msg.parts if (t := part_text(p)) is not None]
                if text_parts:
                    messages.append({"role": role, "content": "\n".join(text_parts)})

            if stream is not None:
                # 内层预算 20s < run_processing 外层 25s：流式故障在本层先归口
                # （超时→按 committed 分流降级/失败），不落进外层"超时文本当成功回复"面
                return await asyncio.wait_for(
                    self._chat_stream(
                        task, messages, stream, api_key=api_key, base_url=base_url, model=model
                    ),
                    timeout=20.0,
                )

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
                    # 主 choice（choices[0]）回复契约零变化；非主 choice 物化为独立
                    # artifact 不静默丢（F4b-⑰，_materialize_extra_choices）
                    primary = data["choices"][0]["message"]["content"]
                    _materialize_extra_choices(task, data.get("choices") or [])
                    return primary
                else:
                    logger.warning(f"LLM API error {resp.status_code}, falling back to ACP")
                    # 与异常路径同款wait_for——此前这里裸await无界（httpx 120s），
                    # 外部LLM故障时拖穿客户端30s预算（test_chat_fallback ReadTimeout根因）
                    return await asyncio.wait_for(self._handle_acp_chat(text, task), timeout=10.0)

        except Exception as e:
            if stream is not None and stream.committed:
                # 已发分块不可撤回：剩余块 lastChunk 收尾（客户端不悬挂）后如实失败
                stream.close()
                raise LLMStreamError(f"LLM stream interrupted: {e}") from e
            logger.error(f"Chat handler error: {e}")
            try:
                return await asyncio.wait_for(self._handle_acp_chat(text, task), timeout=10.0)
            except TimeoutError:
                return f"收到您的消息：{text}\n\nAI服务超时，请检查服务配置。"

    async def _chat_stream(
        self,
        task: Task,
        messages: list,
        stream: _ChoiceChunkStreams,
        *,
        api_key: str,
        base_url: str,
        model: str,
    ) -> str:
        """真流式消费上游 chat/completions，逐 delta 发 artifactUpdate 分块（F4b-⑯b）。

        按上游真实响应形态消费（F4b-① gland 同纪律，禁猜形态）：
        - text/event-stream → 逐帧按 choice 归集 delta（F4b-⑰：每 choice 独立
          artifact 分块，非主 choice 不静默丢），[DONE] 即止，注释/心跳/非 data 行
          忽略，坏帧如实抛（JSONDecodeError 不吞=禁静默跳块）
        - application/json（上游忽略 stream:true 回整段）→ 各 choice 整段作单块
          （append=False/lastChunk=True）——不硬造伪增量
        - 其余 content-type 如实抛（禁猜形态）；非 200 首字节前如实抛
        返回=主 choice（index 0）逐块拼接全文（chat 单回复语义，history 契约零
        变化；非主 choice 只进各自 artifact）。
        """
        import httpx

        parts: list[str] = []  # 主 choice（index 0）拼接=回复文本
        async with httpx.AsyncClient(timeout=15) as client:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 2048,
                    "stream": True,
                },
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise RuntimeError(f"LLM API error {resp.status_code}: {body[:200]!r}")
                ctype = resp.headers.get("content-type", "")
                if ctype.startswith("text/event-stream"):
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[len("data:") :].strip()
                        if payload == "[DONE]":
                            break
                        if not payload:
                            continue
                        frame = json.loads(payload)  # 坏帧如实抛禁静默跳块
                        for idx, delta in _sse_choice_deltas(frame).items():
                            if idx == 0:
                                parts.append(delta)
                            stream.push(idx, delta)
                elif ctype.startswith("application/json"):
                    obj = json.loads(await resp.aread())
                    # 主 choice 严格提取（畸形如实抛→降级，契约零变化）；非主 choice
                    # 各作单块（F4b-⑰ 不静默丢，_message_choice_texts 同纪律归集）
                    content = obj["choices"][0]["message"]["content"]
                    if isinstance(content, str) and content:
                        parts.append(content)
                        stream.push(0, content)
                    texts = _message_choice_texts(obj.get("choices") or [])
                    texts.pop(0, None)
                    for idx in sorted(texts):
                        stream.push(idx, texts[idx])
                else:
                    raise RuntimeError(f"Unsupported upstream content-type: {ctype!r}")
        stream.close()
        return "".join(parts)

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
