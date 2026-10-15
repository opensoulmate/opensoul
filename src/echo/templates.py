"""Echo Message Templates — pre-defined notification templates with variable substitution."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MessageTemplate:
    template_id: str
    name: str
    description: str
    channel: str  # preferred channel, or "any"
    title_template: str
    content_template: str
    variables: list[str]  # extracted {{var}} names
    category: str  # "system", "task", "alert", "custom"
    icon: str  # emoji
    created_at: float
    usage_count: int = 0
    last_used: float = 0
    tenant_id: str = "default"


DEFAULT_TEMPLATES = [
    {
        "template_id": "tpl-task-complete",
        "name": "任务完成通知",
        "description": "当Agent完成任务时发送通知",
        "channel": "any",
        "title_template": "✅ 任务完成: {{task_name}}",
        "content_template": "任务 **{{task_name}}** 已完成。\n\n- 耗时: {{duration}}\n- 结果: {{result}}\n- Agent: {{agent}}",
        "category": "task",
        "icon": "✅",
    },
    {
        "template_id": "tpl-task-failed",
        "name": "任务失败告警",
        "description": "当Agent任务失败时发送告警",
        "channel": "any",
        "title_template": "❌ 任务失败: {{task_name}}",
        "content_template": "任务 **{{task_name}}** 执行失败。\n\n- 错误: {{error}}\n- Agent: {{agent}}\n- 重试次数: {{retry_count}}",
        "category": "alert",
        "icon": "❌",
    },
    {
        "template_id": "tpl-system-health",
        "name": "系统健康报告",
        "description": "定期系统健康状态报告",
        "channel": "any",
        "title_template": "💚 系统健康报告",
        "content_template": "## 系统状态: {{status}}\n\n- CPU: {{cpu}}%\n- 内存: {{memory}}%\n- 器官在线: {{organs_online}}/{{organs_total}}\n- 知识库条目: {{knowledge_count}}",
        "category": "system",
        "icon": "💚",
    },
    {
        "template_id": "tpl-knowledge-added",
        "name": "知识入库通知",
        "description": "新知识条目添加到知识库时通知",
        "channel": "any",
        "title_template": "📚 新知识入库: {{title}}",
        "content_template": "新知识条目已添加到知识库。\n\n- 标题: {{title}}\n- 来源: {{source}}\n- 标签: {{tags}}\n- 字数: {{word_count}}",
        "category": "system",
        "icon": "📚",
    },
    {
        "template_id": "tpl-backup-complete",
        "name": "备份完成通知",
        "description": "定时备份完成时发送通知",
        "channel": "any",
        "title_template": "🦴 备份完成",
        "content_template": "系统备份已完成。\n\n- 备份大小: {{size}}\n- 耗时: {{duration}}\n- 备份路径: {{path}}",
        "category": "system",
        "icon": "🦴",
    },
    {
        "template_id": "tpl-security-alert",
        "name": "安全告警",
        "description": "安全事件触发时发送告警",
        "channel": "any",
        "title_template": "🛡️ 安全告警: {{alert_type}}",
        "content_template": "安全事件检测到。\n\n- 类型: {{alert_type}}\n- 来源IP: {{source_ip}}\n- 详情: {{detail}}\n- 时间: {{timestamp}}",
        "category": "alert",
        "icon": "🛡️",
    },
    {
        "template_id": "tpl-daily-digest",
        "name": "每日摘要",
        "description": "每日活动摘要报告",
        "channel": "any",
        "title_template": "📊 每日摘要 — {{date}}",
        "content_template": "## 今日活动摘要\n\n- 新知识: {{new_knowledge}}条\n- Agent任务: {{tasks_completed}}完成 / {{tasks_failed}}失败\n- 消息推送: {{messages_sent}}条\n- 活跃用户: {{active_users}}人",
        "category": "system",
        "icon": "📊",
    },
    {
        "template_id": "tpl-workflow-trigger",
        "name": "工作流触发通知",
        "description": "工作流被触发时发送通知",
        "channel": "any",
        "title_template": "⚡ 工作流触发: {{workflow_name}}",
        "content_template": "工作流 **{{workflow_name}}** 已触发。\n\n- 触发条件: {{trigger}}\n- 执行步骤: {{steps}}\n- 预计耗时: {{estimated_time}}",
        "category": "task",
        "icon": "⚡",
    },
]


def template_row_tenant(raw: object) -> str:
    """行租户键单一判法（DEC-⑦-㉑；DEC-⑦-② message_row_tenant 同款语义：自带
    键缺失/空白→'default' 兜底）。raw=MessageTemplate 行对象（读侧过滤）或原始
    租户值（写侧落行），写读同一判法禁双源漂移（F4b-㉗ 纪律）。
    """
    if hasattr(raw, "tenant_id"):
        raw = getattr(raw, "tenant_id")
    return str(raw or "").strip() or "default"


BUILTIN_TEMPLATE_IDS = frozenset(t["template_id"] for t in DEFAULT_TEMPLATES)


def is_builtin(template_id: str) -> bool:
    """builtin 判法单一源（DEC-⑦-㉑）：种子 starter 目录=DEFAULT_TEMPLATES 派生
    id 集（禁第二套名单手抄漂移），=系统共享只读目录（DEC-⑩-① 语义）。"""
    return template_id in BUILTIN_TEMPLATE_IDS


def template_visible_to(template: MessageTemplate, tenant_id: str) -> bool:
    """读面可见性单一判法（DEC-⑦-㉑；DEC-⑩-① builtin starter 目录跨租户共享
    语义）：builtin=系统共享只读目录对全租户可见，其余行恒按行租户键（内部走
    template_row_tenant 单一判法禁另发明 F4b-㉗ 纪律）。写面不适用本判法——
    update/delete 恒按行归属+builtin 保护（共享≠可写），写读判法刻意分开两个
    单一源（gene templates.py template_visible_to 同款）。
    """
    return is_builtin(template.template_id) or template_row_tenant(template) == tenant_id


class TemplateEngine:
    """Manage and render message templates with {{variable}} substitution."""

    def __init__(self, db_path: str | None = None):
        # 库路径单一解析（DEC-⑦-㉑ 测试隔离增补，TIMELINE_DB/PROACTIVE_DB 先例）：
        # ECHO_TEMPLATES_DB env 优先——src.api.echo 模块级 template_engine 单例
        # import 即构造缺省解析生产 data/echo/templates.db，测试进程的 import 曾
        # 把本拍迁移 SQL 提前触发进生产库（Gate3 逼出的测试写生产缺口）；缺省
        # 语义不变（服务无该 env=生产路径）。
        db = (
            db_path
            or os.environ.get("ECHO_TEMPLATES_DB")
            or os.path.expanduser("~/opensoul/data/echo/templates.db")
        )
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()
        self._seed_defaults()

    def _init_db(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS templates (
                template_id    TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                description    TEXT DEFAULT '',
                channel        TEXT DEFAULT 'any',
                title_template TEXT NOT NULL,
                content_template TEXT NOT NULL,
                variables      TEXT DEFAULT '[]',
                category       TEXT DEFAULT 'custom',
                icon           TEXT DEFAULT '📨',
                created_at     REAL NOT NULL,
                usage_count    INTEGER DEFAULT 0,
                last_used      REAL DEFAULT 0,
                tenant_id      TEXT NOT NULL DEFAULT 'default'
            );
            CREATE INDEX IF NOT EXISTS idx_templates_cat ON templates(category);
        """)
        # 老库幂等自愈（C1 模式，DEC-⑦-㉑）：缺 tenant_id 列才 ALTER 补列——
        # 存量行随 NOT NULL DEFAULT 'default' 回填（F3b/DEC-⑦ 家族语义）；租户
        # 索引必须在补列之后建（③c-2① 坑：老库形态先建索引=no such column）。
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(templates)")}
        if "tenant_id" not in cols:
            self._db.execute(
                "ALTER TABLE templates ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_templates_tenant ON templates(tenant_id)")
        self._db.commit()

    def _seed_defaults(self):
        """Seed built-in templates if table is empty."""
        count = self._db.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        if count > 0:
            return

        now = time.time()
        for tpl in DEFAULT_TEMPLATES:
            variables = self._extract_variables(
                tpl["title_template"] + " " + tpl["content_template"]
            )
            self._db.execute(
                """INSERT OR IGNORE INTO templates
                   (template_id, name, description, channel, title_template, content_template,
                    variables, category, icon, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tpl["template_id"],
                    tpl["name"],
                    tpl["description"],
                    tpl["channel"],
                    tpl["title_template"],
                    tpl["content_template"],
                    json.dumps(variables),
                    tpl["category"],
                    tpl["icon"],
                    now,
                ),
            )
        self._db.commit()

    @staticmethod
    def _extract_variables(text: str) -> list[str]:
        """Extract {{variable}} names from template text."""
        return list(set(re.findall(r"\{\{(\w+)\}\}", text)))

    @staticmethod
    def render(template_text: str, variables: dict[str, Any]) -> str:
        """Render a template by substituting {{variables}}."""

        def replacer(match):
            key = match.group(1)
            return str(variables.get(key, match.group(0)))

        return re.sub(r"\{\{(\w+)\}\}", replacer, template_text)

    def create(
        self,
        name: str,
        title_template: str,
        content_template: str,
        description: str = "",
        channel: str = "any",
        category: str = "custom",
        icon: str = "📨",
        tenant_id: str = "",
    ) -> MessageTemplate:
        """Create a new template.（tenant_id=写点派生租户键 DEC-⑦-㉑：未提供/
        空白→'default' 兜底=系统写方语义，键派生故障绝不吃掉落库。）"""
        template_id = f"tpl-{int(time.time())}-{hash(name) % 10000:04d}"
        row_tenant = template_row_tenant(tenant_id)
        variables = self._extract_variables(title_template + " " + content_template)
        now = time.time()

        self._db.execute(
            """INSERT INTO templates
               (template_id, name, description, channel, title_template, content_template,
                variables, category, icon, created_at, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                template_id,
                name,
                description,
                channel,
                title_template,
                content_template,
                json.dumps(variables),
                category,
                icon,
                now,
                row_tenant,
            ),
        )
        self._db.commit()

        return MessageTemplate(
            template_id=template_id,
            name=name,
            description=description,
            channel=channel,
            title_template=title_template,
            content_template=content_template,
            variables=variables,
            category=category,
            icon=icon,
            created_at=now,
            tenant_id=row_tenant,
        )

    @staticmethod
    def _row_to_tpl(row) -> MessageTemplate:
        """行→对象单一映射（写读同源：tenant_id 归一走 template_row_tenant 单一
        判法，get/list/stats 共用禁各处手抄）。"""
        return MessageTemplate(
            template_id=row["template_id"],
            name=row["name"],
            description=row["description"],
            channel=row["channel"],
            title_template=row["title_template"],
            content_template=row["content_template"],
            variables=json.loads(row["variables"]),
            category=row["category"],
            icon=row["icon"],
            created_at=row["created_at"],
            usage_count=row["usage_count"],
            last_used=row["last_used"],
            tenant_id=template_row_tenant(row["tenant_id"]),
        )

    def _raw_get(self, template_id: str) -> MessageTemplate | None:
        row = self._db.execute(
            "SELECT * FROM templates WHERE template_id = ?", (template_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_tpl(row)

    def get(self, template_id: str, tenant_id: str | None = None) -> MessageTemplate | None:
        """by-ID 读面归属闸（DEC-⑦-㉑）：tenant_id=None=系统视图字节不变；显式
        租户=template_visible_to 可见性闸（builtin 共享+行归属），不可见回 None
        与 not-found 同契约不差分泄露行存在性（③c-1 纪律）。"""
        tpl = self._raw_get(template_id)
        if tpl is None:
            return None
        if tenant_id is not None and not template_visible_to(tpl, tenant_id):
            return None
        return tpl

    def list_templates(
        self, category: str | None = None, tenant_id: str | None = None
    ) -> list[dict]:
        """读面加轴（DEC-⑦-㉑）：tenant_id=None=系统视图字节不变；显式租户=恒按
        template_visible_to 可见集过滤（builtin 共享目录+行归属，⑩-① 语义）。
        输出加性键 tenant_id/builtin（行归属可观测，⑦-③ 同款）。"""
        query = "SELECT * FROM templates"
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
                "channel": t.channel,
                "title_template": t.title_template,
                "content_template": t.content_template,
                "variables": t.variables,
                "category": t.category,
                "icon": t.icon,
                "usage_count": t.usage_count,
                "last_used": t.last_used,
                "created_at": t.created_at,
                "tenant_id": template_row_tenant(t),
                "builtin": is_builtin(t.template_id),
            }
            for t in tpls
        ]

    def delete(self, template_id: str, tenant_id: str | None = None) -> bool:
        """写面归属闸（DEC-⑦-㉑）：builtin=系统共享只读目录恒拒（共享≠可写，
        DEC-⑩-① gene delete builtin 恒拒同款）；显式租户=跨租户回 False 与
        not-found 同契约不差分泄露（③c-1）；None=系统视图（内部调用面）。"""
        tpl = self._raw_get(template_id)
        if not tpl:
            return False
        if is_builtin(tpl.template_id):
            return False
        if tenant_id is not None and template_row_tenant(tpl) != tenant_id:
            return False
        cursor = self._db.execute("DELETE FROM templates WHERE template_id = ?", (template_id,))
        self._db.commit()
        return cursor.rowcount > 0

    def update(self, template_id: str, tenant_id: str | None = None, **kwargs) -> bool:
        """写面归属闸（DEC-⑦-㉑）：builtin 恒拒（共享≠可写）、跨租户=False 与
        not-found 同契约（delete 同款）；tenant_id 不在 allowed 集=UPDATE 粘性
        不洗键（行归属=首次写入）；None=系统视图（内部调用面）。"""
        tpl = self._raw_get(template_id)
        if not tpl:
            return False
        if is_builtin(tpl.template_id):
            return False
        if tenant_id is not None and template_row_tenant(tpl) != tenant_id:
            return False

        allowed = {
            "name",
            "description",
            "channel",
            "title_template",
            "content_template",
            "category",
            "icon",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False

        # Recalculate variables if templates changed
        if "title_template" in updates or "content_template" in updates:
            title = updates.get("title_template", tpl.title_template)
            content = updates.get("content_template", tpl.content_template)
            updates["variables"] = json.dumps(self._extract_variables(title + " " + content))

        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [template_id]
        self._db.execute(f"UPDATE templates SET {sets} WHERE template_id = ?", values)
        self._db.commit()
        return True

    def record_usage(self, template_id: str):
        """Record that a template was used."""
        self._db.execute(
            "UPDATE templates SET usage_count = usage_count + 1, last_used = ? WHERE template_id = ?",
            (time.time(), template_id),
        )
        self._db.commit()

    def render_template(
        self, template_id: str, variables: dict[str, Any], tenant_id: str | None = None
    ) -> dict | None:
        """Render a template with variables, returning title and content.
        （DEC-⑦-㉑：读可见性闸同 get——跨租户/不可见回 None=not-found 同契约；
        usage 记账照旧=builtin 共享目录使用计数是系统计数非租户内容写入。）"""
        tpl = self.get(template_id, tenant_id=tenant_id)
        if not tpl:
            return None

        self.record_usage(template_id)

        return {
            "title": self.render(tpl.title_template, variables),
            "content": self.render(tpl.content_template, variables),
            "channel": tpl.channel,
            "template_id": template_id,
            "template_name": tpl.name,
        }

    def stats(self, tenant_id: str | None = None) -> dict:
        """聚合面加轴（DEC-⑦-㉑，闭 DEC-⑦-⑭ 诚实边界「templates 聚合无行租户
        键」）：tenant_id=None=系统视图全量（/health 监控面字节不变）；显式租户
        =恒按 template_visible_to 可见集聚合（禁泄露全局分布，⑦-⑭ 同款）。"""
        rows = self._db.execute("SELECT * FROM templates").fetchall()
        tpls = [self._row_to_tpl(r) for r in rows]
        if tenant_id is not None:
            tpls = [t for t in tpls if template_visible_to(t, tenant_id)]
        by_category: dict = {}
        for t in tpls:
            by_category[t.category] = by_category.get(t.category, 0) + 1
        return {
            "total_templates": len(tpls),
            "by_category": by_category,
            "total_usage": sum(t.usage_count for t in tpls),
        }
