from datetime import UTC, datetime, timedelta
from functools import wraps
from uuid import UUID

import bcrypt as _bcrypt
from jose import JWTError, jwt

from src.config import settings
from src.database.postgres import db_pool
from src.services.tenant_binding import (
    ensure_user_tenant_columns,
    users_db_path,
    validate_tenant,
)


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


def decode_token(token: str) -> dict | None:
    """Decode JWT and return payload dict, or None on failure.

    Handles both UUID sub (standard auth) and integer sub (enterprise auth).
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
            import sqlite3 as _sqlite3
            from pathlib import Path as _Path

            # Verify enterprise user exists
            _ent_db = _Path(__file__).parent.parent.parent / "data" / "enterprise.db"
            _os_db = _Path(__file__).parent.parent.parent / "data" / "opensoul.db"
            try:
                with _sqlite3.connect(str(_ent_db)) as _conn:
                    _conn.row_factory = _sqlite3.Row
                    if not _conn.execute(
                        "SELECT id FROM users WHERE id = ? AND is_active = 1", (int(user_id),)
                    ).fetchone():
                        return None
                # Look up UUID from opensoul.db by username
                with _sqlite3.connect(str(_os_db)) as _conn:
                    _conn.row_factory = _sqlite3.Row
                    _row = _conn.execute(
                        "SELECT id FROM users WHERE username = ?", (username,)
                    ).fetchone()
                    if not _row:
                        return None
                    uid = UUID(_row["id"])
            except Exception:
                return None
        return {
            "user_id": uid,
            "role": payload.get("role", payload.get("scope", "user")),
        }
    except (JWTError, KeyError, ValueError):
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
