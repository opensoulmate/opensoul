import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile

from src.api._errors import stats_error
from src.database.postgres import db_pool
from src.models.knowledge import KnowledgeCreate, KnowledgeResponse, KnowledgeUpdate
from src.services import knowledge as knowledge_service
from src.services.extraction import extract_text_from_file
from src.services.tenant_binding import bind_identity

router = APIRouter()


def _resolve_user_id(user_id: str) -> UUID:
    """F3b-⑥ 家族 _resolve_user_id 同形（档案键家族正典）：UUID 串原样、
    普通用户名/数字 id 按 md5 归一为档案 UUID。"""
    try:
        return UUID(user_id)
    except ValueError:
        h = hashlib.md5(user_id.encode()).hexdigest()
        return UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


@router.get("/health")
async def health():
    return {"status": "ok", "component": "OpenKnowledge"}


@router.get("/stats")
async def knowledge_stats():
    """Get knowledge base statistics."""
    try:
        total = await db_pool.fetchval("SELECT COUNT(*) FROM knowledge") or 0
        # C2根因修复：原EXTRACT(EPOCH FROM NOW()-INTERVAL '24 hours')是PG语法，
        # SQLite必炸被假ok吞掉（live实测306条谎报0）。created_at是
        # datetime('now')文本（YYYY-MM-DD HH:MM:SS），按同一格式比较。
        cutoff = (datetime.now(UTC) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        recent = (
            await db_pool.fetchval("SELECT COUNT(*) FROM knowledge WHERE created_at > $1", cutoff)
            or 0
        )
        by_user = await db_pool.fetch(
            "SELECT user_id, COUNT(*) as cnt FROM knowledge GROUP BY user_id ORDER BY cnt DESC LIMIT 10"
        )
        return {
            "status": "ok",
            "component": "OpenKnowledge",
            "total_entries": total,
            "recent_24h": recent,
            "top_users": [{"user_id": r["user_id"], "count": r["cnt"]} for r in (by_user or [])],
        }
    except Exception as exc:
        # C2统一错误响应：失败说失败、说原因，禁谎报ok（假0冒充"没数据"）
        return stats_error("OpenKnowledge", exc, total_entries=0, recent_24h=0, top_users=[])


# Supported file types for upload
SUPPORTED_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "docx",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/csv": "csv",
    "application/json": "json",
    "text/x-python": "py",
    "text/javascript": "js",
    "text/typescript": "ts",
    "application/xml": "xml",
    "text/xml": "xml",
}

# Extension → content type fallback mapping
_EXT_MAP = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
    ".json": "application/json",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".xml": "application/xml",
    ".yaml": "text/plain",
    ".yml": "text/plain",
    ".toml": "text/plain",
    ".sh": "text/plain",
    ".log": "text/plain",
}


# ── Helpers ──────────────────────────────────────────────────────────────


def _resolve_content_type(file: UploadFile, data: bytes) -> str:
    """Resolve content type from MIME type, extension, or content sniffing."""
    ct = file.content_type or ""
    if ct in SUPPORTED_TYPES:
        return ct
    filename = file.filename or ""
    _, ext = os.path.splitext(filename.lower())
    ext_ct = _EXT_MAP.get(ext)
    if ext_ct:
        return ext_ct
    return "text/plain"


def _guess_tags(filename: str, content_type: str) -> list[str]:
    """Auto-generate tags from filename and content type."""
    tags = []
    _, ext = os.path.splitext(filename.lower())
    ext_label = ext.lstrip(".")
    if ext_label:
        tags.append(ext_label)
    type_labels = {
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "text/markdown": "markdown",
        "text/html": "html",
        "text/csv": "csv",
    }
    label = type_labels.get(content_type)
    if label and label not in tags:
        tags.append(label)
    return tags


# ── CRUD Endpoints ───────────────────────────────────────────────────────


@router.get("/", response_model=list[KnowledgeResponse])
async def list_knowledge(
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    content_type: str | None = None,
    domain: str | None = None,
    tag: str | None = None,
):
    """List knowledge items with pagination and filters.

    身份轴收口（F3b-⑥ 同族语义）：user_id 从认证派生，自报只可确认派生绑定
    （矛盾=403 冒读面收口）；'default'/空/占位零 UUID=未声明→取派生；匿名
    保留自报（既有本地多档案语义契约零变化）；坏 token=401 零副作用。读键=
    档案键家族正典 _resolve_user_id（F3b-⑫ 同源）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    return await knowledge_service.list_knowledge(
        uid,
        offset=offset,
        limit=limit,
        content_type=content_type,
        domain=domain,
        tag=tag,
    )


@router.post("/", response_model=KnowledgeResponse)
async def create_knowledge(
    data: KnowledgeCreate,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """Create a new knowledge item.

    身份轴收口（F3b-⑥ 同族语义）：写键=认证派生档案键（自报只可确认绑定，
    矛盾=403 冒写面收口），经 _resolve_user_id 家族正典归一（F3b-⑫ 同源）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    row = await knowledge_service.create_knowledge(data, uid)
    return row


# ── File Upload (MUST be before /{knowledge_id} catch-all) ──────────────


@router.post("/upload", response_model=KnowledgeResponse)
async def upload_file(
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
    file: UploadFile = File(...),
    title: str = Form(""),
    tags: str = Form(""),
):
    """Upload a file and create a knowledge item from its contents.

    Supports: PDF, DOCX, TXT, MD, HTML, CSV, JSON, and common code files.
    The file is parsed, text is extracted, and stored as a knowledge item
    with automatic chunking and embedding for RAG search.

    身份轴收口（F3b-⑥ 同族语义）：写键=认证派生档案键（自报只可确认绑定，
    矛盾=403 冒写面收口），经 _resolve_user_id 家族正典归一（F3b-⑫ 同源）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    if not file.filename:
        raise HTTPException(status_code=400, detail="File must have a filename")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="File is empty")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")

    content_type = _resolve_content_type(file, data)

    try:
        extracted_text = extract_text_from_file(data, content_type)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to extract text from file: {e}")

    if not extracted_text or not extracted_text.strip():
        raise HTTPException(status_code=422, detail="Could not extract any text from the file.")

    item_title = title.strip() or os.path.splitext(file.filename)[0]
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    tag_list.extend(t for t in _guess_tags(file.filename, content_type) if t not in tag_list)

    create_data = KnowledgeCreate(
        title=item_title,
        content=extracted_text,
        source=f"file:{file.filename}",
        content_type=content_type,
        tags=tag_list,
        metadata={
            "original_filename": file.filename,
            "file_size": len(data),
            "content_type": content_type,
            "char_count": len(extracted_text),
        },
    )

    return await knowledge_service.create_knowledge(create_data, uid)


@router.post("/upload/bulk")
async def upload_files_bulk(
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
    files: list[UploadFile] = File(...),
    tags: str = Form(""),
):
    """Upload multiple files at once and create knowledge items for each.

    身份轴收口（F3b-⑥ 同族语义）：写键=认证派生档案键（自报只可确认绑定，
    矛盾=403 冒写面收口），经 _resolve_user_id 家族正典归一（F3b-⑫ 同源）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    results = {"created": [], "failed": []}
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    for file in files:
        try:
            if not file.filename:
                results["failed"].append({"filename": "unknown", "error": "No filename"})
                continue
            data = await file.read()
            if not data:
                results["failed"].append({"filename": file.filename, "error": "Empty file"})
                continue
            if len(data) > 50 * 1024 * 1024:
                results["failed"].append({"filename": file.filename, "error": "File too large"})
                continue

            content_type = _resolve_content_type(file, data)
            extracted_text = extract_text_from_file(data, content_type)
            if not extracted_text or not extracted_text.strip():
                results["failed"].append({"filename": file.filename, "error": "No text extracted"})
                continue

            item_title = os.path.splitext(file.filename)[0]
            item_tags = list(tag_list)
            item_tags.extend(
                t for t in _guess_tags(file.filename, content_type) if t not in item_tags
            )

            create_data = KnowledgeCreate(
                title=item_title,
                content=extracted_text,
                source=f"file:{file.filename}",
                content_type=content_type,
                tags=item_tags,
                metadata={
                    "original_filename": file.filename,
                    "file_size": len(data),
                    "content_type": content_type,
                    "char_count": len(extracted_text),
                },
            )
            row = await knowledge_service.create_knowledge(create_data, uid)
            results["created"].append(
                {
                    "id": str(row["id"]),
                    "filename": file.filename,
                    "title": item_title,
                    "chars": len(extracted_text),
                }
            )
        except Exception as e:
            results["failed"].append({"filename": file.filename, "error": str(e)})

    return {
        "total": len(files),
        "created": len(results["created"]),
        "failed": len(results["failed"]),
        "results": results,
    }


# ── Parameterized routes (MUST be after static routes) ──────────────────


@router.get("/{knowledge_id}", response_model=KnowledgeResponse)
async def get_knowledge(
    knowledge_id: UUID,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """Get a single knowledge item by ID.

    身份轴收口（F3b-⑥ 同族语义）：读键=认证派生档案键（自报只可确认绑定，
    矛盾=403 冒读面收口），经 _resolve_user_id 家族正典归一（F3b-⑫ 同源）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    row = await knowledge_service.get_knowledge(knowledge_id, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return row


@router.put("/{knowledge_id}", response_model=KnowledgeResponse)
async def update_knowledge(
    knowledge_id: UUID,
    data: KnowledgeUpdate,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """Update an existing knowledge item.

    身份轴收口（F3b-⑥ 同族语义）：写键=认证派生档案键（自报只可确认绑定，
    矛盾=403 冒写面收口），经 _resolve_user_id 家族正典归一（F3b-⑫ 同源）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    row = await knowledge_service.update_knowledge(knowledge_id, data, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return row


@router.delete("/{knowledge_id}")
async def delete_knowledge(
    knowledge_id: UUID,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """Delete a knowledge item.

    身份轴收口（F3b-⑥ 同族语义）：写键=认证派生档案键（自报只可确认绑定，
    矛盾=403 冒删面收口），经 _resolve_user_id 家族正典归一（F3b-⑫ 同源）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    deleted = await knowledge_service.delete_knowledge(knowledge_id, uid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return {"deleted": True}


@router.post("/{knowledge_id}/star")
async def star_knowledge(
    knowledge_id: UUID,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """Toggle star (favorite) on a knowledge item.

    身份轴收口（F3b-⑥ 同族语义）：写键=认证派生档案键（自报只可确认绑定，
    矛盾=403 冒写面收口），经 _resolve_user_id 家族正典归一（F3b-⑫ 同源）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    result = await knowledge_service.toggle_star(knowledge_id, uid)
    if not result:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return {"id": knowledge_id, "starred": result["starred"]}


@router.post("/{knowledge_id}/pin")
async def pin_knowledge(
    knowledge_id: UUID,
    user_id: str = Query("default"),
    authorization: str | None = Header(None),
):
    """Toggle pin on a knowledge item.

    身份轴收口（F3b-⑥ 同族语义）：写键=认证派生档案键（自报只可确认绑定，
    矛盾=403 冒写面收口），经 _resolve_user_id 家族正典归一（F3b-⑫ 同源）。
    """
    identity = bind_identity(authorization, "", user_id)
    uid = _resolve_user_id(identity.user_id)
    result = await knowledge_service.toggle_pin(knowledge_id, uid)
    if not result:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return {"id": knowledge_id, "pinned": result["pinned"]}
