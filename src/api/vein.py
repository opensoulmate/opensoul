"""OpenVein API — 血管系统：大文件分片上传、缓存管理、资源同步。"""

import logging

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.nerve.event_bridge import push_event
from src.services.tenant_binding import bind_identity
from src.vein.cache import CacheManager, cache_row_tenant
from src.vein.chunked import ChunkedUploader
from src.vein.file_store import FileStore, FileTenantDenied, file_row_tenant

logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)
router = APIRouter()

# ── Singletons ─────────────────────────────────────────────
store = FileStore()
cache = CacheManager(max_size_mb=256, default_ttl=3600)
uploader = ChunkedUploader()


# ── Request Schemas ────────────────────────────────────────


class CachePutRequest(BaseModel):
    key: str
    data: str  # base64 encoded
    ttl: int | None = None


class UploadSessionCreate(BaseModel):
    filename: str
    total_size: int
    chunk_size: int | None = None
    mime_type: str = "application/octet-stream"
    tags: list[str] | None = None


class ChunkUpload(BaseModel):
    chunk_index: int
    data: str  # base64 encoded


# ── File Store Endpoints ───────────────────────────────────


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    tags: str = Form(default=""),
    authorization: str | None = Header(None),
):
    """Upload a single file (small-medium size). Content-addressable dedup."""
    identity = bind_identity(authorization)
    data = await file.read()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    meta = store.store(
        data=data,
        name=file.filename or "unnamed",
        mime_type=file.content_type or "application/octet-stream",
        tags=tag_list,
        tenant_id=identity.tenant_id,
    )

    # Auto-cache for hot access（DEC-⑦-㉕b：行归属=文件行租户键）
    cache.put(f"file:{meta.file_id}", data, tenant_id=file_row_tenant(meta))

    # Emit event
    push_event(
        {
            "organ": "vein",
            "emoji": "🩸",
            "type": "file_uploaded",
            "summary": f"📄 File uploaded: {meta.name} ({meta.size} bytes)",
            "detail": {
                "file_id": meta.file_id,
                "name": meta.name,
                "size": meta.size,
                "mime_type": meta.mime_type,
            },
        },
        tenant_id=identity.tenant_id,
    )

    return {
        "file_id": meta.file_id,
        "name": meta.name,
        "content_hash": meta.content_hash,
        "size": meta.size,
        "mime_type": meta.mime_type,
        "tags": tag_list,
    }


@router.get("/files")
async def list_files(
    name: str | None = Query(None, description="Filter by name (substring)"),
    tag: str | None = Query(None, description="Filter by tag"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    """List stored files with optional filters.

    DEC-⑦-④：tenant 由 bind_identity 派生禁自报，恒按行租户键过滤（匿名
    'default'——单机存量行全 'default'，契约不变）；响应 entries 增加性
    tenant_id 键（行归属可观测，file_row_tenant 归一值）。
    """
    identity = bind_identity(authorization)
    files = store.list_files(
        name_filter=name, tag=tag, limit=limit, offset=offset, tenant_id=identity.tenant_id
    )
    return {
        "files": [
            {
                "file_id": f.file_id,
                "name": f.name,
                "size": f.size,
                "mime_type": f.mime_type,
                "tags": f.tags.split(",") if f.tags else [],
                "created_at": f.created_at,
                "content_hash": f.content_hash,
                "tenant_id": file_row_tenant(f),
            }
            for f in files
        ],
        "count": len(files),
    }


@router.get("/files/{file_id}")
async def get_file_meta(file_id: str, authorization: str | None = Header(None)):
    """Get file metadata by ID.

    DEC-⑦-⑩ by-ID 读面归属闸：tenant 由 bind_identity 派生（匿名 'default'），
    跨租户=404 与 not-found 同一契约（不差分泄露行存在性，③c-1 纪律）。
    """
    identity = bind_identity(authorization)
    meta = store.get_meta(file_id, tenant_id=identity.tenant_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")
    return {
        "file_id": meta.file_id,
        "name": meta.name,
        "size": meta.size,
        "mime_type": meta.mime_type,
        "tags": meta.tags.split(",") if meta.tags else [],
        "content_hash": meta.content_hash,
        "created_at": meta.created_at,
        "tenant_id": file_row_tenant(meta),
    }


@router.get("/files/{file_id}/download")
async def download_file(file_id: str, authorization: str | None = Header(None)):
    """Download a file. Streams from cache if available."""
    identity = bind_identity(authorization)
    # DEC-⑦-⑩ by-ID 读面归属闸：先验行归属再走缓存/磁盘——缓存键全局，闸后置
    # 于缓存命中=跨租户借缓存读他人文件内容的泄露面；跨租户=404 与 not-found
    # 同一契约。语义收口如实记：修前"缓存命中但行缺失"仍回内容，现 by-ID 读面
    # 一律要求行可见。
    meta = store.get_meta(file_id, tenant_id=identity.tenant_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")

    # Check cache first（㉕b：缓存行键与文件行同租户，行内核对）
    cached = cache.get(f"file:{file_id}", tenant_id=identity.tenant_id)
    if cached:
        push_event(
            {
                "organ": "vein",
                "emoji": "🩸",
                "type": "file_downloaded",
                "summary": f"⬇️ File downloaded (cached): {meta.name}",
                "detail": {"file_id": file_id},
            },
            tenant_id=identity.tenant_id,
        )
        return StreamingResponse(
            iter([cached]),
            media_type=meta.mime_type,
            headers={"Content-Disposition": f'attachment; filename="{meta.name}"'},
        )

    # Stream from disk
    blob_path, meta = store.stream_retrieve(file_id, tenant_id=identity.tenant_id)
    if not blob_path or not meta:
        raise HTTPException(status_code=404, detail="File not found")

    def iter_file():
        with open(blob_path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type=meta.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{meta.name}"'},
    )


@router.delete("/files/{file_id}")
async def delete_file(file_id: str, authorization: str | None = Header(None)):
    """Delete a file and its cache entry.

    DEC-⑦-④：删除归属闸——tenant 由 bind_identity 派生，跨租户删除=零副作用
    （store.delete 回 False→404 与 not-found 同一契约，不差分泄露）。
    """
    identity = bind_identity(authorization)
    meta = store.get_meta(file_id)
    if not store.delete(file_id, tenant_id=identity.tenant_id):
        raise HTTPException(status_code=404, detail="File not found")
    cache.invalidate(f"file:{file_id}", tenant_id=identity.tenant_id)
    push_event(
        {
            "organ": "vein",
            "emoji": "🩸",
            "type": "file_deleted",
            "summary": f"🗑️ File deleted: {meta.name if meta else file_id}",
            "detail": {"file_id": file_id},
        },
        tenant_id=identity.tenant_id,
    )
    return {"status": "ok", "file_id": file_id}


# ── Versioning Endpoints ──────────────────────────────────


@router.put("/files/{file_id}/content")
async def update_file_content(
    file_id: str,
    file: UploadFile = File(...),
    change_summary: str = Form(default=""),
    authorization: str | None = Header(None),
):
    """Update file content with automatic version tracking.

    DEC-⑦-④：劫持闸——tenant 由 bind_identity 派生，跨租户按 file_id 覆盖=
    FileTenantDenied→403（行零改写，F3b-③c-2②d 纪律）。
    """
    identity = bind_identity(authorization)
    existing = store.get_meta(file_id)
    if not existing:
        raise HTTPException(status_code=404, detail="File not found")

    data = await file.read()
    try:
        meta = store.store(
            data=data,
            name=file.filename or existing.name,
            mime_type=file.content_type or existing.mime_type,
            tags=existing.tags.split(",") if existing.tags else [],
            file_id=file_id,
            change_summary=change_summary,
            tenant_id=identity.tenant_id,
        )
    except FileTenantDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    # Update cache（㉕b：行归属=文件行租户键）
    cache.put(f"file:{meta.file_id}", data, tenant_id=file_row_tenant(meta))

    # T3-⑦：摘要版本号单次读取——修前同一行双调 store.get_version_history
    # (file_id, limit=1)（条件表达式里重复整段调用=两次 SQL 读同一行，T3-④
    # 留档件「api/vein.py:273 双调 cleanup」）；读面带行租户键（identity.tenant_id，
    # DEC-⑦-⑩ by-ID 读面归属闸同款，修前系统视图零键=与同 handler 其余带键
    # 读面不对称）。无版本行（首传 0 行/同内容不记行）='?' 文案契约零变化。
    latest_version = (
        store.get_version_history(file_id, limit=1, tenant_id=identity.tenant_id) or [None]
    )[0]
    version_label = f"v{latest_version.version_number}" if latest_version else "v?"

    push_event(
        {
            "organ": "vein",
            "emoji": "🩸",
            "type": "file_updated",
            "summary": f"📝 File updated: {meta.name} ({version_label})",
            "detail": {"file_id": file_id, "name": meta.name, "size": meta.size},
        },
        tenant_id=identity.tenant_id,
    )

    return {
        "file_id": meta.file_id,
        "name": meta.name,
        "content_hash": meta.content_hash,
        "size": meta.size,
        # T3-⑤：计数走 count 语义（get_version_count），修前 len(get_version_history)
        # 缺省 limit=50 截断——超 50 版文件计数虚低（T3-④ 留档覆盖缺失活证据根因）。
        "version_count": store.get_version_count(file_id, tenant_id=identity.tenant_id),
    }


@router.get("/files/{file_id}/versions")
async def get_version_history(
    file_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    authorization: str | None = Header(None),
):
    """Get version history for a file.

    DEC-⑦-⑩ by-ID 读面归属闸（get_file_meta 同款）：跨租户=404 与 not-found
    同一契约。
    """
    identity = bind_identity(authorization)
    meta = store.get_meta(file_id, tenant_id=identity.tenant_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")

    versions = store.get_version_history(file_id, limit, tenant_id=identity.tenant_id)
    return {
        "file_id": file_id,
        # T3-⑥：current_version 走 count 语义（get_version_count，与 update 响应
        # version_count 同一读真源），修前 len(versions) 被 limit 截断——超 limit
        # 文件当前版本数虚低（T3-⑤ 同族截断件）。
        # 键名复核（复核即切片本体）：version_number=MAX+1 连续分配、行只增不单删
        # （delete_history 整删），COUNT(*)==MAX(version_number) 恒等=「版本行数」
        # 与「当前版本号」两读同值，键名 current_version 保留（既有契约零破坏）；
        # 若未来引入行裁剪/部分删除（行号出现空洞）须改读
        # (get_version_history(file_id, 1) or [None])[0].version_number。
        "current_version": store.get_version_count(file_id, tenant_id=identity.tenant_id),
        "versions": [
            {
                "version_number": v.version_number,
                "content_hash": v.content_hash,
                "size": v.size,
                "change_summary": v.change_summary,
                "created_at": v.created_at,
            }
            for v in versions
        ],
    }


@router.get("/files/{file_id}/versions/{version_number}")
async def get_specific_version(
    file_id: str,
    version_number: int,
    authorization: str | None = Header(None),
):
    """Get a specific version of a file.

    DEC-⑦-⑩ by-ID 读面归属闸（get_file_meta 同款）：跨租户=404 与 not-found
    同一契约（detail 沿用 "Version not found" 不差分）。
    """
    identity = bind_identity(authorization)
    version = store.get_version(file_id, version_number, tenant_id=identity.tenant_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    return {
        "file_id": file_id,
        "version_number": version.version_number,
        "content_hash": version.content_hash,
        "size": version.size,
        "change_summary": version.change_summary,
        "created_at": version.created_at,
    }


@router.post("/files/{file_id}/rollback/{version_number}")
async def rollback_file(
    file_id: str,
    version_number: int,
    authorization: str | None = Header(None),
):
    """Rollback a file to a specific version.

    DEC-⑦-④：归属闸——tenant 由 bind_identity 派生，跨租户回 None→404 与
    not-found 同一契约（零副作用）。
    """
    identity = bind_identity(authorization)
    meta = store.rollback_to_version(file_id, version_number, tenant_id=identity.tenant_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File or version not found")

    # Update cache with rolled back content
    result = store.retrieve(file_id)
    if result:
        data, _ = result
        cache.put(f"file:{file_id}", data, tenant_id=file_row_tenant(meta))

    push_event(
        {
            "organ": "vein",
            "emoji": "🩸",
            "type": "file_rollback",
            "summary": f"⏪ File rolled back: {meta.name} → version {version_number}",
            "detail": {"file_id": file_id, "version_number": version_number},
        },
        tenant_id=identity.tenant_id,
    )

    return {
        "status": "ok",
        "file_id": file_id,
        "name": meta.name,
        "content_hash": meta.content_hash,
        "size": meta.size,
        "rolled_back_to": version_number,
    }


# ── Chunked Upload Endpoints ───────────────────────────────


@router.post("/upload/chunked/init")
async def init_chunked_upload(
    body: UploadSessionCreate,
    authorization: str | None = Header(None),
):
    """Initialize a chunked upload session.

    DEC-⑦-㉕：会话行归属=bind_identity 派生租户（匿名 'default' 契约不变、
    无自报轴）；坏 token 401 先于建会话零副作用。
    """
    identity = bind_identity(authorization)
    session = uploader.create_session(
        filename=body.filename,
        total_size=body.total_size,
        chunk_size=body.chunk_size,
        mime_type=body.mime_type,
        tags=body.tags,
        tenant_id=identity.tenant_id,
    )
    return session.to_dict()


@router.post("/upload/chunked/{upload_id}/chunk/{chunk_index}")
async def upload_chunk(
    upload_id: str,
    chunk_index: int,
    file: UploadFile = File(...),
    authorization: str | None = Header(None),
):
    """Upload a single chunk.

    DEC-⑦-㉕：归属闸——跨租户会话与 not-found 同 404 同 detail 不差分泄露，
    分片字节零落盘（禁向他人会话投毒）。
    """
    identity = bind_identity(authorization)
    session = uploader.get_session(upload_id, tenant_id=identity.tenant_id)
    if not session:
        raise HTTPException(status_code=404, detail="Upload session not found")

    data = await file.read()
    updated = uploader.upload_chunk(upload_id, chunk_index, data, tenant_id=identity.tenant_id)
    if not updated:
        raise HTTPException(status_code=400, detail="Failed to store chunk")

    return updated.to_dict()


@router.post("/upload/chunked/{upload_id}/complete")
async def complete_chunked_upload(
    upload_id: str,
    authorization: str | None = Header(None),
):
    """Complete a chunked upload — reassemble and store.

    DEC-⑦-④：落库行归属=bind_identity 派生租户（匿名 'default' 契约不变）。
    """
    identity = bind_identity(authorization)
    session = uploader.get_session(upload_id, tenant_id=identity.tenant_id)
    if not session:
        raise HTTPException(status_code=404, detail="Upload session not found")
    if not session.is_complete:
        raise HTTPException(
            status_code=400,
            detail=f"Upload incomplete: {session.progress}% ({len(session.received_chunks)}/{session.total_chunks} chunks)",
        )

    result = uploader.assemble(upload_id, tenant_id=identity.tenant_id)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to assemble chunks")

    data, sess = result
    meta = store.store(
        data=data,
        name=sess.filename,
        mime_type=sess.mime_type,
        tags=sess.tags,
        tenant_id=identity.tenant_id,
    )

    # Auto-cache（㉕b：行归属=文件行租户键）
    cache.put(f"file:{meta.file_id}", data, tenant_id=file_row_tenant(meta))

    # Cleanup temp chunks
    uploader.cleanup(upload_id)

    return {
        "status": "ok",
        "file_id": meta.file_id,
        "name": meta.name,
        "content_hash": meta.content_hash,
        "size": meta.size,
        "elapsed_seconds": sess.elapsed_seconds,
    }


@router.get("/upload/chunked/{upload_id}")
async def get_upload_status(
    upload_id: str,
    authorization: str | None = Header(None),
):
    """Get the status of a chunked upload session.

    DEC-⑦-㉕：归属闸——跨租户与 not-found 同 404 同 detail（⑦-㉔ 同契约）。
    """
    identity = bind_identity(authorization)
    session = uploader.get_session(upload_id, tenant_id=identity.tenant_id)
    if not session:
        raise HTTPException(status_code=404, detail="Upload session not found")
    return session.to_dict()


@router.get("/upload/chunked")
async def list_uploads(authorization: str | None = Header(None)):
    """List all active upload sessions.

    DEC-⑦-㉕：读面恒按派生租户过滤（匿名 'default' 契约不变；修前零鉴权恒
    全量=任意调用方可见全部租户文件名）。
    """
    identity = bind_identity(authorization)
    return {"sessions": uploader.list_sessions(tenant_id=identity.tenant_id)}


@router.delete("/upload/chunked/{upload_id}")
async def cancel_upload(
    upload_id: str,
    authorization: str | None = Header(None),
):
    """Cancel and cleanup an upload session.

    DEC-⑦-㉕：归属闸——跨租户与 not-found 同 404 同 detail+行零破坏
    （不删他人分片目录 ③b-3）。
    """
    identity = bind_identity(authorization)
    if not uploader.cleanup(upload_id, tenant_id=identity.tenant_id):
        raise HTTPException(status_code=404, detail="Upload session not found")
    return {"status": "ok", "upload_id": upload_id}


# ── Cache Endpoints ────────────────────────────────────────


@router.get("/cache/stats")
async def get_cache_stats():
    """Get cache statistics.

    DEC-⑦-㉕b：系统视图（计数含全部租户行，/api/vein/health 消费计数字节
    不变，㉕ health 计数同款纪律）。
    """
    return cache.stats


@router.get("/cache/{key}")
async def get_cached(key: str, authorization: str | None = Header(None)):
    """Get a cached value by key.

    DEC-⑦-㉕b：读面归属闸——tenant 由 bind_identity 派生（匿名 'default'
    契约不变、无自报轴），跨租户与 miss 同 404 同 detail 不差分泄露 ③c-1
    （file: 热缓存行同样不得经此面跨租户直读）。
    """
    identity = bind_identity(authorization)
    entry = cache.get_entry(key, tenant_id=identity.tenant_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Cache miss or expired")
    return {
        "key": key,
        "size": entry.size,
        "data": entry.data.hex()[:100] + "...",
        "tenant_id": cache_row_tenant(entry),
    }


@router.put("/cache")
async def put_cache(body: CachePutRequest, authorization: str | None = Header(None)):
    """Put a value into the cache.

    DEC-⑦-㉕b：写点归属=bind_identity 派生租户（匿名 'default' 契约不变、
    请求体无 tenant 自报轴）；同 key 异租户=两个独立缓存行互不覆盖（③a
    幂等键隔离同款语义收口，修前全局单键=跨租户互覆盖）。
    """
    identity = bind_identity(authorization)
    import base64

    try:
        data = base64.b64decode(body.data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 data")
    entry = cache.put(body.key, data, ttl=body.ttl, tenant_id=identity.tenant_id)
    return {
        "status": "ok",
        "key": body.key,
        "size": len(data),
        "tenant_id": cache_row_tenant(entry),
    }


@router.delete("/cache/{key}")
async def invalidate_cache(key: str, authorization: str | None = Header(None)):
    """Invalidate a specific cache entry.

    DEC-⑦-㉕b：删面归属闸——tenant 由 bind_identity 派生，跨租户=404 与
    not-found 同 detail 不差分泄露+行零破坏（他人缓存行一个字节不动 ③b-3）。
    """
    identity = bind_identity(authorization)
    if not cache.invalidate(key, tenant_id=identity.tenant_id):
        raise HTTPException(status_code=404, detail="Key not in cache")
    return {"status": "ok", "key": key}


@router.post("/cache/clear")
async def clear_cache(authorization: str | None = Header(None)):
    """Clear the caller's tenant cache entries.

    DEC-⑦-㉕b：bulk 清理恒按行租户键清（防"只给租户"变相清全史，③a purge
    同款纪律）；tenant 由 bind_identity 派生、无自报轴。
    """
    identity = bind_identity(authorization)
    count = cache.clear(tenant_id=identity.tenant_id)
    return {"status": "ok", "cleared": count, "tenant_id": cache_row_tenant(identity.tenant_id)}


@router.post("/cache/cleanup")
async def cleanup_cache(authorization: str | None = Header(None)):
    """Remove expired cache entries of the caller's tenant.

    DEC-⑦-㉕b：bulk 清理恒按行租户键（clear 同款纪律，跨租户过期行不代删）。
    """
    identity = bind_identity(authorization)
    count = cache.cleanup_expired(tenant_id=identity.tenant_id)
    return {"status": "ok", "removed": count, "tenant_id": cache_row_tenant(identity.tenant_id)}


# ── Promote to Knowledge ───────────────────────────────────


class PromoteRequest(BaseModel):
    user_id: str = "default"
    tags: list[str] | None = None


@router.post("/files/{file_id}/promote")
async def promote_to_knowledge(
    file_id: str,
    req: PromoteRequest | None = None,
    authorization: str | None = Header(None),
):
    """Promote a Vein file to the knowledge base.

    Reads the file content from Vein and creates a knowledge entry in OpenSoul,
    enabling RAG search, graph linking, and full-text retrieval.
    """
    identity = bind_identity(authorization)
    if req is None:
        req = PromoteRequest()

    # DEC-⑦-⑩ by-ID 读面归属闸：跨租户 404 零副作用（不入库 knowledge/tags）
    meta = store.get_meta(file_id, tenant_id=identity.tenant_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")

    # Read file content
    result = store.retrieve(file_id, tenant_id=identity.tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="File content not found")
    data, _meta = result

    # Try to decode as text for knowledge entry
    content = ""
    is_text = meta.mime_type.startswith("text/") or meta.mime_type in (
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
        "application/yaml",
    )
    if is_text:
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content = data.decode("latin-1")
            except Exception:
                content = f"[Binary file: {meta.name}, {meta.size} bytes]"
    else:
        content = f"[Binary file: {meta.name}, {meta.size} bytes, type: {meta.mime_type}]"

    # Build tags
    file_tags = ["vein", "file", meta.mime_type.split("/")[-1]]
    if req.tags:
        file_tags.extend(req.tags)
    if meta.tags:
        file_tags.extend(meta.tags.split(","))

    # Insert into knowledge base
    import json
    import time as _time
    import uuid as _uuid

    try:
        from src.database.postgres import db_pool

        knowledge_id = str(_uuid.uuid4())
        await db_pool.execute(
            """INSERT INTO knowledge (id, user_id, title, content, source, content_type, metadata, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            knowledge_id,
            req.user_id,
            meta.name,
            content[:100000],  # Cap at 100KB for knowledge
            f"vein://{file_id}",
            meta.mime_type,
            json.dumps({"tags": list(set(file_tags)), "vein_file_id": file_id}),
            _time.time(),
            _time.time(),
        )

        # fire-and-forget: meilisearch增量索引（失败不阻塞主流程）
        try:
            from src.services.meili_indexer import schedule_index

            schedule_index(knowledge_id)
        except Exception:
            pass
        # Add tags to knowledge_tags table
        for tag_name in set(file_tags):
            tag_id = str(_uuid.uuid4())
            await db_pool.execute(
                "INSERT INTO tags (id, name, user_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                tag_id,
                tag_name,
                req.user_id,
            )
            tag = await db_pool.fetchrow(
                "SELECT id FROM tags WHERE name = $1 AND user_id = $2", tag_name, req.user_id
            )
            if tag:
                await db_pool.execute(
                    "INSERT INTO knowledge_tags (knowledge_id, tag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    knowledge_id,
                    tag["id"],
                )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create knowledge entry: {e}")

    # Push event to nerve
    try:
        push_event(
            {
                "type": "vein.promoted",
                "file_id": file_id,
                "filename": meta.name,
                "user_id": req.user_id,
                "content_length": len(content),
            },
            tenant_id=identity.tenant_id,
        )
    except Exception as exc:
        logging.getLogger(__name__).debug("probe skipped: %s", exc)

    return {
        "promoted": True,
        "file_id": file_id,
        "filename": meta.name,
        "user_id": req.user_id,
        "content_length": len(content),
        "tags": file_tags,
    }


# ── Auto-Process (Vein → Sense → Knowledge) ───────────────


class AutoProcessRequest(BaseModel):
    user_id: str = "default"
    language: str | None = None
    auto_promote: bool = True  # auto-promote OCR/ASR results to knowledge base


@router.post("/files/{file_id}/auto-process")
async def auto_process_file(
    file_id: str,
    req: AutoProcessRequest | None = None,
    authorization: str | None = Header(None),
):
    """Auto-detect file type and process through Sense (OCR/ASR), then promote to knowledge.

    Pipeline: Vein (storage) → Sense (OCR/ASR) → Soul (knowledge base)
    Supported:
      - Images (png, jpg, gif, webp, bmp, tiff) → Smart OCR
      - PDFs → Smart PDF OCR
      - Audio (wav, mp3, ogg, flac, webm, m4a) → ASR transcription
    """
    identity = bind_identity(authorization)
    if req is None:
        req = AutoProcessRequest()

    # DEC-⑦-⑩ by-ID 读面归属闸：跨租户 404 零副作用（不进 Sense/知识入库）
    meta = store.get_meta(file_id, tenant_id=identity.tenant_id)
    if not meta:
        raise HTTPException(status_code=404, detail="File not found")

    # Retrieve file content
    result = store.retrieve(file_id, tenant_id=identity.tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="File content not found")
    data, _meta = result

    mime = meta.mime_type.lower()
    filename = meta.name.lower()

    # Determine processing route
    image_types = {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
    }
    pdf_types = {"application/pdf"}
    audio_types = {
        "audio/wav",
        "audio/x-wav",
        "audio/mp3",
        "audio/mpeg",
        "audio/ogg",
        "audio/flac",
        "audio/webm",
        "audio/mp4",
        "audio/x-m4a",
    }

    extracted_text = ""
    engine_used = "none"
    processing_type = "unknown"

    try:
        if mime in image_types or any(
            filename.endswith(ext)
            for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff")
        ):
            # Route to OCR
            from src.api.sense import _get_gateway as _get_sense_gw
            from src.sense.ocr import OCREngine

            _get_sense_gw()
            ocr = OCREngine()
            ocr_result = await ocr.smart_image_to_text(data, language=req.language)
            extracted_text = ocr_result.text
            engine_used = ocr_result.engine
            processing_type = "ocr"

        elif mime in pdf_types or filename.endswith(".pdf"):
            # Route to PDF OCR
            from src.api.sense import _get_gateway as _get_sense_gw
            from src.sense.ocr import OCREngine

            _get_sense_gw()
            ocr = OCREngine()
            ocr_result = await ocr.smart_pdf_to_text(data, language=req.language, max_pages=30)
            extracted_text = ocr_result.text
            engine_used = ocr_result.engine
            processing_type = "pdf_ocr"

        elif mime in audio_types or any(
            filename.endswith(ext) for ext in (".wav", ".mp3", ".ogg", ".flac", ".webm", ".m4a")
        ):
            # Route to ASR
            from src.api.sense import _get_gateway as _get_sense_gw
            from src.sense.asr import ASREngine

            _get_sense_gw()
            asr = ASREngine()
            fmt = "wav"
            if filename.endswith(".mp3") or "mp3" in mime or "mpeg" in mime:
                fmt = "mp3"
            elif filename.endswith(".ogg") or "ogg" in mime:
                fmt = "ogg"
            elif filename.endswith(".flac") or "flac" in mime:
                fmt = "flac"
            elif filename.endswith(".m4a") or "m4a" in mime:
                fmt = "m4a"
            asr_result = await asr.transcribe_async(data, language=req.language, format=fmt)
            extracted_text = asr_result.text
            engine_used = asr_result.engine
            processing_type = "asr"

        else:
            # Unsupported type — try OCR as fallback for unknown image-like types
            if mime.startswith("image/"):
                from src.sense.ocr import OCREngine

                ocr = OCREngine()
                ocr_result = ocr.image_to_text(data, lang=req.language)
                extracted_text = ocr_result.text
                engine_used = ocr_result.engine
                processing_type = "ocr_fallback"
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type for auto-processing: {mime}. Supported: images, PDFs, audio.",
                )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

    if not extracted_text or not extracted_text.strip():
        return {
            "status": "no_text_extracted",
            "file_id": file_id,
            "processing_type": processing_type,
            "engine": engine_used,
            "text_length": 0,
        }

    # Auto-promote to knowledge base if requested
    promoted = False
    knowledge_id = None
    if req.auto_promote and extracted_text.strip():
        try:
            import json
            import time as _time
            import uuid as _uuid

            from src.database.postgres import db_pool

            tags = ["auto-processed", processing_type, engine_used, meta.mime_type.split("/")[-1]]
            if meta.tags:
                tags.extend(meta.tags.split(","))
            knowledge_id = str(_uuid.uuid4())
            await db_pool.execute(
                """INSERT INTO knowledge (id, user_id, title, content, source, content_type, metadata, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                knowledge_id,
                req.user_id,
                f"[{processing_type.upper()}] {meta.name}",
                extracted_text[:100000],
                f"vein://{file_id}",
                meta.mime_type,
                json.dumps(
                    {
                        "tags": list(set(tags)),
                        "processing_type": processing_type,
                        "engine": engine_used,
                    }
                ),
                _time.time(),
            )

            # fire-and-forget: meilisearch增量索引（失败不阻塞主流程）
            try:
                from src.services.meili_indexer import schedule_index

                schedule_index(knowledge_id)
            except Exception:
                pass
            # Add tags to knowledge_tags table
            for tag_name in set(tags):
                tag_id = str(_uuid.uuid4())
                await db_pool.execute(
                    "INSERT INTO tags (id, name, user_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    tag_id,
                    tag_name,
                    req.user_id,
                )
                tag = await db_pool.fetchrow(
                    "SELECT id FROM tags WHERE name = $1 AND user_id = $2", tag_name, req.user_id
                )
                if tag:
                    await db_pool.execute(
                        "INSERT INTO knowledge_tags (knowledge_id, tag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        knowledge_id,
                        tag["id"],
                    )
            promoted = True
        except Exception:
            pass  # non-fatal

    # Emit event
    push_event(
        {
            "organ": "vein",
            "emoji": "🩸",
            "type": "file_auto_processed",
            "summary": f"🔍 Auto-processed: {meta.name} via {processing_type} ({engine_used})",
            "detail": {
                "file_id": file_id,
                "processing_type": processing_type,
                "engine": engine_used,
                "text_length": len(extracted_text),
                "promoted": promoted,
            },
        },
        tenant_id=identity.tenant_id,
    )

    return {
        "status": "ok",
        "file_id": file_id,
        "filename": meta.name,
        "processing_type": processing_type,
        "engine": engine_used,
        "text_length": len(extracted_text),
        "text_preview": extracted_text[:500],
        "promoted_to_knowledge": promoted,
    }


class BatchAutoProcessRequest(BaseModel):
    user_id: str = "default"
    language: str | None = None
    auto_promote: bool = True
    mime_filter: list[str] | None = (
        None  # e.g. ["image/", "application/pdf"] to only process images and PDFs
    )
    limit: int = 50


@router.post("/auto-process/batch")
async def batch_auto_process(
    req: BatchAutoProcessRequest,
    authorization: str | None = Header(None),
):
    """Batch auto-process all eligible files through Sense (OCR/ASR).

    Processes files that match the mime_filter criteria.
    Returns summary of all processing results.
    """
    identity = bind_identity(authorization)
    # Get all files
    # DEC-⑦-⑩ by-ID 读面归属闸：批量面同闸——列本租户行+逐行读点带派生租户键
    # （修前 list 全租户+retrieve 零闸=批量跨租户读他人文件内容）。
    all_files = store.list_files(limit=req.limit, offset=0, tenant_id=identity.tenant_id)

    # Filter by mime type if specified
    eligible = []
    for f in all_files:
        if req.mime_filter:
            if any(f.mime_type.lower().startswith(prefix) for prefix in req.mime_filter):
                eligible.append(f)
        else:
            # Default: process images, PDFs, and audio
            mime = f.mime_type.lower()
            if mime.startswith("image/") or mime == "application/pdf" or mime.startswith("audio/"):
                eligible.append(f)

    results = []
    for f in eligible:
        try:
            # Retrieve file content
            file_result = store.retrieve(f.file_id, tenant_id=identity.tenant_id)
            if not file_result:
                results.append(
                    {
                        "file_id": f.file_id,
                        "filename": f.name,
                        "status": "error",
                        "error": "File content not found",
                    }
                )
                continue

            data, _meta = file_result
            mime = f.mime_type.lower()
            f.name.lower()

            extracted_text = ""
            engine_used = "none"
            processing_type = "unknown"

            if mime.startswith("image/"):
                from src.sense.ocr import OCREngine

                ocr = OCREngine()
                ocr_result = ocr.image_to_text(data, lang=req.language)
                extracted_text = ocr_result.text
                engine_used = ocr_result.engine
                processing_type = "ocr"
            elif mime == "application/pdf":
                from src.sense.ocr import OCREngine

                ocr = OCREngine()
                ocr_result = await ocr.smart_pdf_to_text(data, language=req.language, max_pages=20)
                extracted_text = ocr_result.text
                engine_used = ocr_result.engine
                processing_type = "pdf_ocr"
            elif mime.startswith("audio/"):
                from src.sense.asr import ASREngine

                asr = ASREngine()
                fmt = "wav"
                if "mp3" in mime or "mpeg" in mime:
                    fmt = "mp3"
                elif "ogg" in mime:
                    fmt = "ogg"
                elif "flac" in mime:
                    fmt = "flac"
                asr_result = await asr.transcribe_async(data, language=req.language, format=fmt)
                extracted_text = asr_result.text
                engine_used = asr_result.engine
                processing_type = "asr"

            promoted = False
            if req.auto_promote and extracted_text.strip():
                try:
                    import json
                    import time as _time
                    import uuid as _uuid

                    from src.database.postgres import db_pool

                    tags = ["auto-processed", "batch", processing_type]
                    kid = str(_uuid.uuid4())
                    await db_pool.execute(
                        """INSERT INTO knowledge (id, user_id, title, content, source, content_type, metadata, created_at, updated_at)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                        kid,
                        req.user_id,
                        f"[{processing_type.upper()}] {f.name}",
                        extracted_text[:100000],
                        f"vein://{f.file_id}",
                        f.mime_type,
                        json.dumps({"tags": tags, "batch": True}),
                        _time.time(),
                        _time.time(),
                    )
                    # fire-and-forget: meilisearch增量索引（失败不阻塞主流程）
                    try:
                        from src.services.meili_indexer import schedule_index

                        schedule_index(kid)
                    except Exception:
                        pass
                    # Add tags to knowledge_tags table
                    for tag_name in set(tags):
                        tag_id = str(_uuid.uuid4())
                        await db_pool.execute(
                            "INSERT INTO tags (id, name, user_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                            tag_id,
                            tag_name,
                            req.user_id,
                        )
                        tag_row = await db_pool.fetchrow(
                            "SELECT id FROM tags WHERE name = $1 AND user_id = $2",
                            tag_name,
                            req.user_id,
                        )
                        if tag_row:
                            await db_pool.execute(
                                "INSERT INTO knowledge_tags (knowledge_id, tag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                                kid,
                                tag_row["id"],
                            )
                    promoted = True
                except Exception as exc:
                    logging.getLogger(__name__).debug("probe skipped: %s", exc)
            results.append(
                {
                    "file_id": f.file_id,
                    "filename": f.name,
                    "status": "ok",
                    "processing_type": processing_type,
                    "engine": engine_used,
                    "text_length": len(extracted_text),
                    "promoted": promoted,
                }
            )
        except Exception as e:
            results.append(
                {"file_id": f.file_id, "filename": f.name, "status": "error", "error": str(e)}
            )

    ok_count = sum(1 for r in results if r["status"] == "ok")
    promoted_count = sum(1 for r in results if r.get("promoted"))

    push_event(
        {
            "organ": "vein",
            "emoji": "🩸",
            "type": "batch_auto_processed",
            "summary": f"🔍 Batch auto-processed: {ok_count}/{len(results)} files",
            "detail": {"total": len(results), "ok": ok_count, "promoted": promoted_count},
        },
        tenant_id=identity.tenant_id,
    )

    return {
        "status": "ok",
        "total_files": len(all_files),
        "eligible_files": len(eligible),
        "processed": ok_count,
        "promoted": promoted_count,
        "results": results,
    }


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def vein_health():
    """OpenVein health check."""
    return {
        "status": "ok",
        "component": "OpenVein",
        "store": store.stats(),
        "cache": cache.stats,
        "uploads": {
            "active_sessions": len(uploader.list_sessions()),
        },
    }


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def get_stats():
    """Get overall OpenVein statistics."""
    return {
        "store": store.stats(),
        "cache": cache.stats,
        "uploads": {
            "active_sessions": len(uploader.list_sessions()),
        },
    }
