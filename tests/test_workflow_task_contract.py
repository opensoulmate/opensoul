"""2026-10-11 14:05 周期 — workflow 建任务 500 真断点收口回归（Gate3 live 实测驱动）。

live 实测链条（本测试逐环锁死）：
① api/workflow.py create_task 把 WorkflowTaskCreate.config（dict）直接绑参进
   SQLite → sqlite3.ProgrammingError: Error binding parameter 6: type 'dict'
   is not supported（live POST /api/workflow/tasks 必 500，前端 workflow 页
   "新建任务"真实操作路径全废）。治理=写路径 json.dumps(req.config)
   （SQLite json.dumps 铁律）+ str(task_id)（UUID 绑参铁律）。
② 读侧 list/create 把 config 的 TEXT 原样回给前端，而前端详情弹窗
   Object.keys(selectedTask.config)、编辑表单 JSON.stringify(task.config)
   都要求 dict（WorkflowTaskResponse.config: dict 契约）——治理=_task_row
   读侧 json.loads（契约三侧收齐：模型 dict/写 dumps/读 loads）。
   遗留 TEXT 行（存量/手写 JSON/坏 JSON）读侧归一为 dict 不炸。

数据面=tmp 隔离（绝不写生产 data/opensoul.db）；DDL=生产实测 .schema 原样
（含 ALTER 补列 task_type/schedule/last_run_at/next_run_at 形状）。
"""

import json
import sqlite3
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PROD_WORKFLOW_DDL = (
    "CREATE TABLE workflow_tasks ("
    "id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL, "
    "name TEXT NOT NULL, "
    "description TEXT DEFAULT '', "
    "status TEXT DEFAULT 'pending', "
    "config TEXT DEFAULT '{}', "
    "created_at TEXT DEFAULT (datetime('now')), "
    "updated_at TEXT DEFAULT (datetime('now')), "
    "task_type TEXT DEFAULT 'manual', "
    "schedule TEXT, "
    "last_run_at TEXT, "
    "next_run_at TEXT)"
)

_USER = "00000000-0000-0000-0000-000000000000"


@pytest.fixture()
def wf_client(tmp_path, monkeypatch):
    from src.api import workflow as wf_api
    from src.config import settings
    from src.database.postgres import SQLitePool
    from src.middleware.auth import get_current_user

    db_path = tmp_path / "opensoul.db"
    conn = sqlite3.connect(db_path)
    conn.execute(_PROD_WORKFLOW_DDL)
    conn.commit()
    conn.close()

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    pool = SQLitePool()
    monkeypatch.setattr(wf_api, "db_pool", pool)

    app = FastAPI()
    app.include_router(wf_api.router, prefix="/api/workflow")
    app.dependency_overrides[get_current_user] = lambda: {"id": _USER}

    @asynccontextmanager
    async def _lifespan(_app):
        await pool.connect()
        yield
        await pool.disconnect()

    app.router.lifespan_context = _lifespan
    with TestClient(app) as c:
        yield c


# ── 控制组：dict 直绑必炸（证明断点真实存在，非臆测）────────────────


def test_control_dict_param_binding_breaks(tmp_path):
    db_path = tmp_path / "ctrl.db"
    conn = sqlite3.connect(db_path)
    conn.execute(_PROD_WORKFLOW_DDL)
    conn.commit()
    with pytest.raises(sqlite3.ProgrammingError, match="type 'dict' is not supported"):
        conn.execute(
            "INSERT INTO workflow_tasks (id, user_id, name, config) VALUES (?, ?, ?, ?)",
            ("t1", _USER, "x", {"a": 1}),
        )
    conn.close()


# ── ① 写路径：dict config 真创建、真往返（修前 500）──────────────────


def test_create_with_dict_config_returns_dict(wf_client):
    resp = wf_client.post(
        "/api/workflow/tasks",
        json={
            "name": "t-contract",
            "description": "",
            "task_type": "manual",
            "schedule": None,
            "config": {"proof": True, "nested": {"k": [1, 2]}},
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert isinstance(data["config"], dict)
    assert data["config"] == {"proof": True, "nested": {"k": [1, 2]}}
    # id 落库为合法 UUID 字符串（str(task_id) 铁律）
    UUID(data["id"])


def test_create_empty_config_defaults_dict(wf_client):
    resp = wf_client.post("/api/workflow/tasks", json={"name": "t-empty"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["config"] == {}


def test_create_delete_roundtrip(wf_client):
    created = wf_client.post("/api/workflow/tasks", json={"name": "t-del", "config": {"a": 1}})
    assert created.status_code == 201, created.text
    tid = created.json()["id"]
    deleted = wf_client.delete(f"/api/workflow/tasks/{tid}")
    assert deleted.status_code == 200, deleted.text
    listed = wf_client.get("/api/workflow/tasks").json()
    assert all(t["id"] != tid for t in listed)


# ── ② 读侧：TEXT config 归一为 dict（前端 Object.keys/JSON.stringify 契约）──


def test_list_normalizes_text_config_rows(wf_client):
    # 存量行：config 是 TEXT JSON（迁移前形状）
    wf_client.post("/api/workflow/tasks", json={"name": "t-new", "config": {"x": 1}})
    listed = wf_client.get("/api/workflow/tasks").json()
    assert all(isinstance(t["config"], dict) for t in listed)
    assert any(t["config"] == {"x": 1} for t in listed)


def test_list_tolerates_invalid_json_config(wf_client, tmp_path, monkeypatch):
    # 手写坏 JSON 行：读侧归一为 {} 禁炸
    from src.api import workflow as wf_api

    conn = sqlite3.connect(str(tmp_path / "opensoul.db"))
    conn.execute(
        "INSERT INTO workflow_tasks (id, user_id, name, config) VALUES (?, ?, ?, ?)",
        ("bad-json-row", _USER, "bad", "{not json"),
    )
    conn.commit()
    conn.close()
    listed = wf_client.get("/api/workflow/tasks").json()
    row = next(t for t in listed if t["id"] == "bad-json-row")
    assert row["config"] == {}


def test_list_null_config_becomes_dict(wf_client):
    from src.api import workflow as wf_api  # noqa: F401  (fixture target)

    resp = wf_client.get("/api/workflow/tasks")
    assert resp.status_code == 200
    assert all(isinstance(t["config"], dict) for t in resp.json())
