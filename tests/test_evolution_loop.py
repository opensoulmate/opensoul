"""P0-7 自进化闭环测试 — EvolutionEngine 声明→审批→落盘→回滚→记账全链路。

调研来源对照：
- LobeChat declareSelfFeedbackIntent：声明≠执行
- claude-code ProposeSkills：无evidence直接拒绝
- agno：审批人≠发起人 + 错误风暴熔断
- CowAgent：保护区硬护栏 + 预算上限 + 回滚
- kilocode：防记忆回声（同digest已applied不再产生新进化）
- agent-zero：稀疏锚点编辑（绝不整文件重写）+ 改动前快照
"""

import sqlite3
import time
from pathlib import Path

import pytest

from src.heredity.evolution_loop import (
    PROPOSAL_KINDS,
    EvolutionEngine,
    is_protected_target,
)


@pytest.fixture
def engine(tmp_path):
    return EvolutionEngine(db_path=str(tmp_path / "evolution.db"))


@pytest.fixture
def target_file(tmp_path):
    f = tmp_path / "prompt_strategy.md"
    f.write_text("# Strategy\nmodel = fast\n# end\n", encoding="utf-8")
    return f


def _declare(engine, title="increase retries", **kw):
    defaults = dict(
        kind="policy_adjustment",
        title=title,
        rationale="observed repeated failures",
        confidence=0.6,
        evidence_refs=["trajectory:sess_abc"],
        proposed_change={},
        proposer="agent",
    )
    defaults.update(kw)
    return engine.declare_intent(**defaults)


# ── ① 声明（LobeChat范式：只声明不动手） ─────────────────────


class TestDeclare:
    def test_declare_creates_pending(self, engine):
        result = _declare(engine)
        assert result["status"] == "pending"
        assert result["proposal_id"].startswith("evo_")
        assert result["evidence_refs"] == ["trajectory:sess_abc"]
        assert result["duplicate"] is False
        ledger = engine.store.get_ledger(proposal_id=result["proposal_id"])
        assert [e["event"] for e in ledger] == ["DECLARED"]
        assert ledger[0]["actor"] == "agent"

    def test_declare_invalid_kind_raises(self, engine):
        with pytest.raises(ValueError, match="invalid kind"):
            _declare(engine, kind="nonsense_kind")

    def test_declare_confidence_out_of_range_raises(self, engine):
        with pytest.raises(ValueError, match="confidence"):
            _declare(engine, confidence=1.5)
        with pytest.raises(ValueError, match="confidence"):
            _declare(engine, confidence=-0.1)

    def test_declare_empty_title_raises(self, engine):
        with pytest.raises(ValueError, match="title"):
            _declare(engine, title="   ")

    def test_declare_without_evidence_rejected(self, engine):
        """claude-code ProposeSkills：无evidence的提议拒绝，但必须可见（不静默丢弃）。"""
        result = _declare(engine, evidence_refs=[])
        assert result["status"] == "rejected"
        assert result["reject_reason"] == "evidence_required"
        ledger = engine.store.get_ledger(proposal_id=result["proposal_id"])
        events = [e["event"] for e in ledger]
        assert events == ["REJECTED"]  # 拒绝也必须可见（mem0失败不静默）

    def test_declare_protected_target_rejected(self, engine):
        """CowAgent硬护栏：目标在保护区的提案自动拒绝，审批也不可放行。"""
        result = _declare(
            engine,
            proposed_change={
                "target": "/opt/opensoul/src/immune/moderator.py",
                "anchor_old": "a",
                "anchor_new": "b",
            },
        )
        assert result["status"] == "rejected"
        assert result["reject_reason"].startswith("protected_target")

    def test_declare_duplicate_pending_returns_existing(self, engine):
        first = _declare(engine, title="Same Title")
        second = _declare(engine, title="same  title")  # 归一化后同digest
        assert second["duplicate"] is True
        assert second["proposal_id"] == first["proposal_id"]
        pending = engine.store.list_proposals(status="pending")
        assert len(pending) == 1

    def test_memory_echo_rejected(self, engine):
        """kilocode防记忆回声：同digest已applied → 新声明拒绝并指向原提案。"""
        tmp = Path(engine.store.db_path).parent / "target.md"
        tmp.write_text("alpha beta gamma\n", encoding="utf-8")
        first = _declare(
            engine,
            title="echo test",
            proposed_change={"target": str(tmp), "anchor_old": "beta", "anchor_new": "BETA"},
        )
        engine.review(first["proposal_id"], "approve", reviewer="user")
        applied = engine.apply(first["proposal_id"], actor="user")
        assert applied["status"] == "applied"

        again = _declare(engine, title="Echo Test")
        assert again["status"] == "rejected"
        assert again["reject_reason"] == "memory_echo"
        assert again["duplicate_of"] == first["proposal_id"]

    def test_budget_exceeded(self, tmp_path):
        engine = EvolutionEngine(db_path=str(tmp_path / "e.db"), max_pending=2)
        _declare(engine, title="p1")
        _declare(engine, title="p2")
        result = _declare(engine, title="p3")
        assert result["status"] == "rejected"
        assert "budget_exceeded" in result["reject_reason"]

    def test_kind_taxonomy_matches_research(self):
        """kind覆盖调研报告中的进化类型（含failure_avoidance）。"""
        assert "failure_avoidance" in PROPOSAL_KINDS
        assert "skill_improvement" in PROPOSAL_KINDS
        assert "policy_adjustment" in PROPOSAL_KINDS


# ── ② 审批（agno：审批人≠发起人） ────────────────────────────


class TestReview:
    def test_approve_by_different_reviewer(self, engine):
        p = _declare(engine)
        result = engine.review(p["proposal_id"], "approve", reviewer="user")
        assert result["status"] == "approved"
        assert result["reviewed_by"] == "user"
        ledger = engine.store.get_ledger(proposal_id=p["proposal_id"])
        assert [e["event"] for e in ledger] == ["DECLARED", "APPROVED"]

    def test_reviewer_must_not_be_proposer(self, engine):
        p = _declare(engine, proposer="agent")
        with pytest.raises(ValueError, match="reviewer_is_proposer"):
            engine.review(p["proposal_id"], "approve", reviewer="agent")

    def test_reject_by_reviewer(self, engine):
        p = _declare(engine)
        result = engine.review(p["proposal_id"], "reject", reviewer="user", comment="not now")
        assert result["status"] == "rejected"
        assert result["reject_reason"] == "not now"

    def test_review_non_pending_raises(self, engine):
        p = _declare(engine)
        engine.review(p["proposal_id"], "approve", reviewer="user")
        with pytest.raises(ValueError, match="pending"):
            engine.review(p["proposal_id"], "reject", reviewer="user2")

    def test_review_not_found_raises(self, engine):
        with pytest.raises(ValueError, match="not found"):
            engine.review("evo_nonexistent", "approve", reviewer="user")

    def test_review_invalid_decision_raises(self, engine):
        p = _declare(engine)
        with pytest.raises(ValueError, match="invalid decision"):
            engine.review(p["proposal_id"], "maybe", reviewer="user")


# ── ③ 落盘（agent-zero稀疏编辑+快照） ────────────────────────


class TestApply:
    def test_apply_approved_sparse_edit_with_snapshot(self, engine, target_file):
        p = _declare(
            engine,
            proposed_change={
                "target": str(target_file),
                "anchor_old": "model = fast",
                "anchor_new": "model = careful",
            },
        )
        engine.review(p["proposal_id"], "approve", reviewer="user")
        result = engine.apply(p["proposal_id"], actor="user")
        assert result["status"] == "applied"
        content = target_file.read_text(encoding="utf-8")
        assert "model = careful" in content
        assert "# Strategy" in content  # 其余部分原样保留
        snap = Path(result["snapshot_path"])
        assert snap.exists()
        assert snap.read_text(encoding="utf-8") == "# Strategy\nmodel = fast\n# end\n"
        ledger = engine.store.get_ledger(proposal_id=p["proposal_id"])
        assert [e["event"] for e in ledger] == ["DECLARED", "APPROVED", "APPLIED"]

    def test_apply_without_approval_raises(self, engine, target_file):
        p = _declare(
            engine,
            proposed_change={"target": str(target_file), "anchor_old": "x", "anchor_new": "y"},
        )
        with pytest.raises(ValueError, match="only approved"):
            engine.apply(p["proposal_id"])

    def test_apply_anchor_not_found_leaves_file_untouched(self, engine, target_file):
        p = _declare(
            engine,
            proposed_change={
                "target": str(target_file),
                "anchor_old": "nonexistent-anchor",
                "anchor_new": "new",
            },
        )
        engine.review(p["proposal_id"], "approve", reviewer="user")
        result = engine.apply(p["proposal_id"], actor="user")
        assert result["status"] == "apply_failed"
        assert "anchor_not_found" in result["reject_reason"]
        assert target_file.read_text(encoding="utf-8") == "# Strategy\nmodel = fast\n# end\n"

    def test_apply_target_missing(self, engine, tmp_path):
        p = _declare(
            engine,
            proposed_change={
                "target": str(tmp_path / "nope.md"),
                "anchor_old": "a",
                "anchor_new": "b",
            },
        )
        engine.review(p["proposal_id"], "approve", reviewer="user")
        result = engine.apply(p["proposal_id"], actor="user")
        assert result["status"] == "apply_failed"
        assert "target_missing" in result["reject_reason"]

    def test_apply_invalid_change_missing_anchor(self, engine, target_file):
        p = _declare(
            engine,
            proposed_change={"target": str(target_file)},  # 缺anchor_old
        )
        engine.review(p["proposal_id"], "approve", reviewer="user")
        result = engine.apply(p["proposal_id"], actor="user")
        assert result["status"] == "apply_failed"
        assert "invalid_change" in result["reject_reason"]

    def test_apply_fail_closed_on_tampered_protected_target(self, engine, tmp_path):
        """即使DB里被篡改为approved+保护区目标，apply时仍拒绝（fail-closed二次校验）。"""
        protected = tmp_path / "src" / "immune" / "moderator.py"
        protected.parent.mkdir(parents=True)
        protected.write_text("keep me\n", encoding="utf-8")
        p = _declare(
            engine,
            title="tamper attempt",
            proposed_change={
                "target": str(protected),
                "anchor_old": "keep me",
                "anchor_new": "pwned",
            },
        )
        # 绕过declare的护栏？不行——declare已拒绝；直接篡改DB模拟攻击
        assert p["status"] == "rejected"
        with sqlite3.connect(engine.store.db_path) as conn:
            conn.execute(
                "UPDATE evolution_proposals SET status='approved', reject_reason='' WHERE proposal_id=?",
                (p["proposal_id"],),
            )
        result = engine.apply(p["proposal_id"], actor="attacker")
        assert result["status"] == "apply_failed"
        assert "protected_target" in result["reject_reason"]
        assert protected.read_text(encoding="utf-8") == "keep me\n"

    def test_is_protected_target_matrix(self):
        assert is_protected_target("src/immune/moderator.py")
        assert is_protected_target("/any/prefix/data/opensoul.db")
        assert is_protected_target("~/.ssh/id_rsa")
        assert is_protected_target(".env")
        assert is_protected_target("/etc/app/.env.local")  # ".env"子串命中，fail-closed
        assert is_protected_target("/etc/certs/server.pem")
        assert is_protected_target("src/heredity/evolution_loop.py")
        assert not is_protected_target("skills/my_new_skill.md")
        assert not is_protected_target("docs/dev-reports.md")
        assert is_protected_target("")  # 空目标fail-closed


# ── ④ 回滚（CowAgent无效结果回滚） ───────────────────────────


class TestRollback:
    def test_rollback_restores_snapshot(self, engine, target_file):
        original = target_file.read_text(encoding="utf-8")
        p = _declare(
            engine,
            proposed_change={
                "target": str(target_file),
                "anchor_old": "model = fast",
                "anchor_new": "model = careful",
            },
        )
        engine.review(p["proposal_id"], "approve", reviewer="user")
        engine.apply(p["proposal_id"], actor="user")
        assert "model = careful" in target_file.read_text(encoding="utf-8")

        result = engine.rollback(p["proposal_id"], actor="user", reason="regression detected")
        assert result["status"] == "rolled_back"
        assert target_file.read_text(encoding="utf-8") == original
        ledger = engine.store.get_ledger(proposal_id=p["proposal_id"])
        assert [e["event"] for e in ledger] == ["DECLARED", "APPROVED", "APPLIED", "ROLLED_BACK"]
        assert ledger[-1]["reason"] == "regression detected"

    def test_rollback_non_applied_raises(self, engine):
        p = _declare(engine)
        engine.review(p["proposal_id"], "approve", reviewer="user")
        with pytest.raises(ValueError, match="only applied"):
            engine.rollback(p["proposal_id"])

    def test_rollback_missing_snapshot_raises(self, engine, target_file):
        p = _declare(
            engine,
            proposed_change={
                "target": str(target_file),
                "anchor_old": "model = fast",
                "anchor_new": "model = careful",
            },
        )
        engine.review(p["proposal_id"], "approve", reviewer="user")
        engine.apply(p["proposal_id"], actor="user")
        # 删除快照模拟丢失
        result = engine.store.get_proposal(p["proposal_id"])
        Path(result.snapshot_path).unlink()
        with pytest.raises(ValueError, match="snapshot missing"):
            engine.rollback(p["proposal_id"])


# ── ⑤ 触发器（CowAgent idle + agno错误风暴熔断） ─────────────


class TestTriggers:
    def test_no_fire_below_threshold(self, engine):
        result = engine.evaluate_triggers(
            idle_seconds=10.0, context_pressure=0.3, budget_remaining=1.0
        )
        assert result["fired"] == []
        assert engine.store.count_triggers() == 0

    def test_idle_trigger_fires(self, engine):
        result = engine.evaluate_triggers(
            idle_seconds=120.0, context_pressure=0.9, budget_remaining=0.5
        )
        assert result["fired"] == ["idle_evolution"]
        assert len(result["trigger_ids"]) == 1
        assert engine.store.count_triggers() == 1

    def test_idle_without_pressure_does_not_fire(self, engine):
        """CowAgent条件是合取：idle够但context压力不足 → 不触发。"""
        result = engine.evaluate_triggers(
            idle_seconds=120.0, context_pressure=0.2, budget_remaining=1.0
        )
        assert result["fired"] == []

    def test_idle_without_budget_does_not_fire(self, engine):
        result = engine.evaluate_triggers(
            idle_seconds=120.0, context_pressure=0.9, budget_remaining=0.0
        )
        assert result["fired"] == []

    def test_error_pattern_trigger_below_storm(self, engine):
        result = engine.evaluate_triggers(recent_errors=["timeout", "other", "timeout"])
        assert result["fired"] == ["error_pattern"]
        assert result["breaker_open"] is False

    def test_error_storm_breaker_opens_once(self, engine):
        """agno熔断：连续同错≥storm_window → 打开熔断只记一条，绝不记N条。"""
        errors = ["provider_500"] * 3
        result = engine.evaluate_triggers(recent_errors=errors)
        assert result["fired"] == ["error_storm_breaker"]
        assert result["breaker_open"] is True
        assert engine.store.count_triggers() == 1
        breaker = engine.store.get_breaker()
        assert breaker == {"open": True, "error_type": "provider_500", "consecutive": 3}

        # 再次评估同样的错误风暴 → 抑制，不新增触发记录
        result2 = engine.evaluate_triggers(recent_errors=errors)
        assert result2["fired"] == []
        assert result2["suppressed"] is True
        assert engine.store.count_triggers() == 1

    def test_breaker_resets_on_error_type_change(self, engine):
        engine.evaluate_triggers(recent_errors=["provider_500"] * 3)
        assert engine.store.get_breaker()["open"] is True
        # 错误类型变了 → 旧熔断解除
        result = engine.evaluate_triggers(recent_errors=["timeout"])
        assert result["fired"] == ["error_pattern"]
        assert engine.store.get_breaker()["open"] is False

    def test_auto_propose_creates_pending_proposal(self, engine):
        result = engine.evaluate_triggers(
            idle_seconds=300.0,
            context_pressure=0.95,
            budget_remaining=1.0,
            auto_propose=True,
        )
        assert result["fired"] == ["idle_evolution"]
        proposals = result["auto_proposals"]
        assert len(proposals) == 1
        assert proposals[0]["status"] == "pending"
        assert proposals[0]["proposer"] == "auto_trigger"
        assert proposals[0]["evidence_refs"][0].startswith("trigger:")
        # 触发器行回链提案id
        with sqlite3.connect(engine.store.db_path) as conn:
            row = conn.execute("SELECT proposal_id FROM evolution_triggers").fetchone()
        assert row[0] == proposals[0]["proposal_id"]

    def test_ledger_records_trigger_events(self, engine):
        engine.evaluate_triggers(idle_seconds=300.0, context_pressure=0.95, budget_remaining=1.0)
        ledger = engine.store.get_ledger()
        assert any(e["event"] == "TRIGGERED" for e in ledger)


# ── ⑥ 可观测性（用户痛点"我都不知道他们在干嘛"） ────────────


class TestStats:
    def test_stats_shape(self, engine):
        p1 = _declare(engine, title="stat one")
        _declare(engine, title="stat two", evidence_refs=[])  # rejected
        engine.review(p1["proposal_id"], "approve", reviewer="user")
        stats = engine.get_stats()
        assert stats["proposals"]["pending"] >= 0
        assert stats["proposals"]["approved"] == 1
        assert stats["proposals"]["rejected"] == 1
        assert stats["max_pending"] == 20
        assert stats["pending_budget_remaining"] == 20 - stats["pending"]
        assert "DECLARED" in stats["ledger_events"]
        assert stats["breaker"]["open"] is False

    def test_list_and_get_proposal(self, engine):
        p = _declare(engine, title="inspect me")
        listed = engine.store.list_proposals(status="pending")
        assert any(x["proposal_id"] == p["proposal_id"] for x in listed)
        got = engine.store.get_proposal(p["proposal_id"])
        assert got.title == "inspect me"
        assert got.evidence_refs == ["trajectory:sess_abc"]


# ── ⑦ API层（heredity_client隔离面；live :8090仅存一条只读冒烟） ──────


class TestEvolutionAPI:
    """进化API端到端接线验证（conftest heredity_client隔离面：真实router+tmp数据面）。

    2026-10-06收编：此前全部走live :8090，每次全量跑把测试提案/审批/触发器写穿
    生产evolution.db（86条api指纹假提案含43条approved+136条假触发器）。写面全迁
    heredity_client；test_health_includes_evolution_stats留守live只读GET冒烟
    （部署证明：真实服务面router挂载不受影响，零写入）。
    """

    def _declare_via_api(self, hc, title):
        return hc.post(
            "/api/heredity/evolution/intents",
            json={
                "kind": "policy_adjustment",
                "title": title,
                "rationale": "api test",
                "confidence": 0.5,
                "evidence_refs": ["api-test:evolution-loop"],
                "proposer": "api_test_agent",
            },
        )

    def test_health_includes_evolution_stats(self, client):
        """live只读冒烟（唯一live用例）：GET health零写入，验证真实服务面挂载。"""
        resp = client.get("/api/heredity/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "evolution" in data
        assert "proposals" in data["evolution"]
        assert "breaker" in data["evolution"]

    def test_declare_review_flow_via_api(self, heredity_client):
        import uuid as _uuid

        title = f"api flow test {_uuid.uuid4().hex[:8]}"
        resp = self._declare_via_api(heredity_client, title)
        assert resp.status_code == 200
        proposal = resp.json()
        pid = proposal["proposal_id"]
        assert proposal["status"] == "pending"

        # 同proposer审批 → 400（审批人≠发起人）
        resp = heredity_client.post(
            f"/api/heredity/evolution/proposals/{pid}/review",
            json={"decision": "approve", "reviewer": "api_test_agent"},
        )
        assert resp.status_code == 400
        assert "reviewer_is_proposer" in resp.json()["detail"]

        # 不同审批人 → 通过
        resp = heredity_client.post(
            f"/api/heredity/evolution/proposals/{pid}/review",
            json={"decision": "approve", "reviewer": "human_reviewer"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

        # 提案详情带审计轨迹
        resp = heredity_client.get(f"/api/heredity/evolution/proposals/{pid}")
        assert resp.status_code == 200
        history = resp.json()["history"]
        assert [e["event"] for e in history] == ["DECLARED", "APPROVED"]

    def test_apply_and_rollback_via_api(self, heredity_client, tmp_path):
        import uuid as _uuid

        target = tmp_path / f"api_target_{_uuid.uuid4().hex[:6]}.md"
        target.write_text("param = old_value\n", encoding="utf-8")
        resp = heredity_client.post(
            "/api/heredity/evolution/intents",
            json={
                "kind": "policy_adjustment",
                "title": f"api apply test {_uuid.uuid4().hex[:8]}",
                "confidence": 0.7,
                "evidence_refs": ["api-test:apply"],
                "proposed_change": {
                    "target": str(target),
                    "anchor_old": "param = old_value",
                    "anchor_new": "param = new_value",
                },
                "proposer": "api_apply_agent",
            },
        )
        assert resp.status_code == 200
        pid = resp.json()["proposal_id"]

        resp = heredity_client.post(
            f"/api/heredity/evolution/proposals/{pid}/review",
            json={"decision": "approve", "reviewer": "human_reviewer"},
        )
        assert resp.status_code == 200

        resp = heredity_client.post(
            f"/api/heredity/evolution/proposals/{pid}/apply",
            json={"actor": "human_reviewer"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"
        assert target.read_text(encoding="utf-8") == "param = new_value\n"

        resp = heredity_client.post(
            f"/api/heredity/evolution/proposals/{pid}/rollback",
            json={"actor": "human_reviewer", "reason": "api test rollback"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rolled_back"
        assert target.read_text(encoding="utf-8") == "param = old_value\n"

    def test_evidence_required_via_api(self, heredity_client):
        resp = heredity_client.post(
            "/api/heredity/evolution/intents",
            json={"kind": "skill_new", "title": "no evidence proposal"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert data["reject_reason"] == "evidence_required"

    def test_invalid_kind_returns_400(self, heredity_client):
        resp = heredity_client.post(
            "/api/heredity/evolution/intents",
            json={"kind": "bad_kind", "title": "x", "evidence_refs": ["e"]},
        )
        assert resp.status_code == 400

    def test_triggers_endpoint_breaker(self, heredity_client):
        resp = heredity_client.post(
            "/api/heredity/evolution/triggers/evaluate",
            json={"recent_errors": ["api_storm_err"] * 3},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["breaker_open"] is True
        # 熔断打开后同样错误不再刷触发记录
        resp2 = heredity_client.post(
            "/api/heredity/evolution/triggers/evaluate",
            json={"recent_errors": ["api_storm_err"] * 3},
        )
        assert resp2.json()["suppressed"] is True
        # 换错误类型 → 熔断解除路径
        resp3 = heredity_client.post(
            "/api/heredity/evolution/triggers/evaluate",
            json={"recent_errors": ["different_err"]},
        )
        assert resp3.json()["fired"] == ["error_pattern"]
        assert resp3.json()["breaker_open"] is False

    def test_brain_evolve_feeds_pipeline(self, heredity_client):
        """写了≠接线了：/api/brain/evolve 必须把真实发现喂进进化管线（共享单例）。

        种子3条同根因失败经验→SelfEvolution.failure_avoidance发现→经brain/evolve
        声明进heredity共享单例（src/api/brain.py late import），提案落tmp管线。
        非只验key存在：断言发现确实变成声明提案且status=pending。
        """
        import sqlite3 as _sqlite3
        import time as _time

        from src.learn.experience_collector import ExperienceCollector

        tmp_db = heredity_client.app.state.heredity_client_opensoul_db
        with _sqlite3.connect(tmp_db) as conn:
            ExperienceCollector(db_path=tmp_db).ensure_schema(conn)
            for i in range(3):
                conn.execute(
                    "INSERT INTO experiences (tenant_id, agent_id, action,"
                    " intent_summary, outcome, error, relevance_score, created_at,"
                    " metadata, source_ref) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        "default",
                        "default",
                        "seeded action",
                        "seeded intent",
                        "failure",
                        "seeded isolation failure: boom",
                        1.0,
                        _time.time(),
                        "{}",
                        f"seed:iso:{i}",
                    ),
                )
            conn.commit()

        resp = heredity_client.post("/api/brain/evolve")
        assert resp.status_code == 200
        data = resp.json()
        assert "declared_proposals" in data
        assert "pipeline" in data
        assert "proposals" in data["pipeline"]
        # 真实行为钉：种子失败模式必须变成声明提案
        assert len(data["declared_proposals"]) >= 1, data
        assert data["declared_proposals"][0]["status"] == "pending"
        # 接线钉：提案落tmp进化库（共享单例可换数据面），不是死响应
        evodb = heredity_client.app.state.heredity_client_evolution_db
        with _sqlite3.connect(evodb) as conn:
            n = conn.execute("SELECT COUNT(*) FROM evolution_proposals").fetchone()[0]
        assert n >= 1

    def test_ledger_endpoint(self, heredity_client):
        resp = heredity_client.get("/api/heredity/evolution/ledger?limit=10")
        assert resp.status_code == 200
        assert "ledger" in resp.json()

    def test_evolution_stats_endpoint(self, heredity_client):
        resp = heredity_client.get("/api/heredity/evolution/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "proposals" in data
        assert "ledger_events" in data


# ── ⑧ 预算自愈回归（实盘教训：20/20 pending卡死→budget_exceeded永久拒绝一切声明） ──


class TestDigestVolatileNormalization:
    def test_count_reading_collapses(self, engine):
        """同一问题的失败计数读数(31次)/(310次)必须同digest→去重生效。"""
        a = _declare(engine, title="高频失败: No handler for job type 'test_job' (31次)")
        b = _declare(engine, title="高频失败: no handler for job type 'test_job' (310次)")
        assert b["duplicate"] is True
        assert b["proposal_id"] == a["proposal_id"]

    def test_rate_reading_collapses(self, engine):
        """成功率趋势读数"32% vs 90%"/"50% vs 93%"是同一问题→同digest。"""
        a = _declare(engine, title="切换到更保守的策略: 成功率下降: 32% vs 90%")
        b = _declare(engine, title="切换到更保守的策略: 成功率下降: 50% vs 93%")
        assert b["duplicate"] is True
        assert b["proposal_id"] == a["proposal_id"]

    def test_distinct_titles_stay_distinct(self, engine):
        a = _declare(engine, title="增加tool_calls前置检查")
        b = _declare(engine, title="增加session前置检查")
        assert b["duplicate"] is False
        assert a["proposal_id"] != b["proposal_id"]

    def test_plain_percentage_change_not_collapsed(self):
        """保守归一化：只折叠"(N次)"读数与"N% vs N%"趋势对，普通百分比改动保持区分度。"""
        d1 = EvolutionEngine._digest("policy_adjustment", "把阈值从32%调到40%")
        d2 = EvolutionEngine._digest("policy_adjustment", "把阈值从50%调到60%")
        assert d1 != d2


class TestStalePendingSweep:
    def test_sweep_retires_stale_and_keeps_fresh(self, engine):
        stale = _declare(engine, title="old junk")
        fresh = _declare(engine, title="new item")
        engine.store.update_proposal(stale["proposal_id"], created_at=time.time() - 3 * 86400)
        retired = engine.store.retire_stale_pending(engine.pending_ttl_s)
        assert retired == [stale["proposal_id"]]
        got = engine.store.get_proposal(stale["proposal_id"])
        assert got.status == "rejected"
        assert got.reject_reason == "stale_pending_expired"
        assert engine.store.get_proposal(fresh["proposal_id"]).status == "pending"
        events = [e["event"] for e in engine.store.get_ledger(proposal_id=stale["proposal_id"])]
        assert events == ["DECLARED", "REJECTED"]  # 退役必须账本可见（mem0失败不静默）

    def test_budget_self_heals_on_declare(self, tmp_path):
        """实盘回归：预算被超龄pending占死→新声明时惰性清扫先退役，预算自愈。"""
        engine = EvolutionEngine(db_path=str(tmp_path / "e.db"), max_pending=2)
        for t in ("junk1", "junk2"):
            r = _declare(engine, title=t)
            engine.store.update_proposal(r["proposal_id"], created_at=time.time() - 3 * 86400)
        result = _declare(engine, title="fresh idea")
        assert result["status"] == "pending"
        assert result["reject_reason"] == ""
        assert engine.store.count_by_status("pending") == 1  # junk已退役，只余新单

    def test_budget_still_blocks_fresh_saturation(self, tmp_path):
        """预算语义保留：全是新单时饱和仍然拦截（防测试把budget上限改没了）。"""
        engine = EvolutionEngine(db_path=str(tmp_path / "e.db"), max_pending=2)
        _declare(engine, title="f1")
        _declare(engine, title="f2")
        result = _declare(engine, title="f3")
        assert result["status"] == "rejected"
        assert "budget_exceeded" in result["reject_reason"]

    def test_count_stale_pending(self, engine):
        p = _declare(engine, title="aging")
        assert engine.store.count_stale_pending(engine.pending_ttl_s) == 0
        engine.store.update_proposal(p["proposal_id"], created_at=time.time() - 3 * 86400)
        assert engine.store.count_stale_pending(engine.pending_ttl_s) == 1

    def test_stats_expose_sweep_observability(self, engine):
        stats = engine.get_stats()
        assert stats["pending_stale"] == 0
        assert stats["pending_ttl_s"] > 0
        assert stats["redeclare_cooldown_s"] > 0


class TestDecidedEcho:
    def test_reviewer_rejected_not_reproposed(self, engine):
        """kilocode防回声推广：人类已裁决拒绝的问题，冷却期内不重复建单。"""
        first = _declare(engine, title="增加前置检查")
        engine.review(first["proposal_id"], "reject", "human_reviewer", comment="not now")
        again = _declare(engine, title="增加前置检查")
        assert again["duplicate"] is True
        assert again["proposal_id"] == first["proposal_id"]
        assert engine.store.count_by_status("pending") == 0  # 不重复建单

    def test_rolled_back_not_reproposed(self, engine):
        """试过并回滚=最强的'别再提'信号，冷却期内不重复建单。"""
        tmp = Path(engine.store.db_path).parent / "rb.md"
        tmp.write_text("old\n", encoding="utf-8")
        first = _declare(
            engine,
            title="改配置rb",
            proposed_change={"target": str(tmp), "anchor_old": "old", "anchor_new": "new"},
        )
        engine.review(first["proposal_id"], "approve", "human_reviewer")
        engine.apply(first["proposal_id"], actor="human_reviewer")
        engine.rollback(first["proposal_id"], actor="human_reviewer", reason="bad idea")
        again = _declare(engine, title="改配置rb")
        assert again["duplicate"] is True
        assert again["proposal_id"] == first["proposal_id"]

    def test_guard_reject_stays_reproposable(self, engine):
        """guard瞬态拒绝（evidence_required等无reviewer）不算裁决，补证据后必须可重提。"""
        first = _declare(engine, title="increase retries", evidence_refs=[])
        assert first["status"] == "rejected"
        again = _declare(
            engine,
            title="increase retries",
            evidence_refs=["trajectory:sess_new"],
        )
        assert again["duplicate"] is False
        assert again["status"] == "pending"

    def test_cooldown_expiry_allows_redeclare(self, engine):
        first = _declare(engine, title="增加前置检查")
        engine.review(first["proposal_id"], "reject", "human_reviewer")
        engine.store.update_proposal(first["proposal_id"], reviewed_at=time.time() - 8 * 86400)
        again = _declare(engine, title="增加前置检查")
        assert again["duplicate"] is False
        assert again["proposal_id"] != first["proposal_id"]
