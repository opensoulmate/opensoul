"""Template engine — manage and instantiate templates."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Template:
    template_id: str
    name: str
    category: str  # "agent", "knowledge_base", "workflow", "skill"
    description: str
    version: str = "1.0.0"
    author: str = "system"
    tags: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    variables: list[dict] = field(default_factory=list)  # [{name, type, default, description}]
    created_at: float = field(default_factory=time.time)
    usage_count: int = 0
    builtin: bool = False
    # 行租户键（DEC-⑦-③ gene 子搜索存储层加轴）：自带键、缺失/空白→'default'
    # 兜底（template_row_tenant 单一判法，DEC-⑦-① _event_row_tenant / DEC-⑦-②
    # message_row_tenant 同款语义）；旧 JSON 无此键加载为 '' = 'default' 兼容。
    tenant_id: str = ""


# ── 写入护栏（2026-10-03 gene模板写入护栏轮）─────────────────────────────
# 参照kilocode discovery.ts"origin钉死+原子swap+校验"三件套：
# - 校验：template_id白名单字符集（首字符字母数字，禁路径分隔符/前导点）→
#   堵死 _save_template 的 `storage_dir / f"{template_id}.json"` 路径穿越写
#   （template_id经POST /api/gene/templates、/import、clone完全用户可控）
# - 钉死：落盘文件名==template_id，加载时文件名与内部id不符即拒载（防伪造来源）
# - 原子swap：tmp+os.replace，崩溃不留半截JSON（半截文件曾被_load_user_templates
#   静默跳过=静默丢模板）

_TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class TemplateValidationError(ValueError):
    """写入护栏拒绝：非法template_id或缺失必填字段。API层映射为HTTP 400。"""


class TemplateTenantDenied(TemplateValidationError):
    """跨租户写拒（行归属不可被跨租户改写）：API层映射为HTTP 403。"""


def template_row_tenant(template: Template) -> str:
    """行租户键单一判法（DEC-⑦-③）：自带键、缺失/空白→'default' 兜底。

    写读同源：create/import/clone 落行与 list_templates 过滤、delete 归属闸共用
    本函数，禁消费点另发明判法（F4b-㉗ 纪律）；DEC-⑦-① _event_row_tenant /
    DEC-⑦-② message_row_tenant 同款语义（系统/缺省写方→'default'，键派生故障
    绝不吃掉落库）。
    """
    return ((template.tenant_id or "").strip()) or "default"


def _validate_template_id(template_id: Any) -> str:
    """校验template_id安全合规（白名单字符集），非法即raise TemplateValidationError。"""
    if not isinstance(template_id, str) or not _TEMPLATE_ID_RE.match(template_id):
        raise TemplateValidationError(
            f"invalid template_id: {template_id!r} "
            f"(allowed: [A-Za-z0-9][A-Za-z0-9._-]{{0,127}}, no path separators)"
        )
    return template_id


class TemplateEngine:
    """Template registry with built-in and user-defined templates."""

    def __init__(self, storage_dir: str | Path | None = None):
        self.storage_dir = Path(storage_dir or os.path.expanduser("~/.opensoul/templates"))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._templates: dict[str, Template] = {}
        self._lock = threading.Lock()
        self._load_user_templates()
        self._register_builtins()

    def _load_user_templates(self):
        for f in self.storage_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                t = Template(**data)
                # origin钉死：文件名必须==template_id，且id过白名单校验
                if t.template_id != f.stem:
                    raise TemplateValidationError(
                        f"template_id {t.template_id!r} != filename stem {f.stem!r}"
                    )
                _validate_template_id(t.template_id)
                self._templates[t.template_id] = t
            except Exception as exc:
                # 拒载必须可观测（曾为debug静默跳过=假模板/丢模板无感知）
                logging.getLogger(__name__).warning("template load rejected: %s (%s)", f.name, exc)

    def _save_template(self, template: Template):
        tid = _validate_template_id(template.template_id)
        path = self.storage_dir / f"{tid}.json"
        # 路径穿越双重保险：解析后必须仍在storage_dir内
        if path.resolve().parent != self.storage_dir.resolve():
            raise TemplateValidationError(f"template_id escapes storage dir: {tid!r}")
        tmp = self.storage_dir / f".{tid}.json.tmp"
        tmp.write_text(
            json.dumps(
                {
                    "template_id": template.template_id,
                    "name": template.name,
                    "category": template.category,
                    "description": template.description,
                    "version": template.version,
                    "author": template.author,
                    "tags": template.tags,
                    "config": template.config,
                    "variables": template.variables,
                    "created_at": template.created_at,
                    "usage_count": template.usage_count,
                    "builtin": template.builtin,
                    "tenant_id": template.tenant_id,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        os.replace(tmp, path)  # 原子swap：崩溃不留半截JSON

    def _register_builtins(self):
        """Register built-in starter templates."""
        builtins = [
            Template(
                template_id="agent-rag-assistant",
                name="RAG知识助手",
                category="agent",
                description="基于RAG的知识问答Agent，支持文档检索和智能回答",
                tags=["rag", "qa", "knowledge"],
                config={
                    "system_prompt": "你是一个知识助手，基于检索到的文档内容回答用户问题。如果文档中没有相关信息，请明确告知。",
                    "model": "gpt-4",
                    "temperature": 0.3,
                    "tools": ["knowledge_search", "web_search"],
                    "knowledge_base_ids": [],
                },
                variables=[
                    {
                        "name": "knowledge_base_ids",
                        "type": "list",
                        "default": [],
                        "description": "关联的知识库ID列表",
                    },
                    {
                        "name": "model",
                        "type": "string",
                        "default": "gpt-4",
                        "description": "使用的模型",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="agent-code-reviewer",
                name="代码审查Agent",
                category="agent",
                description="自动代码审查Agent，检测代码质量、安全漏洞和最佳实践",
                tags=["code", "review", "security"],
                config={
                    "system_prompt": "你是一个专业的代码审查员。审查代码的：1)正确性 2)安全性 3)性能 4)可维护性。给出具体改进建议。",
                    "model": "gpt-4",
                    "temperature": 0.2,
                    "tools": ["file_read", "terminal"],
                },
                variables=[
                    {
                        "name": "language",
                        "type": "string",
                        "default": "python",
                        "description": "编程语言",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="kb-tech-docs",
                name="技术文档知识库",
                category="knowledge_base",
                description="技术文档知识库模板，含标准分块策略和元数据结构",
                tags=["tech", "docs", "rag"],
                config={
                    "chunk_size": 512,
                    "chunk_overlap": 50,
                    "embedding_model": "text-embedding-3-small",
                    "metadata_schema": {
                        "source": "string",
                        "category": "string",
                        "version": "string",
                        "language": "string",
                    },
                },
                variables=[
                    {
                        "name": "name",
                        "type": "string",
                        "default": "技术文档库",
                        "description": "知识库名称",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="kb-customer-support",
                name="客服知识库",
                category="knowledge_base",
                description="客服FAQ和产品文档知识库，支持多轮对话上下文",
                tags=["customer", "support", "faq"],
                config={
                    "chunk_size": 256,
                    "chunk_overlap": 30,
                    "embedding_model": "text-embedding-3-small",
                    "metadata_schema": {
                        "product": "string",
                        "version": "string",
                        "category": "string",
                    },
                },
                variables=[
                    {
                        "name": "product_name",
                        "type": "string",
                        "default": "",
                        "description": "产品名称",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="workflow-doc-processing",
                name="文档处理流水线",
                category="workflow",
                description="文档采集→解析→分块→入库→索引 的标准处理流水线",
                tags=["document", "pipeline", "processing"],
                config={
                    "steps": [
                        {"name": "采集", "type": "collect", "config": {"source": "file_watch"}},
                        {"name": "解析", "type": "parse", "config": {"ocr": True}},
                        {"name": "分块", "type": "chunk", "config": {"strategy": "semantic"}},
                        {"name": "入库", "type": "store", "config": {"target": "knowledge_base"}},
                        {
                            "name": "索引",
                            "type": "index",
                            "config": {"engines": ["vector", "fulltext"]},
                        },
                    ],
                    "trigger": {"type": "file_watch", "paths": []},
                },
                variables=[
                    {
                        "name": "watch_paths",
                        "type": "list",
                        "default": [],
                        "description": "监控目录列表",
                    },
                    {
                        "name": "target_kb",
                        "type": "string",
                        "default": "",
                        "description": "目标知识库ID",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="skill-web-scraper",
                name="网页采集Skill",
                category="skill",
                description="网页数据采集和清洗Skill模板",
                tags=["web", "scraper", "collect"],
                config={
                    "url_patterns": [],
                    "selectors": {},
                    "schedule": "0 */6 * * *",
                    "output_format": "json",
                },
                variables=[
                    {
                        "name": "target_url",
                        "type": "string",
                        "default": "",
                        "description": "目标URL",
                    },
                    {
                        "name": "schedule",
                        "type": "string",
                        "default": "0 */6 * * *",
                        "description": "定时表达式",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="agent-data-analyst",
                name="数据分析Agent",
                category="agent",
                description="数据分析Agent，支持CSV/JSON数据探索、统计分析和可视化建议",
                tags=["data", "analysis", "statistics", "visualization"],
                config={
                    "system_prompt": "你是一个数据分析专家。帮助用户探索数据、发现模式、提供统计分析和可视化建议。用清晰的中文解释分析结果。",
                    "model": "gpt-4",
                    "temperature": 0.3,
                    "tools": ["file_read", "terminal", "knowledge_search"],
                },
                variables=[
                    {
                        "name": "data_source",
                        "type": "string",
                        "default": "",
                        "description": "数据源路径或描述",
                    },
                    {
                        "name": "analysis_focus",
                        "type": "string",
                        "default": "general",
                        "description": "分析重点 (general/trend/correlation/anomaly)",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="agent-translator",
                name="多语言翻译Agent",
                category="agent",
                description="专业翻译Agent，支持中英日韩多语言互译，保持术语一致性",
                tags=["translation", "multilingual", "i18n"],
                config={
                    "system_prompt": "你是一个专业翻译。准确翻译内容，保持原文风格和语气。对于专业术语，首次出现时附上原文。支持中英日韩互译。",
                    "model": "gpt-4",
                    "temperature": 0.2,
                    "tools": ["knowledge_search"],
                },
                variables=[
                    {
                        "name": "source_lang",
                        "type": "string",
                        "default": "auto",
                        "description": "源语言 (auto/zh/en/ja/ko)",
                    },
                    {
                        "name": "target_lang",
                        "type": "string",
                        "default": "en",
                        "description": "目标语言",
                    },
                    {
                        "name": "glossary_kb",
                        "type": "string",
                        "default": "",
                        "description": "术语表知识库ID",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="workflow-monitoring-alert",
                name="监控告警流水线",
                category="workflow",
                description="系统监控→阈值检测→告警通知 的自动化运维流水线",
                tags=["monitoring", "alert", "devops", "ops"],
                config={
                    "steps": [
                        {
                            "name": "采集指标",
                            "type": "collect",
                            "config": {"source": "vital", "interval_seconds": 60},
                        },
                        {
                            "name": "阈值检测",
                            "type": "check",
                            "config": {
                                "rules": [
                                    {"metric": "cpu_percent", "threshold": 90, "operator": ">"},
                                    {"metric": "memory_percent", "threshold": 85, "operator": ">"},
                                    {"metric": "disk_percent", "threshold": 90, "operator": ">"},
                                ]
                            },
                        },
                        {
                            "name": "发送告警",
                            "type": "notify",
                            "config": {"channels": ["echo"], "severity": "warning"},
                        },
                    ],
                    "trigger": {"type": "interval", "interval_seconds": 300},
                },
                variables=[
                    {
                        "name": "cpu_threshold",
                        "type": "number",
                        "default": 90,
                        "description": "CPU告警阈值 (%)",
                    },
                    {
                        "name": "memory_threshold",
                        "type": "number",
                        "default": 85,
                        "description": "内存告警阈值 (%)",
                    },
                    {
                        "name": "alert_channels",
                        "type": "list",
                        "default": ["echo"],
                        "description": "告警通知通道",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="workflow-backup-schedule",
                name="定时备份流水线",
                category="workflow",
                description="定时备份→压缩→校验→清理旧备份 的自动化灾备流水线",
                tags=["backup", "disaster-recovery", "schedule", "marrow"],
                config={
                    "steps": [
                        {
                            "name": "创建备份",
                            "type": "backup",
                            "config": {"source": "marrow", "incremental": True},
                        },
                        {"name": "完整性校验", "type": "verify", "config": {"checksum": "sha256"}},
                        {
                            "name": "清理旧备份",
                            "type": "cleanup",
                            "config": {"keep_days": 30, "keep_min": 5},
                        },
                    ],
                    "trigger": {"type": "cron", "schedule": "0 3 * * *"},
                },
                variables=[
                    {
                        "name": "backup_dirs",
                        "type": "list",
                        "default": ["~/.opensoul/data"],
                        "description": "备份目录列表",
                    },
                    {
                        "name": "keep_days",
                        "type": "number",
                        "default": 30,
                        "description": "保留天数",
                    },
                    {
                        "name": "schedule",
                        "type": "string",
                        "default": "0 3 * * *",
                        "description": "Cron表达式",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="kb-personal-notes",
                name="个人笔记知识库",
                category="knowledge_base",
                description="个人笔记和学习记录知识库，支持Markdown导入和语义搜索",
                tags=["notes", "personal", "learning", "markdown"],
                config={
                    "chunk_size": 384,
                    "chunk_overlap": 64,
                    "embedding_model": "text-embedding-3-small",
                    "metadata_schema": {
                        "topic": "string",
                        "date": "date",
                        "source": "string",
                        "tags": "list",
                    },
                    "auto_index": True,
                    "supported_formats": ["markdown", "txt", "pdf"],
                },
                variables=[
                    {
                        "name": "name",
                        "type": "string",
                        "default": "我的笔记",
                        "description": "知识库名称",
                    },
                    {
                        "name": "auto_tag",
                        "type": "boolean",
                        "default": True,
                        "description": "自动标签",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="agent-summarizer",
                name="文档摘要Agent",
                category="agent",
                description="长文档智能摘要Agent，支持多种摘要策略：要点提取、执行摘要、详细总结",
                tags=["summarize", "document", "extraction"],
                config={
                    "system_prompt": "你是一个文档摘要专家。根据用户需求生成高质量摘要。支持三种模式：1)要点提取：列出关键要点 2)执行摘要：200字以内的核心总结 3)详细总结：结构化的完整摘要。",
                    "model": "gpt-4",
                    "temperature": 0.3,
                    "tools": ["file_read", "knowledge_search"],
                },
                variables=[
                    {
                        "name": "summary_mode",
                        "type": "string",
                        "default": "executive",
                        "description": "摘要模式 (bullets/executive/detailed)",
                    },
                    {
                        "name": "max_length",
                        "type": "number",
                        "default": 500,
                        "description": "最大摘要长度（字数）",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="kb-legal-docs",
                name="法律文档知识库",
                category="knowledge_base",
                description="法律合同、法规文档知识库，支持条款检索和合规查询",
                tags=["legal", "compliance", "contract"],
                config={
                    "chunk_size": 1024,
                    "chunk_overlap": 128,
                    "embedding_model": "text-embedding-3-large",
                    "metadata_schema": {
                        "document_type": "string",
                        "jurisdiction": "string",
                        "effective_date": "date",
                        "party": "string",
                    },
                },
                variables=[
                    {
                        "name": "jurisdiction",
                        "type": "string",
                        "default": "CN",
                        "description": "法律管辖区 (CN/US/EU)",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="workflow-etl",
                name="数据ETL流水线",
                category="workflow",
                description="数据抽取→清洗→转换→加载 的标准ETL流水线",
                tags=["etl", "data", "pipeline", "transform"],
                config={
                    "steps": [
                        {
                            "name": "数据抽取",
                            "type": "extract",
                            "config": {"sources": ["csv", "json", "api"]},
                        },
                        {
                            "name": "数据清洗",
                            "type": "clean",
                            "config": {"dedup": True, "null_handle": "drop"},
                        },
                        {"name": "数据转换", "type": "transform", "config": {"normalize": True}},
                        {
                            "name": "数据加载",
                            "type": "load",
                            "config": {"target": "knowledge_base"},
                        },
                    ],
                    "trigger": {"type": "manual"},
                },
                variables=[
                    {
                        "name": "source_path",
                        "type": "string",
                        "default": "",
                        "description": "数据源路径",
                    },
                    {
                        "name": "target_kb",
                        "type": "string",
                        "default": "",
                        "description": "目标知识库ID",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="skill-api-monitor",
                name="API监控Skill",
                category="skill",
                description="定时检测API端点可用性、响应时间、状态码监控",
                tags=["api", "monitor", "health", "devops"],
                config={
                    "endpoints": [],
                    "check_interval_seconds": 300,
                    "timeout_seconds": 10,
                    "alert_on": ["timeout", "status_4xx", "status_5xx"],
                },
                variables=[
                    {
                        "name": "api_url",
                        "type": "string",
                        "default": "",
                        "description": "监控的API地址",
                    },
                    {
                        "name": "expected_status",
                        "type": "number",
                        "default": 200,
                        "description": "期望的状态码",
                    },
                    {
                        "name": "alert_channel",
                        "type": "string",
                        "default": "echo",
                        "description": "告警通知通道",
                    },
                ],
                builtin=True,
            ),
            # ── Phase 3+ Templates ──────────────────────────────────────
            Template(
                template_id="agent-bidding-collab",
                name="标书协作Agent",
                category="agent",
                description="标书协作Agent，支持多人协同编辑、自动合规检查、模板化生成投标文档",
                tags=["bidding", "collaboration", "document", "compliance"],
                config={
                    "system_prompt": "你是一个标书协作专家。帮助用户编写、审查和优化投标文档。检查合规性、格式一致性和内容完整性。支持多人协作模式。",
                    "model": "gpt-4",
                    "temperature": 0.3,
                    "tools": ["knowledge_search", "file_read", "file_write"],
                },
                variables=[
                    {
                        "name": "project_name",
                        "type": "string",
                        "default": "",
                        "description": "项目名称",
                    },
                    {
                        "name": "compliance_rules",
                        "type": "list",
                        "default": [],
                        "description": "合规检查规则列表",
                    },
                    {
                        "name": "template_doc",
                        "type": "string",
                        "default": "",
                        "description": "标书模板文档路径",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="workflow-multi-agent",
                name="多Agent协作流水线",
                category="workflow",
                description="多Agent分工协作流水线：规划→执行→验证→总结，支持并行任务和结果聚合",
                tags=["multi-agent", "collaboration", "orchestration"],
                config={
                    "steps": [
                        {
                            "name": "任务规划",
                            "type": "plan",
                            "config": {"agent": "planner", "decompose": True},
                        },
                        {
                            "name": "并行执行",
                            "type": "execute_parallel",
                            "config": {"agents": ["worker-1", "worker-2"], "timeout_seconds": 300},
                        },
                        {
                            "name": "结果验证",
                            "type": "verify",
                            "config": {
                                "agent": "verifier",
                                "criteria": ["completeness", "accuracy"],
                            },
                        },
                        {
                            "name": "汇总输出",
                            "type": "aggregate",
                            "config": {"agent": "summarizer", "format": "markdown"},
                        },
                    ],
                    "trigger": {"type": "manual"},
                    "max_iterations": 3,
                },
                variables=[
                    {
                        "name": "task_description",
                        "type": "string",
                        "default": "",
                        "description": "任务描述",
                    },
                    {
                        "name": "worker_count",
                        "type": "number",
                        "default": 2,
                        "description": "工作Agent数量",
                    },
                    {
                        "name": "output_format",
                        "type": "string",
                        "default": "markdown",
                        "description": "输出格式 (markdown/json/html)",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="skill-data-masking",
                name="数据脱敏Skill",
                category="skill",
                description="敏感数据自动脱敏Skill，支持手机号、身份证、银行卡、邮箱等多类型脱敏规则",
                tags=["security", "privacy", "masking", "compliance", "immune"],
                config={
                    "rules": [
                        {"type": "phone", "pattern": r"1[3-9]\d{9}", "mask": "***"},
                        {"type": "id_card", "pattern": r"\d{17}[\dXx]", "mask": "***"},
                        {"type": "bank_card", "pattern": r"\d{16,19}", "mask": "***"},
                        {"type": "email", "pattern": r"[\w.]+@[\w.]+", "mask": "***"},
                    ],
                    "output_mode": "replace",  # replace / annotate / remove
                    "log_original": False,
                },
                variables=[
                    {
                        "name": "input_text",
                        "type": "string",
                        "default": "",
                        "description": "待脱敏的文本",
                    },
                    {
                        "name": "custom_rules",
                        "type": "list",
                        "default": [],
                        "description": "自定义脱敏规则",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="workflow-doc-translate",
                name="文档翻译流水线",
                category="workflow",
                description="文档翻译流水线：解析→分段→翻译→校对→合并，支持多语言和术语表",
                tags=["translation", "document", "multilingual", "i18n"],
                config={
                    "steps": [
                        {"name": "文档解析", "type": "parse", "config": {"format": "auto"}},
                        {
                            "name": "智能分段",
                            "type": "segment",
                            "config": {"strategy": "paragraph", "max_length": 2000},
                        },
                        {
                            "name": "批量翻译",
                            "type": "translate",
                            "config": {"model": "gpt-4", "batch_size": 5},
                        },
                        {"name": "术语校对", "type": "review", "config": {"glossary_check": True}},
                        {
                            "name": "文档合并",
                            "type": "merge",
                            "config": {"preserve_formatting": True},
                        },
                    ],
                    "trigger": {"type": "manual"},
                },
                variables=[
                    {
                        "name": "source_lang",
                        "type": "string",
                        "default": "auto",
                        "description": "源语言",
                    },
                    {
                        "name": "target_lang",
                        "type": "string",
                        "default": "en",
                        "description": "目标语言",
                    },
                    {
                        "name": "glossary_kb",
                        "type": "string",
                        "default": "",
                        "description": "术语表知识库ID",
                    },
                    {
                        "name": "source_file",
                        "type": "string",
                        "default": "",
                        "description": "源文件路径",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="agent-customer-chatbot",
                name="客服聊天机器人",
                category="agent",
                description="智能客服Agent，基于产品知识库回答用户问题，支持多轮对话和人工转接",
                tags=["customer-service", "chatbot", "support", "faq"],
                config={
                    "system_prompt": "你是一个专业的客服助手。基于产品知识库回答用户问题。如果无法解决，建议转接人工客服。保持友好、专业的语气。",
                    "model": "gpt-4",
                    "temperature": 0.4,
                    "tools": ["knowledge_search"],
                    "max_turns": 10,
                    "handoff_threshold": 3,  # 连续无法回答次数触发转接
                },
                variables=[
                    {
                        "name": "product_name",
                        "type": "string",
                        "default": "",
                        "description": "产品名称",
                    },
                    {
                        "name": "knowledge_base_id",
                        "type": "string",
                        "default": "",
                        "description": "产品知识库ID",
                    },
                    {
                        "name": "handoff_agent",
                        "type": "string",
                        "default": "human",
                        "description": "转接目标Agent",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="kb-meeting-notes",
                name="会议纪要知识库",
                category="knowledge_base",
                description="会议纪要和讨论记录知识库，支持按参会人、日期、议题检索",
                tags=["meeting", "notes", "collaboration", "search"],
                config={
                    "chunk_size": 512,
                    "chunk_overlap": 64,
                    "embedding_model": "text-embedding-3-small",
                    "metadata_schema": {
                        "meeting_date": "date",
                        "attendees": "list",
                        "topic": "string",
                        "action_items": "list",
                        "decisions": "list",
                    },
                    "auto_extract_actions": True,
                },
                variables=[
                    {
                        "name": "team_name",
                        "type": "string",
                        "default": "",
                        "description": "团队名称",
                    },
                    {
                        "name": "auto_summary",
                        "type": "boolean",
                        "default": True,
                        "description": "自动生成会议摘要",
                    },
                ],
                builtin=True,
            ),
            Template(
                template_id="workflow-knowledge-graph",
                name="知识图谱构建流水线",
                category="workflow",
                description="文档→实体抽取→关系抽取→图谱入库 的知识图谱自动构建流水线",
                tags=["knowledge-graph", "ner", "relation", "graph"],
                config={
                    "steps": [
                        {"name": "文档预处理", "type": "parse", "config": {"chunk_size": 1000}},
                        {
                            "name": "实体抽取",
                            "type": "ner",
                            "config": {
                                "model": "gpt-4",
                                "entity_types": [
                                    "person",
                                    "organization",
                                    "location",
                                    "concept",
                                    "event",
                                ],
                            },
                        },
                        {
                            "name": "关系抽取",
                            "type": "relation",
                            "config": {
                                "model": "gpt-4",
                                "relation_types": [
                                    "works_at",
                                    "located_in",
                                    "related_to",
                                    "part_of",
                                ],
                            },
                        },
                        {
                            "name": "实体消歧",
                            "type": "disambiguate",
                            "config": {"similarity_threshold": 0.85},
                        },
                        {
                            "name": "图谱入库",
                            "type": "store_graph",
                            "config": {"target": "soul_graph", "merge_strategy": "update"},
                        },
                    ],
                    "trigger": {"type": "manual"},
                },
                variables=[
                    {
                        "name": "source_kb",
                        "type": "string",
                        "default": "",
                        "description": "源知识库ID",
                    },
                    {
                        "name": "entity_types",
                        "type": "list",
                        "default": [],
                        "description": "自定义实体类型",
                    },
                ],
                builtin=True,
            ),
        ]

        for t in builtins:
            if t.template_id not in self._templates:
                self._templates[t.template_id] = t

    def list_templates(
        self,
        category: str | None = None,
        tag: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict]:
        with self._lock:
            templates = list(self._templates.values())

        if tenant_id is not None:
            # DEC-⑦-③ 租户过滤：显式租户恒按行租户键过滤（template_row_tenant
            # 单一判法）；tenant_id=None=系统视图，既有调用面字节不变。
            templates = [t for t in templates if template_row_tenant(t) == tenant_id]
        if category:
            templates = [t for t in templates if t.category == category]
        if tag:
            templates = [t for t in templates if tag in t.tags]

        return [
            {
                "template_id": t.template_id,
                "name": t.name,
                "category": t.category,
                "description": t.description,
                "version": t.version,
                "author": t.author,
                "tags": t.tags,
                "variables": t.variables,
                "usage_count": t.usage_count,
                "builtin": t.builtin,
                # 加性契约：行归属可观测（归一值=template_row_tenant，空白显示 'default'）
                "tenant_id": template_row_tenant(t),
            }
            for t in sorted(templates, key=lambda x: x.created_at, reverse=True)
        ]

    def get_template(self, template_id: str) -> Template | None:
        with self._lock:
            return self._templates.get(template_id)

    def instantiate(self, template_id: str, variable_values: dict | None = None) -> dict:
        """Create an instance from a template with variable substitution."""
        with self._lock:
            template = self._templates.get(template_id)
        if not template:
            return {"success": False, "error": f"Template '{template_id}' not found"}

        config = copy.deepcopy(template.config)
        variables = variable_values or {}

        # Apply variable defaults
        for var in template.variables:
            name = var["name"]
            if name not in variables:
                variables[name] = var.get("default", "")

        # Simple variable substitution in config values
        config = self._substitute(config, variables)

        with self._lock:
            template.usage_count += 1

        return {
            "success": True,
            "template_id": template_id,
            "name": template.name,
            "category": template.category,
            "config": config,
            "variables": variables,
        }

    def create_template(self, data: dict, tenant_id: str | None = None) -> Template:
        """Create a new user template.（tenant_id=写点派生租户键，DEC-⑦-③；未提供
        →'default' 兜底=系统写方语义，键派生故障绝不吃掉落库。）"""
        missing = [
            f
            for f in ("name", "category")
            if not isinstance(data.get(f), str) or not data[f].strip()
        ]
        if missing:
            # 明确400语义（曾data["name"]直接KeyError→API 500）
            raise TemplateValidationError(f"missing/empty required field(s): {missing}")
        tid = data.get("template_id") or f"user-{int(time.time())}"
        _validate_template_id(tid)
        template = Template(
            template_id=tid,
            name=data["name"],
            category=data["category"],
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            author=data.get("author", "user"),
            tags=data.get("tags", []),
            config=data.get("config", {}),
            variables=data.get("variables", []),
            tenant_id=tenant_id or "",
        )
        with self._lock:
            existing = self._templates.get(template.template_id)
            if existing is not None and template_row_tenant(existing) != template_row_tenant(
                template
            ):
                # 行归属不可被跨租户改写（劫持闸门，F3b-③c-2②d 同款）：同名覆盖是
                # 既有语义，仅跨租户覆盖拒绝（行零改写）。
                raise TemplateTenantDenied(f"template_id {tid!r} belongs to another tenant")
            self._templates[template.template_id] = template
        self._save_template(template)
        return template

    def delete_template(self, template_id: str, tenant_id: str | None = None) -> bool:
        try:
            _validate_template_id(template_id)
        except TemplateValidationError:
            return False  # 非法id不触盘（路径穿越删除面）
        with self._lock:
            t = self._templates.get(template_id)
            if not t:
                return False
            if t.builtin:
                return False
            if tenant_id is not None and template_row_tenant(t) != tenant_id:
                # 跨租户删除=零副作用；回 False 与 not-found 同一契约（不差分泄露
                # 他租户行存在性，③c-1 纪律）。
                return False
            del self._templates[template_id]

        path = self.storage_dir / f"{template_id}.json"
        if path.exists():
            path.unlink()
        return True

    @staticmethod
    def _substitute(obj: Any, variables: dict) -> Any:
        if isinstance(obj, str):
            for k, v in variables.items():
                obj = obj.replace(f"{{{{{k}}}}}", str(v))
            return obj
        elif isinstance(obj, dict):
            return {k: TemplateEngine._substitute(v, variables) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [TemplateEngine._substitute(item, variables) for item in obj]
        return obj

    def categories(self) -> list[dict]:
        """List all categories with template counts."""
        with self._lock:
            cats: dict[str, int] = {}
            for t in self._templates.values():
                cats[t.category] = cats.get(t.category, 0) + 1
        return [{"category": k, "count": v} for k, v in sorted(cats.items())]

    def tags(self) -> list[dict]:
        """List all tags with usage counts."""
        with self._lock:
            tag_counts: dict[str, int] = {}
            for t in self._templates.values():
                for tag in t.tags:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        return [{"tag": k, "count": v} for k, v in sorted(tag_counts.items(), key=lambda x: -x[1])]

    def search(self, query: str) -> list[dict]:
        """Search templates by name, description, or tags."""
        q = query.lower()
        with self._lock:
            results = [
                t
                for t in self._templates.values()
                if q in t.name.lower()
                or q in t.description.lower()
                or any(q in tag.lower() for tag in t.tags)
            ]
        return [
            {
                "template_id": t.template_id,
                "name": t.name,
                "category": t.category,
                "description": t.description,
                "version": t.version,
                "author": t.author,
                "tags": t.tags,
                "variables": t.variables,
                "usage_count": t.usage_count,
                "builtin": t.builtin,
            }
            for t in sorted(results, key=lambda x: x.created_at, reverse=True)
        ]

    def export_template(self, template_id: str) -> dict | None:
        """Export a template as a JSON-serializable dict."""
        with self._lock:
            t = self._templates.get(template_id)
        if not t:
            return None
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
            "created_at": t.created_at,
            "usage_count": t.usage_count,
            "builtin": t.builtin,
        }

    def export_all(self, category: str | None = None, include_builtin: bool = True) -> list[dict]:
        """Export all templates (optionally filtered)."""
        with self._lock:
            templates = list(self._templates.values())
        if category:
            templates = [t for t in templates if t.category == category]
        if not include_builtin:
            templates = [t for t in templates if not t.builtin]
        return [
            {
                "template_id": t.template_id,
                "name": t.name,
                "category": t.category,
                "description": t.description,
                "version": t.version,
                "author": t.author,
                "tags": t.tags,
                "config": t.config,
                "variables": t.variables,
                "created_at": t.created_at,
                "usage_count": t.usage_count,
                "builtin": t.builtin,
            }
            for t in sorted(templates, key=lambda x: x.created_at, reverse=True)
        ]

    def import_template(
        self, data: dict, overwrite: bool = False, tenant_id: str | None = None
    ) -> tuple[Template | None, str]:
        """Import a template from a dict. Returns (template, message).

        tenant_id=写点派生租户键（DEC-⑦-③）；未提供→'default' 兜底=系统写方。
        """
        required = ["name", "category"]
        for req_field in required:
            if req_field not in data:
                return None, f"Missing required field: {req_field}"

        tid = data.get("template_id", f"imported-{int(time.time())}")
        try:
            _validate_template_id(tid)
        except TemplateValidationError as exc:
            return None, str(exc)  # 按import契约回(error msg)，不炸整批导入

        # Check if already exists
        with self._lock:
            existing = self._templates.get(tid)
        if existing and existing.builtin and not overwrite:
            return None, f"Cannot overwrite built-in template '{tid}'"
        if existing and not overwrite:
            return None, f"Template '{tid}' already exists. Use overwrite=true to replace."

        template = Template(
            template_id=tid,
            name=data["name"],
            category=data["category"],
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            author=data.get("author", "imported"),
            tags=data.get("tags", []),
            config=data.get("config", {}),
            variables=data.get("variables", []),
            builtin=False,  # imported templates are never builtin
            tenant_id=tenant_id or "",
        )
        if existing and template_row_tenant(existing) != template_row_tenant(template):
            # 行归属不可被跨租户改写（劫持闸门，F3b-③c-2②d 同款）：按 import 契约
            # 回 error msg 不炸整批（行零改写）。
            return None, f"Template '{tid}' belongs to another tenant"
        with self._lock:
            self._templates[tid] = template
        self._save_template(template)
        return template, f"Template '{tid}' imported successfully"

    def clone_template(
        self,
        source_id: str,
        new_id: str = "",
        new_name: str = "",
        overrides: dict | None = None,
        tenant_id: str | None = None,
    ) -> tuple[Template | None, str]:
        """Clone a template with optional overrides. Returns (template, message).

        行租户键=克隆者派生租户（DEC-⑦-③）：新行归属克隆者，不继承源行键。
        """
        with self._lock:
            source = self._templates.get(source_id)
        if not source:
            return None, f"Source template '{source_id}' not found"

        tid = new_id or f"{source_id}-copy-{int(time.time())}"
        name = new_name or f"{source.name} (副本)"

        data = {
            "template_id": tid,
            "name": name,
            "category": source.category,
            "description": source.description,
            "version": "1.0.0",
            "author": "clone",
            "tags": list(source.tags),
            "config": copy.deepcopy(source.config),
            "variables": copy.deepcopy(source.variables),
        }
        if overrides:
            data.update(overrides)

        return self.import_template(data, overwrite=True, tenant_id=tenant_id)

    def stats(self) -> dict:
        with self._lock:
            categories = {}
            for t in self._templates.values():
                categories[t.category] = categories.get(t.category, 0) + 1
            return {
                "total_templates": len(self._templates),
                "builtin_count": sum(1 for t in self._templates.values() if t.builtin),
                "user_count": sum(1 for t in self._templates.values() if not t.builtin),
                "by_category": categories,
            }
