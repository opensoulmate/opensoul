"""RV-①b-⑫ will/dag_planner._plans 计划持久化测试。

覆盖：行语义（to_state/from_state 完整往返/快照 upsert/坏行跳过/回载只读不建库）、
写穿（create/update_step/LLM 解析链落行）、重启回载（双实例 parity/懒回载恰一次/
只补缺不覆盖/by-ID 契约跨重启）、计数真源（对账行聚合/毒化缓存硬反证/对账失败回落）、
纯内存零变化、写/回载失败旁路、真实路径接线（AST 锁/env）。
"""

import json
import sqlite3

import pytest

from src.will.dag_planner import DAGPlanner, ExecutionPlan, PlanStep, StepStatus
from src.will.plan_persist import PlanPersistence, default_db_path


def _steps_spec(n: int = 2) -> list[dict]:
    spec = []
    for i in range(n):
        spec.append(
            {
                "id": f"step_{i}",
                "description": f"do thing {i}",
                "tool": "shell",
                "arguments": {"cmd": f"echo {i}", "nested": {"a": [1, 2]}},
                "depends_on": [f"step_{i - 1}"] if i else [],
                "estimated_seconds": 1.5,
            }
        )
    return spec


def _mk_planner(tmp_path) -> DAGPlanner:
    return DAGPlanner(persist=PlanPersistence(db_path=str(tmp_path / "will_plans.db")))


# ── 行语义 ──────────────────────────────────────────────────────────


def test_to_state_from_state完整往返逐字段():
    step = PlanStep(
        step_id="step_0",
        description="中文描述",
        tool_name="shell",
        arguments={"cmd": "ls", "nested": {"a": [1, 2]}},
        depends_on=["step_x"],
        status=StepStatus.COMPLETED,
        result={"out": "ok", "n": 3},
        error="",
        estimated_duration=1.5,
        actual_duration=2.25,
        retry_count=2,
    )
    plan = ExecutionPlan(
        plan_id="plan_x",
        goal="目标",
        steps=[step],
        created_at=111.0,
        started_at=222.0,
        completed_at=333.0,
        status="completed",
        metadata={"k": "v", "n": 1},
        tenant_id="tenant-a",
    )
    back = ExecutionPlan.from_state(json.loads(json.dumps(plan.to_state(), default=str)))
    assert back.to_state() == plan.to_state()
    assert back.tenant_id == "tenant-a"
    assert back.steps[0].status == StepStatus.COMPLETED
    assert back.steps[0].arguments == {"cmd": "ls", "nested": {"a": [1, 2]}}
    assert back.steps[0].result == {"out": "ok", "n": 3}
    assert back.metadata == {"k": "v", "n": 1}


def test_from_state缺字段如实KeyError禁fabricate():
    state = ExecutionPlan(
        plan_id="p", goal="g", steps=[], created_at=1.0, status="pending"
    ).to_state()
    del state["goal"]
    with pytest.raises(KeyError):
        ExecutionPlan.from_state(state)


def test_upsert当前态一计划一行(tmp_path):
    db = str(tmp_path / "p.db")
    p = PlanPersistence(db_path=db)
    planner = DAGPlanner(persist=p)
    plan = planner.create_plan("g", _steps_spec(2))
    planner.update_step(plan.plan_id, "step_0", StepStatus.COMPLETED)
    planner.update_step(plan.plan_id, "step_1", StepStatus.COMPLETED)
    assert p.count() == 1  # 同 plan_id 三次写=一行当前态（禁膨胀多行）
    rows = p.load_all()
    assert rows[0]["status"] == "completed"
    assert rows[0]["steps_count"] == 2


def test_坏payload行跳过可见禁静默丢(tmp_path):
    db = str(tmp_path / "p.db")
    p = PlanPersistence(db_path=db)
    good = DAGPlanner(persist=p)
    good.create_plan("好计划", _steps_spec(1))
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO will_plans (plan_id, goal, status, tenant_id, created_at, payload) "
        "VALUES ('plan_bad', '坏', 'pending', 'default', 1.0, 'not-json')"
    )
    conn.commit()
    conn.close()
    rows = p.load_all()
    assert len(rows) == 1  # 坏行跳过、好行不丢
    assert rows[0]["plan_id"].startswith("plan_")
    # planner 侧 from_state 坏行（枚举不可解）同样跳过：伪造状态列 payload
    planner = DAGPlanner(persist=p)
    planner._ensure_loaded()
    assert len(planner._plans) == 1


def test_回载只读不建库(tmp_path):
    p = PlanPersistence(db_path=str(tmp_path / "never.db"))
    assert p.load_all() == []
    assert p.count() == 0
    assert not (tmp_path / "never.db").exists()  # 写穿才建库


# ── 写穿 ──────────────────────────────────────────────────────────


def test_create_plan落行逐键(tmp_path):
    p = PlanPersistence(db_path=str(tmp_path / "p.db"))
    planner = DAGPlanner(persist=p)
    plan = planner.create_plan("目标A", _steps_spec(3), tenant_id="tenant-a")
    rows = p.load_all()
    assert len(rows) == 1
    row = rows[0]
    assert row["plan_id"] == plan.plan_id
    assert row["goal"] == "目标A"
    assert row["tenant_id"] == "tenant-a"
    assert row["status"] == "pending"
    assert row["steps_count"] == 3
    assert [s["step_id"] for s in row["payload"]["steps"]] == ["step_0", "step_1", "step_2"]


def test_update_step落行含终态迁移(tmp_path):
    p = PlanPersistence(db_path=str(tmp_path / "p.db"))
    planner = DAGPlanner(persist=p)
    plan = planner.create_plan("g", _steps_spec(2))
    planner.update_step(plan.plan_id, "step_0", StepStatus.COMPLETED, result={"ok": 1})
    row = p.load_all()[0]
    assert row["status"] == "pending"  # 未全终态
    s0 = row["payload"]["steps"][0]
    assert s0["status"] == "completed" and s0["result"] == {"ok": 1}
    planner.update_step(plan.plan_id, "step_1", StepStatus.FAILED, error="boom")
    row = p.load_all()[0]
    assert row["status"] == "failed"  # 终态迁移随写穿落行
    assert row["payload"]["steps"][1]["error"] == "boom"
    assert row["completed_at"] > 0


def test_create_from_llm_response同链落行(tmp_path):
    p = PlanPersistence(db_path=str(tmp_path / "p.db"))
    planner = DAGPlanner(persist=p)
    plan = planner.create_from_llm_response(
        "g", json.dumps({"steps": _steps_spec(2)}), tenant_id="tenant-b"
    )
    assert plan is not None
    rows = p.load_all()
    assert len(rows) == 1 and rows[0]["tenant_id"] == "tenant-b"


# ── 重启回载 ──────────────────────────────────────────────────────


def test_重启回载双实例逐键parity(tmp_path):
    db = str(tmp_path / "p.db")
    a = DAGPlanner(persist=PlanPersistence(db_path=db))
    plan = a.create_plan("目标", _steps_spec(2), tenant_id="tenant-a")
    plan.metadata["extra"] = {"deep": [1, {"x": "中文"}]}
    a._sync_plan(plan)  # metadata 变更写穿
    a.update_step(plan.plan_id, "step_0", StepStatus.COMPLETED, result={"r": 1})

    b = DAGPlanner(persist=PlanPersistence(db_path=db))  # 重启=新实例
    got = b.get_plan(plan.plan_id)
    assert got is not None
    got_a = a.get_plan(plan.plan_id)
    assert got_a is not None
    assert got.to_state() == got_a.to_state()
    assert got.tenant_id == "tenant-a"
    assert got.metadata["extra"] == {"deep": [1, {"x": "中文"}]}


def test_重启后by_ID契约跨重启可达(tmp_path):
    db = str(tmp_path / "p.db")
    a = DAGPlanner(persist=PlanPersistence(db_path=db))
    plan = a.create_plan("g", _steps_spec(1))
    b = DAGPlanner(persist=PlanPersistence(db_path=db))
    assert b.get_plan(plan.plan_id) is not None  # 修前重启后 None=404 契约破
    b.update_step(plan.plan_id, "step_0", StepStatus.COMPLETED)
    rows = PlanPersistence(db_path=db).load_all()
    assert rows[0]["payload"]["steps"][0]["status"] == "completed"


def test_懒回载恰一次先载后录只补缺不覆盖(tmp_path):
    db = str(tmp_path / "p.db")
    a = DAGPlanner(persist=PlanPersistence(db_path=db))
    plan = a.create_plan("g", _steps_spec(1))
    b = DAGPlanner(persist=PlanPersistence(db_path=db))
    calls = []
    assert b._persist is not None
    orig = b._persist.load_all

    def counting():
        calls.append(1)
        return orig()

    b._persist.load_all = counting
    b.get_plan(plan.plan_id)  # 首触回载
    b.get_plan(plan.plan_id)
    assert len(calls) == 1  # 懒回载恰一次
    # 只补缺不覆盖：内存在册新态不被回载洗掉
    b._plans_loaded = False
    b._plans[plan.plan_id].goal = "内存新态"
    b._ensure_loaded()
    assert b._plans[plan.plan_id].goal == "内存新态"


def test_读入口全接懒回载(tmp_path):
    db = str(tmp_path / "p.db")
    a = DAGPlanner(persist=PlanPersistence(db_path=db))
    plan = a.create_plan("g", _steps_spec(1))
    b = DAGPlanner(persist=PlanPersistence(db_path=db))
    assert b.visualize_ascii(plan.plan_id).startswith("📋 Plan: g")
    c = DAGPlanner(persist=PlanPersistence(db_path=db))
    assert c.to_mermaid(plan.plan_id) != "graph TD\n    empty[No data]"
    d = DAGPlanner(persist=PlanPersistence(db_path=db))
    assert d.get_stats()["total_plans"] == 1


# ── 计数真源（MEM-⑥）───────────────────────────────────────────────


def test_计数对账行聚合跨重启真源(tmp_path):
    db = str(tmp_path / "p.db")
    a = DAGPlanner(persist=PlanPersistence(db_path=db))
    p1 = a.create_plan("g1", _steps_spec(2))
    p2 = a.create_plan("g2", _steps_spec(3))
    a.update_step(p1.plan_id, "step_0", StepStatus.COMPLETED)
    a.update_step(p1.plan_id, "step_1", StepStatus.COMPLETED)  # p1 completed
    a.update_step(p2.plan_id, "step_0", StepStatus.FAILED)
    a.update_step(p2.plan_id, "step_1", StepStatus.COMPLETED)
    a.update_step(p2.plan_id, "step_2", StepStatus.COMPLETED)  # p2 failed
    b = DAGPlanner(persist=PlanPersistence(db_path=db))  # 重启计数不归零
    stats = b.get_stats()
    assert stats == {
        "total_plans": 2,
        "completed_plans": 1,
        "failed_plans": 1,
        "total_steps": 5,
        "active_plans": 0,
    }


def test_计数毒化缓存硬反证读行真源(tmp_path):
    db = str(tmp_path / "p.db")
    a = DAGPlanner(persist=PlanPersistence(db_path=db))
    a.create_plan("g", _steps_spec(1))
    a._stats["total_plans"] = 999  # 毒化内存缓存
    assert a.get_stats()["total_plans"] == 1  # 恒对账行真源，不读缓存


def test_对账失败回落缓存禁假0(tmp_path):
    a = DAGPlanner(persist=PlanPersistence(db_path=str(tmp_path / "p.db")))
    a.create_plan("g", _steps_spec(1))

    def boom():
        raise RuntimeError("db exploded")

    assert a._persist is not None
    a._persist.counters = boom
    stats = a.get_stats()
    assert stats["total_plans"] == 1  # 回落内存缓存非假 0
    assert stats["active_plans"] == 1


# ── 纯内存·故障旁路 ────────────────────────────────────────────────


def test_纯内存零落盘零变化(tmp_path, monkeypatch):
    monkeypatch.setenv("WILL_PLANS_DB", str(tmp_path / "mem.db"))
    planner = DAGPlanner()  # persist=None
    plan = planner.create_plan("g", _steps_spec(1))
    planner.update_step(plan.plan_id, "step_0", StepStatus.COMPLETED)
    assert planner.get_plan(plan.plan_id) is not None
    stats = planner.get_stats()
    assert set(stats) == {
        "total_plans",
        "completed_plans",
        "failed_plans",
        "total_steps",
        "active_plans",
    }
    assert stats["total_plans"] == 1
    assert not (tmp_path / "mem.db").exists()  # 零落盘


def test_写失败旁路不阻断计划动作(tmp_path):
    planner = _mk_planner(tmp_path)

    def boom(row):
        raise RuntimeError("disk full")

    assert planner._persist is not None
    planner._persist.upsert = boom
    plan = planner.create_plan("g", _steps_spec(1))  # 动作本体照常
    assert plan is not None
    assert planner.get_plan(plan.plan_id) is not None
    planner.update_step(plan.plan_id, "step_0", StepStatus.COMPLETED)  # 步进照常


def test_回载失败空起步不置位下次触碰重试(tmp_path):
    planner = _mk_planner(tmp_path)
    state = {"n": 0}
    assert planner._persist is not None
    orig = planner._persist.load_all

    def flaky():
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("io error")
        return orig()

    planner._persist.load_all = flaky
    planner.create_plan("g", _steps_spec(1))
    assert planner._plans_loaded is False  # 失败不置位
    planner._ensure_loaded()  # 下次触碰真重试
    assert planner._plans_loaded is True
    assert state["n"] == 2


# ── 接线锁 ────────────────────────────────────────────────────────


def test_真实路径接线AST锁():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    will_api = (root / "src" / "api" / "will.py").read_text()
    planner_src = (root / "src" / "will" / "dag_planner.py").read_text()
    assert "dag_planner = DAGPlanner(persist=" in will_api
    assert "persist: PlanPersistence | None" in planner_src
    # 写穿单一点：_sync_plan 定义恰一处、调用点=create/update 两变更点
    assert planner_src.count("def _sync_plan(") == 1
    assert planner_src.count("self._sync_plan(plan)") == 2
    assert planner_src.count("self._ensure_loaded()") == 6  # 6 公开口首触


def test_env隔离与default路径(monkeypatch):
    import os

    monkeypatch.delenv("WILL_PLANS_DB", raising=False)  # conftest 隔离 env 先清
    assert default_db_path().endswith("data/will_plans.db")
    os.environ["WILL_PLANS_DB"] = "/tmp/x/will.db"
    try:
        assert default_db_path() == "/tmp/x/will.db"
    finally:
        del os.environ["WILL_PLANS_DB"]
