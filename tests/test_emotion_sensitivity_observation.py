"""F4b-㉛ get_emotion_sensitivity 观测口接线测试。

修前（HEAD grep 实证）：get_emotion_sensitivity 全仓零消费（wired-but-not-functional
观测口，F4b-㉒ 诚实边界⑤留档"另拍可复核"），且 adjust_decision 与 accessor 两处
EMOTION_WEIGHTS.get 重复查找=两套查找漂移面。

收口两面：
①单一查找源——adjust_decision 权重取数改走 self.get_emotion_sensitivity
  （行为字节零变化，但取数口唯一；override accessor 决策行为随之变=真消费硬证，
  修前读裸 dict 则 override 无效，控制组必炸）
②观测口接线——GET /api/mind/emotion/sensitivity（与 /emotion/keywords 同族），
  未收录情绪回落 neutral 如实报 matched/resolved 不冒充。
"""

from typing import Literal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.mind.emotion_decision import EMOTION_WEIGHTS, EmotionDrivenDecision
from src.models.cognitive import Decision, Intent, RiskAssessment


@pytest.fixture()
def mind_client(tmp_path, monkeypatch):
    """进程内真实 mind router（test_tenant_binding mind_client 同款先例）。

    刻意不走 conftest client（那是 live :8090 HTTP 客户端）：本测需可 monkeypatch
    单例 + 控制组（旧代码 worktree）如实回 404，与部署状态解耦。
    PreferenceLearner 默认落 ~/.hermes/opensoul/mind——HOME 换 tmp 免写生产。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    import src.api.mind as mind_api

    app = FastAPI()
    app.include_router(mind_api.router, prefix="/api/mind")
    with TestClient(app) as c:
        yield c


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


# ── ① 单一查找源接线（Gate2 行为硬证：adjust_decision 真消费 accessor）─────


class TestSingleLookupSource:
    def test_adjust_decision_consumes_accessor_tighten(self):
        """override accessor 给 'joy' 恐惧权重 → 低信心收紧真触发。

        若 adjust_decision 读裸 EMOTION_WEIGHTS（修前），joy tolerance 1.2
        不满足收紧条件 → 恒 auto，本断言必炸=真消费硬证。"""

        class Override(EmotionDrivenDecision):
            def get_emotion_sensitivity(self, user_emotion: str) -> dict:
                return {"risk_tolerance": 0.6, "confirm_threshold": 0.3, "backup_urge": 1.5}

        out = Override().adjust_decision(_decision(), user_emotion="joy", agent_confidence=0.3)
        assert out.execute_mode == "confirm_required"

    def test_adjust_decision_consumes_accessor_relax(self):
        """override accessor 给 'fear' 喜悦权重 → 低信心收紧真不触发。

        若读裸 dict（修前），fear tolerance 0.6<0.8 + conf 0.3<0.5 → 收紧
        confirm_required，本断言必炸。"""

        class Override(EmotionDrivenDecision):
            def get_emotion_sensitivity(self, user_emotion: str) -> dict:
                return {"risk_tolerance": 1.2, "confirm_threshold": 0.7, "backup_urge": 0.8}

        out = Override().adjust_decision(_decision(), user_emotion="fear", agent_confidence=0.3)
        assert out.execute_mode == "auto"


# ── ② accessor 契约（不变面：查表+neutral 回落语义锁死）────────────────


class TestAccessorContract:
    def test_known_emotions_exact_table_values(self):
        """7 情绪逐个 == 表值（单一源一致性：accessor 读的就是决策用的表）。"""
        engine = EmotionDrivenDecision()
        for name, weights in EMOTION_WEIGHTS.items():
            assert engine.get_emotion_sensitivity(name) == weights

    def test_unknown_emotion_falls_back_neutral(self):
        """未收录情绪（F4b-㉒ 既有语义锁死）回落 neutral 权重。"""
        for emo in ("anticipation", "confusion", "瞎写值", ""):
            assert (
                EmotionDrivenDecision().get_emotion_sensitivity(emo) == EMOTION_WEIGHTS["neutral"]
            )


# ── ③ adjust_decision 行为零变化（不变面：接线前后语义逐值一致）──────────


class TestBehaviorUnchanged:
    def test_fear_low_confidence_still_tightens(self):
        out = EmotionDrivenDecision().adjust_decision(
            _decision(), user_emotion="fear", agent_confidence=0.3
        )
        assert out.execute_mode == "confirm_required"
        assert out.confirm_prompt is not None and "信心不足" in out.confirm_prompt

    def test_joy_high_confidence_still_auto(self):
        out = EmotionDrivenDecision().adjust_decision(
            _decision(), user_emotion="joy", agent_confidence=0.9
        )
        assert out.execute_mode == "auto"
        assert out.confirm_prompt is None

    def test_deny_floor_unchanged(self):
        d = _decision(risk_level="critical", execute_mode="deny", confirm_prompt="风险过高：x")
        out = EmotionDrivenDecision().adjust_decision(d, user_emotion="anger", agent_confidence=0.2)
        assert out.execute_mode == "deny"
        assert out.risk.recommendation == "正常执行"  # deny 不注入备份建议


# ── ④ 观测口 API（修前 404=控制组必炸）────────────────────────────────


class TestSensitivityEndpoint:
    def test_full_table_shape(self, mind_client: TestClient):
        resp = mind_client.get("/api/mind/emotion/sensitivity")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"emotions"}
        assert set(data["emotions"].keys()) == set(EMOTION_WEIGHTS.keys())
        for name, weights in data["emotions"].items():
            assert set(weights.keys()) == {"risk_tolerance", "confirm_threshold", "backup_urge"}
            assert weights == EMOTION_WEIGHTS[name]

    def test_single_matched(self, mind_client: TestClient):
        resp = mind_client.get("/api/mind/emotion/sensitivity", params={"emotion": "fear"})
        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "emotion": "fear",
            "matched": True,
            "resolved": "fear",
            "weights": EMOTION_WEIGHTS["fear"],
        }

    def test_single_unmatched_honest_fallback(self, mind_client: TestClient):
        """未收录情绪回落 neutral 但如实报 matched/resolved，禁冒充真值。"""
        resp = mind_client.get("/api/mind/emotion/sensitivity", params={"emotion": "anticipation"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched"] is False
        assert data["resolved"] == "neutral"
        assert data["weights"] == EMOTION_WEIGHTS["neutral"]

    def test_endpoint_consumes_accessor(self, mind_client: TestClient, monkeypatch):
        """观测口真消费 accessor（Gate2 行为硬证）：override accessor 响应随之变。

        若端点直读裸 EMOTION_WEIGHTS（第二套查找），override 无效本断言必炸。"""
        from src.api import mind

        sentinel = {"risk_tolerance": 0.6, "confirm_threshold": 0.3, "backup_urge": 1.5}
        monkeypatch.setattr(mind.emotion_decision, "get_emotion_sensitivity", lambda emo: sentinel)
        resp = mind_client.get("/api/mind/emotion/sensitivity", params={"emotion": "joy"})
        assert resp.status_code == 200
        assert resp.json()["weights"] == sentinel

    def test_keywords_face_regression(self, mind_client: TestClient):
        """同族观测口 /emotion/keywords 零回归。"""
        resp = mind_client.get("/api/mind/emotion/keywords")
        assert resp.status_code == 200
        assert "emotions" in resp.json()
