"""F3b-② 认证→租户绑定：tenant/user 从认证身份派生，禁客户端自报。

F3 审计③定性：brain/mind 的 tenant_id（及 user_id）是客户端自报 query/body
参数——隔离键真过滤但键值客户端说了算（live 实证：tenant_id=attacker-tenant
自报即被当真），且全仓无认证→租户绑定（enterprise users 表无 tenant 列）。

本模块是单一派生口（声明恒等于绑定，F4b-⑤"声明从实现派生"同哲学）：
- 认证请求（Bearer JWT）：tenant/user 从 token 身份对应的 users 行派生
  （单一真源=users.tenant_id 行，不从 token 内嵌读=防双源漂移）；自报值
  只能确认派生绑定（"default"/空=未声明→取派生），不得矛盾——矛盾=403，
  冒读面收口。
- 匿名请求：钉死 tenant='default'（本地单机单用户真态，生产数据全挂
  default 名下），自报非 default 一律 403；user_id 保留自报（既有本地多
  档案语义，如 /api/brain/feedback 的查询面）。

users.tenant_id 双库缺列自愈（opensoul.db users / enterprise.db users，
C1 _SCHEMA_FIXES 同款幂等模式：PRAGMA 探测→缺失才 ALTER→留痕），存量行
自动 'default'，与 F3 审计实测数据分布（tenant_id 全='default'）一致。

诚实边界：
- agent_id 不在本收口内（agent 身份轴走 X-Agent-Token/agent_registry 另一口）。
- 存量用户改绑无管理端点（注册可带 tenant_id，改绑=SQL，可另拍）。
- user_id 派生值=users.id 字符串（enterprise=数字 id、user=UUID）。
- 用户行 tenant_id 为空时按 'default' 处理（老行未显式绑定）。
"""

import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from jose import JWTError, jwt

from src.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TENANT = "default"
DEFAULT_USER = "default"
# 占位零 UUID（admin-ui/调用方「未指定档案」占位形，如 feedback/export 面硬编码
# 00000000-0000-0000-0000-000000000000）：与 'default'/空 同义=未声明占位，
# 不是真实身份 claim——认证确认检查按未声明处理（取派生）；匿名保留自报语义
# 零变化（占位值原样保留，既有本地多档案语义）。
NIL_UUID = "00000000-0000-0000-0000-000000000000"

_REPO_ROOT = Path(__file__).parent.parent.parent


def users_db_path() -> str:
    """opensoul.db 路径：USERS_DB 环境变量优先（测试隔离，NEST_DB 同款先例）。"""
    return os.environ.get("USERS_DB") or str(_REPO_ROOT / "data" / "opensoul.db")


def enterprise_db_path() -> str:
    """enterprise.db 路径：ENTERPRISE_DB 环境变量优先（测试隔离）。"""
    return os.environ.get("ENTERPRISE_DB") or str(_REPO_ROOT / "data" / "enterprise.db")


# ── users.tenant_id 缺列自愈 ──────────────────────────────────────────


def ensure_user_tenant_columns(db_path: str | None = None) -> None:
    """users.tenant_id 缺列自愈（幂等：PRAGMA 探测→缺失才 ALTER→留痕）。

    db_path 缺省对两个身份库都补；库文件不存在时跳过（不空建文件）。
    """
    paths = [db_path] if db_path else [users_db_path(), enterprise_db_path()]
    for path in paths:
        if not Path(path).exists():
            continue
        conn = sqlite3.connect(path, timeout=30)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            if cols and "tenant_id" not in cols:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
                )
                conn.commit()
                logger.info("schema drift healed: users.tenant_id added (%s)", path)
        finally:
            conn.close()


def _lookup_user(db_path: str, user_pk) -> dict | None:
    """按 users.id 查身份行（含 tenant_id 绑定）。表/库缺失→None（如实 401）。"""
    if not Path(db_path).exists():
        return None
    ensure_user_tenant_columns(db_path)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, username, is_active, tenant_id FROM users WHERE id = ?",
            (user_pk,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# ── 身份派生 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BoundIdentity:
    """认证派生出的租户/用户绑定（单一真源=users 行）。"""

    tenant_id: str
    user_id: str
    authed: bool
    source: str  # "anonymous" / "token:enterprise" / "token:user"


def _bearer_token(authorization) -> str | None:
    """取 Bearer token；非字符串（进程内直调带 FastAPI Header 缺省占位）视为未提供。"""
    if not isinstance(authorization, str):
        return None
    value = authorization.strip()
    if not value:
        return None
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value or None


def bind_identity(
    authorization, declared_tenant: str = "", declared_user: str | None = ""
) -> BoundIdentity:
    """tenant/user 从认证身份派生；自报值只可确认绑定，不得矛盾。

    - 匿名：tenant 钉 'default'，自报非 default→403；user_id 保留自报。
    - 认证：tenant/user 取 users 行（token 身份），自报非 default 且与派生
      不一致→403；自报 "default"/空/占位零 UUID=未声明→取派生（不误伤不传参
      或传占位符的调用方）。
    """
    declared_tenant = (declared_tenant or "").strip() or DEFAULT_TENANT
    # 自报值非 str（进程内直调带 UUID/int 形）按 _bearer_token 容缺纪律 str() 归一
    # 再判（FastAPI HTTP 面恒 str；直调面真实调用形态，禁 .strip() 炸掉集成）
    if declared_user is not None and not isinstance(declared_user, str):
        declared_user = str(declared_user)
    declared_user = (declared_user or "").strip() or None

    token = _bearer_token(authorization)
    if token is None:
        if declared_tenant != DEFAULT_TENANT:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"tenant self-report rejected: unauthenticated access is pinned to "
                    f"tenant '{DEFAULT_TENANT}' (omit tenant_id or present a token bound "
                    f"to '{declared_tenant}')"
                ),
            )
        return BoundIdentity(DEFAULT_TENANT, declared_user or DEFAULT_USER, False, "anonymous")

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")

    user_pk: int | str
    if payload.get("scope") == "enterprise":
        try:
            user_pk = int(str(payload.get("sub")))
        except (TypeError, ValueError):
            raise HTTPException(status_code=401, detail="invalid token subject")
        row = _lookup_user(enterprise_db_path(), user_pk)
        source = "token:enterprise"
    else:
        user_pk = str(payload.get("sub") or "")
        if not user_pk:
            raise HTTPException(status_code=401, detail="invalid token subject")
        row = _lookup_user(users_db_path(), user_pk)
        source = "token:user"

    if not row or not row.get("is_active"):
        raise HTTPException(status_code=401, detail="token identity not found or inactive")

    derived_tenant = (row.get("tenant_id") or "").strip() or DEFAULT_TENANT
    derived_user = str(row["id"])
    if declared_tenant != DEFAULT_TENANT and declared_tenant != derived_tenant:
        raise HTTPException(
            status_code=403,
            detail=(
                f"tenant mismatch: token identity bound to '{derived_tenant}', "
                f"declared '{declared_tenant}'"
            ),
        )
    if (
        declared_user
        and declared_user != DEFAULT_USER
        and declared_user != NIL_UUID
        and declared_user != derived_user
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                f"user mismatch: token identity is '{derived_user}', declared '{declared_user}'"
            ),
        )
    return BoundIdentity(derived_tenant, derived_user, True, source)


def validate_tenant(tenant_id: str | None) -> str:
    """注册绑定校验：租户必须已在 NestStore 登记（F3b-① 租户真源），'default' 恒可用。

    未知租户抛 ValueError（API 层映射 400）——绑定必须指向真租户，禁悬空引用。
    """
    tenant_id = (tenant_id or "").strip() or DEFAULT_TENANT
    if tenant_id == DEFAULT_TENANT:
        return DEFAULT_TENANT
    from src.nest.tenant import TenantManager

    manager = TenantManager()
    manager.ensure_default_tenant()
    if manager.get(tenant_id) is None:
        raise ValueError(f"unknown tenant '{tenant_id}' (create it via /api/nest/tenants first)")
    return tenant_id
