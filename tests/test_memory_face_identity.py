"""hippo /memories 身份轴收口测试（记忆行无 user 轴→归属=父会话行继承）。

F3b-⑱ 留档「memory 面（memories CRUD）身份轴未动（记忆行无 user 轴，另拍）」
执行件：修前 create/list/get/update/delete 五端点零鉴权零归属——任意人可
冒写他人会话的记忆、看全部记忆（缺省=全量泄露）、冒读/冒改/冒删任意记忆
（by-ID 零归属闸，且 get() 的"读取即活动"对被拒读取也照刷=行改写）。

语义与 F3b-⑱ 逐字一致：认证=派生、自报只可确认绑定、矛盾=403「user
mismatch」、'default'/空/占位零 UUID（NIL_UUID）=未声明→取派生、匿名=
保留自报（既有本地多档案语义契约零变化）、坏 token=401 零副作用；by-ID
越权=与缺行同契约 404 禁存在性探测；闸读走 peek 零副作用（被拒读取绝不
刷 access_count/last_accessed_at），过闸后才 get()。

记忆行归属=父会话行继承（③b-2 _task_tenant 同权威）：记忆行无 user 轴，
归属唯一来源=父会话行 user_id；父会话缺失（孤儿记忆）或归属空白→
'default' 兜底（键派生故障绝不吃掉落库，③b-2 旁路纪律）。

数据面=tmp 全隔离（USERS_DB/ENTERPRISE_DB 随 tmp + SessionManager/
MemoryStore/gate 换 tmp 实例），绝不写生产库。
"""

import ast
import time
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


def _seed_user(db_path, uid, username):
    import sqlite3

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
def env(will_client, tmp_path, monkeypatch):
    """hippo 路由进程内测试面（test_hippo_sessions_identity env 同款路线）+
    身份库 tmp 隔离 + mcp 授权清理面接 tmp gate。"""
    import sqlite3

    import src.api.hippo as hippo_api
    from src.hippo.memory_store import MemoryStore
    from src.hippo.session import SessionManager
    from src.mcp.session_grants import SessionToolGate

    users_db = tmp_path / "opensoul.db"
    with sqlite3.connect(users_db) as c:
        c.executescript(_USERS_DDL)
        c.commit()
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    _seed_user(users_db, UID_A, "alice")
    _seed_user(users_db, UID_B, "bob")

    gate = SessionToolGate(db_path=str(tmp_path / "grants.db"))
    monkeypatch.setattr(hippo_api, "sessions", SessionManager())
    monkeypatch.setattr(hippo_api, "store", MemoryStore())
    monkeypatch.setattr(hippo_api, "default_gate", lambda: gate, raising=False)
    return will_client, hippo_api, gate


def _create_session(client, headers=None, **kw):
    resp = client.post("/api/hippo/sessions", json=kw, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["session_id"]


def _add_memory(client, session_id, headers=None, params=None, **kw):
    body = {"content": kw.pop("content", "记忆内容"), "session_id": session_id}
    body.update(kw)
    return client.post("/api/hippo/memories", json=body, params=params, headers=headers)


# ── create：冒写面收口 + 自动建会话行归属=派生身份 ──────────────────


class TestCreateAxis:
    def test_write_into_own_session_ok(self, env):
        client, hippo_api, _ = env
        sid = _create_session(client, headers=_token(UID_A), user_id="", title="a")
        resp = _add_memory(client, sid, headers=_token(UID_A), content="A 的记忆")
        assert resp.status_code == 200
        mid = resp.json()["memory_id"]
        assert hippo_api.store._memories[mid].session_id == sid
        assert hippo_api.sessions._sessions[sid].memory_count == 1

    def test_cross_user_write_404_zero_side_effect(self, env):
        """B 写 A 会话的记忆：404 且记忆不落、会话行零改写（修前 200 直写）。"""
        client, hippo_api, _ = env
        sid = _create_session(client, headers=_token(UID_A), user_id="", title="a")
        before = hippo_api.sessions._sessions[sid].memory_count
        resp = _add_memory(client, sid, headers=_token(UID_B), content="B 冒写")
        assert resp.status_code == 404
        assert hippo_api.store._memories == {}
        assert hippo_api.sessions._sessions[sid].memory_count == before

    def test_auto_create_session_owned_by_derived_user(self, env):
        """自动建会话行归属=派生身份（修前恒 'default' 无主于写入者）。"""
        client, hippo_api, _ = env
        resp = _add_memory(client, "auto-face20", headers=_token(UID_A), content="自动会话记忆")
        assert resp.status_code == 200
        assert hippo_api.sessions._sessions["auto-face20"].user_id == UID_A
        # by-ID 会话面对派生身份可达
        detail = client.get("/api/hippo/sessions/auto-face20", headers=_token(UID_A))
        assert detail.status_code == 200

    def test_auto_create_anonymous_contract_unchanged(self, env):
        """匿名自动建会话 'default' 兜底归属契约零变化（F3b-⑱ 契约锁死面）。"""
        client, hippo_api, _ = env
        resp = _add_memory(client, "auto-anon20", content="匿名自动")
        assert resp.status_code == 200
        rows = hippo_api.sessions.list_sessions()
        row = next(s for s in rows if s.title == "Auto: auto-anon20")
        assert row.user_id == "default"

    def test_anonymous_self_report_preserved(self, env):
        """匿名保留自报（本地多档案语义）：自报 X→自动会话归属 X。"""
        client, hippo_api, _ = env
        resp = _add_memory(client, "auto-self20", params={"user_id": "profile-x"}, content="自报")
        assert resp.status_code == 200
        assert hippo_api.sessions._sessions["auto-self20"].user_id == "profile-x"

    def test_anonymous_cross_profile_write_404(self, env):
        """匿名自报 X 写 Y 档案会话：404（冒写面收口，修前 200 直写）。"""
        client, hippo_api, _ = env
        sid = _create_session(client, user_id="profile-y", title="y")
        resp = _add_memory(client, sid, params={"user_id": "profile-x"}, content="跨档案冒写")
        assert resp.status_code == 404
        assert hippo_api.store._memories == {}

    def test_bad_token_401_zero_side_effect(self, env):
        client, hippo_api, _ = env
        resp = _add_memory(client, "bad-token-sess", headers=BAD_TOKEN, content="x")
        assert resp.status_code == 401
        assert hippo_api.sessions.get("bad-token-sess") is None
        assert hippo_api.store._memories == {}

    def test_self_report_mismatch_403(self, env):
        client, hippo_api, _ = env
        sid = _create_session(client, headers=_token(UID_A), user_id="", title="a")
        resp = _add_memory(
            client, sid, headers=_token(UID_A), params={"user_id": UID_B}, content="x"
        )
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert hippo_api.store._memories == {}

    def test_nil_uuid_undeclared_takes_derived(self, env):
        client, hippo_api, _ = env
        resp = _add_memory(
            client,
            "auto-nil20",
            headers=_token(UID_A),
            params={"user_id": NIL_UUID},
            content="占位零 UUID=未声明",
        )
        assert resp.status_code == 200
        assert hippo_api.sessions._sessions["auto-nil20"].user_id == UID_A


# ── list：「用户看自己的」+ 显式 session_id=by-ID 引用走父会话闸 ──────


class TestListAxis:
    def test_list_only_own(self, env):
        """修前缺省=全量泄露（任意人看全部记忆）→ 只看自己归属的。"""
        client, _, _ = env
        sa = _create_session(client, headers=_token(UID_A), user_id="", title="a")
        sb = _create_session(client, headers=_token(UID_B), user_id="", title="b")
        for sid, n in ((sa, 2), (sb, 1)):
            for i in range(n):
                assert (
                    _add_memory(
                        client,
                        sid,
                        headers=_token(UID_A if sid == sa else UID_B),
                        content=f"m{i}",
                    ).status_code
                    == 200
                )
        ra = client.get("/api/hippo/memories", headers=_token(UID_A)).json()
        rb = client.get("/api/hippo/memories", headers=_token(UID_B)).json()
        assert ra["total"] == 2 and rb["total"] == 1
        assert {m["session_id"] for m in ra["memories"]} == {sa}
        assert {m["session_id"] for m in rb["memories"]} == {sb}

    def test_list_explicit_session_foreign_404(self, env):
        client, _, _ = env
        sa = _create_session(client, headers=_token(UID_A), user_id="", title="a")
        assert _add_memory(client, sa, headers=_token(UID_A), content="A 记忆").status_code == 200
        resp = client.get("/api/hippo/memories", params={"session_id": sa}, headers=_token(UID_B))
        assert resp.status_code == 404  # 越权=与缺行同契约
        ok = client.get("/api/hippo/memories", params={"session_id": sa}, headers=_token(UID_A))
        assert ok.status_code == 200 and ok.json()["total"] == 1

    def test_list_missing_session_404_same_contract(self, env):
        client, _, _ = env
        resp = client.get(
            "/api/hippo/memories",
            params={"session_id": "no-such-sess"},
            headers=_token(UID_A),
        )
        assert resp.status_code == 404  # 与越权同形=无存在性探测面

    def test_list_anonymous_self_report_bucket(self, env):
        client, _, _ = env
        s1 = _create_session(client, user_id="profile-x", title="x")
        _add_memory(client, s1, params={"user_id": "profile-x"}, content="x 记忆")
        rx = client.get("/api/hippo/memories", params={"user_id": "profile-x"}).json()
        rd = client.get("/api/hippo/memories").json()
        assert rx["total"] == 1 and rd["total"] == 0  # 本地多档案语义保留

    def test_list_orphan_memory_default_bucket(self, env):
        """孤儿记忆（父会话缺失）→'default' 兜底：default 可见、他人不可见。"""
        client, hippo_api, _ = env
        mid = _add_memory(client, "orphan-sess20", content="孤儿记忆").json()["memory_id"]
        assert (
            client.delete(
                "/api/hippo/sessions/orphan-sess20", params={"user_id": "default"}
            ).status_code
            == 200
        )
        assert hippo_api.sessions.get("orphan-sess20") is None  # 父会话真没了
        assert mid in hippo_api.store._memories
        rd = client.get("/api/hippo/memories").json()
        assert rd["total"] == 1  # 'default' 兜底可见
        ra = client.get("/api/hippo/memories", headers=_token(UID_A)).json()
        assert ra["total"] == 0

    def test_list_limit_applies_on_visible_set(self, env):
        """limit 的 top-N 语义在可见集上取（过滤先于截断）。"""
        client, _, _ = env
        sa = _create_session(client, headers=_token(UID_A), user_id="", title="a")
        for i in range(3):
            assert (
                _add_memory(
                    client, sa, headers=_token(UID_A), content=f"m{i}", importance=0.9
                ).status_code
                == 200
            )
        resp = client.get("/api/hippo/memories", params={"limit": 2}, headers=_token(UID_A)).json()
        assert resp["total"] == 2


# ── by-ID：冒读/冒改/冒删收口 + peek 零副作用 ─────────────────────────


class TestByMemoryIdAxis:
    def _pair(self, env):
        client, hippo_api, _ = env
        sid = _create_session(client, headers=_token(UID_A), user_id="", title="a")
        mid = _add_memory(client, sid, headers=_token(UID_A), content="A 的记忆").json()[
            "memory_id"
        ]
        return client, hippo_api, sid, mid

    def test_get_cross_user_404_zero_mutation(self, env):
        """B 冒读 404 且 access_count/last_accessed_at 一字不动（修前 200+
        get() 读取即活动=被拒读取也行改写）。"""
        client, hippo_api, _, mid = self._pair(env)
        before = (
            hippo_api.store._memories[mid].access_count,
            hippo_api.store._memories[mid].last_accessed_at,
        )
        resp = client.get(f"/api/hippo/memories/{mid}", headers=_token(UID_B))
        assert resp.status_code == 404
        after = (
            hippo_api.store._memories[mid].access_count,
            hippo_api.store._memories[mid].last_accessed_at,
        )
        assert after == before

    def test_get_cross_user_does_not_touch_session(self, env):
        """被拒读取绝不触发会话活动镜像（touch 必须发生在过闸之后）。"""
        client, hippo_api, sid, mid = self._pair(env)
        before = hippo_api.sessions._sessions[sid].last_active_at
        assert client.get(f"/api/hippo/memories/{mid}", headers=_token(UID_B)).status_code == 404
        assert hippo_api.sessions._sessions[sid].last_active_at == before

    def test_get_own_keeps_refresh_contract(self, env):
        """过闸后 get() 保留既有"读取即活动"语义（契约零变化）。"""
        client, hippo_api, sid, mid = self._pair(env)
        before = hippo_api.store._memories[mid].access_count
        resp = client.get(f"/api/hippo/memories/{mid}", headers=_token(UID_A))
        assert resp.status_code == 200
        assert resp.json()["content"] == "A 的记忆"
        assert hippo_api.store._memories[mid].access_count == before + 1

    def test_patch_cross_user_404_content_unchanged(self, env):
        client, hippo_api, _, mid = self._pair(env)
        resp = client.patch(
            f"/api/hippo/memories/{mid}",
            headers=_token(UID_B),
            json={"content": "B 冒改"},
        )
        assert resp.status_code == 404
        assert hippo_api.store._memories[mid].content == "A 的记忆"  # 行零改写

    def test_patch_own_ok(self, env):
        client, hippo_api, _, mid = self._pair(env)
        resp = client.patch(
            f"/api/hippo/memories/{mid}",
            headers=_token(UID_A),
            json={"content": "A 自己改"},
        )
        assert resp.status_code == 200 and resp.json()["updated"] is True
        assert hippo_api.store._memories[mid].content == "A 自己改"

    def test_delete_cross_user_404_row_survives(self, env):
        client, hippo_api, _, mid = self._pair(env)
        resp = client.delete(f"/api/hippo/memories/{mid}", headers=_token(UID_B))
        assert resp.status_code == 404
        assert mid in hippo_api.store._memories  # 行存活

    def test_delete_own_ok(self, env):
        client, hippo_api, _, mid = self._pair(env)
        resp = client.delete(f"/api/hippo/memories/{mid}", headers=_token(UID_A))
        assert resp.status_code == 200 and resp.json() == {"deleted": True}
        assert mid not in hippo_api.store._memories

    def test_orphan_memory_gate_default_only(self, env):
        client, hippo_api, _ = env
        mid = _add_memory(client, "orphan-byid20", content="孤儿记忆").json()["memory_id"]
        client.delete("/api/hippo/sessions/orphan-byid20", params={"user_id": "default"})
        assert client.get(f"/api/hippo/memories/{mid}", headers=_token(UID_A)).status_code == 404
        ok = client.get(f"/api/hippo/memories/{mid}")
        assert ok.status_code == 200  # 'default' 兜底可见


# ── 契约面：响应形状逐键不变 + 坏 token 电池 ──────────────────────────


class TestContracts:
    def test_create_response_shape_unchanged(self, env):
        client, _, _ = env
        resp = _add_memory(client, "shape-sess20", content="形状")
        assert resp.status_code == 200
        assert set(resp.json().keys()) == {
            "memory_id",
            "session_id",
            "retention",
            "created_at",
        }

    def test_list_and_get_shape_unchanged(self, env):
        client, _, _ = env
        mid = _add_memory(client, "shape-sess20b", content="形状").json()["memory_id"]
        listed = client.get("/api/hippo/memories").json()
        assert set(listed.keys()) == {"memories", "total"}
        assert set(listed["memories"][0].keys()) == {
            "memory_id",
            "session_id",
            "content",
            "importance",
            "tags",
            "retention",
            "access_count",
            "archived",
            "created_at",
            "last_accessed_at",
        }
        got = client.get(f"/api/hippo/memories/{mid}").json()
        assert set(got.keys()) == {
            "memory_id",
            "session_id",
            "content",
            "importance",
            "tags",
            "metadata",
            "retention",
            "access_count",
            "archived",
            "created_at",
            "last_accessed_at",
        }

    def test_missing_memory_404_contract_unchanged(self, env):
        client, _, _ = env
        assert client.get("/api/hippo/memories/no-such-mid").status_code == 404
        assert (
            client.patch("/api/hippo/memories/no-such-mid", json={"content": "x"}).status_code
            == 404
        )
        assert client.delete("/api/hippo/memories/no-such-mid").status_code == 404

    def test_bad_token_401_battery(self, env):
        client, hippo_api, _ = env
        mid = _add_memory(client, "bt-sess20", content="坏 token 电池").json()["memory_id"]
        for method, url, body in (
            ("POST", "/api/hippo/memories", {"content": "x", "session_id": "bt-s2"}),
            ("GET", "/api/hippo/memories", None),
            ("GET", f"/api/hippo/memories/{mid}", None),
            ("PATCH", f"/api/hippo/memories/{mid}", {"content": "x"}),
            ("DELETE", f"/api/hippo/memories/{mid}", None),
        ):
            resp = client.request(method, url, json=body, headers=BAD_TOKEN, params={"limit": 5})
            assert resp.status_code == 401, (method, url)
        assert mid in hippo_api.store._memories  # 零副作用


# ── 静态接线普查（写了≠接线了） ──────────────────────────────────────


class TestStaticWiring:
    def test_five_endpoints_bound_identity_and_gated(self):
        src = Path("src/api/hippo.py").read_text()
        tree = ast.parse(src)
        targets = {
            "create_memory",
            "list_memories",
            "get_memory",
            "update_memory",
            "delete_memory",
        }
        found = {}
        gate_hits = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                calls = [
                    n.func.id
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                ]
                if node.name in targets:
                    found[node.name] = calls
                if "_gate_memory" in calls:
                    gate_hits.append(node.name)
        assert set(found) == targets
        for name, calls in found.items():
            assert "bind_identity" in calls, name
        # by-ID 三口走记忆归属闸
        assert set(gate_hits) == {"get_memory", "update_memory", "delete_memory"}
        # 读闸专用 peek 在 store 真定义（闸读零副作用原语）
        store_src = Path("src/hippo/memory_store.py").read_text()
        assert "def peek(" in store_src
        assert "_memory_owner" in src and "_gate_session_owner" in src
