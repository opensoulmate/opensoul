"""OpenPipeline API — 跨组件智能流水线：文件上传→安全扫描→内容提取→知识入库。

Chains: Vein → Immune → Sense → Soul (Knowledge)
One upload, automatic multi-organ processing.
"""

import asyncio
import logging
import time

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel

from src.nerve.event_bridge import push_event
from src.services.tenant_binding import bind_identity

router = APIRouter()
logger = logging.getLogger(__name__)

# 知识入库步骤的墙钟上限：embedding降级预算12s+DB操作留余量；
# 超时→步骤标记error并返回，上传请求永不挂死（此前embedding挂起≈3分钟→客户端ReadTimeout）
KNOWLEDGE_STEP_TIMEOUT = 20.0


# ── Request Schemas ────────────────────────────────────────


class PipelineRunRequest(BaseModel):
    """Run a pipeline on an existing Vein file."""

    file_id: str
    pipeline: str = "auto"  # "auto", "ocr", "asr", "text", "full"
    user_id: str = "default"
    tags: list[str] | None = None
    skip_immune: bool = False
    skip_knowledge: bool = False


class PipelineStatus(BaseModel):
    pipeline_id: str
    status: str  # "running", "completed", "failed", "partial"
    steps: list[dict]
    started_at: float
    finished_at: float | None = None
    result: dict | None = None
    error: str | None = None


# ── In-memory pipeline history ────────────────────────────
_pipeline_history: dict[str, dict] = {}


def _detect_pipeline(mime_type: str, filename: str) -> str:
    """Auto-detect which pipeline to use based on file type."""
    if mime_type.startswith("image/"):
        return "ocr"
    if mime_type.startswith("audio/"):
        return "asr"
    if mime_type.startswith("video/"):
        return "video"
    # Text-based files
    text_types = {
        "text/",
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
        "application/yaml",
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument",
    }
    if any(mime_type.startswith(t) for t in text_types):
        return "text"
    # Check extension
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff"):
        return "ocr"
    if ext in ("mp3", "wav", "ogg", "flac", "m4a", "webm"):
        return "asr"
    if ext in ("mp4", "avi", "mov", "mkv"):
        return "video"
    if ext in ("pdf", "doc", "docx", "txt", "md", "csv", "json", "yaml", "yml", "xml", "html"):
        return "text"
    return "text"  # default fallback


async def _step_immune_scan(text: str) -> dict:
    """Run content through Immune system."""
    try:
        from src.immune.moderator import ContentModerator

        moderator = ContentModerator()
        result = moderator.moderate(text)
        return {
            "step": "immune",
            "status": "ok",
            "is_safe": result.is_safe,
            "risk_level": result.risk_level,
            "findings_count": len(result.findings),
            "redacted_available": bool(result.redacted_text),
        }
    except Exception as e:
        logger.warning(f"Immune scan failed: {e}")
        return {"step": "immune", "status": "skipped", "error": str(e)}


async def _step_sense_extract(
    data: bytes, mime_type: str, filename: str, pipeline_type: str
) -> dict:
    """Run content through Sense system for extraction."""
    try:
        if pipeline_type == "ocr":
            from src.sense.ocr import OCREngine

            engine = OCREngine()
            result = engine.image_to_text(data)
            return {
                "step": "sense-ocr",
                "status": "ok",
                "text": result.text[:5000],  # cap preview
                "confidence": result.confidence,
                "engine": result.engine,
                "text_length": len(result.text),
            }
        elif pipeline_type == "asr":
            from src.sense.asr import ASREngine

            engine = ASREngine()
            result = await engine.transcribe_async(
                data, format=filename.rsplit(".", 1)[-1] if "." in filename else "wav"
            )
            return {
                "step": "sense-asr",
                "status": "ok",
                "text": result.text[:5000],
                "language": result.language,
                "duration_seconds": result.duration_seconds,
                "engine": result.engine,
                "text_length": len(result.text),
            }
        elif pipeline_type == "text":
            # Try to decode as text
            text = ""
            for encoding in ("utf-8", "latin-1", "gbk"):
                try:
                    text = data.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if not text:
                text = f"[Binary: {filename}, {len(data)} bytes]"
            return {
                "step": "sense-text",
                "status": "ok",
                "text": text[:5000],
                "text_length": len(text),
                "encoding": encoding if text else "binary",
            }
        elif pipeline_type == "video":
            from src.sense.multimodal import MultimodalAnalyzer

            analyzer = MultimodalAnalyzer()
            result = analyzer.analyze_video(data)
            return {
                "step": "sense-video",
                "status": "ok",
                "duration": result.duration,
                "width": result.width,
                "height": result.height,
                "fps": result.fps,
                "codec": result.codec,
            }
        else:
            return {
                "step": "sense",
                "status": "skipped",
                "reason": f"Unknown pipeline: {pipeline_type}",
            }
    except Exception as e:
        logger.warning(f"Sense extraction failed: {e}")
        return {"step": "sense", "status": "error", "error": str(e)}


async def _step_knowledge_import(title: str, content: str, tags: list[str], user_id: str) -> dict:
    """Import extracted content into the knowledge base."""
    try:
        import uuid

        from src.database.postgres import db_pool
        from src.models.knowledge import KnowledgeCreate
        from src.services.auth import register_user
        from src.services.knowledge import create_knowledge

        if not content or len(content.strip()) < 10:
            return {"step": "knowledge", "status": "skipped", "reason": "Content too short"}

        # Normalize user_id to UUID format
        try:
            uid = uuid.UUID(user_id)
        except ValueError:
            uid = uuid.uuid5(uuid.NAMESPACE_DNS, user_id)

        # Ensure the user exists in the database
        existing = await db_pool.fetchrow("SELECT id FROM users WHERE id = $1", str(uid))
        if not existing:
            # Try to find by username
            by_name = await db_pool.fetchrow("SELECT id FROM users WHERE username = $1", user_id)
            if by_name:
                uid = uuid.UUID(by_name["id"])
            else:
                # Create a system user for pipeline imports
                try:
                    sys_user = await register_user(
                        username=user_id,
                        email=f"{user_id}@pipeline.local",
                        password="pipeline-system-no-login",
                        role="user",
                    )
                    uid = uuid.UUID(sys_user["id"])
                except Exception as reg_err:
                    # User might already exist
                    by_name = await db_pool.fetchrow(
                        "SELECT id FROM users WHERE username = $1", user_id
                    )
                    if by_name:
                        uid = uuid.UUID(by_name["id"])
                    else:
                        return {
                            "step": "knowledge",
                            "status": "error",
                            "error": f"Cannot create user: {reg_err}",
                        }

        data = KnowledgeCreate(
            title=title,
            content=content[:100000],
            source="pipeline",
            content_type="text/plain",
            tags=tags,
            metadata={"source": "pipeline", "auto_import": True},
        )

        row = await create_knowledge(data, uid)

        return {
            "step": "knowledge",
            "status": "ok",
            "entry_id": row.get("id", ""),
            "title": title,
            "content_length": len(content),
            "tags": tags,
        }
    except Exception as e:
        logger.warning(f"Knowledge import failed: {e}")
        return {"step": "knowledge", "status": "error", "error": str(e)}


# ── Pipeline Endpoints ────────────────────────────────────


@router.post("/upload")
async def pipeline_upload(
    file: UploadFile = File(...),
    pipeline: str = Form(default="auto"),
    user_id: str = Form(default="default"),
    tags: str = Form(default=""),
    skip_immune: bool = Form(default=False),
    skip_knowledge: bool = Form(default=False),
    authorization: str | None = Header(None),
):
    """Upload a file and run it through the smart processing pipeline.

    Pipeline stages:
    1. Vein: Store the file (content-addressable, dedup)
    2. Immune: Security scan the extracted text
    3. Sense: Extract content (OCR/ASR/text)
    4. Soul: Import to knowledge base

    Set pipeline="auto" for auto-detection, or specify: ocr, asr, text, video.
    """
    identity = bind_identity(authorization)
    pipeline_id = f"pipe_{int(time.time() * 1000)}"
    started_at = time.time()
    steps = []
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    data = await file.read()
    mime_type = file.content_type or "application/octet-stream"
    filename = file.filename or "unnamed"

    # Auto-detect pipeline
    if pipeline == "auto":
        pipeline = _detect_pipeline(mime_type, filename)

    # Step 1: Store in Vein
    try:
        from src.vein.file_store import FileStore

        store = FileStore()
        meta = store.store(
            data=data,
            name=filename,
            mime_type=mime_type,
            tags=tag_list + ["pipeline", pipeline],
            tenant_id=identity.tenant_id,
        )
        steps.append(
            {
                "step": "vein",
                "status": "ok",
                "file_id": meta.file_id,
                "name": meta.name,
                "size": meta.size,
                "content_hash": meta.content_hash,
            }
        )
        file_id = meta.file_id
    except Exception as e:
        steps.append({"step": "vein", "status": "error", "error": str(e)})
        _pipeline_history[pipeline_id] = {
            "pipeline_id": pipeline_id,
            "status": "failed",
            "steps": steps,
            "started_at": started_at,
            "finished_at": time.time(),
            "error": f"Vein storage failed: {e}",
        }
        raise HTTPException(500, f"File storage failed: {e}")

    # Step 2: Sense — Extract content
    extraction = await _step_sense_extract(data, mime_type, filename, pipeline)
    steps.append(extraction)
    extracted_text = extraction.get("text", "")

    # Step 3: Immune — Security scan
    if not skip_immune and extracted_text:
        immune_result = await _step_immune_scan(extracted_text)
        steps.append(immune_result)

        # If content is flagged as high risk, stop pipeline
        if immune_result.get("risk_level") == "high":
            _pipeline_history[pipeline_id] = {
                "pipeline_id": pipeline_id,
                "status": "blocked",
                "steps": steps,
                "started_at": started_at,
                "finished_at": time.time(),
                "error": "Content blocked by Immune: high risk",
            }
            push_event(
                {
                    "organ": "pipeline",
                    "emoji": "🔄",
                    "type": "pipeline_blocked",
                    "summary": f"🚫 Pipeline blocked: high-risk content in {filename}",
                    "detail": {"pipeline_id": pipeline_id, "file_id": file_id},
                },
                tenant_id=identity.tenant_id,
            )
            return {
                "pipeline_id": pipeline_id,
                "status": "blocked",
                "file_id": file_id,
                "steps": steps,
                "error": "Content blocked by security scan (high risk)",
            }

    # Step 4: Knowledge import（wait_for有界：超时=步骤error可见，不拖垮整个上传请求）
    if not skip_knowledge and extracted_text:
        knowledge_tags = tag_list + ["pipeline", pipeline, "auto-import"]
        try:
            knowledge_result = await asyncio.wait_for(
                _step_knowledge_import(
                    title=f"[Pipeline] {filename}",
                    content=extracted_text,
                    tags=knowledge_tags,
                    user_id=user_id,
                ),
                timeout=KNOWLEDGE_STEP_TIMEOUT,
            )
        except TimeoutError:
            knowledge_result = {
                "step": "knowledge",
                "status": "error",
                "error": f"knowledge import timed out after {KNOWLEDGE_STEP_TIMEOUT:.0f}s (bounded so upload never hangs)",
            }
        steps.append(knowledge_result)

    finished_at = time.time()
    status = "completed"
    if any(s.get("status") == "error" for s in steps):
        status = "partial"

    result = {
        "pipeline_id": pipeline_id,
        "status": status,
        "file_id": file_id,
        "pipeline_type": pipeline,
        "steps": steps,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_ms": round((finished_at - started_at) * 1000),
    }

    _pipeline_history[pipeline_id] = result

    # Emit event
    push_event(
        {
            "organ": "pipeline",
            "emoji": "🔄",
            "type": "pipeline_completed",
            "summary": f"✅ Pipeline [{pipeline}] completed: {filename} ({round((finished_at - started_at) * 1000)}ms)",
            "detail": {
                "pipeline_id": pipeline_id,
                "file_id": file_id,
                "pipeline_type": pipeline,
                "steps_completed": sum(1 for s in steps if s.get("status") == "ok"),
                "steps_total": len(steps),
            },
        },
        tenant_id=identity.tenant_id,
    )

    return result


@router.post("/run")
async def pipeline_run(
    req: PipelineRunRequest,
    authorization: str | None = Header(None),
):
    """Run a pipeline on an existing Vein file by file_id."""
    identity = bind_identity(authorization)
    pipeline_id = f"pipe_{int(time.time() * 1000)}"
    started_at = time.time()
    steps = []

    # Load file from Vein
    try:
        from src.vein.file_store import FileStore

        store = FileStore()
        # DEC-⑦-⑩ by-ID 读面归属闸：跨租户=404 与 not-found 同契约（detail 沿用
        # "File not found in Vein" 不差分）
        result = store.retrieve(req.file_id, tenant_id=identity.tenant_id)
        if not result:
            raise HTTPException(404, "File not found in Vein")
        data, meta = result
        steps.append(
            {
                "step": "vein",
                "status": "ok",
                "file_id": meta.file_id,
                "name": meta.name,
                "size": meta.size,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to load file: {e}")

    # Detect pipeline
    pipeline = req.pipeline
    if pipeline == "auto":
        pipeline = _detect_pipeline(meta.mime_type, meta.name)

    # Extract content
    extraction = await _step_sense_extract(data, meta.mime_type, meta.name, pipeline)
    steps.append(extraction)
    extracted_text = extraction.get("text", "")

    # Immune scan
    if not req.skip_immune and extracted_text:
        immune_result = await _step_immune_scan(extracted_text)
        steps.append(immune_result)

    # Knowledge import（同样有界）
    if not req.skip_knowledge and extracted_text:
        tags = (req.tags or []) + ["pipeline", pipeline, "auto-import"]
        try:
            knowledge_result = await asyncio.wait_for(
                _step_knowledge_import(
                    title=f"[Pipeline] {meta.name}",
                    content=extracted_text,
                    tags=tags,
                    user_id=req.user_id,
                ),
                timeout=KNOWLEDGE_STEP_TIMEOUT,
            )
        except TimeoutError:
            knowledge_result = {
                "step": "knowledge",
                "status": "error",
                "error": f"knowledge import timed out after {KNOWLEDGE_STEP_TIMEOUT:.0f}s (bounded so pipeline never hangs)",
            }
        steps.append(knowledge_result)

    finished_at = time.time()
    status = "completed"
    if any(s.get("status") == "error" for s in steps):
        status = "partial"

    result = {
        "pipeline_id": pipeline_id,
        "status": status,
        "file_id": req.file_id,
        "pipeline_type": pipeline,
        "steps": steps,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_ms": round((finished_at - started_at) * 1000),
    }

    _pipeline_history[pipeline_id] = result

    push_event(
        {
            "organ": "pipeline",
            "emoji": "🔄",
            "type": "pipeline_completed",
            "summary": f"✅ Pipeline [{pipeline}] completed: {meta.name}",
            "detail": {"pipeline_id": pipeline_id, "file_id": req.file_id},
        },
        tenant_id=identity.tenant_id,
    )

    return result


@router.get("/history")
async def pipeline_history(limit: int = Query(default=50, ge=1, le=200)):
    """Get pipeline execution history."""
    items = sorted(_pipeline_history.values(), key=lambda x: x.get("started_at", 0), reverse=True)
    return {"pipelines": items[:limit], "total": len(items)}


@router.get("/history/{pipeline_id}")
async def get_pipeline(pipeline_id: str):
    """Get details of a specific pipeline run."""
    if pipeline_id not in _pipeline_history:
        raise HTTPException(404, "Pipeline not found")
    return _pipeline_history[pipeline_id]


@router.get("/types")
async def list_pipeline_types():
    """List available pipeline types and their descriptions."""
    return {
        "types": [
            {"key": "auto", "label": "自动检测", "description": "根据文件类型自动选择处理流水线"},
            {"key": "ocr", "label": "OCR识别", "description": "图片→文字提取（Tesseract/LLM）"},
            {"key": "asr", "label": "语音转写", "description": "音频→文字转写（Whisper/LLM）"},
            {"key": "text", "label": "文本提取", "description": "文档→纯文本提取"},
            {"key": "video", "label": "视频分析", "description": "视频→元数据+抽帧"},
        ],
        "stages": [
            {
                "key": "vein",
                "label": "血管存储",
                "emoji": "🩸",
                "description": "文件存储+去重+版本控制",
            },
            {
                "key": "sense",
                "label": "感官提取",
                "emoji": "👁",
                "description": "OCR/ASR/文本内容提取",
            },
            {
                "key": "immune",
                "label": "免疫扫描",
                "emoji": "🛡",
                "description": "敏感数据检测+风控",
            },
            {
                "key": "knowledge",
                "label": "知识入库",
                "emoji": "🧠",
                "description": "RAG向量化+全文索引",
            },
        ],
    }


# ── Stats ──────────────────────────────────────────────────


@router.get("/stats")
async def pipeline_stats():
    """OpenPipeline detailed statistics."""
    items = list(_pipeline_history.values())
    by_status = {}
    by_type = {}
    total_steps = 0
    total_elapsed = 0.0
    for p in items:
        s = p.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
        t = p.get("pipeline_type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        total_steps += len(p.get("steps", []))
        if p.get("elapsed_seconds"):
            total_elapsed += p["elapsed_seconds"]

    return {
        "status": "ok",
        "component": "OpenPipeline",
        "total_runs": len(items),
        "by_status": by_status,
        "by_type": by_type,
        "total_steps_executed": total_steps,
        "avg_elapsed_seconds": round(total_elapsed / len(items), 2) if items else 0,
        "available_pipelines": ["auto", "ocr", "asr", "text", "video"],
        "connected_organs": ["vein", "sense", "immune", "soul"],
    }


# ── Health ─────────────────────────────────────────────────


@router.get("/health")
async def pipeline_health():
    """OpenPipeline health check."""
    return {
        "status": "ok",
        "component": "OpenPipeline",
        "description": "跨组件智能流水线",
        "pipelines_run": len(_pipeline_history),
        "available_pipelines": ["auto", "ocr", "asr", "text", "video"],
        "connected_organs": ["vein", "sense", "immune", "soul"],
    }
