"""P0-6决策延迟回填（TradingAgents模式）测试。

覆盖：store/update核心语义、幂等、审计、as_of时间旅行过滤、同域/跨域检索预算、
轮转保pending、provider成功率聚合、route_policy反馈互换规则、chat.py写路径helpers、
API端点契约。
"""

import json

import pytest

import src.hippo.decision_log as dl_mod
from src.hippo.decision_log import DecisionLog, get_decision_log


@pytest.fixture()
def log(tmp_path, monkeypatch):
    inst = DecisionLog(db_path=str(tmp_path / "decision_log.db"), max_resolved=500)
    monkeypatch.setattr(dl_mod, "_decision_log", inst)
    return inst


# ── 核心语义：store / update_with_outcome ─────────────────────────────


class TestDecisionLogCore:
    def test_store_pending(self, log):
        entry = log.store_decision(
            decision="route to online",
            domain="llm_routing",
            agent_id="chat",
            context="q",
            metadata={"attempts": ["online"]},
        )
        assert entry["status"] == "pending"
        assert entry["decision_id"].startswith("dec_")
        assert entry["domain"] == "llm_routing"
        assert (
            log.get_pending_entries(domain="llm_routing")[0]["decision_id"] == entry["decision_id"]
        )

    def test_store_empty_decision_rejected(self, log):
        with pytest.raises(ValueError):
            log.store_decision(decision="   ")

    def test_store_dedup_pending(self, log):
        e1 = log.store_decision(decision="same decision", domain="tool_choice")
        e2 = log.store_decision(decision="same decision", domain="tool_choice")
        assert e2["decision_id"] == e1["decision_id"]
        assert e2.get("deduped") is True
        assert len(log.get_pending_entries(domain="tool_choice")) == 1

    def test_store_dedup_allows_new_after_resolve(self, log):
        e1 = log.store_decision(decision="same decision", domain="tool_choice")
        log.update_with_outcome(decision_id=e1["decision_id"], outcome="ok", success=True)
        e2 = log.store_decision(decision="same decision", domain="tool_choice")
        assert e2["decision_id"] != e1["decision_id"]
        assert e2["status"] == "pending"

    def test_update_with_outcome_resolves(self, log):
        e = log.store_decision(decision="choose A", domain="intent")
        r = log.update_with_outcome(
            decision_id=e["decision_id"],
            outcome="A worked",
            metrics={"score": 0.9},
            reflection="A因为X成功",
            success=True,
        )
        assert r["status"] == "resolved"
        assert r["outcome"] == "A worked"
        assert r["outcome_metrics"]["score"] == 0.9
        assert r["outcome_metrics"]["success"] is True
        assert r["resolution_date"]  # 缺省=今天
        assert r["resolved_at"] > 0
        assert log.get_pending_entries(domain="intent") == []

    def test_update_unknown_id_returns_none(self, log):
        assert log.update_with_outcome(decision_id="dec_nope", outcome="x") is None

    def test_update_by_domain_takes_oldest_pending(self, log):
        e1 = log.store_decision(decision="first", domain="d1")
        log.store_decision(decision="second", domain="d1")
        r = log.update_with_outcome(domain="d1", outcome="result", success=True)
        assert r["decision_id"] == e1["decision_id"]

    def test_update_resolved_is_idempotent(self, log):
        e = log.store_decision(decision="choose A", domain="intent")
        log.update_with_outcome(decision_id=e["decision_id"], outcome="first", success=True)
        r2 = log.update_with_outcome(decision_id=e["decision_id"], outcome="second", success=False)
        assert r2.get("_already_resolved") is True
        assert r2["outcome"] == "first"  # 不覆盖
        assert r2["outcome_metrics"]["success"] is True

    def test_audit_trail(self, log):
        e = log.store_decision(decision="audited", domain="audit_dom")
        log.update_with_outcome(decision_id=e["decision_id"], outcome="done", success=True)
        events = [a["event"] for a in log.get_audit(decision_id=e["decision_id"])]
        assert "STORE" in events and "RESOLVE" in events

    def test_batch_update(self, log):
        # T3 处置：batch_update_with_outcomes 便利包已删（H1-② 降级件），
        # 回填语义=逐条 update_with_outcome（单条面为唯一回填真源）。
        e1 = log.store_decision(decision="b1", domain="batch")
        e2 = log.store_decision(decision="b2", domain="batch")
        results = []
        for u in [
            {"decision_id": e1["decision_id"], "outcome": "o1", "success": True},
            {"decision_id": e2["decision_id"], "outcome": "o2", "success": False},
            {"decision_id": "dec_missing", "outcome": "x", "success": None},
        ]:
            r = log.update_with_outcome(
                decision_id=u["decision_id"], outcome=u["outcome"], success=u["success"]
            )
            results.append(r is not None)
        assert results == [True, True, False]

    def test_list_and_stats(self, log):
        log.store_decision(decision="p1", domain="dom_a")
        e2 = log.store_decision(decision="r1", domain="dom_b")
        log.update_with_outcome(decision_id=e2["decision_id"], outcome="ok", success=True)
        listed = log.list_decisions(domain="dom_a")
        assert len(log.list_decisions(status="pending", domain="dom_a")) == 1
        assert all(d["domain"] == "dom_a" for d in listed)
        stats = log.get_stats()
        assert stats["total"] == 2
        assert stats["by_status"]["pending"] == 1
        assert stats["by_status"]["resolved"] == 1
        assert stats["success_rate"] == 1.0
        with pytest.raises(ValueError):
            log.list_decisions(status="bogus")


# ── get_past_context：同域/跨域预算 + as_of时间旅行 ───────────────────


class TestPastContext:
    def _seed(self, log):
        ids = []
        for i in range(4):
            e = log.store_decision(
                decision=f"routing-decision-{i} full-text-marker",
                domain="llm_routing",
                rating="online",
            )
            log.update_with_outcome(
                decision_id=e["decision_id"],
                outcome=f"outcome-{i}",
                metrics={"success": i % 2 == 0},
                reflection=f"routing-reflection-{i}",
                resolution_date=f"2026-09-0{i + 1}",
            )
            ids.append(e["decision_id"])
        for i in range(2):
            e = log.store_decision(
                decision=f"tool-choice-{i} cross-full-text-marker",
                domain="tool_choice",
            )
            log.update_with_outcome(
                decision_id=e["decision_id"],
                outcome="cross-outcome",
                reflection=f"cross-reflection-{i}",
                resolution_date="2026-09-05",
            )
        return ids

    def test_same_domain_full_cross_reflection_only(self, log):
        self._seed(log)
        ctx = log.get_past_context(domain="llm_routing", n_same=2, n_cross=1)
        # 同域：全量格式（decision原文在场）
        assert "routing-decision-" in ctx and "full-text-marker" in ctx
        assert "REFLECTION:\nrouting-reflection-" in ctx
        # 跨域：只取反思，decision原文（full-text-marker）不得出现
        assert "cross-reflection-0" in ctx or "cross-reflection-1" in ctx
        assert "cross-full-text-marker" not in ctx

    def test_pending_never_in_context(self, log):
        log.store_decision(decision="pending-decision-marker", domain="llm_routing")
        ctx = log.get_past_context(domain="llm_routing")
        assert "pending-decision-marker" not in ctx
        assert ctx == ""  # 只有pending时上下文为空

    def test_as_of_time_travel_filter(self, log):
        self._seed(log)
        # as_of=2026-09-02：只应包含resolution_date<=2026-09-02的条目
        ctx = log.get_past_context(domain="llm_routing", as_of="2026-09-02")
        assert "routing-reflection-0" in ctx  # resolved 2026-09-01
        assert "routing-reflection-1" in ctx  # resolved 2026-09-02
        assert "routing-reflection-3" not in ctx  # resolved 2026-09-04 > as_of

    def test_as_of_excludes_missing_resolution_date(self, log):
        e = log.store_decision(decision="legacy-no-date", domain="llm_routing")
        log.update_with_outcome(decision_id=e["decision_id"], outcome="old entry", success=True)
        # 直接SQL模拟旧条目无resolution_date（迁移前存量数据）
        import sqlite3

        with sqlite3.connect(log.db_path) as conn:
            conn.execute(
                "UPDATE decision_log SET resolution_date='' WHERE decision_id=?",
                (e["decision_id"],),
            )
            conn.commit()
        ctx = log.get_past_context(domain="llm_routing", as_of="2026-12-31")
        assert "legacy-no-date" not in ctx
        ctx_no_filter = log.get_past_context(domain="llm_routing")
        assert "legacy-no-date" in ctx_no_filter  # as_of=None不影响无日期条目

    def test_token_budget_truncation(self, log):
        for i in range(10):
            e = log.store_decision(decision=f"long-decision-{i}-" + "x" * 200, domain="budget_dom")
            log.update_with_outcome(
                decision_id=e["decision_id"], outcome="y" * 200, reflection="z" * 200, success=True
            )
        ctx = log.get_past_context(domain="budget_dom", n_same=10, token_budget=200)
        assert len(ctx) <= 200 * 4 + 60  # 预算×4近似 + 截断标记
        assert ctx.endswith("...[decision context truncated]")

    def test_empty_log_returns_empty(self, log):
        assert log.get_past_context(domain="nothing") == ""


# ── 轮转：淘汰最旧resolved，pending永不淘汰 ──────────────────────────


class TestRotation:
    def test_rotation_evicts_oldest_resolved_keeps_pending(self, tmp_path, monkeypatch):
        inst = DecisionLog(db_path=str(tmp_path / "rot.db"), max_resolved=2)
        monkeypatch.setattr(dl_mod, "_decision_log", inst)
        pending = inst.store_decision(decision="never-done", domain="rot")
        import time

        for i in range(4):
            e = inst.store_decision(decision=f"resolved-{i}", domain="rot")
            time.sleep(0.01)  # 保证resolved_at有序
            inst.update_with_outcome(
                decision_id=e["decision_id"],
                outcome=f"o{i}",
                success=True,
                resolution_date=f"2026-09-0{i + 1}",
            )
        resolved = inst.list_decisions(status="resolved", domain="rot")
        assert len(resolved) <= 2
        # 最旧的被淘汰，最新保留
        kept = {d["decision"] for d in resolved}
        assert "resolved-3" in kept
        assert "resolved-0" not in kept
        # pending永不淘汰
        pend = inst.get_pending_entries(domain="rot")
        assert len(pend) == 1 and pend[0]["decision_id"] == pending["decision_id"]
        # 轮转审计可见
        events = [a["event"] for a in inst.get_audit(limit=100)]
        assert "ROTATION" in events

    def test_rotation_disabled_when_max_zero(self, tmp_path, monkeypatch):
        inst = DecisionLog(db_path=str(tmp_path / "rot0.db"), max_resolved=0)
        monkeypatch.setattr(dl_mod, "_decision_log", inst)
        import time

        for i in range(5):
            e = inst.store_decision(decision=f"r-{i}", domain="rot0")
            time.sleep(0.005)
            inst.update_with_outcome(decision_id=e["decision_id"], outcome="o", success=True)
        assert len(inst.list_decisions(status="resolved", domain="rot0")) == 5


# ── provider成功率聚合（route_policy反馈数据源） ──────────────────────


class TestProviderStats:
    def test_attempts_detail_aggregation(self, log):
        for i in range(3):
            e = log.store_decision(
                decision=f"mode=balance prefer=online primary=online:m q_len={i}",
                domain="llm_routing",
            )
            log.update_with_outcome(
                decision_id=e["decision_id"],
                outcome="failover",
                metrics={
                    "success": True,
                    "used_provider": "ollama",
                    "attempts_detail": [
                        {"provider": "online", "ok": False, "error": "402"},
                        {"provider": "ollama", "ok": True},
                    ],
                },
                success=True,
            )
        stats = log.get_provider_stats(domain="llm_routing")
        assert stats["online"]["attempts"] == 3
        assert stats["online"]["failures"] == 3
        assert stats["online"]["failure_rate"] == 1.0
        assert stats["ollama"]["attempts"] == 3
        assert stats["ollama"]["failure_rate"] == 0.0

    def test_fallback_single_provider_metrics(self, log):
        e = log.store_decision(decision="legacy-shape", domain="llm_routing")
        log.update_with_outcome(
            decision_id=e["decision_id"],
            outcome="ok",
            metrics={"success": True, "used_provider": "online"},
            success=True,
        )
        stats = log.get_provider_stats(domain="llm_routing")
        assert stats["online"]["attempts"] == 1
        assert stats["online"]["failure_rate"] == 0.0

    def test_window_and_domain_isolation(self, log):
        e = log.store_decision(decision="other-domain", domain="not_routing")
        log.update_with_outcome(
            decision_id=e["decision_id"],
            outcome="x",
            metrics={"attempts_detail": [{"provider": "p", "ok": False}]},
            success=False,
        )
        assert log.get_provider_stats(domain="llm_routing") == {}


# ── route_policy读路径①：反馈互换规则 ─────────────────────────────────


class TestRoutePolicyFeedback:
    ONLINE = {"provider": "online", "base_url": "u", "model": "m", "api_key": "k"}
    OLLAMA = {"provider": "ollama", "base_url": "u2", "model": "m2", "api_key": ""}

    def test_swap_when_prefer_failing_backup_better(self, log, monkeypatch):
        import src.gland.route_policy as rp

        # online全部失败(n=6, rate=100%)，ollama全部成功(n=6, rate=0%) → 互换
        for i in range(6):
            e = log.store_decision(decision=f"online-fail-ollama-ok-{i}", domain="llm_routing")
            log.update_with_outcome(
                decision_id=e["decision_id"],
                outcome="seed",
                metrics={
                    "attempts_detail": [
                        {"provider": "online", "ok": False},
                        {"provider": "ollama", "ok": True},
                    ]
                },
                success=True,
            )
        monkeypatch.setattr(rp, "get_mode", lambda: "balance")
        monkeypatch.setattr(rp, "online_target", lambda: dict(self.ONLINE))
        monkeypatch.setattr(rp, "local_target", lambda: dict(self.OLLAMA))
        out = rp.resolve_target("test question?")
        assert out["target"]["provider"] == "ollama"
        assert out["backup_target"]["provider"] == "online"
        assert "决策记忆反馈" in out["reason"]

    def test_intelligence_mode_never_swapped(self, log, monkeypatch):
        import src.gland.route_policy as rp

        for i in range(8):
            e = log.store_decision(decision=f"intel-fail-{i}", domain="llm_routing")
            log.update_with_outcome(
                decision_id=e["decision_id"],
                outcome="seed",
                metrics={"attempts_detail": [{"provider": "online", "ok": False}]},
                success=True,
            )
        monkeypatch.setattr(rp, "get_mode", lambda: "intelligence")
        monkeypatch.setattr(rp, "online_target", lambda: dict(self.ONLINE))
        monkeypatch.setattr(rp, "local_target", lambda: dict(self.OLLAMA))
        out = rp.resolve_target("q")
        assert out["target"]["provider"] == "online"  # 用户显式语义不被反馈覆盖
        assert "决策记忆反馈" not in out["reason"]

    def test_no_data_behavior_unchanged(self, tmp_path, monkeypatch):
        import src.gland.route_policy as rp

        fresh = DecisionLog(db_path=str(tmp_path / "empty.db"))
        monkeypatch.setattr(dl_mod, "_decision_log", fresh)
        monkeypatch.setattr(rp, "get_mode", lambda: "balance")
        monkeypatch.setattr(rp, "online_target", lambda: dict(self.ONLINE))
        monkeypatch.setattr(rp, "local_target", lambda: dict(self.OLLAMA))
        out = rp.resolve_target("q")
        assert out["target"]["provider"] == "online"  # 与接线前一致
        assert "决策记忆反馈" not in out["reason"]

    def test_backup_also_bad_no_swap(self, tmp_path, monkeypatch):
        import src.gland.route_policy as rp

        inst = DecisionLog(db_path=str(tmp_path / "bothbad.db"))
        monkeypatch.setattr(dl_mod, "_decision_log", inst)
        for i in range(6):
            e = inst.store_decision(decision=f"both-bad-{i}", domain="llm_routing")
            inst.update_with_outcome(
                decision_id=e["decision_id"],
                outcome="seed",
                metrics={
                    "attempts_detail": [
                        {"provider": "online", "ok": False},
                        {"provider": "ollama", "ok": False},
                    ]
                },
                success=True,
            )
        swapped, note = rp._decision_feedback_swap("online", dict(self.ONLINE), dict(self.OLLAMA))
        assert swapped is False  # 备选同样烂→不换

    def test_prefer_failing_but_under_threshold_no_swap(self, tmp_path, monkeypatch):
        import src.gland.route_policy as rp

        inst = DecisionLog(db_path=str(tmp_path / "mild.db"))
        monkeypatch.setattr(dl_mod, "_decision_log", inst)
        for i in range(10):
            e = inst.store_decision(decision=f"mild-{i}", domain="llm_routing")
            inst.update_with_outcome(
                decision_id=e["decision_id"],
                outcome="seed",
                metrics={
                    "attempts_detail": [
                        {"provider": "online", "ok": i >= 4},  # 4/10失败=40%<50%阈值
                        {"provider": "ollama", "ok": True},
                    ]
                },
                success=True,
            )
        swapped, _ = rp._decision_feedback_swap("online", dict(self.ONLINE), dict(self.OLLAMA))
        assert swapped is False

    def test_decision_log_broken_fail_safe(self, monkeypatch):
        import src.gland.route_policy as rp

        def _boom(db_path=""):
            raise RuntimeError("db broken")

        monkeypatch.setattr(dl_mod, "get_decision_log", _boom)
        monkeypatch.setattr(rp, "get_mode", lambda: "balance")
        monkeypatch.setattr(rp, "online_target", lambda: dict(self.ONLINE))
        monkeypatch.setattr(rp, "local_target", lambda: dict(self.OLLAMA))
        out = rp.resolve_target("q")
        assert out["target"]["provider"] == "online"  # 静默降级，行为不变


# ── chat.py写路径helpers（真实运行时记录点） ──────────────────────────


class TestChatDecisionHelpers:
    def test_begin_then_end_failover(self, log):
        from src.api import chat as chat_mod

        dec_id = chat_mod._route_decision_begin(
            "test question",
            {"mode": "balance", "prefer": "online"},
            [{"provider": "online", "model": "gpt"}, {"provider": "ollama", "model": "r1"}],
        )
        assert dec_id
        pending = log.get_pending_entries(domain="llm_routing")
        assert len(pending) == 1 and pending[0]["decision_id"] == dec_id
        assert "mode=balance" in pending[0]["decision"]

        chat_mod._route_decision_end(
            dec_id,
            [
                {"provider": "online", "ok": False, "error": "402 Payment Required"},
                {"provider": "ollama", "ok": True},
            ],
        )
        resolved = log.list_decisions(status="resolved", domain="llm_routing")
        assert len(resolved) == 1
        m = resolved[0]["outcome_metrics"]
        assert m["success"] is True and m["failover"] is True
        assert m["used_provider"] == "ollama"
        assert "failover" in resolved[0]["reflection"]
        stats = log.get_provider_stats(domain="llm_routing")
        assert stats["online"]["failures"] == 1
        assert stats["ollama"]["failure_rate"] == 0.0

    def test_end_all_failed(self, log):
        from src.api import chat as chat_mod

        dec_id = chat_mod._route_decision_begin(
            "q", {"mode": "cost", "prefer": "local"}, [{"provider": "ollama", "model": "r1"}]
        )
        chat_mod._route_decision_end(
            dec_id, [{"provider": "ollama", "ok": False, "error": "ConnectError"}]
        )
        resolved = log.list_decisions(status="resolved", domain="llm_routing")
        assert resolved[0]["outcome_metrics"]["success"] is False
        assert "全部provider失败" in resolved[0]["reflection"]

    def test_helpers_fail_safe_on_broken_db(self, monkeypatch):
        from src.api import chat as chat_mod

        def _boom(db_path=""):
            raise RuntimeError("db broken")

        monkeypatch.setattr(dl_mod, "get_decision_log", _boom)
        assert chat_mod._route_decision_begin("q", {"mode": "x"}, []) == ""
        chat_mod._route_decision_end("", [])  # 不抛异常
        chat_mod._route_decision_end("dec_x", [{"provider": "p", "ok": True}])  # 不抛异常


# ── API端点契约（src/api/hippo.py /api/hippo/decisions/*） ────────────


class TestDecisionAPI:
    @pytest.fixture()
    def client(self, log):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import src.api.hippo as hippo_api

        app = FastAPI()
        app.include_router(hippo_api.router, prefix="/api/hippo")
        return TestClient(app)

    def test_store_list_outcome_flow(self, client):
        r = client.post(
            "/api/hippo/decisions",
            json={
                "decision": "use tool X",
                "domain": "tool_choice",
                "agent_id": "test",
                "context": "task-1",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending"
        dec_id = body["decision_id"]

        r2 = client.get(
            "/api/hippo/decisions", params={"status": "pending", "domain": "tool_choice"}
        )
        assert r2.status_code == 200
        assert r2.json()["count"] == 1

        r3 = client.post(
            f"/api/hippo/decisions/{dec_id}/outcome",
            json={
                "outcome": "X succeeded",
                "metrics": {"duration_ms": 120},
                "reflection": "X适合此类任务",
                "success": True,
            },
        )
        assert r3.status_code == 200
        out = r3.json()
        assert out["status"] == "resolved"
        assert out["outcome_metrics"]["success"] is True
        assert out["resolution_date"]

        # 幂等：重复回填不覆盖
        r4 = client.post(f"/api/hippo/decisions/{dec_id}/outcome", json={"outcome": "changed"})
        assert r4.status_code == 200
        assert r4.json().get("_already_resolved") is True
        assert r4.json()["outcome"] == "X succeeded"

    def test_outcome_unknown_id_444(self, client):
        r = client.post("/api/hippo/decisions/dec_missing/outcome", json={"outcome": "x"})
        assert r.status_code == 444

    def test_store_empty_decision_422(self, client):
        r = client.post("/api/hippo/decisions", json={"decision": "  "})
        assert r.status_code == 422

    def test_store_dedup_via_api(self, client):
        p = {"decision": "same api decision", "domain": "api_dom"}
        r1 = client.post("/api/hippo/decisions", json=p)
        r2 = client.post("/api/hippo/decisions", json=p)
        assert r2.json()["decision_id"] == r1.json()["decision_id"]
        assert r2.json()["deduped"] is True

    def test_stats_context_audit_endpoints(self, client):
        r = client.post(
            "/api/hippo/decisions",
            json={
                "decision": "ctx-decision-marker",
                "domain": "ctx_dom",
                "rating": "A",
            },
        )
        dec_id = r.json()["decision_id"]
        client.post(
            f"/api/hippo/decisions/{dec_id}/outcome",
            json={
                "outcome": "ctx-outcome-marker",
                "reflection": "ctx-reflection-marker",
                "success": True,
                "resolution_date": "2026-09-10",
            },
        )

        rs = client.get("/api/hippo/decisions/stats")
        assert rs.status_code == 200
        assert rs.json()["total"] >= 1
        assert "by_domain" in rs.json() and "provider_stats_llm_routing" in rs.json()

        rc = client.get("/api/hippo/decisions/context", params={"domain": "ctx_dom"})
        assert rc.status_code == 200
        ctx = rc.json()["context"]
        assert "ctx-decision-marker" in ctx and "ctx-reflection-marker" in ctx

        # as_of时间旅行：as_of早于resolution_date→排除
        rc2 = client.get(
            "/api/hippo/decisions/context", params={"domain": "ctx_dom", "as_of": "2026-09-01"}
        )
        assert "ctx-decision-marker" not in rc2.json()["context"]

        ra = client.get("/api/hippo/decisions/audit", params={"decision_id": dec_id})
        assert ra.status_code == 200
        events = [h["event"] for h in ra.json()["history"]]
        assert "STORE" in events and "RESOLVE" in events

    def test_health_includes_decision_log(self, client):
        r = client.get("/api/hippo/health")
        assert r.status_code == 200
        assert "decision_log" in r.json()
        assert "total" in r.json()["decision_log"]


# ── 单例 ──────────────────────────────────────────────────────────────


def test_singleton_returns_same_instance(log):
    assert get_decision_log() is log
    assert get_decision_log() is get_decision_log()
