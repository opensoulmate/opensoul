"""agno retry_or_fail退避 + 错误风暴熔断 + requeue（P0-8上轮遗留#5销账）。

调研来源对照：
- agno job_queue（42-agno-source.md §5.2 / evolution-engine-patterns.md §5.2）：
  retry_or_fail退避；requeue "budget grant = attempt+1 用户触发的一次续跑绝不静默重跑"
- agno environments §1.4 错误风暴熔断（evolution-engine-patterns.md §1.4）：
  连续同错即停——防系统性故障烧完K×N次调用，也防把系统性故障记成N条独立失败
- agno durable_queue cookbook：at-most-once默认——崩溃作业显式失败不静默重跑
  （副作用可能已发生），operator可requeue
- mem0 §1.1：失败必须可见——熔断/budget耗尽终态携带原因，不静默吞
- cortex/llm_retry.py：2s×2^n capped+half-jitter 同款退避惯例（系统一致性）

单元测试无网络（SQLite tmp_path隔离）；API面用例走conftest will_client隔离
（真实router+tmp数据面，2026-10-06收编轮）；live只读冒烟经conftest client打:8090。
"""

import asyncio
import sqlite3
import time

import pytest

from src.will.job_queue import JobQueue, JobStatus


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def jq(tmp_path):
    return JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=2, retry_jitter=False)


async def _poll(q: JobQueue, job_id: str, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = q.get(job_id)
        if info and info["status"] in ("completed", "failed", "timeout", "cancelled"):
            return info
        await asyncio.sleep(0.05)
    return q.get(job_id) or {}


# ── ① 指数退避（agno retry_or_fail退避）────────────────────────


class TestBackoff:
    def test_backoff_delay_grows_exponentially_capped(self, tmp_path):
        q = JobQueue(
            db_path=str(tmp_path / "j.db"),
            retry_base_delay_s=2.0,
            retry_max_delay_s=5.0,
            retry_jitter=False,
        )
        assert q._backoff_delay(1) == 2.0
        assert q._backoff_delay(2) == 4.0
        assert q._backoff_delay(3) == 5.0  # capped
        # jitter区间：half-jitter ∈ [0.5, 1.0]×base（llm_retry.py同款）
        qj = JobQueue(db_path=str(tmp_path / "j2.db"), retry_base_delay_s=2.0, retry_jitter=True)
        for attempt in (1, 2, 3):
            d = qj._backoff_delay(attempt)
            base = min(2.0 * 2 ** (attempt - 1), 60.0)
            assert base * 0.5 <= d <= base

    def test_retry_waits_backoff_gap(self, tmp_path):
        """失败后不立即重跑：两次执行间隔≥退避下界（修复前=立即回队，gap≈0）"""
        q = JobQueue(
            db_path=str(tmp_path / "jobs.db"),
            max_workers=2,
            retry_base_delay_s=0.6,
            retry_max_delay_s=2.0,
            retry_jitter=False,
        )
        runs = []

        async def flaky(**p):
            runs.append(time.time())
            if len(runs) == 1:
                raise RuntimeError("transient blip")
            return {"n": len(runs)}

        async def scenario():
            q.register_handler("t.backoff", flaky)
            await q.start()
            job_id = await q.submit("t.backoff", {}, max_retries=2)
            info = await _poll(q, job_id)
            await q.stop()
            return info

        info = run(scenario())
        assert info["status"] == "completed"
        assert info["retries"] == 1
        assert len(runs) == 2
        gap = runs[1] - runs[0]
        assert gap >= 0.6, f"重试未退避: gap={gap:.2f}s < 0.6s"

    def test_backoff_grows_between_retries(self, tmp_path):
        """连续两次重试间隔递增（0.4s→0.8s指数语义）"""
        q = JobQueue(
            db_path=str(tmp_path / "jobs.db"),
            max_workers=2,
            retry_base_delay_s=0.4,
            retry_max_delay_s=2.0,
            retry_jitter=False,
        )
        runs = []

        async def always_fail(**p):
            runs.append(time.time())
            raise RuntimeError(f"boom{len(runs)}")  # 每次错误不同→不触发熔断

        async def scenario():
            q.register_handler("t.grow", always_fail)
            await q.start()
            job_id = await q.submit("t.grow", {}, max_retries=2)
            info = await _poll(q, job_id, timeout=10)
            await q.stop()
            return info

        info = run(scenario())
        assert info["status"] == "failed"
        assert info["retries"] == 2  # budget耗尽终态，error原文无breaker前缀
        assert len(runs) == 3
        gap1 = runs[1] - runs[0]
        gap2 = runs[2] - runs[1]
        assert gap2 > gap1, f"退避未递增: {gap1:.2f}s vs {gap2:.2f}s"
        assert not info["error"].startswith("[breaker_open]")

    def test_next_retry_at_visible_in_to_dict_and_stats(self, tmp_path):
        """observability：退避等待期间next_retry_at落盘可见+stats暴露retry_scheduled"""
        q = JobQueue(
            db_path=str(tmp_path / "jobs.db"),
            max_workers=2,
            retry_base_delay_s=1.0,
            retry_jitter=False,
        )
        runs = []

        async def failing(**p):
            runs.append(time.time())
            raise RuntimeError(f"err{len(runs)}")

        async def scenario():
            q.register_handler("t.obs", failing)
            await q.start()
            job_id = await q.submit("t.obs", {}, max_retries=2)
            snapshot = None
            deadline = time.time() + 3
            while time.time() < deadline:
                info = q.get(job_id)
                if info and info["status"] == "pending" and info["retries"] == 1:
                    snapshot = (info, time.time())
                    break
                await asyncio.sleep(0.02)
            stats = q.get_stats()
            await q.stop()
            return snapshot, stats

        snapshot, stats = run(scenario())
        assert snapshot is not None, "退避等待窗口未观测到pending+retries=1"
        info, ts = snapshot
        assert info["next_retry_at"] > ts, "next_retry_at应指向未来（退避窗口）"
        assert stats["retry_scheduled"] == 1
        assert "breakers" in stats
        assert isinstance(stats["breakers"], dict)

    def test_completed_job_next_retry_at_zero(self, jq):
        async def scenario():
            async def ok(**p):
                return {}

            jq.register_handler("t.ok", ok)
            await jq.start()
            job_id = await jq.submit("t.ok", {})
            info = await _poll(jq, job_id)
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "completed"
        assert info["next_retry_at"] == 0


# ── ② 错误风暴熔断（agno environments §1.4）─────────────────────


class TestStormBreaker:
    def test_same_error_storm_opens_breaker_suppresses_retry(self, tmp_path):
        """连续同错误≥window→熔断：budget=5但只重试1次，error带breaker_open前缀"""
        q = JobQueue(
            db_path=str(tmp_path / "jobs.db"),
            max_workers=2,
            retry_base_delay_s=0.1,
            retry_max_delay_s=0.2,
            retry_jitter=False,
            storm_window=2,
            storm_cooldown_s=60,
        )
        attempts = {"n": 0}

        async def systemic(**p):
            attempts["n"] += 1
            raise ConnectionError("provider down")  # 同错误风暴

        async def scenario():
            q.register_handler("t.storm", systemic)
            await q.start()
            job_id = await q.submit("t.storm", {}, max_retries=5)
            info = await _poll(q, job_id)
            stats = q.get_stats()
            await q.stop()
            return info, stats

        info, stats = run(scenario())
        # window=2：fail1 count=1→允许重试；fail2 count=2→熔断→终态
        assert info["status"] == "failed"
        assert info["retries"] == 1, "熔断后不烧剩余重试预算"
        assert attempts["n"] == 2
        assert info["error"].startswith("[breaker_open]")
        assert "provider down" in info["error"]
        br = stats["breakers"]["t.storm"]
        assert br["open"] is True
        assert br["count"] >= 2

    def test_different_errors_do_not_trip_breaker(self, tmp_path):
        """不同错误签名≠风暴：重试预算正常用完，终态error无breaker前缀"""
        q = JobQueue(
            db_path=str(tmp_path / "jobs.db"),
            max_workers=2,
            retry_base_delay_s=0.1,
            retry_jitter=False,
            storm_window=2,
        )
        n = {"i": 0}

        async def varied(**p):
            n["i"] += 1
            raise RuntimeError(f"error variant {n['i']}")

        async def scenario():
            q.register_handler("t.varied", varied)
            await q.start()
            job_id = await q.submit("t.varied", {}, max_retries=2)
            info = await _poll(q, job_id)
            await q.stop()
            return info

        info = run(scenario())
        assert info["status"] == "failed"
        assert info["retries"] == 2, "非风暴错误应正常消耗重试预算"
        assert not info["error"].startswith("[breaker_open]")

    def test_breaker_resets_on_success(self, tmp_path):
        """同名作业成功→熔断复位（功能验证：复位后新风暴从0计数）。

        窗口=3。Job A：第1次失败（count=1）→重试→成功→熔断条目复位清除。
        Job B（同名）：三次同错→count 1/2/3→第3次熔断终态retries=2。
        若A成功未复位，B的第一次失败将继承count=1→第2次失败即熔断→retries=1；
        retries==2证明计数从0重新开始=成功复位真实生效。
        """
        q = JobQueue(
            db_path=str(tmp_path / "jobs.db"),
            max_workers=2,
            retry_base_delay_s=0.1,
            retry_jitter=False,
            storm_window=3,
        )
        state = {"ok_calls": 0, "bad_calls": 0}

        async def handler(**p):
            if p.get("mode") == "ok":
                state["ok_calls"] += 1
                if state["ok_calls"] == 1:
                    raise RuntimeError("same error")
                return {"ok": state["ok_calls"]}
            state["bad_calls"] += 1
            raise RuntimeError("same error")

        async def scenario():
            q.register_handler("t.reset", handler)
            await q.start()
            jid_a = await q.submit("t.reset", {"mode": "ok"}, max_retries=5)
            info_a = await _poll(q, jid_a)
            breaker_after_success = q._breakers.get("t.reset")
            jid_b = await q.submit("t.reset", {"mode": "bad"}, max_retries=3)
            info_b = await _poll(q, jid_b)
            await q.stop()
            return info_a, breaker_after_success, info_b

        info_a, breaker_after_success, info_b = run(scenario())
        assert info_a["status"] == "completed"
        assert info_a["result"] == {"ok": 2}
        assert breaker_after_success is None, "成功后熔断条目应清除"
        # 复位生效的功能证据：B的风暴从0计数（retries=2而非1）
        assert info_b["retries"] == 2
        assert info_b["error"].startswith("[breaker_open]")
        assert state["bad_calls"] == 3

    def test_breaker_half_open_after_cooldown(self, tmp_path):
        """cooldown过期→半开：新失败重新计数，允许退避重试一次后再次熔断"""
        q = JobQueue(
            db_path=str(tmp_path / "jobs.db"),
            max_workers=2,
            retry_base_delay_s=0.05,
            retry_jitter=False,
            storm_window=2,
            storm_cooldown_s=0.3,
        )
        calls = []

        async def systemic(**p):
            calls.append(time.time())
            raise ValueError("same")

        async def scenario():
            q.register_handler("t.half", systemic)
            await q.start()
            jid1 = await q.submit("t.half", {}, max_retries=3)
            info1 = await _poll(q, jid1)  # fail1重试→fail2熔断→终态(retries=1)
            await asyncio.sleep(0.4)  # cooldown(0.3s)过期
            jid2 = await q.submit("t.half", {}, max_retries=3)
            info2 = await _poll(q, jid2)  # 半开：失败重新计数，允许一次重试
            await q.stop()
            return info1, info2

        info1, info2 = run(scenario())
        assert info1["retries"] == 1
        assert info1["error"].startswith("[breaker_open]")
        # cooldown过期后：fail3(count=1)允许重试→fail4(count=2)再熔断
        assert info2["retries"] == 1, "半开后应允许一次退避重试"
        assert len(calls) == 4


# ── ③ requeue人工续跑（agno budget grant=恰好一次）──────────────


class TestRequeue:
    def test_requeue_grants_exactly_one_attempt_then_succeeds(self, tmp_path):
        """终态作业requeue后重跑一次成功"""
        q = JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=1, retry_jitter=False)
        calls = {"n": 0}

        async def handler(**p):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("first run fails")
            return {"n": calls["n"]}

        async def scenario():
            q.register_handler("t.req", handler)
            await q.start()
            jid = await q.submit("t.req", {}, max_retries=0)  # 无自动重试预算
            info = await _poll(q, jid)
            assert info["status"] == "failed" and info["retries"] == 0
            rq = await q.requeue(jid)
            info2 = await _poll(q, jid)
            await q.stop()
            return rq, info2

        rq, info2 = run(scenario())
        assert rq["requeued"] is True
        assert info2["status"] == "completed"
        assert info2["result"] == {"n": 2}

    def test_requeue_failure_is_terminal_no_silent_retry(self, tmp_path):
        """'用户触发的一次续跑绝不静默重跑'：requeue后再失败恰好只多一次执行"""
        q = JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=1, retry_jitter=False)
        calls = {"n": 0}

        async def broken(**p):
            calls["n"] += 1
            raise ValueError("always")

        async def scenario():
            q.register_handler("t.req2", broken)
            await q.start()
            jid = await q.submit("t.req2", {}, max_retries=1)
            info1 = await _poll(q, jid)  # 执行+1次重试=2次调用后终态
            n_after_first = calls["n"]
            rq = await q.requeue(jid)  # grant恰好一次
            info2 = await _poll(q, jid)
            await q.stop()
            return info1, rq, info2, n_after_first

        info1, rq, info2, n_after_first = run(scenario())
        assert info1["retries"] == 1
        assert n_after_first == 2
        assert rq["requeued"] is True
        assert info2["status"] == "failed"
        assert calls["n"] == n_after_first + 1, "requeue只grant恰好一次执行，不静默追加重试"

    def test_requeue_not_found_and_not_terminal(self, jq):
        async def scenario():
            await jq.start()
            rq_missing = await jq.requeue("job_nope")

            async def noop(**p):
                return {"ok": 1}

            jq.register_handler("t.nt", noop)
            jid_pending = await jq.submit("t.nt", {})
            await jq.requeue(jid_pending)  # 未start消费前pending…实际worker已启动
            info = await _poll(jq, jid_pending)
            rq_completed = await jq.requeue(jid_pending) if info["status"] == "completed" else None
            await jq.stop()
            return rq_missing, info, rq_completed

        rq_missing, info, rq_completed = run(scenario())
        assert rq_missing == {"requeued": False, "reason": "not_found"}
        assert info["status"] == "completed"
        assert rq_completed is not None
        assert rq_completed["requeued"] is False
        assert rq_completed["reason"].startswith("not_terminal")

    def test_requeue_cancelled_job_runs_once(self, tmp_path):
        """cancelled作业可requeue（人工反悔=合法意图）"""
        q = JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=1, retry_jitter=False)

        async def scenario():
            # 不start：submit后pending→cancel→requeue→start执行
            async def ok(**p):
                return {"ran": True}

            q.register_handler("t.cx", ok)
            jid = await q.submit("t.cx", {})
            assert q.cancel(jid) is True
            rq = await q.requeue(jid)
            await q.start()
            info = await _poll(q, jid)
            await q.stop()
            return rq, info

        rq, info = run(scenario())
        assert rq["requeued"] is True
        assert info["status"] == "completed"
        assert info["result"] == {"ran": True}


# ── ④ stale回收遵守重试预算（agno at-most-once）────────────────


class TestStaleRecoveryBudget:
    def test_stale_running_exhausted_budget_fails_visibly(self, jq, tmp_path):
        """RUNNING崩溃且retries≥max_retries→显式失败不静默重跑，error指引requeue"""
        with sqlite3.connect(str(tmp_path / "jobs.db")) as conn:
            conn.execute(
                "INSERT INTO jobs (id, name, status, params, created_at, retries, max_retries, idempotency_key) "
                "VALUES ('job_dead01', 't.dead', 'running', '{}', ?, 2, 2, '')",
                (time.time(),),
            )

        async def scenario():
            ran = []

            async def handler(**p):
                ran.append(1)
                return {}

            jq.register_handler("t.dead", handler)
            await jq.start()
            await asyncio.sleep(0.3)
            info = jq.get("job_dead01")
            await jq.stop()
            return ran, info

        ran, info = run(scenario())
        assert info["status"] == "failed"
        assert "retry budget exhausted" in info["error"]
        assert "/requeue" in info["error"]
        assert ran == [], "budget耗尽的崩溃作业不允许静默重跑（agno at-most-once）"

    def test_requeue_recovers_budget_exhausted_stale_job(self, jq, tmp_path):
        """requeue可救回budget耗尽的崩溃作业（operator干预路径）"""
        with sqlite3.connect(str(tmp_path / "jobs.db")) as conn:
            conn.execute(
                "INSERT INTO jobs (id, name, status, params, created_at, retries, max_retries, idempotency_key) "
                "VALUES ('job_dead02', 't.dead2', 'running', '{}', ?, 2, 2, '')",
                (time.time(),),
            )

        async def scenario():
            async def handler(**p):
                return {"recovered": True}

            jq.register_handler("t.dead2", handler)
            await jq.start()
            info1 = await _poll(jq, "job_dead02")
            rq = await jq.requeue("job_dead02")
            info2 = await _poll(jq, "job_dead02")
            await jq.stop()
            return info1, rq, info2

        info1, rq, info2 = run(scenario())
        assert info1["status"] == "failed"
        assert rq["requeued"] is True
        assert info2["status"] == "completed"
        assert info2["result"] == {"recovered": True}

    def test_stale_running_within_budget_still_requeued(self, jq, tmp_path):
        """既有契约回归：budget未耗尽的崩溃作业仍回pending重跑（原行为保留）"""
        with sqlite3.connect(str(tmp_path / "jobs.db")) as conn:
            conn.execute(
                "INSERT INTO jobs (id, name, status, params, created_at, retries, max_retries, idempotency_key) "
                "VALUES ('job_live01', 't.rec3', 'running', '{}', ?, 0, 2, '')",
                (time.time(),),
            )

        async def scenario():
            async def rec(**p):
                return {"recovered": True}

            jq.register_handler("t.rec3", rec)
            await jq.start()
            info = await _poll(jq, "job_live01")
            await jq.stop()
            return info

        info = run(scenario())
        assert info["status"] == "completed"
        assert info["result"] == {"recovered": True}


# ── ⑤ 退避窗口跨重启保留（DB真源语义）──────────────────────────


class TestBackoffAcrossRestart:
    def test_pending_backoff_window_survives_restart(self, tmp_path):
        """重启后next_retry_at未到→不立即执行；到点→自动执行"""
        db = str(tmp_path / "jobs.db")
        JobQueue(db_path=db, max_workers=1)  # 仅初始化schema
        future = time.time() + 0.5
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO jobs (id, name, status, params, created_at, retries, max_retries, idempotency_key, next_retry_at) "
                "VALUES ('job_bt01', 't.bt', 'pending', '{}', ?, 1, 2, '', ?)",
                (time.time(), future),
            )

        async def scenario():
            q = JobQueue(db_path=db, max_workers=1)

            async def handler(**p):
                return {"ok": 1}

            q.register_handler("t.bt", handler)
            await q.start()
            await asyncio.sleep(0.2)
            early = q.get("job_bt01")  # 退避窗口内：仍pending
            info = await _poll(q, "job_bt01", timeout=5)  # 到点后执行
            await q.stop()
            return early, info

        early, info = run(scenario())
        assert early["status"] == "pending", "退避窗口内不允许立即执行"
        assert info["status"] == "completed"

    def test_schema_migration_adds_next_retry_at_column(self, tmp_path):
        """老库迁移：无next_retry_at列的jobs表启动后列存在（幂等probe ALTER）"""
        db = str(tmp_path / "old.db")
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT DEFAULT 'pending',
                    params TEXT DEFAULT '{}', result TEXT, error TEXT DEFAULT '',
                    created_at REAL, started_at REAL DEFAULT 0, finished_at REAL DEFAULT 0,
                    timeout_s INTEGER DEFAULT 300, retries INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 2
                )
                """
            )
            conn.execute(
                "INSERT INTO jobs (id, name, status, created_at) VALUES ('j_old', 't.old', 'pending', ?)",
                (time.time(),),
            )
        q = JobQueue(db_path=db, max_workers=1)
        with sqlite3.connect(db) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)")]
        assert "next_retry_at" in cols
        assert "idempotency_key" in cols
        info = q.get("j_old")
        assert info["next_retry_at"] == 0


# ── ⑥ API面（隔离：conftest will_client）+ live只读冒烟 ──────────
# 2026-10-06收编轮：原live写用例 test_requeue_endpoint_wired_e2e 迁到隔离面
# （live提交的作业行source='real'落生产job_queue.db→假experiences，多轮挂账
# "test_job_retry_backoff等live测试写生产库"销账）。只读观测键用例留守live。


class TestRequeueAPI:
    """requeue端点语义（隔离面）：真实router+tmp数据面，零生产写入。"""

    def test_requeue_endpoint_flow(self, will_client):
        # not_found → 404
        r = will_client.post("/api/will/jobs/job_nonexistent_xyz/requeue")
        assert r.status_code == 404
        # 未注册handler作业→failed→requeue→200 grant一次→再次failed（no handler）
        r = will_client.post(
            "/api/will/jobs/submit", json={"name": "no.such.handler", "params": {}}
        )
        assert r.status_code == 200
        jid = r.json()["job_id"]
        info = None
        deadline = time.time() + 10
        while time.time() < deadline:
            info = will_client.get(f"/api/will/jobs/{jid}").json()
            if info["status"] in ("failed", "completed", "timeout"):
                break
            time.sleep(0.2)
        assert info is not None and info["status"] == "failed", f"job未失败: {info}"
        assert "No handler for job type" in info["error"]
        rq = will_client.post(f"/api/will/jobs/{jid}/requeue")
        assert rq.status_code == 200
        assert rq.json()["requeued"] is True
        deadline = time.time() + 10
        while time.time() < deadline:
            info = will_client.get(f"/api/will/jobs/{jid}").json()
            if info["status"] in ("failed", "completed", "timeout"):
                break
            time.sleep(0.2)
        assert info["status"] == "failed", "no handler作业requeue后应再次显式失败"
        assert "No handler for job type" in info["error"]
        # 原live版此处purge生产噪声——隔离面作业行落tmp库随fixture销毁，无需清


@pytest.mark.live
class TestLiveRequeueAPI:
    """对live opensoul服务验证观测键接线（只读，零生产写入；重启服务后运行）。"""

    def test_health_exposes_breaker_and_retry_stats(self, client):
        """新观测键接线：/jobs/health响应携带breakers+retry_scheduled"""
        r = client.get("/api/will/jobs/health")
        assert r.status_code == 200
        data = r.json()
        assert data["component"] == "JobQueue"
        assert "breakers" in data
        assert isinstance(data["breakers"], dict)
        assert "retry_scheduled" in data

    def test_jobs_list_carries_next_retry_at_field(self, client):
        r = client.get("/api/will/jobs?limit=5")
        assert r.status_code == 200
        jobs = r.json()["jobs"]
        assert isinstance(jobs, list)
        for j in jobs:
            assert "next_retry_at" in j, "既有作业to_dict应携带新字段（迁移生效）"
