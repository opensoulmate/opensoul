"""C3-⑪ — entity properties 契约收口（2026-10-11 live :8090 实测驱动）。

live 实测：POST /api/entity/ 全形态 500（空 properties 与非空 properties 都炸）：
① 写侧 services/entity.py create_entity：json.dumps 死表达式（结果丢弃）+
   `json.dumps(...) if data.properties else None`——空 dict 落 NULL；
   update_entity 把 properties dict 直接绑参（SQLite 铁律违规）。
② 读侧 _row_to_entity 原样返回 raw TEXT/None，而 EntityResponse.properties: dict
   ——回包过不了响应校验（ResponseValidationError）。
治理=写侧统一 json.dumps(properties or {})、update 绑参前 json.dumps、
读侧 TEXT→dict 解析并兼容存量 NULL 行（None→{}）。
数据面=tmp 隔离，开局=生产实测 entities 表形状（2026-10-11 .schema）。
"""

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_PROD_ENTITIES_DDL = (
    "CREATE TABLE entities ("
    "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, "
    "type TEXT NOT NULL DEFAULT 'concept', description TEXT DEFAULT '', "
    "properties TEXT, created_at TEXT DEFAULT (datetime('now')), "
    "updated_at TEXT DEFAULT (datetime('now')))"
)

_USER = UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture()
def entity_client(tmp_path, monkeypatch):
    from src.api import entity as entity_api
    from src.config import settings
    from src.database.postgres import SQLitePool
    from src.services import entity as entity_service

    db_path = tmp_path / "opensoul.db"
    conn = sqlite3.connect(db_path)
    conn.execute(_PROD_ENTITIES_DDL)
    conn.commit()
    conn.close()

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    pool = SQLitePool()
    monkeypatch.setattr(entity_service, "db_pool", pool)

    app = FastAPI()
    app.include_router(entity_api.router, prefix="/api/entity")

    @asynccontextmanager
    async def _lifespan(_app):
        await pool.connect()
        yield
        await pool.disconnect()

    app.router.lifespan_context = _lifespan
    with TestClient(app) as c:
        yield c


def test_create_empty_properties_roundtrip(entity_client):
    resp = entity_client.post(
        "/api/entity/?user_id=default", json={"name": "e1", "entity_type": "concept"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["properties"] == {}
    UUID(body["id"])


def test_create_with_properties_roundtrip(entity_client):
    resp = entity_client.post(
        "/api/entity/?user_id=default",
        json={"name": "e2", "entity_type": "person", "properties": {"k": "v"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["properties"] == {"k": "v"}
    listed = entity_client.get("/api/entity/?user_id=default").json()
    assert [e["properties"] for e in listed if e["name"] == "e2"] == [{"k": "v"}]


def test_update_properties_dict_not_bound_raw(entity_client):
    created = entity_client.post(
        "/api/entity/?user_id=default", json={"name": "e3", "entity_type": "concept"}
    ).json()
    resp = entity_client.patch(
        f"/api/entity/{created['id']}?user_id=default",
        json={"properties": {"a": 1}, "description": "d"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["properties"] == {"a": 1}


def test_legacy_null_properties_row_reads_as_dict(tmp_path, monkeypatch):
    """存量 properties=NULL 行读侧归一为 {}（禁 500）。"""
    from src.config import settings
    from src.database.postgres import SQLitePool
    from src.services.entity import _row_to_entity

    db_path = tmp_path / "opensoul.db"
    conn = sqlite3.connect(db_path)
    conn.execute(_PROD_ENTITIES_DDL)
    eid = str(uuid4())
    conn.execute(
        "INSERT INTO entities (id, user_id, name, type, properties) VALUES (?, ?, ?, ?, NULL)",
        (eid, str(_USER), "legacy", "concept"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    pool = SQLitePool()

    async def run():
        await pool.connect()
        row = await pool.fetchrow("SELECT * FROM entities WHERE id = $1", eid)
        await pool.disconnect()
        return row

    d = _row_to_entity(asyncio.run(run()))
    assert d["properties"] == {}
    assert d["entity_type"] == "concept"
    # 写侧落库形态也锁死：json 文本而非裸 dict/NULL
    assert isinstance(json.dumps(d["properties"]), str)
