"""Content-addressable file storage with deduplication and versioning.

Stores files by SHA-256 hash. Metadata tracked in SQLite.
Supports streaming reads, atomic writes, and file version history.
"""

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from src.vein.versioning import FileVersion, VersionManager


@dataclass
class FileMeta:
    file_id: str
    name: str
    content_hash: str
    size: int
    mime_type: str
    tags: str  # comma-separated
    created_at: float
    ref_count: int
    # 行租户键（DEC-⑦-④ files 子搜索存储层加轴）：自带键、缺失/空白→'default'
    # 兜底（file_row_tenant 单一判法，DEC-⑦-① _event_row_tenant / ②
    # message_row_tenant / ③ template_row_tenant 同款语义）；存量行随迁移回填
    # 'default'（与 F3 实测分布一致）。
    tenant_id: str = ""


class FileTenantDenied(Exception):
    """跨租户写拒（行归属不可被跨租户改写）：API层映射为HTTP 403。

    DEC-⑦-③ TemplateTenantDenied 同款（劫持闸语义，F3b-③c-2②d 纪律）。
    """


def file_row_tenant(meta: FileMeta) -> str:
    """行租户键单一判法（DEC-⑦-④）：自带键、缺失/空白→'default' 兜底。

    写读同源：store() 落行与 list_files 过滤、delete/rollback 归属闸、API 加性
    tenant_id 键共用本函数，禁消费点另发明判法（F4b-㉗ 纪律）；DEC-⑦-①
    _event_row_tenant / ② message_row_tenant / ③ template_row_tenant 同款语义
    （系统/缺省写方→'default'，键派生故障绝不吃掉落库）。
    """
    return ((meta.tenant_id or "").strip()) or "default"


class FileStore:
    """Content-addressable file store with deduplication and versioning."""

    def __init__(self, root: str | None = None, db_path: str | None = None):
        self._root = Path(root or os.path.expanduser("~/opensoul/data/vein/files"))
        self._root.mkdir(parents=True, exist_ok=True)

        db = db_path or str(self._root.parent / "vein_meta.db")
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._init_db()
        self._versions = VersionManager(self._db)

    def _init_db(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                file_id    TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                size       INTEGER NOT NULL,
                mime_type  TEXT DEFAULT 'application/octet-stream',
                tags       TEXT DEFAULT '',
                created_at REAL NOT NULL,
                ref_count  INTEGER DEFAULT 1,
                tenant_id  TEXT NOT NULL DEFAULT 'default'
            );
            CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);
            CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
        """)
        self._db.commit()
        # 老库自愈（C1 幂等双轨，③c-2① 先例）：CREATE TABLE IF NOT EXISTS 不补列，
        # 存量库按 PRAGMA 探测补 tenant_id 列（常量默认='default' 回填存量行，
        # 与 F3 实测分布一致）；租户索引必须在补列之后建（③c-2① 坑延续）。
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(files)").fetchall()}
        if "tenant_id" not in cols:
            self._db.execute(
                "ALTER TABLE files ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
            self._db.commit()
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_files_tenant ON files(tenant_id)")
        self._db.commit()

    # ── Core Operations ──────────────────────────────────────

    def store(
        self,
        data: bytes,
        name: str,
        mime_type: str = "application/octet-stream",
        tags: list[str] | None = None,
        file_id: str | None = None,
        change_summary: str = "",
        tenant_id: str | None = None,
    ) -> FileMeta:
        """Store a file with deduplication. Returns metadata.

        tenant_id=写点派生租户键（DEC-⑦-④）；未提供/空白→'default' 兜底=系统
        写方语义（file_row_tenant 单一判法，键派生故障绝不吃掉落库）。
        """
        content_hash = hashlib.sha256(data).hexdigest()
        size = len(data)
        now = time.time()
        fid = file_id or hashlib.md5(f"{name}{now}{content_hash[:8]}".encode()).hexdigest()[:16]
        tag_str = ",".join(tags) if tags else ""
        # 行归属=写点派生租户（写读同一判法：落行值即 file_row_tenant 归一值）
        meta = FileMeta(fid, name, content_hash, size, mime_type, tag_str, now, 1, tenant_id or "")
        row_tenant = file_row_tenant(meta)
        meta.tenant_id = row_tenant

        # Check if this is an update to an existing file
        existing_meta = self.get_meta(fid) if file_id else None

        if existing_meta:
            if file_row_tenant(existing_meta) != row_tenant:
                # 行归属不可被跨租户改写（劫持闸，DEC-⑦-③ TemplateTenantDenied
                # 同款，F3b-③c-2②d 纪律）：跨租户按 file_id 覆盖=拒绝且行零改写。
                raise FileTenantDenied(f"file_id {fid!r} belongs to another tenant")
            # UPDATE existing file
            # Store the new blob if content changed
            if existing_meta.content_hash != content_hash:
                blob_path = self._blob_path(content_hash)
                blob_path.parent.mkdir(parents=True, exist_ok=True)
                blob_path.write_bytes(data)

                # Record version
                self._versions.record_version(
                    fid, content_hash, size, change_summary or "File updated"
                )

            # Update the existing record（tenant_id 不进 SET：行归属=首次写入粘性，
            # 同租户更新绝不洗键，③a _persist COALESCE 粘性同款语义）
            self._db.execute(
                "UPDATE files SET name = ?, content_hash = ?, size = ?, mime_type = ?, tags = ?, created_at = ? "
                "WHERE file_id = ?",
                (name, content_hash, size, mime_type, tag_str, now, fid),
            )
            self._db.commit()
            return FileMeta(
                fid,
                name,
                content_hash,
                size,
                mime_type,
                tag_str,
                now,
                1,
                tenant_id=file_row_tenant(existing_meta),
            )

        # NEW file — check for dedup by content hash
        existing_hash = self._db.execute(
            "SELECT file_id FROM files WHERE content_hash = ?", (content_hash,)
        ).fetchone()

        if existing_hash:
            # Content already exists, just add a new reference（行键=本写方租户，
            # 跨租户同内容各落各的行键；blob 共享=内容寻址既有语义）
            self._db.execute(
                "INSERT INTO files (file_id, name, content_hash, size, mime_type, tags, created_at, ref_count, tenant_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (fid, name, content_hash, size, mime_type, tag_str, now, row_tenant),
            )
            self._db.commit()
            return meta

        # Store the actual blob
        blob_path = self._blob_path(content_hash)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(data)

        self._db.execute(
            "INSERT INTO files (file_id, name, content_hash, size, mime_type, tags, created_at, ref_count, tenant_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (fid, name, content_hash, size, mime_type, tag_str, now, row_tenant),
        )
        self._db.commit()
        return meta

    def retrieve(self, file_id: str) -> tuple[bytes, FileMeta] | None:
        """Retrieve a file by ID. Returns (data, meta) or None."""
        meta = self.get_meta(file_id)
        if not meta:
            return None
        blob_path = self._blob_path(meta.content_hash)
        if not blob_path.exists():
            return None
        return blob_path.read_bytes(), meta

    def stream_retrieve(self, file_id: str):
        """Generator that yields file data in chunks for streaming."""
        meta = self.get_meta(file_id)
        if not meta:
            return None, None
        blob_path = self._blob_path(meta.content_hash)
        if not blob_path.exists():
            return None, None
        return blob_path, meta

    def get_meta(self, file_id: str) -> FileMeta | None:
        row = self._db.execute("SELECT * FROM files WHERE file_id = ?", (file_id,)).fetchone()
        if not row:
            return None
        return FileMeta(**dict(row))

    def delete(self, file_id: str, tenant_id: str | None = None) -> bool:
        """Delete a file reference. Removes blob if no other references.

        DEC-⑦-④ 删除归属闸（DEC-⑦-③ delete_template 同款）：tenant_id 显式给出时
        跨租户删除=零副作用回 False（与 not-found 同一契约，不差分泄露他租户行
        存在性，③c-1 纪律）；None=系统视图不闸（既有内部调用面字节不变）。
        """
        meta = self.get_meta(file_id)
        if not meta:
            return False
        if tenant_id is not None and file_row_tenant(meta) != tenant_id:
            return False

        self._db.execute("DELETE FROM files WHERE file_id = ?", (file_id,))

        # Check if any other files reference the same content
        refs = self._db.execute(
            "SELECT COUNT(*) FROM files WHERE content_hash = ?", (meta.content_hash,)
        ).fetchone()[0]

        if refs == 0:
            blob_path = self._blob_path(meta.content_hash)
            if blob_path.exists():
                blob_path.unlink()

        self._db.commit()
        return True

    def list_files(
        self,
        name_filter: str | None = None,
        tag: str | None = None,
        limit: int = 100,
        offset: int = 0,
        tenant_id: str | None = None,
    ) -> list[FileMeta]:
        """List files with optional filters.

        DEC-⑦-④ 租户过滤：tenant_id 显式给出=恒按行租户键过滤（行键写点归一+
        迁移回填，SQL 等值=file_row_tenant 等值；过滤先于截断=limit 只计可见命中，
        跨租户行不占名额）；None=系统视图，既有调用面字节不变。
        """
        query = "SELECT * FROM files WHERE 1=1"
        params: list = []

        if name_filter:
            query += " AND name LIKE ?"
            params.append(f"%{name_filter}%")
        if tag:
            query += " AND tags LIKE ?"
            params.append(f"%{tag}%")
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._db.execute(query, params).fetchall()
        return [FileMeta(**dict(r)) for r in rows]

    def stats(self) -> dict:
        total = self._db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        size = self._db.execute("SELECT COALESCE(SUM(size), 0) FROM files").fetchone()[0]
        unique = self._db.execute("SELECT COUNT(DISTINCT content_hash) FROM files").fetchone()[0]

        disk_usage = 0
        blob_dir = self._root / "blobs"
        if blob_dir.exists():
            for f in blob_dir.rglob("*"):
                if f.is_file():
                    disk_usage += f.stat().st_size

        return {
            "total_files": total,
            "total_size_bytes": size,
            "unique_blobs": unique,
            "disk_usage_bytes": disk_usage,
            "dedup_savings_bytes": size - disk_usage if size > disk_usage else 0,
            "versioning": self._versions.stats(),
        }

    # ── Versioning Operations ────────────────────────────────

    def record_version(
        self,
        file_id: str,
        content_hash: str,
        size: int,
        change_summary: str = "",
    ) -> FileVersion | None:
        """Record a new version when file content changes."""
        meta = self.get_meta(file_id)
        if not meta:
            return None
        return self._versions.record_version(file_id, content_hash, size, change_summary)

    def get_version_history(self, file_id: str, limit: int = 50) -> list[FileVersion]:
        """Get version history for a file."""
        return self._versions.get_history(file_id, limit)

    def get_version(self, file_id: str, version_number: int) -> FileVersion | None:
        """Get a specific version of a file."""
        return self._versions.get_version(file_id, version_number)

    def rollback_to_version(
        self, file_id: str, version_number: int, tenant_id: str | None = None
    ) -> FileMeta | None:
        """Rollback a file to a specific version.

        DEC-⑦-④ 归属闸（delete 同款）：tenant_id 显式给出时跨租户回 None（与
        not-found 同一契约零副作用）；None=系统视图不闸。
        """
        version = self._versions.get_version(file_id, version_number)
        if not version:
            return None

        meta = self.get_meta(file_id)
        if not meta:
            return None
        if tenant_id is not None and file_row_tenant(meta) != tenant_id:
            return None

        # Check if the version's content blob exists
        blob_path = self._blob_path(version.content_hash)
        if not blob_path.exists():
            return None

        # Update file metadata to point to the old content
        now = time.time()
        self._db.execute(
            "UPDATE files SET content_hash = ?, size = ?, created_at = ? WHERE file_id = ?",
            (version.content_hash, version.size, now, file_id),
        )
        self._db.commit()

        # Record the rollback as a new version
        self._versions.record_version(
            file_id,
            version.content_hash,
            version.size,
            f"Rollback to version {version_number}",
        )

        return self.get_meta(file_id)

    def _blob_path(self, content_hash: str) -> Path:
        # Shard by first 2 chars for filesystem balance
        return self._root / "blobs" / content_hash[:2] / content_hash
