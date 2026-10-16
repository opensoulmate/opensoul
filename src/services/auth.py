import logging
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from uuid import UUID

import bcrypt as _bcrypt
from jose import JWTError, jwt

from src.config import settings
from src.database.postgres import db_pool
from src.services.tenant_binding import (
    ensure_user_tenant_columns,
    enterprise_db_path,
    users_db_path,
    validate_tenant,
)

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── JWT token ──────────────────────────────────────────────────────────


def create_access_token(user_id: UUID, role: str = "user", extra: dict | None = None) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
        "iat": datetime.now(UTC),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class TokenLookupFault(Exception):
    """凭据身份查库故障（身份库不可读/查询失败）——故障≠失效凭据。

    与「凭据无效」（decode_token 回 None）刻意分开：故障吞成 None=谎报凭据
    无效（客户端误重登、看门狗误杀活会话、监控把服务故障记成鉴权失败）。
    故障必须原样上抛，由调用方按各自语义处置：HTTP 面 fail-closed 503 如实
    （禁 401 谎报凭据无效）、看门狗面记日志继续禁误杀。
    根因：DEC-⑭-③ 边界③留档件「企业 token 分支 DB 瞬断被吞成 None=故障被当
    失效凭据误杀」——DEC-⑭-④ 收了 ws_chat 判口，本件收 decode_token 本体
    及其余消费面。
    """


def decode_token(token: str) -> dict | None:
    """Decode JWT and return payload dict, or None on failure.

    Handles both UUID sub (standard auth) and integer sub (enterprise auth).

    故障语义（DEC-⑭-⑤ 收口，与 _identity_for_token/bind_identity 家族同判）：
    - 凭据无效（签名/过期/畸形、企业身份行缺失或停用、opensoul 侧无对应用户）
      → None（如实「凭据无效」）
    - 判定口自身故障（身份库不可读/查询失败）→ TokenLookupFault 原样上抛：
      故障≠失效凭据（修前 except Exception 吞成 None=故障被当失效凭据）
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        if not user_id:
            return None
        # Try UUID first (standard auth), fall back to int (enterprise auth)
        try:
            uid = UUID(user_id)
        except ValueError:
            # Enterprise auth uses integer IDs — look up UUID by username
            username = payload.get("username")
            if not username:
                return None
            uid = _enterprise_identity_uid(user_id, username)
            if uid is None:
                return None
        return {
            "user_id": uid,
            "role": payload.get("role", payload.get("scope", "user")),
        }
    except (JWTError, KeyError, ValueError):
        return None


def _enterprise_identity_uid(user_id, username):
    """企业 token 身份解析单一查库口（decode_token 企业分支）。

    - 身份行缺失/停用、opensoul 侧无对应用户行 → None（凭据无法解析=无效）
    - 库文件缺失 → None（tenant_binding._lookup_user 同判法：如实 401，且
      不空建库文件——修前 sqlite3.connect 缺库即建空库副作用一并收口）
    - 查库故障（sqlite3/OSError）→ TokenLookupFault 原样上抛（故障≠失效凭据）
    """
    try:
        uid_int = int(user_id)
    except (TypeError, ValueError):
        return None
    ent_db = enterprise_db_path()
    if not Path(ent_db).exists():
        return None
    try:
        with closing(sqlite3.connect(str(ent_db))) as conn:
            conn.row_factory = sqlite3.Row
            if not conn.execute(
                "SELECT id FROM users WHERE id = ? AND is_active = 1", (uid_int,)
            ).fetchone():
                return None
    except (sqlite3.Error, OSError) as e:
        raise TokenLookupFault(f"enterprise identity lookup failed: {e}") from e
    os_db = users_db_path()
    if not Path(os_db).exists():
        return None
    try:
        with closing(sqlite3.connect(str(os_db))) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    except (sqlite3.Error, OSError) as e:
        raise TokenLookupFault(f"user identity lookup failed: {e}") from e
    if not row:
        return None
    try:
        return UUID(row["id"])
    except (ValueError, KeyError, TypeError):
        return None


# ── User operations ────────────────────────────────────────────────────


async def authenticate_user(username: str, password: str) -> dict | None:
    row = await db_pool.fetchrow(
        "SELECT id, username, email, role, password_hash FROM users WHERE username = $1",
        username,
    )
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "role": row["role"],
    }


async def get_user_by_id(user_id: UUID) -> dict | None:
    row = await db_pool.fetchrow(
        "SELECT id, username, email, role, is_active, created_at, updated_at FROM users WHERE id = $1",
        str(user_id),
    )
    return dict(row) if row else None


async def register_user(
    username: str,
    email: str,
    password: str,
    role: str = "user",
    tenant_id: str = "default",
) -> dict:
    import uuid

    user_id = str(uuid.uuid4())
    hashed = hash_password(password)
    # F3b-② 认证→租户绑定：用户行携带租户绑定（token 身份派生单一真源），
    # 未知租户 ValueError→API 层 400（禁悬空绑定）
    tenant_id = validate_tenant(tenant_id)
    ensure_user_tenant_columns(users_db_path())
    await db_pool.execute(
        "INSERT INTO users (id, username, email, password_hash, role, tenant_id) VALUES ($1, $2, $3, $4, $5, $6)",
        user_id,
        username,
        email,
        hashed,
        role,
        tenant_id,
    )
    row = await db_pool.fetchrow(
        "SELECT id, username, email, role, is_active, created_at, updated_at FROM users WHERE id = $1",
        str(user_id),
    )
    return dict(row)


# ── Role helpers ───────────────────────────────────────────────────────

ROLE_HIERARCHY = {"viewer": 0, "user": 1, "editor": 2, "admin": 3}


def role_at_least(user_role: str, required: str) -> bool:
    return ROLE_HIERARCHY.get(user_role, -1) >= ROLE_HIERARCHY.get(required, 999)


def require_role(required_role: str):
    """Decorator for route handlers — raises 403 if user role < required_role.

    Must be used AFTER Depends(get_current_user) so that `current_user`
    is available as a kwarg.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, current_user: dict | None = None, **kwargs):
            if not current_user:
                from fastapi import HTTPException

                raise HTTPException(status_code=401, detail="Not authenticated")
            if not role_at_least(current_user.get("role", "user"), required_role):
                from fastapi import HTTPException

                raise HTTPException(status_code=403, detail="Insufficient permissions")
            return await func(*args, current_user=current_user, **kwargs)

        return wrapper

    return decorator
