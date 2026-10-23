"""OpenHeredity API — 遗传链：版本演化中心、插件版本管理、平滑升级、知识库结构迁移。"""

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.heredity.evolution_loop import EvolutionEngine
from src.heredity.migration import MigrationEngine
from src.heredity.migration_persist import MigrationPersistence
from src.heredity.registry_persist import RegistryPersistence
from src.heredity.version_registry import VersionRegistry

logger = logging.getLogger(__name__)

router = APIRouter()

# ── retention运行时自报状态（可观测性：stats面板能看到"清扫在跑"）──
# claude-code noop自报范式：周期任务空转也必须自报——journal里app级INFO被
# 无handler的root logger丢弃（只有WARNING+可见），所以清扫心跳走stats面板
# （/api/heredity/evolution/stats.retention），用户不用翻日志就知道retention活着。
_retention_state: dict = {
    "sweeps": 0,
    "last_sweep_at": 0.0,
    "last_sweep_retired": 0,
    "last_sweep_error": "",
}

# ── Seed with all known components ─────────────────────────
SEED_COMPONENTS = [
    ("opensoul", "OpenSoul", "0.1.0", {}),
    ("openmate", "OpenMate", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opensoma", "OpenSoma", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opencortex", "OpenCortex", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opennerve", "OpenNerve", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openvein", "OpenVein", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opensense", "OpenSense", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openwill", "OpenWill", "0.1.0", {"opensoul": ">=0.1.0", "opennerve": ">=0.1.0"}),
    ("openvital", "OpenVital", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opengland", "OpenGland", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openimmune", "OpenImmune", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openmarrow", "OpenMarrow", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opengene", "OpenGene", "0.1.0", {}),
    ("openecho", "OpenEcho", "0.1.0", {"opennerve": ">=0.1.0"}),
    ("openmirror", "OpenMirror", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openlink", "OpenLink", "0.1.0", {"opensoul": ">=0.1.0", "opennerve": ">=0.1.0"}),
    ("openhippo", "OpenHippo", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openreflex", "OpenReflex", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openheredity", "OpenHeredity", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openpulse", "OpenPulse", "0.1.0", {}),
    ("opennest", "OpenNest", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openlimb", "OpenLimb", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openvoice", "OpenVoice", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openvision", "OpenVision", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openmind", "OpenMind", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openlearn", "OpenLearn", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("openplugins", "OpenPlugins", "0.1.0", {"opensoul": ">=0.1.0"}),
    ("opentrajectory", "OpenTrajectory", "0.1.0", {"opensoul": ">=0.1.0"}),
]

# ── Singletons ─────────────────────────────────────────────
# RV-①b-⑭-1：注册表三面持久化（组件版本/迁移计划/changelog + platform_version）；
# SEED 随首触懒播只补缺（修前 import 期无条件播种=import 副作用写库污染测试面
# +无条件播种会把 API 注册新版本顶回旧版 current，只补缺=根因修）。
registry = VersionRegistry(
    persist=RegistryPersistence(),
    seed_components=SEED_COMPONENTS,
)
engine = MigrationEngine(
    persist=MigrationPersistence(),
)  # RV-①b-⑭-2 注册 schema/迁移脚本 applied 状态两面持久化（写穿+首触懒回载）
evolution_engine = EvolutionEngine()  # P0-7 自进化闭环：声明→审批→落盘→回滚→记账


# ── Request Schemas ────────────────────────────────────────


class RegisterRequest(BaseModel):
    component_id: str
    component_name: str
    version: str
    dependencies: dict[str, str] = {}
    config_schema: dict = {}
    release_notes: str = ""
    breaking_changes: list[str] = []


class MigrationCreateRequest(BaseModel):
    component_id: str
    from_version: str
    to_version: str
    steps: list[dict] = []
    dry_run: bool = False


class SchemaRegisterRequest(BaseModel):
    component_id: str
    version: str
    fields: list[dict]


class MigrationScriptRequest(BaseModel):
    migration_type: str
    component_id: str
    from_version: str
    to_version: str
    up_sql: str = ""
    down_sql: str = ""
    transform: str = ""


# ── Evolution Loop Schemas（P0-7 自进化闭环） ───────────────


class DeclareIntentRequest(BaseModel):
    kind: str
    title: str
    rationale: str = ""
    confidence: float = 0.5
    evidence_refs: list[str] = []
    proposed_change: dict = {}
    proposer: str = "agent"


class ReviewRequest(BaseModel):
    decision: str  # approve / reject
    reviewer: str
    comment: str = ""


class ApplyRequest(BaseModel):
    actor: str = "system"
    base_dir: str = ""


class RollbackRequest(BaseModel):
    actor: str = "system"
    reason: str = ""


class TriggerEvalRequest(BaseModel):
    idle_seconds: float = 0.0
    context_pressure: float = 0.0
    budget_remaining: float = 1.0
    recent_errors: list[str] = []
    auto_propose: bool = False


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def heredity_stats():
    """Get OpenHeredity statistics."""
    return {
        "status": "ok",
        "component": "OpenHeredity",
        "registry": registry.get_stats(),
        "migrations": engine.get_stats(),
        "evolution": evolution_engine.get_stats(),
    }


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def health():
    """OpenHeredity health check."""
    return {
        "status": "ok",
        "component": "OpenHeredity",
        "registry": registry.get_stats(),
        "migrations": engine.get_stats(),
        "evolution": evolution_engine.get_stats(),
    }


# ── Version Registry ──────────────────────────────────────


@router.get("/components")
async def list_components():
    """List all registered components with their current version."""
    return {"components": registry.list_components(), "total": len(registry.list_components())}


@router.post("/components")
async def register_component(req: RegisterRequest):
    """Register a new component version."""
    cv = registry.register(
        req.component_id,
        req.component_name,
        req.version,
        dependencies=req.dependencies,
        config_schema=req.config_schema,
        release_notes=req.release_notes,
        breaking_changes=req.breaking_changes,
    )
    return {
        "component_id": cv.component_id,
        "version": cv.version,
        "status": cv.status.value,
        "created_at": cv.created_at,
    }


@router.get("/components/{component_id}")
async def get_component(component_id: str):
    """Get component details with all versions."""
    current = registry.get_current(component_id)
    if not current:
        raise HTTPException(404, f"Component {component_id} not found")
    all_versions = registry.get_all_versions(component_id)
    compatibility = registry.check_compatibility(component_id)
    return {
        "component_id": component_id,
        "current": {
            "version": current.version,
            "status": current.status.value,
            "dependencies": current.dependencies,
            "release_notes": current.release_notes,
            "breaking_changes": current.breaking_changes,
            "created_at": current.created_at,
        },
        "versions": [
            {"version": v.version, "status": v.status.value, "created_at": v.created_at}
            for v in all_versions
        ],
        "compatibility": compatibility,
    }


@router.get("/dependencies")
async def get_dependency_graph():
    """Get the full dependency graph."""
    return {"graph": registry.get_dependency_graph()}


@router.get("/compatibility/{component_id}")
async def check_compatibility(component_id: str):
    """Check compatibility for a component."""
    result = registry.check_compatibility(component_id)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


# ── Migrations ────────────────────────────────────────────


@router.post("/migrations")
async def create_migration(req: MigrationCreateRequest):
    """Create a migration plan."""
    migration = registry.create_migration(
        req.component_id,
        req.from_version,
        req.to_version,
        steps=req.steps or None,
        dry_run=req.dry_run,
    )
    return {
        "migration_id": migration.migration_id,
        "component_id": migration.component_id,
        "from_version": migration.from_version,
        "to_version": migration.to_version,
        "status": migration.status.value,
        "dry_run": migration.dry_run,
        "steps": migration.steps,
    }


@router.get("/migrations")
async def list_migrations(component_id: str = Query(default=None)):
    """List migrations."""
    migrations = registry.list_migrations(component_id=component_id)
    return {
        "migrations": [
            {
                "migration_id": m.migration_id,
                "component_id": m.component_id,
                "from_version": m.from_version,
                "to_version": m.to_version,
                "status": m.status.value,
                "dry_run": m.dry_run,
                "started_at": m.started_at,
                "completed_at": m.completed_at,
                "error": m.error,
            }
            for m in migrations
        ],
        "total": len(migrations),
    }


@router.post("/migrations/{migration_id}/execute")
async def execute_migration(migration_id: str):
    """Execute a migration."""
    try:
        migration = registry.execute_migration(migration_id)
        return {
            "migration_id": migration.migration_id,
            "status": migration.status.value,
            "started_at": migration.started_at,
            "completed_at": migration.completed_at,
        }
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/migrations/{migration_id}/rollback")
async def rollback_migration(migration_id: str):
    """Rollback a migration."""
    try:
        migration = registry.rollback_migration(migration_id)
        return {"migration_id": migration.migration_id, "status": migration.status.value}
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── Schema Migrations ─────────────────────────────────────


@router.post("/schemas")
async def register_schema(req: SchemaRegisterRequest):
    """Register a schema version."""
    sv = engine.register_schema(req.component_id, req.version, req.fields)
    return {"schema_id": sv.schema_id, "component_id": sv.component_id, "version": sv.version}


@router.get("/schemas/{component_id}")
async def get_schema(component_id: str, version: str = Query(default=None)):
    """Get schema for a component."""
    schema = engine.get_schema(component_id, version)
    if not schema:
        raise HTTPException(404, "Schema not found")
    return {
        "schema_id": schema.schema_id,
        "component_id": schema.component_id,
        "version": schema.version,
        "fields": [
            {
                "name": f.name,
                "field_type": f.field_type,
                "required": f.required,
                "default": f.default,
            }
            for f in schema.fields
        ],
    }


@router.get("/schemas/{component_id}/diff")
async def diff_schemas(
    component_id: str, from_version: str = Query(...), to_version: str = Query(...)
):
    """Get diff between two schema versions."""
    diff = engine.diff_schemas(component_id, from_version, to_version)
    return {
        "component_id": component_id,
        "from_version": from_version,
        "to_version": to_version,
        "added": [{"name": f.name, "field_type": f.field_type} for f in diff.added],
        "removed": diff.removed,
        "modified": diff.modified,
    }


@router.post("/scripts")
async def create_script(req: MigrationScriptRequest):
    """Create a migration script."""
    script = engine.create_migration_script(
        req.migration_type,
        req.component_id,
        req.from_version,
        req.to_version,
        up_sql=req.up_sql,
        down_sql=req.down_sql,
        transform=req.transform,
    )
    return {"script_id": script.script_id, "applied": script.applied}


@router.get("/scripts")
async def list_scripts(
    component_id: str = Query(default=None), applied: bool = Query(default=None)
):
    """List migration scripts."""
    return {"scripts": engine.list_scripts(component_id=component_id, applied=applied)}


@router.post("/scripts/{script_id}/apply")
async def apply_script(script_id: str):
    """Mark a migration script as applied."""
    try:
        script = engine.apply_script(script_id)
        return {"script_id": script.script_id, "applied": True, "applied_at": script.applied_at}
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── Changelog ─────────────────────────────────────────────


@router.get("/changelog")
async def get_changelog(component_id: str = Query(default=None), limit: int = Query(default=50)):
    """Get changelog."""
    return {"changelog": registry.get_changelog(component_id=component_id, limit=limit)}


# ── Platform Version ──────────────────────────────────────


@router.get("/platform")
async def get_platform():
    """Get platform version info."""
    return registry.get_platform_version()


@router.post("/platform/bump")
async def bump_platform(bump_type: str = Query(default="patch")):
    """Bump platform version."""
    if bump_type not in ("major", "minor", "patch"):
        raise HTTPException(400, "bump_type must be major, minor, or patch")
    new_version = registry.bump_platform_version(bump_type)
    return {"platform_version": new_version, "bump_type": bump_type}


# ── Evolution Loop（P0-7 自进化闭环：声明→审批→落盘→回滚→记账） ──


@router.post("/evolution/intents")
async def declare_evolution_intent(req: DeclareIntentRequest):
    """声明一条进化意图（LobeChat范式：只声明不执行，pending等审批）。"""
    try:
        result = evolution_engine.declare_intent(
            kind=req.kind,
            title=req.title,
            rationale=req.rationale,
            confidence=req.confidence,
            evidence_refs=req.evidence_refs,
            proposed_change=req.proposed_change,
            proposer=req.proposer,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/evolution/proposals")
async def list_evolution_proposals(
    status: str = Query(default=""), limit: int = Query(default=50, ge=1, le=500)
):
    """列出进化提案（按状态过滤）。"""
    proposals = evolution_engine.store.list_proposals(status=status, limit=limit)
    return {"proposals": proposals, "total": len(proposals)}


@router.get("/evolution/proposals/{proposal_id}")
async def get_evolution_proposal(proposal_id: str):
    """查看单条提案及其完整审计轨迹。"""
    proposal = evolution_engine.store.get_proposal(proposal_id)
    if not proposal:
        raise HTTPException(404, f"proposal {proposal_id} not found")
    return {
        "proposal": proposal.to_dict(),
        "history": evolution_engine.store.get_ledger(proposal_id=proposal_id),
    }


@router.post("/evolution/proposals/{proposal_id}/review")
async def review_evolution_proposal(proposal_id: str, req: ReviewRequest):
    """审批提案（审批人≠发起人；approve后才允许apply）。"""
    try:
        return evolution_engine.review(proposal_id, req.decision, req.reviewer, comment=req.comment)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/evolution/proposals/{proposal_id}/apply")
async def apply_evolution_proposal(proposal_id: str, req: ApplyRequest):
    """落盘：稀疏锚点替换，改动前先快照，保护区fail-closed。"""
    try:
        return evolution_engine.apply(proposal_id, actor=req.actor, base_dir=req.base_dir)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/evolution/proposals/{proposal_id}/rollback")
async def rollback_evolution_proposal(proposal_id: str, req: RollbackRequest):
    """回滚：从快照恢复，账本记ROLLED_BACK。"""
    try:
        return evolution_engine.rollback(proposal_id, actor=req.actor, reason=req.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/evolution/triggers/evaluate")
async def evaluate_evolution_triggers(req: TriggerEvalRequest):
    """评估进化触发器（CowAgent idle触发 + agno错误风暴熔断）。"""
    return evolution_engine.evaluate_triggers(
        idle_seconds=req.idle_seconds,
        context_pressure=req.context_pressure,
        budget_remaining=req.budget_remaining,
        recent_errors=req.recent_errors,
        auto_propose=req.auto_propose,
    )


@router.get("/evolution/ledger")
async def get_evolution_ledger(
    proposal_id: str = Query(default=""), limit: int = Query(default=100, ge=1, le=1000)
):
    """审计账本：DECLARED→APPROVED→APPLIED→ROLLED_BACK全程可追溯。"""
    return {"ledger": evolution_engine.store.get_ledger(proposal_id=proposal_id, limit=limit)}


@router.get("/evolution/stats")
async def evolution_stats():
    """进化管线统计（提案状态分布/账本事件/熔断状态 + retention清扫心跳）。"""
    return {**evolution_engine.get_stats(), "retention": dict(_retention_state)}


# ── stale pending周期清扫入口（运行时接线，main.py _evolution_retention_loop调用） ──


def sweep_stale_proposals() -> dict:
    """agno stale回收的运行时周期入口：超龄pending转rejected+账本REJECTED(actor=retention)。

    为什么需要（"写了≠接线了"缺口）：retire_stale_pending（0b5e0372进化预算自愈
    三件套）只挂在 declare_intent 声明路径做惰性清扫——声明一停，清扫就停。
    生产实证：2026-10-04 23:48批量立案后 SelfEvolution 因数据面干净不再产生新
    发现（也就不再声明），5条假模式pending（test_job 910次等测试噪声+9月已修复
    bug）超龄48h TTL无人清扫，长期滞留pending面板（"没有进化啊"的度量前提=
    面板只显示真实待审提案）。本入口被 main.py 周期任务每小时调用，TTL契约
    （PENDING_TTL_S=48h）与声明流量彻底解耦。

    只操作引擎全局单例（heredity_client隔离面 monkeypatch evolution_engine 即换
    数据面，测试零生产接触）；清扫失败不抛出（retention是后台维护，绝不反噬调用方，
    失败显式进返回值error——mem0 §1.1失败必须可见）。

    每次清扫更新 _retention_state（stats面板heartbeat，见文件头注释）。
    """
    import time as _time

    started = _time.time()
    try:
        retired = evolution_engine.store.retire_stale_pending(evolution_engine.pending_ttl_s)
    except Exception as e:  # 清扫失败可见但不影响服务
        logger.warning("[evolution-retention] sweep failed: %s", e)
        _retention_state["sweeps"] += 1
        _retention_state["last_sweep_at"] = started
        _retention_state["last_sweep_error"] = str(e)
        return {"retired": [], "error": str(e)}
    _retention_state["sweeps"] += 1
    _retention_state["last_sweep_at"] = started
    _retention_state["last_sweep_retired"] = len(retired)
    _retention_state["last_sweep_error"] = ""
    return {"retired": retired}
