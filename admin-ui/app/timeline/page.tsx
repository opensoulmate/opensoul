'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Clock, Trash2, RefreshCw, X, AlertCircle, Filter,
  ChevronLeft, ChevronRight, Plus, Loader2, Calendar, BarChart3, Layers
} from 'lucide-react';

interface TimelineEvent {
  id: string;
  organ: string;
  emoji: string;
  type: string;
  summary: string;
  detail: Record<string, any>;
  timestamp: number;
  collected_at: number;
}

interface TimelineStats {
  total_events: number;
  recent_24h: number;
  by_organ: Record<string, number>;
  by_type: Record<string, number>;
}

interface OrganInfo {
  organ: string;
  count: number;
}

interface TypeInfo {
  type: string;
  count: number;
}

function formatTime(ts: number): string {
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch { return '-'; }
}

function relativeTime(ts: number): string {
  const now = Date.now() / 1000;
  const diff = now - ts;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
  return `${Math.floor(diff / 86400)}天前`;
}

export default function TimelinePage() {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [stats, setStats] = useState<TimelineStats | null>(null);
  const [organs, setOrgans] = useState<OrganInfo[]>([]);
  const [types, setTypes] = useState<TypeInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [organFilter, setOrganFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [offset, setOffset] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const limit = 50;

  // Record dialog
  const [showRecord, setShowRecord] = useState(false);
  const [recordForm, setRecordForm] = useState({ organ: '', emoji: '📌', event_type: 'info', summary: '', detail: '{}' });
  const [recording, setRecording] = useState(false);

  // Detail dialog
  const [selectedEvent, setSelectedEvent] = useState<TimelineEvent | null>(null);
  const [showDetail, setShowDetail] = useState(false);

  // Clear dialog
  const [showClear, setShowClear] = useState(false);
  const [clearDays, setClearDays] = useState(30);

  const fetchEvents = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (organFilter) params.set('organ', organFilter);
      if (typeFilter) params.set('event_type', typeFilter);
      if (search) params.set('search', search);
      const data = await apiFetch(`/api/timeline/events?${params}`);
      setEvents(Array.isArray(data.events) ? data.events : []);
      setTotalCount(data.count || 0);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [offset, organFilter, typeFilter, search]);

  const fetchMeta = useCallback(async () => {
    try {
      const [s, o, t] = await Promise.all([
        apiFetch('/api/timeline/stats').catch(() => null),
        apiFetch('/api/timeline/organs').catch(() => ({ organs: [] })),
        apiFetch('/api/timeline/types').catch(() => ({ types: [] })),
      ]);
      if (s) setStats(s);
      setOrgans(Array.isArray(o.organs) ? o.organs : []);
      setTypes(Array.isArray(t.types) ? t.types : []);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchEvents(); }, [fetchEvents]);
  useEffect(() => { fetchMeta(); }, [fetchMeta]);

  // Reset offset when filters change
  useEffect(() => { setOffset(0); }, [organFilter, typeFilter, search]);

  const handleRecord = async () => {
    if (!recordForm.summary.trim() || !recordForm.organ.trim()) return;
    try {
      setRecording(true);
      let detail = {};
      try { detail = JSON.parse(recordForm.detail); } catch { /* empty */ }
      await apiFetch('/api/timeline/record', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          organ: recordForm.organ.trim(),
          emoji: recordForm.emoji,
          event_type: recordForm.event_type,
          summary: recordForm.summary.trim(),
          detail,
        }),
      });
      setShowRecord(false);
      setRecordForm({ organ: '', emoji: '📌', event_type: 'info', summary: '', detail: '{}' });
      fetchEvents();
      fetchMeta();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRecording(false);
    }
  };

  const handleDeleteEvent = async (eventId: string) => {
    try {
      await apiFetch(`/api/timeline/events/${eventId}`, { method: 'DELETE' });
      setEvents((prev) => prev.filter((e) => e.id !== eventId));
    } catch (e: any) { setError(e.message); }
  };

  const handleClear = async () => {
    try {
      await apiFetch('/api/timeline/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ older_than_days: clearDays }),
      });
      setShowClear(false);
      fetchEvents();
      fetchMeta();
    } catch (e: any) { setError(e.message); }
  };

  const handleSync = async () => {
    try {
      const data = await apiFetch('/api/timeline/sync', { method: 'POST' });
      if (data.synced > 0) { fetchEvents(); fetchMeta(); }
    } catch (e: any) { setError(e.message); }
  };

  const hasMore = offset + limit < totalCount;

  return (
    <div className="space-y-4">
      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-500">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')} className="p-0.5 hover:bg-red-500/20 rounded-md"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.total_events ?? '-'}</div>
          <div className="text-xs text-muted-foreground">总事件数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{stats?.recent_24h ?? '-'}</div>
          <div className="text-xs text-muted-foreground">近24小时</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-500">{organs.length}</div>
          <div className="text-xs text-muted-foreground">事件来源</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-orange-500">{types.length}</div>
          <div className="text-xs text-muted-foreground">事件类型</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索事件摘要..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={organFilter}
          onChange={(e) => setOrganFilter(e.target.value)}
        >
          <option value="">全部来源</option>
          {organs.map((o) => <option key={o.organ} value={o.organ}>{o.organ} ({o.count})</option>)}
        </select>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="">全部类型</option>
          {types.map((t) => <option key={t.type} value={t.type}>{t.type} ({t.count})</option>)}
        </select>
        <button onClick={() => { fetchEvents(); fetchMeta(); }} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </button>
        <button onClick={handleSync} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors" title="从事件流同步">
          <Layers className="w-3.5 h-3.5" />同步
        </button>
        <button onClick={() => setShowRecord(true)} className="flex items-center gap-1.5 px-2 lg:px-2 lg:px-4 py-2 text-xs lg:text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 font-medium transition-colors">
          <Plus className="w-3.5 h-3.5" />记录事件
        </button>
        <button onClick={() => setShowClear(true)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm text-red-500 bg-muted border border-border rounded-lg hover:bg-red-500/10">
          <Trash2 className="w-3.5 h-3.5" />清理
        </button>
      </div>

      {/* Event List */}
      {loading ? (
        <div className="text-sm text-muted-foreground p-4">加载中...</div>
      ) : events.length === 0 ? (
        <div className="text-center py-12 text-sm text-muted-foreground">
          <Clock className="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p>暂无时间线事件</p>
          <p className="text-xs mt-1">点击「记录事件」添加手动事件，或「同步」从事件流导入</p>
        </div>
      ) : (
        <div className="space-y-0">
          {events.map((event, i) => (
            <div
              key={event.id}
              className="flex items-start gap-3 px-4 py-3 hover:bg-muted/20 cursor-pointer transition-colors border-b border-border last:border-b-0 group"
              onClick={() => { setSelectedEvent(event); setShowDetail(true); }}
            >
              {/* Timeline dot + line */}
              <div className="flex flex-col items-center pt-1">
                <div className="w-2.5 h-2.5 rounded-full bg-primary/60 shrink-0" />
                {i < events.length - 1 && <div className="w-px flex-1 bg-border mt-1" />}
              </div>

              {/* Content */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-base">{event.emoji || '📌'}</span>
                  <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{event.organ}</span>
                  <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-primary/10 text-primary">{event.type}</span>
                  <span className="text-[10px] text-muted-foreground ml-auto">{relativeTime(event.timestamp)}</span>
                </div>
                <p className="text-sm">{event.summary}</p>
                {event.detail && Object.keys(event.detail).length > 0 && (
                  <div className="text-[10px] text-muted-foreground mt-1 truncate">
                    {Object.entries(event.detail).slice(0, 3).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ')}
                  </div>
                )}
              </div>

              {/* Delete button */}
              <button
                onClick={(e) => { e.stopPropagation(); handleDeleteEvent(event.id); }}
                className="p-1 opacity-0 group-hover:opacity-100 hover:bg-red-500/10 rounded-md text-red-500 transition-opacity"
                title="删除"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalCount > limit && (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>显示 {offset + 1}-{Math.min(offset + limit, totalCount)} / {totalCount}</span>
          <div className="flex items-center gap-1">
            <button onClick={() => setOffset(Math.max(0, offset - limit))} disabled={offset === 0} className="p-1.5 hover:bg-muted rounded-md disabled:opacity-30">
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button onClick={() => setOffset(offset + limit)} disabled={!hasMore} className="p-1.5 hover:bg-muted rounded-md disabled:opacity-30">
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Record Dialog */}
      {showRecord && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowRecord(false)}>
          <div className="bg-card border border-border rounded-xl shadow-xl w-full max-w-lg mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">记录事件</h2>
              <button onClick={() => setShowRecord(false)} className="p-1 hover:bg-muted rounded-md"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-3">
              <div className="grid grid-cols-[60px_1fr] gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">图标</label>
                  <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md text-center focus:outline-none focus:ring-1 focus:ring-primary/50" value={recordForm.emoji} onChange={(e) => setRecordForm((f) => ({ ...f, emoji: e.target.value }))} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">来源 *</label>
                  <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={recordForm.organ} onChange={(e) => setRecordForm((f) => ({ ...f, organ: e.target.value }))} placeholder="如 nerve, cortex" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">事件类型</label>
                  <select className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none" value={recordForm.event_type} onChange={(e) => setRecordForm((f) => ({ ...f, event_type: e.target.value }))}>
                    <option value="info">信息</option>
                    <option value="warning">警告</option>
                    <option value="error">错误</option>
                    <option value="success">成功</option>
                    <option value="action">操作</option>
                    <option value="system">系统</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">摘要 *</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={recordForm.summary} onChange={(e) => setRecordForm((f) => ({ ...f, summary: e.target.value }))} placeholder="事件摘要" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">详细信息 (JSON)</label>
                <textarea className="w-full px-3 py-1.5 text-xs font-mono bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-none" rows={3} value={recordForm.detail} onChange={(e) => setRecordForm((f) => ({ ...f, detail: e.target.value }))} />
              </div>
            </div>
            <div className="flex justify-end gap-2 p-4 border-t border-border">
              <button onClick={() => setShowRecord(false)} className="px-4 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">取消</button>
              <button onClick={handleRecord} disabled={recording || !recordForm.summary.trim() || !recordForm.organ.trim()} className="flex items-center gap-1.5 px-4 py-1.5 text-xs lg:text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 font-medium transition-colors">
                {recording && <Loader2 className="w-3 h-3 animate-spin" />}记录
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Detail Dialog */}
      {showDetail && selectedEvent && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDetail(false)}>
          <div className="bg-card border border-border rounded-xl shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">事件详情</h2>
              <button onClick={() => setShowDetail(false)} className="p-1 hover:bg-muted rounded-md"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-3">
              <div className="flex items-center gap-3">
                <span className="text-2xl">{selectedEvent.emoji || '📌'}</span>
                <div>
                  <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{selectedEvent.organ}</span>
                  <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-primary/10 text-primary ml-2">{selectedEvent.type}</span>
                </div>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">摘要</span>
                <div className="text-sm mt-1">{selectedEvent.summary}</div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div><span className="text-xs text-muted-foreground">时间</span><div className="text-sm">{formatTime(selectedEvent.timestamp)}</div></div>
                <div><span className="text-xs text-muted-foreground">相对时间</span><div className="text-sm">{relativeTime(selectedEvent.timestamp)}</div></div>
                <div><span className="text-xs text-muted-foreground">收集时间</span><div className="text-sm">{formatTime(selectedEvent.collected_at)}</div></div>
                <div><span className="text-xs text-muted-foreground">ID</span><div className="text-xs font-mono text-muted-foreground">{selectedEvent.id}</div></div>
              </div>
              {selectedEvent.detail && Object.keys(selectedEvent.detail).length > 0 && (
                <div>
                  <span className="text-xs text-muted-foreground">详细信息</span>
                  <pre className="text-xs font-mono mt-1 p-2 bg-muted rounded-md overflow-x-auto">{JSON.stringify(selectedEvent.detail, null, 2)}</pre>
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2 p-4 border-t border-border">
              <button onClick={() => { handleDeleteEvent(selectedEvent.id); setShowDetail(false); }} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm text-red-500 bg-muted border border-border rounded-lg hover:bg-red-500/10">
                <Trash2 className="w-3 h-3" />删除
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Clear Dialog */}
      {showClear && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowClear(false)}>
          <div className="bg-card border border-border rounded-xl shadow-xl w-full max-w-sm mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">清理事件</h2>
              <button onClick={() => setShowClear(false)} className="p-1 hover:bg-muted rounded-md"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 space-y-3">
              <p className="text-sm text-muted-foreground">清理超过指定天数的旧事件。</p>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">保留天数</label>
                <input type="number" min={1} className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={clearDays} onChange={(e) => setClearDays(Math.max(1, +e.target.value || 30))} />
                <p className="text-[10px] text-muted-foreground mt-1">将删除 {clearDays} 天前的所有事件</p>
              </div>
            </div>
            <div className="flex justify-end gap-2 p-4 border-t border-border">
              <button onClick={() => setShowClear(false)} className="px-4 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">取消</button>
              <button onClick={handleClear} className="flex items-center gap-1.5 px-4 py-1.5 text-xs lg:text-sm bg-red-500 text-white rounded-lg hover:bg-red-600">
                <Trash2 className="w-3 h-3" />确认清理
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
