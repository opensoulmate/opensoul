"""cron 管理读面加轴 + job 引用判法单一源测试（DEC-⑦-⑧）。

修前（HEAD 源码+hermes CLI 源码双实证）两断点：
①管理读面零租户过滤——/list 回全量作业（任意租户可见全部 cron 作业）、
  /get /history 按 id 跨租户读零闸（跨租户泄露）。
②写闸引用判法与执行体漂移（wired-but-not-functional）——hermes CLI
  resolve_job_ref=精确 ID→大小写不敏感 name（pause/resume/trigger/remove
  全走它），而 ⑦-⑦ 闸门 cron_job_row 只认精确 id：用作业 name 引用→闸查无
  行→放行交 CLI→CLI 按名执行=跨租户改删绕闸。

收口=cron_store.cron_job_row 引用解析单一源（精确 ID→独一 name→独一前缀，
歧义=None 如实不猜）+ /list _visible_jobs 过滤（过滤先于 total+加性键
tenant_id+归属不可判 fail-closed 隐藏）+ /get /history 归属闸（跨租户=与
not-found 同契约 404 不差分泄露行存在性）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB 归 tmp + HERMES_CRON_JOBS_FILE 归
tmp 假 store——绝不碰生产 ~/.hermes/cron/jobs.json；CLI=记录型桩）。
"""

import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

try:
    from src.services.cron_store import cron_job_row

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


def _table(rows, extra_lines=()):
    """把 store 行渲染成 hermes cron list 表格形态（/list /get 解析面输入）。"""
    lines = []
    for r in rows:
        rid = str(r.get("id"))
        lines.append(f"{rid:<12} [active]")
        lines.append(f"Name: {r.get('name', '')}")
        lines.append(f"Schedule: {r.get('schedule_display', '')}")
        lines.append(f"Prompt: {r.get('prompt', '')}")
    lines.extend(extra_lines)
    return "\n".join(lines)


ROWS = [
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
    {
        "id": "job-gamma",
        "name": "gamma job",
        "prompt": "gamma work",
        "schedule_display": "0 12 * * *",
        "enabled": True,
        "tenant_id": "tenant-a",
    },
    {  # 重名对（job-x/job-y 同 name）：歧义面
        "id": "job-x",
        "name": "dup name",
        "prompt": "x work",
        "schedule_display": "0 13 * * *",
        "enabled": True,
        "tenant_id": "tenant-a",
    },
    {
        "id": "job-y",
        "name": "dup name",
        "prompt": "y work",
        "schedule_display": "0 14 * * *",
        "enabled": True,
        "tenant_id": "tenant-b",
    },
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + tmp 假 hermes cron store + 真 cron router + 记录型 CLI 桩
    （list 渲染 store 行为表格输出、runs 回执行史、其余只记录调用）。"""
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

    _seed_store(store, [dict(r) for r in ROWS])

    calls = []

    async def fake_run_hermes_cron(*args):
        calls.append(args)
        if args and args[0] == "list":
            return {
                "output": _table(_store_rows(store)),
                "error": None,
                "exit_code": 0,
            }
        if args and args[0] == "runs":
            return {
                "output": "run 1 status=success\nrun 2 status=failed",
                "error": None,
                "exit_code": 0,
            }
        return {"output": "ok", "error": None, "exit_code": 0}

    import src.api.hermes_cron as cron_api

    monkeypatch.setattr(cron_api, "run_hermes_cron", fake_run_hermes_cron)

    app = FastAPI()
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


def _list_ids(client, headers):
    r = client.get("/api/cron/list", headers=headers)
    assert r.status_code == 200
    data = r.json()
    return sorted(j["id"] for j in data["jobs"]), data


# ── 引用解析单一源（cron_job_row：精确 ID→独一 name→独一前缀） ─────────


class TestRefResolver:
    def test_exact_id_wins_over_name(self, env):
        # job-y 的 name 不是 id，构造 id-name 撞形：ref="job-a" 精确命中 id 行
        rows = _store_rows(env["store"])
        rows.append({"id": "zz", "name": "job-a", "tenant_id": "tenant-b"})
        _seed_store(env["store"], rows)
        row = cron_job_row("job-a")
        assert row is not None
        assert row["id"] == "job-a"  # ID 优先（resolve_job_ref 同款）

    def test_unique_name_case_insensitive(self, env):
        row = cron_job_row("ALPHA REPORT")
        assert row is not None
        assert row["id"] == "job-a"

    def test_ambiguous_name_none_no_prefix_fallthrough(self, env):
        # "dup name" 命中 job-x/job-y 两行→None（hermes AmbiguousJobReference
        # 拒执行同款；即便它同时是某行 id 前缀也不跌落）
        assert cron_job_row("dup name") is None

    def test_unique_prefix(self, env):
        row = cron_job_row("job-gam")
        assert row is not None
        assert row["id"] == "job-gamma"

    def test_ambiguous_prefix_none(self, env):
        # "job-def" 前缀命中 job-default/job-default2→None（修前 /get 静默取首匹配）
        assert cron_job_row("job-def") is None

    def test_empty_ref_none(self, env):
        # 空串是全行前缀，禁解析到任意行
        assert cron_job_row("") is None

    def test_missing_none(self, env):
        assert cron_job_row("no-such-job") is None


# ── /list 按行租户键过滤 ─────────────────────────────────────────────


class TestListFace:
    def test_three_way_isolation(self, env):
        a_ids, _ = _list_ids(env["client"], _token(env["user_tenant_a"]))
        assert a_ids == ["job-a", "job-gamma", "job-x"]
        b_ids, _ = _list_ids(env["client"], _token(env["user_tenant_b"]))
        assert b_ids == ["job-b", "job-y"]
        d_ids, _ = _list_ids(env["client"], _token(env["user_default"]))
        assert d_ids == ["job-default", "job-default2"]

    def test_total_counts_visible_only(self, env):
        _, data = _list_ids(env["client"], _token(env["user_tenant_a"]))
        assert data["total"] == 3  # 过滤先于计数（修前 7=全量泄露）
        assert len(data["jobs"]) == data["total"]

    def test_additive_tenant_key_and_shape(self, env):
        _, data = _list_ids(env["client"], _token(env["user_tenant_a"]))
        job = next(j for j in data["jobs"] if j["id"] == "job-a")
        assert job["tenant_id"] == "tenant-a"  # 加性键=行归属可观测
        for key in ("id", "status", "name", "schedule", "prompt"):
            assert key in job  # 既有形状键零丢失

    def test_unresolvable_parsed_row_hidden_fail_closed(self, env, monkeypatch):
        import src.api.hermes_cron as cron_api

        async def with_phantom(*args):
            if args and args[0] == "list":
                return {
                    "output": _table(
                        _store_rows(env["store"]),
                        extra_lines=[
                            "ghost-id-99   [active]",
                            "Name: phantom",
                            "Prompt: not in store",
                        ],
                    ),
                    "error": None,
                    "exit_code": 0,
                }
            return {"output": "ok", "error": None, "exit_code": 0}

        monkeypatch.setattr(cron_api, "run_hermes_cron", with_phantom)
        ids, data = _list_ids(env["client"], _token(env["user_tenant_a"]))
        assert "ghost-id-99" not in ids  # 归属不可判→隐藏（fail-closed 宁缺勿泄）
        assert data["total"] == 3


# ── /get 读面归属闸 + 引用判法与写闸同源 ─────────────────────────────


class TestGetFace:
    def test_own_by_id(self, env):
        r = env["client"].get("/api/cron/job-a", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == "job-a"
        assert data["tenant_id"] == "tenant-a"

    def test_cross_tenant_by_id_404(self, env):
        # 修前：零闸 200=跨租户读泄露硬反证
        r = env["client"].get("/api/cron/job-a", headers=_token(env["user_tenant_b"]))
        assert r.status_code == 404
        assert r.json()["detail"] == "Job not found"  # 与缺行同契约不差分泄露

    def test_own_by_name(self, env):
        # 引用判法与 CLI resolve_job_ref 对齐：name 引用读面可达
        r = env["client"].get("/api/cron/alpha report", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert r.json()["id"] == "job-a"

    def test_cross_tenant_by_name_404(self, env):
        r = env["client"].get("/api/cron/alpha report", headers=_token(env["user_tenant_b"]))
        assert r.status_code == 404

    def test_unique_prefix_own(self, env):
        r = env["client"].get("/api/cron/job-gam", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert r.json()["id"] == "job-gamma"

    def test_ambiguous_prefix_404(self, env):
        # 修前：startswith 静默取首匹配（job-default）→越权读+歧义错配
        r = env["client"].get("/api/cron/job-def", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 404

    def test_missing_404(self, env):
        r = env["client"].get("/api/cron/no-such-job", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 404


# ── 写闸 name 引用绕闸真断点收口（核心反证） ──────────────────────────


class TestWriteGateNameBypass:
    @pytest.mark.parametrize("op", ["pause", "resume", "run"])
    def test_cross_tenant_name_ref_403_no_cli(self, env, op):
        # 修前硬反证：cron_job_row 只认精确 id→name 引用闸查无行→放行交 CLI
        # →CLI resolve_job_ref 按名执行=跨租户改（calls 非空）
        c = env["client"]
        before = json.dumps(_row(env["store"], "job-a"), sort_keys=True)
        r = c.post(f"/api/cron/alpha report/{op}", headers=_token(env["user_tenant_b"]))
        assert r.status_code == 403
        assert "tenant" in r.json()["detail"]
        assert env["calls"] == []  # CLI 零调用=零副作用
        assert json.dumps(_row(env["store"], "job-a"), sort_keys=True) == before

    def test_cross_tenant_name_delete_404_row_alive(self, env):
        c = env["client"]
        r = c.delete("/api/cron/alpha report", headers=_token(env["user_tenant_b"]))
        assert r.status_code == 404  # 与 not-found 同契约
        assert env["calls"] == []
        assert _row(env["store"], "job-a") is not None

    def test_owner_name_ref_ok(self, env):
        r = env["client"].post("/api/cron/alpha report/pause", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert ("pause", "alpha report") in env["calls"]  # 引用原样交 CLI

    def test_missing_ref_contract_unchanged(self, env):
        # 缺行照旧交 CLI（⑦-⑦ 契约零变化）
        r = env["client"].post("/api/cron/no-such-job/pause", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        assert ("pause", "no-such-job") in env["calls"]


# ── /history 读面归属闸 ─────────────────────────────────────────────


class TestHistoryFace:
    def test_own_200(self, env):
        r = env["client"].get("/api/cron/job-a/history", headers=_token(env["user_tenant_a"]))
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert [run["status"] for run in data["runs"]] == ["success", "failed"]

    def test_cross_tenant_404_no_cli(self, env):
        r = env["client"].get("/api/cron/job-a/history", headers=_token(env["user_tenant_b"]))
        assert r.status_code == 404
        assert env["calls"] == []  # 执行史零泄露零调用

    def test_cross_tenant_name_404(self, env):
        r = env["client"].get(
            "/api/cron/alpha report/history", headers=_token(env["user_tenant_b"])
        )
        assert r.status_code == 404


# ── 鉴权契约 ────────────────────────────────────────────────────────


class TestAuthContract:
    def test_list_bad_token_401(self, env):
        r = env["client"].get("/api/cron/list", headers={"Authorization": "Bearer not-a-token"})
        assert r.status_code == 401

    def test_get_bad_token_401(self, env):
        r = env["client"].get("/api/cron/job-a", headers={"Authorization": "Bearer not-a-token"})
        assert r.status_code == 401
