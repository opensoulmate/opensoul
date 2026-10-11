'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Plus, Trash2, X, RefreshCw, Pause, Play, Eye, Camera,
  Variable, Clock, Tag, Filter, FlaskConical, Copy, Layers, Activity, Settings
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ─────────────────────────────────────────────────────

interface Sandbox {
  sandbox_id: string;
  name: string;
  status: string;
  description: string;
  tags: string[];
  variables: Record<string, string>;
  log_count: number;
  snapshot_count: number;
  ttl_seconds: number;
  created_at: string;
}

interface SandboxTemplate {
  template_id: string;
  name: string;
  description: string;
  icon: string;
  config: Record<string, unknown>;
  variables: Record<string, string>;
  tags: string[];
  category: string;
  usage_count: number;
  created_at: string;
}

interface MirrorStats {
  status: string;
  component: string;
  total_sandboxes: number;
  active_sandboxes: number;
  paused_sandboxes: number;
  destroyed_sandboxes: number;
  templates: { total: number; categories: string[] };
}

interface LogEntry {
  action: string;
  detail: Record<string, unknown>;
  timestamp: string;
}

// ── Component ─────────────────────────────────────────────────

export default function MirrorPage() {
  // State
  const [stats, setStats] = useState<MirrorStats | null>(null);
  const [sandboxes, setSandboxes] = useState<Sandbox[]>([]);
  const [templates, setTemplates] = useState<SandboxTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'sandboxes' | 'templates'>('sandboxes');

  // Filters
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');

  // Selected sandbox detail
  const [selected, setSelected] = useState<Sandbox | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loadingLogs, setLoadingLogs] = useState(false);

  // Create sandbox dialog
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({
    name: '', description: '', tags: '', ttl_seconds: 3600,
  });
  const [creating, setCreating] = useState(false);

  // Create template dialog
  const [showCreateTpl, setShowCreateTpl] = useState(false);
  const [tplForm, setTplForm] = useState({
    name: '', description: '', icon: '🧪', category: 'custom', tags: '', variables: '',
  });

  // Instantiate from template
  const [showInstantiate, setShowInstantiate] = useState<SandboxTemplate | null>(null);
  const [instForm, setInstForm] = useState({ name: '', variables: '' });

  // ── Data Fetching ──────────────────────────────────────────

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/mirror/stats');
      setStats(data as MirrorStats);
    } catch { /* silent */ }
  }, []);

  const fetchSandboxes = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/mirror/sandboxes');
      setSandboxes((data as { sandboxes: Sandbox[] }).sandboxes || []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchTemplates = useCallback(async () => {
    try {
      const data = await apiFetch('/api/mirror/templates');
      setTemplates((data as { templates: SandboxTemplate[] }).templates || []);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    fetchStats();
    fetchSandboxes();
    fetchTemplates();
  }, [fetchStats, fetchSandboxes, fetchTemplates]);

  // ── Sandbox Actions ────────────────────────────────────────

  const handleCreateSandbox = async () => {
    if (!createForm.name) return;
    setCreating(true);
    try {
      await apiFetch('/api/mirror/sandboxes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: createForm.name,
          description: createForm.description,
          tags: createForm.tags ? createForm.tags.split(',').map(t => t.trim()) : [],
          ttl_seconds: createForm.ttl_seconds,
        }),
      });
      setShowCreate(false);
      setCreateForm({ name: '', description: '', tags: '', ttl_seconds: 3600 });
      await fetchSandboxes();
      await fetchStats();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handleDestroy = async (id: string) => {
    if (!confirm('确定销毁此沙箱？所有数据将丢失。')) return;
    try {
      await apiFetch(`/api/mirror/sandboxes/${id}`, { method: 'DELETE' });
      setSandboxes(prev => prev.filter(s => s.sandbox_id !== id));
      if (selected?.sandbox_id === id) setSelected(null);
      await fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handlePause = async (id: string) => {
    try {
      await apiFetch(`/api/mirror/sandboxes/${id}/pause`, { method: 'POST' });
      await fetchSandboxes();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleResume = async (id: string) => {
    try {
      await apiFetch(`/api/mirror/sandboxes/${id}/resume`, { method: 'POST' });
      await fetchSandboxes();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleSnapshot = async (id: string) => {
    try {
      await apiFetch(`/api/mirror/sandboxes/${id}/snapshot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: `snapshot-${Date.now()}` }),
      });
      await fetchSandboxes();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const loadLogs = async (id: string) => {
    setLoadingLogs(true);
    try {
      const data = await apiFetch(`/api/mirror/sandboxes/${id}/logs?limit=50`);
      setLogs((data as { logs: LogEntry[] }).logs || []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoadingLogs(false);
    }
  };

  const selectSandbox = async (sb: Sandbox) => {
    setSelected(sb);
    await loadLogs(sb.sandbox_id);
  };

  const handleCleanup = async () => {
    try {
      const data = await apiFetch('/api/mirror/cleanup', { method: 'POST' });
      const cleaned = (data as { cleaned: number }).cleaned;
      alert(`清理了 ${cleaned} 个过期沙箱`);
      await fetchSandboxes();
      await fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  // ── Template Actions ───────────────────────────────────────

  const handleCreateTemplate = async () => {
    if (!tplForm.name) return;
    try {
      await apiFetch('/api/mirror/templates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: tplForm.name,
          description: tplForm.description,
          icon: tplForm.icon,
          category: tplForm.category,
          tags: tplForm.tags ? tplForm.tags.split(',').map(t => t.trim()) : [],
          variables: tplForm.variables ? Object.fromEntries(
            tplForm.variables.split(',').map(v => {
              const [k, ...rest] = v.trim().split('=');
              return [k.trim(), rest.join('=').trim()];
            }).filter(([k]) => k)
          ) : {},
        }),
      });
      setShowCreateTpl(false);
      setTplForm({ name: '', description: '', icon: '🧪', category: 'custom', tags: '', variables: '' });
      await fetchTemplates();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleInstantiate = async () => {
    if (!showInstantiate) return;
    try {
      const variables = instForm.variables ? Object.fromEntries(
        instForm.variables.split(',').map(v => {
          const [k, ...rest] = v.trim().split('=');
          return [k.trim(), rest.join('=').trim()];
        }).filter(([k]) => k)
      ) : {};
      await apiFetch(`/api/mirror/templates/${showInstantiate.template_id}/instantiate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: instForm.name, variables }),
      });
      setShowInstantiate(null);
      setInstForm({ name: '', variables: '' });
      await fetchSandboxes();
      await fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDeleteTemplate = async (id: string) => {
    if (!confirm('确定删除此模板？')) return;
    try {
      await apiFetch(`/api/mirror/templates/${id}`, { method: 'DELETE' });
      setTemplates(prev => prev.filter(t => t.template_id !== id));
    } catch (e: any) {
      setError(e.message);
    }
  };

  // ── Filters ────────────────────────────────────────────────

  const filteredSandboxes = sandboxes.filter(sb => {
    if (statusFilter !== 'all' && sb.status !== statusFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return (
        (sb.name || '').toLowerCase().includes(q) ||
        sb.sandbox_id.toLowerCase().includes(q) ||
        (sb.description || '').toLowerCase().includes(q) ||
        sb.tags?.some(t => t.toLowerCase().includes(q))
      );
    }
    return true;
  });

  // ── Status Helpers ─────────────────────────────────────────

  const statusColor = (s: string) => {
    switch (s) {
      case 'active': return 'text-green-400 bg-green-400/10 border-green-400/20';
      case 'paused': return 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20';
      case 'destroyed': return 'text-red-400 bg-red-400/10 border-red-400/20';
      case 'expired': return 'text-orange-400 bg-orange-400/10 border-orange-400/20';
      default: return 'text-muted-foreground bg-muted/50 border-border';
    }
  };

  const statusLabel = (s: string) => {
    switch (s) {
      case 'active': return '运行中';
      case 'paused': return '已暂停';
      case 'destroyed': return '已销毁';
      case 'expired': return '已过期';
      default: return s;
    }
  };

  const formatTTL = (seconds: number) => {
    if (seconds >= 86400) return `${Math.floor(seconds / 86400)}天`;
    if (seconds >= 3600) return `${Math.floor(seconds / 3600)}小时`;
    if (seconds >= 60) return `${Math.floor(seconds / 60)}分钟`;
    return `${seconds}秒`;
  };

  // ── Render ─────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Error Banner */}
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')} className="shrink-0"><X className="w-4 h-4" /></Button>
        </div>
      )}

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
          {[
            { label: '总沙箱', value: stats.total_sandboxes ?? 0, icon: <Layers className="w-4 h-4" />, color: 'text-blue-400' },
            { label: '运行中', value: stats.active_sandboxes ?? 0, icon: <Activity className="w-4 h-4" />, color: 'text-green-400' },
            { label: '已暂停', value: stats.paused_sandboxes ?? 0, icon: <Pause className="w-4 h-4" />, color: 'text-yellow-400' },
            { label: '已销毁', value: stats.destroyed_sandboxes ?? 0, icon: <Trash2 className="w-4 h-4" />, color: 'text-red-400' },
            { label: '模板', value: stats.templates?.total ?? 0, icon: <FlaskConical className="w-4 h-4" />, color: 'text-purple-400' },
          ].map((c, i) => (
            <div key={i} className="rounded-xl border border-border bg-card p-4">
              <div className="flex items-center gap-2 text-muted-foreground text-xs mb-1">
                <span className={c.color}>{c.icon}</span>
                {c.label}
              </div>
              <div className={`text-2xl font-bold ${c.color}`}>{c.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* Tab Bar */}
      <div className="flex items-center gap-1 border-b border-border pb-0">
        {[
          { key: 'sandboxes' as const, label: '沙箱', icon: <Layers className="w-4 h-4" /> },
          { key: 'templates' as const, label: '模板', icon: <FlaskConical className="w-4 h-4" /> },
        ].map(t => (
          <Button variant="bare"
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
              tab === t.key
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {t.icon}
            {t.label}
            <Badge variant="muted" className="ml-1">
              {t.key === 'sandboxes' ? sandboxes.length : templates.length}
            </Badge>
          </Button>
        ))}
      </div>

      {/* ── Sandboxes Tab ─────────────────────────────────── */}
      {tab === 'sandboxes' && (
        <div className="space-y-4">
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[200px] max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <input
                type="text"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="搜索沙箱名称、ID、标签..."
                className="w-full pl-9 pr-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary"
              />
            </div>
            <select
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
              className="px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none"
            >
              <option value="all">全部状态</option>
              <option value="active">运行中</option>
              <option value="paused">已暂停</option>
              <option value="destroyed">已销毁</option>
              <option value="expired">已过期</option>
            </select>
            <Button onClick={() => fetchSandboxes()}  title="刷新" variant="secondary" className="p-2 bg-muted">
              <RefreshCw className="w-4 h-4" />
            </Button>
            <Button variant="bare" onClick={handleCleanup} className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-lg bg-orange-500/10 text-orange-400 hover:bg-amber-500/20 border border-orange-500/20" title="清理过期沙箱">
              <Trash2 className="w-4 h-4" /> 清理过期
            </Button>
            <Button onClick={() => setShowCreate(true)} variant="primary" className="gap-1.5 px-3 text-sm">
              <Plus className="w-4 h-4" /> 创建沙箱
            </Button>
          </div>

          {/* Sandbox List */}
          {loading ? (
            <div className="text-sm text-muted-foreground p-8 text-center">加载中...</div>
          ) : filteredSandboxes.length === 0 ? (
            <div className="text-sm text-muted-foreground p-8 text-center">
              {search || statusFilter !== 'all' ? '无匹配结果' : '暂无沙箱，点击"创建沙箱"开始'}
            </div>
          ) : (
            <div className="grid gap-3">
              {filteredSandboxes.map(sb => (
                <div
                  key={sb.sandbox_id}
                  onClick={() => selectSandbox(sb)}
                  className={`rounded-xl border p-4 cursor-pointer transition-all hover:border-primary/30 ${
                    selected?.sandbox_id === sb.sandbox_id
                      ? 'border-primary/50 bg-primary/5'
                      : 'border-border bg-card'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-medium text-sm truncate">{sb.name || sb.sandbox_id}</span>
                        <Badge className={`gap-1 border ${statusColor(sb.status)}`}>
                          {statusLabel(sb.status)}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-3 text-xs text-muted-foreground">
                        <span className="font-mono">{sb.sandbox_id.slice(0, 12)}</span>
                        {sb.description && <span className="truncate max-w-[300px]">{sb.description}</span>}
                        <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{formatTTL(sb.ttl_seconds)}</span>
                        <span>日志 {sb.log_count}</span>
                        <span>快照 {sb.snapshot_count}</span>
                      </div>
                      {sb.tags?.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-2">
                          {sb.tags.map((t, i) => (
                            <Badge key={i} variant="muted">{t}</Badge>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="flex items-center gap-1 shrink-0" onClick={e => e.stopPropagation()}>
                      {sb.status === 'active' && (
                        <Button onClick={() => handlePause(sb.sandbox_id)} variant="icon-md" className="hover:bg-yellow-400/10 text-yellow-400" title="暂停">
                          <Pause className="w-4 h-4" />
                        </Button>
                      )}
                      {sb.status === 'paused' && (
                        <Button onClick={() => handleResume(sb.sandbox_id)} variant="icon-md" className="hover:bg-green-400/10 text-green-400" title="恢复">
                          <Play className="w-4 h-4" />
                        </Button>
                      )}
                      <Button onClick={() => handleSnapshot(sb.sandbox_id)} variant="icon-md" className="hover:bg-blue-400/10 text-blue-400" title="快照">
                        <Camera className="w-4 h-4" />
                      </Button>
                      <Button onClick={() => handleDestroy(sb.sandbox_id)} variant="icon-md" className="hover:bg-red-400/10 text-red-400" title="销毁">
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Detail Panel */}
          {selected && (
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold flex items-center gap-2">
                  <Eye className="w-4 h-4 text-primary" />
                  {selected.name || selected.sandbox_id}
                </h3>
                <Button onClick={() => setSelected(null)} variant="icon-sm" className="hover:bg-muted">
                  <X className="w-4 h-4" />
                </Button>
              </div>

              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 text-sm">
                <div><span className="text-muted-foreground">ID: </span><span className="font-mono text-xs">{selected.sandbox_id}</span></div>
                <div><span className="text-muted-foreground">状态: </span><span className={statusColor(selected.status).split(' ')[0]}>{statusLabel(selected.status)}</span></div>
                <div><span className="text-muted-foreground">TTL: </span>{formatTTL(selected.ttl_seconds)}</div>
                <div><span className="text-muted-foreground">创建: </span>{selected.created_at ? new Date(selected.created_at).toLocaleString('zh-CN') : '-'}</div>
              </div>

              {/* Variables */}
              {selected.variables && Object.keys(selected.variables).length > 0 && (
                <div>
                  <h4 className="text-sm font-medium mb-2 flex items-center gap-1.5"><Variable className="w-3.5 h-3.5" /> 变量</h4>
                  <div className="rounded-lg bg-muted/50 p-3 text-xs font-mono space-y-1">
                    {Object.entries(selected.variables).map(([k, v]) => (
                      <div key={k} className="flex gap-2">
                        <span className="text-primary">{k}</span>
                        <span className="text-muted-foreground">=</span>
                        <span>{String(v)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Logs */}
              <div>
                <h4 className="text-sm font-medium mb-2 flex items-center gap-1.5">
                  <Activity className="w-3.5 h-3.5" /> 操作日志
                  {loadingLogs && <span className="text-xs text-muted-foreground">加载中...</span>}
                </h4>
                {logs.length === 0 ? (
                  <div className="text-xs text-muted-foreground p-3">暂无日志</div>
                ) : (
                  <div className="rounded-lg bg-muted/50 max-h-48 overflow-y-auto">
                    {logs.map((log, i) => (
                      <div key={i} className="flex items-start gap-2 px-3 py-1.5 text-xs border-b border-border/50 last:border-0">
                        <span className="text-muted-foreground shrink-0 w-16">{log.timestamp ? new Date(log.timestamp).toLocaleTimeString('zh-CN') : ''}</span>
                        <span className="font-medium shrink-0">{log.action}</span>
                        <span className="text-muted-foreground truncate">{JSON.stringify(log.detail)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Templates Tab ─────────────────────────────────── */}
      {tab === 'templates' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="text-sm text-muted-foreground">{templates.length} 个模板</div>
            <Button onClick={() => setShowCreateTpl(true)} variant="primary" className="gap-1.5 px-3 text-sm">
              <Plus className="w-4 h-4" /> 创建模板
            </Button>
          </div>

          {templates.length === 0 ? (
            <div className="text-sm text-muted-foreground p-8 text-center">暂无模板</div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {templates.map(tpl => (
                <div key={tpl.template_id} className="rounded-xl border border-border bg-card p-4 space-y-3">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-2xl">{tpl.icon}</span>
                      <div>
                        <div className="font-medium text-sm">{tpl.name}</div>
                        <div className="text-xs text-muted-foreground">{tpl.category}</div>
                      </div>
                    </div>
                    <Button onClick={() => handleDeleteTemplate(tpl.template_id)} variant="icon-sm" className="hover:bg-red-400/10 text-red-400" title="删除">
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                  {tpl.description && <div className="text-xs text-muted-foreground line-clamp-2">{tpl.description}</div>}
                  {tpl.tags?.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {tpl.tags.map((t, i) => (
                        <Badge key={i} variant="muted">{t}</Badge>
                      ))}
                    </div>
                  )}
                  {tpl.variables && Object.keys(tpl.variables).length > 0 && (
                    <div className="text-xs text-muted-foreground">
                      变量: {Object.keys(tpl.variables).join(', ')}
                    </div>
                  )}
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>使用 {tpl.usage_count} 次</span>
                    <Button variant="bare"
                      onClick={() => {
                        setInstForm({
                          name: tpl.name,
                          variables: Object.entries(tpl.variables || {}).map(([k, v]) => `${k}=${v}`).join(', '),
                        });
                        setShowInstantiate(tpl);
                      }}
                      className="flex items-center gap-1 px-2 py-1 rounded-lg bg-primary/10 text-primary hover:bg-primary/20"
                    >
                      <Copy className="w-3 h-3" /> 从此模板创建
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Create Sandbox Dialog ─────────────────────────── */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="w-full max-w-md rounded-xl border border-border bg-card p-5 space-y-4 shadow-xl" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">创建沙箱</h3>
              <Button variant="bare" onClick={() => setShowCreate(false)}><X className="w-4 h-4" /></Button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">名称 *</label>
                <input value={createForm.name} onChange={e => setCreateForm(f => ({ ...f, name: e.target.value }))} placeholder="测试环境名称" className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <input value={createForm.description} onChange={e => setCreateForm(f => ({ ...f, description: e.target.value }))} placeholder="可选描述" className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">标签（逗号分隔）</label>
                <input value={createForm.tags} onChange={e => setCreateForm(f => ({ ...f, tags: e.target.value }))} placeholder="test, dev, staging" className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">存活时间（秒）</label>
                <input type="number" value={createForm.ttl_seconds} onChange={e => setCreateForm(f => ({ ...f, ttl_seconds: parseInt(e.target.value) || 3600 }))} className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
                <span className="text-xs text-muted-foreground mt-1 block">当前: {formatTTL(createForm.ttl_seconds)}</span>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button onClick={() => setShowCreate(false)} variant="secondary" className="px-4 py-2 text-sm">取消</Button>
              <Button onClick={handleCreateSandbox} disabled={creating || !createForm.name} variant="primary" className="text-sm">
                {creating ? '创建中...' : '创建'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* ── Create Template Dialog ────────────────────────── */}
      {showCreateTpl && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreateTpl(false)}>
          <div className="w-full max-w-md rounded-xl border border-border bg-card p-5 space-y-4 shadow-xl" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">创建模板</h3>
              <Button variant="bare" onClick={() => setShowCreateTpl(false)}><X className="w-4 h-4" /></Button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">名称 *</label>
                <input value={tplForm.name} onChange={e => setTplForm(f => ({ ...f, name: e.target.value }))} placeholder="模板名称" className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <input value={tplForm.description} onChange={e => setTplForm(f => ({ ...f, description: e.target.value }))} placeholder="模板描述" className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">图标</label>
                  <input value={tplForm.icon} onChange={e => setTplForm(f => ({ ...f, icon: e.target.value }))} className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">分类</label>
                  <input value={tplForm.category} onChange={e => setTplForm(f => ({ ...f, category: e.target.value }))} placeholder="custom" className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">标签（逗号分隔）</label>
                <input value={tplForm.tags} onChange={e => setTplForm(f => ({ ...f, tags: e.target.value }))} placeholder="python, web, api" className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">变量（key=value 逗号分隔）</label>
                <input value={tplForm.variables} onChange={e => setTplForm(f => ({ ...f, variables: e.target.value }))} placeholder="PORT=3000, DEBUG=true" className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button onClick={() => setShowCreateTpl(false)} variant="secondary" className="px-4 py-2 text-sm">取消</Button>
              <Button onClick={handleCreateTemplate} disabled={!tplForm.name} variant="primary" className="text-sm">创建模板</Button>
            </div>
          </div>
        </div>
      )}

      {/* ── Instantiate Dialog ────────────────────────────── */}
      {showInstantiate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowInstantiate(null)}>
          <div className="w-full max-w-md rounded-xl border border-border bg-card p-5 space-y-4 shadow-xl" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">从模板创建沙箱</h3>
              <Button variant="bare" onClick={() => setShowInstantiate(null)}><X className="w-4 h-4" /></Button>
            </div>
            <div className="text-sm text-muted-foreground">
              模板: <span className="text-foreground">{showInstantiate.icon} {showInstantiate.name}</span>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">沙箱名称（可选覆盖）</label>
                <input value={instForm.name} onChange={e => setInstForm(f => ({ ...f, name: e.target.value }))} placeholder={showInstantiate.name} className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">变量覆盖（key=value 逗号分隔）</label>
                <input value={instForm.variables} onChange={e => setInstForm(f => ({ ...f, variables: e.target.value }))} className="w-full px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary" />
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button onClick={() => setShowInstantiate(null)} variant="secondary" className="px-4 py-2 text-sm">取消</Button>
              <Button onClick={handleInstantiate} variant="primary" className="text-sm">创建</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
