"""OpenBenchmark API — organ performance benchmarking with historical tracking."""

import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.api.gland import _ensure_bootstrapped
from src.api.gland import gateway as llm_gateway
from src.benchmark.engine import BENCHMARK_TARGETS, benchmark_engine
from src.benchmark.eval_loop import (
    CodeScorer,
    EnvMismatchError,
    EvalCase,
    EvalStore,
    ExperimentRunner,
    JudgeScorer,
    make_router_judge,
    make_router_runner,
)
from src.benchmark.evaluator import CapabilityEvaluator, EvaluationDimension

router = APIRouter()
capability_evaluator = CapabilityEvaluator()
eval_store = EvalStore()


class BenchmarkRunRequest(BaseModel):
    organs: list[str] | None = None  # None = all
    iterations: int = 20
    concurrency: int = 5


class EvaluateRequest(BaseModel):
    accuracy: float = 0.5
    efficiency: float = 0.5
    completeness: float = 0.5
    safety: float = 0.5
    helpfulness: float = 0.5
    details: dict = {}


# ── Health ──────────────────────────────────────────────────


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "component": "OpenBenchmark",
        "capability_evaluations": capability_evaluator.get_stats().get("total_evaluations", 0),
    }


# ── Stats ───────────────────────────────────────────────────


@router.get("/stats")
async def stats():
    return benchmark_engine.stats()


# ── Available Targets ───────────────────────────────────────


@router.get("/targets")
async def list_targets():
    """List all benchmarkable organs."""
    return {
        "targets": [
            {"organ": k, "label": v["label"], "endpoint": v["endpoint"]}
            for k, v in BENCHMARK_TARGETS.items()
        ],
        "total": len(BENCHMARK_TARGETS),
    }


# ── Run Benchmark ───────────────────────────────────────────


@router.post("/run")
async def run_benchmark(req: BenchmarkRunRequest):
    """Run a performance benchmark against specified organs."""
    if req.iterations < 1 or req.iterations > 200:
        raise HTTPException(status_code=400, detail="iterations must be 1-200")
    if req.concurrency < 1 or req.concurrency > 50:
        raise HTTPException(status_code=400, detail="concurrency must be 1-50")

    result = await benchmark_engine.run_benchmark(
        organs=req.organs,
        iterations=req.iterations,
        concurrency=req.concurrency,
    )
    return result


# ── Quick Benchmark (single organ) ──────────────────────────


@router.post("/quick/{organ}")
async def quick_benchmark(organ: str, iterations: int = Query(default=10, ge=1, le=100)):
    """Quick benchmark a single organ with default settings."""
    if organ not in BENCHMARK_TARGETS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown organ: {organ}. Valid: {list(BENCHMARK_TARGETS.keys())}",
        )

    result = await benchmark_engine.run_benchmark(
        organs=[organ],
        iterations=iterations,
        concurrency=3,
    )
    return result


# ── Cancel Running Benchmark ────────────────────────────────


@router.post("/cancel/{run_id}")
async def cancel_benchmark(run_id: str):
    """Cancel a running benchmark."""
    if benchmark_engine.cancel_run(run_id):
        return {"message": f"Benchmark {run_id} cancelled"}
    raise HTTPException(status_code=404, detail=f"No running benchmark with id: {run_id}")


# ── History ─────────────────────────────────────────────────


@router.get("/history")
async def get_history(
    organ: str = Query(default="", description="Filter by organ"),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Get benchmark history."""
    return {
        "history": benchmark_engine.get_history(organ=organ, limit=limit),
    }


@router.get("/history/runs")
async def get_runs(limit: int = Query(default=20, ge=1, le=100)):
    """Get benchmark run history."""
    return {
        "runs": benchmark_engine.get_runs(limit=limit),
    }


# ── Latest / Comparison ─────────────────────────────────────


@router.get("/latest")
async def get_latest():
    """Get latest benchmark results for all tested organs."""
    return {
        "latest": benchmark_engine.get_latest(),
        "timestamp": time.time(),
    }


@router.get("/comparison")
async def get_comparison():
    """Get comparison data — all organs sorted by performance."""
    return {
        "comparison": benchmark_engine.get_comparison(),
        "timestamp": time.time(),
    }


# ── Delete History ──────────────────────────────────────────


@router.delete("/history")
async def delete_history(organ: str = Query(default="", description="Delete by organ, or all")):
    """Delete benchmark history."""
    deleted = benchmark_engine.delete_history(organ=organ)
    return {"deleted": deleted, "organ": organ or "all"}


# ── Capability Evaluation ────────────────────────────────────


@router.post("/capability/evaluate")
async def evaluate_capability(req: EvaluateRequest):
    """Evaluate agent capability across 5 dimensions."""
    dimension_scores = {
        "accuracy": req.accuracy,
        "efficiency": req.efficiency,
        "completeness": req.completeness,
        "safety": req.safety,
        "helpfulness": req.helpfulness,
    }
    result = capability_evaluator.record(
        session_id=f"eval_{int(time.time() * 1000)}",
        task_type="general",
        dimension_scores=dimension_scores,
    )
    return {
        "eval_id": result.eval_id,
        "overall_score": result.overall_score,
        "dimensions": result.dimensions,
    }


@router.get("/capability/report")
async def capability_report():
    """Get capability evaluation report."""
    return capability_evaluator.get_stats()


@router.get("/capability/trends")
async def capability_trends(days: int = Query(default=7)):
    """Get trend data."""
    return {"trend": capability_evaluator.get_trend(days)}


@router.get("/capability/weak-dimensions")
async def capability_weak_dimensions(
    task_type: str = Query(default="", description="Filter by task type, or all"),
    min_samples: int = Query(default=5, ge=1, description="Minimum samples per dimension"),
):
    """Get weak dimensions (avg score < 0.7) — capability short-board report."""
    weak = capability_evaluator.get_weak_dimensions(task_type=task_type, min_samples=min_samples)
    return {
        "weak_dimensions": weak,
        "task_type": task_type,
        "min_samples": min_samples,
        "count": len(weak),
    }


# ── Evaluation Loop (P0-5: agno/langfuse-style 评估闭环) ──────────────


class EvalCaseModel(BaseModel):
    case_id: str = ""
    prompt: str
    task_type: str = "general"
    expected: str = ""
    rubric: str = ""
    tags: list[str] = []
    metadata: dict = {}


class DatasetCreateRequest(BaseModel):
    name: str
    description: str = ""


class CasesAddRequest(BaseModel):
    cases: list[EvalCaseModel]


class ExperimentRunRequest(BaseModel):
    dataset_id: str
    k: int = 1
    policy_name: str = "default"
    policy_meta: dict = {}
    system_prompt: str = ""
    model: str | None = None
    temperature: float = 0.2
    seed: int = 42
    baseline_id: str = ""
    scorer: str = "auto"  # auto | code | judge
    judge_model: str | None = None
    session_prefix: str = "eval"
    write_trajectory_scores: bool = True


@router.post("/eval/datasets")
async def create_eval_dataset(req: DatasetCreateRequest):
    """Create a named evaluation dataset (langfuse Dataset)."""
    dataset_id = eval_store.create_dataset(req.name, req.description)
    return {"dataset_id": dataset_id, "name": req.name}


@router.get("/eval/datasets")
async def list_eval_datasets():
    return {"datasets": eval_store.list_datasets()}


@router.post("/eval/datasets/{dataset_id}/cases")
async def add_eval_cases(dataset_id: str, req: CasesAddRequest):
    """Add or replace cases in a dataset."""
    cases = [
        EvalCase(
            case_id=c.case_id,
            prompt=c.prompt,
            task_type=c.task_type,
            expected=c.expected,
            rubric=c.rubric,
            tags=c.tags,
            metadata=c.metadata,
        )
        for c in req.cases
    ]
    try:
        added = eval_store.add_cases(dataset_id, cases)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"dataset_id": dataset_id, "added": added}


@router.get("/eval/datasets/{dataset_id}/cases")
async def list_eval_cases(
    dataset_id: str,
    seed: int | None = Query(default=None, description="Deterministic sampling seed"),
    limit: int | None = Query(default=None, ge=1),
):
    """List dataset cases; seed gives deterministic shuffled sampling."""
    if not eval_store.get_dataset(dataset_id):
        raise HTTPException(status_code=404, detail=f"unknown dataset: {dataset_id}")
    cases = eval_store.list_cases(dataset_id, seed=seed, limit=limit)
    return {
        "dataset_id": dataset_id,
        "seed": seed,
        "cases": [
            {
                "case_id": c.case_id,
                "prompt": c.prompt,
                "task_type": c.task_type,
                "expected": c.expected,
                "rubric": c.rubric,
                "tags": c.tags,
            }
            for c in cases
        ],
    }


@router.post("/eval/experiments/run")
async def run_eval_experiment(req: ExperimentRunRequest):
    """Run an evaluation experiment against a dataset.

    scorer=auto: CodeScorer first (programmatic checks win when the case has
    an expected value), JudgeScorer (LLM-as-Judge) for the rest —
    CAMEL: "能程序化验证的绝不靠LLM".
    """
    if req.k < 1 or req.k > 10:
        raise HTTPException(status_code=400, detail="k must be 1-10")
    dataset = eval_store.get_dataset(req.dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail=f"unknown dataset: {req.dataset_id}")
    if dataset["case_count"] == 0:
        raise HTTPException(status_code=400, detail="dataset has no cases")

    _ensure_bootstrapped()
    scorers: list = []
    if req.scorer in ("auto", "code"):
        scorers.append(CodeScorer())
    if req.scorer in ("auto", "judge"):
        judge_fn, identity = make_router_judge(llm_gateway, model=req.judge_model)
        scorers.append(JudgeScorer(judge_fn, model_identity=identity))
    if not scorers:
        raise HTTPException(status_code=400, detail="scorer must be one of: auto, code, judge")

    runner_fn = make_router_runner(
        llm_gateway,
        system_prompt=req.system_prompt,
        model=req.model,
        temperature=req.temperature,
    )
    trajectory_store = None
    if req.write_trajectory_scores:
        from src.trajectory.store import trajectory_store as _ts

        trajectory_store = _ts

    runner = ExperimentRunner(
        store=eval_store,
        dataset_id=req.dataset_id,
        runner_fn=runner_fn,
        scorers=scorers,
        k=req.k,
        policy_name=req.policy_name,
        policy_meta=req.policy_meta,
        seed=req.seed,
        baseline_id=req.baseline_id,
        session_prefix=req.session_prefix,
        trajectory_store=trajectory_store,
    )
    try:
        results = await runner.run()
    except EnvMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return results


@router.get("/eval/experiments")
async def list_eval_experiments(
    dataset_id: str = Query(default="", description="Filter by dataset"),
):
    return {"experiments": eval_store.list_experiments(dataset_id=dataset_id)}


@router.get("/eval/experiments/{experiment_id}")
async def get_eval_experiment(experiment_id: str):
    """Full experiment results incl. per-case attempts + learning_zone."""
    results = eval_store.get_experiment(experiment_id)
    if not results:
        raise HTTPException(status_code=404, detail=f"unknown experiment: {experiment_id}")
    return results


@router.get("/eval/experiments/{experiment_id}/diff")
async def diff_eval_experiment(experiment_id: str, baseline_id: str = Query(...)):
    """Recompute a baseline diff for a stored experiment (409 on env mismatch)."""
    from src.benchmark.eval_loop import diff_against_baseline as _diff

    current = eval_store.get_experiment(experiment_id)
    baseline = eval_store.get_experiment(baseline_id)
    if not current:
        raise HTTPException(status_code=404, detail=f"unknown experiment: {experiment_id}")
    if not baseline:
        raise HTTPException(status_code=404, detail=f"unknown baseline: {baseline_id}")
    try:
        return {"diff": _diff(baseline, current)}
    except EnvMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/eval/learning-zone/{experiment_id}")
async def eval_learning_zone(experiment_id: str):
    """Cases with mixed outcomes (0 < pass < scored) = SFT candidates
    (agno learning_zone → to_sft_jsonl的上游)."""
    results = eval_store.get_experiment(experiment_id)
    if not results:
        raise HTTPException(status_code=404, detail=f"unknown experiment: {experiment_id}")
    zone = []
    for cid in results.get("learning_zone", []):
        rec = results["cases"][cid]
        zone.append(
            {
                "case_id": cid,
                "pass_rate": rec["pass_rate"],
                "pass_count": rec["pass_count"],
                "scored_count": rec["scored_count"],
            }
        )
    return {"experiment_id": experiment_id, "learning_zone": zone}
