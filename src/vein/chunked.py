"""Chunked upload manager for large files.

Splits large files into chunks, tracks upload progress,
and reassembles when all chunks arrive.
"""

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def upload_session_row_tenant(raw: object) -> str:
    """上传会话行租户键单一判法（DEC-⑦-㉕；DEC-⑦-② message_row_tenant /
    ⑦-㉔ task_row_tenant 同款语义：自带键缺失/空白→'default' 兜底，键派生故障
    绝不吃掉落库）。raw=UploadSession 行对象（读侧过滤）/to_dict 行/原始租户值
    （写侧落行）三形同收，写读同一判法禁双源漂移（F4b-㉗ 纪律）。"""
    if isinstance(raw, dict):
        raw = raw.get("tenant_id")
    elif hasattr(raw, "tenant_id"):
        raw = getattr(raw, "tenant_id")
    return str(raw or "").strip() or "default"


def _session_visible(session: "UploadSession", tenant_id: str | None) -> bool:
    """by-ID 归属闸单一判法：显式租户=恒按行租户键比对（跨租户=不可见，
    与 not-found 同返回不差分泄露 ③c-1）；None=系统视图（内部清理面/health）。"""
    if tenant_id is None:
        return True
    return upload_session_row_tenant(session) == str(tenant_id).strip()


@dataclass
class UploadSession:
    upload_id: str
    filename: str
    total_size: int
    chunk_size: int
    total_chunks: int
    received_chunks: set[int] = field(default_factory=set)
    content_hash: str | None = None
    mime_type: str = "application/octet-stream"
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    tenant_id: str = "default"

    @property
    def progress(self) -> float:
        if self.total_chunks == 0:
            return 100.0
        return round(len(self.received_chunks) / self.total_chunks * 100, 2)

    @property
    def is_complete(self) -> bool:
        return len(self.received_chunks) >= self.total_chunks

    @property
    def elapsed_seconds(self) -> float:
        end = self.completed_at or time.time()
        return round(end - self.created_at, 2)

    def to_dict(self) -> dict:
        return {
            "upload_id": self.upload_id,
            "filename": self.filename,
            "total_size": self.total_size,
            "chunk_size": self.chunk_size,
            "total_chunks": self.total_chunks,
            "received_chunks": sorted(self.received_chunks),
            "progress": self.progress,
            "is_complete": self.is_complete,
            "elapsed_seconds": self.elapsed_seconds,
            "mime_type": self.mime_type,
            "tags": self.tags,
            "tenant_id": upload_session_row_tenant(self),
        }


class ChunkedUploader:
    """Manages chunked file uploads with resumable sessions."""

    DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024  # 5MB

    def __init__(
        self,
        temp_dir: str | None = None,
        max_sessions: int = 100,
        persist=None,
    ):
        self._temp = Path(temp_dir or os.path.expanduser("~/opensoul/data/vein/uploads"))
        self._temp.mkdir(parents=True, exist_ok=True)
        self._max_sessions = max_sessions
        self._sessions: dict[str, UploadSession] = {}
        # 会话行持久化真源（RV-①b-⑥-1）：persist 缺省 None=纯内存既有语义零变化；
        # 接 persist=写穿+懒回载，重启会话不丢（分片字节本就在盘=跨重启续传成真）。
        self._persist = persist
        self._loaded = False

    # === 上传会话行持久化（RV-①b-⑥-1）：写穿单一点 + 首触懒回载 ===

    def _ensure_loaded(self) -> None:
        """首触懒回载（MEM-④/RV-①b-⑤-1 先例）：persist=None 或已回载=零操作。

        回载失败不置 _loaded（下次触碰重试）+logger.error 可见，行真源仍在库
        （RV-①b-②④ 语义）；只补缺不覆盖（self._sessions 内存真源优先，禁回载
        洗掉进程内新态）。
        """
        if self._persist is None or self._loaded:
            return
        try:
            rows = self._persist.load_all()
        except Exception as e:
            logger.error(f"vein 上传会话回载失败（下次触碰重试）: {e}")
            return
        for data in rows:
            try:
                session = UploadSession(**data)
            except Exception as e:
                logger.error(f"vein 上传会话行不可解跳过: {e}")
                continue
            self._sessions.setdefault(session.upload_id, session)
        self._loaded = True

    def _sync_session(self, session: UploadSession) -> None:
        """会话状态写穿单一点（RV-①b-② _sync_entry/⑤-1 _sync_task 纪律：禁散落写点漂移）。

        全部会话变更点（建会话/收分片/组装落 content_hash）共用本口落盘。写失败
        =记账旁路纪律（RV-①b-①）：logger.error 可见不阻断上传本体（该态只活
        内存重启丢，非谎报 durable）。
        """
        if self._persist is None:
            return
        try:
            self._persist.upsert(
                upload_id=str(session.upload_id),
                filename=str(session.filename),
                total_size=int(session.total_size),
                chunk_size=int(session.chunk_size),
                total_chunks=int(session.total_chunks),
                received_chunks=json.dumps(sorted(session.received_chunks)),
                content_hash=None if session.content_hash is None else str(session.content_hash),
                mime_type=str(session.mime_type),
                tags=json.dumps(list(session.tags), ensure_ascii=False),
                created_at=float(session.created_at),
                completed_at=None if session.completed_at is None else float(session.completed_at),
                tenant_id=upload_session_row_tenant(session),
            )
        except Exception as e:
            logger.error(f"vein 上传会话写穿失败（该态只活内存）: {e}")

    def _drop_session(self, upload_id: str) -> None:
        """会话删行写穿（cleanup 变更点唯一删行口）。写失败=logger.error 可见，
        库行残留待下次清扫（诚实记，非静默丢）。"""
        if self._persist is None:
            return
        try:
            self._persist.delete(upload_id)
        except Exception as e:
            logger.error(f"vein 上传会话删行失败（库行残留待下次清扫）: {e}")

    def create_session(
        self,
        filename: str,
        total_size: int,
        chunk_size: int | None = None,
        mime_type: str = "application/octet-stream",
        tags: list[str] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> UploadSession:
        """Create a new upload session.

        DEC-⑦-㉕：行归属=调用方 bind_identity 派生租户（写读同判法落行，
        自报不在形参面=无自报轴）；缺失/空白→'default' 兜底不掉落库。"""
        self._ensure_loaded()
        self._cleanup_old_sessions()

        cs = chunk_size or self.DEFAULT_CHUNK_SIZE
        total_chunks = (total_size + cs - 1) // cs
        upload_id = uuid.uuid4().hex[:12]

        session = UploadSession(
            upload_id=upload_id,
            filename=filename,
            total_size=total_size,
            chunk_size=cs,
            total_chunks=total_chunks,
            mime_type=mime_type,
            tags=tags or [],
            tenant_id=upload_session_row_tenant(tenant_id),
        )

        # Create temp dir for this upload
        chunk_dir = self._temp / upload_id
        chunk_dir.mkdir(parents=True, exist_ok=True)

        self._sessions[upload_id] = session
        self._sync_session(session)
        return session

    def upload_chunk(
        self,
        upload_id: str,
        chunk_index: int,
        data: bytes,
        *,
        tenant_id: str | None = None,
    ) -> UploadSession | None:
        """Upload a single chunk. Returns session or None if not found/invisible.

        DEC-⑦-㉕：归属闸前置——跨租户与 not-found 同返回 None（API 同 404
        同 detail 不差分泄露），分片字节零落盘（禁向他人会话投毒）。"""
        self._ensure_loaded()
        session = self._sessions.get(upload_id)
        if not session or not _session_visible(session, tenant_id):
            return None

        chunk_path = self._temp / upload_id / f"chunk_{chunk_index:06d}"
        chunk_path.write_bytes(data)

        session.received_chunks.add(chunk_index)
        if session.is_complete:
            session.completed_at = time.time()
        self._sync_session(session)

        return session

    def get_session(self, upload_id: str, *, tenant_id: str | None = None) -> UploadSession | None:
        """DEC-⑦-㉕：单一读源本体收口——显式租户=跨租户回 None 与缺行同契约
        （未来消费方无法绕闸）；None=系统视图字节不变。"""
        self._ensure_loaded()
        session = self._sessions.get(upload_id)
        if session is None or not _session_visible(session, tenant_id):
            return None
        return session

    def list_sessions(self, *, tenant_id: str | None = None) -> list[dict]:
        """DEC-⑦-㉕：读面恒按行租户键过滤（显式=本租户可见行；None=系统视图
        全量字节不变=health 计数面）。"""
        self._ensure_loaded()
        return [s.to_dict() for s in self._sessions.values() if _session_visible(s, tenant_id)]

    def assemble(
        self, upload_id: str, *, tenant_id: str | None = None
    ) -> tuple[bytes, UploadSession] | None:
        """Reassemble chunks into complete file. Returns (data, session) or None.

        DEC-⑦-㉕：归属闸前置——跨租户与 not-found 同返回 None（禁组装他人
        分片内容落成自己的文件=内容窃取面收口）。"""
        self._ensure_loaded()
        session = self._sessions.get(upload_id)
        if not session or not session.is_complete or not _session_visible(session, tenant_id):
            return None

        chunk_dir = self._temp / upload_id
        hasher = hashlib.sha256()
        assembled = bytearray()

        for i in range(session.total_chunks):
            chunk_path = chunk_dir / f"chunk_{i:06d}"
            if not chunk_path.exists():
                return None
            chunk_data = chunk_path.read_bytes()
            assembled.extend(chunk_data)
            hasher.update(chunk_data)

        session.content_hash = hasher.hexdigest()
        self._sync_session(session)
        return bytes(assembled), session

    def cleanup(self, upload_id: str, *, tenant_id: str | None = None) -> bool:
        """Remove upload session and temp files.

        DEC-⑦-㉕：归属闸前置——跨租户与 not-found 同返回 False（API 同 404
        同 detail）+行零破坏（不 pop 不删他人分片目录 ③b-3）。"""
        self._ensure_loaded()
        session = self._sessions.get(upload_id)
        if not session or not _session_visible(session, tenant_id):
            return False
        self._sessions.pop(upload_id, None)
        self._drop_session(upload_id)

        chunk_dir = self._temp / upload_id
        if chunk_dir.exists():
            import shutil

            shutil.rmtree(chunk_dir)

        return True

    def _cleanup_old_sessions(self, max_age: float = 3600):
        """Remove sessions older than max_age seconds."""
        now = time.time()
        stale = [uid for uid, s in self._sessions.items() if now - s.created_at > max_age]
        for uid in stale:
            self.cleanup(uid)

        # Also enforce max sessions
        while len(self._sessions) > self._max_sessions:
            oldest = min(self._sessions, key=lambda k: self._sessions[k].created_at)
            self.cleanup(oldest)
