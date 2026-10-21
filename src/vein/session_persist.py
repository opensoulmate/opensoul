"""SQLite persistence for vein ChunkedUploader._sessions（RV-①b-⑥-1）——上传会话行唯一落盘口。

修前（RV-① A 组定性）：ChunkedUploader._sessions 全在进程内存（dict），create/
upload_chunk/assemble/cleanup 只改内存——上传会话行（received/complete 状态、
total_chunks 进度）随 service 重启即丢，而分片字节本就在盘（temp_dir 持久），
重启后孤儿分片目录无人认领（㉕ 既有清理面只认内存会话）。本模块把会话行落
SQLite：状态每次变更随写随落盘（写穿），首触懒回载，重启会话不丢——分片字节
+会话行双真源都在盘，跨重启续传成真。

铁律（本仓 SQLite 约定）：纯 sqlite3 语法（migrations 是 PG 语法禁直接跑）、
各 TEXT 列显式 str()、tags/received_chunks 是 list/set 列整体 json.dumps 落
TEXT 列（dict/list 列禁裸存）。

测试隔离：VEIN_UPLOAD_DB 环境变量覆盖库路径（GLAND_USAGE_DB/IMMUNE_ACCESS_DB/
ECHO_HISTORY_DB/A2A_TASKS_DB 同款先例），缺省 <repo>/data/vein_upload_sessions.db；
库路径每次连接按 env 解析（模块级 api/vein uploader 单例跨用例存活，测试面每
用例 tmp 真源）。

表语义（a2a_tasks 同族、echo_history 刻意不同）：vein_upload_sessions 是**状态
快照表**——upload_id=PRIMARY KEY，同会话每次变更 upsert 覆盖旧行=「当前态」
语义（一个上传会话一行）；echo_history 是追加型历史表故禁 REPLACE。两者纪律
不混用。

诚实边界：
①写失败不阻断调用方但 logger.error 可见（RV-①b-① 记账旁路纪律同款）——会话
  创建/分片落盘本体已发生，为落盘失败炸掉已收分片不成比例；代价是故障窗口内
  该会话态只活内存、重启丢（如实记，非谎报 durable）。
②回载失败=空会话起步+logger.error 可见，行真源仍在库下次触碰重试（RV-①b-②④
  语义）；不在回载故障上发明降级判法。
③行内单条不可解（json 坏行/未来版本字段漂移）跳过该行并 logger.error 可见，
  不因单行坏数据丢整库会话（禁静默丢：跳过必留日志真因）。
④无老化/裁剪面在本模块：会话 TTL 清扫沿用 ChunkedUploader._cleanup_old_sessions
  既有语义（清扫经 cleanup() 写穿删行=行随会话走，不另造第二套裁剪判法）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_TABLE = "vein_upload_sessions"

_COLUMNS = (
    "upload_id",
    "filename",
    "total_size",
    "chunk_size",
    "total_chunks",
    "received_chunks",
    "content_hash",
    "mime_type",
    "tags",
    "created_at",
    "completed_at",
    "tenant_id",
)


def default_db_path() -> str:
    """库路径：VEIN_UPLOAD_DB 环境变量优先（测试隔离），缺省 <repo>/data/vein_upload_sessions.db。"""
    env = os.environ.get("VEIN_UPLOAD_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "vein_upload_sessions.db")


class UploadSessionPersistence:
    """上传会话行持久化真源（单表 vein_upload_sessions：upsert 写穿 + load_all 懒回载 + delete）。"""

    def __init__(self, db_path: str | None = None):
        # 显式路径（测试/多实例）优先；缺省路径每次连接按 env 解析（缺省
        # default_db_path）——模块级 uploader 单例跨用例存活，测试面每用例
        # tmp 真源。构造零 IO（不建库）：回载只读不建库防 import 期建空生产库
        # （RV-①b-②④ 同纪律），写穿才建库。
        self.explicit_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        path = self.explicit_path or default_db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "upload_id TEXT PRIMARY KEY, "
            "filename TEXT NOT NULL, "
            "total_size INTEGER NOT NULL, "
            "chunk_size INTEGER NOT NULL, "
            "total_chunks INTEGER NOT NULL, "
            "received_chunks TEXT NOT NULL, "
            "content_hash TEXT, "
            "mime_type TEXT NOT NULL, "
            "tags TEXT NOT NULL, "
            "created_at REAL NOT NULL, "
            "completed_at REAL, "
            "tenant_id TEXT NOT NULL)"
        )
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_vein_upload_created ON {_TABLE}(created_at)")
        return conn

    def upsert(
        self,
        *,
        upload_id: str,
        filename: str,
        total_size: int,
        chunk_size: int,
        total_chunks: int,
        received_chunks: str,
        content_hash: str | None,
        mime_type: str,
        tags: str,
        created_at: float,
        completed_at: float | None,
        tenant_id: str,
    ) -> None:
        """写穿一行会话当前态（状态快照表语义：同 upload_id upsert 覆盖=当前态）。

        received_chunks/tags 已是 json.dumps 文本（调用方单点序列化）；失败如实
        抛（禁静默丢写），是否阻断调用方归消费方裁决（ChunkedUploader 侧=记账
        旁路纪律：logger.error 不阻断上传本体）。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    f"INSERT INTO {_TABLE} ({', '.join(_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(upload_id) DO UPDATE SET "
                    "filename=excluded.filename, total_size=excluded.total_size, "
                    "chunk_size=excluded.chunk_size, total_chunks=excluded.total_chunks, "
                    "received_chunks=excluded.received_chunks, content_hash=excluded.content_hash, "
                    "mime_type=excluded.mime_type, tags=excluded.tags, "
                    "created_at=excluded.created_at, completed_at=excluded.completed_at, "
                    "tenant_id=excluded.tenant_id",
                    (
                        str(upload_id),
                        str(filename),
                        int(total_size),
                        int(chunk_size),
                        int(total_chunks),
                        str(received_chunks),
                        None if content_hash is None else str(content_hash),
                        str(mime_type),
                        str(tags),
                        float(created_at),
                        None if completed_at is None else float(completed_at),
                        str(tenant_id),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete(self, upload_id: str) -> None:
        """删一行会话（cleanup 写穿口）。缺行零操作（幂等，cleanup 语义本就容忍缺行）。"""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(f"DELETE FROM {_TABLE} WHERE upload_id = ?", (str(upload_id),))
                conn.commit()
            finally:
                conn.close()

    def load_all(self) -> list[dict]:
        """回载全部会话行（UploadSession 构造形 dict 列表，created_at 升序=稳定回载序）。

        回载只读不建库：库文件缺失=空会话起步直接返回（防模块 import 期构造
        回载顺手建出空生产库——写穿才建库，RV-①b-②④ 同纪律）。
        单行不可解（json 坏行/字段漂移）跳过该行并 logger.error 可见（诚实边界③）。
        """
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return []
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM {_TABLE} ORDER BY created_at, upload_id"
                ).fetchall()
            finally:
                conn.close()
        out: list[dict] = []
        for row in rows:
            data = dict(zip(_COLUMNS, row))
            try:
                received = json.loads(data["received_chunks"])
                tags = json.loads(data["tags"])
                if not isinstance(received, list) or not isinstance(tags, list):
                    raise ValueError("received_chunks/tags 非数组")
                out.append(
                    {
                        "upload_id": str(data["upload_id"]),
                        "filename": str(data["filename"]),
                        "total_size": int(data["total_size"]),
                        "chunk_size": int(data["chunk_size"]),
                        "total_chunks": int(data["total_chunks"]),
                        "received_chunks": {int(i) for i in received},
                        "content_hash": None
                        if data["content_hash"] is None
                        else str(data["content_hash"]),
                        "mime_type": str(data["mime_type"]),
                        "tags": [str(t) for t in tags],
                        "created_at": float(data["created_at"]),
                        "completed_at": None
                        if data["completed_at"] is None
                        else float(data["completed_at"]),
                        "tenant_id": str(data["tenant_id"]),
                    }
                )
            except (TypeError, ValueError) as e:
                logger.error(f"vein 上传会话行不可解跳过: {e}")
                continue
        return out

    def count(self) -> int:
        """行数（探针/取证回读口）。库缺失=0。"""
        with self._lock:
            path = self.explicit_path or default_db_path()
            if not Path(path).exists():
                return 0
            conn = self._connect()
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()
            finally:
                conn.close()
        return int(row[0]) if row else 0
