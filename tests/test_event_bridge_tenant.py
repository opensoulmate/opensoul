"""F3b-⑨b-① event_bridge tenant_id 透传——桥本体（emit/push_event）可选租户键。

修前（F3b-⑨ 审计）：event_bridge emit/push_event 构造的事件 dict 无 tenant_id 键
→ TimelineStore.record() 'default' 兜底（timeline/store.py），非 default 租户身份
用户的动作事件落 'default'→被自己的租户过滤查询挡掉=自动作自我不可见。

本拍=桥本体最小件：emit/push_event 增可选 tenant_id 形参→事件 dict 透传→
record 行归属。调用点不传行为字节不变（'default' 兜底延续）——为 ②（API 调用点
按 organ 模块分批接 bind_identity 派生）铺路。

数据面全 tmp（TimelineStore db_path 注入），绝不写生产 timeline.db。
"""

import asyncio
import json
import sqlite3

import httpx
import pytest

from src.nerve import event_bridge
from src.timeline.store import TimelineStore


def _rows(db_path: str) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute("SELECT * FROM events ORDER BY timestamp, event_id")]
    con.close()
    return out


@pytest.fixture()
def bridge_env(tmp_path, monkeypatch):
    """tmp TimelineStore 注入 emit/push_event 缓存 + 清空 event_stream 环形缓冲。"""
    db = str(tmp_path / "timeline.db")
    store = TimelineStore(db)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge.push_event, "_timeline", store, raising=False)

    from src.api import event_stream

    event_stream._event_buffer.clear()
    yield db, event_stream
    event_stream._event_buffer.clear()


class _NerveSpy:
    """MockTransport 捕获 nerve publish 请求体（emit 步骤 1 的 bus 载荷）。"""

    def __init__(self):
        self.bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


# ── 纯函数：_tenant_key 注入口 ─────────────────────────────────


def test_tenant_key_injects_when_provided():
    assert event_bridge._tenant_key("tenant-abc") == {"tenant_id": "tenant-abc"}


def test_tenant_key_empty_when_absent_or_blank():
    # 未提供/空白=不注入键（行为字节不变的构造基础）
    assert event_bridge._tenant_key(None) == {}
    assert event_bridge._tenant_key("") == {}
    assert event_bridge._tenant_key("   ") == {}


def test_tenant_key_str_normalizes():
    # SQLite 铁律：UUID/任意参数 str() 归一
    assert event_bridge._tenant_key(123) == {"tenant_id": "123"}  # type: ignore[arg-type]


# ── emit：租户键透传到 record 行归属 ──────────────────────────


def test_emit_without_tenant_falls_back_default(bridge_env):
    """调用点不传行为字节不变：行归 'default'，事件 dict 不带 tenant_id 键（硬反证）。"""
    db, event_stream = bridge_env
    spy = _NerveSpy()

    async def run():
        async with spy.client() as c:
            return await event_bridge.emit("soul", "t_default", "no tenant", {"k": 1}, _client=c)

    asyncio.run(run())
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"
    # 缓冲事件 dict 零新增键（行为字节不变）
    buffered = list(event_stream._event_buffer)
    assert len(buffered) == 1
    assert "tenant_id" not in buffered[0]
    # bus 载荷同样不带键
    assert "tenant_id" not in spy.bodies[0]["data"]


def test_emit_with_tenant_records_row(bridge_env):
    db, event_stream = bridge_env
    spy = _NerveSpy()

    async def run():
        async with spy.client() as c:
            return await event_bridge.emit(
                "vein",
                "t_tenant",
                "with tenant",
                {"k": 2},
                tenant_id="tenant-e2e00001",
                _client=c,
            )

    asyncio.run(run())
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "tenant-e2e00001"
    # 事件 dict 自带键（buffer/record/bus 三面一致）
    buffered = list(event_stream._event_buffer)
    assert buffered[0]["tenant_id"] == "tenant-e2e00001"
    assert spy.bodies[0]["data"]["tenant_id"] == "tenant-e2e00001"


def test_emit_blank_tenant_falls_back_default(bridge_env):
    """键派生故障（空白）绝不吃掉落库：行照落 'default' 兜底，dict 不带键。"""
    db, event_stream = bridge_env
    spy = _NerveSpy()

    async def run():
        async with spy.client() as c:
            return await event_bridge.emit(
                "mind", "t_blank", "blank tenant", {}, tenant_id="  ", _client=c
            )

    asyncio.run(run())
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"
    buffered = list(event_stream._event_buffer)
    assert "tenant_id" not in buffered[0]


def test_emit_two_tenants_rows_isolated(bridge_env):
    """两租户各落各的行键（② 接线后查询面隔离的前提）。"""
    db, _ = bridge_env
    spy = _NerveSpy()

    async def run():
        async with spy.client() as c:
            await event_bridge.emit(
                "echo", "t_iso_a", "A action", {}, tenant_id="tenant-a", _client=c
            )
            await event_bridge.emit(
                "echo", "t_iso_b", "B action", {}, tenant_id="tenant-b", _client=c
            )

    asyncio.run(run())
    by_type = {r["event_type"]: r["tenant_id"] for r in _rows(db)}
    assert by_type == {"t_iso_a": "tenant-a", "t_iso_b": "tenant-b"}


# ── push_event：显式参数注入口 ────────────────────────────────


def test_push_event_param_sets_key(bridge_env):
    db, event_stream = bridge_env
    event = {"id": "evt_pe_set", "organ": "link", "type": "t_pe_set", "summary": "s"}
    event_bridge.push_event(event, tenant_id="tenant-pe1")
    rows = _rows(db)
    assert rows[0]["tenant_id"] == "tenant-pe1"
    assert event["tenant_id"] == "tenant-pe1"  # dict 自带键（record 同源）


def test_push_event_param_overrides_dict_key(bridge_env):
    """显式参数=身份真源，覆盖 dict 同名键（防构造处手抄漂移双源）。"""
    db, _ = bridge_env
    event = {
        "id": "evt_pe_override",
        "organ": "link",
        "type": "t_pe_override",
        "summary": "s",
        "tenant_id": "stale-value",
    }
    event_bridge.push_event(event, tenant_id="tenant-truth")
    rows = _rows(db)
    assert rows[0]["tenant_id"] == "tenant-truth"


def test_push_event_dict_key_passes_through(bridge_env):
    """未提供参数：事件自带键原样透传不洗掉。"""
    db, _ = bridge_env
    event = {
        "id": "evt_pe_passthrough",
        "organ": "link",
        "type": "t_pe_pt",
        "summary": "s",
        "tenant_id": "tenant-y",
    }
    event_bridge.push_event(event)
    rows = _rows(db)
    assert rows[0]["tenant_id"] == "tenant-y"


def test_push_event_no_key_anywhere_falls_back_default(bridge_env):
    """调用点不传行为字节不变：dict 不被塞键，行归 'default' 兜底。"""
    db, _ = bridge_env
    event = {"id": "evt_pe_none", "organ": "link", "type": "t_pe_none", "summary": "s"}
    event_bridge.push_event(event)
    assert "tenant_id" not in event  # 行为字节不变硬反证
    rows = _rows(db)
    assert rows[0]["tenant_id"] == "default"


def test_push_event_blank_param_untouched(bridge_env):
    db, _ = bridge_env
    event = {"id": "evt_pe_blank", "organ": "link", "type": "t_pe_blank", "summary": "s"}
    event_bridge.push_event(event, tenant_id="   ")
    assert "tenant_id" not in event
    rows = _rows(db)
    assert rows[0]["tenant_id"] == "default"
