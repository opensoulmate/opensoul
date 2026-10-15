"""mirror 沙箱管理面（SandboxManager）行租户键加轴测试（DEC-⑦-㉓）。

修前（HEAD 源码实证）：src/mirror/sandbox.py 零租户轴——Sandbox 行无
tenant_id 字段、create 写点丢身份（api create 有 bind_identity 但 create()
不收租户）、GET/DELETE /sandboxes/{id} 与 pause/resume/log/logs/variables/
snapshot/network-policy/exec/fork 全部零鉴权零归属闸（持有 sandbox_id 跨租户
可读 variables 全文/可 pause/可 set_variable/可 destroy 真删/可 exec 真执行/
可 fork 拿源快照建新沙箱）、list/stats/cleanup 恒系统视图（跨租户列表+全局
计数+跨租户清理）。

收口=DEC-⑦ 家族同款（⑦-㉒b mirror 模板面直接先例）：
① sandbox_row_tenant 行租户键单一判法（自带键缺失/空白→'default' 兜底，写读
   同源禁消费点另发明判法 F4b-㉗ 纪律）
② Sandbox.tenant_id 行键 + create 写点归属（bind_identity 派生落行，匿名
   'default' 契约不变；instantiate 产物同源）+ fork 行归属=归克隆者（⑦-⑥）
③ 读面恒过滤（list/status 过滤先于计数）+ by-ID 单点归属闸 get（显式=跨租户
   与 not-found 同契约不差分泄露 ③c-1，None=系统视图字节不变）覆盖
   get/destroy/pause/resume/log/set_variable/get_variable/snapshot/get_log/
   network_policy/set_network_policy/exec/fork 源读全部 by-ID 面
④ destroy 归属闸（跨租户=False 与 not-found 同契约、行零破坏 ③b-3）
⑤ stats/cleanup 聚合作用域（恒按可见集，③a purge 同款防"只给租户"变相清
   他租户行）；/health 保持系统视图全量
⑥ API 沙箱面 bind_identity 派生流入（坏 token 401 先于读/写零副作用）+读面
   输出加性键 tenant_id（行归属可观测）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/tmp SandboxManager/tmp
TemplateEngine/tmp TimelineStore/run_in_sandbox 桩），绝不写生产
~/opensoul/sandboxes、不打 live :8090。
"""

import sqlite3
import time
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)


def _seed_user(db_path, uid, username, tenant="default"):
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
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
    """tmp 身份库 + tmp 沙箱/模板面 + 真 router 真 bind_identity。"""
    users_db = tmp_path / "users.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.mirror as mirror_api
    from src.mirror import isolation as isolation_mod
    from src.mirror.isolation import ExecResult
    from src.mirror.sandbox import SandboxManager
    from src.mirror.templates import SandboxTemplateEngine

    manager = SandboxManager(tmp_path / "sandboxes")
    monkeypatch.setattr(mirror_api, "manager", manager)
    monkeypatch.setattr(
        mirror_api, "template_engine", SandboxTemplateEngine(str(tmp_path / "templates.db"))
    )

    # 真隔离执行换桩（bwrap 环境依赖不进测试面；归属闸在 run 之前是本拍被测物）
    def _stub_run(command, workspace, network_policy="deny", timeout=30, env=None):
        return ExecResult(
            command=command,
            exit_code=0,
            stdout="stub-out\n",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            stdout_total_bytes=9,
            stderr_total_bytes=0,
            timed_out=False,
            duration_s=0.01,
            network_policy=network_policy,
        )

    monkeypatch.setattr(isolation_mod, "run_in_sandbox", _stub_run)

    # timeline 事件面 tmp（push_event._timeline 注入，绝不写生产 timeline.db）
    from src.nerve import event_bridge
    from src.timeline.store import TimelineStore

    store = TimelineStore(str(tmp_path / "timeline.db"))
    monkeypatch.setattr(event_bridge.push_event, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    app = FastAPI()
    app.include_router(mirror_api.router, prefix="/api/mirror")
    client = TestClient(app)
    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_d = _seed_user(users_db, str(uuid.uuid4()), "carol-d", "default")

    yield {
        "client": client,
        "manager": manager,
        "user_a": user_a,
        "user_b": user_b,
        "user_d": user_d,
    }


def _create(c, headers, name="s-x", **kw):
    body = {"name": name}
    body.update(kw)
    return c.post("/api/mirror/sandboxes", json=body, headers=headers)


# ── ① 行租户键单一判法 ──────────────────────────────────────────


class TestJudgmentSingleSource:
    def test_row_tenant_shapes(self):
        from src.mirror.sandbox import Sandbox, sandbox_row_tenant

        row = Sandbox(sandbox_id="sandbox-1", name="n", created_at=1.0, tenant_id="tenant-x")
        assert sandbox_row_tenant(row) == "tenant-x"
        assert sandbox_row_tenant("tenant-y") == "tenant-y"
        assert sandbox_row_tenant(None) == "default"
        assert sandbox_row_tenant("") == "default"
        assert sandbox_row_tenant("   ") == "default"
        bare = Sandbox(sandbox_id="sandbox-2", name="n", created_at=1.0)
        assert sandbox_row_tenant(bare) == "default"

    def test_write_read_single_source(self):
        """写侧落行与读侧过滤同一判法（monkeypatch spy 硬证禁双源）。"""
        from src.mirror.sandbox import SandboxManager

        mgr = SandboxManager(None)
        s = mgr.create(name="w", tenant_id="  tenant-z  ")
        assert s.tenant_id == "tenant-z"
        assert mgr.get(s.sandbox_id, tenant_id="tenant-z") is s


# ── ② 写点归属 ─────────────────────────────────────────────────


class TestWriteAttribution:
    def test_api_create_row_tenant_derived(self, env):
        c = env["client"]
        r = _create(c, _token(env["user_a"]), "s-a")
        assert r.status_code == 200
        sid = r.json()["sandbox_id"]
        row = env["manager"].get(sid)  # 系统视图直读行
        assert row.tenant_id == "tenant-a"  # 非 'default'=真接派生

    def test_api_create_anonymous_default(self, env):
        c = env["client"]
        sid = _create(c, None, "s-anon").json()["sandbox_id"]
        assert env["manager"].get(sid).tenant_id == "default"

    def test_instantiate_row_tenant_derived(self, env):
        c = env["client"]
        h = _token(env["user_a"])
        tid = c.post("/api/mirror/templates", json={"name": "tpl-i"}, headers=h).json()[
            "template_id"
        ]
        r = c.post(
            f"/api/mirror/templates/{tid}/instantiate",
            json={"template_id": tid},
            headers=h,
        )
        assert r.status_code == 200
        sid = r.json()["sandbox_id"]
        assert env["manager"].get(sid).tenant_id == "tenant-a"

    def test_fork_child_belongs_to_cloner(self, env):
        """fork 归克隆者（⑦-⑥）：API fork 子行=调用方派生租户。"""
        c = env["client"]
        h = _token(env["user_a"])
        sid = _create(c, h, "s-fork").json()["sandbox_id"]
        snap = c.post(f"/api/mirror/sandboxes/{sid}/snapshot", json={"name": "snap-1"}, headers=h)
        assert snap.status_code == 200
        r = c.post(
            f"/api/mirror/sandboxes/{sid}/fork",
            json={"snapshot_id": snap.json()["snapshot_id"]},
            headers=h,
        )
        assert r.status_code == 200
        child_id = r.json()["sandbox_id"]
        assert env["manager"].get(child_id).tenant_id == "tenant-a"

    def test_fork_no_identity_inherits_source_row(self, env):
        """无身份内部面：子行=源行继承（禁掉落 'default' 跨租户可见）。"""
        from src.mirror.sandbox import SandboxManager

        mgr = SandboxManager(None)
        src = mgr.create(name="src", tenant_id="tenant-a")
        snap = mgr.snapshot(src.sandbox_id, "s1", tenant_id="tenant-a")
        assert snap is not None
        child = mgr.fork_from_snapshot(src.sandbox_id, snap["snapshot_id"])
        assert child is not None
        assert child.tenant_id == "tenant-a"


# ── ③ 读面恒过滤/聚合作用域 ─────────────────────────────────────


class TestBulkFilter:
    def test_list_three_way_isolation(self, env):
        c = env["client"]
        sid_a = _create(c, _token(env["user_a"]), "s-a").json()["sandbox_id"]
        sid_b = _create(c, _token(env["user_b"]), "s-b").json()["sandbox_id"]
        sid_d = _create(c, None, "s-d").json()["sandbox_id"]

        rows_a = c.get("/api/mirror/sandboxes", headers=_token(env["user_a"])).json()["sandboxes"]
        rows_b = c.get("/api/mirror/sandboxes", headers=_token(env["user_b"])).json()["sandboxes"]
        rows_d = c.get("/api/mirror/sandboxes").json()["sandboxes"]
        assert [r["sandbox_id"] for r in rows_a] == [sid_a]
        assert [r["sandbox_id"] for r in rows_b] == [sid_b]
        assert [r["sandbox_id"] for r in rows_d] == [sid_d]

    def test_list_additive_tenant_key(self, env):
        c = env["client"]
        _create(c, _token(env["user_a"]), "s-a")
        rows = c.get("/api/mirror/sandboxes", headers=_token(env["user_a"])).json()["sandboxes"]
        assert rows[0]["tenant_id"] == "tenant-a"

    def test_stats_scoped(self, env):
        c = env["client"]
        _create(c, _token(env["user_a"]), "s-a1")
        _create(c, _token(env["user_a"]), "s-a2")
        _create(c, _token(env["user_b"]), "s-b1")
        _create(c, None, "s-d1")
        st_a = c.get("/api/mirror/stats", headers=_token(env["user_a"])).json()
        assert st_a["total_sandboxes"] == 2
        assert st_a["active"] == 2
        assert st_a["paused"] == 0
        # 全局配置键恒全局
        assert "sandbox_dir" in st_a
        assert "isolation_available" in st_a

    def test_health_system_view_unchanged(self, env):
        c = env["client"]
        _create(c, _token(env["user_a"]), "s-a")
        _create(c, _token(env["user_b"]), "s-b")
        _create(c, None, "s-d")
        st = c.get("/api/mirror/health").json()
        assert st["total_sandboxes"] == 3  # /health 系统视图全量不变

    def test_get_additive_tenant_key(self, env):
        c = env["client"]
        sid = _create(c, _token(env["user_a"]), "s-a").json()["sandbox_id"]
        r = c.get(f"/api/mirror/sandboxes/{sid}", headers=_token(env["user_a"]))
        assert r.json()["tenant_id"] == "tenant-a"


# ── ④ by-ID 归属闸（跨租户与 not-found 同契约）──────────────────


class TestByIDGate:
    def test_cross_tenant_get_same_contract_as_not_found(self, env):
        c = env["client"]
        sid = _create(c, _token(env["user_a"]), "s-a").json()["sandbox_id"]
        miss = c.get("/api/mirror/sandboxes/sandbox-nonexistent", headers=_token(env["user_b"]))
        cross = c.get(f"/api/mirror/sandboxes/{sid}", headers=_token(env["user_b"]))
        assert miss.status_code == cross.status_code == 404
        assert miss.json() == cross.json()  # 同契约不差分泄露

    def test_cross_tenant_destroy_zero_side_effect(self, env):
        c = env["client"]
        h_a = _token(env["user_a"])
        sid = _create(c, h_a, "s-a").json()["sandbox_id"]
        r = c.delete(f"/api/mirror/sandboxes/{sid}", headers=_token(env["user_b"]))
        assert r.status_code == 404
        row = env["manager"].get(sid)  # 行零破坏
        assert row is not None
        assert row.name == "s-a"
        # 同租户正对照
        assert c.delete(f"/api/mirror/sandboxes/{sid}", headers=h_a).status_code == 200

    def test_cross_tenant_write_battery_zero_side_effect(self, env):
        c = env["client"]
        h_a = _token(env["user_a"])
        h_b = _token(env["user_b"])
        sid = _create(c, h_a, "s-a").json()["sandbox_id"]
        # pause/resume/log/variables/network-policy 跨租户全 404（resume 同 not-found 契约 400）
        assert c.post(f"/api/mirror/sandboxes/{sid}/pause", headers=h_b).status_code == 404
        assert (
            c.post(
                f"/api/mirror/sandboxes/{sid}/log", json={"action": "x"}, headers=h_b
            ).status_code
            == 404
        )
        assert (
            c.post(
                f"/api/mirror/sandboxes/{sid}/variables",
                json={"key": "k", "value": "v"},
                headers=h_b,
            ).status_code
            == 404
        )
        assert (
            c.post(
                f"/api/mirror/sandboxes/{sid}/network-policy",
                json={"policy": "allow"},
                headers=h_b,
            ).status_code
            == 404
        )
        assert (
            c.post(
                f"/api/mirror/sandboxes/{sid}/snapshot", json={"name": "snap"}, headers=h_b
            ).status_code
            == 404
        )
        # 行哨兵一字不动（变量/日志/状态/网络策略零改写）
        row = env["manager"].get(sid)
        assert row.variables == {}
        assert row.log == []
        assert row.status == "active"
        assert env["manager"].network_policy(sid) == "deny"
        assert row.snapshot_count == 0

    def test_cross_tenant_read_battery_same_contract(self, env):
        c = env["client"]
        h_a = _token(env["user_a"])
        h_b = _token(env["user_b"])
        sid = _create(c, h_a, "s-a").json()["sandbox_id"]
        c.post(
            f"/api/mirror/sandboxes/{sid}/variables", json={"key": "k", "value": "v"}, headers=h_a
        )
        # variables/logs 跨租户=404（与 not-found 同 detail）
        mv = c.get("/api/mirror/sandboxes/sandbox-nonexistent/variables", headers=h_b)
        xv = c.get(f"/api/mirror/sandboxes/{sid}/variables", headers=h_b)
        assert mv.status_code == xv.status_code == 404
        assert mv.json() == xv.json()
        ml = c.get("/api/mirror/sandboxes/sandbox-nonexistent/logs", headers=h_b)
        xl = c.get(f"/api/mirror/sandboxes/{sid}/logs", headers=h_b)
        assert ml.status_code == xl.status_code == 404
        assert ml.json() == xl.json()
        # 同租户正对照
        assert c.get(f"/api/mirror/sandboxes/{sid}/variables", headers=h_a).json() == {
            "variables": {"k": "v"}
        }

    def test_cross_tenant_exec_refused_zero_side_effect(self, env):
        c = env["client"]
        h_a = _token(env["user_a"])
        h_b = _token(env["user_b"])
        sid = _create(c, h_a, "s-a").json()["sandbox_id"]
        r = c.post(f"/api/mirror/sandboxes/{sid}/exec", json={"command": "echo hi"}, headers=h_b)
        assert r.status_code == 404
        # 零执行零留痕
        assert env["manager"].get_log(sid, tenant_id="tenant-a") == []
        # 同租户正对照（桩执行真留痕）
        r = c.post(f"/api/mirror/sandboxes/{sid}/exec", json={"command": "echo hi"}, headers=h_a)
        assert r.status_code == 200
        assert env["manager"].get_log(sid, tenant_id="tenant-a") != []

    def test_cross_tenant_fork_zero_child(self, env):
        c = env["client"]
        h_a = _token(env["user_a"])
        h_b = _token(env["user_b"])
        sid = _create(c, h_a, "s-a").json()["sandbox_id"]
        snap = c.post(
            f"/api/mirror/sandboxes/{sid}/snapshot", json={"name": "snap-1"}, headers=h_a
        ).json()
        before = env["manager"].stats()["total_sandboxes"]
        r = c.post(
            f"/api/mirror/sandboxes/{sid}/fork",
            json={"snapshot_id": snap["snapshot_id"]},
            headers=h_b,
        )
        assert r.status_code == 404
        assert env["manager"].stats()["total_sandboxes"] == before  # 零建行

    def test_same_tenant_positive_control(self, env):
        c = env["client"]
        h_a = _token(env["user_a"])
        sid = _create(c, h_a, "s-a").json()["sandbox_id"]
        assert c.get(f"/api/mirror/sandboxes/{sid}", headers=h_a).status_code == 200
        assert c.post(f"/api/mirror/sandboxes/{sid}/pause", headers=h_a).status_code == 200
        assert c.post(f"/api/mirror/sandboxes/{sid}/resume", headers=h_a).status_code == 200

    def test_anonymous_contract_unchanged(self, env):
        """匿名='default' 面既有语义零变化（创建/读/删照旧）。"""
        c = env["client"]
        sid = _create(c, None, "s-anon").json()["sandbox_id"]
        assert c.get(f"/api/mirror/sandboxes/{sid}").status_code == 200
        assert c.delete(f"/api/mirror/sandboxes/{sid}").status_code == 200


# ── ⑤ 清理作用域 ────────────────────────────────────────────────


class TestCleanupScope:
    def test_cleanup_scoped_by_tenant(self, env):
        c = env["client"]
        # 三个租户各建 1 个已过期沙箱（ttl=0 即时过期）
        sid_a = _create(c, _token(env["user_a"]), "s-a", ttl_seconds=0).json()["sandbox_id"]
        sid_b = _create(c, _token(env["user_b"]), "s-b", ttl_seconds=0).json()["sandbox_id"]
        sid_d = _create(c, None, "s-d", ttl_seconds=0).json()["sandbox_id"]
        time.sleep(0.01)
        r = c.post("/api/mirror/cleanup", headers=_token(env["user_a"]))
        assert r.status_code == 200
        assert r.json()["cleaned"] == 1  # 只清自己
        assert env["manager"].get(sid_a) is None
        assert env["manager"].get(sid_b) is not None  # 他租户行零破坏
        assert env["manager"].get(sid_d) is not None

    def test_cleanup_system_view_all(self, env):
        """系统视图（manager 层 None）=全量清理，既有语义字节不变。"""
        c = env["client"]
        _create(c, _token(env["user_a"]), "s-a", ttl_seconds=0)
        _create(c, _token(env["user_b"]), "s-b", ttl_seconds=0)
        time.sleep(0.01)
        assert env["manager"].cleanup_expired() == 2


# ── ⑥ API 身份轴（坏 token 401 先于读写零副作用）────────────────


class TestAPIIdentity:
    def test_bad_token_401_battery_zero_side_effect(self, env):
        c = env["client"]
        h_a = _token(env["user_a"])
        sid = _create(c, h_a, "s-a").json()["sandbox_id"]
        battery = [
            ("GET", f"/api/mirror/sandboxes/{sid}", None),
            ("DELETE", f"/api/mirror/sandboxes/{sid}", None),
            ("POST", f"/api/mirror/sandboxes/{sid}/pause", None),
            ("POST", f"/api/mirror/sandboxes/{sid}/resume", None),
            ("POST", f"/api/mirror/sandboxes/{sid}/log", {"action": "x"}),
            ("GET", f"/api/mirror/sandboxes/{sid}/logs", None),
            ("POST", f"/api/mirror/sandboxes/{sid}/variables", {"key": "k", "value": "v"}),
            ("GET", f"/api/mirror/sandboxes/{sid}/variables", None),
            ("POST", f"/api/mirror/sandboxes/{sid}/snapshot", {"name": "snap"}),
            ("POST", f"/api/mirror/sandboxes/{sid}/exec", {"command": "echo hi"}),
            ("POST", f"/api/mirror/sandboxes/{sid}/network-policy", {"policy": "allow"}),
            ("POST", f"/api/mirror/sandboxes/{sid}/fork", {"snapshot_id": "snap-x"}),
            ("GET", "/api/mirror/sandboxes", None),
            ("POST", "/api/mirror/cleanup", None),
        ]
        for method, url, body in battery:
            r = c.request(method, url, json=body, headers=BAD_TOKEN)
            assert r.status_code == 401, (method, url, r.status_code)
        # 零副作用：行哨兵一字不动
        row = env["manager"].get(sid)
        assert row is not None
        assert row.variables == {}
        assert row.log == []
        assert row.status == "active"
        assert row.snapshot_count == 0
        assert env["manager"].network_policy(sid) == "deny"


# ── ⑦ 环境隔离（MIRROR_SANDBOX_DIR）────────────────────────────


class TestEnvIsolation:
    def test_mirror_sandbox_dir_env(self, tmp_path, monkeypatch):
        from src.mirror.sandbox import SandboxManager

        target = tmp_path / "env-sandboxes"
        monkeypatch.setenv("MIRROR_SANDBOX_DIR", str(target))
        mgr = SandboxManager()
        assert mgr.sandbox_dir == target
        assert target.is_dir()
