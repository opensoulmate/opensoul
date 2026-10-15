"""limb 任务面行租户键加轴测试（DEC-⑦-㉔）。

修前（⑦-㉒ 留档+HEAD 源码实证）：RPATask 无 tenant_id、create 写点零身份轴、
/instantiate 产物任务无行键（⑦-㉒ 留档明记）、/tasks 全面（list/get/execute/
cancel/delete）与 /history 零鉴权零租户过滤（任意调用方可见/可执行/可删全部
租户任务）、stats 任务计数键全局泄露（⑦-㉒ 留档明记）。

收口=DEC-⑦ 家族同款（① _event_row_tenant~㉓ sandbox_row_tenant 先例）：
① task_row_tenant 行租户键单一判法（自带键缺失/空白→'default' 兜底，dict 行/
   带属性对象/原始值三形同收，写读同源禁消费点另发明判法 F4b-㉗ 纪律）
② RPATask.tenant_id 行键 + create 写点归属（bind_identity 派生，匿名 'default'
   契约不变）+ create_from_template 双职（tenant_id 同时=源读闸+产物行归属，
   ⑦-⑫ clone 双职同款）
③ by-ID 归属闸单点（_gate_task：get/execute/cancel/delete 四面全经本口）：
   显式租户=跨租户与 not-found 同契约不差分泄露（③c-1 纪律）、闸前置禁状态差分、
   行零破坏；None=系统视图字节不变
④ list/history 恒按行租户键过滤（过滤先于截断防跨租户行占名额，⑦-⑬ 同款）；
   历史行键=任务行继承（③b-2 父行继承）
⑤ stats 任务计数键聚合作用域（total_tasks/by_status/queue_length 按行键、
   running/total_* 按分租户桶计数，禁泄露全局分布 ⑦-⑭⑰ 同款）；/health 系统视图
⑥ API 任务面 7 端点 bind_identity 派生流入（坏 token 401 先于读/写零副作用）
   +读面输出加性键 tenant_id（行归属可观测）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB/fresh RPAExecutor），纯内存面无生产
库可写、不打 live :8090。
"""

import sqlite3
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


def _seed_user(db_path: Path, uid: str, username: str, tenant: str = "default") -> str:
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


BAD_TOKEN = {"Authorization": "Bearer not-a-real-token"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + fresh RPAExecutor + 真 router 真 bind_identity。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.limb as limb_api
    from src.limb.executor import RPAExecutor

    fresh = RPAExecutor()
    monkeypatch.setattr(limb_api, "executor", fresh)

    app = FastAPI()
    app.include_router(limb_api.router, prefix="/api/limb")
    client = TestClient(app)

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")

    yield {
        "client": client,
        "eng": fresh,
        "user_a": user_a,
        "user_b": user_b,
        "user_default": user_default,
    }


def _mk(c, headers, name="task-x", extra=None):
    body = {"name": name, "actions": []}
    if extra:
        body.update(extra)
    return c.post("/api/limb/tasks", json=body, headers=headers)


# ── ① 行租户键单一判法 ──────────────────────────────────────────


class TestJudgmentSingleSource:
    def test_row_tenant_three_shapes(self):
        from src.limb.executor import RPATask, task_row_tenant

        assert task_row_tenant(RPATask(task_id="t", name="n", actions=[])) == "default"
        row = RPATask(task_id="t", name="n", actions=[], tenant_id="tenant-a")
        assert task_row_tenant(row) == "tenant-a"
        assert task_row_tenant({"tenant_id": "tenant-a"}) == "tenant-a"
        assert task_row_tenant({"name": "x"}) == "default"  # dict 行缺键兜底
        assert task_row_tenant(None) == "default"
        assert task_row_tenant("") == "default"
        assert task_row_tenant("   ") == "default"
        assert task_row_tenant("tenant-a") == "tenant-a"
        assert task_row_tenant("  tenant-a  ") == "tenant-a"  # 归一值

    def test_judgment_consistent_with_template_face(self):
        from src.limb.executor import RPATask, task_row_tenant
        from src.limb.tasks import template_row_tenant

        # 同一归一语义（⑦-㉒ template_row_tenant）：两面判法一致性锁死
        for raw in (None, "", "   ", "tenant-a", RPATask(task_id="t", name="n", actions=[])):
            assert task_row_tenant(raw) == template_row_tenant(raw)

    def test_single_judgment_consumed(self, env, monkeypatch):
        """monkeypatch 翻转判法→写点落行/读面过滤/闸全真跟走=消费单一源非行内私抄。"""
        import src.limb.executor as ex

        orig = ex.task_row_tenant

        def masked(raw):
            # 翻转探针：A 行在读侧被判成 'default'（写侧原始值照常归一）——写点/
            # 过滤/闸真消费判法的硬证（若行内私抄判法，翻转不会生效）
            v = orig(raw)
            if not isinstance(raw, (str, type(None))) and v == "tenant-a":
                return "default"
            return v

        monkeypatch.setattr(ex, "task_row_tenant", masked)
        eng = env["eng"]
        task = eng.create_task(name="t", actions=[], tenant_id="tenant-a")
        assert task.tenant_id == "tenant-a"  # 写点落行走判法（原始值归一不变）
        assert eng.list_tasks(tenant_id="tenant-a") == []  # 读侧判法被翻转→不可见
        assert [t["task_id"] for t in eng.list_tasks(tenant_id="default")] == [task.task_id]
        assert eng.get_task(task.task_id, tenant_id="tenant-a") is None  # 闸跟判法
        assert eng.get_task(task.task_id, tenant_id="default") is not None


# ── ② create 写点归属 ──────────────────────────────────────────


class TestWriteAttribution:
    def test_create_derived_row(self, env):
        data = _mk(env["client"], _token(env["user_a"]), name="a-task").json()
        assert data["tenant_id"] == "tenant-a"
        row = env["eng"].get_task(data["task_id"])  # 系统视图取行
        assert row.tenant_id == "tenant-a"

    def test_anonymous_default_contract(self, env):
        data = _mk(env["client"], None, name="anon-task").json()
        assert data["tenant_id"] == "default"

    def test_self_report_ignored(self, env):
        """请求体自报 tenant_id 不进落行（派生轴纯，pydantic 忽略未声明字段）。"""
        data = _mk(env["client"], None, name="atk", extra={"tenant_id": "attacker-tenant"}).json()
        assert data["tenant_id"] == "default"

    def test_bad_token_401_zero_row(self, env):
        r = _mk(env["client"], BAD_TOKEN, name="bad")
        assert r.status_code == 401
        assert env["eng"].stats()["total_tasks"] == 0  # 零副作用硬证


# ── ③ instantiate 产物行归属（双职） ────────────────────────────


class TestInstantiateProduct:
    def test_product_row_inherits_instantiator(self, env):
        c = env["client"]
        r = c.post(
            "/api/limb/templates/tpl-form-fill/instantiate",
            json={"variables": {"url": "http://x"}, "name": "from-tpl"},
            headers=_token(env["user_a"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["tenant_id"] == "tenant-a"  # 产物行=实例化者派生租户
        assert env["eng"].get_task(data["task_id"], tenant_id="tenant-a") is not None

    def test_cross_tenant_source_gate_zero_product(self, env):
        c = env["client"]
        tpl = _create_template(c, _token(env["user_a"]), "tpl-a")
        before = env["eng"].stats()["total_tasks"]
        r = c.post(
            f"/api/limb/templates/{tpl}/instantiate",
            json={},
            headers=_token(env["user_b"]),
        )
        assert r.status_code == 404  # 源读闸（⑦-㉒）
        assert env["eng"].stats()["total_tasks"] == before  # 零产物零副作用


def _create_template(c, headers, name):
    r = c.post(
        "/api/limb/templates",
        json={"name": name, "category": "custom", "actions": []},
        headers=headers,
    )
    return r.json()["template_id"]


# ── ④ by-ID 归属闸（get/execute/cancel/delete 四面） ─────────────


class TestByIdGate:
    def test_own_read_additive_key(self, env):
        c = env["client"]
        h = _token(env["user_a"])
        tid = _mk(c, h, name="own").json()["task_id"]
        data = c.get(f"/api/limb/tasks/{tid}", headers=h).json()
        assert data["task_id"] == tid
        assert data["tenant_id"] == "tenant-a"  # 加性键
        assert data["error"] == ""  # 既有键逐键不变

    def test_cross_get_same_contract_as_missing(self, env):
        c = env["client"]
        tid = _mk(c, _token(env["user_a"]), name="a-row").json()["task_id"]
        cross = c.get(f"/api/limb/tasks/{tid}", headers=_token(env["user_b"]))
        missing = c.get("/api/limb/tasks/task-gone", headers=_token(env["user_b"]))
        assert cross.status_code == missing.status_code == 404
        assert cross.json() == missing.json()  # 同契约不差分泄露行存在性

    def test_cross_execute_404_row_untouched(self, env):
        c = env["client"]
        tid = _mk(c, _token(env["user_a"]), name="a-row").json()["task_id"]
        r = c.post(f"/api/limb/tasks/{tid}/execute", headers=_token(env["user_b"]))
        assert r.status_code == 404
        row = env["eng"].get_task(tid)  # 系统视图回读
        assert row.status.value == "queued" and row.started_at is None  # 行零改写

    def test_cross_cancel_same_contract_row_untouched(self, env):
        c = env["client"]
        h_b = _token(env["user_b"])
        tid = _mk(c, _token(env["user_a"]), name="a-row").json()["task_id"]
        cross = c.post(f"/api/limb/tasks/{tid}/cancel", headers=h_b)
        missing = c.post("/api/limb/tasks/task-gone/cancel", headers=h_b)
        assert cross.status_code == missing.status_code == 400  # 同契约
        assert cross.json() == missing.json()
        assert env["eng"].get_task(tid).status.value == "queued"  # 行零改写

    def test_cross_delete_same_contract_row_survives(self, env):
        c = env["client"]
        h_b = _token(env["user_b"])
        tid = _mk(c, _token(env["user_a"]), name="a-row").json()["task_id"]
        cross = c.delete(f"/api/limb/tasks/{tid}", headers=h_b)
        missing = c.delete("/api/limb/tasks/task-gone", headers=h_b)
        assert cross.status_code == missing.status_code == 400  # 同契约
        assert env["eng"].get_task(tid) is not None  # 行存活零破坏

    def test_own_write_positive_control(self, env):
        c = env["client"]
        h = _token(env["user_a"])
        t1 = _mk(c, h, name="to-cancel").json()["task_id"]
        assert c.post(f"/api/limb/tasks/{t1}/cancel", headers=h).status_code == 200
        t2 = _mk(c, h, name="to-exec").json()["task_id"]
        r = c.post(f"/api/limb/tasks/{t2}/execute", headers=h)
        assert r.status_code == 200 and r.json()["status"] == "completed"
        assert c.delete(f"/api/limb/tasks/{t1}", headers=h).status_code == 200
        assert c.delete(f"/api/limb/tasks/{t2}", headers=h).status_code == 200


# ── ⑤ list/history 恒过滤 + 过滤先于截断 ────────────────────────


class TestListHistoryFilter:
    def test_list_three_faces(self, env):
        c = env["client"]
        _mk(c, _token(env["user_a"]), name="a1")
        _mk(c, _token(env["user_a"]), name="a2")
        _mk(c, _token(env["user_b"]), name="b1")
        _mk(c, None, name="d1")
        a_names = {
            t["name"]
            for t in c.get("/api/limb/tasks", headers=_token(env["user_a"])).json()["tasks"]
        }
        b_names = {
            t["name"]
            for t in c.get("/api/limb/tasks", headers=_token(env["user_b"])).json()["tasks"]
        }
        anon_names = {t["name"] for t in c.get("/api/limb/tasks").json()["tasks"]}
        assert a_names == {"a1", "a2"} and b_names == {"b1"} and anon_names == {"d1"}

    def test_list_filter_before_truncation(self, env):
        c = env["client"]
        _mk(c, _token(env["user_a"]), name="a-old")
        _mk(c, _token(env["user_b"]), name="b-new")  # 更晚，limit=1 不吃 A 名额
        out = c.get("/api/limb/tasks", params={"limit": 1}, headers=_token(env["user_a"])).json()[
            "tasks"
        ]
        assert [t["name"] for t in out] == ["a-old"]

    def test_history_scoped_additive_key(self, env):
        c = env["client"]
        for h, n in ((_token(env["user_a"]), "a-done"), (_token(env["user_b"]), "b-done")):
            tid = _mk(c, h, name=n).json()["task_id"]
            c.post(f"/api/limb/tasks/{tid}/execute", headers=h)
        ha = c.get("/api/limb/history", headers=_token(env["user_a"])).json()["history"]
        hb = c.get("/api/limb/history", headers=_token(env["user_b"])).json()["history"]
        assert [h["name"] for h in ha] == ["a-done"] and [h["tenant_id"] for h in ha] == [
            "tenant-a"
        ]
        assert [h["name"] for h in hb] == ["b-done"] and [h["tenant_id"] for h in hb] == [
            "tenant-b"
        ]

    def test_history_filter_before_truncation(self, env):
        c = env["client"]
        tid = _mk(c, _token(env["user_a"]), name="a-old").json()["task_id"]
        c.post(f"/api/limb/tasks/{tid}/execute", headers=_token(env["user_a"]))
        tid2 = _mk(c, _token(env["user_b"]), name="b-new").json()["task_id"]
        c.post(f"/api/limb/tasks/{tid2}/execute", headers=_token(env["user_b"]))
        out = c.get("/api/limb/history", params={"limit": 1}, headers=_token(env["user_a"])).json()[
            "history"
        ]
        assert [h["name"] for h in out] == ["a-old"]  # 跨租户新行不吃名额


# ── ⑥ stats 聚合作用域 ─────────────────────────────────────────


class TestStatsScope:
    def test_task_keys_scoped(self, env):
        c = env["client"]
        h_a, h_b = _token(env["user_a"]), _token(env["user_b"])
        t1 = _mk(c, h_a, name="a1").json()["task_id"]
        t2 = _mk(c, h_a, name="a2").json()["task_id"]
        _mk(c, h_b, name="b1")
        c.post(f"/api/limb/tasks/{t1}/execute", headers=h_a)
        c.post(f"/api/limb/tasks/{t2}/cancel", headers=h_a)
        sa = c.get("/api/limb/stats", headers=h_a).json()
        sb = c.get("/api/limb/stats", headers=h_b).json()
        health = c.get("/api/limb/health").json()
        assert sa["total_tasks"] == 2 and sa["by_status"] == {"completed": 1, "cancelled": 1}
        assert sa["queue_length"] == 1  # 执行不摘队列（既有引擎语义），仅 cancel 摘 t2
        assert sb["total_tasks"] == 1 and sb["by_status"] == {"queued": 1}
        assert sb["queue_length"] == 1
        assert health["total_tasks"] == 3  # /health 系统视图=全量

    def test_counters_scoped_no_global_leak(self, env):
        c = env["client"]
        h_a, h_b = _token(env["user_a"]), _token(env["user_b"])
        for n in ("a1", "a2"):
            tid = _mk(c, h_a, name=n).json()["task_id"]
            c.post(f"/api/limb/tasks/{tid}/execute", headers=h_a)
        _mk(c, h_b, name="b1")
        sa = c.get("/api/limb/stats", headers=h_a).json()
        sb = c.get("/api/limb/stats", headers=h_b).json()
        assert (
            sa["total_executed"] == 2 and sa["total_succeeded"] == 2 and sa["success_rate"] == 100.0
        )
        assert sb["total_executed"] == 0 and sb["total_succeeded"] == 0 and sb["success_rate"] == 0
        assert sa["running"] == 0 and sb["running"] == 0

    def test_health_system_view_unchanged(self, env):
        c = env["client"]
        h_a = _token(env["user_a"])
        tid = _mk(c, h_a, name="a1").json()["task_id"]
        c.post(f"/api/limb/tasks/{tid}/execute", headers=h_a)
        health = c.get("/api/limb/health").json()
        sys_stats = env["eng"].stats()
        for key in (
            "total_tasks",
            "by_status",
            "queue_length",
            "running",
            "total_executed",
            "total_succeeded",
            "total_failed",
            "success_rate",
            "templates",
        ):
            assert health[key] == sys_stats[key]
        assert health["total_executed"] == 1 and health["templates"] == 4  # 全量=builtin


# ── ⑦ 系统视图/契约不变面 ──────────────────────────────────────


class TestSystemViewAndAuth:
    def test_system_view_byte_unchanged(self, env):
        """None=系统视图字节不变：跨租户可见全部（引擎内部/维护面既有语义）。"""
        eng = env["eng"]
        c = env["client"]
        t_a = _mk(c, _token(env["user_a"]), name="a1").json()["task_id"]
        _mk(c, _token(env["user_b"]), name="b1")
        assert len(eng.list_tasks()) == 2
        assert eng.get_task(t_a, tenant_id="tenant-b") is None
        assert eng.get_task(t_a) is not None  # 系统视图不闸
        tid_b = [t for t in eng.list_tasks() if t["name"] == "b1"][0]["task_id"]
        eng.execute_task(t_a)
        eng.execute_task(tid_b)
        assert len(eng.get_history()) == 2  # 系统视图历史全量
        assert eng.stats()["total_executed"] == 2

    def test_anonymous_roundtrip_contract(self, env):
        """匿名 default 面既有契约（test_limb.py 同款路径）零回归钉。"""
        c = env["client"]
        tid = _mk(c, None, name="anon").json()["task_id"]
        assert c.get("/api/limb/tasks").status_code == 200
        assert c.get(f"/api/limb/tasks/{tid}").status_code == 200
        assert c.delete(f"/api/limb/tasks/{tid}").status_code == 200

    def test_bad_token_battery_zero_side_effect(self, env):
        c = env["client"]
        eng = env["eng"]
        tid = _mk(c, _token(env["user_a"]), name="a1").json()["task_id"]
        assert _mk(c, BAD_TOKEN, name="x").status_code == 401
        assert c.get("/api/limb/tasks", headers=BAD_TOKEN).status_code == 401
        assert c.get(f"/api/limb/tasks/{tid}", headers=BAD_TOKEN).status_code == 401
        assert c.post(f"/api/limb/tasks/{tid}/execute", headers=BAD_TOKEN).status_code == 401
        assert c.post(f"/api/limb/tasks/{tid}/cancel", headers=BAD_TOKEN).status_code == 401
        assert c.delete(f"/api/limb/tasks/{tid}", headers=BAD_TOKEN).status_code == 401
        assert c.get("/api/limb/history", headers=BAD_TOKEN).status_code == 401
        row = eng.get_task(tid)
        assert row.status.value == "queued" and eng.stats()["total_executed"] == 0
