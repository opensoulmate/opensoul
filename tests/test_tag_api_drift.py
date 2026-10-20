"""C3-⑪ — tags 功能真断点收口（2026-10-11 live :8090 实测驱动）。

live 实测链条（本测试逐环锁死）：
① tags 表缺 color 列（knowledge/feedback/vein/capture 建的形状
   id/user_id/name/created_at），而 api/tag.py 增改读全用 color——
   "no column named color" 建/改标签必炸（live POST 500）。治理=C1 同款
   缺列自愈（SQLitePool._ensure_schema 的 _SCHEMA_FIXES 补 color，
   默认值取 models/tag.py TagBase.color 同值 '#6366f1'）。
② api/tag.py create 原不带 id（依赖 PG 端 id 默认），而 SQLite 的
   TEXT PRIMARY KEY 允许 NULL（SQLite 怪癖）→ id=None 落库，
   TagResponse.id UUID 校验炸（live 实测 id=None + ResponseValidationError）。
   治理=写路径显式 str(uuid4())（双库同义，过 SQLite UUID 绑参铁律）。
③ GET /api/tags/ 的 user_id 是必填 UUID 查询参数（前端 tags 页原调用缺参
   →422 整页废），前端调用面补 ?user_id=…（与 entity 页同 API 调用面一致）。
   本测试锁契约：缺参=422，带参=按 user 过滤。

数据面=tmp 隔离（绝不写生产 data/opensoul.db）；开局=生产实测漂移 DDL
原样复刻（2026-10-11 sqlite3 data/opensoul.db .schema，heal 前无 color）。
"""

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── 生产实测漂移schema原样复刻（2026-10-11 .schema实测DDL，heal前无color）──

_PROD_TAGS_DDL = (
    "CREATE TABLE tags ("
    "id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL, "
    "name TEXT NOT NULL, "
    "created_at TEXT DEFAULT (datetime('now')))"
)

_PROD_KNOWLEDGE_TAGS_DDL = (
    "CREATE TABLE knowledge_tags ("
    "knowledge_id TEXT NOT NULL, "
    "tag_id TEXT NOT NULL, "
    "PRIMARY KEY (knowledge_id, tag_id))"
)

_USER = "00000000-0000-0000-0000-000000000000"
_OTHER_USER = "00000000-0000-0000-0000-000000000001"


def _build_drift_db(db_path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(_PROD_TAGS_DDL)
    conn.execute(_PROD_KNOWLEDGE_TAGS_DDL)
    conn.commit()
    conn.close()


def _columns(db_path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    return cols


@pytest.fixture()
def tag_client(tmp_path, monkeypatch):
    from src.api import tag as tag_api
    from src.config import settings
    from src.database.postgres import SQLitePool

    db_path = tmp_path / "opensoul.db"
    _build_drift_db(db_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    pool = SQLitePool()
    monkeypatch.setattr(tag_api, "db_pool", pool)

    app = FastAPI()
    app.include_router(tag_api.router, prefix="/api/tags")

    @asynccontextmanager
    async def _lifespan(_app):
        await pool.connect()
        yield
        await pool.disconnect()

    app.router.lifespan_context = _lifespan
    with TestClient(app) as c:
        yield c


# ── ① 控制组：漂移形状下原调用必炸（证明断点真实存在）────────────────


def test_control_missing_color_breaks_create(tmp_path):
    db_path = tmp_path / "ctrl.db"
    _build_drift_db(db_path)
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.OperationalError, match="no column named color"):
        conn.execute(
            "INSERT INTO tags (name, color, user_id) VALUES (?, ?, ?)",
            ("x", "#22c55e", _USER),
        )
    conn.close()


# ── ① 自愈 + 真断点恢复 ──────────────────────────────────────────────


def test_color_column_healed_on_connect(tag_client):
    resp = tag_client.post(
        f"/api/tags/?user_id={_USER}",
        json={"name": "heal-probe", "color": "#22c55e"},
    )
    assert resp.status_code == 200, resp.text


def test_color_heal_idempotent_reconnect(tmp_path, monkeypatch):
    from src.config import settings
    from src.database.postgres import SQLitePool

    db_path = tmp_path / "opensoul.db"
    _build_drift_db(db_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")

    async def run():
        for _ in range(2):
            pool = SQLitePool()
            await pool.connect()
            await pool.disconnect()

    asyncio.run(run())
    assert "color" in _columns(db_path, "tags")
    # 默认值取 TagBase.color 同值（存量行/无色插入都落模型默认色）
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO tags (id, name, user_id) VALUES (?, ?, ?)", (str(uuid4()), "n", _USER)
    )
    conn.commit()
    (color,) = conn.execute("SELECT color FROM tags WHERE name='n'").fetchone()
    conn.close()
    assert color == "#6366f1"


# ── ② create 显式 id：真 UUID 落库 + color 往返 ───────────────────────


def test_create_returns_real_uuid_id_and_color_roundtrip(tag_client):
    resp = tag_client.post(
        f"/api/tags/?user_id={_USER}",
        json={"name": "c3-tag", "color": "#22c55e"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # id 不为 None 且是合法 UUID（修前 id=None 直接炸响应校验）
    assert body["id"] is not None
    UUID(body["id"])
    assert body["color"] == "#22c55e"
    assert body["usage_count"] == 0

    listed = tag_client.get(f"/api/tags/?user_id={_USER}").json()
    assert [t["id"] for t in listed] == [body["id"]]
    assert listed[0]["color"] == "#22c55e"


def test_patch_delete_roundtrip(tag_client):
    created = tag_client.post(
        f"/api/tags/?user_id={_USER}",
        json={"name": "patch-me", "color": "#22c55e"},
    ).json()
    tid = created["id"]

    patched = tag_client.patch(f"/api/tags/{tid}?user_id={_USER}", json={"color": "#ef4444"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["color"] == "#ef4444"

    deleted = tag_client.delete(f"/api/tags/{tid}?user_id={_USER}")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert tag_client.get(f"/api/tags/?user_id={_USER}").json() == []


# ── ③ user_id 契约（F3b-⑮ 身份轴收口契约随更新，历史裁决文本不改写）─────
# 勘误（2026-10-20 F3b-⑮）：C3-⑪ 时代契约「缺参=422」已随身份轴收口更新——
# 缺参=未声明→默认桶 200（F3b-⑭ knowledge 面同款既有行为变更），带参=按
# user 过滤（匿名保留自报）语义零变化。修前契约由控制组（worktree 旧码）锁证。


def test_user_id_query_contract_and_filter(tag_client):
    tag_client.post(f"/api/tags/?user_id={_USER}", json={"name": "mine", "color": "#22c55e"})
    # 缺参=未声明默认桶（修前 422）：UID 档案行不进默认桶
    missing = tag_client.get("/api/tags/")
    assert missing.status_code == 200
    assert missing.json() == []

    tag_client.post(
        f"/api/tags/?user_id={_OTHER_USER}", json={"name": "theirs", "color": "#22c55e"}
    )
    mine = tag_client.get(f"/api/tags/?user_id={_USER}").json()
    theirs = tag_client.get(f"/api/tags/?user_id={_OTHER_USER}").json()
    assert [t["name"] for t in mine] == ["mine"]
    assert [t["name"] for t in theirs] == ["theirs"]
