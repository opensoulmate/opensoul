#!/usr/bin/env python3
"""CD-⑥ 编辑脚本：traces/page.tsx 消费面收编真源键 + events 端点改指真端点。

精确字符串替换（增量修改禁全量重写），逐条 old 唯一命中才替换，任何一条
不唯一/缺失即中止零写入。绕 patch 通道竖线坑（F3b-⑤ 同纪律），替换后 chr(124)
探针验证管道字节零星号损坏。
"""
from __future__ import annotations

import sys
from pathlib import Path

PAGE = Path("/home/climbing/opensoul/admin-ui/app/traces/page.tsx")

REPLACEMENTS = [
    # ① session 元素接口收编真源键（TrajectorySession.to_dict 真源形）
    (
        """interface TrajectorySession {
  session_id: string;
  event_count: number;
  first_event: string;
  last_event: string;
  agents: string;
}""",
        """interface TrajectorySession {
  id: string;
  agent_id: string;
  total_events: number;
  created_at: string;
  ended_at: string | null;
}""",
    ),
    # ② event 元素接口收编真源键（TrajectoryEvent.to_dict 真源形：id=str、created_at）
    (
        """interface TrajectoryEvent {
  id: number;
  session_id: string;
  agent_id: string;
  event_type: string;
  content: string;
  metadata: Record<string, any>;
  timestamp: string;
}""",
        """interface TrajectoryEvent {
  id: string;
  session_id: string;
  agent_id: string;
  event_type: string;
  content: string;
  metadata: Record<string, any>;
  created_at: string;
}""",
    ),
    # ③ agent 列表=真源 agent_id 单值（真源无多 agent 逗号串字段）
    (
        "  const agentList = (session.agents || '').split(',').filter(Boolean);",
        "  const agentList = session.agent_id ? [session.agent_id] : [];",
    ),
    # ④ 元素键逐点收编
    (
        '<span className="text-sm font-medium font-mono truncate">{session.session_id}</span>',
        '<span className="text-sm font-medium font-mono truncate">{session.id}</span>',
    ),
    (
        '<FileText className="w-2.5 h-2.5" />{session.event_count} 事件',
        '<FileText className="w-2.5 h-2.5" />{session.total_events} 事件',
    ),
    (
        '<Clock className="w-2.5 h-2.5" />{formatRelative(session.last_event)}',
        '<Clock className="w-2.5 h-2.5" />{formatRelative(session.ended_at || session.created_at)}',
    ),
    (
        '<div className="text-xs text-muted-foreground">{formatTime(session.first_event)}</div>',
        '<div className="text-xs text-muted-foreground">{formatTime(session.created_at)}</div>',
    ),
    (
        '<div className="text-[10px] text-muted-foreground">→ {formatTime(session.last_event)}</div>',
        '<div className="text-[10px] text-muted-foreground">→ {formatTime(session.ended_at || session.created_at)}</div>',
    ),
    (
        "{formatTime(evt.timestamp)}",
        "{formatTime(evt.created_at)}",
    ),
    # ⑤ events 端点改指真端点（CD-③ 候选③ 本体）：GET /sessions/{id} 无 events 键
    #    → GET /sessions/{id}/events 真源 {session_id,events,count}
    (
        """      const data = await apiFetch(`/api/trajectory/sessions/${encodeURIComponent(sessionId)}`);
      setSessionDetail(prev => ({ ...prev, [sessionId]: data.events || [] }));""",
        """      const data: SessionDetail = await apiFetch(`/api/trajectory/sessions/${encodeURIComponent(sessionId)}/events`);
      setSessionDetail(prev => ({ ...prev, [sessionId]: data.events || [] }));""",
    ),
    # ⑥ 搜索过滤收编真源键
    (
        """      return s.session_id.toLowerCase().includes(q) ||
        (s.agents || '').toLowerCase().includes(q);""",
        """      return s.id.toLowerCase().includes(q) ||
        (s.agent_id || '').toLowerCase().includes(q);""",
    ),
    # ⑦ 统计卡收编真源键
    (
        "{sessions.reduce((s, sess) => s + sess.event_count, 0)}",
        "{sessions.reduce((s, sess) => s + sess.total_events, 0)}",
    ),
    (
        "{new Set(sessions.flatMap(s => (s.agents || '').split(',').filter(Boolean))).size}",
        "{new Set(sessions.map(s => s.agent_id).filter(Boolean)).size}",
    ),
    # ⑧ 列表渲染键收编真源键
    (
        """            key={session.session_id}
            session={session}
            expanded={expandedSession === session.session_id}
            onToggle={() => handleToggleSession(session.session_id)}
            events={sessionDetail[session.session_id] || []}
            loadingEvents={loadingDetail === session.session_id}""",
        """            key={session.id}
            session={session}
            expanded={expandedSession === session.id}
            onToggle={() => handleToggleSession(session.id)}
            events={sessionDetail[session.id] || []}
            loadingEvents={loadingDetail === session.id}""",
    ),
]


def main() -> int:
    src = PAGE.read_text(encoding="utf-8")
    out = src
    for i, (old, new) in enumerate(REPLACEMENTS):
        n = out.count(old)
        if n != 1:
            print(f"ABORT: replacement #{i} old-string count={n} (must be exactly 1)")
            return 1
        out = out.replace(old, new, 1)
    PAGE.write_text(out, encoding="utf-8")
    # chr(124) 探针：竖线字节零星号损坏（新增 union `string | null` 必须真在场）
    pipes = out.count(chr(124))
    stars_union = out.count("string * null")
    print(f"applied {len(REPLACEMENTS)} replacements; pipes={pipes} bad_union={stars_union}")
    assert "ended_at: string | null" in out, "union pipe corrupted"
    assert stars_union == 0, "pipe->star corruption"
    print("PIPE-PROBE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
