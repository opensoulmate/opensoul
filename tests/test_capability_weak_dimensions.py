"""H1-②b-① 弱维度出口面测试 — /api/benchmark/capability/weak-dimensions。

H1-② 定性「get_weak_dimensions 弱维度出口」真缺口：record/get_stats/get_trend
活（api/benchmark.py 既有消费点）而弱维度分析零出口=评测数据无人指短板。

隔离纪律（TL-② 先例）：src.api.benchmark 模块 import 有单例构造副作用
（CapabilityEvaluator()/EvalStore() 缺省打生产 benchmark 目录），fixture 内延迟
import 并把模块单例换 tmp 实例——测试面=真实 router/handler 代码零损失，
数据面=绝不写生产 evaluations.db。
"""

import sqlite3
import time

import pytest


@pytest.fixture()
def capability_client(tmp_path, monkeypatch):
    """benchmark router 隔离测试面：真实 router + tmp evaluations.db。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.benchmark as benchmark_api
    from src.benchmark.evaluator import CapabilityEvaluator

    app = FastAPI()
    app.include_router(benchmark_api.router, prefix="/api/benchmark")

    evaluator = CapabilityEvaluator(db_path=str(tmp_path / "evaluations.db"))
    monkeypatch.setattr(benchmark_api, "capability_evaluator", evaluator)
    app.state.capability_db = evaluator.db_path
    with TestClient(app) as c:
        yield c


def _record_via_api(
    client, accuracy=0.9, efficiency=0.9, completeness=0.9, safety=0.9, helpfulness=0.9
):
    """经真实写入口记录一次评测。缺省全维 0.9=强分（>=0.7 不入弱维），
    被测维度显式传弱分——/capability/evaluate 恒写全 5 维，缺省弱分
    会把未测维度一并带进弱维列表（测试作者坑，首轮 4 failed 逼出）。"""
    resp = client.post(
        "/api/benchmark/capability/evaluate",
        json={
            "accuracy": accuracy,
            "efficiency": efficiency,
            "completeness": completeness,
            "safety": safety,
            "helpfulness": helpfulness,
        },
    )
    assert resp.status_code == 200
    time.sleep(0.005)  # eval_id=毫秒时间戳主键，防同毫秒撞主键（非被测语义）
    return resp.json()


class TestWeakDimensionsExit:
    """弱维度出口端点行为面。"""

    def test_weak_listed_strong_absent_exact_shape(self, capability_client):
        for _ in range(5):
            _record_via_api(capability_client, accuracy=0.2, completeness=0.9)
        resp = capability_client.get("/api/benchmark/capability/weak-dimensions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["task_type"] == ""
        assert data["min_samples"] == 5
        weak = data["weak_dimensions"]
        assert [w["dimension"] for w in weak] == ["accuracy"]
        assert set(weak[0].keys()) == {"dimension", "name", "avg_score", "sample_count"}
        assert weak[0]["name"] == "准确性"
        assert weak[0]["avg_score"] == 0.2
        assert weak[0]["sample_count"] == 5

    def test_min_samples_gate(self, capability_client):
        for _ in range(2):
            _record_via_api(capability_client, accuracy=0.3)
        # 默认 min_samples=5：2 个样本不足，不列
        resp = capability_client.get("/api/benchmark/capability/weak-dimensions")
        assert resp.json()["weak_dimensions"] == []
        # 显式 min_samples=2：达标即列
        resp = capability_client.get(
            "/api/benchmark/capability/weak-dimensions", params={"min_samples": 2}
        )
        data = resp.json()
        assert data["min_samples"] == 2
        weak = data["weak_dimensions"]
        assert [w["dimension"] for w in weak] == ["accuracy"]
        assert weak[0]["sample_count"] == 2

    def test_sorted_ascending_by_avg(self, capability_client):
        for _ in range(5):
            _record_via_api(capability_client, accuracy=0.3, helpfulness=0.1)
        resp = capability_client.get("/api/benchmark/capability/weak-dimensions")
        weak = resp.json()["weak_dimensions"]
        assert [w["dimension"] for w in weak] == ["helpfulness", "accuracy"]
        assert [w["avg_score"] for w in weak] == [0.1, 0.3]

    def test_task_type_filter(self, capability_client, monkeypatch):
        import src.api.benchmark as benchmark_api

        ev = benchmark_api.capability_evaluator
        for _ in range(5):
            ev.record("s", "code", {"accuracy": 0.2})
            time.sleep(0.005)
        for _ in range(5):
            _record_via_api(capability_client, accuracy=0.9)

        resp = capability_client.get(
            "/api/benchmark/capability/weak-dimensions", params={"task_type": "code"}
        )
        weak = resp.json()["weak_dimensions"]
        assert [w["dimension"] for w in weak] == ["accuracy"]
        assert resp.json()["task_type"] == "code"

        resp = capability_client.get(
            "/api/benchmark/capability/weak-dimensions",
            params={"task_type": "general"},
        )
        assert resp.json()["weak_dimensions"] == []

    def test_unknown_dimension_name_fallback(self, capability_client, monkeypatch):
        import src.api.benchmark as benchmark_api

        ev = benchmark_api.capability_evaluator
        for _ in range(3):
            ev.record("s", "general", {"mystery_dim": 0.1})
            time.sleep(0.005)
        resp = capability_client.get(
            "/api/benchmark/capability/weak-dimensions", params={"min_samples": 3}
        )
        weak = resp.json()["weak_dimensions"]
        assert [w["dimension"] for w in weak] == ["mystery_dim"]
        # 未收录维度名回落本名，不 fabricate 中文名
        assert weak[0]["name"] == "mystery_dim"

    def test_empty_db_empty_list(self, capability_client):
        resp = capability_client.get("/api/benchmark/capability/weak-dimensions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["weak_dimensions"] == []
        assert data["count"] == 0

    def test_min_samples_validation(self, capability_client):
        for bad in (0, -1, "abc"):
            resp = capability_client.get(
                "/api/benchmark/capability/weak-dimensions",
                params={"min_samples": bad},
            )
            assert resp.status_code == 422, bad


class TestWiring:
    """端点=真消费 get_weak_dimensions（写了≠接线了）。"""

    def test_endpoint_delegates_to_get_weak_dimensions(self, capability_client, monkeypatch):
        import src.api.benchmark as benchmark_api

        seen = {}

        def spy(task_type="", min_samples=5):
            seen["task_type"] = task_type
            seen["min_samples"] = min_samples
            return [{"dimension": "sentinel", "name": "s", "avg_score": 0.0, "sample_count": 1}]

        monkeypatch.setattr(benchmark_api.capability_evaluator, "get_weak_dimensions", spy)
        resp = capability_client.get(
            "/api/benchmark/capability/weak-dimensions",
            params={"task_type": "code", "min_samples": 3},
        )
        assert resp.status_code == 200
        assert resp.json()["weak_dimensions"][0]["dimension"] == "sentinel"
        assert seen == {"task_type": "code", "min_samples": 3}

    def test_write_face_isolated_to_tmp(self, capability_client, tmp_path):
        # 数据面真落 tmp（哨兵：生产 evaluations.db 绝不被写）
        _record_via_api(capability_client, accuracy=0.1)
        with sqlite3.connect(str(tmp_path / "evaluations.db")) as conn:
            rows = conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
        assert rows == 1
        assert str(tmp_path) in capability_client.app.state.capability_db

    def test_sibling_endpoints_unchanged(self, capability_client):
        resp = capability_client.get("/api/benchmark/capability/report")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {
            "total_evaluations",
            "avg_score",
            "recent_avg_score",
            "dimensions",
        }
        resp = capability_client.get("/api/benchmark/capability/trends")
        assert resp.status_code == 200
        assert set(resp.json().keys()) == {"trend"}
