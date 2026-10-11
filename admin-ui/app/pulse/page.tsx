'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Plus, Trash2, RefreshCw, X, Timer, Play, Pause, RotateCcw,
  Clock, BarChart3, Zap, Activity, CheckCircle, AlertCircle
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface PulseSignal {
  signal_id: string;
  name: string;
  signal_type: string;
  interval_ms: number;
  status: string;
  fire_count: number;
  max_fires: number;
  last_fired_at: string | null;
  next_fire_at: string | null;
  drift_correction: number;
  created_at: string;
}

interface TickRecord {
  tick_id: string;
  signal_id: string;
  timestamp: string;
  drift_ms: number;
  latency_ms?: number;
}

export default function PulsePage() {
  const [signals, setSignals] = useState<PulseSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [stats, setStats] = useState<any>(null);
  const [ticks, setTicks] = useState<TickRecord[]>([]);
  const [showTicks, setShowTicks] = useState(false);

  // Create dialog
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({
    name: '', signal_type: 'interval', interval_ms: '1000',
    callback_url: '', max_fires: '0',
  });
  const [saving, setSaving] = useState(false);

  // Detail
  const [selected, setSelected] = useState<PulseSignal | null>(null);
  const [showDetail, setShowDetail] = useState(false);

  const fetchSignals = useCallback(async () => {
    try {
      setLoading(true);
      const params = filterStatus ? `?status=${filterStatus}` : '';
      const data = await apiFetch(`/api/pulse/signals${params}`);
      setSignals(Array.isArray(data.signals) ? data.signals : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [filterStatus]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/pulse/stats');
      setStats(data);
    } catch { /* silent */ }
  }, []);

  const fetchTicks = useCallback(async () => {
    try {
      const data = await apiFetch('/api/pulse/ticks?limit=50');
      setTicks(Array.isArray(data.ticks) ? data.ticks : []);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchSignals(); fetchStats(); }, [fetchSignals, fetchStats]);

  const handleCreate = async () => {
    try {
      setSaving(true);
      await apiFetch('/api/pulse/signals', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.name,
          signal_type: form.signal_type,
          interval_ms: parseFloat(form.interval_ms) || 1000,
          callback_url: form.callback_url,
          max_fires: parseInt(form.max_fires) || 0,
        }),
      });
      setShowCreate(false);
      setForm({ name: '', signal_type: 'interval', interval_ms: '1000', callback_url: '', max_fires: '0' });
      await fetchSignals();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('确定删除此信号？')) return;
    try {
      await apiFetch(`/api/pulse/signals/${id}`, { method: 'DELETE' });
      await fetchSignals();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handlePause = async (id: string) => {
    try {
      await apiFetch(`/api/pulse/signals/${id}/pause`, { method: 'POST' });
      await fetchSignals();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleResume = async (id: string) => {
    try {
      await apiFetch(`/api/pulse/signals/${id}/resume`, { method: 'POST' });
      await fetchSignals();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleTick = async (id: string) => {
    try {
      await apiFetch(`/api/pulse/signals/${id}/tick`, { method: 'POST' });
      await fetchSignals();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const filtered = signals.filter(s => {
    if (!search) return true;
    const q = search.toLowerCase();
    return s.name.toLowerCase().includes(q) || s.signal_id.toLowerCase().includes(q);
  });

  const statusColor = (s: string) => {
    switch (s) {
      case 'active': return 'text-green-600 bg-green-500/10';
      case 'paused': return 'text-yellow-500 bg-yellow-500/10';
      case 'completed': return 'text-blue-500 bg-blue-500/10';
      case 'cancelled': return 'text-muted-foreground bg-muted';
      default: return 'text-muted-foreground bg-muted';
    }
  };

  const typeIcon = (t: string) => {
    switch (t) {
      case 'tick': return <Zap className="w-3.5 h-3.5" />;
      case 'interval': return <Timer className="w-3.5 h-3.5" />;
      case 'cron': return <Clock className="w-3.5 h-3.5" />;
      case 'once': return <Play className="w-3.5 h-3.5" />;
      default: return <Activity className="w-3.5 h-3.5" />;
    }
  };

  const formatInterval = (ms: number) => {
    if (ms >= 3600000) return `${(ms / 3600000).toFixed(1)}h`;
    if (ms >= 60000) return `${(ms / 60000).toFixed(1)}m`;
    if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
    return `${ms}ms`;
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 text-red-500 text-xs">
          {error}
          <Button variant="bare" onClick={() => setError('')} className="ml-auto"><X className="w-3.5 h-3.5" /></Button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.total_signals ?? signals.length}</div>
          <div className="text-xs text-muted-foreground">信号总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{stats?.active_signals ?? signals.filter(s => s.status === 'active').length}</div>
          <div className="text-xs text-muted-foreground">活跃信号</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{stats?.total_ticks ?? 0}</div>
          <div className="text-xs text-muted-foreground">总触发次数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-purple-500">{stats?.avg_drift_ms ? `${stats.avg_drift_ms.toFixed(1)}ms` : '—'}</div>
          <div className="text-xs text-muted-foreground">平均漂移</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" placeholder="搜索信号名称..." value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <select className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
          <option value="">全部状态</option>
          <option value="active">活跃</option>
          <option value="paused">暂停</option>
          <option value="completed">完成</option>
        </select>
        <Button onClick={() => { setShowTicks(true); fetchTicks(); }} variant="secondary">
          <BarChart3 className="w-3.5 h-3.5" />触发历史
        </Button>
        <Button onClick={() => setShowCreate(true)} variant="chip-sm" className="flex items-center gap-1.5 bg-primary/10 text-primary border border-border hover:bg-primary/20">
          <Plus className="w-3.5 h-3.5" />新建信号
        </Button>
        <Button onClick={() => { fetchSignals(); fetchStats(); }} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Signal Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {filtered.map(s => (
          <div
            key={s.signal_id}
            className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer"
            onClick={() => { setSelected(s); setShowDetail(true); }}
          >
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-2">
                {typeIcon(s.signal_type)}
                <h3 className="text-sm font-medium">{s.name}</h3>
              </div>
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${statusColor(s.status)}`}>{s.status}</span>
            </div>
            <div className="grid grid-cols-2 gap-2 mt-3 text-xs text-muted-foreground">
              <div>类型: <span className="font-medium text-foreground">{s.signal_type}</span></div>
              <div>间隔: <span className="font-medium text-foreground">{formatInterval(s.interval_ms)}</span></div>
              <div>触发: <span className="font-medium text-foreground">{s.fire_count}{s.max_fires > 0 ? `/${s.max_fires}` : ''}</span></div>
              <div>漂移: <span className="font-medium text-foreground">{s.drift_correction}ms</span></div>
            </div>
            {s.next_fire_at && (
              <div className="text-[10px] text-muted-foreground mt-2">下次: {new Date(s.next_fire_at).toLocaleString()}</div>
            )}
            <div className="flex items-center gap-1.5 mt-3 pt-3 border-t border-border" onClick={e => e.stopPropagation()}>
              {s.status === 'active' ? (
                <>
                  <Button onClick={() => handlePause(s.signal_id)} variant="chip-xs" className="bg-yellow-500/10 text-yellow-500 hover:bg-yellow-500/20">
                    <Pause className="w-3 h-3 inline mr-0.5" />暂停
                  </Button>
                  <Button onClick={() => handleTick(s.signal_id)} variant="chip-xs" className="bg-primary/10 text-primary hover:bg-primary/20">
                    <Zap className="w-3 h-3 inline mr-0.5" />手动触发
                  </Button>
                </>
              ) : s.status === 'paused' ? (
                <Button onClick={() => handleResume(s.signal_id)} variant="chip-xs" className="bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20">
                  <Play className="w-3 h-3 inline mr-0.5" />恢复
                </Button>
              ) : null}
              <Button onClick={() => handleDelete(s.signal_id)} variant="chip-xs" className="ml-auto bg-red-500/10 text-red-500 hover:bg-red-500/20">
                <Trash2 className="w-3 h-3 inline mr-0.5" />删除
              </Button>
            </div>
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="col-span-full text-center text-sm text-muted-foreground py-8">暂无信号</div>
        )}
      </div>

      {/* Detail Dialog */}
      {showDetail && selected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowDetail(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-lg w-full mx-4 max-h-[80vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">{selected.name}</h2>
              <Button variant="bare" onClick={() => setShowDetail(false)}><X className="w-4 h-4 text-muted-foreground" /></Button>
            </div>
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-2">
                <div><span className="text-muted-foreground">ID: </span><code className="text-xs bg-muted px-1.5 py-0.5 rounded">{selected.signal_id}</code></div>
                <div><span className="text-muted-foreground">类型: </span>{selected.signal_type}</div>
                <div><span className="text-muted-foreground">间隔: </span>{formatInterval(selected.interval_ms)}</div>
                <div><span className="text-muted-foreground">状态: </span><Badge className={`${statusColor(selected.status)}`}>{selected.status}</Badge></div>
                <div><span className="text-muted-foreground">触发次数: </span>{selected.fire_count}{selected.max_fires > 0 ? `/${selected.max_fires}` : ''}</div>
                <div><span className="text-muted-foreground">漂移修正: </span>{selected.drift_correction}ms</div>
              </div>
              {selected.last_fired_at && <div className="text-xs text-muted-foreground">最后触发: {new Date(selected.last_fired_at).toLocaleString()}</div>}
              {selected.next_fire_at && <div className="text-xs text-muted-foreground">下次触发: {new Date(selected.next_fire_at).toLocaleString()}</div>}
              <div className="text-xs text-muted-foreground">创建时间: {new Date(selected.created_at).toLocaleString()}</div>
            </div>
          </div>
        </div>
      )}

      {/* Create Dialog */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-md w-full mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">新建信号</h2>
              <Button variant="bare" onClick={() => setShowCreate(false)}><X className="w-4 h-4 text-muted-foreground" /></Button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">名称 *</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="信号名称" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">类型</label>
                  <select className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={form.signal_type} onChange={e => setForm({ ...form, signal_type: e.target.value })}>
                    <option value="interval">interval（周期）</option>
                    <option value="tick">tick（手动）</option>
                    <option value="cron">cron（定时）</option>
                    <option value="once">once（单次）</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">间隔（毫秒）</label>
                  <input type="number" className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={form.interval_ms} onChange={e => setForm({ ...form, interval_ms: e.target.value })} />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">回调URL</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={form.callback_url} onChange={e => setForm({ ...form, callback_url: e.target.value })} placeholder="https://..." />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">最大触发次数（0=无限）</label>
                <input type="number" className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={form.max_fires} onChange={e => setForm({ ...form, max_fires: e.target.value })} />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <Button onClick={() => setShowCreate(false)} variant="chip-sm" className="bg-muted border border-border">取消</Button>
              <Button onClick={handleCreate} disabled={saving || !form.name.trim()} variant="primary" className="gap-1.5">
                {saving ? <Zap className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle className="w-3.5 h-3.5" />}创建
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Ticks History Dialog */}
      {showTicks && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowTicks(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-2xl w-full mx-4 max-h-[80vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">触发历史</h2>
              <Button variant="bare" onClick={() => setShowTicks(false)}><X className="w-4 h-4 text-muted-foreground" /></Button>
            </div>
            {ticks.length === 0 ? (
              <div className="text-center text-muted-foreground py-8 text-sm">暂无触发记录</div>
            ) : (
              <div className="space-y-1.5">
                {ticks.map((t, i) => (
                  <div key={t.tick_id || i} className="flex items-center gap-3 px-3 py-2 rounded bg-muted/30 text-xs">
                    <Zap className="w-3 h-3 text-primary" />
                    <span className="font-mono">{t.signal_id}</span>
                    <span className="text-muted-foreground">{new Date(t.timestamp).toLocaleString()}</span>
                    <span className="ml-auto">漂移: {t.drift_ms?.toFixed(2) ?? '—'}ms</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
