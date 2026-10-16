import logging

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.database.postgres import db_pool
from src.services.auth import TokenLookupFault, decode_token, role_at_least

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """Extract and validate JWT from Authorization header, return user dict."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except TokenLookupFault as exc:
        # 故障≠失效凭据（DEC-⑭-⑤）：身份库故障如实 503，禁 401 谎报凭据无效；
        # fail-closed 不放行，但失败原因必须可见。
        logger.warning("token identity lookup unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="authentication backend unavailable") from exc
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = await db_pool.fetchrow(
        "SELECT id, username, email, role, is_active FROM users WHERE id = $1",
        payload["user_id"],
    )
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="User account is disabled")
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
    }


async def get_current_active_user(user: dict = Depends(get_current_user)) -> dict:
    """Alias that makes intent explicit — user is already checked active."""
    return user


async def get_agent_from_header(request: Request) -> dict | None:
    """Extract agent token from X-Agent-Token header and validate."""
    token = request.headers.get("X-Agent-Token")
    if not token:
        return None
    row = await db_pool.fetchrow(
        "SELECT id, name, agent_type, status FROM agents WHERE token = $1",
        token,
    )
    if not row:
        return None
    return dict(row)


async def require_agent(request: Request) -> dict:
    """Like get_agent_from_header but raises 401 if missing/invalid."""
    agent = await get_agent_from_header(request)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or missing agent token")
    return agent


def require_role(required_role: str):
    """FastAPI dependency — raises 403 if current user role < required_role.

    Use as: `current_user: dict = Depends(require_role("admin"))`
    """

    async def _check(user: dict = Depends(get_current_user)) -> dict:
        if not role_at_least(user.get("role", "user"), required_role):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return _check
