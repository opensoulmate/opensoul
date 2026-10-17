"""File versioning system for OpenVein.

Tracks file versions when content changes, supports history listing,
rollback to previous versions, and diff between versions.
"""

import sqlite3
import time
from dataclasses import dataclass


@dataclass
class FileVersion:
    version_id: int
    file_id: str
    content_hash: str
    size: int
    version_number: int
    change_summary: str
    created_at: float


class VersionManager:
    """Manages file version history with rollback support."""

    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._init_db()

    def _init_db(self):
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS file_versions (
                version_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id       TEXT NOT NULL,
                content_hash  TEXT NOT NULL,
                size          INTEGER NOT NULL,
                version_number INTEGER NOT NULL,
                change_summary TEXT DEFAULT '',
                created_at    REAL NOT NULL,
                FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_versions_file ON file_versions(file_id, version_number);
            CREATE INDEX IF NOT EXISTS idx_versions_hash ON file_versions(content_hash);
        """)
        self._db.commit()

    def record_version(
        self,
        file_id: str,
        content_hash: str,
        size: int,
        change_summary: str = "",
    ) -> FileVersion:
        """Record a new version of a file."""
        # Get next version number
        row = self._db.execute(
            "SELECT COALESCE(MAX(version_number), 0) FROM file_versions WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        next_version = (row[0] if row else 0) + 1

        now = time.time()
        cursor = self._db.execute(
            "INSERT INTO file_versions (file_id, content_hash, size, version_number, change_summary, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (file_id, content_hash, size, next_version, change_summary, now),
        )
        self._db.commit()

        return FileVersion(
            version_id=cursor.lastrowid or 0,
            file_id=file_id,
            content_hash=content_hash,
            size=size,
            version_number=next_version,
            change_summary=change_summary,
            created_at=now,
        )

    def get_history(self, file_id: str, limit: int = 50) -> list[FileVersion]:
        """Get version history for a file, newest first."""
        rows = self._db.execute(
            "SELECT * FROM file_versions WHERE file_id = ? ORDER BY version_number DESC LIMIT ?",
            (file_id, limit),
        ).fetchall()
        return [FileVersion(**dict(r)) for r in rows]

    def get_version(self, file_id: str, version_number: int) -> FileVersion | None:
        """Get a specific version of a file."""
        row = self._db.execute(
            "SELECT * FROM file_versions WHERE file_id = ? AND version_number = ?",
            (file_id, version_number),
        ).fetchone()
        if not row:
            return None
        return FileVersion(**dict(row))

    # T3-④（DEC-⑳-③ 留口复审）：get_latest_version 已删——其 SQL 与活面
    # get_history(file_id, 1) 逐字同构（ORDER BY version_number DESC LIMIT 1），
    # 语义被 get_history 100% 覆盖非独特（留口判据不成立）；全仓零生产消费零测试消费。
    # 替代读法：(self.get_history(file_id, 1) or [None])[0]（同 None/最新版语义）。

    def get_version_count(self, file_id: str) -> int:
        """Get total number of versions for a file.

        T3-⑤ 已接线（不再是留口死面）：file_store.get_version_count→api/vein.py
        update 响应 version_count 首个真消费点（count 不被 limit 截断）；
        T3-⑥ 第二消费点：GET /versions 响应 current_version（同族截断件收口）。
        """
        row = self._db.execute(
            "SELECT COUNT(*) FROM file_versions WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        return row[0] if row else 0

    def delete_history(self, file_id: str) -> int:
        """Delete all version history for a file."""
        cursor = self._db.execute(
            "DELETE FROM file_versions WHERE file_id = ?",
            (file_id,),
        )
        self._db.commit()
        return cursor.rowcount

    def stats(self) -> dict:
        """Get versioning statistics."""
        total_versions = self._db.execute("SELECT COUNT(*) FROM file_versions").fetchone()[0]
        total_files = self._db.execute(
            "SELECT COUNT(DISTINCT file_id) FROM file_versions"
        ).fetchone()[0]
        avg_versions = round(total_versions / total_files, 1) if total_files > 0 else 0

        return {
            "total_versions": total_versions,
            "total_versioned_files": total_files,
            "avg_versions_per_file": avg_versions,
        }
