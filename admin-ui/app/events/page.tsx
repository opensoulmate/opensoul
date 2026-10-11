'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Activity, RefreshCw, X, AlertCircle, Radio, Pause, Play,
  ChevronDown, ChevronRight, Filter, Zap, Wifi, WifiOff, Clock
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface Event {
  id: string;
  organ: string;
  emoji: string;
  type: string;
  summary: string;
  detail: Record<string, any>;
  collected_at: number;
  timestamp?: number | string;
}

interface Summary {
  total_events: number;
  by_organ: Record<string, number>;
  by_type: Record<string, number>;
  most_active_organ: string | null;
  collected_at: number;
}

interface Health {
  status: string;
  component: string;
  buffer_size: number;
  probes_configured: number;
  sse_subscribers: number;
}

const ORGAN_COLORS: Record<string, string> = {
  vein: 'text-red-400 bg-red-500/10',
  gland: 'text-purple-400 bg-purple-500/10',
  immune: 'text-green-400 bg-green-500/10',
  trajectory: 'text-blue-400 bg-blue-500/10',
  echo: 'text-cyan-400 bg-cyan-500/10',
  mirror: 'text-pink-400 bg-pink-500/10',
  link: 'text-indigo-400 bg-indigo-500/10',
  limb: 'text-orange-400 bg-orange-500/10',
  cron: 'text-amber-400 bg-amber-500/10',
  hippo: 'text-teal-400 bg-teal-500/10',
  reflex: 'text-lime-400 bg-lime-500/10',
  nerve: 'text-violet-400 bg-violet-500/10',
  cortex: 'text-emerald-400 bg-emerald-500/10',
  sense: 'text-sky-400 bg-sky-500/10',
  vital: 'text-rose-400 bg-rose-500/10',
  pulse: 'text-fuchsia-400 bg-fuchsia-500/10',
  voice: 'text-yellow-400 bg-yellow-500/10',
  vision: 'text-blue-300 bg-blue-400/10',
  mind: 'text-purple-300 bg-purple-400/10',
  nest: 'text-green-300 bg-green-400/10',
  marrow: 'text-red-300 bg-red-400/10',
  heredity: 'text-amber-300 bg-amber-400/10',
  gene: 'text-teal-300 bg-teal-400/10',
  learn: 'text-orange-300 bg-orange-400/10',
  mcp: 'text-cyan-300 bg-cyan-400/10',
  plugins: 'text-pink-300 bg-pink-400/10',
};

export default function EventsPage() {
  const [events, setEvents] = useState<Event[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [organFilter, setOrganFilter] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [liveMode, setLiveMode] = useState(false);
  const [sseConnected, setSseConnected] = useState(false);
  const [liveEvents, setLiveEvents] = useState<Event[]>([]);
  const eventSourceRef = useRef<EventSource | null>(null);

  // ── Fetch events ──
  const fetchEvents = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams({ limit: '100' });
      if (organFilter) params.set('organ', organFilter);
      const data = await apiFetch(`/api/events/stream?${params}`);
      setEvents(Array.isArray(data.events) ? data.events : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [organFilter]);

  const fetchSummary = useCallback(async () => {
    try {
      const data = await apiFetch('/api/events/stream/summary');
      setSummary(data);
    } catch { /* silent */ }
  }, []);

  const fetchHealth = useCallback(async () => {
    try {
      const data = await apiFetch('/api/events/health');
      setHealth(data);
    } catch { /* silent */ }
  }, []);

  const handleRefresh = useCallback(async () => {
    try {
      await apiFetch('/api/events/stream/refresh', { method: 'POST' });
      await Promise.all([fetchEvents(), fetchSummary(), fetchHealth()]);
    } catch (e: any) {
      setError(e.message);
    }
  }, [fetchEvents, fetchSummary, fetchHealth]);

  useEffect(() => {
    Promise.all([fetchEvents(), fetchSummary(), fetchHealth()]);
  }, [fetchEvents, fetchSummary, fetchHealth]);

  // ── SSE live mode ──
  const toggleLive = useCallback(() => {
    if (liveMode) {
      // Disconnect SSE
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      setSseConnected(false);
      setLiveMode(false);
      setLiveEvents([]);
    } else {
      // Connect SSE
      setLiveMode(true);
      const url = organFilter ? `/api/events/sse?organ=${organFilter}` : '/api/events/sse';
      const es = new EventSource(url);
      eventSourceRef.current = es;

      es.addEventListener('connected', () => {
        setSseConnected(true);
      });

      es.addEventListener('message', (e) => {
        try {
          const event = JSON.parse(e.data);
          setLiveEvents((prev) => [event, ...prev].slice(0, 200));
        } catch { /* parse error */ }
      });

      es.onerror = () => {
        setSseConnected(false);
      };
    }
  }, [liveMode, organFilter]);

  // Cleanup SSE on unmount
  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  // ── Filtering ──
  const displayEvents = liveMode ? liveEvents : events;
  const filtered = displayEvents.filter((ev) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      ev.summary?.toLowerCase().includes(q) ||
      ev.organ?.toLowerCase().includes(q) ||
      ev.type?.toLowerCase().includes(q) ||
      ev.emoji?.includes(q)
    );
  });

  // ── Organ list from summary ──
  const organs = summary ? Object.entries(summary.by_organ).sort((a, b) => b[1] - a[1]) : [];

  const formatTime = (ts: number | string | undefined) => {
    if (!ts) return '';
    const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const getOrganColor = (organ: string) => ORGAN_COLORS[organ] || 'text-gray-400 bg-gray-500/10';

  if (loading && events.length === 0) {
    return <div className="text-sm text-muted-foreground p-4">加载中...</div>;
  }

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{summary?.total_events || events.length}</div>
          <div className="text-xs text-muted-foreground">总事件数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{organs.length}</div>
          <div className="text-xs text-muted-foreground">活跃器官</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-emerald-500">{health?.probes_configured || 0}</div>
          <div className="text-xs text-muted-foreground">探测器</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <div className="text-2xl font-bold text-violet-500">{health?.sse_subscribers || 0}</div>
            {sseConnected && <Wifi className="w-4 h-4 text-emerald-500 animate-pulse" />}
          </div>
          <div className="text-xs text-muted-foreground">SSE连接</div>
        </div>
      </div>

      {/* Organ badges */}
      {organs.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          <Button variant="bare"
            onClick={() => setOrganFilter('')}
            className={`px-2.5 py-1 text-[11px] rounded-full border transition-colors ${
              !organFilter ? 'bg-primary/10 text-primary border-primary/30' : 'border-border text-muted-foreground hover:text-foreground hover:bg-muted/50'
            }`}
          >
            全部
          </Button>
          {organs.map(([organ, count]) => (
            <Button variant="bare"
              key={organ}
              onClick={() => setOrganFilter(organFilter === organ ? '' : organ)}
              className={`px-2.5 py-1 text-[11px] rounded-full border transition-colors ${
                organFilter === organ ? 'bg-primary/10 text-primary border-primary/30' : 'border-border text-muted-foreground hover:text-foreground hover:bg-muted/50'
              }`}
            >
              {organ} <span className="ml-0.5 opacity-60">{count}</span>
            </Button>
          ))}
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索事件（器官、类型、内容）..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button
          onClick={toggleLive}
          variant="chip-sm" className={`flex items-center gap-1.5 border transition-colors  ${
            liveMode
              ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30 hover:bg-emerald-500/20'
              : 'bg-muted border-border text-muted-foreground hover:text-foreground hover:bg-muted'
          }`}
        >
          {liveMode ? <Pause className="w-3.5 h-3.5" /> : <Radio className="w-3.5 h-3.5" />}
          {liveMode ? '停止实时' : '实时模式'}
        </Button>
        <Button onClick={handleRefresh} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Events list */}
      <div className="space-y-1">
        {filtered.map((ev) => (
          <div
            key={ev.id}
            className="rounded-lg border border-border bg-card hover:bg-muted/30 transition-colors"
          >
            <div
              className="flex items-start gap-3 p-3 cursor-pointer"
              onClick={() => setExpandedId(expandedId === ev.id ? null : ev.id)}
            >
              <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 text-base ${getOrganColor(ev.organ)}`}>
                {ev.emoji || '📡'}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <Badge className={`${getOrganColor(ev.organ)}`}>
                    {ev.organ}
                  </Badge>
                  <span className="text-[10px] text-muted-foreground">{ev.type}</span>
                  {liveMode && (
                    <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
                  )}
                </div>
                <p className="text-xs text-foreground mt-0.5 line-clamp-2">{ev.summary}</p>
                <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground">
                  <Clock className="w-3 h-3" />
                  <span>{formatTime(ev.timestamp || ev.collected_at)}</span>
                  <span className="opacity-50">#{ev.id?.slice(-8)}</span>
                </div>
              </div>
              <div className="shrink-0 mt-1">
                {expandedId === ev.id ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
              </div>
            </div>
            {expandedId === ev.id && ev.detail && (
              <div className="border-t border-border px-3 py-2 bg-muted/20">
                <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap break-all font-mono">
                  {JSON.stringify(ev.detail, null, 2)}
                </pre>
              </div>
            )}
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <Activity className="w-10 h-10 mb-2 opacity-30" />
            <p className="text-xs">{search ? '没有匹配的事件' : '暂无事件'}</p>
          </div>
        )}
      </div>

      {/* Footer */}
      {summary && (
        <div className="text-[10px] text-muted-foreground text-right">
          最活跃器官: {summary.most_active_organ || '无'} · 收集于 {formatTime(summary.collected_at)}
        </div>
      )}
    </div>
  );
}
