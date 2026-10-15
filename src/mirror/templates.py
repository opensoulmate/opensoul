"""Mirror Sandbox Templates — pre-defined sandbox configurations for common testing scenarios."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SandboxTemplate:
    template_id: str
    name: str
    description: str
    icon: str
    config: dict
    variables: dict
    tags: list[str]
    category: str  # "workflow", "agent", "connector", "custom"
    usage_count: int = 0
    created_at: float = 0
    tenant_id: str = "default"


DEFAULT_TEMPLATES = [
    {
        "template_id": "stpl-workflow-test",
        "name": "工作流测试沙箱",
        "description": "用于测试工作流的隔离环境，预设工作流相关变量",
        "icon": "⚡",
        "config": {"ttl_seconds": 7200, "auto_snapshot": True},
        "variables": {"workflow_id": "", "trigger_type": "manual", "test_mode": "true"},
        "tags": ["workflow", "test"],
        "category": "workflow",
    },
    {
        "template_id": "stpl-agent-debug",
        "name": "Agent调试沙箱",
        "description": "用于调试Agent行为的隔离环境，启用详细日志",
        "icon": "🤖",
        "config": {"ttl_seconds": 3600, "log_level": "debug"},
        "variables": {"agent_id": "", "debug_mode": "true", "max_steps": "10"},
        "tags": ["agent", "debug"],
        "category": "agent",
    },
    {
        "template_id": "stpl-connector-test",
        "name": "连接器测试沙箱",
        "description": "用于测试外部连接器的沙箱，模拟外部系统响应",
        "icon": "🔌",
        "config": {"ttl_seconds": 1800},
        "variables": {"connector_type": "", "mock_response": "true", "endpoint": ""},
        "tags": ["connector", "integration"],
        "category": "connector",
    },
    {
        "template_id": "stpl-knowledge-import",
        "name": "知识导入沙箱",
        "description": "用于测试知识导入流程的隔离环境",
        "icon": "📚",
        "config": {"ttl_seconds": 3600},
        "variables": {"source_type": "", "auto_tag": "true", "dedup_enabled": "true"},
        "tags": ["knowledge", "import"],
        "category": "workflow",
    },
    {
        "template_id": "stpl-prompt-experiment",
        "name": "Prompt实验沙箱",
        "description": "用于测试不同Prompt策略的隔离环境",
        "icon": "🧪",
        "config": {"ttl_seconds": 1800, "log_level": "debug"},
        "variables": {
            "model": "",
            "temperature": "0.7",
            "max_tokens": "2000",
            "system_prompt": "",
        },
        "tags": ["prompt", "experiment"],
        "category": "agent",
    },
    {
        "template_id": "stpl-api-stress",
        "name": "API压测沙箱",
        "description": "用于API压力测试的隔离环境",
        "icon": "🏋️",
        "config": {"ttl_seconds": 900},
        "variables": {"endpoint": "", "concurrent": "10", "total_requests": "100"},
        "tags": ["api", "stress-test"],
        "category": "connector",
    },
]


BUILTIN_TEMPLATE_IDS = frozenset(t["template_id"] for t in DEFAULT_TEMPLATES)


def template_row_tenant(raw: object) -> str:
    """行租户键单一判法（DEC-⑦-㉒b；DEC-⑦-② message_row_tenant / ⑦-㉑ echo
    template_row_tenant 同款语义：自带键缺失/空白→'default' 兜底，键派生故障
    绝不吃掉落库）。raw=SandboxTemplate 行对象（读侧过滤）或原始租户值（写侧
    落行），写读同一判法禁双源漂移（F4b-㉗ 纪律）。"""
    if hasattr(raw, "tenant_id"):
        raw = getattr(raw, "tenant_id")
    return str(raw or "").strip() or "default"


def is_builtin(template_id: str) -> bool:
    """builtin 判法单一源（DEC-⑦-㉒b）：种子 starter 目录=DEFAULT_TEMPLATES 派生
    id 集（禁第二套名单手抄漂移 F4b-㉗ 纪律），=系统共享只读目录（DEC-⑩-① 语义）。"""
    return template_id in BUILTIN_TEMPLATE_IDS


def template_visible_to(template: SandboxTemplate, tenant_id: str) -> bool:
    """读面可见性单一判法（DEC-⑦-㉒b；DEC-⑩-① builtin starter 目录跨租户共享
    语义）：builtin=系统共享只读目录对全租户可见，其余行恒按行租户键（内部走
    template_row_tenant 单一判法禁另发明 F4b-㉗ 纪律）。写面不适用本判法——
    delete 恒按行归属+builtin 保护（共享≠可写），写读判法刻意分开两个单一源
    （echo/gene templates.py template_visible_to 同款）。"""
    return is_builtin(template.template_id) or template_row_tenant(template) == tenant_id


class SandboxTemplateEngine:
    """Manage sandbox templates for quick sandbox creation."""

    def __init__(self, db_path: str | None = None):
        # 库路径单一解析（DEC-⑦-㉒b 测试隔离增补，ECHO_TEMPLATES_DB 先例）：
        # MIRROR_TEMPLATES_DB env 优先——src.api.mirror 模块级 template_engine
        # 单例 import 即构造缺省解析生产 data/mirror/templates.db，测试进程的
        # import 会把迁移 SQL 提前触发进生产库（⑦-㉑ Gate3 同型缺口）；缺省
        # 语义不变（服务无该 env=生产路径）。
        db = (
            db_path
            or os.environ.get("MIRROR_TEMPLATES_DB")
            or os.path.expanduser("~/opensoul/data/mirror/templates.db")
        )
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()
        self._seed_defaults()

    def _init_db(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS sandbox_templates (
                template_id    TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                description    TEXT DEFAULT '',
                icon           TEXT DEFAULT '🧪',
                config         TEXT DEFAULT '{}',
                variables      TEXT DEFAULT '{}',
                tags           TEXT DEFAULT '[]',
                category       TEXT DEFAULT 'custom',
                usage_count    INTEGER DEFAULT 0,
                created_at     REAL NOT NULL,
                tenant_id      TEXT NOT NULL DEFAULT 'default'
            );
            CREATE INDEX IF NOT EXISTS idx_sboxtpl_cat ON sandbox_templates(category);
        """)
        # 老库幂等自愈（C1 模式，DEC-⑦-㉒b）：缺 tenant_id 列才 ALTER 补列
        # ——存量行随 NOT NULL DEFAULT 'default' 回填（F3b/DEC-⑦ 家族语义）；
        # 租户索引必须在补列之后建（③c-2① 坑：老库形态先建索引=no such column）。
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(sandbox_templates)")}
        if "tenant_id" not in cols:
            self._db.execute(
                "ALTER TABLE sandbox_templates ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sboxtpl_tenant ON sandbox_templates(tenant_id)"
        )
        self._db.commit()

    def _seed_defaults(self):
        count = self._db.execute("SELECT COUNT(*) FROM sandbox_templates").fetchone()[0]
        if count > 0:
            return

        defaults = DEFAULT_TEMPLATES

        now = time.time()
        for tpl in defaults:
            self._db.execute(
                """INSERT OR IGNORE INTO sandbox_templates
                   (template_id, name, description, icon, config, variables, tags, category, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tpl["template_id"],
                    tpl["name"],
                    tpl["description"],
                    tpl["icon"],
                    json.dumps(tpl["config"]),
                    json.dumps(tpl["variables"]),
                    json.dumps(tpl["tags"]),
                    tpl["category"],
                    now,
                ),
            )
        self._db.commit()

    def create(
        self,
        name: str,
        description: str = "",
        icon: str = "🧪",
        config: dict | None = None,
        variables: dict | None = None,
        tags: list[str] | None = None,
        category: str = "custom",
        tenant_id: str = "",
    ) -> SandboxTemplate:
        """Create a new sandbox template.（tenant_id=写点派生租户键 DEC-⑦-㉒b：
        未提供/空白→'default' 兜底=系统写方语义，键派生故障绝不吃掉落库。）"""
        template_id = f"stpl-{int(time.time())}-{hash(name) % 10000:04d}"
        now = time.time()
        row_tenant = template_row_tenant(tenant_id)

        self._db.execute(
            """INSERT INTO sandbox_templates
               (template_id, name, description, icon, config, variables, tags, category, created_at, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                template_id,
                name,
                description,
                icon,
                json.dumps(config or {}),
                json.dumps(variables or {}),
                json.dumps(tags or []),
                category,
                now,
                row_tenant,
            ),
        )
        self._db.commit()

        return SandboxTemplate(
            template_id=template_id,
            name=name,
            description=description,
            icon=icon,
            config=config or {},
            variables=variables or {},
            tags=tags or [],
            category=category,
            created_at=now,
            tenant_id=row_tenant,
        )

    @staticmethod
    def _row_to_tpl(row) -> SandboxTemplate:
        """行→对象单一映射（写读同源：tenant_id 归一走 template_row_tenant
        单一判法，get/list/stats 共用禁各处手抄）。"""
        return SandboxTemplate(
            template_id=row["template_id"],
            name=row["name"],
            description=row["description"],
            icon=row["icon"],
            config=json.loads(row["config"]),
            variables=json.loads(row["variables"]),
            tags=json.loads(row["tags"]),
            category=row["category"],
            usage_count=row["usage_count"],
            created_at=row["created_at"],
            tenant_id=template_row_tenant(row["tenant_id"]),
        )

    def _raw_get(self, template_id: str) -> SandboxTemplate | None:
        row = self._db.execute(
            "SELECT * FROM sandbox_templates WHERE template_id = ?", (template_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_tpl(row)

    def get(self, template_id: str, tenant_id: str | None = None) -> SandboxTemplate | None:
        """by-ID 读面归属闸（DEC-⑦-㉒b）：tenant_id=None=系统视图字节不变；
        显式租户=template_visible_to 可见性闸（builtin 共享目录+行归属），
        不可见回 None 与 not-found 同契约不差分泄露行存在性（③c-1 纪律）。
        instantiate 源读全经本口=未来消费方无法绕闸。"""
        tpl = self._raw_get(template_id)
        if tpl is None:
            return None
        if tenant_id is not None and not template_visible_to(tpl, tenant_id):
            return None
        return tpl

    def list_templates(
        self, category: str | None = None, tenant_id: str | None = None
    ) -> list[dict]:
        """读面加轴（DEC-⑦-㉒b）：tenant_id=None=系统视图字节不变；显式
        租户=恒按 template_visible_to 可见集过滤（builtin 共享目录+行归属，
        ⑩-① 语义）。输出加性键 tenant_id/builtin（行归属可观测，⑦-③ 同款）。"""
        query = "SELECT * FROM sandbox_templates"
        params: list = []
        if category:
            query += " WHERE category = ?"
            params.append(category)
        query += " ORDER BY usage_count DESC, created_at DESC"
        rows = self._db.execute(query, params).fetchall()
        tpls = [self._row_to_tpl(r) for r in rows]
        if tenant_id is not None:
            tpls = [t for t in tpls if template_visible_to(t, tenant_id)]
        return [
            {
                "template_id": t.template_id,
                "name": t.name,
                "description": t.description,
                "icon": t.icon,
                "config": t.config,
                "variables": t.variables,
                "tags": t.tags,
                "category": t.category,
                "usage_count": t.usage_count,
                "created_at": t.created_at,
                "tenant_id": template_row_tenant(t),
                "builtin": is_builtin(t.template_id),
            }
            for t in tpls
        ]

    def delete(self, template_id: str, tenant_id: str | None = None) -> bool:
        """写面归属闸（DEC-⑦-㉒b）：builtin=系统共享只读目录恒拒（共享≠可写，
        ⑦-㉑ echo delete builtin 恒拒同款）；显式租户=跨租户回 False 与
        not-found 同契约不差分泄露（③c-1）；None=系统视图（内部调用面）。"""
        tpl = self._raw_get(template_id)
        if not tpl:
            return False
        if is_builtin(tpl.template_id):
            return False
        if tenant_id is not None and template_row_tenant(tpl) != tenant_id:
            return False
        cursor = self._db.execute(
            "DELETE FROM sandbox_templates WHERE template_id = ?", (template_id,)
        )
        self._db.commit()
        return cursor.rowcount > 0

    def record_usage(self, template_id: str):
        self._db.execute(
            "UPDATE sandbox_templates SET usage_count = usage_count + 1 WHERE template_id = ?",
            (template_id,),
        )
        self._db.commit()

    def instantiate(
        self,
        template_id: str,
        overrides: dict | None = None,
        tenant_id: str | None = None,
    ) -> dict | None:
        """Get template config for creating a sandbox from it.（DEC-⑦-㉒b：
        源读走 get 单点归属闸——跨租户/不可见回 None=not-found 同契约零副作用
        （usage 记账在闸后=闸拒不记账）；usage 计数照旧=builtin 共享目录使用
        计数是系统计数非租户内容写入。）"""
        tpl = self.get(template_id, tenant_id=tenant_id)
        if not tpl:
            return None

        self.record_usage(template_id)

        config = {**tpl.config}
        variables = {**tpl.variables}
        if overrides:
            variables.update(overrides)

        return {
            "name": f"{tpl.icon} {tpl.name}",
            "description": tpl.description,
            "config": config,
            "variables": variables,
            "tags": tpl.tags,
            "ttl_seconds": config.get("ttl_seconds", 3600),
        }

    def stats(self, tenant_id: str | None = None) -> dict:
        """聚合面加轴（DEC-⑦-㉒b，⑦-⑭⑰ 同款）：tenant_id=None=系统视图
        全量（/health 监控面字节不变）；显式租户=恒按 template_visible_to
        可见集聚合（禁泄露全局分布）。"""
        rows = self._db.execute("SELECT * FROM sandbox_templates").fetchall()
        tpls = [self._row_to_tpl(r) for r in rows]
        if tenant_id is not None:
            tpls = [t for t in tpls if template_visible_to(t, tenant_id)]
        by_category = {}
        for t in tpls:
            by_category[t.category] = by_category.get(t.category, 0) + 1
        return {
            "total_templates": len(tpls),
            "by_category": by_category,
        }
