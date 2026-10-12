"""Risk Assessor — 理解式风险评估；支持多文件风险聚合。"""

import logging

from src.cortex.project_memory import ProjectMemory
from src.models.cognitive import Intent, Risk, RiskAssessment, TaskContext

logger = logging.getLogger(__name__)

# F4b-⑳ 意图级风险词：破坏性/不可逆操作意图（字面匹配非语义理解，阈值如实可调；
# 窄集防误伤日常开发语（"删除注释"不触发），宁可漏报不滥报——触发后只是问一次人）
_DESTRUCTIVE_KEYWORDS = (
    "清空",
    "格式化",
    "删库",
    "清库",
    "销毁",
    "永久删除",
    "不可逆",
    "rm -rf",
    "drop table",
    "drop database",
    "truncate",
    "删除生产",
    "重置生产",
    "覆盖生产",
    "清空生产",
    "delete from",
)


class RiskAssessor:
    """理解式风险评估器"""

    def __init__(self, project_memory: ProjectMemory, experience_memory=None):
        self.project = project_memory
        self.experience = experience_memory

    async def assess(self, intent: Intent, task_ctx: TaskContext) -> RiskAssessment:
        all_risks: list[Risk] = []

        for file_path in intent.target_files:
            all_risks.extend(self._assess_single_file(file_path, intent))

        if len(intent.target_files) > 3:
            all_risks.append(
                Risk("多文件改动", "medium", f"同时修改{len(intent.target_files)}个文件")
            )

        if self.experience:
            # F4b-㉚ 故障可见不阻断（F2b-②c-1 纪律）：历史失败经验是增强轴，读取
            # 故障跳过该轴但风险评估照常（静默吞=盲区，logger 留真因可见）。
            try:
                similar_failures = await self.experience.get_similar_failures(intent)
            except Exception as e:
                logger.warning("历史失败经验读取失败（不阻断风险评估）: %s", e)
                similar_failures = []
            if similar_failures:
                level = "high" if len(similar_failures) >= 3 else "medium"
                all_risks.append(
                    Risk("历史失败经验", level, f"类似操作曾失败{len(similar_failures)}次")
                )

        # F4b-⑳ 意图级风险（确认门禁触发面）：破坏性/不可逆操作意图无文件轴也须可见。
        # 修前 risk 只看文件/经验——无 repo_root 的调用面（ai_groups 执行面 think 恒不带）
        # target_files 恒空→overall 恒 low→confirm_required 构造上不可达=确认门禁永不触发。
        prompt_text = f"{intent.user_prompt} {getattr(task_ctx, 'user_input', '') or ''}"
        if any(w in prompt_text for w in _DESTRUCTIVE_KEYWORDS):
            all_risks.append(
                Risk("破坏性操作意图", "high", "含删除/清空/格式化等不可逆操作，建议人工确认后执行")
            )

        overall = self._aggregate(all_risks)
        recommendation = self._recommend(all_risks)
        return RiskAssessment(risks=all_risks, overall_level=overall, recommendation=recommendation)

    def _assess_single_file(self, path: str, intent: Intent) -> list[Risk]:
        risks = []
        if self.project.is_core_file(path):
            risks.append(Risk("核心文件修改", "high", f"{path}是项目核心文件"))
        impact = self.project.get_impact(path)
        if impact.risk_level in ("high", "critical"):
            risks.append(
                Risk(
                    f"影响{len(impact.direct_impact)}个文件",
                    impact.risk_level,
                    f"直接依赖：{', '.join(impact.direct_impact[:5])}",
                )
            )
        if intent.change_size == "large":
            risks.append(Risk("大规模改动", "medium", "建议分步执行"))
        return risks

    def _aggregate(self, risks: list[Risk]) -> str:
        if not risks:
            return "low"
        levels = [r.level for r in risks]
        if "critical" in levels:
            return "critical"
        if levels.count("high") >= 2:
            return "critical"
        if "high" in levels:
            return "high"
        if levels.count("medium") >= 2:
            return "high"
        if "medium" in levels:
            return "medium"
        return "low"

    def _recommend(self, risks: list[Risk]) -> str:
        if any(r.title == "破坏性操作意图" for r in risks):
            return "目标含不可逆破坏性操作（删除/清空/格式化等），建议人工确认后执行"
        if any(r.level == "critical" for r in risks):
            return "风险过高，建议人工审查后执行"
        if any(r.level == "high" for r in risks):
            return "建议：先备份，增量编辑，改完验证"
        if any(r.level == "medium" for r in risks):
            return "建议：增量编辑，改完检查"
        return "正常执行"
