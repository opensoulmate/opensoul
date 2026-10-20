"""knowledge.py CRUD 面 user_id 轴 bind_identity 收口测试（user 从认证派生，禁自报冒读/冒写/冒删）。

F3b-⑥ 已裁决语义（user_id 自报轴改认证派生）同族既有面延伸（F3b-④b 先例，
F3b-⑦ entity/F3b-⑩ cortex/F3b-⑪ capture/F3b-⑫ pipeline/F3b-⑬ chat·export·feedback
同款）：/api/knowledge/ 九端点（list/create/upload/upload-bulk/get/update/
delete/star/pin）修前 user_id: UUID 完全客户端自报直接进读写档案键——认证请求
可冒读他人知识（get/list）、冒写/冒删他人知识档案（create/upload/update/
delete/star/pin），坏 token 200 无鉴权轴。

语义与 F3b-⑥ 逐字一致：认证=派生档案键、自报只可确认绑定、矛盾=403、
'default'/空/占位零 UUID（NIL_UUID）=未声明→取派生、匿名=保留自报（既有
本地多档案语义契约零变化）、坏 token=401 零副作用。

写键/读键=档案键家族正典 str(_resolve_user_id(identity.user_id))（F3b-⑫ 同源）。

数据面走 conftest.knowledge_client（tmp opensoul.db 真 schema + tmp SQLitePool
+ embedding 降级空向量 + meili AVAILABLE=False），USERS_DB/ENTERPRISE_DB 随
tmp（绝不写生产库）；真 SQL 落库回读非接缝桩。
"""

import ast
import hashlib
import sqlite3
import uuid
from pathlib import Path

import pytest

NIL_UUID = "00000000-0000-0000-0000-000000000000"
UID_A = "11111111-1111-4111-8111-111111111111"
UID_B = "22222222-2222-4222-8222-222222222222"


def _md5_uuid(s: str) -> uuid.UUID:
    h = hashlib.md5(s.encode()).hexdigest()
    return uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


def _seed_user(db_path, uid: str, username: str) -> str:
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, 'default')",
            (uid, username),
        )
        c.commit()
    return uid


def _token(uid: str) -> dict:
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


def _enterprise_token(ent_id: int) -> dict:
    """enterprise scope token（sub=数字 id，bind_identity 企业分支真身份）。"""
    from datetime import UTC, datetime, timedelta

    from jose import jwt

    from src.config import settings

    payload = {
        "sub": str(ent_id),
        "scope": "enterprise",
        "role": "user",
        "exp": datetime.now(UTC) + timedelta(minutes=30),
        "iat": datetime.now(UTC),
    }
    tok = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {tok}"}


def _seed_enterprise_user(tmp_path, ent_id: int) -> None:
    db = tmp_path / "enterprise.db"
    with sqlite3.connect(db) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
            "email TEXT DEFAULT '', password_hash TEXT NOT NULL, "
            "role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1, "
            "tenant_id TEXT DEFAULT 'default', "
            "created_at TEXT DEFAULT (datetime('now')), "
            "updated_at TEXT DEFAULT (datetime('now')))"
        )
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, 'default')",
            (ent_id, f"ent{ent_id}"),
        )
        c.commit()


def _payload(title="t1"):
    return {"title": title, "content": "hello identity axis", "tags": ["x"]}


def _sql_rows(db_path, where="", *args):
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        q = "SELECT * FROM knowledge"
        if where:
            q += " WHERE " + where
        return [dict(r) for r in c.execute(q, args).fetchall()]


# ── 轴语义（create 面） ─────────────────────────────────────────────────


def test_anon_self_report_preserved(knowledge_client):
    """匿名自报保留：修前 ?user_id=UID 直写该档案键，契约零变化。"""
    db = knowledge_client.app.state.knowledge_client_db
    r = knowledge_client.post("/api/knowledge/", params={"user_id": UID_A}, json=_payload())
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert len(rows) == 1
    assert rows[0]["user_id"] == UID_A


def test_anon_missing_user_id_family_default_key(knowledge_client):
    """缺省 user_id 修前 422（必填自报面缺省不可用）；现=匿名 default 档案家族正典键。"""
    db = knowledge_client.app.state.knowledge_client_db
    r = knowledge_client.post("/api/knowledge/", json=_payload())
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert rows[0]["user_id"] == str(_md5_uuid("default"))


def test_anon_nonuuid_key_family_canonical(knowledge_client):
    """匿名非 UUID 自报：修前 422（UUID 类型校验拒收），现=md5 家族正典键。"""
    db = knowledge_client.app.state.knowledge_client_db
    r = knowledge_client.post("/api/knowledge/", params={"user_id": "alice"}, json=_payload())
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert rows[0]["user_id"] == str(_md5_uuid("alice"))


def test_authed_derived_key(knowledge_client):
    """认证缺省=未声明→取派生：写键=A 派生档案键（identity 派生真消费）。"""
    db = knowledge_client.app.state.knowledge_client_db
    _seed_user(db, UID_A, "alice")
    r = knowledge_client.post("/api/knowledge/", json=_payload(), headers=_token(UID_A))
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert rows[0]["user_id"] == UID_A


@pytest.mark.parametrize("placeholder", ["default", NIL_UUID])
def test_authed_placeholder_is_undeclared(knowledge_client, placeholder):
    """'default'/占位零 UUID=未声明→取派生（admin-ui 占位形不误伤）。"""
    db = knowledge_client.app.state.knowledge_client_db
    _seed_user(db, UID_A, "alice")
    r = knowledge_client.post(
        "/api/knowledge/", params={"user_id": placeholder}, json=_payload(), headers=_token(UID_A)
    )
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert rows[0]["user_id"] == UID_A


def test_authed_confirm_binding(knowledge_client):
    """自报==派生=确认绑定放行。"""
    db = knowledge_client.app.state.knowledge_client_db
    _seed_user(db, UID_A, "alice")
    r = knowledge_client.post(
        "/api/knowledge/", params={"user_id": UID_A}, json=_payload(), headers=_token(UID_A)
    )
    assert r.status_code == 200, r.text
    assert _sql_rows(db)[0]["user_id"] == UID_A


def test_conflict_403_zero_side_effect(knowledge_client):
    """矛盾=403 冒写面收口+零副作用（修前 200 直写他人档案）。"""
    db = knowledge_client.app.state.knowledge_client_db
    _seed_user(db, UID_A, "alice")
    _seed_user(db, UID_B, "bob")
    r = knowledge_client.post(
        "/api/knowledge/", params={"user_id": UID_A}, json=_payload(), headers=_token(UID_B)
    )
    assert r.status_code == 403
    assert "user mismatch" in r.json()["detail"]
    assert _sql_rows(db) == []


def test_bad_token_401_zero_side_effect(knowledge_client):
    db = knowledge_client.app.state.knowledge_client_db
    r = knowledge_client.post(
        "/api/knowledge/",
        params={"user_id": UID_A},
        json=_payload(),
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert r.status_code == 401
    assert _sql_rows(db) == []


# ── 写键家族正典（enterprise 数字 id → md5 档案键） ────────────────────


def test_enterprise_write_key_family_canonical(knowledge_client, tmp_path):
    """enterprise 用户（users.id 数字）写键=md5 家族正典（F3b-⑬ 同款），写读同键。"""
    db = knowledge_client.app.state.knowledge_client_db
    _seed_enterprise_user(tmp_path, 7)
    hdrs = _enterprise_token(7)
    r = knowledge_client.post("/api/knowledge/", json=_payload(), headers=hdrs)
    assert r.status_code == 200, r.text
    rows = _sql_rows(db)
    assert rows[0]["user_id"] == str(_md5_uuid("7"))
    # 写读同键：同 token list/get 真命中自己刚写的行
    lst = knowledge_client.get("/api/knowledge/", headers=hdrs)
    assert lst.status_code == 200
    assert len(lst.json()) == 1
    got = knowledge_client.get(f"/api/knowledge/{rows[0]['id']}", headers=hdrs)
    assert got.status_code == 200


# ── 读隔离（冒读面收口） ───────────────────────────────────────────────


def test_read_isolation_list_and_get(knowledge_client):
    """B 恒读不到 A 的行：list 0 行、by-ID 404（派生键不达他人行）。"""
    db = knowledge_client.app.state.knowledge_client_db
    _seed_user(db, UID_A, "alice")
    _seed_user(db, UID_B, "bob")
    r = knowledge_client.post("/api/knowledge/", json=_payload(), headers=_token(UID_A))
    kid = r.json()["id"]
    lst = knowledge_client.get("/api/knowledge/", headers=_token(UID_B))
    assert lst.status_code == 200
    assert lst.json() == []
    got = knowledge_client.get(f"/api/knowledge/{kid}", headers=_token(UID_B))
    assert got.status_code == 404


def test_get_conflict_403_before_404(knowledge_client):
    """B 显式自报 A 档案读 A 的行：403 先于 404（身份证明先于一切授权检查）。"""
    db = knowledge_client.app.state.knowledge_client_db
    _seed_user(db, UID_A, "alice")
    _seed_user(db, UID_B, "bob")
    r = knowledge_client.post("/api/knowledge/", json=_payload(), headers=_token(UID_A))
    kid = r.json()["id"]
    got = knowledge_client.get(
        f"/api/knowledge/{kid}", params={"user_id": UID_A}, headers=_token(UID_B)
    )
    assert got.status_code == 403
    assert "user mismatch" in got.json()["detail"]


# ── 九端点同轴电池（B 自报 A=403 零副作用 + A 正对照） ─────────────────


def _seed_owned_row(knowledge_client, db, uid):
    r = knowledge_client.post(
        "/api/knowledge/", params={"user_id": uid}, json=_payload(), headers=_token(uid)
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


CROSS_CASES = [
    ("GET", "/api/knowledge/", None),
    ("POST", "/api/knowledge/", "create"),
    ("POST", "/api/knowledge/upload", "upload"),
    ("POST", "/api/knowledge/upload/bulk", "upload"),
    ("GET", "/api/knowledge/{kid}", None),
    ("PUT", "/api/knowledge/{kid}", "update"),
    ("DELETE", "/api/knowledge/{kid}", None),
    ("POST", "/api/knowledge/{kid}/star", None),
    ("POST", "/api/knowledge/{kid}/pin", None),
]


@pytest.mark.parametrize("method,path,shape", CROSS_CASES)
def test_cross_user_battery_403(knowledge_client, method, path, shape):
    """B token+自报 A 档案：九端点逐一 403（修前 200 冒读/冒写/冒删）+零副作用。"""
    db = knowledge_client.app.state.knowledge_client_db
    _seed_user(db, UID_A, "alice")
    _seed_user(db, UID_B, "bob")
    kid = _seed_owned_row(knowledge_client, db, UID_A)
    url = path.format(kid=kid)
    kwargs = {"params": {"user_id": UID_A}, "headers": _token(UID_B)}
    if shape == "create":
        kwargs["json"] = _payload("intruder")
    elif shape == "update":
        kwargs["json"] = {"title": "intruder"}
    elif shape == "upload":
        kwargs["files"] = [("files" if "bulk" in path else "file", ("e.txt", b"e", "text/plain"))]
    r = knowledge_client.request(method, url, **kwargs)
    assert r.status_code == 403, (method, path, r.status_code, r.text)
    assert "user mismatch" in r.json()["detail"]
    # 零副作用：A 的行一字不动、无新行
    rows = _sql_rows(db)
    assert len(rows) == 1
    assert rows[0]["title"] == "t1"
    assert rows[0]["user_id"] == UID_A


POSITIVE_CASES = [
    ("GET", "/api/knowledge/{kid}", None, 200),
    ("PUT", "/api/knowledge/{kid}", "update", 200),
    ("POST", "/api/knowledge/{kid}/star", None, 200),
    ("POST", "/api/knowledge/{kid}/pin", None, 200),
    ("DELETE", "/api/knowledge/{kid}", None, 200),
]


@pytest.mark.parametrize("method,path,shape,expect", POSITIVE_CASES)
def test_own_row_positive_control(knowledge_client, method, path, shape, expect):
    """A 正对照：属主操作全通（九端点正向面零回归）。"""
    db = knowledge_client.app.state.knowledge_client_db
    _seed_user(db, UID_A, "alice")
    kid = _seed_owned_row(knowledge_client, db, UID_A)
    kwargs = {"params": {"user_id": UID_A}, "headers": _token(UID_A)}
    if shape == "update":
        kwargs["json"] = {"title": "t2"}
    r = knowledge_client.request(method, path.format(kid=kid), **kwargs)
    assert r.status_code == expect, (method, path, r.text)


def test_upload_and_bulk_write_key(knowledge_client):
    """upload/upload-bulk 写键=派生档案键（真落库回读）。"""
    db = knowledge_client.app.state.knowledge_client_db
    _seed_user(db, UID_A, "alice")
    r = knowledge_client.post(
        "/api/knowledge/upload",
        headers=_token(UID_A),
        files={"file": ("a.txt", b"hello upload", "text/plain")},
    )
    assert r.status_code == 200, r.text
    r2 = knowledge_client.post(
        "/api/knowledge/upload/bulk",
        headers=_token(UID_A),
        files=[("files", ("b.txt", b"hello bulk", "text/plain"))],
    )
    assert r2.status_code == 200, r2.text
    rows = _sql_rows(db)
    assert len(rows) == 2
    assert {r_["user_id"] for r_ in rows} == {UID_A}


# ── 系统视图零闸（既有契约） ───────────────────────────────────────────


def test_health_stats_system_view_unchanged(knowledge_client):
    assert knowledge_client.get("/api/knowledge/health").status_code == 200
    assert knowledge_client.get("/api/knowledge/stats").status_code == 200


def test_star_pin_columns_schema_heal(tmp_path):
    """knowledge.starred/pinned 缺列自愈（F3b-⑭ 根因修）：老库形态 connect 即补列，
    toggle_star/toggle_pin 修前 "no such column" 必 500（生产 PRAGMA 实证缺列）。"""
    import asyncio

    from src.database.postgres import SQLitePool

    db = tmp_path / "old_shape.db"
    with sqlite3.connect(db) as c:
        c.execute(
            "CREATE TABLE knowledge (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
            "title TEXT NOT NULL, content TEXT DEFAULT '', tags TEXT DEFAULT '[]', "
            "source TEXT DEFAULT '', content_type TEXT DEFAULT '', "
            "metadata TEXT DEFAULT '{}', "
            "created_at TEXT DEFAULT (datetime('now')), "
            "updated_at TEXT DEFAULT (datetime('now')))"
        )
        c.commit()

    async def _run():
        pool = SQLitePool(db_path=str(db))
        await pool.connect()
        rows = await pool.fetch("PRAGMA table_info(knowledge)")
        await pool.disconnect()
        return {r[1] for r in rows}

    cols = asyncio.run(_run())
    assert {"starred", "pinned"} <= cols


# ── 静态接线（AST：派生先于服务调用，authorization 形参在场） ─────────


def test_static_wiring_ast():
    src = Path("src/api/knowledge.py").read_text()
    tree = ast.parse(src)
    targets = {
        "list_knowledge",
        "create_knowledge",
        "upload_file",
        "upload_files_bulk",
        "get_knowledge",
        "update_knowledge",
        "delete_knowledge",
        "star_knowledge",
        "pin_knowledge",
    }
    seen = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name not in targets:
            continue
        seen.add(node.name)
        params = [a.arg for a in node.args.args]
        assert "authorization" in params, f"{node.name} 缺 authorization 形参"
        assert "user_id" in params, f"{node.name} 缺 user_id 形参"
        # user_id 不再是 UUID 强制类型（修前 user_id: UUID 自报面）
        uid_ann = node.args.args[params.index("user_id")].annotation
        assert uid_ann is not None
        ann = ast.unparse(uid_ann)
        assert ann == "str", f"{node.name} user_id 注解={ann}"
        calls = [
            n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        ]
        bind_calls = [
            n for n in calls if isinstance(n.func, ast.Name) and n.func.id == "bind_identity"
        ]
        assert len(bind_calls) == 1, f"{node.name} bind_identity 调用数={len(bind_calls)}"
        resolve_calls = [
            n for n in calls if isinstance(n.func, ast.Name) and n.func.id == "_resolve_user_id"
        ]
        assert len(resolve_calls) == 1, f"{node.name} _resolve_user_id 调用数={len(resolve_calls)}"
        # 派生（bind_identity）语句先于服务调用语句（写了≠接线了：真路径顺序）
        bind_line = bind_calls[0].lineno
        service_calls = [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "knowledge_service"
        ]
        assert service_calls, f"{node.name} 无 knowledge_service 调用"
        assert bind_line < min(n.lineno for n in service_calls), (
            f"{node.name} bind_identity 未先于服务调用"
        )
    assert seen == targets, f"端点集不齐: {targets - seen}"
    # 模块级家族同形 _resolve_user_id + bind_identity 真 import
    assert (
        "_resolve_user_id" in src and "from src.services.tenant_binding import bind_identity" in src
    )
