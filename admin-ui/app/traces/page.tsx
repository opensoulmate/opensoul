'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, ChevronDown, ChevronRight, Clock,
  Database, Bot, Shield, Eye, ArrowRight, Lock, Filter,
  Play, AlertTriangle, FileText, MessageSquare,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface TrajectorySession {
  session_id: string;
  event_count: number;
  first_event: string;
  last_event: string;
  agents: string;
}

interface TrajectoryEvent {
  id: number;
  session_id: string;
  agent_id: string;
  event_type: string;
  content: string;
  metadata: Record<string, any>;
  timestamp: string;
}

interface SessionDetail {
  session_id: string;
  events: TrajectoryEvent[];
  count: number;
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch { return ts; }
}

function formatRelative(ts: string) {
  if (!ts) return '';
  try {
    const diff = Date.now() - new Date(ts).getTime();
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    return `${Math.floor(diff / 86400000)}天前`;
  } catch { return ''; }
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  tool_call: '工具调用',
  message: '消息',
  thought: '思考',
  action: '操作',
  result: '结果',
  error: '错误',
  feedback: '反馈',
  knowledge: '知识提取',
  pattern: '模式发现',
  decision: '决策',
  start: '开始',
  end: '结束',
};

const EVENT_TYPE_COLORS: Record<string, string> = {
  tool_call: 'bg-blue-500/10 text-blue-500 border-blue-500/30',
  message: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30',
  thought: 'bg-violet-500/10 text-violet-500 border-violet-500/30',
  action: 'bg-amber-500/10 text-amber-500 border-amber-500/30',
  result: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30',
  error: 'bg-red-500/10 text-red-500 border-red-500/30',
  feedback: 'bg-cyan-500/10 text-cyan-500 border-cyan-500/30',
  knowledge: 'bg-indigo-500/10 text-indigo-500 border-indigo-500/30',
  pattern: 'bg-amber-500/10 text-amber-500 border-amber-500/30',
  decision: 'bg-pink-500/10 text-pink-500 border-pink-500/30',
  start: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30',
  end: 'bg-gray-500/10 text-gray-500 border-gray-500/30',
};

function EventTypeBadge({ type }: { type: string }) {
  const colorClass = EVENT_TYPE_COLORS[type] || 'bg-muted text-muted-foreground border-border';
  const label = EVENT_TYPE_LABELS[type] || type;
  return (
    <Badge variant="bare" className={`border ${colorClass}`}>
      {label}
    </Badge>
  );
}

function SessionCard({ session, expanded, onToggle, events, loadingEvents }: {
  session: TrajectorySession;
  expanded: boolean;
  onToggle: () => void;
  events: TrajectoryEvent[];
  loadingEvents: boolean;
}) {
  const agentList = (session.agents || '').split(',').filter(Boolean);

  return (
    <div className="border border-border rounded-lg bg-card">
      <Button
        onClick={onToggle}
        variant="row" className="gap-3 p-3 hover:bg-muted/30 transition-colors"
      >
        {/* Immutable badge */}
        <div className="relative">
          <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center">
            <Database className="w-5 h-5 text-muted-foreground" />
          </div>
          <Lock className="absolute -bottom-0.5 -right-0.5 w-3 h-3 text-amber-500" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium font-mono truncate">{session.session_id}</span>
            <Badge variant="muted" className="flex items-center gap-1">
              <Lock className="w-2.5 h-2.5" />不可变
            </Badge>
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <FileText className="w-2.5 h-2.5" />{session.event_count} 事件
            </span>
            {agentList.length > 0 && (
              <span className="flex items-center gap-1">
                <Bot className="w-2.5 h-2.5" />{agentList.length} Agent
              </span>
            )}
            <span className="flex items-center gap-1">
              <Clock className="w-2.5 h-2.5" />{formatRelative(session.last_event)}
            </span>
          </div>
        </div>

        <div className="text-right shrink-0 mr-2">
          <div className="text-xs text-muted-foreground">{formatTime(session.first_event)}</div>
          <div className="text-[10px] text-muted-foreground">→ {formatTime(session.last_event)}</div>
        </div>

        {expanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
      </Button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-border pt-2">
          {/* Agent tags */}
          {agentList.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-3">
              {agentList.map((agent) => (
                <Badge key={agent} variant="default">
                  <Bot className="w-2.5 h-2.5 inline mr-1" />{agent.trim()}
                </Badge>
              ))}
            </div>
          )}

          {/* Events timeline */}
          {loadingEvents ? (
            <div className="text-xs text-muted-foreground py-4 text-center">加载事件...</div>
          ) : events.length > 0 ? (
            <div className="space-y-1 max-h-[400px] overflow-auto">
              {events.map((evt, i) => (
                <div key={evt.id || i} className="flex items-start gap-2 py-1.5 px-2 rounded hover:bg-muted/30 group">
                  {/* Timeline dot */}
                  <div className="flex flex-col items-center mt-1 shrink-0">
                    <div className="w-1.5 h-1.5 rounded-full bg-primary" />
                    {i < events.length - 1 && <div className="w-px h-full bg-border mt-0.5" />}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <EventTypeBadge type={evt.event_type} />
                      {evt.agent_id && (
                        <span className="text-[10px] text-muted-foreground font-mono">{evt.agent_id}</span>
                      )}
                      <span className="text-[10px] text-muted-foreground ml-auto shrink-0">
                        {formatTime(evt.timestamp)}
                      </span>
                    </div>
                    {evt.content && (
                      <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2 break-all">
                        {evt.content.length > 200 ? evt.content.slice(0, 200) + '...' : evt.content}
                      </p>
                    )}
                    {evt.metadata && Object.keys(evt.metadata).length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {Object.entries(evt.metadata).slice(0, 5).map(([k, v]) => (
                          <Badge key={k} variant="muted" className="font-mono">
                            {k}: {typeof v === 'string' ? v.slice(0, 30) : JSON.stringify(v).slice(0, 30)}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-xs text-muted-foreground py-4 text-center">暂无事件数据</div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function TracesPage() {
  const [sessions, setSessions] = useState<TrajectorySession[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [expandedSession, setExpandedSession] = useState<string | null>(null);
  const [sessionDetail, setSessionDetail] = useState<Record<string, TrajectoryEvent[]>>({});
  const [loadingDetail, setLoadingDetail] = useState<string | null>(null);
  const [eventTypeFilter, setEventTypeFilter] = useState('all');
  const [page, setPage] = useState(0);
  const pageSize = 20;

  const fetchSessions = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch(`/api/trajectory/sessions?limit=${pageSize}&offset=${page * pageSize}`);
      setSessions(Array.isArray(data.sessions) ? data.sessions : []);
      setTotal(data.total || 0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { fetchSessions(); }, [fetchSessions]);

  const loadSessionDetail = useCallback(async (sessionId: string) => {
    if (sessionDetail[sessionId]) return; // already loaded
    try {
      setLoadingDetail(sessionId);
      const data = await apiFetch(`/api/trajectory/sessions/${encodeURIComponent(sessionId)}`);
      setSessionDetail(prev => ({ ...prev, [sessionId]: data.events || [] }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoadingDetail(null);
    }
  }, [sessionDetail]);

  const handleToggleSession = useCallback((sessionId: string) => {
    if (expandedSession === sessionId) {
      setExpandedSession(null);
    } else {
      setExpandedSession(sessionId);
      loadSessionDetail(sessionId);
    }
  }, [expandedSession, loadSessionDetail]);

  // Client-side search filter
  const filtered = sessions.filter((s) => {
    if (search) {
      const q = search.toLowerCase();
      return s.session_id.toLowerCase().includes(q) ||
        (s.agents || '').toLowerCase().includes(q);
    }
    return true;
  });

  const totalPages = Math.ceil(total / pageSize);

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{total}</div>
          <div className="text-xs text-muted-foreground">总会话轨迹</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-amber-500">
            {sessions.reduce((s, sess) => s + sess.event_count, 0)}
          </div>
          <div className="text-xs text-muted-foreground">总事件数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Lock className="w-4 h-4 text-amber-500" />
            <span className="text-2xl font-bold">不可变</span>
          </div>
          <div className="text-xs text-muted-foreground">轨迹只追加，不删除</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-primary">
            {new Set(sessions.flatMap(s => (s.agents || '').split(',').filter(Boolean))).size}
          </div>
          <div className="text-xs text-muted-foreground">参与Agent数</div>
        </div>
      </div>

      {/* Immutability notice */}
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 flex items-center gap-2">
        <Shield className="w-4 h-4 text-amber-500 shrink-0" />
        <div>
          <span className="text-xs font-medium text-amber-500">Raw层 · 不可变执行轨迹</span>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            所有执行轨迹为只追加（append-only），不可修改或删除。这是知识大脑的原始记忆层。
          </p>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索会话ID、Agent..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button onClick={() => fetchSessions()} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Session list */}
      <div className="space-y-2">
        {filtered.map((session) => (
          <SessionCard
            key={session.session_id}
            session={session}
            expanded={expandedSession === session.session_id}
            onToggle={() => handleToggleSession(session.session_id)}
            events={sessionDetail[session.session_id] || []}
            loadingEvents={loadingDetail === session.session_id}
          />
        ))}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted-foreground">
            第 {page + 1} / {totalPages} 页，共 {total} 条
          </span>
          <div className="flex items-center gap-1">
            <Button onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0} variant="secondary" className="disabled:opacity-30">
              上一页
            </Button>
            <Button onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
              disabled={page >= totalPages - 1} variant="secondary" className="disabled:opacity-30">
              下一页
            </Button>
          </div>
        </div>
      )}

      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Database className="w-10 h-10 mb-2 opacity-30" />
          <p className="text-xs">暂无执行轨迹</p>
          <p className="text-[10px] mt-1">Agent执行时会自动记录轨迹到Raw层</p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500 shrink-0" />
          <span className="text-xs text-red-500">{error}</span>
          <Button onClick={() => setError('')} variant="icon-sm" className="ml-auto hover:bg-muted">
            <X className="w-3 h-3" />
          </Button>
        </div>
      )}
    </div>
  );
}
