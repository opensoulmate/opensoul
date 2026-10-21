"""brain 记忆面删除口（MEM-③）测试——DELETE /api/brain/memories/{memory_id}。

MEM-② 诚实边界①留档「brain 记忆面=进程内 MemoryStore 无删除口（删除口另拍）」：
修前 store.delete() 在 brain 面零 API 消费点（wired-but-no-consumer）——记忆只能
进不能出，hippo /memories CRUD 是独立 store 实例删不到 brain 面的行。

语义与 F3b-㉛/⑳ 逐字：认证=派生、自报只可确认绑定、矛盾=403「user mismatch」、
'default'/空/占位零 UUID（NIL_UUID）=未声明→取派生、匿名=保留自报（既有本地
多档案语义契约零变化）、坏 token=401 零副作用；by-ID 归属闸越权=与缺行同契约
404 且行存活（禁存在性探测），闸读走 peek 零副作用（被拒请求行零改写）。

数据面=tmp 全隔离（USERS_DB/ENTERPRISE_DB/tmp SQLitePool/brain store·sessions
换 tmp 实例），绝不写生产库。
"""

import ast
import sqlite3
import uuid
from pathlib import Path

import pytest

NIL_UUID = "00000000-0000-0000-0000-000000000000"
UID_A = "11111111-1111-4111-8111-111111111111"
UID_B = "22222222-2222-4222-8222-222222222222"

_USERS_DDL = (
    "CREATE TABLE IF NOT EXISTS users ("
    "id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "email TEXT DEFAULT '', password_hash TEXT NOT NULL, "
    "role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1, "
    "tenant_id TEXT DEFAULT 'default', "
    "created_at TEXT DEFAULT (datetime('now')), "
    "updated_at TEXT DEFAULT (datetime('now')));"
)

_ENTERPRISE_DDL = (
    "CREATE TABLE IF NOT EXISTS users ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "username TEXT UNIQUE NOT NULL, "
    "password TEXT NOT NULL, "
    "is_active INTEGER NOT NULL DEFAULT 1, "
    "created_at TEXT DEFAULT (datetime('now')));"
)


def _seed_user(db_path, uid, username):
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, 'default')",
            (uid, username),
        )
        c.commit()
    return uid


def _token(uid):
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": "Bearer " + tok}


BAD_TOKEN = {"Authorization": "Bearer not-a-real-token"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """brain router 进程内测试面（test_brain_memory_identity env 同款路线）+
    brain store/sessions 换 tmp 实例 + 身份库 tmp 隔离。"""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.brain as brain_api
    import src.database.postgres as pg_mod
    from src.config import settings
    from src.database.postgres import SQLitePool
    from src.hippo.memory_store import MemoryStore
    from src.hippo.session import SessionManager

    users_db = tmp_path / "opensoul.db"
    with sqlite3.connect(users_db) as c:
        c.executescript(_USERS_DDL)
        c.commit()
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(tmp_path / "enterprise.db") as c:
        c.executescript(_ENTERPRISE_DDL)
        c.commit()
    _seed_user(users_db, UID_A, "alice")
    _seed_user(users_db, UID_B, "bob")

    # brain 记忆面数据面换 tmp 实例（模块单例调用时取真源）
    monkeypatch.setattr(brain_api, "store", MemoryStore())
    monkeypatch.setattr(brain_api, "sessions", SessionManager())

    tmp_db = tmp_path / "brain_data.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", tmp_pool)

    # think/verify emit：timeline 换 tmp + 死端口（绝不打 live/生产 timeline）
    from src.nerve import event_bridge
    from src.timeline.store import TimelineStore

    monkeypatch.setattr(
        event_bridge.emit, "_timeline", TimelineStore(str(tmp_path / "timeline.db")), raising=False
    )
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(brain_api.router, prefix="/api/brain")
    with TestClient(app) as c:
        yield c, brain_api


def _remember(client, headers=None, params=None):
    p = {"session_id": "s-del-1", "content": "待删记忆内容"}
    if params:
        p.update(params)
    return client.post("/api/brain/remember", params=p, headers=headers)


def _add(env, headers=None, params=None, content="待删记忆内容"):
    """写一条记忆返回 memory_id。"""
    c, _ = env
    p = {"session_id": "s-del-1", "content": content}
    if params:
        p.update(params)
    resp = c.post("/api/brain/remember", params=p, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["memory_id"]


# ── 删除成功面 + 形状 + 计数对称 ──────────────────────────────────────────


def test_owner_delete_success(env):
    c, api = env
    mid = _add(env, headers=_token(UID_A), params={"user_id": UID_A})
    resp = c.delete(f"/api/brain/memories/{mid}", headers=_token(UID_A), params={"user_id": UID_A})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"memory_id": mid, "deleted": True}
    # 行真消失（store 真源回读）
    assert api.store.peek(mid) is None


def test_delete_response_shape_exact(env):
    c, _ = env
    mid = _add(env, headers=_token(UID_A), params={"user_id": UID_A})
    resp = c.delete(f"/api/brain/memories/{mid}", headers=_token(UID_A), params={"user_id": UID_A})
    body = resp.json()
    assert set(body.keys()) == {"memory_id", "deleted"}
    assert body["deleted"] is True
    assert body["memory_id"] == mid


def test_count_symmetry_write_plus_one_delete_minus_one(env):
    """写入 +1、删除成功 -1：会话 memory_count 与活记忆对称。"""
    c, api = env
    mid1 = _add(env, headers=_token(UID_A), params={"user_id": UID_A}, content="第一条")
    mid2 = _add(env, headers=_token(UID_A), params={"user_id": UID_A}, content="第二条")
    sess = api.sessions.peek("s-del-1")
    assert sess.memory_count == 2
    assert (
        c.delete(
            f"/api/brain/memories/{mid1}", headers=_token(UID_A), params={"user_id": UID_A}
        ).status_code
        == 200
    )
    assert api.sessions.peek("s-del-1").memory_count == 1
    assert (
        c.delete(
            f"/api/brain/memories/{mid2}", headers=_token(UID_A), params={"user_id": UID_A}
        ).status_code
        == 200
    )
    assert api.sessions.peek("s-del-1").memory_count == 0


def test_count_decrement_once_on_repeat_delete(env):
    """同一行删两次：第二次缺行 404，计数只回退一次（禁双退）。"""
    c, api = env
    mid = _add(env, headers=_token(UID_A), params={"user_id": UID_A})
    assert (
        c.delete(
            f"/api/brain/memories/{mid}", headers=_token(UID_A), params={"user_id": UID_A}
        ).status_code
        == 200
    )
    assert (
        c.delete(
            f"/api/brain/memories/{mid}", headers=_token(UID_A), params={"user_id": UID_A}
        ).status_code
        == 404
    )
    assert api.sessions.peek("s-del-1").memory_count == 0


def test_orphan_memory_delete_count_noop(env):
    """孤儿记忆（无父会话行）删除：归属 'default' 兜底可删、计数如实 no-op。"""
    c, api = env
    mem = api.store.add(session_id="no-such-session", content="孤儿记忆")
    assert (
        c.delete(f"/api/brain/memories/{mem.memory_id}", params={"user_id": "default"}).status_code
        == 200
    )
    assert api.store.peek(mem.memory_id) is None


# ── 归属闸：越权=与缺行同契约 404 行零改写 ─────────────────────────────────


def test_cross_user_delete_404_row_alive(env):
    c, api = env
    mid = _add(env, headers=_token(UID_A), params={"user_id": UID_A})
    resp = c.delete(f"/api/brain/memories/{mid}", headers=_token(UID_B), params={"user_id": UID_B})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Memory not found"
    # 行存活（禁冒删）
    alive = api.store.peek(mid)
    assert alive is not None
    assert alive.content == "待删记忆内容"


def test_gate_read_zero_side_effect_on_reject(env):
    """闸读走 peek：被拒请求行零改写（access_count/last_accessed_at 一字不动）。"""
    c, api = env
    mid = _add(env, headers=_token(UID_A), params={"user_id": UID_A})
    before = api.store.peek(mid)
    snapshot = (before.access_count, before.last_accessed_at, before.content)
    resp = c.delete(f"/api/brain/memories/{mid}", headers=_token(UID_B), params={"user_id": UID_B})
    assert resp.status_code == 404
    after = api.store.peek(mid)
    assert (after.access_count, after.last_accessed_at, after.content) == snapshot


def test_missing_row_same_contract_as_reject(env):
    """缺行 404 与越权 404 逐字同契约（禁存在性探测）。"""
    c, _ = env
    mid = _add(env, headers=_token(UID_A), params={"user_id": UID_A})
    denied = c.delete(
        f"/api/brain/memories/{mid}", headers=_token(UID_B), params={"user_id": UID_B}
    )
    missing = c.delete(
        "/api/brain/memories/no-such-memory-id", headers=_token(UID_B), params={"user_id": UID_B}
    )
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()


# ── 身份轴（F3b-㉛ 语义逐字）────────────────────────────────────────────────


def test_auth_derived_confirm_binding(env):
    """认证派生 + 自报确认绑定（同值）→ 200。"""
    c, _ = env
    mid = _add(env, headers=_token(UID_A), params={"user_id": UID_A})
    resp = c.delete(f"/api/brain/memories/{mid}", headers=_token(UID_A), params={"user_id": UID_A})
    assert resp.status_code == 200


def test_conflict_declared_403_row_alive(env):
    """自报矛盾=403 冒删面收口，行零改写。"""
    c, api = env
    mid = _add(env, headers=_token(UID_A), params={"user_id": UID_A})
    resp = c.delete(f"/api/brain/memories/{mid}", headers=_token(UID_A), params={"user_id": UID_B})
    assert resp.status_code == 403
    assert "user mismatch" in resp.json()["detail"]
    assert api.store.peek(mid) is not None


def test_unstated_default_and_nil_take_derived(env):
    """'default'/占位零 UUID=未声明→取派生（删到 A 自己的行）。"""
    for declared in ("default", "", NIL_UUID):
        mid = _add(env, headers=_token(UID_A), params={"user_id": UID_A})
        resp = c_delete(env, mid, headers=_token(UID_A), declared=declared)
        assert resp.status_code == 200, (declared, resp.text)


def c_delete(env, mid, headers=None, declared="default"):
    c, _ = env
    return c.delete(f"/api/brain/memories/{mid}", headers=headers, params={"user_id": declared})


def test_anonymous_self_report_kept(env):
    """匿名=保留自报契约零变化（既有本地多档案语义）：自报 UID_A 可删自己名下。"""
    c, api = env
    mid = _add(env, params={"user_id": UID_A})
    resp = c.delete(f"/api/brain/memories/{mid}", params={"user_id": UID_A})
    assert resp.status_code == 200
    assert api.store.peek(mid) is None
    # 匿名自报他人=404 行存活（越权同契约）
    mid2 = _add(env, params={"user_id": UID_A}, content="他人不可删")
    resp2 = c.delete(f"/api/brain/memories/{mid2}", params={"user_id": UID_B})
    assert resp2.status_code == 404
    assert api.store.peek(mid2) is not None


def test_bad_token_401_row_alive(env):
    c, api = env
    mid = _add(env, headers=_token(UID_A), params={"user_id": UID_A})
    resp = c.delete(f"/api/brain/memories/{mid}", headers=BAD_TOKEN, params={"user_id": "default"})
    assert resp.status_code == 401
    assert api.store.peek(mid) is not None


# ── 静态接线（Gate2：写了=接线了）──────────────────────────────────────────


def test_static_wiring_gate_before_delete():
    """AST 锁：删除口真注册 + bind_identity 真调用 + 闸（peek/归属判定）先于
    store.delete 副作用。"""
    src = Path("src/api/brain.py").read_text()
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "forget_memory":
            target = node
    assert target is not None, "forget_memory 端点不存在"

    def _calls(fn):
        out = []
        # 只扫函数体（decorator_list 里的 router.delete 调用不算消费点）
        for stmt in fn.body:
            for n in ast.walk(stmt):
                if isinstance(n, ast.Call):
                    f = n.func
                    if isinstance(f, ast.Attribute):
                        out.append((n.lineno, f.attr))
                    elif isinstance(f, ast.Name):
                        out.append((n.lineno, f.id))
        return out

    calls = _calls(target)
    names = {n for _, n in calls}
    assert "bind_identity" in names
    assert "peek" in names
    assert "delete" in names
    assert "increment_memory_count" in names
    ln_peek = min(ln for ln, n in calls if n == "peek")
    del_lines = [ln for ln, n in calls if n == "delete"]
    assert del_lines, "store.delete 无调用点"
    assert ln_peek < min(del_lines), "闸读必须先于 store.delete 副作用"
    # 归属判定（_memory_owner）也必须先于 delete
    owner_call_lines = [ln for ln, n in calls if n == "_memory_owner"]
    assert owner_call_lines, "_memory_owner 归属判定无调用点"
    assert min(owner_call_lines) < min(del_lines)

    # 路由真注册（delete("/memories/{memory_id}")）
    decorators = []
    for d in target.decorator_list:
        if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant):
            fn = d.func
            attr = fn.attr if isinstance(fn, ast.Attribute) else None
            decorators.append((attr, d.args[0].value))
    assert ("delete", "/memories/{memory_id}") in decorators


def test_static_store_delete_consumer_exists():
    """Gate2 静态锁：brain 面 store.delete 有真 API 消费点（修前零消费）。"""
    src = Path("src/api/brain.py").read_text()
    assert "store.delete(" in src
