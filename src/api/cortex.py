import time as _time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.cortex.chain_of_thought import ChainOfThought
from src.cortex.creativity import creative_alternatives_for, render_alternatives_for_prompt
from src.cortex.multi_agent import MultiAgent
from src.cortex.task_planner import TaskPlanner

router = APIRouter()

# ── Usage counters ─────────────────────────────────────────
_usage = {
    "plan_calls": 0,
    "agent_calls": 0,
    "think_calls": 0,
    "total_calls": 0,
    "errors": 0,
    "last_activity": None,
}


@router.get("/health")
async def cortex_health():
    """OpenCortex health check."""
    return {
        "status": "ok",
        "component": "OpenCortex",
        "modules": {
            "task_planner": "available",
            "multi_agent": "available",
            "chain_of_thought": "available",
        },
    }


@router.get("/stats")
async def cortex_stats():
    """Get OpenCortex usage statistics."""
    return {
        "status": "ok",
        "component": "OpenCortex",
        "modules": {
            "task_planner": "available",
            "multi_agent": "available",
            "chain_of_thought": "available",
            "graphrag": "available",
            "recommendation": "available",
            "quality": "available",
        },
        "usage": _usage,
    }


class PlanRequest(BaseModel):
    goal: str


class AgentRequest(BaseModel):
    topic: str


class ThinkRequest(BaseModel):
    question: str
    context: str = ""


@router.post("/plan")
async def plan(req: PlanRequest):
    """Decompose a goal into sub-tasks.

    G1-③b-1 创造力回流规划面：generate_alternatives 产出真进规划决策——备选
    思路软建议块（render_alternatives_for_prompt，自带「以目标要求为准」免责）
    织入 TaskPlanner 规划 prompt，闭「生成新方案只出 API 不进规划面」断点；
    返回载荷加性键 alternatives（count/items/(error)，与 G1-③a 同形）。
    生成故障不阻断规划（方法层哨兵→error 键如实、advisory 空=零织入无痕可撤，
    规划照常跑）。成本契约：本口为按需规划面（用户显式触发），每次触达创造力
    引擎一次；③a「auto 执行路径零生成」契约不覆盖本面——本面即规划消费缝本身。
    """
    try:
        planner = TaskPlanner()
        advisory_payload, _ = await creative_alternatives_for(req.goal)
        advisory_text = render_alternatives_for_prompt(advisory_payload.get("items") or [])
        tasks = await planner.plan(req.goal, advisory=advisory_text)
        _usage["plan_calls"] += 1
        _usage["total_calls"] += 1
        _usage["last_activity"] = _time.time()
        return {
            "goal": req.goal,
            "tasks": [
                {
                    "index": i,
                    "description": t.description,
                    "dependencies": t.dependencies,
                    "priority": t.priority,
                }
                for i, t in enumerate(tasks)
            ],
            "alternatives": advisory_payload,
        }
    except ValueError as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=502, detail=f"Planning failed: {e}")


@router.post("/agent")
async def agent(req: AgentRequest):
    """Run the multi-agent pipeline (Researcher → Analyzer → Writer)."""
    try:
        pipeline = MultiAgent()
        result = await pipeline.run(req.topic)
        _usage["agent_calls"] += 1
        _usage["total_calls"] += 1
        _usage["last_activity"] = _time.time()
        return {"topic": req.topic, **result}
    except ValueError as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=502, detail=f"Agent pipeline failed: {e}")


@router.post("/think")
async def think(req: ThinkRequest):
    """Chain-of-thought reasoning with self-reflection."""
    try:
        cot = ChainOfThought()
        result = await cot.think(req.question, req.context)
        _usage["think_calls"] += 1
        _usage["total_calls"] += 1
        _usage["last_activity"] = _time.time()
        return result
    except ValueError as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _usage["errors"] += 1
        raise HTTPException(status_code=502, detail=f"Reasoning failed: {e}")
