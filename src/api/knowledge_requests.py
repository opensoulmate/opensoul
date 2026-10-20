import hashlib
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.database.postgres import db_pool
from src.middleware.auth import get_current_user
from src.services.auth import role_at_least

router = APIRouter()

# F3b-⑯ 身份轴收口（F3b-⑥ 家族语义）：user 轴从认证派生（middleware get_current_user
# =users 行真源+role，与 require_role("admin") 同一身份/判法单一源），admin 判定
# role_at_least 单一判法（services.auth，middleware require_role 同源复用）。
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
    """写读键家族正典（F3b-⑫/⑭/⑮ 同源）：认证派生 users.id 归一档案键。"""
    return _resolve_user_id(str(user["id"]))


def _is_admin(user: dict) -> bool:
    """管理员判定单一判法（services.auth.role_at_least=middleware require_role 同判法）。"""
    return role_at_least(user.get("role", "user"), "admin")


@router.get("/health")
async def knowledge_requests_health():
    """KnowledgeRequests health check."""
    return {"status": "ok", "component": "KnowledgeRequests"}


class KnowledgeRequestCreate(BaseModel):
    kb_name: str
    kb_description: str = ""


class KnowledgeRequestReview(BaseModel):
    status: str  # "approved" or "rejected"
    review_note: str = ""


@router.post("/")
async def create_request(
    data: KnowledgeRequestCreate, current_user: dict = Depends(get_current_user)
):
    """用户提交知识库创建申请（F3b-⑯ 身份轴收口：行归属=认证派生档案键家族正典）"""
    uid = _archive_key(current_user)
    req_id = str(uuid4())
    user_name = current_user.get("username") or "unknown"

    await db_pool.execute(
        "INSERT INTO knowledge_requests (id, user_id, user_name, kb_name, kb_description) VALUES (?, ?, ?, ?, ?)",
        req_id,
        str(uid),
        user_name,
        data.kb_name,
        data.kb_description,
    )
    row = await db_pool.fetchrow("SELECT * FROM knowledge_requests WHERE id = ?", req_id)
    return _parse_row(row)


@router.get("/")
async def list_requests(
    status: str | None = None,
    user_id: str | None = None,
    current_user: dict = Depends(get_current_user),
):
    """列出知识库申请（管理员看全部，用户看自己的——F3b-⑯ 落地 docstring 既有契约）。

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
        f"SELECT * FROM knowledge_requests{where} ORDER BY created_at DESC", *values
    )
    return [_parse_row(r) for r in rows]


@router.get("/my")
async def my_requests(current_user: dict = Depends(get_current_user)):
    """当前用户的申请（F3b-⑯：读键=档案键家族正典）"""
    uid = _archive_key(current_user)
    rows = await db_pool.fetch(
        "SELECT * FROM knowledge_requests WHERE user_id = ? ORDER BY created_at DESC",
        str(uid),
    )
    return [_parse_row(r) for r in rows]


@router.get("/{request_id}")
async def get_request(request_id: str, current_user: dict = Depends(get_current_user)):
    row = await db_pool.fetchrow("SELECT * FROM knowledge_requests WHERE id = ?", request_id)
    uid = _archive_key(current_user)
    # F3b-⑯ 冒读面收口：非本人非 admin 与缺行同契约（禁存在性探测）。
    if not row or (row["user_id"] != str(uid) and not _is_admin(current_user)):
        raise HTTPException(status_code=404, detail="Request not found")
    return _parse_row(row)


@router.post("/{request_id}/review")
async def review_request(
    request_id: str, data: KnowledgeRequestReview, current_user: dict = Depends(get_current_user)
):
    """管理员审批知识库申请（F3b-⑯ 管理员门收口：修前任意认证用户可审批他人申请，
    approved 直写申请人知识档案=冒写面；现仅 admin 可裁决——role_at_least 单一判法，
    与 agent.py require_role("admin") 同源）。"""
    if not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Insufficient permissions: admin required")
    if data.status not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")

    row = await db_pool.fetchrow("SELECT * FROM knowledge_requests WHERE id = ?", request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    if row["status"] != "pending":
        raise HTTPException(status_code=400, detail="Already reviewed")

    await db_pool.execute(
        "UPDATE knowledge_requests SET status = ?, reviewer_id = ?, review_note = ?, reviewed_at = datetime('now') WHERE id = ?",
        data.status,
        str(current_user["id"]),
        data.review_note,
        request_id,
    )

    # 如果审批通过，创建知识库
    if data.status == "approved":
        kb_id = str(uuid4())
        await db_pool.execute(
            "INSERT INTO knowledge (id, user_id, title, content, source, content_type, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            kb_id,
            row["user_id"],
            row["kb_name"],
            f"知识库: {row['kb_name']}\n{row['kb_description']}",
            "auto-created",
            "kb",
            json.dumps({"type": "knowledge_base", "description": row["kb_description"]}),
        )

    updated = await db_pool.fetchrow("SELECT * FROM knowledge_requests WHERE id = ?", request_id)
    return _parse_row(updated)


@router.delete("/{request_id}")
async def delete_request(request_id: str, current_user: dict = Depends(get_current_user)):
    row = await db_pool.fetchrow("SELECT * FROM knowledge_requests WHERE id = ?", request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    uid = _archive_key(current_user)
    if row["user_id"] != str(uid):
        raise HTTPException(status_code=403, detail="Not your request")
    await db_pool.execute("DELETE FROM knowledge_requests WHERE id = ?", request_id)
    return {"ok": True}


def _parse_row(row):
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("kb_description"), str):
        pass  # already string
    return d
