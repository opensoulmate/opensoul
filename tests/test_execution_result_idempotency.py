"""DEC-⑤-①b 执行结果回传幂等——重复回传不重复落库（幂等键语义自决见
pending-decisions ⑪：显式 result_id 优先 + 证据指纹派生兜底）。

测的是 src/learn/experience.py::execution_result_key（键单一派生口）+
ExperienceMemory.record（(tenant, agent, result_key) at-most-once 去重）+
src/api/brain.py::execution_result（接收面幂等语义：duplicate 如实回报首行结果
禁谎报 recorded=True）。数据面 tmp SQLitePool 全隔离，绝不写生产 data/opensoul.db。
"""

import asyncio
import sqlite3
import time

import pytest


@pytest.fixture()
def exec_client(tmp_path, monkeypatch):
    """brain router + tmp opensoul.db（conf_client 同款路线：真 router/真 Reflector/
    真 ExperienceMemory，数据面=tmp）。"""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.brain as brain_api
    import src.database.postgres as pg_mod
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_db = tmp_path / "opensoul.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", tmp_pool)

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(brain_api.router, prefix="/api/brain")
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def tmp_pool(tmp_path, monkeypatch):
    """数据面直驱池（record 单元面用；连接/使用/断开全在同一 asyncio.run 块）。"""
    import src.database.postgres as pg_mod
    from src.config import settings
    from src.database.postgres import SQLitePool

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'opensoul.db'}")
    pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", pool)
    return pool


def _rows(tmp_path):
    """独立 sqlite3 回读（不经被测池）：真落盘硬证。零写入=表未建→空集。"""
    con = sqlite3.connect(tmp_path / "opensoul.db")
    con.row_factory = sqlite3.Row
    try:
        try:
            return [dict(r) for r in con.execute("SELECT * FROM experiences ORDER BY id")]
        except sqlite3.OperationalError:
            return []
    finally:
        con.close()


def _verification(success=True, error=None, fix=None):
    from src.models.cognitive import Verification

    return Verification(success=success, checks=[], error=error, fix=fix)


def _task_ctx(task_id=""):
    from src.models.cognitive import TaskContext

    return TaskContext(task_id=task_id)


# ── ① 幂等键单一派生口（纯函数面）────────────────────────────────


class TestExecutionResultKey:
    def test_explicit_id_wins_over_payload(self):
        """显式 result_id=客户端声明身份：同 id 不同证据同键（证据差异不拆行）。"""
        from src.learn.experience import execution_result_key

        k1 = execution_result_key("report-1", {"action": "a", "exit_code": 0})
        k2 = execution_result_key("report-1", {"action": "b", "exit_code": 3})
        assert k1 == k2 == "explicit:report-1"

    def test_explicit_strips_whitespace_blank_falls_to_derived(self):
        """空白 result_id=未给→证据指纹派生（显式/派生两形前缀如实可辨）。"""
        from src.learn.experience import execution_result_key

        derived = execution_result_key("", {"action": "a"})
        assert derived.startswith("derived:")
        assert execution_result_key("   ", {"action": "a"}) == derived
        assert execution_result_key(" r2 ", {"action": "a"}) == "explicit:r2"

    def test_derived_deterministic_same_payload_same_key(self):
        """同份证据（含 dict 字段）规范 JSON 派生确定性：重试/重复回传同键。"""
        from src.learn.experience import execution_result_key

        payload = {
            "task_id": "t1",
            "action": "跑测试",
            "goal": "fix_bug",
            "target_files": ["a.py"],
            "exit_code": 1,
            "test_output": {"passed": False, "error": "x"},
        }
        assert execution_result_key("", payload) == execution_result_key("", dict(payload))

    def test_derived_splits_on_evidence_change(self):
        """证据变化（另一次观测）→异键各记一行；逐字段任一变化都拆键。"""
        from src.learn.experience import execution_result_key

        base = {"task_id": "t1", "action": "a", "exit_code": 0, "error": None}
        assert execution_result_key("", base) != execution_result_key("", dict(base, exit_code=1))
        assert execution_result_key("", base) != execution_result_key("", dict(base, action="b"))
        assert execution_result_key("", base) != execution_result_key("", dict(base, error="e"))

    def test_derived_ignores_identity_axes(self):
        """键刻意不含租户/agent/result_id（作用域=行键）：跨租户同键互不去重。"""
        from src.learn.experience import execution_result_key

        base = {"action": "a", "exit_code": 0, "tenant_id": "tenant-a", "agent_id": "agent-1"}
        other = {"action": "a", "exit_code": 0, "tenant_id": "tenant-b", "agent_id": "agent-2"}
        assert execution_result_key("", base) == execution_result_key("", other)


# ── ② record at-most-once（数据面直驱）──────────────────────────


class TestRecordIdempotency:
    def test_same_key_second_is_duplicate_no_new_row(self, tmp_pool, tmp_path):
        """同键第二次回传：不落新行，返回首行 outcome/error/fix（duplicate 如实）。"""
        from src.learn.experience import ExperienceMemory

        async def _body():
            pool = tmp_pool
            await pool.connect()
            try:
                mem = ExperienceMemory(pool, "default", "default")
                r1 = await mem.record(
                    "act",
                    {},
                    _verification(False, "boom", "fix-it"),
                    _task_ctx("t1"),
                    result_key="k1",
                )
                r2 = await mem.record(
                    "act",
                    {},
                    _verification(False, "boom", "fix-it"),
                    _task_ctx("t1"),
                    result_key="k1",
                )
                return r1, r2
            finally:
                await pool.disconnect()

        r1, r2 = asyncio.run(_body())
        assert r1 == {
            "recorded": True,
            "duplicate": False,
            "outcome": "failure",
            "error": "boom",
            "fix": "fix-it",
        }
        assert r2 == {
            "recorded": False,
            "duplicate": True,
            "outcome": "failure",
            "error": "boom",
            "fix": "fix-it",
        }
        rows = _rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["result_key"] == "k1"

    def test_different_key_two_rows(self, tmp_pool, tmp_path):
        """异键（证据变化）各记一行——去重不吞掉第二次真实观测。"""
        from src.learn.experience import ExperienceMemory

        async def _body():
            pool = tmp_pool
            await pool.connect()
            try:
                mem = ExperienceMemory(pool, "default", "default")
                r1 = await mem.record(
                    "act", {}, _verification(True), _task_ctx(), result_key="derived:a"
                )
                r2 = await mem.record(
                    "act", {}, _verification(False, "e", "f"), _task_ctx(), result_key="derived:b"
                )
                return r1, r2
            finally:
                await pool.disconnect()

        r1, r2 = asyncio.run(_body())
        assert r1["recorded"] is True and r2["recorded"] is True
        assert len(_rows(tmp_path)) == 2

    def test_blank_key_behavior_unchanged(self, tmp_pool, tmp_path):
        """空键=既有语义逐字不变：每次回传都记一行（无幂等声明不拦截）。"""
        from src.learn.experience import ExperienceMemory

        async def _body():
            pool = tmp_pool
            await pool.connect()
            try:
                mem = ExperienceMemory(pool, "default", "default")
                r1 = await mem.record("act", {}, _verification(True), _task_ctx())
                r2 = await mem.record("act", {}, _verification(True), _task_ctx())
                return r1, r2
            finally:
                await pool.disconnect()

        r1, r2 = asyncio.run(_body())
        assert r1["recorded"] is True and r2["recorded"] is True
        rows = _rows(tmp_path)
        assert len(rows) == 2
        assert all(r["result_key"] is None for r in rows)

    def test_same_key_cross_scope_two_rows(self, tmp_pool, tmp_path):
        """同键跨 (tenant, agent) 作用域互不去重（防去重面变跨租户信息通道）。"""
        from src.learn.experience import ExperienceMemory

        async def _body():
            pool = tmp_pool
            await pool.connect()
            try:
                r1 = await ExperienceMemory(pool, "tenant-a", "default").record(
                    "act", {}, _verification(True), _task_ctx(), result_key="k1"
                )
                r2 = await ExperienceMemory(pool, "tenant-b", "default").record(
                    "act", {}, _verification(True), _task_ctx(), result_key="k1"
                )
                r3 = await ExperienceMemory(pool, "tenant-a", "agent-2").record(
                    "act", {}, _verification(True), _task_ctx(), result_key="k1"
                )
                return r1, r2, r3
            finally:
                await pool.disconnect()

        r1, r2, r3 = asyncio.run(_body())
        assert [r["recorded"] for r in (r1, r2, r3)] == [True, True, True]
        rows = _rows(tmp_path)
        assert len(rows) == 3
        assert {(r["tenant_id"], r["agent_id"]) for r in rows} == {
            ("tenant-a", "default"),
            ("tenant-b", "default"),
            ("tenant-a", "agent-2"),
        }

    def test_legacy_rows_never_deduped_and_migration(self, tmp_pool, tmp_path):
        """老库迁移（缺 result_key 列）幂等自愈：补列+唯一索引真建、存量行（NULL 键）
        不参与去重也不被误伤、迁移后同键去重真生效。"""
        con = sqlite3.connect(tmp_path / "opensoul.db")
        con.execute(
            """CREATE TABLE experiences (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   tenant_id TEXT NOT NULL,
                   agent_id TEXT NOT NULL,
                   action TEXT NOT NULL,
                   intent_summary TEXT NOT NULL,
                   outcome TEXT NOT NULL,
                   error TEXT,
                   fix TEXT,
                   relevance_score REAL DEFAULT 1.0,
                   created_at REAL NOT NULL,
                   metadata TEXT DEFAULT '{}'
               )"""
        )
        con.execute(
            "INSERT INTO experiences (tenant_id, agent_id, action, intent_summary, outcome, created_at)"
            " VALUES ('default','default','legacy-act','unknown','success', ?)",
            (time.time(),),
        )
        con.commit()
        con.close()

        from src.learn.experience import ExperienceMemory

        async def _body():
            pool = tmp_pool
            await pool.connect()
            try:
                mem = ExperienceMemory(pool, "default", "default")
                r1 = await mem.record("act", {}, _verification(True), _task_ctx(), result_key="k1")
                r2 = await mem.record("act", {}, _verification(True), _task_ctx(), result_key="k1")
                return r1, r2
            finally:
                await pool.disconnect()

        r1, r2 = asyncio.run(_body())
        assert r1["recorded"] is True and r2["duplicate"] is True
        rows = _rows(tmp_path)
        assert len(rows) == 2  # 存量 1 行 + 新 1 行（同键第二发被去重）
        assert rows[0]["result_key"] is None and rows[0]["action"] == "legacy-act"
        assert rows[1]["result_key"] == "k1"
        con = sqlite3.connect(tmp_path / "opensoul.db")
        try:
            names = {r[1] for r in con.execute("PRAGMA table_info(experiences)")}
            assert "result_key" in names
            idx = {r[1]: r[2] for r in con.execute("PRAGMA index_list(experiences)")}
            assert idx.get("idx_exp_result_key") == 1  # 唯一索引真建
        finally:
            con.close()


# ── ③ 接收面 HTTP 全链（重复回传幂等语义）────────────────────────


class TestExecutionResultHttpIdempotency:
    def test_identical_retry_single_row(self, exec_client, tmp_path):
        """同份证据重复回传（网络重试同型）：恰 1 行，第二发 duplicate 如实。"""
        payload = {"action": "跑测试修登录bug", "goal": "fix_bug", "exit_code": 1, "error": "x"}
        r1 = exec_client.post("/api/brain/execution-result", json=payload)
        r2 = exec_client.post("/api/brain/execution-result", json=payload)
        assert r1.status_code == 200 and r2.status_code == 200
        b1, b2 = r1.json(), r2.json()
        assert b1["recorded"] is True and b1["duplicate"] is False
        assert b2["recorded"] is False and b2["duplicate"] is True
        assert b2["outcome"] == b1["outcome"] == "failure"
        assert b2["result_key"] == b1["result_key"]
        assert len(_rows(tmp_path)) == 1

    def test_evidence_change_records_second_observation(self, exec_client, tmp_path):
        """证据变化（exit_code 0→1）=另一次观测：两行都真记，去重不吞第二次。"""
        base = {"action": "跑测试", "goal": "fix_bug"}
        r1 = exec_client.post("/api/brain/execution-result", json=dict(base, exit_code=0))
        r2 = exec_client.post("/api/brain/execution-result", json=dict(base, exit_code=1))
        assert r1.json()["recorded"] is True and r2.json()["recorded"] is True
        assert r1.json()["outcome"] == "success" and r2.json()["outcome"] == "failure"
        assert r1.json()["result_key"] != r2.json()["result_key"]
        assert len(_rows(tmp_path)) == 2

    def test_explicit_result_id_dedupes_across_payloads(self, exec_client, tmp_path):
        """显式 result_id=客户端声明身份：同 id 不同证据第二发仍 duplicate（不拆行）。"""
        r1 = exec_client.post(
            "/api/brain/execution-result",
            json={"action": "第一次上报", "result_id": "report-1", "exit_code": 0},
        )
        r2 = exec_client.post(
            "/api/brain/execution-result",
            json={"action": "第二次上报证据不同", "result_id": "report-1", "exit_code": 1},
        )
        assert r1.json()["recorded"] is True
        assert r2.json()["duplicate"] is True and r2.json()["recorded"] is False
        rows = _rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["action"] == "第一次上报"  # 首行胜出，第二发不改写
        assert rows[0]["result_key"] == "explicit:report-1"

    def test_duplicate_returns_stored_error_and_fix(self, exec_client, tmp_path):
        """duplicate 回报的是首行真落库的 error/fix（如实回放非现算）。"""
        payload = {"action": "执行崩溃", "exception": True, "error": "boom"}
        b1 = exec_client.post("/api/brain/execution-result", json=payload).json()
        b2 = exec_client.post("/api/brain/execution-result", json=payload).json()
        row = _rows(tmp_path)[0]
        assert b1["fix"] == row["fix"] == "异常处理：boom"
        assert b2["error"] == row["error"] == "boom"
        assert b2["fix"] == row["fix"]
        assert b2["checks"] == []  # 不重算不伪造检查项

    def test_first_record_response_shape_unchanged(self, exec_client):
        """首记响应既有键逐键不变（duplicate/result_key 为加性键）。"""
        r = exec_client.post(
            "/api/brain/execution-result",
            json={
                "action": "改文件",
                "target_files": ["app.py"],
                "file_written": True,
                "exit_code": 0,
            },
        )
        assert r.status_code == 200
        body = r.json()
        for key in ("status", "outcome", "checks", "error", "fix", "recorded"):
            assert key in body
        assert body["status"] == "ok"
        assert body["recorded"] is True
        assert [c["name"] for c in body["checks"]] == ["语法校验", "退出码"]

    def test_scope_by_agent_same_payload_two_rows(self, exec_client, tmp_path):
        """同份证据不同 agent_id=不同作用域两行（键不含 agent，作用域=行键）。"""
        payload = {"action": "跑脚本", "exit_code": 0}
        r1 = exec_client.post("/api/brain/execution-result", json=dict(payload, agent_id="agent-1"))
        r2 = exec_client.post("/api/brain/execution-result", json=dict(payload, agent_id="agent-2"))
        assert r1.json()["recorded"] is True and r2.json()["recorded"] is True
        assert r1.json()["result_key"] == r2.json()["result_key"]
        rows = _rows(tmp_path)
        assert len(rows) == 2
        assert {r["agent_id"] for r in rows} == {"agent-1", "agent-2"}
