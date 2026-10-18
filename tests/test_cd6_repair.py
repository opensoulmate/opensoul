#!/usr/bin/env python3
"""CD-⑥ 修复候选③——traces events 端点改指真端点 + 同页消费键收编（UI 消费面收编真源键）。

背景（CD-③ 深挖裁决 confirmed_drift→候选③，「一候选一拍」第③件）：
traces/page.tsx:252 `data.events || []` 打在 GET /api/trajectory/sessions/{id}
（真源 session.to_dict() 无 events 键）→ 会话详情展开恒空事件列表（有事件也不
显示）；真源=GET /api/trajectory/sessions/{id}/events（{session_id,events,count}）。

深挖扩面（普查启发式漏收如实记——CD-⑤ 同款：census 只抓 apiFetch 调用点顶层
响应键，setSessions 三元回落形（Array.isArray(data.sessions)?…:[]）不进元素级
消费链，session 元素键漂移零 findings）：
①session 元素 5 键全漂移：session_id/event_count/first_event/last_event/agents
  对真源 TrajectorySession.to_dict()（id/total_events/created_at/ended_at/agent_id）
  → 列表行 React key 全 undefined、事件数/时间全空、Agent 渲染恒空、搜索过滤必炸；
②event 元素键 timestamp 漂移（真源 created_at）→ 事件时间恒 undefined。

收口（UI 消费面收编真源键，真源零改动=CD 系列 wire 权威纪律；GLAND-⑤ 根因修
非 API 加别名堵漏）：
- events 取数改指真端点 /sessions/{id}/events（data.events 真源键在场）；
- TrajectorySession/TrajectoryEvent 接口收编真源形状，消费点全改真源键；
- agents 逗号串→真源 agent_id 单值（真源无多 agent 字段，不编造）。

断言三层（禁自嗨恒真断言）：
①源码消费面：真源键真在场 + 漂移键零残留（消费形，注释剥除）；
②census 工具真跑真仓（scripts/census_contract_drift.py）：traces findings
  全 produced、events 键真映射 get_events 真源、旧端点 get_session 归零；
③行为桥（真 API 全链 tmp 全隔离）：UI 消费的每个键由真端点真产出（list 元素
  /events 元素逐键对账），且旧端点真无 events 键=修前缺陷硬证。
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "traces" / "page.tsx"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd6_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd6_repair"] = cdc
_spec.loader.exec_module(cdc)

# CD-③ 候选③ + 深挖扩面的漂移键（消费形，注释剥除后判定）
DRIFT_SESSION_KEYS = ("session_id", "event_count", "first_event", "last_event", "agents")
DRIFT_EVENT_KEYS = ("timestamp",)

# 真源键（行为桥对账基准）
TRUE_SESSION_KEYS = {
    "id",
    "agent_id",
    "task_description",
    "status",
    "forked_from",
    "fork_point_event_id",
    "total_events",
    "total_tokens",
    "total_duration_ms",
    "tags",
    "created_at",
    "ended_at",
    "tenant_id",
}
TRUE_EVENT_KEYS = {
    "id",
    "session_id",
    "parent_event_id",
    "event_type",
    "agent_id",
    "content",
    "metadata",
    "token_usage",
    "duration_ms",
    "status",
    "created_at",
    "end_at",
    "tenant_id",
}


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """去掉行注释后的真实消费代码（注释里的历史记述不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_events_fetch_targets_true_endpoint():
    """events 取数改指真端点 /sessions/{id}/events（CD-③ 候选③ 本体）。"""
    code = _code()
    assert "apiFetch(`/api/trajectory/sessions/${encodeURIComponent(sessionId)}/events`)" in code, (
        "events 取数未指向真端点 /sessions/{id}/events"
    )


def test_old_detail_endpoint_not_fetched():
    """旧端点（会话详情，无 events 键）在取数面零残留——修前 data.events 打错端点。"""
    code = _code()
    # 以反引号收尾=裸详情端点（events 端点同样含 sessions/${...} 前缀，须区分尾缀）
    assert re.search(r"sessions/\$\{encodeURIComponent\(sessionId\)\}`", code) is None, (
        "旧详情端点取数残留（events 键打在无 events 键的端点上=修前缺陷）"
    )
    assert code.count("apiFetch(`/api/trajectory/sessions/") == 1, (
        "traces 页 sessions 取数点数量异常（列表用 query 形不带尾斜杠，"
        "带尾斜杠应恰 1=events 真端点）"
    )


def test_session_element_consumes_true_keys():
    """session 元素收编真源键：id/total_events/created_at/ended_at/agent_id 真被消费。"""
    code = _code()
    for expr in (
        "session.id",
        "session.total_events",
        "session.created_at",
        "session.ended_at",
        "session.agent_id",
    ):
        assert expr in code, f"真源 session 键未被消费: {expr}"


def test_session_drift_keys_absent():
    """session 5 个漂移键消费形零残留（修前列表行 key 全 undefined、过滤必炸）。

    接口声明检查只看 TrajectorySession 段（TrajectoryEvent 的 session_id 是真源
    event 键，不得误伤——判定按接口分段禁全文扫）。
    """
    code = _code()
    seg = code[code.index("interface TrajectorySession") : code.index("interface TrajectoryEvent")]
    for key in DRIFT_SESSION_KEYS:
        for pat in (rf"\bsession\.{key}\b", rf"\bs\.{key}\b", rf"\bsess\.{key}\b"):
            assert re.search(pat, code, re.M) is None, f"session 漂移键残留: {key} ({pat})"
        assert re.search(rf"^\s+{key}: (?:string|number);", seg, re.M) is None, (
            f"TrajectorySession 接口漂移键残留: {key}"
        )


def test_event_element_consumes_true_keys():
    """event 元素收编真源键 created_at（修前 timestamp 恒 undefined）。"""
    code = _code()
    assert "evt.created_at" in code, "真源 event 键 created_at 未被消费"
    seg = code[code.index("interface TrajectoryEvent") : code.index("interface SessionDetail")]
    for key in DRIFT_EVENT_KEYS:
        assert re.search(rf"\bevt\.{key}\b", code) is None, f"event 漂移键残留: {key}"
        assert re.search(rf"^\s+{key}: string;", seg, re.M) is None, (
            f"TrajectoryEvent 接口漂移键残留: {key}"
        )


def test_agent_consumption_single_id():
    """Agent 渲染收编真源 agent_id 单值（真源无逗号串多 agent 字段，禁 split 编造）。"""
    code = _code()
    assert "session.agent_id ? [session.agent_id] : []" in code
    assert ".split(',')" not in code, "agents 逗号串 split 残留（真源无此形态）"


# ── ② census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_traces_all_produced(census_report):
    """修后 traces findings 全 produced（修前 events 键 unresolved_shape 挂 get_session）。"""
    tr = [f for f in census_report["findings"] if f["page"].endswith("traces/page.tsx")]
    assert tr, "traces findings 为空=提取失效断言空转（修前有 3 条）"
    bad = [(f["key"], f["verdict"], f["handler"]) for f in tr if f["verdict"] != "produced"]
    assert not bad, f"traces 残留非 produced findings: {bad}"


def test_census_events_key_mapped_to_true_handler(census_report):
    """events 键真映射 get_events 真源（{session_id,events,count}），非 get_session。"""
    tr = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("traces/page.tsx") and list(f["key"]) == ["events"]
    ]
    assert len(tr) == 1, f"events 键 findings 数异常: {len(tr)}"
    f = tr[0]
    assert f["handler"] == "trajectory.get_events:268", f"events 键端点映射漂移: {f['handler']}"
    assert "events" in f["produced"] and f["path"].endswith("/events"), (
        f"events 键未映射真端点真源: {f['path']} produced={f['produced']}"
    )


def test_census_old_endpoint_attribution_gone(census_report):
    """旧端点 get_session 归零——events 键不再打在无 events 键的会话详情端点上。"""
    tr = [
        f
        for f in census_report["findings"]
        if f["page"].endswith("traces/page.tsx") and f["handler"] == "trajectory.get_session:166"
    ]
    assert not tr, f"events 键仍挂旧端点 get_session: {tr}"


# ── ③ 行为桥（真 API 全链，tmp 全隔离绝不写生产库）──────────────


USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    traj_db = str(tmp_path / "opensoul.db")
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    from src.config import settings
    from src.database.postgres import EphemeralSQLitePool

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{traj_db}")
    import src.trajectory.store as store_mod

    monkeypatch.setattr(store_mod, "db_pool", EphemeralSQLitePool(db_path=traj_db))

    import src.api.trajectory as trajectory_api

    app = FastAPI()
    app.include_router(trajectory_api.router, prefix="/api/trajectory")
    return TestClient(app)


def _seed(client) -> tuple[dict, list[dict]]:
    r = client.post(
        "/api/trajectory/sessions",
        json={"agent_id": "agent-cd6", "task_description": "cd6 probe task"},
    )
    assert r.status_code == 200, r.text
    sess = r.json()
    events = []
    for content in ("cd6 step one", "cd6 step two"):
        er = client.post(
            f"/api/trajectory/sessions/{sess['id']}/events",
            json={"event_type": "tool_call", "agent_id": "agent-cd6", "content": content},
        )
        assert er.status_code == 200, er.text
        events.append(er.json())
    return sess, events


def test_list_element_keys_cover_ui_consumption(env):
    """行为桥：UI 消费的每个 session 键 GET /sessions 列表元素真产出。"""
    _seed(env)
    data = env.get("/api/trajectory/sessions").json()
    assert data["sessions"], "真源列表为空=断言空转"
    element = data["sessions"][0]

    code = _code()
    consumed = set(re.findall(r"\bsession\.([A-Za-z_$][\w$]*)", code))
    seg = code[code.index("const filtered") : code.index("const totalPages")]
    consumed |= set(re.findall(r"\bs\.([A-Za-z_$][\w$]*)", seg))
    consumed |= set(re.findall(r"\bsess\.([A-Za-z_$][\w$]*)", code))
    assert {"id", "total_events", "created_at", "agent_id"} <= consumed, (
        f"提取失效=断言空转: {consumed}"
    )
    for k in consumed:
        assert k in element, f"UI 消费 session 键真源不产出: {k}"
        assert k in TRUE_SESSION_KEYS
    assert isinstance(element["total_events"], int)
    assert element["agent_id"] == "agent-cd6"


def test_events_endpoint_element_keys_cover_ui_consumption(env):
    """行为桥：UI 消费的每个 event 键 GET /sessions/{id}/events 真产出（真源形逐键）。"""
    sess, _ = _seed(env)
    data = env.get(f"/api/trajectory/sessions/{sess['id']}/events").json()
    assert set(data) == {"session_id", "events", "count"}, f"events 真源键集漂移: {set(data)}"
    assert data["count"] == 2 and len(data["events"]) == 2
    element = data["events"][0]

    code = _code()
    consumed = set(re.findall(r"\bevt\.([A-Za-z_$][\w$]*)", code))
    assert {"created_at", "event_type", "content"} <= consumed, f"提取失效=断言空转: {consumed}"
    for k in consumed:
        assert k in element, f"UI 消费 event 键真源不产出: {k}"
        assert k in TRUE_EVENT_KEYS
    assert isinstance(element["created_at"], str) and element["created_at"]


def test_old_detail_endpoint_has_no_events_key(env):
    """修前缺陷硬证：旧端点 GET /sessions/{id} 真源无 events 键（data.events 恒 [] 根因）。"""
    sess, _ = _seed(env)
    detail = env.get(f"/api/trajectory/sessions/{sess['id']}").json()
    assert "events" not in detail, "真源突现 events 键——CD-③ 证据锁失效须复挖"
    assert set(detail) <= TRUE_SESSION_KEYS, (
        f"session 真源键集漂移: {set(detail) - TRUE_SESSION_KEYS}"
    )
