"""会话导入 — 历史agent会话 → 海马长期记忆（P3-② 路线图集成项）

数据源：opensoul.db的agent_sessions/agent_messages（acp-proxy ws_chat写入）
写入：hippo LongTermMemoryStore（三因子记忆，P0-6 chat路径已接线检索）

设计原则（用户要求：数据准确、拒绝噪声）：
- 质量过滤：消息数<min_messages的会话跳过；evo-test-*测试会话跳过
- 去重：已导入过的session（source_session匹配）不重复导入
- 双层记忆：用户消息→episodic；会话摘要→semantic
- importance随内容长度适度提升，上限0.6，不让批量导入淹没真实记忆
"""

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("opensoul.hippo.session_importer")

OPENSOUL_DB = os.environ.get(
    "OPENSOUL_DB",
    os.path.join(os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul"), "data", "opensoul.db"),
)

# 噪声会话前缀：测试/系统生成的会话不值得进记忆
DEFAULT_EXCLUDE_PREFIXES = ("evo-test-", "stress_test", "systemic-test")

# ── 会话登记面读口（DEC-㉘b-① 真源补现）────────────────────────────────
# 「近期会话」统计读口：数据面=agent_sessions（会话本体登记面：title/
# message_count/last_activity_at，acp-proxy ws_chat 写入）——㉘b 语义随拍取
# 会话登记本体面（trajectory_sessions=任务轨迹面、hippo SessionManager=内存
# 执行面，均非会话本体）。

# 活动窗口=24h，与 ㉗a recent_count RECENT_WINDOW_S 同窗语义（tests 契约锁
# 两常量相等防漂移）。
RECENT_SESSIONS_WINDOW_S = 24 * 3600


def _sessions_db_path() -> str:
    """call-time 读 OPENSOUL_DB env（PROACT-③ 数据面隔离纪律，禁 import 时冻结）。"""
    env = os.environ.get("OPENSOUL_DB")
    if env:
        return env
    return os.path.join(
        os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul"), "data", "opensoul.db"
    )


def count_recent_sessions(
    window_s: float = RECENT_SESSIONS_WINDOW_S, db_path: str | None = None
) -> int:
    """近 window_s 秒内有活动的会话登记行计数（窗口闭区间 last_activity_at >= now-window_s）。

    库/表缺席=空存储如实 0；其余错误如实抛（禁静默吞造 0，C2 失败可见）。
    归档轴刻意不过滤（活动口径与归档状态正交，归档会话 24h 后自然出窗）。
    """
    path = db_path or _sessions_db_path()
    if not Path(path).exists():
        return 0
    cutoff = time.time() - window_s
    with sqlite3.connect(path) as conn:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM agent_sessions WHERE last_activity_at >= ?",
                (cutoff,),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return 0  # 空库如实零
            raise
    return int(row[0])


class SessionImporter:
    """历史会话→海马记忆导入器"""

    def __init__(self, ltm_store=None, db_path: str = OPENSOUL_DB):
        self.db_path = db_path
        if ltm_store is None:
            from src.hippo.long_term_memory import LongTermMemoryStore

            ltm_store = LongTermMemoryStore()
        self.ltm = ltm_store

    def _fetch_sessions(self, limit: int, min_messages: int, exclude_prefixes: tuple) -> list[dict]:
        """读取候选会话（含消息内容）"""
        if not Path(self.db_path).exists():
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT s.id, s.agent_id, s.title, s.created_at, s.last_activity_at,
                          s.message_count
                   FROM agent_sessions s
                   WHERE s.message_count >= ?
                   ORDER BY s.last_activity_at DESC
                   LIMIT ?""",
                (min_messages, limit * 3),  # 多取一些，过滤后再截断
            ).fetchall()

            sessions = []
            for row in rows:
                sid = row["id"]
                if any(sid.startswith(p) for p in exclude_prefixes):
                    continue
                msgs = conn.execute(
                    """SELECT role, content, timestamp FROM agent_messages
                       WHERE session_id = ? ORDER BY timestamp""",
                    (sid,),
                ).fetchall()
                if len(msgs) < min_messages:
                    continue
                sessions.append(
                    {
                        "id": sid,
                        "agent_id": row["agent_id"],
                        "title": row["title"] or "未命名会话",
                        "created_at": row["created_at"],
                        "messages": [
                            {"role": m["role"], "content": m["content"] or "", "ts": m["timestamp"]}
                            for m in msgs
                        ],
                    }
                )
                if len(sessions) >= limit:
                    break
        return sessions

    def _already_imported(self, session_id: str) -> bool:
        """检查该会话是否已导入过（source_session精确匹配）"""
        try:
            with sqlite3.connect(self.ltm.db_path) as conn:
                row = conn.execute(
                    "SELECT 1 FROM memories WHERE source_session = ? LIMIT 1",
                    (session_id,),
                ).fetchone()
                return row is not None
        except Exception:
            return False

    def import_sessions(
        self,
        limit: int = 200,
        min_messages: int = 2,
        exclude_prefixes: tuple = DEFAULT_EXCLUDE_PREFIXES,
        dry_run: bool = False,
    ) -> dict:
        """执行导入。dry_run=True时只统计不写入。"""
        t0 = time.time()
        candidates = self._fetch_sessions(limit, min_messages, exclude_prefixes)

        stats = {
            "scanned": len(candidates),
            "skipped_imported": 0,
            "skipped_short": 0,
            "gate_rejected": 0,
            "imported_sessions": 0,
            "memories_created": 0,
            "dry_run": dry_run,
            "elapsed_s": 0.0,
            "examples": [],
        }

        for sess in candidates:
            sid = sess["id"]
            if not dry_run and self._already_imported(sid):
                stats["skipped_imported"] += 1
                continue

            user_msgs = [
                m for m in sess["messages"] if m["role"] == "user" and m["content"].strip()
            ]
            if not user_msgs:
                stats["skipped_short"] += 1
                continue

            if dry_run:
                stats["imported_sessions"] += 1
                stats["memories_created"] += len(user_msgs) + 1  # +1会话摘要
                if len(stats["examples"]) < 3:
                    stats["examples"].append(
                        {"session": sid, "user_msgs": len(user_msgs), "title": sess["title"]}
                    )
                continue

            created = 0
            # 1. 用户消息 → episodic记忆（gatekeeper准入：reject返回None不计数）
            for m in user_msgs:
                content = m["content"][:500]
                importance = min(0.6, 0.3 + len(m["content"]) / 3000.0)
                try:
                    mem = self.ltm.store(
                        content=content,
                        memory_type="episodic",
                        importance=importance,
                        tags=[sess["agent_id"], "session-import", f"session:{sid}"],
                        metadata={
                            "imported_from": "agent_messages",
                            "session_id": sid,
                            "session_title": sess["title"],
                            "timestamp": m["ts"],
                        },
                        source_session=sid,
                    )
                    if mem is not None:
                        created += 1
                    else:
                        stats["gate_rejected"] += 1
                except Exception as e:
                    logger.warning("store episodic failed session=%s: %s", sid, e)

            # 2. 会话摘要 → semantic记忆
            first_ask = user_msgs[0]["content"][:150]
            # created_at可能是unix时间戳float也可能是ISO字符串
            created_raw = sess.get("created_at", "")
            if isinstance(created_raw, (int, float)):
                created_disp = time.strftime("%Y-%m-%d", time.localtime(created_raw))
            else:
                created_disp = str(created_raw)[:10]
            summary = (
                f"会话[{sess['title']}]({sess['agent_id']}, {created_disp}): "
                f"用户共{len(user_msgs)}条消息，开场话题: {first_ask}"
            )
            try:
                mem = self.ltm.store(
                    content=summary,
                    memory_type="semantic",
                    importance=0.4,
                    tags=[sess["agent_id"], "session-import", "session-summary", f"session:{sid}"],
                    metadata={
                        "imported_from": "agent_sessions",
                        "session_id": sid,
                        "message_count": len(sess["messages"]),
                    },
                    source_session=sid,
                )
                if mem is not None:
                    created += 1
                else:
                    stats["gate_rejected"] += 1
            except Exception as e:
                logger.warning("store summary failed session=%s: %s", sid, e)

            stats["imported_sessions"] += 1
            stats["memories_created"] += created
            if len(stats["examples"]) < 3:
                stats["examples"].append(
                    {"session": sid, "memories": created, "title": sess["title"]}
                )

        stats["elapsed_s"] = round(time.time() - t0, 2)
        return stats
