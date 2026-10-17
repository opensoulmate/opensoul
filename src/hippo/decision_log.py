"""P0-6: 决策日志 — TradingAgents延迟回填模式（pending→update_with_outcome）。

调研来源（14-tradingagents-source.md #1/#2/#3/#4 + SUMMARY.md P0-6）：
- store_decision先记pending → 结果出来后update_with_outcome把真实结果写回同一条目
  —— "先记决策、后补结果"是让记忆带真实反馈信号的最便宜方案（"不进化"痛点独有解）。
- as_of时间旅行过滤（#1251）：检索时只取resolved日期≤as_of的条目，防回放从未来学答案。
- 同域/跨域检索预算：同domain取全量格式（决策+结果+反思），跨domain只取反思部分
  —— token效率设计（TradingAgents n_same/n_cross两档粒度）。
- 轮转保pending（#4）：超max_resolved时淘汰最旧的已resolved条目，pending永不淘汰
  （pending=未处理工作）。
- mem0 §1.2审计表：每次store/resolve写decision_audit记录（old/new/event），全程可追溯。
- mem0 §1.1失败必须可见：update未知decision_id返回None，不静默假成功。

与TradingAgents的差异（已在各docstring注明）：
- 原版是append-only markdown文件+原子写；本实现用SQLite（OpenSoul hippo既有惯例，
  LongTermMemoryStore同款），审计表替代tag行标记。
- domain字段=ticker的泛化（OpenSoul决策域：llm_routing/tool_choice/intent/...）。
- 原版raw_return/alpha是金融指标 → 泛化为outcome_metrics自由JSON + success布尔。

运行时接线（写了≠接线了，两条真实路径）：
- 写路径：src/api/chat.py三个LLM调用点——路由决策先记pending，调用结束后回填
  attempts_detail真实结果（provider×ok）。
- 读路径①：src/gland/route_policy.py resolve_target——auto/balance模式下按
  get_provider_stats()近期provider成功率做主备互换（决策记忆直接影响下一次决策）。
- 读路径②：src/api/hippo.py /api/hippo/decisions/* 五个端点 + get_past_context()。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("opensoul.hippo.decision_log")

VALID_STATUSES = ("pending", "resolved")


@dataclass
class DecisionEntry:
    """一条决策记录。status=pending时outcome为空；回填后status=resolved。"""

    decision_id: str
    decision: str
    domain: str = ""  # ticker泛化：llm_routing / tool_choice / intent ...
    agent_id: str = ""
    context: str = ""
    rating: str = ""  # 置信度/分类标签（TradingAgents rating泛化）
    status: str = "pending"
    outcome: str = ""
    outcome_metrics: dict[str, Any] = field(default_factory=dict)
    reflection: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source_session: str = ""
    created_at: float = field(default_factory=time.time)
    resolved_at: float = 0.0  # 0=pending；回填时盖章
    resolution_date: str = ""  # 'YYYY-MM-DD' 结果已知日期（as_of过滤用，#1251）

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "decision": self.decision,
            "domain": self.domain,
            "agent_id": self.agent_id,
            "context": self.context,
            "rating": self.rating,
            "status": self.status,
            "outcome": self.outcome,
            "outcome_metrics": self.outcome_metrics,
            "reflection": self.reflection,
            "metadata": self.metadata,
            "source_session": self.source_session,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolution_date": self.resolution_date,
        }


def _decision_hash(domain: str, decision: str) -> str:
    return hashlib.sha256(f"{domain}\x00{decision}".encode()).hexdigest()[:16]


class DecisionLog:
    """SQLite-backed决策日志（TradingAgents TradingMemoryLog的OpenSoul移植）。"""

    def __init__(self, db_path: str = "", max_resolved: int = 500):
        if not db_path:
            base = Path.home() / ".hermes" / "opensoul" / "hippo"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "decision_log.db")
        self.db_path = db_path
        # TradingAgents #4: 轮转上限只作用于resolved条目，pending永不淘汰
        self.max_resolved = max_resolved if max_resolved and max_resolved > 0 else 0
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_log (
                    decision_id TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    decision_hash TEXT NOT NULL,
                    domain TEXT DEFAULT '',
                    agent_id TEXT DEFAULT '',
                    context TEXT DEFAULT '',
                    rating TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    outcome TEXT DEFAULT '',
                    outcome_metrics TEXT DEFAULT '{}',
                    reflection TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}',
                    source_session TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    resolved_at REAL DEFAULT 0,
                    resolution_date TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dec_status ON decision_log(status, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dec_domain ON decision_log(domain, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dec_hash ON decision_log(decision_hash, status)"
            )
            # mem0 §1.2审计表：memory_id/old/new/event/is_deleted镜像
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_audit (
                    audit_id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    old_value TEXT DEFAULT '',
                    new_value TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dec_audit ON decision_audit(decision_id, created_at)"
            )
            conn.commit()

    # ── 内部 helpers ──────────────────────────────────────────

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> dict:
        d = dict(row)
        for key in ("outcome_metrics", "metadata"):
            try:
                d[key] = json.loads(d.get(key) or "{}")
            except (json.JSONDecodeError, TypeError):
                d[key] = {}
        return d

    def _write_audit(
        self,
        decision_id: str,
        event: str,
        old_value: str = "",
        new_value: str = "",
        reason: str = "",
    ):
        audit_id = f"daud_{hashlib.sha256(f'{decision_id}:{event}:{time.time()}'.encode()).hexdigest()[:12]}"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO decision_audit
                   (audit_id, decision_id, event, old_value, new_value, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (audit_id, decision_id, event, old_value, new_value, reason, time.time()),
            )
            conn.commit()

    def _apply_rotation(self):
        """TradingAgents #4轮转：resolved超max_resolved时淘汰最旧，pending永不淘汰。"""
        if not self.max_resolved:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM decision_log WHERE status = 'resolved'"
            ).fetchone()["c"]
            if total <= self.max_resolved:
                return
            to_drop = total - self.max_resolved
            rows = conn.execute(
                """SELECT decision_id, decision, domain, outcome FROM decision_log
                   WHERE status = 'resolved' ORDER BY resolved_at ASC LIMIT ?""",
                (to_drop,),
            ).fetchall()
            for r in rows:
                conn.execute("DELETE FROM decision_log WHERE decision_id = ?", (r["decision_id"],))
            conn.commit()
        for r in rows:
            self._write_audit(
                r["decision_id"],
                "ROTATION",
                old_value=json.dumps(
                    {"status": "resolved", "domain": r["domain"], "decision": r["decision"][:200]},
                    ensure_ascii=False,
                ),
                new_value="",
                reason="rotation_evict_oldest_resolved",
            )

    # ── 写路径（Phase A: store_decision） ─────────────────────

    def store_decision(
        self,
        decision: str,
        domain: str = "",
        agent_id: str = "",
        context: str = "",
        rating: str = "",
        metadata: dict | None = None,
        source_session: str = "",
    ) -> dict:
        """记录一条pending决策。

        幂等保护（TradingAgents store_decision raw-text scan泛化）：
        同(domain, decision)已有pending条目时不重复创建，返回既有条目。
        """
        if not decision or not decision.strip():
            raise ValueError("decision must be non-empty")
        d_hash = _decision_hash(domain, decision)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                """SELECT * FROM decision_log
                   WHERE decision_hash = ? AND status = 'pending' LIMIT 1""",
                (d_hash,),
            ).fetchone()
            if existing:
                logger.debug(f"Decision deduped (pending exists): {d_hash}")
                out = self._row_to_entry(existing)
                out["deduped"] = True
                return out
            decision_id = f"dec_{d_hash}_{int(time.time() * 1000)}"
            entry = DecisionEntry(
                decision_id=decision_id,
                decision=decision,
                domain=domain,
                agent_id=agent_id,
                context=context,
                rating=rating,
                metadata=metadata or {},
                source_session=source_session,
            )
            conn.execute(
                """INSERT INTO decision_log
                   (decision_id, decision, decision_hash, domain, agent_id, context,
                    rating, status, metadata, source_session, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (
                    decision_id,
                    decision,
                    d_hash,
                    domain,
                    agent_id,
                    context,
                    rating,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    source_session,
                    entry.created_at,
                ),
            )
            conn.commit()
        self._write_audit(
            decision_id,
            "STORE",
            old_value="",
            new_value=json.dumps(
                {"decision": decision[:200], "domain": domain, "status": "pending"},
                ensure_ascii=False,
            ),
            reason="store_decision",
        )
        return entry.to_dict()

    # ── 回填路径（Phase B: update_with_outcome） ──────────────

    def update_with_outcome(
        self,
        decision_id: str = "",
        domain: str = "",
        outcome: str = "",
        metrics: dict | None = None,
        reflection: str = "",
        success: bool | None = None,
        resolution_date: str = "",
    ) -> dict | None:
        """把真实结果回填到同一条pending决策（TradingAgents update_with_outcome）。

        - decision_id为空时按domain找最早的pending条目（原版按(ticker,date)匹配泛化）。
        - 只更新pending条目；已resolved条目原样返回且带"_already_resolved"标志
          （不覆盖已有结果——回填是幂等的）。
        - 未知decision_id返回None（mem0 §1.1：失败必须可见，不静默假成功）。
        - resolution_date缺省=今天（as_of时间旅行过滤的事实日期）。
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if decision_id:
                row = conn.execute(
                    "SELECT * FROM decision_log WHERE decision_id = ?", (decision_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT * FROM decision_log
                       WHERE status = 'pending' AND domain = ?
                       ORDER BY created_at ASC LIMIT 1""",
                    (domain,),
                ).fetchone()
            if row is None:
                logger.warning(
                    f"update_with_outcome: no matching decision (id={decision_id!r} domain={domain!r})"
                )
                return None
            entry = self._row_to_entry(row)
            if entry["status"] != "pending":
                entry["_already_resolved"] = True
                return entry

            merged_metrics = dict(entry.get("outcome_metrics") or {})
            if metrics:
                merged_metrics.update(metrics)
            if success is not None:
                merged_metrics["success"] = success
            res_date = resolution_date or datetime.now().strftime("%Y-%m-%d")
            now = time.time()
            conn.execute(
                """UPDATE decision_log
                   SET status = 'resolved', outcome = ?, outcome_metrics = ?,
                       reflection = ?, resolved_at = ?, resolution_date = ?
                   WHERE decision_id = ?""",
                (
                    outcome,
                    json.dumps(merged_metrics, ensure_ascii=False),
                    reflection,
                    now,
                    res_date,
                    entry["decision_id"],
                ),
            )
            conn.commit()

        self._write_audit(
            entry["decision_id"],
            "RESOLVE",
            old_value=json.dumps({"status": "pending"}, ensure_ascii=False),
            new_value=json.dumps(
                {
                    "status": "resolved",
                    "outcome": outcome[:200],
                    "metrics": merged_metrics,
                    "resolution_date": res_date,
                },
                ensure_ascii=False,
            ),
            reason="update_with_outcome",
        )
        self._apply_rotation()
        entry.update(
            {
                "status": "resolved",
                "outcome": outcome,
                "outcome_metrics": merged_metrics,
                "reflection": reflection,
                "resolved_at": now,
                "resolution_date": res_date,
            }
        )
        return entry

    # ── 查询路径 ──────────────────────────────────────────────

    def _query(self, sql: str, params: list) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def get_pending_entries(self, domain: str = "", agent_id: str = "") -> list[dict]:
        sql = "SELECT * FROM decision_log WHERE status = 'pending'"
        params: list = []
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        if agent_id:
            sql += " AND agent_id = ?"
            params.append(agent_id)
        sql += " ORDER BY created_at ASC"
        return self._query(sql, params)

    def list_decisions(self, status: str = "", domain: str = "", limit: int = 50) -> list[dict]:
        if status and status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")
        sql = "SELECT * FROM decision_log WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        return self._query(sql, params)

    @staticmethod
    def _format_full(e: dict) -> str:
        """同域条目：全量格式（决策+结果+反思）——TradingAgents _format_full。"""
        metrics = e.get("outcome_metrics") or {}
        metric_str = ", ".join(
            f"{k}={v}" for k, v in metrics.items() if isinstance(v, (int, float, bool, str))
        )[:200]
        tag = f"[{e.get('resolution_date', '')} | {e.get('domain', '')} | {e.get('rating', '')} | {metric_str}]"
        parts = [tag, f"DECISION:\n{e.get('decision', '')}"]
        if e.get("outcome"):
            parts.append(f"OUTCOME:\n{e['outcome']}")
        if e.get("reflection"):
            parts.append(f"REFLECTION:\n{e['reflection']}")
        return "\n\n".join(parts)

    @staticmethod
    def _format_reflection_only(e: dict) -> str:
        """跨域条目：只取反思部分（无反思则决策截断300字）——token预算设计。"""
        tag = f"[{e.get('resolution_date', '')} | {e.get('domain', '')} | {e.get('rating', '')}]"
        if e.get("reflection"):
            return f"{tag}\n{e['reflection']}"
        text = (e.get("decision") or "")[:300]
        return f"{tag}\n{text}"

    def get_past_context(
        self,
        domain: str = "",
        n_same: int = 5,
        n_cross: int = 3,
        as_of: str = "",
        token_budget: int = 800,
    ) -> str:
        """带真实反馈信号的few-shot上下文（TradingAgents get_past_context移植）。

        - 只取resolved条目（pending无结果，不进上下文）。
        - as_of='YYYY-MM-DD'时只取resolution_date≤as_of的条目（#1251时间旅行过滤：
          防回放/复盘从"未来"学答案）；resolution_date为空的条目在as_of模式下排除。
        - domain非空：同domain条目全量格式（n_same条），其他domain只取反思（n_cross条）。
        - token_budget≈字符预算（×4近似），超预算按顺序截断。
        """
        entries = self._query(
            "SELECT * FROM decision_log WHERE status = 'resolved' ORDER BY resolved_at DESC LIMIT 500",
            [],
        )
        if as_of:
            entries = [
                e for e in entries if e.get("resolution_date") and e["resolution_date"] <= as_of
            ]
        if not entries:
            return ""

        same: list[dict] = []
        cross: list[dict] = []
        if domain:
            for e in entries:
                if e["domain"] == domain and len(same) < n_same:
                    same.append(e)
                elif e["domain"] != domain and len(cross) < n_cross:
                    cross.append(e)
                if len(same) >= n_same and len(cross) >= n_cross:
                    break
        else:
            same = entries[: n_same + n_cross]

        if not same and not cross:
            return ""

        parts: list[str] = []
        if same:
            header = (
                f"Past decisions in domain '{domain}' (most recent first, with real outcomes):"
                if domain
                else "Past decisions (most recent first, with real outcomes):"
            )
            parts.append(header)
            parts.extend(self._format_full(e) for e in same)
        if cross:
            parts.append("Recent cross-domain lessons (reflection only):")
            parts.extend(self._format_reflection_only(e) for e in cross)

        text = "\n\n".join(parts)
        char_budget = max(200, token_budget * 4)
        if len(text) > char_budget:
            text = text[:char_budget] + "\n...[decision context truncated]"
        return text

    def get_provider_stats(self, domain: str = "llm_routing", window: int = 100) -> dict:
        """按provider聚合attempts_detail的成功/失败统计（route_policy反馈数据源）。

        metrics约定（src/api/chat.py写入）：attempts_detail=[{provider, ok, error?}, ...]。
        返回 {provider: {"attempts": n, "failures": m, "failure_rate": f}}。
        无attempts_detail时退化用metrics的(used_provider, success)记一次尝试。
        """
        entries = self._query(
            """SELECT outcome_metrics FROM decision_log
               WHERE status = 'resolved' AND domain = ?
               ORDER BY resolved_at DESC LIMIT ?""",
            [domain, max(1, min(int(window), 1000))],
        )
        agg: dict[str, dict[str, int]] = {}
        for e in entries:
            metrics = e.get("outcome_metrics") or {}
            detail = metrics.get("attempts_detail")
            if isinstance(detail, list) and detail:
                for a in detail:
                    if not isinstance(a, dict) or not a.get("provider"):
                        continue
                    prov = a["provider"]
                    slot = agg.setdefault(prov, {"attempts": 0, "failures": 0})
                    slot["attempts"] += 1
                    if not a.get("ok"):
                        slot["failures"] += 1
            else:
                prov = metrics.get("used_provider") or metrics.get("provider") or ""
                if not prov:
                    continue
                slot = agg.setdefault(prov, {"attempts": 0, "failures": 0})
                slot["attempts"] += 1
                if metrics.get("success") is False:
                    slot["failures"] += 1
        return {
            prov: {
                **slot,
                "failure_rate": slot["failures"] / slot["attempts"] if slot["attempts"] else 0.0,
            }
            for prov, slot in agg.items()
        }

    def get_stats(self) -> dict:
        """决策日志统计——"有没有进化"的度量面（success_rate随时间可观察）。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute("SELECT COUNT(*) AS c FROM decision_log").fetchone()["c"]
            by_status = conn.execute(
                "SELECT status, COUNT(*) AS c FROM decision_log GROUP BY status"
            ).fetchall()
            by_domain = conn.execute(
                "SELECT domain, COUNT(*) AS c FROM decision_log GROUP BY domain ORDER BY c DESC LIMIT 10"
            ).fetchall()
            resolved_with_success = conn.execute(
                """SELECT COUNT(*) AS c FROM decision_log
                   WHERE status = 'resolved' AND outcome_metrics LIKE '%"success": true%'"""
            ).fetchone()["c"]
            resolved_total = conn.execute(
                "SELECT COUNT(*) AS c FROM decision_log WHERE status = 'resolved'"
            ).fetchone()["c"]
            recent = conn.execute(
                """SELECT decision_id, domain, status, decision, outcome, resolution_date
                   FROM decision_log ORDER BY created_at DESC LIMIT 5"""
            ).fetchall()
        return {
            "total": total,
            "by_status": {r["status"]: r["c"] for r in by_status},
            "by_domain": {r["domain"] or "(empty)": r["c"] for r in by_domain},
            "success_rate": (resolved_with_success / resolved_total) if resolved_total else None,
            "provider_stats_llm_routing": self.get_provider_stats(domain="llm_routing"),
            "max_resolved": self.max_resolved,
            "recent": [dict(r) for r in recent],
        }

    def get_audit(self, decision_id: str = "", limit: int = 50) -> list[dict]:
        """审计轨迹（mem0 §1.2：这条决策是被谁、因为什么、从什么改成什么）。"""
        sql = "SELECT * FROM decision_audit WHERE 1=1"
        params: list = []
        if decision_id:
            sql += " AND decision_id = ?"
            params.append(decision_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        return self._query(sql, params)


# ── 单例 ─────────────────────────────────────────────────────
_decision_log: DecisionLog | None = None


def get_decision_log(db_path: str = "") -> DecisionLog:
    """进程级单例（与LongTermMemoryStore同款模式）。测试可monkeypatch本模块全局。"""
    global _decision_log
    if _decision_log is None:
        _decision_log = DecisionLog(db_path=db_path)
    return _decision_log
