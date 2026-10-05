"""P0-8 后台作业队列运行时接线测试 — handler注册/懒启动/stale回收/幂等键/生产者全链路。

调研来源对照：
- agno job_queue/store.py（evolution-engine-patterns.md §5.2）：idempotency_key去重、
  stale锁回收（单进程部署差异已在job_queue.py docstring注明）
- mem0 §1.1 "失败必须可见"：未注册handler→FAILED带error，不静默
- 本轮修复的缺口：c1feb676队列基础设施零接线（register_handler/start/submit调用点全为0）

单元测试无网络依赖：SQLite用tmp_path隔离；live测试经conftest client → localhost:8090。
"""

import asyncio
import sqlite3
import time

import pytest

from src.will.job_handlers import HANDLER_SPECS, register_default_handlers
from src.will.job_queue import JobQueue, JobStatus


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def jq(tmp_path):
    return JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=2)


async def _poll(q: JobQueue, job_id: str, timeout: float = 8.0) -> dict:
    """轮询作业状态直到终态（live/单元共用语义）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = q.get(job_id)
        if info and info["status"] in ("completed", "failed", "timeout", "cancelled"):
            return info
        await asyncio.sleep(0.05)
    return q.get(job_id) or {}


# ── ① handler注册 ────────────────────────────────────────────


class TestHandlerRegistration:
    def test_default_specs_contain_real_organ_handlers(self):
        # handler命名=<organ>.<action>，指向真实器官函数而非日志占位
        assert "heredity.evaluate_triggers" in HANDLER_SPECS
        assert "hippo.dream" in HANDLER_SPECS

    def test_register_idempotent(self, jq):
        first = register_default_handlers(jq)
        second = register_default_handlers(jq)
        assert first == second
        assert sorted(jq._handlers.keys()) == sorted(HANDLER_SPECS.keys())

    def test_unknown_handler_fails_visibly(self, jq):
        async def scenario():
            job_id = await jq.submit("no.such.handler", {})
            await jq.start()
            info = await _poll(jq, job_id)
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "failed"
        assert "No handler for job type" in info["error"]


# ── ② 执行管线 ───────────────────────────────────────────────


class TestExecution:
    def test_submit_executes_handler_to_completion(self, jq):
        async def scenario():
            async def echo(**params):
                return {"echo": params.get("x")}

            jq.register_handler("t.echo", echo)
            await jq.start()
            job_id = await jq.submit("t.echo", {"x": 42})
            info = await _poll(jq, job_id)
            await jq.stop()
            return job_id, info

        job_id, info = run(scenario())
        assert info["status"] == "completed"
        assert info["result"] == {"echo": 42}
        assert info["started_at"] > 0 and info["finished_at"] >= info["started_at"]

    def test_result_survives_process_restart(self, jq, tmp_path):
        """DB为真源：模拟进程重启（新实例同db_path）后历史与结果仍可读"""

        async def scenario():
            async def echo(**params):
                return {"v": params.get("v")}

            jq.register_handler("t.echo", echo)
            await jq.start()
            job_id = await jq.submit("t.echo", {"v": "persisted"})
            info = await _poll(jq, job_id)
            await jq.stop()
            return job_id, info

        job_id, info = run(scenario())
        assert info["status"] == "completed"

        jq2 = JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=1)
        revived = jq2.get(job_id)
        assert revived is not None
        assert revived["status"] == "completed"
        assert revived["result"] == {"v": "persisted"}
        listed = jq2.list_jobs()
        assert any(j["id"] == job_id for j in listed)
        stats = jq2.get_stats()
        assert stats["by_status"].get("completed", 0) >= 1

    def test_retry_then_succeed(self, jq):
        async def scenario():
            calls = {"n": 0}

            async def flaky(**params):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("transient blip")
                return {"attempts": calls["n"]}

            jq.register_handler("t.flaky", flaky)
            await jq.start()
            job_id = await jq.submit("t.flaky", {}, max_retries=2)
            info = await _poll(jq, job_id)
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "completed"
        assert info["retries"] == 1
        assert info["result"] == {"attempts": 2}

    def test_retries_exhausted_fails_with_error(self, jq):
        async def scenario():
            async def broken(**params):
                raise ValueError("always fails")

            jq.register_handler("t.broken", broken)
            await jq.start()
            job_id = await jq.submit("t.broken", {}, max_retries=1)
            info = await _poll(jq, job_id)
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "failed"
        assert "always fails" in info["error"]
        assert info["retries"] == 1  # 重试计数可见，失败不静默（mem0 §1.1）


# ── ③ stale回收（agno stale回收的单进程版） ──────────────────


class TestStaleRecovery:
    def test_stale_running_requeued_on_start(self, jq, tmp_path):
        """死进程遗留的running作业：新进程start()→回pending重排队→执行完成"""
        # 直接往DB写一条"上个进程死在running"的作业
        with sqlite3.connect(str(tmp_path / "jobs.db")) as conn:
            conn.execute(
                "INSERT INTO jobs (id, name, status, params, created_at, retries, max_retries, idempotency_key) "
                "VALUES ('job_stale001', 't.rec', 'running', '{}', ?, 0, 2, '')",
                (time.time(),),
            )

        async def scenario():
            async def rec(**params):
                return {"recovered": True}

            jq.register_handler("t.rec", rec)
            await jq.start()
            info = await _poll(jq, "job_stale001")
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "completed"
        assert info["result"] == {"recovered": True}

    def test_pending_from_db_requeued(self, jq, tmp_path):
        with sqlite3.connect(str(tmp_path / "jobs.db")) as conn:
            conn.execute(
                "INSERT INTO jobs (id, name, status, params, created_at, retries, max_retries, idempotency_key) "
                "VALUES ('job_pend001', 't.rec2', 'pending', '{}', ?, 0, 2, '')",
                (time.time(),),
            )

        async def scenario():
            async def rec2(**params):
                return {"ok": 1}

            jq.register_handler("t.rec2", rec2)
            await jq.start()
            info = await _poll(jq, "job_pend001")
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "completed"


# ── ④ idempotency_key（agno §5.2幂等键） ─────────────────────


class TestIdempotency:
    def test_same_key_pending_dedupes(self, jq):
        async def scenario():
            # 不start()：作业停留pending，两次提交同key
            jid1 = await jq.submit("t.x", {"n": 1}, idempotency_key="key-A")
            jid2 = await jq.submit("t.x", {"n": 2}, idempotency_key="key-A")
            return jid1, jid2

        jid1, jid2 = run(scenario())
        assert jid1 == jid2
        rows = jq.list_jobs()
        assert len([j for j in rows if j.get("status") == "pending"]) == 1
        # 只有第一次的params被保留
        assert rows[0]["params"] == {"n": 1}

    def test_completed_key_does_not_block_resubmit(self, jq):
        """已完成的同key作业不拦新提交（重跑是合法意图）"""

        async def scenario():
            async def quick(**params):
                return {"n": params.get("n")}

            jq.register_handler("t.quick", quick)
            await jq.start()
            jid1 = await jq.submit("t.quick", {"n": 1}, idempotency_key="key-B")
            await _poll(jq, jid1)
            jid2 = await jq.submit("t.quick", {"n": 2}, idempotency_key="key-B")
            info2 = await _poll(jq, jid2)
            await jq.stop()
            return jid1, jid2, info2

        jid1, jid2, info2 = run(scenario())
        assert jid1 != jid2
        assert info2["status"] == "completed"
        assert info2["result"] == {"n": 2}

    def test_find_by_idempotency_key(self, jq):
        async def scenario():
            jid = await jq.submit("t.x", {}, idempotency_key="key-C")
            found = jq.find_by_idempotency_key("key-C")
            return jid, found

        jid, found = run(scenario())
        assert found == jid
        assert jq.find_by_idempotency_key("nope") is None
        assert jq.find_by_idempotency_key("") is None

    def test_key_persisted_in_db(self, jq):
        run(jq.submit("t.x", {}, idempotency_key="key-D"))
        with sqlite3.connect(jq._db_path) as conn:
            row = conn.execute(
                "SELECT idempotency_key FROM jobs WHERE idempotency_key='key-D'"
            ).fetchone()
        assert row is not None


# ── ⑤ live API（:8090真实服务，conftest client） ─────────────


@pytest.mark.live
class TestLiveJobQueueAPI:
    """对live opensoul服务验证端到端接线（重启服务后运行）。"""

    def test_health_lists_registered_handlers(self, client):
        r = client.get("/api/will/jobs/health")
        assert r.status_code == 200
        data = r.json()
        assert data["component"] == "JobQueue"
        for name in ("heredity.evaluate_triggers", "hippo.dream"):
            assert name in data["handlers"], f"handler {name} 未注册=未接线"

    def test_submit_evaluate_triggers_job_completes(self, client):
        r = client.post(
            "/api/will/jobs/submit",
            json={"name": "heredity.evaluate_triggers", "params": {"idle_seconds": 0}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "submitted"
        assert body["workers"] >= 1, "懒启动后worker池必须存在"
        job_id = body["job_id"]

        deadline = time.time() + 15
        info = None
        while time.time() < deadline:
            gr = client.get(f"/api/will/jobs/{job_id}")
            assert gr.status_code == 200
            info = gr.json()
            if info["status"] in ("completed", "failed", "timeout"):
                break
            time.sleep(0.3)
        assert info is not None and info["status"] == "completed", f"job未完成: {info}"
        assert isinstance(info["result"], dict)
        # evaluate_triggers真实返回（fired/breaker_open/suppressed之一必存在）
        assert any(k in info["result"] for k in ("fired", "breaker_open", "suppressed")), info[
            "result"
        ]

    def test_dream_background_producer(self, client):
        r = client.post("/api/hippo/ltm/dream", json={"messages": [], "background": True})
        assert r.status_code == 200
        body = r.json()
        assert body["queued"] is True
        assert body["handler"] == "hippo.dream"
        job_id = body["job_id"]

        deadline = time.time() + 15
        info = None
        while time.time() < deadline:
            gr = client.get(f"/api/will/jobs/{job_id}")
            assert gr.status_code == 200
            info = gr.json()
            if info["status"] in ("completed", "failed", "timeout"):
                break
            time.sleep(0.3)
        assert info is not None and info["status"] == "completed", f"job未完成: {info}"
        # dream空消息路径：不烧LLM，返回"No messages to distill"（诚实结果）
        assert info["result"]["error"] == "No messages to distill"
        assert info["result"]["dream_id"].startswith("dream_")

    def test_dream_sync_path_unchanged(self, client):
        """background=False保持既有同步行为（增量改动不破坏原路径）"""
        r = client.post("/api/hippo/ltm/dream", json={"messages": []})
        assert r.status_code == 200
        body = r.json()
        assert "queued" not in body
        assert body["dream_id"].startswith("dream_")

    def test_idempotency_live(self, client):
        key = f"itest-{int(time.time())}"
        r1 = client.post(
            "/api/will/jobs/submit",
            json={
                "name": "heredity.evaluate_triggers",
                "params": {"idle_seconds": 0},
                "idempotency_key": key,
                "timeout_s": 1,
            },
        )
        jid1 = r1.json()["job_id"]
        r2 = client.post(
            "/api/will/jobs/submit",
            json={
                "name": "heredity.evaluate_triggers",
                "params": {"idle_seconds": 0},
                "idempotency_key": key,
                "timeout_s": 1,
            },
        )
        body2 = r2.json()
        # 第二次提交可能撞上pending/running窗口（deduped）或已完成（新作业），
        # 两者都是合法语义；此断言只允许这两种情况，不允许空id
        assert body2["job_id"]
        if body2.get("deduped"):
            assert body2["job_id"] == jid1

    def test_jobs_list_shows_history(self, client):
        r = client.get("/api/will/jobs?limit=20")
        assert r.status_code == 200
        jobs = r.json()["jobs"]
        assert isinstance(jobs, list)
        # 前面的live用例已产生作业
        assert len(jobs) >= 1
        names = {j["name"] for j in jobs}
        assert "heredity.evaluate_triggers" in names or "hippo.dream" in names


# ════════════════════════════════════════════════════════════════
# ⑥ jobs.source provenance（learned_skills.source同款哲学）
# ════════════════════════════════════════════════════════════════
# 背景（本轮修复）：systemic_test往生产 /api/will/jobs/submit 提交test_job/
# test_cancel/stress_test（无标记）→ 生产job_queue.db 1092行垃圾 → experiences
# 1092行假经验 → evolution_log 279行假"高频失败"进化。修复=source列provenance
# +采集侧跳过+产生侧自清。名字不参与判定（test_job_outcomes同款钉）。


@pytest.fixture()
def will_client(tmp_path, monkeypatch):
    """will jobs API隔离测试面：真实router + tmp独立job库——绝不写生产
    job_queue.db，也不依赖live :8090（gene_client同款路线）。

    src.api.will延迟import：模块import有单例构造副作用
    （_register_job_handlers(_get_job_queue())）——先换tmp单例再import，
    副作用落tmp库，生产库零触碰。live purge?source=test在旧部署上会退化成
    零过滤purge（=DELETE全表footgun），故API面测试必须走隔离面。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.will.job_queue as jq_mod
    from src.will.job_queue import JobQueue

    jq = JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=1)
    monkeypatch.setattr(jq_mod, "_job_queue", jq)

    import src.api.will as will_api  # noqa: E402 —— 必须在单例替换后import

    app = FastAPI()
    app.include_router(will_api.router, prefix="/api/will")
    with TestClient(app) as c:
        yield c


_LEGACY_JOB_SCHEMA = """
CREATE TABLE jobs (
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
    idempotency_key TEXT DEFAULT ''
)
"""


class TestJobSourceProvenance:
    def test_default_source_real(self, jq):
        jid = run(jq.submit("heredity.evaluate_triggers", {"idle_seconds": 0}))
        info = jq.get(jid)
        assert info is not None and info["source"] == "real"

    def test_explicit_test_source(self, jq):
        jid = run(jq.submit("test_job", {}, source="test"))
        info = jq.get(jid)
        assert info is not None and info["source"] == "test"

    def test_unknown_source_kept_verbatim(self, jq):
        # 未知不猜不归一：非'test'原样保留（只有显式test被采集侧跳过）
        jid = run(jq.submit("x.y", {}, source="custom"))
        assert jq.get(jid)["source"] == "custom"

    def test_source_sticky_through_persist_and_reopen(self, tmp_path):
        db = str(tmp_path / "jobs.db")
        jq1 = JobQueue(db_path=db, max_workers=1)
        jid = run(jq1.submit("test_job", {}, source="test"))
        job = jq1._jobs[jid]
        job.status = JobStatus.FAILED
        job.error = "No handler for job type 'test_job'"
        jq1._persist(job)  # 状态更新轮（INSERT OR REPLACE）不洗白provenance
        jq2 = JobQueue(db_path=db, max_workers=1)  # 模拟进程重启
        info = jq2.get(jid)
        assert info is not None and info["source"] == "test"

    def test_legacy_db_migration_and_idempotent(self, tmp_path):
        db = tmp_path / "jobs.db"
        conn = sqlite3.connect(db)
        conn.execute(_LEGACY_JOB_SCHEMA)  # 迁移前老schema（无source列）
        conn.execute(
            "INSERT INTO jobs (id, name, status, created_at) VALUES ('old1','hippo.dream','completed',1000.0)"
        )
        conn.commit()
        conn.close()
        JobQueue(db_path=str(db), max_workers=1)  # 迁移补列
        jq = JobQueue(db_path=str(db), max_workers=1)  # 幂等：重复init不炸
        info = jq.get("old1")
        assert info["source"] == "real"  # 存量行默认real（清理靠一次性purge不靠列默认值）

    def test_purge_by_source_only_removes_test(self, tmp_path):
        jq = JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=1)
        jid_test = run(jq.submit("test_job", {}, source="test"))
        jid_real = run(jq.submit("heredity.evaluate_triggers", {}))
        assert jq.purge(source="test") == 1
        assert jq.get(jid_test) is None  # 内存注册表同步清理（不留幽灵副本）
        assert jq.get(jid_real) is not None

    def test_purge_zero_filter_refused(self, tmp_path):
        # 零过滤purge=DELETE全表footgun——拒绝且不动任何行
        jq = JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=1)
        jid = run(jq.submit("heredity.evaluate_triggers", {}))
        assert jq.purge() == 0
        assert jq.get(jid) is not None

    def test_api_submit_carries_source(self, will_client):
        # API面（隔离in-process真实router）：source经submit请求→API→queue落库
        r = will_client.post(
            "/api/will/jobs/submit",
            json={"name": "test_job", "params": {}, "timeout_s": 1, "source": "test"},
        )
        assert r.status_code == 200
        jid = r.json()["job_id"]
        info = will_client.get(f"/api/will/jobs/{jid}").json()
        assert info.get("source") == "test"
        # purge端点按source精确清（零过滤guard同面验证）
        assert will_client.post("/api/will/jobs/purge").status_code == 400  # 零过滤拒绝
        p = will_client.post("/api/will/jobs/purge?source=test")
        assert p.status_code == 200 and p.json()["purged"] == 1
        assert will_client.get(f"/api/will/jobs/{jid}").status_code == 404
