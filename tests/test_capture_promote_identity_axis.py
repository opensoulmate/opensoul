"""capture promote_to_knowledge 面 user_id 轴 bind_identity 收口测试（user 从认证派生，禁自报冒写）。

F3b-⑥ 已裁决语义（user_id 自报轴改认证派生）同族既有面延伸（F3b-④b 先例
「已裁决语义的同族既有面延伸=无需拍板可立即执行件」，F3b-⑦ entity/F3b-⑩
cortex 同款）：POST /api/capture/{id}/promote 修前 user_id 完全客户端自报
直接进 knowledge/tags 写键——认证请求可冒写他人知识档案（F3b-⑥ search、
F3b-⑦ entity 同型，本件=采集提升面收口）。

语义与 F3b-⑥ 逐字一致：认证=派生档案键、自报只可确认绑定、矛盾=403、
'default'/空=未声明→取派生、匿名=保留自报（既有本地多档案语义契约零变化）、
坏 token=401 零副作用。

写键=档案键家族正典 str(_resolve_user_id(identity.user_id))（与
search/cortex/graph/entity 家族同源）——修前裸串写键（缺省 'default'）与
知识/标签读写面 str(UUID) 键形不匹配=promoted 行对家族读面不可见（孤儿行，
生产 knowledge 0 行无存量影响），语义收口如实记。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB + capture_client tmp captures/knowledge
库），绝不写生产库。数据面=真 db_pool 真 SQL 落库回读（非接缝桩——promote 是
纯写面，落库回读即真实产出证据）。
"""

import hashlib
import sqlite3
import uuid

import pytest

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)

# promote 写面三表（knowledge/tags/knowledge_tags，与 promote INSERT 列集同形）
KNOWLEDGE_DDL = (
    "CREATE TABLE IF NOT EXISTS knowledge ("
    "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', "
    "content TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', "
    "content_type TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}', "
    "created_at REAL, updated_at REAL); "
    "CREATE TABLE IF NOT EXISTS tags ("
    "id TEXT PRIMARY KEY, name TEXT NOT NULL, user_id TEXT NOT NULL); "
    "CREATE TABLE IF NOT EXISTS knowledge_tags ("
    "knowledge_id TEXT NOT NULL, tag_id TEXT NOT NULL);"
)


def _md5_uuid(s: str) -> uuid.UUID:
    h = hashlib.md5(s.encode()).hexdigest()
    return uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


def _seed_user(db_path, uid: str, username: str, tenant: str = "default") -> str:
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
        )
        c.commit()
    return uid


def _token(uid: str) -> dict:
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def env(tmp_path, monkeypatch, capture_client):
    """tmp 身份库 + capture_client 真 router 真 db_pool（knowledge 三表随 tmp 落库）。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    db_path = capture_client.app.state.capture_client_db
    with sqlite3.connect(db_path) as c:
        c.executescript(KNOWLEDGE_DDL)
        c.commit()

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-cap")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-cap")
    return {"client": capture_client, "db": db_path, "user_a": user_a, "user_b": user_b}


def _make_capture(env, url: str = "https://example.com/promote-probe") -> int:
    resp = env["client"].post(
        "/api/capture/page",
        json={"url": url, "title": "Probe", "content": f"body-{url}"},
    )
    assert resp.status_code == 200
    return int(resp.json()["id"])


def _promote(env, capture_id, headers=None, params=None):
    return env["client"].post(
        f"/api/capture/{capture_id}/promote",
        headers=headers or {},
        params=params or {},
    )


def _knowledge_rows(env) -> list[dict]:
    with sqlite3.connect(env["db"]) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM knowledge")]


def _tags_rows(env) -> list[dict]:
    with sqlite3.connect(env["db"]) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM tags")]


def _capture_status(env, capture_id) -> str:
    with sqlite3.connect(env["db"]) as c:
        row = c.execute("SELECT status FROM captures WHERE id = ?", (capture_id,)).fetchone()
        return row[0] if row else ""


class TestIdentityAxis:
    def test_anon_default_canonical_key(self, env):
        """匿名缺省：写键=档案键家族正典 str(_resolve_user_id('default'))（契约随更新，
        修前裸串 'default' 与家族读面键形不匹配=孤儿行）。"""
        cid = _make_capture(env)
        resp = _promote(env, cid)
        assert resp.status_code == 200
        expected = str(_md5_uuid("default"))
        rows = _knowledge_rows(env)
        assert len(rows) == 1
        assert rows[0]["user_id"] == expected
        assert resp.json()["user_id"] == expected  # 真写入键如实报

    def test_anon_self_report_preserved(self, env):
        """匿名保留自报（既有本地多档案语义，F3b-⑥ 契约零变化）→ 写键=自报档案正典键。"""
        cid = _make_capture(env)
        resp = _promote(env, cid, params={"user_id": "alice"})
        assert resp.status_code == 200
        assert _knowledge_rows(env)[0]["user_id"] == str(_md5_uuid("alice"))

    def test_authed_undeclared_derives_user(self, env):
        """认证+未声明→写键=派生（users.id），修前自报 'default' 顶替（冒写面）。"""
        cid = _make_capture(env)
        resp = _promote(env, cid, headers=_token(env["user_a"]))
        assert resp.status_code == 200
        assert _knowledge_rows(env)[0]["user_id"] == env["user_a"]

    def test_authed_declare_default_is_undeclared(self, env):
        """'default'/空=未声明→取派生（不误伤不传参调用方）。"""
        cid = _make_capture(env)
        resp = _promote(env, cid, headers=_token(env["user_a"]), params={"user_id": "default"})
        assert resp.status_code == 200
        assert _knowledge_rows(env)[0]["user_id"] == env["user_a"]

    def test_authed_matching_declared_ok(self, env):
        """自报与派生一致=确认绑定，照常提升。"""
        cid = _make_capture(env)
        resp = _promote(env, cid, headers=_token(env["user_a"]), params={"user_id": env["user_a"]})
        assert resp.status_code == 200
        assert _knowledge_rows(env)[0]["user_id"] == env["user_a"]

    def test_authed_conflict_403_zero_side_effect(self, env):
        """认证请求自报他人档案=403 冒写面收口（修前 200 直接把知识写进 victim 档案）。"""
        cid = _make_capture(env)
        resp = _promote(env, cid, headers=_token(env["user_a"]), params={"user_id": env["user_b"]})
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert _knowledge_rows(env) == []  # 零副作用硬反证：知识行零落库
        assert _tags_rows(env) == []  # 标签行零落库
        assert _capture_status(env, cid) != "promoted"  # capture 行零改写

    def test_bad_token_401_zero_side_effect(self, env):
        cid = _make_capture(env)
        resp = _promote(env, cid, headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401
        assert _knowledge_rows(env) == []
        assert _capture_status(env, cid) != "promoted"


class TestWriteKeyFamilyShape:
    def test_tags_key_family_shape(self, env):
        """tags/knowledge_tags 写键同走家族正典键（修前裸串 'default' 与
        knowledge_service._replace_tags 的 str(UUID) 键形分裂=同名标签双份）。"""
        cid = _make_capture(env)
        resp = _promote(env, cid)
        assert resp.status_code == 200
        expected = str(_md5_uuid("default"))
        tags = _tags_rows(env)
        assert {t["name"] for t in tags} == {"capture", "page", "browser"}
        assert all(t["user_id"] == expected for t in tags)
        with sqlite3.connect(env["db"]) as c:
            links = c.execute("SELECT COUNT(*) FROM knowledge_tags").fetchone()[0]
        assert links == 3  # 三标签全挂链接

    def test_written_key_parses_as_uuid_family(self, env):
        """落库键=合法 UUID 字符串（家族读面 _resolve_user_id/hybrid_search 可命中），
        修前裸 'default' 不可被任何 UUID 形读面命中（孤儿行收口）。"""
        cid = _make_capture(env)
        _promote(env, cid, params={"user_id": "alice"})
        key = _knowledge_rows(env)[0]["user_id"]
        assert uuid.UUID(key) == _md5_uuid("alice")

    def test_promote_contract_shape_unchanged(self, env):
        """成功契约键集 {promoted,id,user_id} 与 capture status='promoted' 更新不变。"""
        cid = _make_capture(env)
        resp = _promote(env, cid)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"promoted", "id", "user_id"}
        assert body["promoted"] is True
        assert body["id"] == cid
        assert _capture_status(env, cid) == "promoted"

    def test_missing_capture_404_contract(self, env):
        """缺行 404 契约逐字不变 + 零写入。"""
        resp = _promote(env, 99999)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Capture not found"
        assert _knowledge_rows(env) == []


class TestStaticWiring:
    def test_wiring_shape(self):
        """静态接线：恰 1 处 bind_identity + 1 个 authorization Header 形参、
        旧自报直通写键（promote 体内 user_id 直写）清零、import 在文件顶部。"""
        from pathlib import Path

        src = Path("src/api/capture.py").read_text(encoding="utf-8")
        assert src.count("bind_identity(authorization") == 1
        STR = chr(115) + chr(116) + chr(114)
        assert src.count("authorization: " + STR + " " + chr(124) + " None = Header(None)") == 1
        assert src.count("uid = str(_resolve_user_id(identity.user_id))") == 1
        # 修前自报直通写键形清零（knowledge/tags 两写点 + tags 读点 + 响应键）
        assert "tag_name, user_id" not in src
        assert '"promoted": True, "id": capture_id, "user_id": user_id' not in src
        import_line = "from src.services.tenant_binding import bind_identity"
        assert import_line in src
        assert src.index(import_line) < src.index("@router")
