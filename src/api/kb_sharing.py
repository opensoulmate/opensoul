import hashlib
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.database.postgres import db_pool
from src.middleware.auth import get_current_user
from src.services.auth import role_at_least

router = APIRouter()

# F3b-⑰ 身份轴收口（F3b-⑥ 家族语义，F3b-⑯ knowledge_requests 同形）：user 轴
# 从认证派生（middleware get_current_user=users 行真源+role，与 require_role
# 同一身份源），admin 判定 role_at_least 单一判法（services.auth，middleware
# require_role 同判法）。
NIL_UUID = "00000000-0000-0000-0000-000000000000"
_UNDECLARED_USER_IDS = {"", "default", NIL_UUID}


def _resolve_user_id(user_id: str) -> UUID:
    """F3b-⑥ 家族 _resolve_user_id 同形（档案键家族正典）：UUID 串原样、
    普通用户名/数字 id 按 md5 归一为档案 UUID。"""
    try:
        return UUID(user_id)
    except ValueError:
        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


def _archive_key(user: dict) -> UUID:
    """写读键家族正典（F3b-⑫/⑭/⑮/⑯ 同源）：认证派生 users.id 归一档案键。"""
    return _resolve_user_id(str(user["id"]))


def _is_admin(user: dict) -> bool:
    """管理员判定单一判法（services.auth.role_at_least=middleware require_role 同判法）。"""
    return role_at_least(user.get("role", "user"), "admin")


@router.get("/health")
async def kb_sharing_health():
    """KBSharing health check."""
    return {"status": "ok", "component": "KBSharing"}


class SharingRequestCreate(BaseModel):
    kb_id: str
    kb_name: str


class SharingRequestReview(BaseModel):
    status: str  # "approved" or "rejected"
    review_note: str = ""


@router.post("/")
async def create_sharing_request(
    data: SharingRequestCreate, current_user: dict = Depends(get_current_user)
):
    """用户申请将个人知识库共享到企业知识库（F3b-⑰ 身份轴收口：行归属=认证派生
    档案键家族正典）"""
    uid = _archive_key(current_user)
    req_id = str(uuid4())
    user_name = current_user.get("username") or "unknown"

    await db_pool.execute(
        "INSERT INTO kb_sharing_requests (id, user_id, user_name, kb_id, kb_name) VALUES (?, ?, ?, ?, ?)",
        req_id,
        str(uid),
        user_name,
        data.kb_id,
        data.kb_name,
    )
    row = await db_pool.fetchrow("SELECT * FROM kb_sharing_requests WHERE id = ?", req_id)
    return dict(row)


@router.get("/")
async def list_sharing_requests(
    status: str | None = None,
    user_id: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """列出共享申请（管理员看全部，用户看自己的——F3b-⑰ 落地与 F3b-⑯ 同族契约）。

    F3b-⑥ 语义：非 admin 的 user_id 自报只可确认派生绑定、矛盾=403（冒读面收口）、
    'default'/空/占位零 UUID=未声明→只看自己的；admin 的 user_id=管理面过滤参数。
    """
    uid = _archive_key(current_user)
    admin = _is_admin(current_user)
    conditions = []
    values = []
    if status:
        conditions.append("status = ?")
        values.append(status)
    if user_id is not None and user_id.strip() not in _UNDECLARED_USER_IDS:
        filter_key = _resolve_user_id(user_id.strip())
        if admin:
            conditions.append("user_id = ?")
            values.append(str(filter_key))
        elif filter_key != uid:
            raise HTTPException(
                status_code=403,
                detail=f"user mismatch: authenticated user may only list own requests (declared={user_id})",
            )
    if not admin:
        conditions.append("user_id = ?")
        values.append(str(uid))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = await db_pool.fetch(
        f"SELECT * FROM kb_sharing_requests{where} ORDER BY created_at DESC", *values
    )
    return [dict(r) for r in rows]


@router.get("/my")
async def my_sharing_requests(current_user: dict = Depends(get_current_user)):
    """当前用户的申请（F3b-⑰：读键=档案键家族正典）"""
    uid = _archive_key(current_user)
    rows = await db_pool.fetch(
        "SELECT * FROM kb_sharing_requests WHERE user_id = ? ORDER BY created_at DESC",
        str(uid),
    )
    return [dict(r) for r in rows]


@router.post("/{request_id}/review")
async def review_sharing_request(
    request_id: str, data: SharingRequestReview, current_user: dict = Depends(get_current_user)
):
    """管理员审批共享申请（F3b-⑰ 管理员门收口：修前任意认证用户可审批他人申请，
    approved 直写申请人 knowledge 行=冒写面；现仅 admin 可裁决——role_at_least
    单一判法，与 agent.py require_role(\"admin\") 同源）。"""
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions: admin required")
    if data.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")

    row = await db_pool.fetchrow("SELECT * FROM kb_sharing_requests WHERE id = ?", request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=400, detail="Already reviewed")

    await db_pool.execute(
        "UPDATE kb_sharing_requests SET status = ?, reviewer_id = ?, review_note = ?, reviewed_at = datetime('now') WHERE id = ?",
        data.status,
        str(current_user["id"]),
        data.review_note,
        request_id,
    )

    # 审批通过：将该用户的知识条目标记为企业共享（WHERE 写键=申请人档案键家族正典，
    # 与 F3b-⑭ knowledge 写键同源；shared_by=申请人原语义不变）
    if data.status == "approved":
        await db_pool.execute(
            "UPDATE knowledge SET metadata = json_set(COALESCE(metadata, '{}'), '$.shared_to_enterprise', json('true'), '$.shared_at', datetime('now'), '$.shared_by', ?) WHERE user_id = ?",
            str(row["user_id"]),
            str(_resolve_user_id(str(row["user_id"]))),
        )

    updated = await db_pool.fetchrow("SELECT * FROM kb_sharing_requests WHERE id = ?", request_id)
    return dict(updated)
