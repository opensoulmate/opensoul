"""ScheduleWakeup — agent自主唤醒调度 + noop自报 + streak折叠。

调研来源：claude-code SDK ScheduleWakeup（feature-matrix/10-claude-code-source.md #4）：
- agent 自己决定"多久后再醒"，delaySeconds 被 runtime 钳到 [60, 3600]；
- 必须给出 reason（一句话，进 telemetry + 展示给用户）——reason 为空直接拒收；
- **noop 自报**：true=这次检查什么都没发生，false=有进展；
- **连续 noop 被折叠显示并统计 streak**——用户一眼看到"它在干嘛/是不是空转"；
- stop=true 结束唤醒循环。

执行侧闭环（claude-code runtime"delay到点后重新唤醒agent"，本模块的执行语义）：
- due_wakeups() 给出到点事件；executor经 claim_fire() CAS领取执行权（at-most-once，
  双实例并发领取只有一个赢家）；执行结果 ack_fire() 回执——失败释放回待执行=自动重试，
  达 MAX_FIRE_ATTEMPTS 上限进 EXHAUSTED 终态（留痕，绝不无限重试烧钱）。

设计纪律（与源码注释语义一致）：
1. 钳制必须显式可见：requested_delay_s 原样留档，clamped=True 标记，禁止静默改值；
2. 禁止静默截断同族：reason 超长只截显式标记（[+N chars]），不留无痕截断；
3. 失败必须可见：reason 缺失抛 ValueError（API层转 400），不静默给默认值；
4. streak 只统计"同 (agent_id, session_id) 唤醒循环"的连续 noop，non-noop 一到即归零，
   且把上一段 streak 折叠成一个 noop_streak 折叠块（folded_view）。

存储：SQLite（~/opensoul/data/wakeup.db，默认），单连接锁保护（同步API，调用方多为
FastAPI 线程池/asyncio.to_thread 场景）。表结构幂等（CREATE TABLE IF NOT EXISTS）。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

# claude-code：delaySeconds 被 runtime 钳到 [60, 3600]
MIN_WAKEUP_DELAY_S = 60.0
MAX_WAKEUP_DELAY_S = 3600.0
# claude-code：reason 一句话——超长截断但必须显式标记（禁止静默截断）
REASON_MAX_CHARS = 500
# 执行侧唤醒循环（claude-code runtime"到点重新唤醒agent"）：fire失败重试上限。
# 超限后事件进 EXHAUSTED 终态（fired_at 保持 + last_fire_error 留痕），绝不无限重试。
MAX_FIRE_ATTEMPTS = 5


def clamp_delay(delay_seconds: float) -> tuple[float, float, bool]:
    """返回 (requested, clamped, was_clamped)。钳到 [60, 3600] 秒。"""
    try:
        requested = float(delay_seconds)
    except (TypeError, ValueError):
        requested = MIN_WAKEUP_DELAY_S
    clamped = max(MIN_WAKEUP_DELAY_S, min(MAX_WAKEUP_DELAY_S, requested))
    return requested, clamped, clamped != requested


def _trim_reason(reason: str) -> str:
    """reason 超长截断必须显式标记（pi truncateForSummary 同款纪律）。"""
    text = (reason or "").strip()
    if len(text) <= REASON_MAX_CHARS:
        return text
    cut = text[:REASON_MAX_CHARS]
    return f"{cut} [+{len(text) - REASON_MAX_CHARS} chars]"


class WakeupScheduler:
    """唤醒循环状态机：调度 + noop自报 + streak折叠（持久化到 SQLite）。"""

    def __init__(self, db_path: str = ""):
        self._db_path = db_path or str(Path.home() / "opensoul" / "data" / "wakeup.db")
        self._lock = threading.Lock()
        self._init_db()

    # ── 存储 ────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wakeup_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    session_id TEXT NOT NULL DEFAULT '',
                    ts REAL NOT NULL,
                    requested_delay_s REAL NOT NULL,
                    delay_s REAL NOT NULL,
                    clamped INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL,
                    noop INTEGER NOT NULL DEFAULT 0,
                    streak INTEGER NOT NULL DEFAULT 0,
                    stopped INTEGER NOT NULL DEFAULT 0,
                    next_wakeup_at REAL,
                    source TEXT NOT NULL DEFAULT '',
                    fired_at REAL,
                    fire_attempts INTEGER NOT NULL DEFAULT 0,
                    last_fire_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wakeup_agent_ts "
                "ON wakeup_events(agent_id, session_id, ts)"
            )
            # 旧库迁移（执行侧闭环新增列）：上一轮的 wakeup.db 没有这些列，
            # CREATE IF NOT EXISTS 不会补——必须显式 ALTER（幂等：存在即跳过）。
            existing = {
                r["name"] for r in conn.execute("PRAGMA table_info(wakeup_events)").fetchall()
            }
            for col, ddl in (
                ("source", "ALTER TABLE wakeup_events ADD COLUMN source TEXT NOT NULL DEFAULT ''"),
                ("fired_at", "ALTER TABLE wakeup_events ADD COLUMN fired_at REAL"),
                (
                    "fire_attempts",
                    "ALTER TABLE wakeup_events ADD COLUMN fire_attempts INTEGER NOT NULL DEFAULT 0",
                ),
                (
                    "last_fire_error",
                    "ALTER TABLE wakeup_events ADD COLUMN last_fire_error TEXT NOT NULL DEFAULT ''",
                ),
            ):
                if col not in existing:
                    conn.execute(ddl)

    @staticmethod
    def _key(agent_id: str, session_id: str) -> tuple[str, str]:
        return (agent_id or "default", session_id or "")

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict:
        return {
            "event_id": row["id"],
            "agent_id": row["agent_id"],
            "session_id": row["session_id"],
            "ts": row["ts"],
            "requested_delay_s": row["requested_delay_s"],
            "delay_s": row["delay_s"],
            "clamped": bool(row["clamped"]),
            "reason": row["reason"],
            "noop": bool(row["noop"]),
            "streak": row["streak"],
            "stopped": bool(row["stopped"]),
            "next_wakeup_at": row["next_wakeup_at"],
            "source": row["source"] or "",
            "fired_at": row["fired_at"],
            "fire_attempts": int(row["fire_attempts"]),
            "last_fire_error": row["last_fire_error"] or "",
            # 执行侧派生态：fired=已领取执行权（at-most-once）；exhausted=重试用尽终态
            "fired": row["fired_at"] is not None,
            "exhausted": (row["fired_at"] is not None)
            and int(row["fire_attempts"]) >= MAX_FIRE_ATTEMPTS
            and bool(row["last_fire_error"]),
        }

    # ── 核心：record（agent 调用 ScheduleWakeup 的唯一入口） ─

    def record(
        self,
        reason: str,
        noop: bool,
        delay_seconds: float = MIN_WAKEUP_DELAY_S,
        stop: bool = False,
        agent_id: str = "default",
        session_id: str = "",
        source: str = "",
    ) -> dict:
        """记录一次 ScheduleWakeup 自报。

        返回事件摘要：streak（当前连续noop计数）、folded（streak>=2 已进入折叠区）、
        next_wakeup_at（stop=True 时为 None）、clamped（是否被钳制）。
        ValueError：reason 为空（claude-code：reason 必填）。
        """
        text = _trim_reason(reason)
        if not text:
            raise ValueError("reason 必填：ScheduleWakeup 必须说明这次唤醒检查了什么（一句话）")
        requested, clamped, was_clamped = clamp_delay(delay_seconds)
        stop = bool(stop)
        noop = bool(noop)
        now = time.time()
        aid, sid = self._key(agent_id, session_id)

        with self._lock, self._conn() as conn:
            prev = conn.execute(
                "SELECT * FROM wakeup_events WHERE agent_id=? AND session_id=? "
                "ORDER BY ts DESC, id DESC LIMIT 1",
                (aid, sid),
            ).fetchone()

            # streak：连续 noop 计数。noop=False 立即归零（有进展就打断空转统计）；
            # stop 结束循环同样打断 streak（循环都不在了，streak 无意义）。
            if noop and not stop and prev is not None and prev["noop"] and not prev["stopped"]:
                streak = int(prev["streak"]) + 1
            elif noop and not stop:
                streak = 1
            else:
                streak = 0

            next_at = None if stop else now + clamped
            cur = conn.execute(
                "INSERT INTO wakeup_events "
                "(agent_id, session_id, ts, requested_delay_s, delay_s, clamped, "
                " reason, noop, streak, stopped, next_wakeup_at, source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    aid,
                    sid,
                    now,
                    requested,
                    clamped,
                    1 if was_clamped else 0,
                    text,
                    1 if noop else 0,
                    streak,
                    1 if stop else 0,
                    next_at,
                    source or "",
                ),
            )
            event_id = cur.lastrowid

        return {
            "event_id": event_id,
            "agent_id": aid,
            "session_id": sid,
            "reason": text,
            "noop": noop,
            "stop": stop,
            "streak": streak,
            "folded": streak >= 2,
            "requested_delay_s": requested,
            "delay_s": clamped,
            "clamped": was_clamped,
            "next_wakeup_at": next_at,
            "active": not stop,
            "source": source or "",
            "fired": False,
            "fire_attempts": 0,
            "last_fire_error": "",
        }

    # ── 查询：status（"它现在在干嘛"） ────────────────────────

    def status(self, agent_id: str = "default", session_id: str = "") -> dict:
        """当前唤醒循环状态：active / current_streak / next_wakeup_at / 倒计时。"""
        aid, sid = self._key(agent_id, session_id)
        now = time.time()
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM wakeup_events WHERE agent_id=? AND session_id=? "
                "ORDER BY ts DESC, id DESC LIMIT 1",
                (aid, sid),
            ).fetchone()
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM wakeup_events WHERE agent_id=? AND session_id=?",
                (aid, sid),
            ).fetchone()["n"]
            noop_n = conn.execute(
                "SELECT COUNT(*) AS n FROM wakeup_events "
                "WHERE agent_id=? AND session_id=? AND noop=1 AND stopped=0",
                (aid, sid),
            ).fetchone()["n"]
            fired_n = conn.execute(
                "SELECT COUNT(*) AS n FROM wakeup_events "
                "WHERE agent_id=? AND session_id=? AND fired_at IS NOT NULL "
                "AND last_fire_error=''",
                (aid, sid),
            ).fetchone()["n"]
            exhausted_n = conn.execute(
                "SELECT COUNT(*) AS n FROM wakeup_events "
                "WHERE agent_id=? AND session_id=? AND last_fire_error LIKE 'EXHAUSTED%'",
                (aid, sid),
            ).fetchone()["n"]

        if row is None:
            return {
                "agent_id": aid,
                "session_id": sid,
                "active": False,
                "current_streak": 0,
                "next_wakeup_at": None,
                "countdown_s": None,
                "last_event": None,
                "total_events": 0,
                "noop_events": 0,
                "fire": {"due_now": False, "fired": 0, "exhausted": 0},
            }
        ev = self._row_to_event(row)
        active = (not ev["stopped"]) and (ev["next_wakeup_at"] is not None)
        countdown = None
        if active and ev["next_wakeup_at"] is not None:
            countdown = round(ev["next_wakeup_at"] - now, 1)
        # 执行侧闭环可观测："到点了还没人来叫醒我吗"一眼可见
        due_now = (
            (not ev["stopped"])
            and ev["next_wakeup_at"] is not None
            and ev["next_wakeup_at"] <= now
            and not ev["fired"]
            and ev["fire_attempts"] < MAX_FIRE_ATTEMPTS
        )
        return {
            "agent_id": aid,
            "session_id": sid,
            "active": active,
            "current_streak": ev["streak"],
            "next_wakeup_at": ev["next_wakeup_at"],
            "countdown_s": countdown,
            "last_event": ev,
            "total_events": total,
            "noop_events": noop_n,
            "fire": {"due_now": due_now, "fired": fired_n, "exhausted": exhausted_n},
        }

    # ── 查询：folded_view（"连续noop折叠显示"） ─────────────

    def folded_view(
        self,
        agent_id: str = "default",
        session_id: str = "",
        limit: int = 50,
    ) -> dict:
        """折叠显示：连续 noop 折叠成一个 {kind:"noop_streak", count:N} 块并统计 streak。

        返回 {"items": [...], "current_streak": n, "active": bool, "total_events": N}。
        items 按时间倒序（最新在前）；折叠块覆盖它折叠掉的连续 noop 段（时间正序聚合）。
        """
        aid, sid = self._key(agent_id, session_id)
        limit = max(1, min(500, int(limit or 50)))
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM wakeup_events WHERE agent_id=? AND session_id=? "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (aid, sid, limit),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM wakeup_events WHERE agent_id=? AND session_id=?",
                (aid, sid),
            ).fetchone()["n"]

        # 先按时间正序折叠连续 noop 段（>=2 才折叠成块；单个 noop 原样显示），
        # 再整体倒序输出（最新在前）
        chronological = [self._row_to_event(r) for r in reversed(rows)]
        items: list[dict] = []
        i = 0
        while i < len(chronological):
            ev = chronological[i]
            if ev["noop"] and not ev["stopped"]:
                j = i
                while (
                    j < len(chronological)
                    and chronological[j]["noop"]
                    and not chronological[j]["stopped"]
                ):
                    j += 1
                run = chronological[i:j]
                if len(run) == 1:
                    items.append({**run[0], "kind": "wakeup", "folded": False})
                else:
                    items.append(
                        {
                            "kind": "noop_streak",
                            "folded": True,
                            "count": len(run),
                            "streak_max": max(e["streak"] for e in run),
                            "first_ts": run[0]["ts"],
                            "last_ts": run[-1]["ts"],
                            "first_reason": run[0]["reason"],
                            "last_reason": run[-1]["reason"],
                            "reasons": [e["reason"] for e in run],
                            "event_ids": [e["event_id"] for e in run],
                        }
                    )
                i = j
            else:
                items.append({**ev, "kind": "wakeup", "folded": False})
                i += 1
        items.reverse()

        last = chronological[-1] if chronological else None
        current_streak = last["streak"] if last else 0
        active = False
        if last is not None:
            active = (not last["stopped"]) and (last["next_wakeup_at"] is not None)
        return {
            "agent_id": aid,
            "session_id": sid,
            "items": items,
            "current_streak": current_streak,
            "active": active,
            "total_events": total,
        }

    # ── 调度：due_wakeups（执行侧按此拉取到点唤醒） ───────────

    def due_wakeups(self, now: float | None = None) -> list[dict]:
        """到点应唤醒的循环（每个 (agent_id, session_id) 取最新事件判定——
        stop事件是循环终态，必须参与"最新"判定，否则旧的未stop事件会复活成due）。

        执行侧闭环过滤：已被领取执行权（fired_at非空）或重试用尽（EXHAUSTED）的
        事件不再返回——at-most-once，双实例executor靠claim_fire CAS保证不双跑。
        失败被ack_fire释放回 fired_at=NULL 后重新可见=自动进入重试。"""
        now = time.time() if now is None else now
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT * FROM wakeup_events ORDER BY ts DESC, id DESC").fetchall()
        latest: dict[tuple[str, str], dict] = {}
        for r in rows:
            ev = self._row_to_event(r)
            key = (ev["agent_id"], ev["session_id"])
            if key not in latest:
                latest[key] = ev
        due = [
            e
            for e in latest.values()
            if (not e["stopped"])
            and e["next_wakeup_at"] is not None
            and e["next_wakeup_at"] <= now
            and not e["fired"]
            and e["fire_attempts"] < MAX_FIRE_ATTEMPTS
        ]
        due.sort(key=lambda e: e["next_wakeup_at"] or 0)
        return due

    # ── 执行：claim_fire / ack_fire（executor到点真正"再醒"） ──

    def claim_fire(self, event_id: int) -> dict | None:
        """CAS领取一次到点唤醒的执行权（at-most-once）。

        UPDATE ... WHERE fired_at IS NULL AND fire_attempts < MAX 的 rowcount==1
        即唯一赢家（双实例executor并发领取时输家拿None，绝不双跑）。
        领取即 fire_attempts+1（失败重试计数在领取时发生，ack只决定释放/终结）。"""
        now = time.time()
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE wakeup_events SET fired_at=?, fire_attempts=fire_attempts+1 "
                "WHERE id=? AND fired_at IS NULL AND fire_attempts < ?",
                (now, int(event_id), MAX_FIRE_ATTEMPTS),
            )
            if cur.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT * FROM wakeup_events WHERE id=?", (int(event_id),)
            ).fetchone()
        return self._row_to_event(row) if row is not None else None

    def ack_fire(self, event_id: int, ok: bool, error: str = "") -> dict:
        """执行结果回执。ok=True：保持fired终态；ok=False：
        - 未达MAX_FIRE_ATTEMPTS → 释放（fired_at=NULL）重新可见=自动重试；
        - 已达上限 → EXHAUSTED终态（fired_at保持+last_fire_error留痕），绝不再烧。"""
        text = _trim_reason(error)
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM wakeup_events WHERE id=?", (int(event_id),)
            ).fetchone()
            if row is None:
                raise ValueError(f"wakeup事件不存在: {event_id}")
            attempts = int(row["fire_attempts"])
            if ok:
                conn.execute(
                    "UPDATE wakeup_events SET last_fire_error='' WHERE id=?", (int(event_id),)
                )
                state = "fired"
            elif attempts >= MAX_FIRE_ATTEMPTS:
                conn.execute(
                    "UPDATE wakeup_events SET last_fire_error=? WHERE id=?",
                    (f"EXHAUSTED after {attempts} attempts: {text}", int(event_id)),
                )
                state = "exhausted"
            else:
                conn.execute(
                    "UPDATE wakeup_events SET fired_at=NULL, last_fire_error=? WHERE id=?",
                    (text, int(event_id)),
                )
                state = "retry"
            row2 = conn.execute(
                "SELECT * FROM wakeup_events WHERE id=?", (int(event_id),)
            ).fetchone()
        ev = self._row_to_event(row2)
        return {
            "event_id": int(event_id),
            "state": state,
            "retryable": state == "retry",
            "fire_attempts": ev["fire_attempts"],
            "last_fire_error": ev["last_fire_error"],
            "fired": ev["fired"],
            "exhausted": ev["exhausted"],
        }


# ── 单例（get_job_queue 同款惯例） ──────────────────────────

_scheduler: WakeupScheduler | None = None
_scheduler_lock = threading.Lock()


def get_wakeup_scheduler() -> WakeupScheduler:
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = WakeupScheduler()
    return _scheduler
