"""OpenGene API — 基因系统：模板库管理、模板实例化。"""

import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.gene.skill_learner import SkillLearner
from src.gene.templates import TemplateEngine

router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
engine = TemplateEngine()
skill_learner = SkillLearner()


# ── Request Schemas ────────────────────────────────────────


class TemplateCreateRequest(BaseModel):
    template_id: str = ""
    name: str
    category: str  # "agent", "knowledge_base", "workflow", "skill"
    description: str = ""
    version: str = "1.0.0"
    author: str = "user"
    tags: list[str] = []
    config: dict = {}
    variables: list[dict] = []


class CloneRequest(BaseModel):
    new_id: str = ""
    new_name: str = ""
    overrides: dict = {}


class ImportRequest(BaseModel):
    templates: list[dict]
    overwrite: bool = False


class InstantiateRequest(BaseModel):
    variables: dict = {}


# ── Template Endpoints ─────────────────────────────────────


@router.get("/templates")
async def list_templates(
    category: str = Query(default=None),
    tag: str = Query(default=None),
):
    """List available templates."""
    return {"templates": engine.list_templates(category=category, tag=tag)}


@router.get("/templates/search/{query}")
async def search_templates(query: str):
    """Search templates by name, description, or tags."""
    return {"templates": engine.search(query), "query": query}


@router.get("/categories")
async def list_categories():
    """List all template categories with counts."""
    return {"categories": engine.categories()}


@router.get("/tags")
async def list_tags():
    """List all template tags with usage counts."""
    return {"tags": engine.tags()}


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    """Get template details."""
    t = engine.get_template(template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    return {
        "template_id": t.template_id,
        "name": t.name,
        "category": t.category,
        "description": t.description,
        "version": t.version,
        "author": t.author,
        "tags": t.tags,
        "config": t.config,
        "variables": t.variables,
        "usage_count": t.usage_count,
        "builtin": t.builtin,
    }


@router.post("/templates")
async def create_template(req: TemplateCreateRequest):
    """Create a new template."""
    data = req.model_dump()
    if not data.get("template_id"):
        data.pop("template_id", None)
    template = engine.create_template(data)
    return {
        "template_id": template.template_id,
        "name": template.name,
        "category": template.category,
    }


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    """Delete a user template (built-in templates cannot be deleted)."""
    if not engine.delete_template(template_id):
        raise HTTPException(400, "Cannot delete: template not found or is built-in")
    return {"message": f"Template '{template_id}' deleted"}


@router.post("/templates/{template_id}/instantiate")
async def instantiate_template(template_id: str, req: InstantiateRequest):
    """Create an instance from a template with variable substitution."""
    result = engine.instantiate(template_id, req.variables)
    if not result["success"]:
        raise HTTPException(404, result["error"])
    return result


# ── Export / Import / Clone ────────────────────────────────


@router.get("/templates/{template_id}/export")
async def export_template(template_id: str):
    """Export a single template as JSON."""
    data = engine.export_template(template_id)
    if not data:
        raise HTTPException(404, "Template not found")
    return {"template": data, "format": "opensoul-gene-v1"}


@router.get("/export")
async def export_all_templates(
    category: str = Query(default=None),
    include_builtin: bool = Query(default=True),
):
    """Export all templates as a JSON bundle."""
    templates = engine.export_all(category=category, include_builtin=include_builtin)
    return {
        "format": "opensoul-gene-bundle-v1",
        "exported_at": time.time(),
        "count": len(templates),
        "templates": templates,
    }


@router.post("/templates/{template_id}/clone")
async def clone_template(template_id: str, req: CloneRequest):
    """Clone a template with optional overrides."""
    template, msg = engine.clone_template(
        template_id, new_id=req.new_id, new_name=req.new_name, overrides=req.overrides
    )
    if not template:
        raise HTTPException(400, msg)
    return {
        "message": msg,
        "template_id": template.template_id,
        "name": template.name,
    }


@router.post("/import")
async def import_templates(req: ImportRequest):
    """Import one or more templates from JSON."""
    results = []
    for data in req.templates:
        template, msg = engine.import_template(data, overwrite=req.overwrite)
        results.append(
            {
                "template_id": data.get("template_id", "?"),
                "success": template is not None,
                "message": msg,
            }
        )
    imported = sum(1 for r in results if r["success"])
    return {
        "imported": imported,
        "total": len(results),
        "results": results,
    }


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def gene_stats():
    """OpenGene detailed statistics."""
    templates = engine.list_templates()
    by_author = {}
    total_usage = 0
    for t in templates:
        author = t.get("author", "unknown")
        by_author[author] = by_author.get(author, 0) + 1
        total_usage += t.get("usage_count", 0)

    return {
        "status": "ok",
        "component": "OpenGene",
        **engine.stats(),
        "total_usage_count": total_usage,
        "by_author": by_author,
        "most_used": sorted(templates, key=lambda x: x.get("usage_count", 0), reverse=True)[:5],
    }


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def gene_health():
    """OpenGene health check."""
    # gene↔skills对齐：文件系统发现的skill（.agents/skills标准层+shared+agent目录）与学习型skill并列展示
    discovered = {"discovered_count": 0, "standard_count": 0, "invalid_count": 0, "sources": {}}
    try:
        from src.api.skills import _scan_agent_skills, _scan_shared_skills, _scan_standard_skills

        standard_skills, validation = _scan_standard_skills()
        shared = _scan_shared_skills()
        agent = _scan_agent_skills()
        all_skills = standard_skills + shared + agent
        discovered = {
            "discovered_count": len(all_skills),
            "standard_count": len(standard_skills),
            "invalid_count": len(validation),
            "sources": {
                "agents_standard": len(standard_skills),
                "shared": len(shared),
                "agent_dirs": len(agent),
            },
        }
    except Exception:
        pass  # gene健康检查不因skills扫描失败而失败

    learned = skill_learner.get_stats()
    return {
        "status": "ok",
        "component": "OpenGene",
        **engine.stats(),
        "skills": {**learned, "discovered": discovered},
    }


# ── Skill Learning ────────────────────────────────────────────


class SkillExtractRequest(BaseModel):
    task_description: str
    execution_log: str
    success: bool = True
    metadata: dict = {}


class SkillRecommendRequest(BaseModel):
    task_description: str


@router.post("/skill/extract")
async def extract_skill(req: SkillExtractRequest):
    """Extract a skill from execution log."""
    import json

    # Parse execution_log as JSON list of tool calls
    try:
        tool_calls = json.loads(req.execution_log)
        if not isinstance(tool_calls, list):
            tool_calls = []
    except (json.JSONDecodeError, TypeError):
        tool_calls = []

    skill = skill_learner.extract_from_execution(
        session_id=f"skill_{int(time.time() * 1000)}",
        task_description=req.task_description,
        tool_calls=tool_calls,
        success=req.success,
    )
    if not skill:
        # 失败执行已进失败记忆分支（skill_audit FAIL，mem0 §1.1失败可见）——
        # 这里400表达"没提炼出技能"，但失败本身已留痕，非静默丢弃
        raise HTTPException(
            400, "No skill extracted (failed executions are recorded in skill_audit)"
        )
    return {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "description": skill.description,
        "success_rate": skill.success_rate,
        "event": skill.audit_event,
    }


@router.post("/skill/recommend")
async def recommend_skills(req: SkillRecommendRequest):
    """Recommend skills for a task."""
    skills = skill_learner.find_relevant(req.task_description)
    return {
        "skills": [
            {
                "skill_id": s.get("skill_id", ""),
                "name": s.get("name", ""),
                "description": s.get("description", ""),
                "success_rate": s.get("success_rate", 0.0),
                "usage_count": s.get("usage_count", 0),
            }
            for s in skills
        ],
        "count": len(skills),
    }


@router.get("/skill/{skill_id}/context")
async def skill_context(skill_id: str, task: str = Query(default="")):
    """Get skill context for prompt injection."""
    return {"context": skill_learner.get_context_prompt(task)}


# ── Skill进化审计（mem0 §1.2 记账：谁/何时/从什么改成什么） ────────


@router.get("/skill/audit")
async def skill_audit_tail(limit: int = Query(default=50, ge=1, le=500)):
    """最近的技能审计事件（ADD/UPDATE/DELETE/FAIL），新→旧。"""
    return {"events": skill_learner.audit_tail(limit=limit)}


@router.get("/skill/{skill_id}/history")
async def skill_history(skill_id: str, limit: int = Query(default=50, ge=1, le=500)):
    """单个技能的审计历史——这条策略是被谁、因为哪次执行、从什么改成什么。"""
    return {"skill_id": skill_id, "events": skill_learner.history(skill_id, limit=limit)}


@router.delete("/skill/{skill_id}")
async def delete_learned_skill(skill_id: str, reason: str = Query(default="")):
    """删除学习型技能并记DELETE审计（old留档=回滚依据）。"""
    if not skill_learner.delete_skill(skill_id, reason=reason):
        raise HTTPException(404, f"skill not found: {skill_id}")
    return {"status": "deleted", "skill_id": skill_id}


# ── 进化回滚（Agent Zero _time_travel：preview先行 + travel/revert） ──


@router.get("/skill/{skill_id}/revert-preview")
async def skill_revert_preview(skill_id: str, audit_id: int = Query(default=None)):
    """回滚预览（Agent Zero _time_travel preview）：逐字段diff看清将发生什么，零写入。

    缺省audit_id=预览"回滚上一次改动"；显式audit_id=预览回到该事件之前。
    """
    try:
        preview = skill_learner.revert_preview(skill_id, audit_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if preview is None:
        raise HTTPException(404, f"no revertible audit event for skill: {skill_id}")
    return preview


@router.post("/skill/{skill_id}/revert")
async def revert_learned_skill(
    skill_id: str,
    audit_id: int = Query(default=None),
    reason: str = Query(default=""),
):
    """一键回滚：技能恢复到目标审计事件之前的状态（缺省=上一次改动）。

    回滚本身记REVERT审计（可再回滚）；审计表只追加不改写。
    """
    try:
        result = skill_learner.revert_skill(skill_id, audit_id, actor="api", reason=reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:
        raise HTTPException(404, f"no revertible audit event for skill: {skill_id}")
    return result
