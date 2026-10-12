"""F4b-㉒ emotion_decision 接线测试：mind 情感驱动决策（EmotionDrivenDecision.adjust_decision）
从"全仓零调用点（wired-but-not-functional）"接入 brain think 决策面。

测的是 src/mind/emotion_decision.py::adjust_decision 真实语义（deny 风险底线/收紧必带问句/
放宽清问句/备份建议同源）+ src/api/brain.py::think 真实决策面（HTTP 全链，agent_confidence
声明字段真消费）。数据面零依赖（MemoryStore 内存、RiskAssessor 无 repo_root 纯规则）。
"""

import asyncio
from typing import Literal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.mind.emotion_decision import EmotionDrivenDecision
from src.models.cognitive import Decision, Intent, Risk, RiskAssessment


def _decision(
    risk_level: Literal["low", "medium", "high", "critical"] = "low",
    execute_mode: Literal["auto", "confirm_required", "deny"] = "auto",
    confirm_prompt: str | None = None,
    recommendation: str = "正常执行",
) -> Decision:
    intent = Intent(
        user_prompt="p",
        target_files=[],
        modify_scope="single_file",
        goal="fix_bug",
        change_size="small",
    )
    risk = RiskAssessment(risks=[], overall_level=risk_level, recommendation=recommendation)
    return Decision(
        intent=intent,
        risk=risk,
        edit_mode="patch",
        execute_mode=execute_mode,
        confirm_prompt=confirm_prompt,
    )


# ── ① adjust_decision 语义面 ─────────────────────────────────────────


class TestAdjustDecisionSemantics:
    def test_deny_is_floor_not_relaxed_by_negative_emotion(self):
        """deny 风险底线：critical deny + 愤怒 + 低信心不得被放宽成 confirm_required
        （修前规则二对 high/critical 无条件置 confirm_required=把 deny 降级=放宽门禁）。"""
        d = _decision(
            risk_level="critical",
            execute_mode="deny",
            confirm_prompt="风险过高：风险过高，建议人工审查后执行",
            recommendation="风险过高，建议人工审查后执行",
        )
        out = EmotionDrivenDecision().adjust_decision(d, user_emotion="anger", agent_confidence=0.2)
        assert out.execute_mode == "deny"
        assert out.confirm_prompt == "风险过高：风险过高，建议人工审查后执行"
        # deny 不执行，"先备份再执行"是错误建议不注入
        assert out.risk.recommendation == "风险过高，建议人工审查后执行"

    def test_low_confidence_negative_emotion_tightens_low_risk(self):
        """低信心+负面情绪+低风险 → auto 收紧成 confirm_required，问句含信心不足+备份建议
        （备份建议先行注入，问句/建议同源）。"""
        d = _decision(risk_level="low", execute_mode="auto")
        out = EmotionDrivenDecision().adjust_decision(d, user_emotion="fear", agent_confidence=0.3)
        assert out.execute_mode == "confirm_required"
        assert out.confirm_prompt is not None
        assert "信心不足" in out.confirm_prompt
        assert "建议先备份再执行" in out.confirm_prompt
        assert out.risk.recommendation.startswith("建议先备份再执行。")

    def test_prompt_syncs_with_backup_advice(self):
        """既有问句内嵌 recommendation：备份建议注入后问句同步（不漏不旧）。
        修前备份建议在问句构造之后注入=问句拿旧建议。"""
        rec = "目标含不可逆破坏性操作（删除/清空/格式化等），建议人工确认后执行"
        d = _decision(
            risk_level="high",
            execute_mode="confirm_required",
            confirm_prompt=f"需要确认：{rec}",
            recommendation=rec,
        )
        out = EmotionDrivenDecision().adjust_decision(
            d, user_emotion="sadness", agent_confidence=0.8
        )
        assert out.execute_mode == "confirm_required"
        assert out.confirm_prompt == f"需要确认：建议先备份再执行。{rec}"
        assert out.risk.recommendation == f"建议先备份再执行。{rec}"

    def test_escalation_without_prompt_gets_question(self):
        """收紧到 confirm_required 必带问句（修前规则二收紧不给问句=门禁问了空话）。"""
        d = _decision(
            risk_level="high",
            execute_mode="auto",
            confirm_prompt=None,
            recommendation="建议：先备份，增量编辑，改完验证",
        )
        out = EmotionDrivenDecision().adjust_decision(d, user_emotion="anger", agent_confidence=0.8)
        assert out.execute_mode == "confirm_required"
        assert out.confirm_prompt is not None
        assert "需要确认" in out.confirm_prompt

    def test_positive_high_confidence_relaxes_to_auto_and_clears_prompt(self):
        """正面情绪+高信心+低风险放宽到 auto 时清空问句（修前放宽后残留"需要确认"问句=
        决策自相矛盾）。"""
        d = _decision(
            risk_level="low",
            execute_mode="confirm_required",
            confirm_prompt="需要确认：正常执行",
            recommendation="正常执行",
        )
        out = EmotionDrivenDecision().adjust_decision(d, user_emotion="joy", agent_confidence=0.9)
        assert out.execute_mode == "auto"
        assert out.confirm_prompt is None

    def test_neutral_decision_unchanged(self):
        """中性情绪+缺省信心=零改动不变面（auto 不动、confirm_required 问句/建议原样）。"""
        d1 = _decision(risk_level="low", execute_mode="auto")
        out1 = EmotionDrivenDecision().adjust_decision(d1)
        assert (out1.execute_mode, out1.confirm_prompt, out1.risk.recommendation) == (
            "auto",
            None,
            "正常执行",
        )

        d2 = _decision(
            risk_level="medium",
            execute_mode="confirm_required",
            confirm_prompt="需要确认：建议：增量编辑，改完检查",
            recommendation="建议：增量编辑，改完检查",
        )
        out2 = EmotionDrivenDecision().adjust_decision(
            d2, user_emotion="neutral", agent_confidence=0.8
        )
        assert out2.execute_mode == "confirm_required"
        assert out2.confirm_prompt == "需要确认：建议：增量编辑，改完检查"
        assert out2.risk.recommendation == "建议：增量编辑，改完检查"

    def test_unknown_emotion_falls_back_neutral_weights(self):
        """EMOTION_WEIGHTS 未收录情绪（anticipation/confusion/瞎写值）回落 neutral 权重。"""
        for emo in ("anticipation", "confusion", "not-an-emotion"):
            d = _decision(risk_level="low", execute_mode="auto")
            out = EmotionDrivenDecision().adjust_decision(d, user_emotion=emo, agent_confidence=0.3)
            assert out.execute_mode == "auto", emo
            assert out.confirm_prompt is None, emo
            assert out.risk.recommendation == "正常执行", emo


# ── ② brain think 决策面（HTTP 全链）─────────────────────────────────


class TestThinkEmotionWiring:
    @pytest.fixture()
    def brain_client(self):
        import src.api.brain as brain_api

        app = FastAPI()
        app.include_router(brain_api.router, prefix="/api/brain")
        with TestClient(app) as c:
            yield c

    def test_think_low_confidence_negative_emotion_tightens_gate(self, brain_client):
        """情绪轴端到端：fear+低信心+低风险 → execute_mode auto→confirm_required；
        同输入缺省信心（0.8）对照回 auto（情绪不越权收紧）。"""
        body = {"user_input": "担心这个改动有危险，帮我修复 foo.py 的 bug", "agent_confidence": 0.3}
        resp = brain_client.post("/api/brain/think", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["emotion"]["primary"] == "fear"  # 情绪轴真驱动（非随机）
        assert data["risk"]["overall_level"] == "low"
        assert data["decision"]["execute_mode"] == "confirm_required"
        assert "信心不足" in data["decision"]["confirm_prompt"]
        assert "建议先备份再执行" in data["decision"]["confirm_prompt"]

        resp2 = brain_client.post("/api/brain/think", json={"user_input": body["user_input"]})
        data2 = resp2.json()
        assert data2["decision"]["execute_mode"] == "auto"
        assert data2["decision"]["confirm_prompt"] is None

    def test_think_deny_floor_survives_negative_emotion(self, brain_client, monkeypatch):
        """think 全链 deny 底线：critical 风险 deny + 负面情绪 + 低信心仍 deny（接线不放宽门禁）。"""
        import src.cortex.risk_assessor as ra

        async def fake_assess(self, intent, task_ctx):
            return RiskAssessment(
                risks=[Risk("核心文件修改", "critical", "y")],
                overall_level="critical",
                recommendation="风险过高，建议人工审查后执行",
            )

        monkeypatch.setattr(ra.RiskAssessor, "assess", fake_assess)
        resp = brain_client.post(
            "/api/brain/think",
            json={"user_input": "担心出事，永久删除生产数据", "agent_confidence": 0.2},
        )
        data = resp.json()
        assert data["decision"]["execute_mode"] == "deny"
        assert data["decision"]["confirm_prompt"].startswith("风险过高：")

    def test_think_negative_emotion_backup_advice_in_prompt(self, brain_client):
        """破坏性意图（high→confirm_required）+ 负面情绪：备份建议注入且问句同源。"""
        resp = brain_client.post(
            "/api/brain/think", json={"user_input": "担心风险，永久删除生产数据"}
        )
        data = resp.json()
        assert data["risk"]["overall_level"] == "high"
        assert data["decision"]["execute_mode"] == "confirm_required"
        assert data["decision"]["confirm_prompt"].startswith("需要确认：建议先备份再执行。")
        assert data["risk"]["recommendation"].startswith("建议先备份再执行。")

    def test_agent_confidence_out_of_range_422(self, brain_client):
        """agent_confidence 越界如实 422 拒（禁静默当缺省）。"""
        for bad in (1.5, -0.1):
            resp = brain_client.post(
                "/api/brain/think",
                json={"user_input": "hi", "agent_confidence": bad},
            )
            assert resp.status_code == 422, bad

    def test_a1_inprocess_direct_call_compat(self):
        """A1 接线回归硬证：进程内直调 think(ThinkRequest(...)) 不带 authorization 照旧可用，
        响应 decision 契约键不变、agent_confidence 缺省 0.8。"""
        from src.api.brain import ThinkRequest, think

        req = ThinkRequest(user_input="帮我修复一个 bug", session_id="t1")
        # F4b-㉕ 契约随更新：未声明=None（派生哨兵），生效信心仍回落 0.8 缺省
        assert req.agent_confidence is None
        result = asyncio.run(think(req))
        assert set(result["decision"]) == {"edit_mode", "execute_mode", "confirm_prompt"}
        assert result["agent_confidence"] == 0.8
        # 未声明→派生（无账本回落 0.8 信心充足）+ 中性情绪 + 低风险 → auto
        # （决策面过 adjust 后契约稳定零误伤）
        assert result["decision"]["execute_mode"] == "auto"
        assert result["decision"]["confirm_prompt"] is None
