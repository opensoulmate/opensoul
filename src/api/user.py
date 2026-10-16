import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

from src.models.user import Token, UserCreate, UserResponse
from src.services.auth import (
    TokenLookupFault,
    authenticate_user,
    create_access_token,
    decode_token,
    get_user_by_id,
    register_user,
)
from src.services.permission import add_role

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def user_health():
    """User system health check."""
    return {"status": "ok", "component": "UserSystem"}


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/user/login")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> UUID:
    try:
        token_data = decode_token(token)
    except TokenLookupFault as exc:
        # 故障≠失效凭据（DEC-⑭-⑤）：身份库故障如实 503，禁 401 谎报凭据无效；
        # fail-closed 不放行，但失败原因必须可见。
        logger.warning("token identity lookup unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="authentication backend unavailable") from exc
    if not token_data:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return token_data["user_id"]


@router.post("/register", response_model=UserResponse)
async def register(data: UserCreate):
    try:
        user = await register_user(
            data.username, data.email, data.password, tenant_id=data.tenant_id
        )
        add_role(data.username, "user")
        return user
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login", response_model=Token)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    user = await authenticate_user(form.username, form.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(user["id"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": str(user["id"]),
        "username": user["username"],
    }


@router.get("/me", response_model=UserResponse)
async def me(user_id: UUID = Depends(get_current_user)):
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
