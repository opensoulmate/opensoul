'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  BarChart3, RefreshCw, Search, X, ChevronDown, ChevronRight,
  Play, GitFork, Clock, Bot, Zap, Activity, Database, Plus,
  Trash2, StopCircle, FileText, AlertTriangle, Lock, Eye,
  TrendingUp, Cpu, Hash,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface TrajectorySession {
  id: string;
  session_id: string;
  agent_id: string;
  task_description: string;
  tags: string[];
  status: string;
  event_count: number;
  created_at: string;
  updated_at: string;
  ended_at?: string;
}

interface TrajectoryEvent {
  id: string;
  event_type: string;
  agent_id: string;
  content: string;
  metadata: Record<string, any>;
  token_usage: number;
  duration_ms: number;
  status: string;
  created_at: string;
}

interface ToolAnalytics {
  tools: Array<{ tool_name: string; call_count: number; success_count: number; error_count: number; avg_duration_ms: number }>;
}

interface AgentAnalytics {
  agents: Array<{ agent_id: string; session_count: number; event_count: number; total_tokens: number }>;
}

interface EventTypeAnalytics {
  distribution: Array<{ event_type: string; count: number }>;
}

interface TokenAnalytics {
  daily: Array<{ date: string; tokens: number; events: number }>;
}

type Tab = 'sessions' | 'analytics' | 'search';

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

function formatDuration(ms: number) {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m${Math.round((ms % 60000) / 1000)}s`;
}

function formatTokens(n: number) {
  if (n < 1000) return String(n);
  if (n < 1000000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1000000).toFixed(2)}M`;
}

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30',
  completed: 'bg-blue-500/10 text-blue-500 border-blue-500/30',
  failed: 'bg-red-500/10 text-red-500 border-red-500/30',
  forked: 'bg-amber-500/10 text-amber-500 border-amber-500/30',
};

const EVENT_TYPE_LABELS: Record<string, string> = {
  tool_call: '工具调用', message: '消息', thought: '思考', action: '操作',
  result: '结果', error: '错误', feedback: '反馈', knowledge: '知识提取',
  pattern: '模式发现', decision: '决策', start: '开始', end: '结束',
  custom: '自定义',
};

const EVENT_TYPE_COLORS: Record<string, string> = {
  tool_call: '#3b82f6', message: '#22c55e', thought: '#a855f7',
  action: '#f59e0b', result: '#10b981', error: '#ef4444',
  feedback: '#06b6d4', knowledge: '#6366f1', pattern: '#f97316',
  decision: '#ec4899', start: '#22c55e', end: '#6b7280', custom: '#8b5cf6',
};

// ── Sub-components ──────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLORS[status] || 'bg-muted text-muted-foreground border-border';
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium border ${cls}`}>{status}</span>;
}

function EventTypeBadge({ type }: { type: string }) {
  const color = EVENT_TYPE_COLORS[type] || '#6b7280';
  const label = EVENT_TYPE_LABELS[type] || type;
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium border"
      style={{ backgroundColor: color + '1a', color, borderColor: color + '4d' }}
    >
      {label}
    </span>
  );
}

// ── Analytics Tab ───────────────────────────────────────────

function AnalyticsView() {
  const [toolData, setToolData] = useState<ToolAnalytics | null>(null);
  const [agentData, setAgentData] = useState<AgentAnalytics | null>(null);
  const [eventTypes, setEventTypes] = useState<EventTypeAnalytics | null>(null);
  const [tokenData, setTokenData] = useState<TokenAnalytics | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [t, a, et, tk] = await Promise.all([
        apiFetch('/api/trajectory/analytics/tools').catch(() => null),
        apiFetch('/api/trajectory/analytics/agents').catch(() => null),
        apiFetch('/api/trajectory/analytics/event-types').catch(() => null),
        apiFetch('/api/trajectory/analytics/tokens?days=30').catch(() => null),
      ]);
      setToolData(t as ToolAnalytics);
      setAgentData(a as AgentAnalytics);
      setEventTypes(et as EventTypeAnalytics);
      setTokenData(tk as TokenAnalytics);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载分析数据...</div>;

  const tools = toolData?.tools || [];
  const agents = agentData?.agents || [];
  const dist = eventTypes?.distribution || [];
  const daily = tokenData?.daily || [];
  const maxTokens = Math.max(1, ...daily.map(d => d.tokens));

  return (
    <div className="space-y-4">
      {/* Token usage chart */}
      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-primary" />Token 用量趋势（30天）
        </h3>
        {daily.length === 0 ? (
          <div className="text-xs text-muted-foreground text-center py-8">暂无数据</div>
        ) : (
          <div className="flex items-end gap-1 h-32">
            {daily.slice(-30).map((d, i) => (
              <div key={i} className="flex-1 flex flex-col items-center gap-1">
                <div
                  className="w-full rounded-t bg-primary/60 hover:bg-primary transition-colors min-h-[2px]"
                  style={{ height: `${(d.tokens / maxTokens) * 100}%` }}
                  title={`${d.date}: ${formatTokens(d.tokens)} tokens, ${d.events} events`}
                />
              </div>
            ))}
          </div>
        )}
        {daily.length > 0 && (
          <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
            <span>{daily[daily.length - 30]?.date || ''}</span>
            <span>{daily[daily.length - 1]?.date || ''}</span>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Event type distribution */}
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
            <Hash className="w-4 h-4 text-amber-500" />事件类型分布
          </h3>
          {dist.length === 0 ? (
            <div className="text-xs text-muted-foreground text-center py-8">暂无数据</div>
          ) : (
            <div className="space-y-2">
              {dist.sort((a, b) => b.count - a.count).map(item => {
                const maxC = Math.max(1, ...dist.map(d => d.count));
                const pct = Math.round((item.count / maxC) * 100);
                const color = EVENT_TYPE_COLORS[item.event_type] || '#6b7280';
                return (
                  <div key={item.event_type}>
                    <div className="flex items-center justify-between text-xs mb-0.5">
                      <span className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
                        {EVENT_TYPE_LABELS[item.event_type] || item.event_type}
                      </span>
                      <span className="text-muted-foreground">{item.count}</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                      <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Tool analytics */}
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
            <Cpu className="w-4 h-4 text-emerald-500" />工具使用频率
          </h3>
          {tools.length === 0 ? (
            <div className="text-xs text-muted-foreground text-center py-8">暂无数据</div>
          ) : (
            <div className="space-y-1.5 max-h-64 overflow-auto">
              {tools.slice(0, 15).map(t => {
                const successRate = t.call_count > 0 ? Math.round((t.success_count / t.call_count) * 100) : 0;
                return (
                  <div key={t.tool_name} className="flex items-center gap-2 text-xs py-1 px-2 rounded hover:bg-muted/30">
                    <span className="font-mono truncate flex-1 min-w-0">{t.tool_name}</span>
                    <span className="text-muted-foreground shrink-0">{t.call_count}次</span>
                    <span className={`shrink-0 ${successRate >= 90 ? 'text-green-500' : successRate >= 70 ? 'text-amber-500' : 'text-red-500'}`}>
                      {successRate}%
                    </span>
                    <span className="text-muted-foreground shrink-0">{formatDuration(t.avg_duration_ms)}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Agent performance */}
      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
          <Bot className="w-4 h-4 text-purple-500" />Agent 性能统计
        </h3>
        {agents.length === 0 ? (
          <div className="text-xs text-muted-foreground text-center py-8">暂无数据</div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {agents.map(a => (
              <div key={a.agent_id} className="rounded-md border border-border p-3 hover:bg-muted/30 transition-colors">
                <div className="flex items-center gap-2 mb-2">
                  <Bot className="w-3.5 h-3.5 text-purple-400" />
                  <span className="text-sm font-medium font-mono truncate">{a.agent_id || '未命名'}</span>
                </div>
                <div className="grid grid-cols-3 gap-2 text-center">
                  <div>
                    <div className="text-lg font-bold">{a.session_count}</div>
                    <div className="text-[10px] text-muted-foreground">会话</div>
                  </div>
                  <div>
                    <div className="text-lg font-bold">{a.event_count}</div>
                    <div className="text-[10px] text-muted-foreground">事件</div>
                  </div>
                  <div>
                    <div className="text-lg font-bold">{formatTokens(a.total_tokens)}</div>
                    <div className="text-[10px] text-muted-foreground">Token</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Search Tab ──────────────────────────────────────────────

function SearchView() {
  const [query, setQuery] = useState('');
  const [eventType, setEventType] = useState('');
  const [results, setResults] = useState<TrajectoryEvent[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);

  const doSearch = useCallback(async () => {
    if (!query && !eventType) return;
    setSearching(true);
    try {
      const params = new URLSearchParams();
      if (query) params.set('keyword', query);
      if (eventType) params.set('event_type', eventType);
      params.set('limit', '100');
      const data = await apiFetch(`/api/trajectory/search?${params}`);
      setResults(data.events || []);
      setSearched(true);
    } catch { setResults([]); }
    setSearching(false);
  }, [query, eventType]);

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap gap-2 mb-3">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <input
              className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="搜索事件内容..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            />
          </div>
          <select
            className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
            value={eventType}
            onChange={(e) => setEventType(e.target.value)}
          >
            <option value="">全部类型</option>
            {Object.entries(EVENT_TYPE_LABELS).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
          <Button onClick={doSearch}
            disabled={searching} variant="primary" className="gap-1.5">
            {searching ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
            搜索
          </Button>
        </div>
      </div>

      {searched && (
        <div className="rounded-lg border border-border bg-card">
          <div className="p-3 border-b border-border">
            <span className="text-xs text-muted-foreground">找到 {results.length} 条结果</span>
          </div>
          {results.length === 0 ? (
            <div className="p-8 text-center text-xs text-muted-foreground">无匹配事件</div>
          ) : (
            <div className="divide-y divide-border max-h-[500px] overflow-auto">
              {results.map((evt) => (
                <div key={evt.id} className="p-3 hover:bg-muted/30 transition-colors">
                  <div className="flex items-center gap-2 mb-1">
                    <EventTypeBadge type={evt.event_type} />
                    {evt.agent_id && <span className="text-[10px] text-muted-foreground font-mono">{evt.agent_id}</span>}
                    {evt.status !== 'ok' && <StatusBadge status={evt.status} />}
                    <span className="text-[10px] text-muted-foreground ml-auto">{formatTime(evt.created_at)}</span>
                  </div>
                  <p className="text-xs text-muted-foreground line-clamp-3 break-all">
                    {evt.content.length > 300 ? evt.content.slice(0, 300) + '...' : evt.content}
                  </p>
                  {(evt.token_usage > 0 || evt.duration_ms > 0) && (
                    <div className="flex items-center gap-3 mt-1">
                      {evt.token_usage > 0 && <span className="text-[10px] text-muted-foreground">Token: {formatTokens(evt.token_usage)}</span>}
                      {evt.duration_ms > 0 && <span className="text-[10px] text-muted-foreground">耗时: {formatDuration(evt.duration_ms)}</span>}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Session Events Detail ───────────────────────────────────

function SessionEvents({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [events, setEvents] = useState<TrajectoryEvent[]>([]);
  const [session, setSession] = useState<TrajectorySession | null>(null);
  const [loading, setLoading] = useState(true);
  const [replayStep, setReplayStep] = useState(-1);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const [s, e] = await Promise.all([
          apiFetch(`/api/trajectory/sessions/${encodeURIComponent(sessionId)}`),
          apiFetch(`/api/trajectory/sessions/${encodeURIComponent(sessionId)}/events?limit=500`),
        ]);
        setSession(s as TrajectorySession);
        setEvents((e as any).events || (Array.isArray(s) ? s : (s as any).events) || []);
      } catch { /* ignore */ }
      setLoading(false);
    })();
  }, [sessionId]);

  const doFork = async () => {
    if (replayStep < 0 || !events[replayStep]) return;
    try {
      await apiFetch(`/api/trajectory/sessions/${encodeURIComponent(sessionId)}/fork`, {
        method: 'POST',
        body: JSON.stringify({ fork_point_event_id: events[replayStep].id }),
      });
      alert('分支创建成功');
    } catch (e: any) {
      alert('分支失败: ' + e.message);
    }
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载会话详情...</div>;

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-card border border-border rounded-xl w-full max-w-4xl max-h-[85vh] overflow-hidden flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="p-4 border-b border-border flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3">
            <Lock className="w-4 h-4 text-amber-500" />
            <div>
              <h2 className="text-sm font-semibold font-mono">{sessionId}</h2>
              <div className="flex items-center gap-2 text-[10px] text-muted-foreground mt-0.5">
                <span>{events.length} 事件</span>
                {session?.agent_id && <><span>·</span><span>Agent: {session.agent_id}</span></>}
                {session?.status && <><span>·</span><StatusBadge status={session.status} /></>}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={() => setReplayStep(replayStep < 0 ? 0 : replayStep + 1 >= events.length ? -1 : replayStep + 1)} variant="secondary">
              <Play className="w-3 h-3" />
              {replayStep < 0 ? '回放' : replayStep + 1 >= events.length ? '停止' : `步骤 ${replayStep + 1}/${events.length}`}
            </Button>
            {replayStep >= 0 && events[replayStep] && (
              <button
                onClick={doFork}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-amber-500/10 text-amber-500 border border-amber-500/30 rounded-lg hover:bg-amber-500/20"
              >
                <GitFork className="w-3 h-3" />从此处分支
              </button>
            )}
            <button onClick={onClose} className="p-1.5 hover:bg-muted rounded-md"><X className="w-4 h-4" /></button>
          </div>
        </div>
        <div className="flex-1 overflow-auto p-4">
          {session?.task_description && (
            <div className="rounded-md bg-muted/50 p-3 mb-4 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">任务描述: </span>{session.task_description}
            </div>
          )}
          {events.length === 0 ? (
            <div className="text-center text-xs text-muted-foreground py-8">暂无事件</div>
          ) : (
            <div className="space-y-1">
              {events.map((evt, i) => {
                const isCurrentReplay = replayStep === i;
                return (
                  <div
                    key={evt.id || i}
                    className={`flex items-start gap-2 py-2 px-3 rounded-md transition-colors ${isCurrentReplay ? 'bg-primary/10 ring-1 ring-primary/30' : 'hover:bg-muted/30'}`}
                  >
                    <div className="flex flex-col items-center mt-1 shrink-0">
                      <div className={`w-2 h-2 rounded-full ${isCurrentReplay ? 'bg-primary animate-pulse' : 'bg-muted-foreground/30'}`} />
                      {i < events.length - 1 && <div className="w-px h-full bg-border mt-0.5" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-[10px] text-muted-foreground font-mono w-5 text-right shrink-0">{i + 1}</span>
                        <EventTypeBadge type={evt.event_type} />
                        {evt.agent_id && <span className="text-[10px] text-muted-foreground font-mono">{evt.agent_id}</span>}
                        {evt.token_usage > 0 && <span className="text-[10px] text-primary">{formatTokens(evt.token_usage)}</span>}
                        {evt.duration_ms > 0 && <span className="text-[10px] text-muted-foreground">{formatDuration(evt.duration_ms)}</span>}
                        <span className="text-[10px] text-muted-foreground ml-auto shrink-0">{formatTime(evt.created_at)}</span>
                      </div>
                      {evt.content && (
                        <p className="text-xs text-muted-foreground mt-0.5 break-all whitespace-pre-wrap">{evt.content.length > 500 ? evt.content.slice(0, 500) + '...' : evt.content}</p>
                      )}
                      {evt.metadata && Object.keys(evt.metadata).length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {Object.entries(evt.metadata).slice(0, 6).map(([k, v]) => (
                            <Badge key={k} variant="muted" className="font-mono">
                              {k}: {typeof v === 'string' ? v.slice(0, 40) : JSON.stringify(v).slice(0, 40)}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function TrajectoryPage() {
  const [sessions, setSessions] = useState<TrajectorySession[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [tab, setTab] = useState<Tab>('sessions');
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState('');
  const [detailSession, setDetailSession] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ agent_id: '', task_description: '', tags: '' });
  const pageSize = 20;

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: String(pageSize), offset: String(page * pageSize) });
      if (statusFilter) params.set('status', statusFilter);
      const [s, st] = await Promise.all([
        apiFetch(`/api/trajectory/sessions?${params}`),
        apiFetch('/api/trajectory/stats').catch(() => null),
      ]);
      setSessions(Array.isArray((s as any).sessions) ? (s as any).sessions : []);
      setTotal((s as any).total || 0);
      setStats(st);
    } catch (e: any) {
      setError(e.message);
    }
    setLoading(false);
  }, [page, statusFilter]);

  useEffect(() => { fetchSessions(); }, [fetchSessions]);

  const doCreate = async () => {
    try {
      await apiFetch('/api/trajectory/sessions', {
        method: 'POST',
        body: JSON.stringify({
          agent_id: createForm.agent_id,
          task_description: createForm.task_description,
          tags: createForm.tags.split(',').map(t => t.trim()).filter(Boolean),
        }),
      });
      setShowCreate(false);
      setCreateForm({ agent_id: '', task_description: '', tags: '' });
      fetchSessions();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const doDelete = async (sessionId: string) => {
    if (!confirm('确定删除该会话及其所有事件？')) return;
    try {
      await apiFetch(`/api/trajectory/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
      fetchSessions();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const doEnd = async (sessionId: string) => {
    try {
      await apiFetch(`/api/trajectory/sessions/${encodeURIComponent(sessionId)}/end?status=completed`, { method: 'POST' });
      fetchSessions();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const filtered = sessions.filter(s => {
    if (search) {
      const q = search.toLowerCase();
      return (s.session_id || '').toLowerCase().includes(q) ||
        (s.agent_id || '').toLowerCase().includes(q) ||
        (s.task_description || '').toLowerCase().includes(q);
    }
    return true;
  });

  const totalPages = Math.ceil(total / pageSize);
  const statsData = stats as any;

  const tabs: Array<{ key: Tab; label: string; icon: typeof BarChart3 }> = [
    { key: 'sessions', label: '会话列表', icon: Database },
    { key: 'analytics', label: '分析统计', icon: BarChart3 },
    { key: 'search', label: '事件搜索', icon: Search },
  ];

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{statsData?.total_sessions ?? total}</div>
          <div className="text-xs text-muted-foreground">总会话</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-primary">{statsData?.total_events ?? '—'}</div>
          <div className="text-xs text-muted-foreground">总事件</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-500">{statsData?.running_sessions ?? '—'}</div>
          <div className="text-xs text-muted-foreground">运行中</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-amber-500">{formatTokens(statsData?.total_tokens ?? 0)}</div>
          <div className="text-xs text-muted-foreground">总Token</div>
        </div>
      </div>

      {/* Immutability notice */}
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 flex items-center gap-2">
        <Lock className="w-4 h-4 text-amber-500 shrink-0" />
        <div>
          <span className="text-xs font-medium text-amber-500">Raw层 · 不可变执行轨迹</span>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            轨迹数据只追加（append-only），支持分支（Fork）和回放（Replay），但原始事件不可修改或删除。
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {tabs.map(t => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${
                tab === t.key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />{t.label}
            </button>
          );
        })}
      </div>

      {/* Content */}
      {tab === 'analytics' ? (
        <AnalyticsView />
      ) : tab === 'search' ? (
        <SearchView />
      ) : (
        <>
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <input
                className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                placeholder="搜索会话ID、Agent、任务描述..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <select
              className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
              value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}
            >
              <option value="">全部状态</option>
              <option value="running">运行中</option>
              <option value="completed">已完成</option>
              <option value="failed">失败</option>
            </select>
            <Button onClick={() => setShowCreate(true)} variant="primary" className="gap-1.5">
              <Plus className="w-3.5 h-3.5" />新建会话
            </Button>
            <Button onClick={fetchSessions} variant="secondary">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </Button>
          </div>

          {/* Session list */}
          {loading ? (
            <div className="text-sm text-muted-foreground p-4 text-center">加载中...</div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Database className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">暂无轨迹会话</p>
              <p className="text-[10px] mt-1">Agent执行时会自动创建轨迹会话</p>
            </div>
          ) : (
            <div className="space-y-2">
              {filtered.map((s) => (
                <div key={s.session_id} className="border border-border rounded-lg bg-card hover:bg-muted/20 transition-colors">
                  <button
                    onClick={() => setDetailSession(s.session_id)}
                    className="flex items-center gap-3 w-full p-3 text-left"
                  >
                    <div className="relative">
                      <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center">
                        <Database className="w-5 h-5 text-muted-foreground" />
                      </div>
                      <Lock className="absolute -bottom-0.5 -right-0.5 w-3 h-3 text-amber-500" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium font-mono truncate">{s.session_id}</span>
                        {s.status && <StatusBadge status={s.status} />}
                        <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground flex items-center gap-1">
                          <Lock className="w-2.5 h-2.5" />不可变
                        </span>
                      </div>
                      {s.task_description && (
                        <p className="text-xs text-muted-foreground truncate mt-0.5">{s.task_description}</p>
                      )}
                      <div className="flex items-center gap-3 mt-0.5 text-[10px] text-muted-foreground">
                        {s.agent_id && (
                          <span className="flex items-center gap-1"><Bot className="w-2.5 h-2.5" />{s.agent_id}</span>
                        )}
                        <span className="flex items-center gap-1"><FileText className="w-2.5 h-2.5" />{s.event_count} 事件</span>
                        <span className="flex items-center gap-1"><Clock className="w-2.5 h-2.5" />{formatRelative(s.updated_at || s.created_at)}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      {s.status === 'running' && (
                        <button
                          onClick={(e) => { e.stopPropagation(); doEnd(s.session_id); }}
                          className="p-1.5 hover:bg-muted rounded-md" title="结束会话"
                        >
                          <StopCircle className="w-3.5 h-3.5 text-amber-500" />
                        </button>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); doDelete(s.session_id); }}
                        className="p-1.5 hover:bg-red-500/10 rounded-md" title="删除"
                      >
                        <Trash2 className="w-3.5 h-3.5 text-red-500" />
                      </button>
                      <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />
                    </div>
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">
                第 {page + 1} / {totalPages} 页，共 {total} 条
              </span>
              <div className="flex items-center gap-1">
                <Button onClick={() => setPage(Math.max(0, page - 1))}
                  disabled={page === 0} variant="secondary" className="disabled:opacity-30">上一页</Button>
                <Button onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                  disabled={page >= totalPages - 1} variant="secondary" className="disabled:opacity-30">下一页</Button>
              </div>
            </div>
          )}
        </>
      )}

      {/* Create session modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={() => setShowCreate(false)}>
          <div className="bg-card border border-border rounded-xl max-w-md w-full p-6" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-semibold mb-4">新建轨迹会话</h2>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">Agent ID</label>
                <input
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary"
                  value={createForm.agent_id}
                  onChange={(e) => setCreateForm(prev => ({ ...prev, agent_id: e.target.value }))}
                  placeholder="hermes-agent"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">任务描述</label>
                <textarea
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary resize-none"
                  rows={3}
                  value={createForm.task_description}
                  onChange={(e) => setCreateForm(prev => ({ ...prev, task_description: e.target.value }))}
                  placeholder="描述这个轨迹会话的任务..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">标签（逗号分隔）</label>
                <input
                  className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary"
                  value={createForm.tags}
                  onChange={(e) => setCreateForm(prev => ({ ...prev, tags: e.target.value }))}
                  placeholder="knowledge, feedback, analysis"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <Button onClick={() => setShowCreate(false)} variant="secondary" className="px-4 py-2">取消</Button>
              <Button onClick={doCreate} variant="primary">创建</Button>
            </div>
          </div>
        </div>
      )}

      {/* Detail modal */}
      {detailSession && (
        <SessionEvents sessionId={detailSession} onClose={() => setDetailSession(null)} />
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500 shrink-0" />
          <span className="text-xs text-red-500">{error}</span>
          <button onClick={() => setError('')} className="ml-auto p-1 hover:bg-muted rounded-md">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}
    </div>
  );
}
