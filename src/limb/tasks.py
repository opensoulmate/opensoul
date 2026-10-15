"""RPA Task definitions — actions, steps, and workflow sequences.

Defines the building blocks for RPA automation:
- Single actions (click, type, navigate, screenshot, etc.)
- Step sequences (ordered list of actions)
- Templates for common workflows (form fill, data extraction, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ActionType(StrEnum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    WAIT = "wait"
    SCREENSHOT = "screenshot"
    EXTRACT = "extract"
    SCROLL = "scroll"
    KEY_PRESS = "key_press"
    HOVER = "hover"
    SUBMIT = "submit"
    CONDITIONAL = "conditional"
    CUSTOM = "custom"


@dataclass
class Action:
    """A single RPA action."""

    action_type: ActionType
    target: str = ""  # CSS selector, URL, or element identifier
    value: str = ""  # Text to type, option to select, etc.
    description: str = ""
    timeout: int = 30  # seconds
    retry: int = 0  # number of retries on failure
    optional: bool = False  # if True, failure doesn't stop execution
    condition: str = ""  # for conditional actions: JS expression
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type.value,
            "target": self.target,
            "value": self.value,
            "description": self.description,
            "timeout": self.timeout,
            "retry": self.retry,
            "optional": self.optional,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Action:
        return cls(
            action_type=ActionType(data.get("action_type", "custom")),
            target=data.get("target", ""),
            value=data.get("value", ""),
            description=data.get("description", ""),
            timeout=data.get("timeout", 30),
            retry=data.get("retry", 0),
            optional=data.get("optional", False),
            condition=data.get("condition", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class StepResult:
    """Result of executing a single step."""

    action_type: str
    success: bool
    action_index: int = 0
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    screenshot_path: str = ""


@dataclass
class TaskTemplate:
    """Reusable RPA task template."""

    template_id: str
    name: str
    description: str
    category: str  # "form_fill", "data_extract", "navigation", "custom"
    actions: list[Action]
    variables: list[dict] = field(default_factory=list)  # [{name, description, default}]
    tags: list[str] = field(default_factory=list)
    tenant_id: str = "default"

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "action_count": len(self.actions),
            "actions": [a.to_dict() for a in self.actions],
            "variables": self.variables,
            "tags": self.tags,
            # 加性键（DEC-⑦-㉒）：行归属可观测（归一值）+builtin 可见性标记
            "tenant_id": template_row_tenant(self),
            "builtin": is_builtin(self.template_id),
        }


# ── Built-in Templates ───────────────────────────────────

BUILTIN_TEMPLATES = [
    TaskTemplate(
        template_id="tpl-form-fill",
        name="表单填写",
        description="自动填写网页表单",
        category="form_fill",
        actions=[
            Action(ActionType.NAVIGATE, target="{{url}}", description="打开目标页面"),
            Action(ActionType.WAIT, target="form", timeout=10, description="等待表单加载"),
            Action(
                ActionType.TYPE,
                target="{{field_selector}}",
                value="{{field_value}}",
                description="填写字段",
            ),
            Action(ActionType.SUBMIT, target="form", description="提交表单"),
            Action(ActionType.SCREENSHOT, description="截图确认"),
        ],
        variables=[
            {"name": "url", "description": "目标URL", "default": ""},
            {"name": "field_selector", "description": "字段CSS选择器", "default": ""},
            {"name": "field_value", "description": "填写内容", "default": ""},
        ],
        tags=["form", "automation"],
    ),
    TaskTemplate(
        template_id="tpl-data-extract",
        name="数据提取",
        description="从网页提取结构化数据",
        category="data_extract",
        actions=[
            Action(ActionType.NAVIGATE, target="{{url}}", description="打开目标页面"),
            Action(
                ActionType.WAIT,
                target="{{container_selector}}",
                timeout=10,
                description="等待内容加载",
            ),
            Action(ActionType.EXTRACT, target="{{container_selector}}", description="提取数据"),
        ],
        variables=[
            {"name": "url", "description": "目标URL", "default": ""},
            {"name": "container_selector", "description": "数据容器CSS选择器", "default": ""},
        ],
        tags=["extract", "scraping"],
    ),
    TaskTemplate(
        template_id="tpl-login",
        name="自动登录",
        description="自动化登录流程",
        category="navigation",
        actions=[
            Action(ActionType.NAVIGATE, target="{{login_url}}", description="打开登录页"),
            Action(
                ActionType.WAIT,
                target="input[type='text'], input[type='email']",
                timeout=10,
                description="等待登录表单",
            ),
            Action(
                ActionType.TYPE,
                target="{{username_selector}}",
                value="{{username}}",
                description="输入用户名",
            ),
            Action(
                ActionType.TYPE,
                target="{{password_selector}}",
                value="{{password}}",
                description="输入密码",
            ),
            Action(ActionType.CLICK, target="{{submit_selector}}", description="点击登录"),
            Action(ActionType.WAIT, timeout=5, description="等待登录完成"),
            Action(ActionType.SCREENSHOT, description="截图确认"),
        ],
        variables=[
            {"name": "login_url", "description": "登录页URL", "default": ""},
            {"name": "username", "description": "用户名", "default": ""},
            {"name": "password", "description": "密码", "default": ""},
            {
                "name": "username_selector",
                "description": "用户名输入框",
                "default": "input[type='text']",
            },
            {
                "name": "password_selector",
                "description": "密码输入框",
                "default": "input[type='password']",
            },
            {
                "name": "submit_selector",
                "description": "提交按钮",
                "default": "button[type='submit']",
            },
        ],
        tags=["login", "auth"],
    ),
    TaskTemplate(
        template_id="tpl-screenshot",
        name="页面截图",
        description="截取网页完整或区域截图",
        category="custom",
        actions=[
            Action(ActionType.NAVIGATE, target="{{url}}", description="打开目标页面"),
            Action(ActionType.WAIT, timeout=5, description="等待页面加载"),
            Action(ActionType.SCREENSHOT, target="{{selector}}", description="截图"),
        ],
        variables=[
            {"name": "url", "description": "目标URL", "default": ""},
            {"name": "selector", "description": "截图区域选择器 (可选)", "default": ""},
        ],
        tags=["screenshot", "capture"],
    ),
]


class TemplateTenantDenied(Exception):
    """跨租户/builtin 写拒（行归属不可被跨租户改写、共享≠可写）：API 层映射 HTTP 403。"""


BUILTIN_TEMPLATE_IDS = frozenset(t.template_id for t in BUILTIN_TEMPLATES)


def is_builtin(template_id: str) -> bool:
    """builtin 判法单一源（DEC-⑦-㉒）：种子 starter 目录=BUILTIN_TEMPLATES 派生
    id 集（禁第二套名单手抄漂移 F4b-㉗ 纪律），=系统共享只读目录（DEC-⑩-① 语义）。"""
    return template_id in BUILTIN_TEMPLATE_IDS


def template_row_tenant(raw: object) -> str:
    """行租户键单一判法（DEC-⑦-㉒；DEC-⑦-② message_row_tenant / ⑦-㉑ echo
    template_row_tenant 同款语义：自带键缺失/空白→'default' 兜底，键派生故障
    绝不吃掉落库）。raw=TaskTemplate 行对象（读侧过滤）或原始租户值（写侧落行），
    写读同一判法禁双源漂移（F4b-㉗ 纪律）。"""
    if hasattr(raw, "tenant_id"):
        raw = getattr(raw, "tenant_id")
    return str(raw or "").strip() or "default"


def template_visible_to(template: TaskTemplate, tenant_id: str) -> bool:
    """读面可见性单一判法（DEC-⑦-㉒；DEC-⑩-① builtin 跨租户共享语义）：builtin=
    系统共享只读目录对全租户可见，其余行恒按行租户键（内部走 template_row_tenant
    单一判法禁另发明 F4b-㉗ 纪律）。写面不适用本判法——delete/创建覆盖恒按行归属
    +builtin 保护（共享≠可写），写读判法刻意分开两个单一源（echo/gene 同款）。"""
    return is_builtin(template.template_id) or template_row_tenant(template) == tenant_id
