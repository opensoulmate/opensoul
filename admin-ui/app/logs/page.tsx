'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, ScrollText, RefreshCw, X, Filter, Download,
  Loader2, AlertCircle, Info, AlertTriangle, CheckCircle,
  ChevronDown, ChevronUp, Terminal, Wifi, WifiOff, Radio,
  Zap, Eye, BarChart3,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface LogEntry {
  id: string;
  type: string;
  organ: string;
  emoji: string;
  summary: string;
  timestamp: number;
  metadata?: Record<string, any>;
}

interface EventSummary {
  total_events: number;
  by_type: Record<string, number>;
  by_organ: Record<string, number>;
  recent_count: number;
}

interface StreamSummary {
  buffer_size: number;
  sse_clients: number;
  organ_probes: number;
  last_refresh: string;
}

export default function LogsPage() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [summary, setSummary] = useState<EventSummary | null>(null);
  const [streamSummary, setStreamSummary] = useState<StreamSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [organFilter, setOrganFilter] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [sseConnected, setSseConnected] = useState(false);
  const [sseEvents, setSseEvents] = useState<LogEntry[]>([]);
  const eventSourceRef = useRef<EventSource | null>(null);
  const [useSse, setUseSse] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const fetchLogs = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/events/stream?limit=200');
      setLogs(Array.isArray(data.events) ? data.events : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchSummary = useCallback(async () => {
    try {
      const data = await apiFetch('/api/events/summary');
      setSummary(data);
    } catch {}
  }, []);

  const fetchStreamSummary = useCallback(async () => {
    try {
      const data = await apiFetch('/api/events/stream/summary');
      setStreamSummary(data);
    } catch {}
  }, []);

  useEffect(() => { fetchLogs(); fetchSummary(); fetchStreamSummary(); }, [fetchLogs, fetchSummary, fetchStreamSummary]);

  // Polling auto-refresh
  useEffect(() => {
    if (!autoRefresh || useSse) return;
    const iv = setInterval(() => { fetchLogs(); fetchSummary(); }, 5000);
    return () => clearInterval(iv);
  }, [autoRefresh, useSse, fetchLogs, fetchSummary]);

  // SSE connection
  useEffect(() => {
    if (!useSse) {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
        setSseConnected(false);
      }
      return;
    }

    const token = localStorage.getItem('opensoul-token') || '';
    const es = new EventSource(`/api/events/sse?token=${encodeURIComponent(token)}`);
    eventSourceRef.current = es;

    es.onopen = () => setSseConnected(true);
    es.onerror = () => {
      setSseConnected(false);
      // Auto-reconnect handled by EventSource
    };
    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        const entry: LogEntry = {
          id: event.id || `sse_${Date.now()}`,
          type: event.type || 'event',
          organ: event.organ || 'system',
          emoji: event.emoji || '📡',
          summary: event.summary || '',
          timestamp: event.timestamp || Date.now() / 1000,
          metadata: event.metadata,
        };
        setSseEvents((prev) => [entry, ...prev].slice(0, 500));
      } catch {}
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
      setSseConnected(false);
    };
  }, [useSse]);

  const handleRefreshStream = async () => {
    setRefreshing(true);
    try {
      await apiFetch('/api/events/stream/refresh', { method: 'POST' });
      await fetchLogs();
      await fetchSummary();
      await fetchStreamSummary();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRefreshing(false);
    }
  };

  const displayLogs = useSse ? sseEvents : logs;
  const types = Array.from(new Set(displayLogs.map((l) => l.type))).sort();
  const organs = Array.from(new Set(displayLogs.map((l) => l.organ))).sort();

  const filtered = displayLogs.filter((l) => {
    if (typeFilter && l.type !== typeFilter) return false;
    if (organFilter && l.organ !== organFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return l.summary.toLowerCase().includes(q) || l.organ.toLowerCase().includes(q) || l.type.toLowerCase().includes(q);
    }
    return true;
  });

  const formatTime = (ts: number) => {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const formatRelative = (ts: number) => {
    if (!ts) return '';
    const diff = Date.now() / 1000 - ts;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
    return `${Math.floor(diff / 86400)}天前`;
  };

  const getTypeColor = (type: string) => {
    if (['error', 'alert', 'ip_blacklisted', 'content_blocked'].includes(type)) return 'text-red-500';
    if (['warning', 'rate_limited'].includes(type)) return 'text-orange-500';
    if (['backup_created', 'file_uploaded', 'component_registered'].includes(type)) return 'text-green-500';
    return 'text-blue-500';
  };

  const getTypeBg = (type: string) => {
    if (['error', 'alert', 'ip_blacklisted', 'content_blocked'].includes(type)) return 'bg-red-500/10';
    if (['warning', 'rate_limited'].includes(type)) return 'bg-orange-500/10';
    if (['backup_created', 'file_uploaded', 'component_registered'].includes(type)) return 'bg-green-500/10';
    return 'bg-blue-500/10';
  };

  const handleExport = () => {
    const csv = filtered.map((l) => `"${formatTime(l.timestamp)}","${l.type}","${l.organ}","${l.summary}"`).join('\n');
    const header = '时间,类型,器官,摘要\n';
    const blob = new Blob([header + csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `opensoul-logs-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      {/* Summary stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <ScrollText className="w-4 h-4 text-primary" />
            <span className="text-xs text-muted-foreground">总事件</span>
          </div>
          <div className="text-2xl font-bold mt-1">{summary?.total_events || displayLogs.length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-blue-500" />
            <span className="text-xs text-muted-foreground">最近事件</span>
          </div>
          <div className="text-2xl font-bold text-blue-500 mt-1">{summary?.recent_count || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-500" />
            <span className="text-xs text-muted-foreground">错误/告警</span>
          </div>
          <div className="text-2xl font-bold text-red-500 mt-1">{(summary?.by_type?.error || 0) + (summary?.by_type?.alert || 0)}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Eye className="w-4 h-4 text-purple-500" />
            <span className="text-xs text-muted-foreground">活跃器官</span>
          </div>
          <div className="text-2xl font-bold mt-1">{Object.keys(summary?.by_organ || {}).length}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Radio className="w-4 h-4 text-green-500" />
            <span className="text-xs text-muted-foreground">SSE客户端</span>
          </div>
          <div className="text-2xl font-bold mt-1">{streamSummary?.sse_clients || 0}</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索日志内容、器官、类型..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="">全部类型</option>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={organFilter}
          onChange={(e) => setOrganFilter(e.target.value)}
        >
          <option value="">全部器官</option>
          {organs.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <Button
          onClick={() => setUseSse(!useSse)}
          variant="chip-sm" className={`flex items-center gap-1.5 border transition-colors  ${useSse ? 'bg-emerald-500/10 text-emerald-500 border-green-500/20' : 'bg-muted border-border hover:bg-muted'}`}
        >
          {useSse ? (
            sseConnected ? <Wifi className="w-3.5 h-3.5" /> : <WifiOff className="w-3.5 h-3.5" />
          ) : (
            <Radio className="w-3.5 h-3.5" />
          )}
          {useSse ? (sseConnected ? 'SSE实时' : 'SSE连接中...') : 'SSE模式'}
        </Button>
        {!useSse && (
          <label className="flex items-center gap-1.5 text-xs cursor-pointer">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} className="rounded" />
            轮询(5s)
          </label>
        )}
        <Button onClick={handleRefreshStream} disabled={refreshing} variant="secondary">
          {refreshing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}刷新流
        </Button>
        <Button onClick={handleExport} variant="secondary">
          <Download className="w-3.5 h-3.5" />导出CSV
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

      {/* Log entries */}
      <div ref={containerRef} className="rounded-lg border border-border bg-card overflow-hidden">
        <div className="max-h-[600px] overflow-auto">
          {filtered.map((log) => (
            <div
              key={log.id}
              className={`border-b border-border hover:bg-muted/30 cursor-pointer transition-colors ${expandedId === log.id ? 'bg-muted/20' : ''}`}
              onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
            >
              <div className="flex items-start gap-2 px-3 py-2">
                <span className="text-lg shrink-0">{log.emoji || '📋'}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Badge variant="bare" className={`font-mono ${getTypeBg(log.type)} ${getTypeColor(log.type)}`}>{log.type}</Badge>
                    <Badge variant="muted">{log.organ}</Badge>
                    <span className="text-[10px] text-muted-foreground" title={formatTime(log.timestamp)}>{formatRelative(log.timestamp)}</span>
                  </div>
                  <p className="text-xs mt-0.5 break-words">{log.summary}</p>
                </div>
                {expandedId === log.id ? <ChevronUp className="w-3 h-3 text-muted-foreground shrink-0" /> : <ChevronDown className="w-3 h-3 text-muted-foreground shrink-0" />}
              </div>
              {expandedId === log.id && log.metadata && Object.keys(log.metadata).length > 0 && (
                <div className="px-3 pb-2 ml-7">
                  <pre className="text-[10px] bg-muted p-2 rounded font-mono whitespace-pre-wrap overflow-x-auto">
                    {JSON.stringify(log.metadata, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          ))}
          {filtered.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <Terminal className="w-10 h-10 mb-2 opacity-30" />
              <p className="text-xs">{search || typeFilter || organFilter ? '没有匹配的日志' : '暂无日志'}</p>
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="text-[10px] text-muted-foreground text-center">
        显示 {filtered.length}/{displayLogs.length} 条日志
        {useSse && <span> | SSE实时模式</span>}
        {streamSummary && <span> | 缓冲: {streamSummary.buffer_size} | 探针: {streamSummary.organ_probes}</span>}
      </div>
    </div>
  );
}
