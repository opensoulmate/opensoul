"""Emotion-Driven Decision — 情感驱动决策。

情绪不只是识别，还影响决策权重：
- 用户焦虑时 → 更保守，多确认
- 用户兴奋时 → 更大胆，少确认
- 用户愤怒时 → 更谨慎，避免出错
- Agent自身信心低时 → 更保守，多备份
"""

import logging

from src.models.cognitive import Decision, RiskAssessment

logger = logging.getLogger(__name__)

# 情绪→决策权重映射
EMOTION_WEIGHTS = {
    "joy": {"risk_tolerance": 1.2, "confirm_threshold": 0.7, "backup_urge": 0.8},
    "trust": {"risk_tolerance": 1.1, "confirm_threshold": 0.6, "backup_urge": 0.9},
    "surprise": {"risk_tolerance": 0.9, "confirm_threshold": 0.5, "backup_urge": 1.0},
    "sadness": {"risk_tolerance": 0.8, "confirm_threshold": 0.4, "backup_urge": 1.2},
    "anger": {"risk_tolerance": 0.7, "confirm_threshold": 0.3, "backup_urge": 1.3},
    "fear": {"risk_tolerance": 0.6, "confirm_threshold": 0.3, "backup_urge": 1.5},
    "neutral": {"risk_tolerance": 1.0, "confirm_threshold": 0.5, "backup_urge": 1.0},
}


class EmotionDrivenDecision:
    """情感驱动决策：情绪影响决策权重"""

    def __init__(self):
        pass

    def adjust_decision(
        self, decision: Decision, user_emotion: str = "neutral", agent_confidence: float = 0.8
    ) -> Decision:
        """根据情绪调整决策。

        语义收口（F4b-㉒ 接线前定形——修前本模块全仓零消费方，无行为影响面）：
        - deny 是风险底线（风险等级给出）：已 deny 的决策不再执行，情绪调整不适用；
          情绪只可收紧门禁或在 auto/confirm 之间按正向语义放宽，不得放宽 deny
          （修前规则二会把 critical 风险的 deny 降成 confirm_required=放宽门禁，方向反了）
        - 收紧到 confirm_required 必带 confirm_prompt（消费方门禁拿问句问人，空问句=问了空话）
        - 放宽到 auto 清空 confirm_prompt（决策自洽：auto 不带"需要确认"问句）
        - 备份建议先行注入 recommendation 并同步既有问句（问句/建议同源不漏不旧）
        """
        # deny 风险底线：情绪不得放宽 deny，已 deny 的决策不注入"先备份再执行"（不执行）
        if decision.execute_mode == "deny":
            return decision

        # 权重取数单一源（F4b-㉛）：决策面与观测口（get_emotion_sensitivity→
        # /api/mind/emotion/sensitivity）共用同一取数口，禁两套查找漂移
        emotion_weight = self.get_emotion_sensitivity(user_emotion)

        # 风险等级调整
        risk_level = decision.risk.overall_level
        adjusted_execute = decision.execute_mode

        # 备份建议（负面情绪 → 更倾向备份）——先于问句规则注入，问句/建议同源
        if emotion_weight["backup_urge"] > 1.0:
            old_rec = decision.risk.recommendation
            decision.risk.recommendation = f"建议先备份再执行。{old_rec}"
            if decision.confirm_prompt and old_rec in decision.confirm_prompt:
                decision.confirm_prompt = decision.confirm_prompt.replace(
                    old_rec, decision.risk.recommendation
                )

        # 低信心 + 负面情绪 → 更保守
        if agent_confidence < 0.5 and emotion_weight["risk_tolerance"] < 0.8:
            if risk_level in ("low", "medium"):
                adjusted_execute = "confirm_required"
                decision.confirm_prompt = (
                    f"⚠️ 信心不足({agent_confidence:.0%})，需要确认：{decision.risk.recommendation}"
                )

        # 高风险 + 负面情绪 → 必须确认
        if risk_level in ("high", "critical") and emotion_weight["risk_tolerance"] < 0.8:
            adjusted_execute = "confirm_required"

        # 高信心 + 正面情绪 + 低风险 → 可以自动执行
        if (
            agent_confidence > 0.8
            and emotion_weight["risk_tolerance"] > 1.0
            and risk_level == "low"
        ):
            adjusted_execute = "auto"

        # 决策自洽：收紧必带问句、放宽到 auto 不留问句
        if adjusted_execute == "confirm_required" and not decision.confirm_prompt:
            decision.confirm_prompt = f"需要确认：{decision.risk.recommendation}"
        elif adjusted_execute == "auto":
            decision.confirm_prompt = None

        if adjusted_execute != decision.execute_mode:
            logger.info(
                "情感调整: emotion=%s, confidence=%.1f, %s → %s",
                user_emotion,
                agent_confidence,
                decision.execute_mode,
                adjusted_execute,
            )
            decision.execute_mode = adjusted_execute

        return decision

    def get_emotion_sensitivity(self, user_emotion: str) -> dict:
        """获取情绪敏感度（权重取数单一源，F4b-㉛）。

        决策面（adjust_decision）与观测面（/api/mind/emotion/sensitivity）共用
        此取数口：EMOTION_WEIGHTS 唯一查找点在此，缺省回落 neutral 权重。
        """
        return EMOTION_WEIGHTS.get(user_emotion, EMOTION_WEIGHTS["neutral"])
