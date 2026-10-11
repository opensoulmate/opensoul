"""F3b-③a will 执行面（jobs 表）数据面隔离接线测试。

覆盖四层（全部 tmp 独立库 + NEST_DB 环境隔离，绝不写生产 data/*.db）：
①jobs.tenant_id 列迁移（老库补列+存量行回填 'default'）与往返/粘性
②查询过滤（list/purge/幂等键按租户，跨租户行互不可见/不可清）
③IsolationEngine.check_access 真执行（by-ID 闸门：跨租户 JobAccessDenied
  +引擎审计留痕，total_access_checks>0——闭 F3 审计"数据面从未咨询隔离引擎"
  硬证据）
④API 层派生租户全链（auth token → bind_identity → 行归属落库 → 闸门 403，
  will/hippo 真实 router）
"""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb
from src.will.job_queue import Job, JobAccessDenied, JobQueue

# 生产 jobs 表 F3b-③a 迁移前形状（无 tenant_id → 证明自愈补列）
OLD_JOBS_DDL = """CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    params TEXT DEFAULT '{}',
    result TEXT,
    error TEXT DEFAULT '',
    created_at REAL,
    started_at REAL DEFAULT 0,
    finished_at REAL DEFAULT 0,
    timeout_s INTEGER DEFAULT 300,
    retries INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 2,
    idempotency_key TEXT DEFAULT '',
    next_retry_at REAL DEFAULT 0,
    source TEXT DEFAULT 'real')"""

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _run(coro):
    return asyncio.run(coro)


def _ent_token(uid, username="u"):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _insert_ent_user(db, username, tenant, is_active=1):
    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, is_active, tenant_id) VALUES (?, ?, ?, ?)",
            (username, "hashed", is_active, tenant),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@pytest.fixture()
def jq(tmp_path):
    """tmp job 库 + 不启动 worker（作业恒 PENDING，状态断言确定性）。"""
    return JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=1)


# ── ① 迁移 + 落行 ─────────────────────────────────────────────────────


class TestMigration:
    def test_tenant_column_added_and_backfilled(self, tmp_path):
        db = tmp_path / "jobs.db"
        conn = sqlite3.connect(db)
        conn.execute(OLD_JOBS_DDL)
        conn.execute(
            "INSERT INTO jobs (id, name, status, created_at) VALUES ('job_old', 'x', 'completed', 1.0)"
        )
        conn.commit()
        conn.close()

        q = JobQueue(db_path=str(db))

        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        row = conn.execute("SELECT tenant_id FROM jobs WHERE id = 'job_old'").fetchone()
        conn.close()
        assert "tenant_id" in cols
        assert row[0] == "default"  # 存量行回填 default（与生产数据分布一致）
        got = q.get("job_old", tenant_id="default")
        assert got["tenant_id"] == "default"

    def test_submit_roundtrip_and_db_row(self, jq, tmp_path):
        jid = _run(jq.submit("t", {"a": 1}, tenant_id="tenant-a"))
        got = jq.get(jid, tenant_id="tenant-a")
        assert got["tenant_id"] == "tenant-a"
        conn = sqlite3.connect(tmp_path / "jobs.db")
        row = conn.execute("SELECT tenant_id FROM jobs WHERE id = ?", (jid,)).fetchone()
        conn.close()
        assert row is not None and row[0] == "tenant-a"

    def test_persist_tenant_sticky_on_empty(self, jq, tmp_path):
        # COALESCE 粘性：字段置空=未声明，REPLACE 不把既有归属洗回 'default'
        jid = _run(jq.submit("t", {}, tenant_id="tenant-a"))
        jq._persist(Job(id=jid, name="t", tenant_id=""))
        conn = sqlite3.connect(tmp_path / "jobs.db")
        row = conn.execute("SELECT tenant_id FROM jobs WHERE id = ?", (jid,)).fetchone()
        conn.close()
        assert row[0] == "tenant-a"


# ── ② 查询过滤 ────────────────────────────────────────────────────────


class TestQueryFilter:
    def test_list_scoped_per_tenant(self, jq):
        a1 = _run(jq.submit("t", {}, tenant_id="tenant-a"))
        a2 = _run(jq.submit("t", {}, tenant_id="tenant-a"))
        b1 = _run(jq.submit("t", {}, tenant_id="tenant-b"))
        listed_a = {j["id"] for j in jq.list_jobs(tenant_id="tenant-a")}
        listed_b = {j["id"] for j in jq.list_jobs(tenant_id="tenant-b")}
        listed_default = {j["id"] for j in jq.list_jobs()}
        assert listed_a == {a1, a2}
        assert listed_b == {b1}
        assert listed_default == set()  # 匿名 default 租户看不到他租户作业

    def test_purge_scoped_per_tenant(self, jq):
        _run(jq.submit("t", {}, tenant_id="tenant-a"))
        _run(jq.submit("t", {}, tenant_id="tenant-a"))
        b1 = _run(jq.submit("t", {}, tenant_id="tenant-b"))
        purged = jq.purge(status="pending", tenant_id="tenant-a")
        assert purged == 2
        assert [j["id"] for j in jq.list_jobs(tenant_id="tenant-b")] == [b1]

    def test_idempotency_scoped_per_tenant(self, jq):
        j1 = _run(jq.submit("t", {}, idempotency_key="k1", tenant_id="tenant-a"))
        j2 = _run(jq.submit("t", {}, idempotency_key="k1", tenant_id="tenant-b"))
        assert j1 != j2  # 同 key 不同租户=两个独立作业，互不去重
        again = _run(jq.submit("t", {}, idempotency_key="k1", tenant_id="tenant-a"))
        assert again == j1  # 同租户同 key 照旧去重


# ── ③ IsolationEngine.check_access 真执行 ────────────────────────────


class TestIsolationGate:
    def test_cross_tenant_get_denied_with_audit(self, jq):
        jid = _run(jq.submit("t", {}, tenant_id="tenant-b"))
        with pytest.raises(JobAccessDenied) as e:
            jq.get(jid, tenant_id="tenant-a")
        assert "namespace" in e.value.reason

        # 引擎真被执行：审计留痕 + 计数器（F3 审计 total_access_checks=0 已闭）
        stats = jq.isolation.stats()
        assert stats["total_access_checks"] >= 1
        assert stats["blocked_attempts"] >= 1
        blocked = jq.isolation.get_access_log(allowed=False)
        hit = [
            x for x in blocked if x["resource_id"] == f"ns-tenant-b:{jid}" and x["action"] == "read"
        ]
        assert hit, f"blocked audit entry missing: {blocked}"
        assert "namespace mismatch" in hit[0]["reason"]
        assert hit[0]["resource_type"] == "job"

    def test_same_tenant_get_allowed_and_logged(self, jq):
        jid = _run(jq.submit("t", {}, tenant_id="tenant-a"))
        assert jq.get(jid, tenant_id="tenant-a")["id"] == jid
        allowed = jq.isolation.get_access_log(allowed=True)
        assert any(
            x["resource_id"] == f"ns-tenant-a:{jid}" and x["action"] == "read" for x in allowed
        )

    def test_cross_tenant_cancel_and_requeue_denied(self, jq):
        jid = _run(jq.submit("t", {}, tenant_id="tenant-a"))
        with pytest.raises(JobAccessDenied):
            jq.cancel(jid, tenant_id="tenant-b")
        assert jq.cancel(jid, tenant_id="tenant-a") is True  # 本租户照常取消
        with pytest.raises(JobAccessDenied):
            _run(jq.requeue(jid, tenant_id="tenant-b"))
        result = _run(jq.requeue(jid, tenant_id="tenant-a"))
        assert result["requeued"] is True

    def test_bulk_calls_audited(self, jq):
        _run(jq.submit("t", {}, tenant_id="tenant-a"))
        jq.list_jobs(tenant_id="tenant-a")
        jq.purge(status="pending", tenant_id="tenant-a")
        log = jq.isolation.get_access_log(tenant_id="ns-tenant-a")
        actions = {x["action"] for x in log}
        assert {"list", "delete"} <= actions
        assert jq.isolation.stats()["total_access_checks"] >= 2


# ── ④ 经验采集租户门（jobs→experiences 不跨租户混入）──────────────────


class TestExperienceCollectorTenantGate:
    def test_jobs_scan_skips_other_tenant(self, tmp_path):
        from src.learn.experience_collector import ExperienceCollector

        jobs_db = tmp_path / "jobs.db"
        JobQueue(db_path=str(jobs_db))  # 构造即迁移建列（tenant_id 等）
        conn = sqlite3.connect(jobs_db)
        conn.execute(
            "INSERT INTO jobs (id, name, status, created_at, tenant_id) "
            "VALUES ('job_d', 'hippo.dream', 'completed', 1.0, 'default')"
        )
        conn.execute(
            "INSERT INTO jobs (id, name, status, created_at, tenant_id) "
            "VALUES ('job_a', 'hippo.dream', 'completed', 2.0, 'tenant-a')"
        )
        conn.commit()
        conn.close()

        c = ExperienceCollector(
            db_path=str(tmp_path / "main.db"),
            job_db_path=str(jobs_db),
            eval_db_path=str(tmp_path / "eval.db"),
            tenant_id="default",
        )
        stats = c._collect_from_jobs()
        assert stats["scanned"] == 1  # 只采 'default' 名下作业
        assert stats["skipped_other_tenant"] == 1

        c_a = ExperienceCollector(
            db_path=str(tmp_path / "main_a.db"),
            job_db_path=str(jobs_db),
            eval_db_path=str(tmp_path / "eval.db"),
            tenant_id="tenant-a",
        )
        stats_a = c_a._collect_from_jobs()
        assert stats_a["scanned"] == 1
        assert stats_a["skipped_other_tenant"] == 1


# ── ⑤ API 层：认证派生租户 → 行归属 → 闸门 403 全链 ───────────────────


class TestAPI:
    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        ent_db = tmp_path / "enterprise.db"
        monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
        monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
        conn = sqlite3.connect(ent_db)
        conn.execute(ENTERPRISE_DDL)
        conn.commit()
        conn.close()
        return ent_db

    @pytest.fixture()
    def api(self, will_client, env):
        return will_client

    def test_auth_to_gate_full_chain(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}

        # A 提交：行归属从认证身份派生落库
        resp = api.post(
            "/api/will/jobs/submit",
            json={"name": "no_such_handler", "params": {}, "source": "test"},
            headers=tok_a,
        )
        assert resp.status_code == 200
        jid = resp.json()["job_id"]

        # A 读自己的：200 且行归属=派生租户
        got = api.get(f"/api/will/jobs/{jid}", headers=tok_a)
        assert got.status_code == 200
        assert got.json()["tenant_id"] == "tenant-a"

        # B 冒读 A 的作业：403（闸门真拒绝，不是 404 假 not_found）
        denied = api.get(f"/api/will/jobs/{jid}", headers=tok_b)
        assert denied.status_code == 403
        assert "access denied" in denied.json()["detail"]

        # B cancel/requeue 同样 403
        assert api.post(f"/api/will/jobs/{jid}/cancel", headers=tok_b).status_code == 403
        assert api.post(f"/api/will/jobs/{jid}/requeue", headers=tok_b).status_code == 403

        # 列表按派生租户隔离：B/匿名看不到 A 的作业
        assert api.get("/api/will/jobs", headers=tok_b).json()["jobs"] == []
        assert api.get("/api/will/jobs").json()["jobs"] == []

    def test_anonymous_default_contract_unchanged(self, api, env):
        # 既有匿名调用方契约不变：作业归 'default'，匿名读写照常
        resp = api.post(
            "/api/will/jobs/submit",
            json={"name": "no_such_handler", "params": {}, "source": "test"},
        )
        assert resp.status_code == 200
        jid = resp.json()["job_id"]
        got = api.get(f"/api/will/jobs/{jid}")
        assert got.status_code == 200
        assert got.json()["tenant_id"] == "default"
        listed = api.get("/api/will/jobs").json()["jobs"]
        assert [j["id"] for j in listed] == [jid]

    def test_purge_scoped_at_api(self, api, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        for _ in range(2):
            api.post(
                "/api/will/jobs/submit",
                json={"name": "no_such_handler", "params": {}, "source": "test"},
                headers=tok_a,
            )
        api.post(
            "/api/will/jobs/submit",
            json={"name": "no_such_handler", "params": {}, "source": "test"},
            headers=tok_b,
        )
        resp = api.post("/api/will/jobs/purge?source=test", headers=tok_a)
        assert resp.status_code == 200
        assert resp.json()["purged"] == 2  # 只清自己租户名下
        assert len(api.get("/api/will/jobs", headers=tok_b).json()["jobs"]) == 1
