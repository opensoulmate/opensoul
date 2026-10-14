"""unified cron 子搜索存储层加轴测试（DEC-⑦-⑦：cron 面=⑦ 清单最后一面）。

修前（HEAD 源码实证）两断点：①_search_cron_jobs 走 db_pool `SELECT * FROM
cron_jobs`——全仓零建表零写方=幽灵表，查询恒异常→except 空集=wired-but-not-
functional（DEC-⑦-⑤ agents.json 幽灵源同型）；真源=hermes cron store
jobs.json（/api/cron 管理面背后的同一 store）②行无租户键、写点（/create）
零身份轴、pause/resume/run/delete 按 job_id 跨租户改删零闸。收口=cron_row_tenant
单一判法（自带键缺失/空白→'default' 兜底，⑦-① _event_row_tenant ~ ⑥
event_row_tenant 同款；写读同一判法）+load_cron_jobs 真源读取+stamp_job_tenant
写点行归属（flock .jobs.lock 跨进程互斥防丢更新）+by-ID 改闸（pause/resume/run
跨租户→403 行零改写）+delete 归属闸（跨租户=零副作用与 not-found 同契约 404）
+过滤先于截断恒按派生租户过滤。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB 归 tmp + HERMES_CRON_JOBS_FILE 归
tmp 假 store——绝不碰生产 ~/.hermes/cron/jobs.json）。
"""

import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

try:
    from src.services.cron_store import (
        CronTenantDenied,
        cron_job_row,
        cron_row_tenant,
        load_cron_jobs,
        require_cron_row_tenant,
        stamp_job_tenant,
    )

    HAS_CRON_STORE = True
except ImportError:  # 控制组（旧代码）容缺导入：让失败暴露在行为断言上
    HAS_CRON_STORE = False

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)


def _seed_user(db_path, uid, username, tenant="default"):
    import sqlite3

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


def _seed_store(path, rows):
    path.write_text(
        json.dumps({"jobs": rows, "updated_at": "2026-10-15T00:00:00+08:00"}),
        encoding="utf-8",
    )


def _store_rows(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["jobs"]


def _row(path, job_id):
    for r in _store_rows(path):
        if str(r.get("id")) == job_id:
            return r
    return None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + tmp 假 hermes cron store + 真 search/cron router + 记录型
    CLI 桩（create 真写 store 行、其余只记录调用）。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    store = tmp_path / "jobs.json"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    monkeypatch.setenv("HERMES_CRON_JOBS_FILE", str(store))

    import sqlite3

    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    _seed_store(
        store,
        [
            {  # 无键→'default'（hermes CLI 外部写方形态）
                "id": "job-default",
                "name": "daily backup",
                "prompt": "run backup daily",
                "schedule_display": "0 8 * * *",
                "enabled": True,
            },
            {
                "id": "job-default2",
                "name": "system update",
                "prompt": "update system packages",
                "schedule_display": "0 9 * * *",
                "enabled": False,
            },
            {
                "id": "job-a",
                "name": "alpha report",
                "prompt": "report for alpha",
                "schedule_display": "0 10 * * *",
                "enabled": True,
                "tenant_id": "tenant-a",
            },
            {
                "id": "job-b",
                "name": "beta report",
                "prompt": "report for beta",
                "schedule_display": "0 11 * * *",
                "enabled": True,
                "tenant_id": "tenant-b",
            },
        ],
    )

    calls = []

    async def fake_run_hermes_cron(*args):
        calls.append(args)
        if args and args[0] == "create":
            rows = _store_rows(store)
            new_id = f"job-new-{len(calls)}"
            rows.append(
                {
                    "id": new_id,
                    "name": "created job",
                    "prompt": args[-1] if len(args) > 1 else "",
                    "schedule_display": "0 * * * *",
                    "enabled": True,
                }
            )
            _seed_store(store, rows)
            return {"output": f"created {new_id}", "error": None, "exit_code": 0}
        return {"output": "ok", "error": None, "exit_code": 0}

    import src.api.hermes_cron as cron_api
    import src.api.search as search_api

    monkeypatch.setattr(cron_api, "run_hermes_cron", fake_run_hermes_cron)

    app = FastAPI()
    app.include_router(search_api.router, prefix="/api/search")
    app.include_router(cron_api.router, prefix="/api/cron")
    client = TestClient(app)

    user_tenant_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_tenant_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")

    yield {
        "client": client,
        "store": store,
        "calls": calls,
        "user_tenant_a": user_tenant_a,
        "user_tenant_b": user_tenant_b,
        "user_default": user_default,
    }


def _search(client, headers, q="backup", limit=10):
    r = client.get(
        "/api/search/unified",
        params={"q": q, "sources": "cron", "limit": limit},
        headers=headers or {},
    )
    assert r.status_code == 200
    return r.json()["by_source"]["cron"]


# ── 幽灵源→真源（修前硬反证面） ─────────────────────────────────────


class TestGhostSourceFixed:
    def test_search_reads_true_source(self, env):
        # 修前：cron_jobs 幽灵表恒异常→[]（任何查询零命中）
        hits = _search(env["client"], {}, q="backup")
        assert [h["job_id"] for h in hits] == ["job-default"]

    def test_result_shape_unchanged(self, env):
        hits = _search(env["client"], {}, q="backup")
        assert len(hits) == 1
        assert set(hits[0].keys()) == {
            "source",
            "icon",
            "title",
            "snippet",
            "job_id",
            "enabled",
        }
        assert hits[0]["source"] == "cron"
        assert hits[0]["title"] == "daily backup"
        assert hits[0]["enabled"] is True
        assert "0 8 * * *" in hits[0]["snippet"]

    def test_prompt_and_schedule_match(self, env):
        assert _search(env["client"], {}, q="packages")  # prompt 命中
        assert _search(env["client"], {}, q="0 9 * * *")  # schedule_display 命中

    def test_no_hit_is_empty_not_error(self, env):
        assert _search(env["client"], {}, q="zzz-no-such-job") == []


# ── 单一判法 + 写点落键原语 ─────────────────────────────────────────


class TestRowTenantVerdict:
    def test_cron_row_tenant_fallback(self):
        assert cron_row_tenant({}) == "default"
        assert cron_row_tenant({"tenant_id": ""}) == "default"
        assert cron_row_tenant({"tenant_id": "   "}) == "default"
        assert cron_row_tenant({"tenant_id": None}) == "default"
        assert cron_row_tenant({"tenant_id": "tenant-a"}) == "tenant-a"
        assert cron_row_tenant({"tenant_id": " tenant-a "}) == "tenant-a"

    def test_stamp_roundtrip_and_sticky_rest(self, env):
        assert stamp_job_tenant("job-default", "tenant-a") is True
        row = _row(env["store"], "job-default")
        assert row["tenant_id"] == "tenant-a"
        # 行其余字段一字不动（只落键）
        assert row["name"] == "daily backup"
        assert row["enabled"] is True
        assert load_cron_jobs()  # 真源可回读

    def test_stamp_missing_job_false(self, env):
        assert stamp_job_tenant("job-gone", "tenant-a") is False

    def test_require_row_tenant_denies_cross(self, env):
        row = {"id": "x", "tenant_id": "tenant-a"}
        require_cron_row_tenant(row, "tenant-a")  # 同租户不抛
        with pytest.raises(CronTenantDenied):
            require_cron_row_tenant(row, "tenant-b")
        with pytest.raises(CronTenantDenied):
            require_cron_row_tenant({"id": "y"}, "tenant-a")  # 无键='default'


# ── 查询过滤 ─────────────────────────────────────────────────────────


class TestFilter:
    def test_three_way_isolation(self, env):
        c = env["client"]
        a = _search(c, _token(env["user_tenant_a"]), q="report")
        b = _search(c, _token(env["user_tenant_b"]), q="report")
        anon = _search(c, {}, q="report")
        assert [h["job_id"] for h in a] == ["job-a"]
        assert [h["job_id"] for h in b] == ["job-b"]
        assert anon == []  # default 行无 report 命中

    def test_anon_sees_default_rows_only(self, env):
        hits = _search(env["client"], {}, q="report")
        assert hits == []
        hits = _search(env["client"], {}, q="a")  # 默认两行 name/prompt 均含 a
        ids = {h["job_id"] for h in hits}
        assert ids == {"job-default", "job-default2"}

    def test_filter_before_limit(self, env):
        # query "e" 全库 4 行全命中；limit=1 时 A 拿到的是自己的行而非全局头部
        hits = _search(env["client"], _token(env["user_tenant_a"]), q="e", limit=1)
        assert [h["job_id"] for h in hits] == ["job-a"]

    def test_default_user_sees_default_rows(self, env):
        hits = _search(env["client"], _token(env["user_default"]), q="backup")
        assert [h["job_id"] for h in hits] == ["job-default"]


# ── 写点归属（/create） ─────────────────────────────────────────────


class TestWritePoint:
    def test_create_stamps_identity_tenant(self, env):
        c = env["client"]
        r = c.post(
            "/api/cron/create",
            json={"name": "new one", "schedule": "0 * * * *", "prompt": "hello"},
            headers=_token(env["user_tenant_a"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["tenant_stamped"] is True
        assert data["tenant_id"] == "tenant-a"
        assert data["job_id"]
        row = _row(env["store"], data["job_id"])
        assert row["tenant_id"] == "tenant-a"
        # 真源可检索（写点→读面闭环）
        hits = _search(c, _token(env["user_tenant_a"]), q="created")
        assert [h["job_id"] for h in hits] == [data["job_id"]]
        assert _search(c, {}, q="created") == []  # B/匿名恒不可见

    def test_create_default_tenant_user(self, env):
        r = env["client"].post(
            "/api/cron/create",
            json={"name": "def one", "schedule": "0 * * * *", "prompt": "hi"},
            headers=_token(env["user_default"]),
        )
        assert r.status_code == 200
        data = r.json()
        assert data["tenant_id"] == "default"
        assert _row(env["store"], data["job_id"])["tenant_id"] == "default"

    def test_create_no_new_row_no_stamp(self, env, monkeypatch):
        import src.api.hermes_cron as cron_api

        async def no_row_cli(*args):
            return {"output": "created nothing", "error": None, "exit_code": 0}

        monkeypatch.setattr(cron_api, "run_hermes_cron", no_row_cli)
        r = env["client"].post(
            "/api/cron/create",
            json={"name": "ghost", "schedule": "0 * * * *", "prompt": "x"},
            headers=_token(env["user_tenant_a"]),
        )
        assert r.status_code == 200
        data = r.json()
        # 歧义/未见=如实不猜不落键（禁 fabricate 行 id）
        assert data["job_id"] is None
        assert data["tenant_stamped"] is False

    def test_create_bad_token_401(self, env):
        r = env["client"].post(
            "/api/cron/create",
            json={"name": "x", "schedule": "0 * * * *", "prompt": "x"},
            headers={"Authorization": "Bearer not-a-token"},
        )
        assert r.status_code == 401


# ── by-ID 改/删闸 ────────────────────────────────────────────────────


class TestGates:
    @pytest.mark.parametrize("op", ["pause", "resume", "run"])
    def test_cross_tenant_modify_403_row_untouched(self, env, op):
        c = env["client"]
        before = json.dumps(_row(env["store"], "job-default"), sort_keys=True)
        r = c.post(f"/api/cron/job-default/{op}", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 403
        assert "tenant" in r.json()["detail"]  # 带真因
        # CLI 零调用（硬反证：修前直接放行 shell CLI）
        assert env["calls"] == []
        assert json.dumps(_row(env["store"], "job-default"), sort_keys=True) == before

    def test_owner_modify_ok(self, env):
        r = env["client"].post("/api/cron/job-a/pause", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert ("pause", "job-a") in env["calls"]

    def test_delete_cross_tenant_404_row_alive(self, env):
        c = env["client"]
        r = c.delete("/api/cron/job-default", headers=_token(env["user_tenant_a"]))
        # 与 not-found 同契约 404 不差分泄露行存在性（⑦-⑥ 同款）
        assert r.status_code == 404
        assert env["calls"] == []
        assert _row(env["store"], "job-default") is not None

    def test_delete_owner_ok(self, env):
        r = env["client"].delete("/api/cron/job-a", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert ("remove", "job-a") in env["calls"]

    def test_missing_row_contract_unchanged(self, env):
        # 缺行照旧交 CLI（既有 not-found/CLI 错误契约不变，不误伤）
        r = env["client"].post("/api/cron/no-such-job/pause", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert ("pause", "no-such-job") in env["calls"]

    def test_cross_tenant_default_user_gated(self, env):
        # 'default' 租户用户对 A 行同闸（判法不看用户名只看行租户键）
        r = env["client"].post("/api/cron/job-a/run", headers=_token(env["user_default"]))
        assert r.status_code == 403
        assert env["calls"] == []

    def test_bad_token_401(self, env):
        r = env["client"].post(
            "/api/cron/job-default/pause",
            headers={"Authorization": "Bearer not-a-token"},
        )
        assert r.status_code == 401
