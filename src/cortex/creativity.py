"""Creativity Engine — 创造力引擎。

Agent能：
- 生成多个设计方案供选择
- 跨领域联想（从不同领域借鉴方案）
- 约束突破（在限制条件下找到创新解法）
- 组合创新（把已有方案重新组合）
"""

import json
import logging
import time
from typing import Optional

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


class CreativityEngine:
    """创造力引擎：生成创新方案"""

    def __init__(self, base_url: str = "", api_key: str = "", model: str = ""):
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model

    async def generate_alternatives(
        self, problem: str, constraints: list[str] = None, count: int = 3
    ) -> list[dict]:
        """生成多个替代方案"""
        if not self.api_key:
            return [{"approach": "无LLM配置", "description": "需要配置LLM API"}]

        constraint_text = ""
        if constraints:
            constraint_text = "\n约束条件：\n" + "\n".join(f"- {c}" for c in constraints)

        prompt = f"""你是一个创新方案生成器。针对以下问题，生成{count}个不同的解决方案。

问题：{problem}{constraint_text}

返回JSON数组，每个元素：
- approach: 方案名称（简短）
- description: 方案描述（50-100字）
- pros: 优点列表
- cons: 缺点列表
- novelty: 新颖度（1-5，5最创新）
- feasibility: 可行性（1-5，5最可行）

只返回JSON数组，不要其他文字。"""

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": "你是创新方案生成器，擅长跨领域思考。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.8,  # 高温度鼓励创新
                        "max_tokens": 2048,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```"):
                content = content[content.index("\n") + 1 :]
            if content.endswith("```"):
                content = content[:-3]
            return json.loads(content.strip())
        except Exception as e:
            logger.error("创造力引擎调用失败: %s", e)
            return [{"approach": "错误", "description": str(e)}]

    async def cross_domain_idea(self, domain: str, problem: str) -> dict:
        """跨领域联想：从其他领域借鉴方案"""
        prompt = f"""从"{domain}"领域之外的3个不同领域，借鉴解决类似问题的方案。

问题：{problem}

返回JSON：
- ideas: 数组，每个包含 domain(借鉴领域), approach(方案), analogy(类比说明)
- best_idea: 最有潜力的方案名称

只返回JSON。"""

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": "你是跨领域创新顾问，擅长从不同领域借鉴灵感。",
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.9,
                        "max_tokens": 1024,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```"):
                content = content[content.index("\n") + 1 :]
            if content.endswith("```"):
                content = content[:-3]
            return json.loads(content.strip())
        except Exception as e:
            logger.error("跨领域联想失败: %s", e)
            return {"ideas": [], "error": str(e)}


# ── G1-③a 消费面载荷/渲染纯函数（任务执行门禁面消费 generate_alternatives 产出） ──

SENTINEL_APPROACHES = frozenset({"错误", "无LLM配置"})


def alternatives_payload(raw) -> dict:
    """把 generate_alternatives 原始产出归一为消费面载荷。

    方法故障契约返回哨兵项（approach 为"错误"/"无LLM配置"，description=真因）——
    消费面把哨兵如实归入 error 键（为什么没有备选方案），禁当真方案织入文案
    （fabricate）。非 dict 条目/缺 approach 条目如实跳过不硬造；真项原样保留
    （approach/description/pros/cons/novelty/feasibility 上游没发不硬造）。
    """
    items: list[dict] = []
    error = None
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            approach = str(entry.get("approach") or "").strip()
            if approach in SENTINEL_APPROACHES:
                error = str(entry.get("description") or approach)
                continue
            if not approach:
                continue
            items.append(dict(entry))
    else:
        error = "产出形态非法: " + type(raw).__name__
    payload: dict = {"count": len(items), "items": items}
    if error:
        payload["error"] = error
    return payload


def render_alternatives(items: list) -> str:
    """备选方案渲染（人机裁决面文案）：空=空串零织入（无痕可撤）；最多 3 条。

    纯函数只改返回串；approach/description 原样展示（缺失段如实略，不硬造）。
    """
    if not items:
        return ""
    lines = ["", "", "💡 备选方案（创造力回流，供改写任务计划参考）："]
    for i, it in enumerate(items[:3], 1):
        approach = str((it or {}).get("approach") or "").strip()
        desc = str((it or {}).get("description") or "").strip()
        if desc:
            lines.append(f"{i}. {approach}：{desc}")
        else:
            lines.append(f"{i}. {approach}")
    return "\n".join(lines)


def render_alternatives_for_prompt(items: list) -> str:
    """备选方案 prompt 侧渲染（G1-③b-1 规划面软建议块）：空=空串零织入（无痕可撤）。

    与 render_alternatives（人机裁决面文案）同族但载体不同——本块织进 LLM
    prompt，自带「仅供参考、以目标要求为准、可全不采纳」免责（F2b-② format_
    advisory_for_prompt 软建议纪律）。最多 3 条；approach/description 原样展示
    （缺失段如实略，不硬造）。纯函数只改返回串。
    """
    if not items:
        return ""
    lines = ["可选思路（创造力回流参考；仅供参考，以目标要求为准，可全不采纳）："]
    for i, it in enumerate(items[:3], 1):
        approach = str((it or {}).get("approach") or "").strip()
        desc = str((it or {}).get("description") or "").strip()
        if desc:
            lines.append(f"{i}. {approach}：{desc}")
        else:
            lines.append(f"{i}. {approach}")
    return "\n".join(lines)


async def creative_alternatives_for(
    problem: str, constraints: list | None = None, count: int = 3
) -> tuple[dict, str]:
    """创造力回流取数口单一源（G1-③b-1）：generate_alternatives → (载荷, 裁决文案)。

    任务执行门禁面（src/api/ai_groups.py::_creative_alternatives_for 委托本口）
    与规划面（TaskPlanner/cortex /plan）共用同一取数路径，禁两套装配漂移。
    返回 (载荷, 人机裁决面文案)：载荷=count/items/(error)（alternatives_payload
    归一——方法层故障哨兵如实归 error 键禁当真方案），文案=render_alternatives
    输出（空=零织入）。方法层既有契约：LLM 故障不抛异常（哨兵返回），取数口
    随之永不因 LLM 故障抛出。
    """
    raw = await CreativityEngine().generate_alternatives(
        problem, list(constraints) if constraints else None, count
    )
    payload = alternatives_payload(raw)
    return payload, render_alternatives(payload.get("items") or [])
