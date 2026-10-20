"""hippo /sessions user_id 轴 bind_identity 收口测试（会话登记另轴）。

F3b-⑮ 复核登记「hippo /sessions list user_id 无鉴权=会话登记轴非知识档案
家族（另轴候选）」执行件：修前 create/list/get/archive/delete 五端点 user_id
完全客户端自报且零鉴权——任意人可冒写他人名义会话、看全部会话（缺省=全量
泄露）、冒读/冒删任意会话（by-ID 零归属闸）。

语义与 F3b-⑥ 逐字一致：认证=派生、自报只可确认绑定、矛盾=403「user mismatch」、
'default'/空/占位零 UUID（NIL_UUID）=未声明→取派生、匿名=保留自报（既有
本地多档案语义契约零变化）、坏 token=401 零副作用。

会话登记另轴（F3b-⑮ 定性）：行键=identity.user_id 原值（users.id 字符串），
刻意不经档案键家族正典 _resolve_user_id（那是知识档案键；会话登记轴历史行
即原值形，md5 归一会让既有行脱靶）。

数据面=tmp 全隔离（USERS_DB/ENTERPRISE_DB 随 tmp + SessionManager/gate 换
tmp 实例），绝不写生产库。
"""

import ast
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
    """hippo 路由进程内测试面（test_session_touch_wiring env 同款路线）+
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


def _create(client, headers=None, **kw):
    resp = client.post("/api/hippo/sessions", json=kw, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── create：行归属=认证派生（会话登记另轴=原值键） ──────────────────


class TestCreateAxis:
    def test_authed_derived_attribution(self, env):
        client, hippo_api, _ = env
        resp = client.post("/api/hippo/sessions", json={"title": "A 的会话"}, headers=_token(UID_A))
        assert resp.status_code == 200
        data = resp.json()
        # 另轴锁死：行键=users.id 原值，非档案键家族 md5 正典
        assert data["user_id"] == UID_A
        assert hippo_api.sessions.get(data["session_id"]).user_id == UID_A

    def test_create_response_additive_user_id_key(self, env):
        client, _, _ = env
        data = _create(client, user_id="raw_user", title="t")
        # 加性键=真写入键如实报；既有键逐键不变
        assert set(data.keys()) == {"session_id", "user_id", "title", "status", "created_at"}
        assert data["user_id"] == "raw_user"

    def test_sentinel_takes_derived(self, env):
        client, hippo_api, _ = env
        for sentinel in ("default", "", NIL_UUID):
            resp = client.post(
                "/api/hippo/sessions", json={"user_id": sentinel}, headers=_token(UID_A)
            )
            assert resp.status_code == 200, sentinel
            sid = resp.json()["session_id"]
            assert hippo_api.sessions.get(sid).user_id == UID_A, sentinel

    def test_declared_confirm_binding_ok(self, env):
        client, _, _ = env
        resp = client.post("/api/hippo/sessions", json={"user_id": UID_A}, headers=_token(UID_A))
        assert resp.status_code == 200
        assert resp.json()["user_id"] == UID_A

    def test_conflict_403_zero_side_effect(self, env):
        client, hippo_api, _ = env
        resp = client.post("/api/hippo/sessions", json={"user_id": "victim"}, headers=_token(UID_A))
        assert resp.status_code == 403
        assert "user mismatch" in resp.text
        assert hippo_api.sessions.list_sessions() == []  # 行未创建硬反证

    def test_anonymous_self_report_preserved(self, env):
        client, hippo_api, _ = env
        data = _create(client, user_id="test_user", title="anon")
        assert data["user_id"] == "test_user"  # 匿名自报契约零变化
        assert hippo_api.sessions.get(data["session_id"]).user_id == "test_user"

    def test_anonymous_default_bucket(self, env):
        client, _, _ = env
        data = _create(client, title="anon default")
        assert data["user_id"] == "default"

    def test_bad_token_401_zero_side_effect(self, env):
        client, hippo_api, _ = env
        resp = client.post("/api/hippo/sessions", json={"title": "x"}, headers=BAD_TOKEN)
        assert resp.status_code == 401
        assert hippo_api.sessions.list_sessions() == []


# ── list：「用户看自己的」（修前缺省=全量泄露） ────────────────────


class TestListAxis:
    def test_authed_sees_only_own(self, env):
        client, _, _ = env
        _create(client, title="a1", headers=_token(UID_A))
        _create(client, title="a2", headers=_token(UID_A))
        _create(client, title="b1", headers=_token(UID_B))
        resp = client.get("/api/hippo/sessions", headers=_token(UID_A))
        assert resp.status_code == 200
        rows = resp.json()["sessions"]
        assert len(rows) == 2  # 修前缺省=全量 3 条（硬反证在控制组）
        assert all(s["user_id"] == UID_A for s in rows)

    def test_conflict_403(self, env):
        client, _, _ = env
        resp = client.get("/api/hippo/sessions", params={"user_id": UID_B}, headers=_token(UID_A))
        assert resp.status_code == 403
        assert "user mismatch" in resp.text

    def test_sentinel_takes_derived(self, env):
        client, _, _ = env
        _create(client, title="a1", headers=_token(UID_A))
        _create(client, title="b1", headers=_token(UID_B))
        for sentinel in ("default", "", NIL_UUID):
            resp = client.get(
                "/api/hippo/sessions", params={"user_id": sentinel}, headers=_token(UID_A)
            )
            assert resp.status_code == 200, sentinel
            rows = resp.json()["sessions"]
            assert [s["user_id"] for s in rows] == [UID_A], sentinel

    def test_anonymous_self_report_preserved(self, env):
        client, _, _ = env
        _create(client, user_id="alice-x", title="x")
        _create(client, user_id="bob-y", title="y")
        resp = client.get("/api/hippo/sessions", params={"user_id": "alice-x"})
        rows = resp.json()["sessions"]
        assert [s["user_id"] for s in rows] == ["alice-x"]

    def test_anonymous_default_bucket_no_leak(self, env):
        client, _, _ = env
        _create(client, user_id="alice-x", title="x")
        data = _create(client, title="d")
        resp = client.get("/api/hippo/sessions")
        rows = resp.json()["sessions"]
        assert [s["session_id"] for s in rows] == [data["session_id"]]  # 修前全量泄露


# ── by-ID 归属闸：越权=同缺行 404（禁存在性探测） ──────────────────


class TestByIdGate:
    def test_get_owner_ok(self, env):
        client, _, _ = env
        sid = _create(client, title="a", headers=_token(UID_A))["session_id"]
        resp = client.get(f"/api/hippo/sessions/{sid}", headers=_token(UID_A))
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == UID_A
        assert set(data.keys()) == {
            "session_id",
            "user_id",
            "title",
            "status",
            "memory_count",
            "total_tokens",
            "created_at",
            "last_active_at",
            "metadata",
        }

    def test_get_cross_user_404_same_shape_as_missing(self, env):
        client, _, _ = env
        sid = _create(client, title="a", headers=_token(UID_A))["session_id"]
        resp = client.get(f"/api/hippo/sessions/{sid}", headers=_token(UID_B))
        missing = client.get("/api/hippo/sessions/no-such-sess", headers=_token(UID_B))
        assert resp.status_code == 404
        assert resp.json() == missing.json()  # 与缺行响应形状逐字一致，禁存在性探测

    def test_get_rejected_read_zero_rewrite(self, env):
        client, hippo_api, _ = env
        sid = _create(client, title="a", headers=_token(UID_A))["session_id"]
        before = hippo_api.sessions._sessions[sid].last_active_at
        resp = client.get(f"/api/hippo/sessions/{sid}", headers=_token(UID_B))
        assert resp.status_code == 404
        # 被拒读取行零改写（闸读走 peek；修前 get() 会刷新 last_active_at）
        assert hippo_api.sessions._sessions[sid].last_active_at == before

    def test_get_owner_read_still_refreshes(self, env):
        client, hippo_api, _ = env
        sid = _create(client, title="a", headers=_token(UID_A))["session_id"]
        before = hippo_api.sessions._sessions[sid].last_active_at
        resp = client.get(f"/api/hippo/sessions/{sid}", headers=_token(UID_A))
        assert resp.status_code == 200
        assert hippo_api.sessions._sessions[sid].last_active_at > before  # 既有语义零变化

    def test_anonymous_self_report_match_ok(self, env):
        client, _, _ = env
        sid = _create(client, user_id="test_user", title="x")["session_id"]
        resp = client.get(f"/api/hippo/sessions/{sid}", params={"user_id": "test_user"})
        assert resp.status_code == 200  # 匿名自报保留

    def test_anonymous_no_declared_cannot_touch_foreign_row(self, env):
        client, _, _ = env
        sid = _create(client, user_id="test_user", title="x")["session_id"]
        resp = client.get(f"/api/hippo/sessions/{sid}")
        assert resp.status_code == 404  # 缺省='default' 桶，非无闸

    def test_archive_cross_user_404_row_alive(self, env):
        client, hippo_api, _ = env
        sid = _create(client, title="a", headers=_token(UID_A))["session_id"]
        resp = client.post(f"/api/hippo/sessions/{sid}/archive", headers=_token(UID_B))
        assert resp.status_code == 404
        assert hippo_api.sessions._sessions[sid].status.value == "active"  # 行零改写

    def test_archive_owner_ok(self, env):
        client, hippo_api, _ = env
        sid = _create(client, title="a", headers=_token(UID_A))["session_id"]
        resp = client.post(f"/api/hippo/sessions/{sid}/archive", headers=_token(UID_A))
        assert resp.status_code == 200
        assert resp.json() == {"archived": True}  # 响应契约逐键不变
        assert hippo_api.sessions._sessions[sid].status.value == "archived"

    def test_delete_cross_user_404_row_alive_grants_untouched(self, env):
        client, hippo_api, gate = env
        sid = _create(client, title="a", headers=_token(UID_A))["session_id"]
        gate.grant(sid, "mcp-github", "*")
        resp = client.delete(f"/api/hippo/sessions/{sid}", headers=_token(UID_B))
        assert resp.status_code == 404
        assert hippo_api.sessions.get(sid) is not None  # 行存活
        assert len(gate.list_grants(sid)) == 1  # 清理不越权执行

    def test_delete_owner_ok_clears_grants(self, env):
        client, hippo_api, gate = env
        sid = _create(client, title="a", headers=_token(UID_A))["session_id"]
        gate.grant(sid, "mcp-github", "*")
        resp = client.delete(f"/api/hippo/sessions/{sid}", headers=_token(UID_A))
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}  # 响应契约逐键不变
        assert hippo_api.sessions.get(sid) is None
        assert gate.list_grants(sid) == []  # H1-②b-⑤ 接线零回归

    def test_delete_missing_404_contract_unchanged(self, env):
        client, _, gate = env
        gate.grant("ghost-sess", "mcp-github", "*")
        resp = client.delete("/api/hippo/sessions/ghost-sess")
        assert resp.status_code == 404  # 缺行 404 契约不变
        assert len(gate.list_grants("ghost-sess")) == 1

    def test_bad_token_401_battery_zero_side_effect(self, env):
        client, hippo_api, gate = env
        sid = _create(client, title="a", headers=_token(UID_A))["session_id"]
        gate.grant(sid, "mcp-github", "*")
        for method, url in (
            ("GET", f"/api/hippo/sessions/{sid}"),
            ("POST", f"/api/hippo/sessions/{sid}/archive"),
            ("DELETE", f"/api/hippo/sessions/{sid}"),
        ):
            resp = client.request(method, url, headers=BAD_TOKEN)
            assert resp.status_code == 401, (method, url)
        assert hippo_api.sessions._sessions[sid].status.value == "active"  # 零副作用
        assert len(gate.list_grants(sid)) == 1


# ── 契约不变面 + 行归属兜底 ──────────────────────────────────────────


class TestContractsAndFallback:
    def test_lifecycle_check_system_face_unchanged(self, env):
        client, _, _ = env
        resp = client.post("/api/hippo/sessions/lifecycle-check")
        assert resp.status_code == 200  # 系统维护面零身份轴（既有契约）

    def test_auto_create_fallback_default_bucket(self, env):
        client, hippo_api, _ = env
        resp = client.post(
            "/api/hippo/memories",
            json={"content": "auto session 记忆", "session_id": "auto-sess-f3b18"},
        )
        assert resp.status_code == 200
        rows = hippo_api.sessions.list_sessions()
        row = next(s for s in rows if s.title == "Auto: auto-sess-f3b18")
        # 无身份轴写点行归属 'default' 兜底（修前 user_id="" 无主不可达）
        assert row.user_id == "default"

    def test_list_element_shape_unchanged(self, env):
        client, _, _ = env
        _create(client, title="shape", headers=_token(UID_A))
        rows = client.get("/api/hippo/sessions", headers=_token(UID_A)).json()["sessions"]
        assert set(rows[0].keys()) == {
            "session_id",
            "user_id",
            "title",
            "status",
            "memory_count",
            "created_at",
            "last_active_at",
        }


# ── 静态接线普查（写了≠接线了） ────────────────────────────────────


class TestStaticWiring:
    def test_five_endpoints_bound_identity(self):
        src = Path("src/api/hippo.py").read_text()
        tree = ast.parse(src)
        targets = {
            "create_session",
            "list_sessions",
            "get_session",
            "archive_session",
            "delete_session",
        }
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in targets:
                calls = [
                    n.func.id
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                ]
                arg_names = [a.arg for a in node.args.args]
                found[node.name] = ("bind_identity" in calls, "authorization" in arg_names)
        assert set(found) == targets
        for name, (has_bind, has_auth) in found.items():
            assert has_bind, name
            assert has_auth, name

    def test_gate_reads_via_peek(self):
        """闸读必须走 peek（零改写），禁直接 get() 闸行。"""
        src = Path("src/api/hippo.py").read_text()
        tree = ast.parse(src)
        for name in ("get_session", "archive_session", "delete_session"):
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                    calls = [
                        n.func.attr
                        for n in ast.walk(node)
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    ]
                    assert "peek" in calls, name
