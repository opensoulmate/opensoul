"""H1-②b-③ pending 复盘查询面（TradingAgents get_pending_entries 出口）测试。

覆盖：行为面（升序复盘顺序/resolved 不列/agent_id·domain 过滤/空表形状）、
回填闭环（盘点→按 decision_id 回填→离开 pending）、
接线面（端点真委托 get_pending_entries 禁重实现）、
不变面（既有 /decisions 系契约零变化）。

隔离纪律（TL-②/H1-②b-① 先例）：真实 router 挂进程内 app + tmp decision_log.db
显式替换 api.hippo 模块单例，绝不写生产 decision_log.db。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.hippo as hippo_api
import src.hippo.decision_log as dl_mod
from src.hippo.decision_log import DecisionLog


@pytest.fixture()
def log(tmp_path, monkeypatch):
    inst = DecisionLog(db_path=str(tmp_path / "decision_log.db"), max_resolved=500)
    monkeypatch.setattr(hippo_api, "_decision_log", inst)
    return inst


@pytest.fixture()
def clock(monkeypatch):
    """确定性时钟：created_at 严格递增，FIFO 序断言不靠真实时间分辨率。"""
    t = {"now": 1000.0}
    monkeypatch.setattr(dl_mod.time, "time", lambda: t["now"])

    def tick(step: float = 1.0):
        t["now"] += step

    return tick


@pytest.fixture()
def client(log):
    app = FastAPI()
    app.include_router(hippo_api.router, prefix="/api/hippo")
    return TestClient(app)


def _store(client, decision, domain="dom", agent_id="agent"):
    r = client.post(
        "/api/hippo/decisions",
        json={"decision": decision, "domain": domain, "agent_id": agent_id},
    )
    assert r.status_code == 200
    return r.json()


class TestPendingFace:
    def test_pending_fifo_ascending_and_resolved_excluded(self, client, clock):
        a = _store(client, "decision A")
        clock()
        b = _store(client, "decision B")
        clock()
        c = _store(client, "decision C")
        # 中间一条回填 resolved——升序复盘序跳过它，前后两条保序
        client.post(
            f"/api/hippo/decisions/{b['decision_id']}/outcome",
            json={"outcome": "ok", "success": True},
        )
        r = client.get("/api/hippo/decisions/pending")
        assert r.status_code == 200
        body = r.json()
        assert [e["decision_id"] for e in body["entries"]] == [
            a["decision_id"],
            c["decision_id"],
        ]
        assert body["count"] == 2
        assert all(e["status"] == "pending" for e in body["entries"])

    def test_agent_id_filter(self, client, clock):
        mine = _store(client, "mine", agent_id="chat")
        _store(client, "theirs", agent_id="other")
        r = client.get("/api/hippo/decisions/pending", params={"agent_id": "chat"})
        body = r.json()
        assert [e["decision_id"] for e in body["entries"]] == [mine["decision_id"]]
        assert body["agent_id"] == "chat"

    def test_domain_filter(self, client, clock):
        hit = _store(client, "in domain", domain="llm_routing")
        _store(client, "out of domain", domain="tool_choice")
        r = client.get("/api/hippo/decisions/pending", params={"domain": "llm_routing"})
        body = r.json()
        assert [e["decision_id"] for e in body["entries"]] == [hit["decision_id"]]
        assert body["domain"] == "llm_routing"

    def test_empty_shape_exact_keys(self, client):
        r = client.get("/api/hippo/decisions/pending")
        body = r.json()
        assert body == {"entries": [], "count": 0, "domain": "", "agent_id": ""}
        assert set(body.keys()) == {"entries", "count", "domain", "agent_id"}

    def test_entry_row_shape(self, client):
        e = _store(client, "shape probe", agent_id="chat")
        r = client.get("/api/hippo/decisions/pending")
        row = r.json()["entries"][0]
        assert row["decision_id"] == e["decision_id"]
        assert row["decision_id"].startswith("dec_")
        assert row["status"] == "pending"
        assert row["agent_id"] == "chat"
        assert row["decision"] == "shape probe"

    def test_backfill_loop_closed(self, client, clock):
        """盘点出待回填项→按 decision_id 回填→离开 pending=复盘闭环真闭环。"""
        keep = _store(client, "keep pending")
        clock()
        done = _store(client, "to resolve")
        listed = client.get("/api/hippo/decisions/pending").json()
        assert listed["count"] == 2
        r = client.post(
            f"/api/hippo/decisions/{done['decision_id']}/outcome",
            json={"outcome": "resolved by review", "success": True},
        )
        assert r.status_code == 200
        after = client.get("/api/hippo/decisions/pending").json()
        assert [e["decision_id"] for e in after["entries"]] == [keep["decision_id"]]
        resolved = client.get("/api/hippo/decisions", params={"status": "resolved"}).json()
        assert [e["decision_id"] for e in resolved["decisions"]] == [done["decision_id"]]


class TestPendingWiring:
    def test_endpoint_delegates_to_get_pending_entries(self, client, monkeypatch):
        """端点真委托 get_pending_entries（全仓唯一真消费点）——禁重实现第二套查询。"""
        calls = []
        sentinel = [{"decision_id": "dec_x", "status": "pending"}]

        class Spy:
            def get_pending_entries(self, domain="", agent_id=""):
                calls.append({"domain": domain, "agent_id": agent_id})
                return sentinel

        monkeypatch.setattr(hippo_api, "_decision_log", Spy())
        r = client.get(
            "/api/hippo/decisions/pending",
            params={"domain": "llm_routing", "agent_id": "chat"},
        )
        assert r.status_code == 200
        assert r.json()["entries"] == sentinel
        assert calls == [{"domain": "llm_routing", "agent_id": "chat"}]

    def test_write_face_roundtrip_tmp(self, client, log):
        """POST /decisions 写入 tmp 库→pending 面真读到同一行（写读同源）。"""
        e = _store(client, "roundtrip")
        rows = log.get_pending_entries()
        assert [x["decision_id"] for x in rows] == [e["decision_id"]]
        body = client.get("/api/hippo/decisions/pending").json()
        assert [x["decision_id"] for x in body["entries"]] == [e["decision_id"]]


class TestUnchangedFaces:
    def test_list_decisions_contract_unchanged(self, client, clock):
        a = _store(client, "older")
        clock()
        b = _store(client, "newer")
        r = client.get("/api/hippo/decisions")
        body = r.json()
        assert set(body.keys()) == {"decisions", "count"}
        # 既有 list_decisions=降序（最新在前）——与 pending 升序差异面锁死
        assert [e["decision_id"] for e in body["decisions"]] == [
            b["decision_id"],
            a["decision_id"],
        ]

    def test_status_filter_pending_still_works(self, client):
        _store(client, "via legacy list")
        r = client.get("/api/hippo/decisions", params={"status": "pending"})
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_outcome_444_contract_unchanged(self, client):
        r = client.post("/api/hippo/decisions/dec_missing/outcome", json={"outcome": "x"})
        assert r.status_code == 444

    def test_stats_contract_unchanged(self, client):
        _store(client, "for stats")
        r = client.get("/api/hippo/decisions/stats")
        assert r.status_code == 200
        assert "success_rate" in r.json()
