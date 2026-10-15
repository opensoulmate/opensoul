"""DEC-⑤-① 执行结果回传协议·OpenSoul 接收面——Reflector 真接线 + experiences fix 列闭环。

测的是 src/cortex/reflector.py::Reflector.reflect（exit_code 证据→退出码检查）+
src/api/brain.py::execution_result（协议接收面：证据→真 Verification→experiences
落库含 fix）+ src/learn/experience.py::ExperienceMemory.record（task_id 溯源进
metadata）。修前 reflect→record 整链全仓零生产调用、experiences.fix 恒空
（pending-decisions ⑤ 根因）。数据面零依赖（tmp SQLitePool 全隔离，绝不写生产
data/opensoul.db）。
"""

import asyncio
import json
import sqlite3

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


def _rows(tmp_path):
    """独立 sqlite3 回读（不经被测池）：真落盘硬证。零写入=表未建→空集
    （ExperienceMemory._ensure_table 随首次 record 建表）。"""
    con = sqlite3.connect(tmp_path / "opensoul.db")
    con.row_factory = sqlite3.Row
    try:
        try:
            return [dict(r) for r in con.execute("SELECT * FROM experiences ORDER BY id")]
        except sqlite3.OperationalError:
            return []
    finally:
        con.close()


# ── ① Reflector exit_code 证据语义（纯函数面）────────────────────────


class TestReflectorExitCodeEvidence:
    def test_exit_code_zero_passes_check(self):
        """exit_code=0 → 退出码检查在场且 passed（回传方给了证据就出检查项）。"""
        from src.cortex.reflector import Reflector
        from src.models.cognitive import TaskContext

        v = asyncio.run(Reflector().reflect("run tests", {"exit_code": 0}, TaskContext()))
        names = [c.name for c in v.checks]
        assert "退出码" in names
        assert all(c.passed for c in v.checks if c.name == "退出码")
        assert v.success is True

    def test_exit_code_nonzero_fails_with_fix(self):
        """exit_code=3 → 退出码检查失败、error 带真码、fix 带非零退出建议。"""
        from src.cortex.reflector import Reflector
        from src.models.cognitive import TaskContext

        v = asyncio.run(Reflector().reflect("run tests", {"exit_code": 3}, TaskContext()))
        failed = [c for c in v.checks if c.name == "退出码"]
        assert len(failed) == 1
        assert failed[0].passed is False
        assert failed[0].error and "3" in failed[0].error
        assert v.success is False
        assert v.fix and "非零退出" in v.fix

    def test_exit_code_absent_zero_change(self):
        """未给 exit_code → 零行为变化：无退出码检查，既有「无检查项」语义照旧。"""
        from src.cortex.reflector import Reflector
        from src.models.cognitive import TaskContext

        v = asyncio.run(Reflector().reflect("run tests", {}, TaskContext()))
        assert [c.name for c in v.checks] == ["无检查项"]
        assert v.success is True

    def test_exit_code_combined_with_test_output(self):
        """多项证据并存：检查全出，error=首个失败项（既有 failed[0] 语义零变化）。"""
        from src.cortex.reflector import Reflector
        from src.models.cognitive import TaskContext

        v = asyncio.run(
            Reflector().reflect(
                "run tests",
                {"test_output": {"passed": False, "error": "assert 1==2"}, "exit_code": 3},
                TaskContext(),
            )
        )
        names = [c.name for c in v.checks]
        assert names == ["功能校验", "退出码"]
        assert v.error == "assert 1==2"
        assert v.success is False


# ── ② 协议接收面（API 全链：证据→真 Verification→experiences 含 fix）──


class TestProtocolFace:
    def test_failure_report_records_fix_row(self, exec_client, tmp_path):
        """fix 列闭环硬证（⑤ 根因修）：失败回报→outcome=failure、fix 非空并
        与响应逐字一致落 experiences 行（修前 fix 恒空）。"""
        r = exec_client.post(
            "/api/brain/execution-result",
            json={
                "action": "跑测试修登录bug",
                "goal": "fix_bug",
                "task_id": "task-fix-1",
                "test_output": {"passed": False, "error": "assert 1==2"},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["outcome"] == "failure"
        assert body["recorded"] is True
        assert body["fix"] == "功能测试失败：assert 1==2"

        rows = _rows(tmp_path)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "failure"
        assert rows[0]["fix"] == body["fix"]
        assert rows[0]["error"] == "assert 1==2"
        assert rows[0]["intent_summary"] == "fix_bug"
        assert json.loads(rows[0]["metadata"]) == {"task_id": "task-fix-1"}

    def test_success_report_records_success_row(self, exec_client, tmp_path):
        """成功回报（test_output passed + exit_code 0）→ outcome=success、fix NULL。"""
        r = exec_client.post(
            "/api/brain/execution-result",
            json={
                "action": "重构完成",
                "goal": "refactor",
                "exit_code": 0,
                "test_output": {"passed": True},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "success"
        assert body["fix"] is None
        names = [c["name"] for c in body["checks"]]
        assert names == ["功能校验", "退出码"]
        rows = _rows(tmp_path)
        assert rows[0]["outcome"] == "success"
        assert rows[0]["fix"] is None

    def test_exit_code_failure_persisted(self, exec_client, tmp_path):
        """只有 exit_code=3 的回报 → 失败+fix 非零退出建议真落库。"""
        r = exec_client.post(
            "/api/brain/execution-result",
            json={"action": "跑脚本", "goal": "create", "exit_code": 3},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "failure"
        assert "非零退出" in body["fix"]
        rows = _rows(tmp_path)
        assert rows[0]["outcome"] == "failure"
        assert rows[0]["fix"] == body["fix"]

    def test_exception_evidence(self, exec_client, tmp_path):
        """exception+error → 系统异常检查失败，fix=异常处理建议真落库。"""
        r = exec_client.post(
            "/api/brain/execution-result",
            json={"action": "执行崩溃", "exception": True, "error": "boom"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "failure"
        assert body["error"] == "boom"
        assert body["fix"] == "异常处理：boom"
        rows = _rows(tmp_path)
        assert rows[0]["fix"] == "异常处理：boom"

    def test_anonymous_scope_and_goal_fallback(self, exec_client, tmp_path):
        """匿名=派生 'default' 行归属契约不变；goal 空→intent_summary 'unknown'。"""
        r = exec_client.post(
            "/api/brain/execution-result",
            json={"action": "匿名回报", "agent_id": "agent-x", "exit_code": 0},
        )
        assert r.status_code == 200
        rows = _rows(tmp_path)
        assert rows[0]["tenant_id"] == "default"
        assert rows[0]["agent_id"] == "agent-x"
        assert rows[0]["intent_summary"] == "unknown"

    def test_empty_evidence_no_checks_success(self, exec_client):
        """零证据回报 → Reflector 既有「无检查项」语义照旧（如实回出检查项，
        不造检查、不谎报 failure）。"""
        r = exec_client.post(
            "/api/brain/execution-result",
            json={"action": "只说做完了"},
        )
        assert r.status_code == 200
        body = r.json()
        assert [c["name"] for c in body["checks"]] == ["无检查项"]
        assert body["outcome"] == "success"

    def test_action_required_422(self, exec_client, tmp_path):
        """action 是协议必填证据（experiences.action NOT NULL）：空→422 零落库。"""
        r = exec_client.post("/api/brain/execution-result", json={"action": ""})
        assert r.status_code == 422
        assert _rows(tmp_path) == []

    def test_declared_nondefault_tenant_anonymous_403(self, exec_client, tmp_path):
        """匿名自报非 default 租户=403（F3b-② 声明矛盾收口），零落库。"""
        r = exec_client.post(
            "/api/brain/execution-result",
            json={"action": "冒读面", "tenant_id": "tenant-x", "exit_code": 0},
        )
        assert r.status_code == 403
        assert _rows(tmp_path) == []

    def test_record_failure_500_no_lie(self, exec_client, monkeypatch):
        """落库失败如实 500（C2 禁谎报 ok）：record 抛错不得回 recorded=True。"""
        import src.learn.experience as exp_mod

        async def _boom(self, action, result, verification, task_ctx):
            raise RuntimeError("db down")

        monkeypatch.setattr(exp_mod.ExperienceMemory, "record", _boom)
        r = exec_client.post(
            "/api/brain/execution-result",
            json={"action": "落库故障", "exit_code": 0},
        )
        assert r.status_code == 500
        assert "经验落库失败" in r.json()["detail"]

    def test_file_written_and_diff_evidence(self, exec_client, tmp_path):
        """file_written/diff 证据→语法校验/意图匹配检查出项（协议字段全接线）。"""
        r = exec_client.post(
            "/api/brain/execution-result",
            json={
                "action": "改文件",
                "target_files": ["app.py"],
                "file_written": True,
                "file_path": "/nonexistent/app.py",  # 缺文件→语法校验按既有语义放行
                "diff": "diff --git a/app.py",
                "exit_code": 0,
            },
        )
        assert r.status_code == 200
        names = [c["name"] for c in r.json()["checks"]]
        assert names == ["语法校验", "退出码", "意图匹配"]
        assert r.json()["outcome"] == "success"
        assert _rows(tmp_path)[0]["outcome"] == "success"
