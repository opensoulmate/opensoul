'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, Zap, Plus, Trash2, X, Settings, Clock,
  Tag, AlertCircle, CheckCircle, Loader2, ArrowRight, BarChart3,
  Activity, Layers, Target, Edit3, Save, Shield,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge, tone } from '@/components/ui/badge';

// ── Types ───────────────────────────────────────────────────

interface CacheEntry {
  entry_id: string;
  query: string;
  response: string;
  category: string;
  tags: string[];
  hit_count: number;
  importance: number;
  source: string;
  created_at: string;
  last_hit_at: string;
  ttl_seconds: number | null;
}

interface ReflexStats {
  status: string;
  component: string;
  cache: {
    total_entries: number;
    total_hits: number;
    avg_hit_count: number;
    categories: string[];
    expired: number;
  };
}

interface ReflexConfig {
  max_entries: number;
  similarity_threshold: number;
  default_ttl_seconds: number | null;
}

interface LookupResult {
  hit: boolean;
  entry_id?: string;
  query?: string;
  response?: string;
  category?: string;
  hit_count?: number;
  importance?: number;
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
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

function importanceColor(val: number) {
  if (val >= 0.8) return tone('destructive');
  if (val >= 0.5) return tone('warning');
  return tone('success');
}

function importanceLabel(val: number) {
  if (val >= 0.8) return '高';
  if (val >= 0.5) return '中';
  return '低';
}

// ── Main Page ───────────────────────────────────────────────

export default function ReflexPage() {
  const [entries, setEntries] = useState<CacheEntry[]>([]);
  const [stats, setStats] = useState<ReflexStats | null>(null);
  const [config, setConfig] = useState<ReflexConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [tab, setTab] = useState<'cache' | 'lookup' | 'config'>('cache');

  // Lookup
  const [lookupQuery, setLookupQuery] = useState('');
  const [lookupResult, setLookupResult] = useState<LookupResult | null>(null);
  const [looking, setLooking] = useState(false);

  // Create
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newEntry, setNewEntry] = useState({ query: '', response: '', category: '', tags: '', importance: 0.5, ttl_seconds: '' });

  // Edit
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({ response: '', category: '', tags: '', importance: 0.5 });

  // Config editing
  const [editingConfig, setEditingConfig] = useState(false);
  const [configForm, setConfigForm] = useState({ max_entries: 5000, similarity_threshold: 0.8, default_ttl: '' });

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const [entryData, statsData, configData] = await Promise.all([
        apiFetch('/api/reflex/cache?limit=200').catch(() => ({ entries: [] })),
        apiFetch('/api/reflex/stats').catch(() => null),
        apiFetch('/api/reflex/config').catch(() => null),
      ]);
      setEntries(Array.isArray(entryData.entries) ? entryData.entries : []);
      if (statsData) setStats(statsData as ReflexStats);
      if (configData) {
        setConfig(configData as ReflexConfig);
        setConfigForm({
          max_entries: (configData as ReflexConfig).max_entries,
          similarity_threshold: (configData as ReflexConfig).similarity_threshold,
          default_ttl: (configData as ReflexConfig).default_ttl_seconds?.toString() || '',
        });
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleLookup = async () => {
    if (!lookupQuery.trim()) return;
    try {
      setLooking(true);
      setLookupResult(null);
      const data = await apiFetch('/api/reflex/lookup', {
        method: 'POST',
        body: JSON.stringify({ query: lookupQuery }),
      });
      setLookupResult(data as LookupResult);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLooking(false);
    }
  };

  const handleCreate = async () => {
    if (!newEntry.query.trim() || !newEntry.response.trim()) return;
    try {
      setCreating(true);
      await apiFetch('/api/reflex/cache', {
        method: 'POST',
        body: JSON.stringify({
          query: newEntry.query,
          response: newEntry.response,
          category: newEntry.category,
          tags: newEntry.tags.split(',').map((t) => t.trim()).filter(Boolean),
          importance: newEntry.importance,
          ttl_seconds: newEntry.ttl_seconds ? Number(newEntry.ttl_seconds) : null,
        }),
      });
      setShowCreate(false);
      setNewEntry({ query: '', response: '', category: '', tags: '', importance: 0.5, ttl_seconds: '' });
      fetchAll();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('确定要删除此缓存条目？')) return;
    try {
      await apiFetch(`/api/reflex/cache/${id}`, { method: 'DELETE' });
      setEntries((prev) => prev.filter((e) => e.entry_id !== id));
    } catch (e: any) {
      setError(e.message);
    }
  };

  const startEdit = (entry: CacheEntry) => {
    setEditingId(entry.entry_id);
    setEditForm({
      response: entry.response,
      category: entry.category,
      tags: entry.tags.join(', '),
      importance: entry.importance,
    });
  };

  const saveEdit = async () => {
    if (!editingId) return;
    try {
      await apiFetch(`/api/reflex/cache/${editingId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          response: editForm.response,
          category: editForm.category,
          tags: editForm.tags.split(',').map((t) => t.trim()).filter(Boolean),
          importance: editForm.importance,
        }),
      });
      setEditingId(null);
      fetchAll();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleCleanup = async () => {
    try {
      const data = await apiFetch('/api/reflex/cleanup', { method: 'POST' });
      fetchAll();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const saveConfig = async () => {
    try {
      const params = new URLSearchParams();
      params.set('max_entries', configForm.max_entries.toString());
      params.set('similarity_threshold', configForm.similarity_threshold.toString());
      if (configForm.default_ttl) params.set('default_ttl', configForm.default_ttl);
      await apiFetch(`/api/reflex/config?${params}`, { method: 'PUT' });
      setEditingConfig(false);
      fetchAll();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const categories = Array.from(new Set(entries.map((e) => e.category).filter(Boolean)));

  const filtered = entries.filter((e) => {
    if (categoryFilter && e.category !== categoryFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      return e.query.toLowerCase().includes(q) || e.response.toLowerCase().includes(q) || e.category.toLowerCase().includes(q);
    }
    return true;
  });

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Layers className="w-3.5 h-3.5" />缓存条目</div>
          <div className="text-2xl font-bold">{stats?.cache?.total_entries ?? entries.length}</div>
          <div className="text-[10px] text-muted-foreground">最大 {config?.max_entries ?? '-'}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Target className="w-3.5 h-3.5" />总命中</div>
          <div className="text-2xl font-bold">{stats?.cache?.total_hits ?? 0}</div>
          <div className="text-[10px] text-muted-foreground">平均 {stats?.cache?.avg_hit_count?.toFixed(1) ?? 0} 次/条</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Tag className="w-3.5 h-3.5" />分类</div>
          <div className="text-2xl font-bold">{categories.length}</div>
          <div className="text-[10px] text-muted-foreground">{stats?.cache?.categories?.length ?? 0} 类</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Shield className="w-3.5 h-3.5" />相似度阈值</div>
          <div className="text-2xl font-bold">{((config?.similarity_threshold ?? 0.8) * 100).toFixed(0)}%</div>
          <div className="text-[10px] text-muted-foreground">模糊匹配</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索问题、回答..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {categories.length > 0 && (
          <select
            className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
          >
            <option value="">全部分类</option>
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
        <Button onClick={fetchAll} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={handleCleanup} variant="chip-sm" className="flex items-center gap-1.5 bg-amber-500/10 text-amber-600 border border-amber-500/20 hover:bg-amber-500/20">
          <Clock className="w-3.5 h-3.5" />清理过期
        </Button>
        <Button onClick={() => setShowCreate(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />添加缓存
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

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        {([['cache', '缓存列表', Layers], ['lookup', '模糊查询', Search], ['config', '配置', Settings]] as const).map(([key, label, Icon]) => (
          <Button
            key={key}
            onClick={() => setTab(key)}
            variant="tabline" className={` ${tab === key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
          >
            <Icon className="w-3.5 h-3.5" />{label}
          </Button>
        ))}
      </div>

      {/* Tab: Cache List */}
      {tab === 'cache' && (
        <div className="space-y-2">
          {filtered.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <Zap className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-xs">{search ? '没有匹配的缓存' : '暂无缓存条目'}</p>
            </div>
          ) : (
            filtered.map((entry) => (
              <div key={entry.entry_id} className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors">
                {editingId === entry.entry_id ? (
                  /* Edit mode */
                  <div className="space-y-2">
                    <div>
                      <label className="text-[10px] text-muted-foreground">问题</label>
                      <div className="text-xs font-medium mt-0.5">{entry.query}</div>
                    </div>
                    <div>
                      <label className="text-[10px] text-muted-foreground">回答</label>
                      <textarea
                        className="w-full mt-1 px-2 py-1.5 text-xs bg-muted border border-border rounded-md resize-y"
                        rows={3}
                        value={editForm.response}
                        onChange={(e) => setEditForm((p) => ({ ...p, response: e.target.value }))}
                      />
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      <div>
                        <label className="text-[10px] text-muted-foreground">分类</label>
                        <input className="w-full mt-1 px-2 py-1 text-xs bg-muted border border-border rounded-md" value={editForm.category} onChange={(e) => setEditForm((p) => ({ ...p, category: e.target.value }))} />
                      </div>
                      <div>
                        <label className="text-[10px] text-muted-foreground">标签（逗号分隔）</label>
                        <input className="w-full mt-1 px-2 py-1 text-xs bg-muted border border-border rounded-md" value={editForm.tags} onChange={(e) => setEditForm((p) => ({ ...p, tags: e.target.value }))} />
                      </div>
                      <div>
                        <label className="text-[10px] text-muted-foreground">重要性 ({editForm.importance})</label>
                        <input type="range" min={0} max={1} step={0.1} className="w-full mt-1" value={editForm.importance} onChange={(e) => setEditForm((p) => ({ ...p, importance: Number(e.target.value) }))} />
                      </div>
                    </div>
                    <div className="flex gap-2 justify-end">
                      <Button onClick={() => setEditingId(null)} variant="secondary" className="px-2 py-1 text-[10px]">取消</Button>
                      <Button onClick={saveEdit} variant="primary" className="py-1 text-[10px]">
                        <Save className="w-3 h-3" />保存
                      </Button>
                    </div>
                  </div>
                ) : (
                  /* View mode */
                  <>
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <Zap className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                          <span className="text-sm font-medium">{entry.query}</span>
                        </div>
                        <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{entry.response}</p>
                        <div className="flex flex-wrap items-center gap-2 mt-2">
                          {entry.category && (
                            <Badge variant="default">{entry.category}</Badge>
                          )}
                          {entry.tags.map((t) => (
                            <Badge variant="muted" key={t}>#{t}</Badge>
                          ))}
                          <Badge variant="bare" className={`${importanceColor(entry.importance)}`}>
                            重要性: {importanceLabel(entry.importance)}
                          </Badge>
                          <span className="text-[10px] text-muted-foreground">命中 {entry.hit_count} 次</span>
                          <span className="text-[10px] text-muted-foreground">{formatRelative(entry.created_at)}</span>
                          {entry.ttl_seconds && (
                            <span className="text-[10px] text-muted-foreground">TTL: {entry.ttl_seconds}s</span>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <Button onClick={() => startEdit(entry)} variant="icon-md" className="hover:bg-muted text-muted-foreground hover:text-foreground" title="编辑">
                          <Edit3 className="w-3.5 h-3.5" />
                        </Button>
                        <Button onClick={() => handleDelete(entry.entry_id)} variant="icon-md" className="hover:bg-red-500/10 text-muted-foreground hover:text-red-500" title="删除">
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </div>
                  </>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {/* Tab: Lookup */}
      {tab === 'lookup' && (
        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="text-xs font-medium mb-3 flex items-center gap-2">
              <Search className="w-3.5 h-3.5 text-primary" />
              模糊匹配查询
            </div>
            <div className="flex gap-2">
              <input
                className="flex-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                placeholder="输入问题，系统将使用模糊匹配查找缓存..."
                value={lookupQuery}
                onChange={(e) => setLookupQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleLookup()}
              />
              <Button onClick={handleLookup}
                disabled={!lookupQuery.trim() || looking} variant="primary">
                {looking ? <Loader2 className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3" />}
                查询
              </Button>
            </div>
          </div>

          {lookupResult && (
            <div className={`rounded-lg border p-4 ${lookupResult.hit ? 'border-emerald-500/30 bg-green-500/5' : 'border-border bg-card'}`}>
              <div className="flex items-center gap-2 mb-2">
                {lookupResult.hit ? (
                  <>
                    <CheckCircle className="w-4 h-4 text-green-500" />
                    <span className="text-sm font-medium text-green-500">缓存命中</span>
                    <span className="text-[10px] text-muted-foreground">命中次数: {lookupResult.hit_count}</span>
                  </>
                ) : (
                  <>
                    <X className="w-4 h-4 text-muted-foreground" />
                    <span className="text-sm font-medium text-muted-foreground">未命中</span>
                  </>
                )}
              </div>
              {lookupResult.hit && (
                <div className="space-y-2">
                  <div>
                    <div className="text-[10px] text-muted-foreground">匹配问题</div>
                    <div className="text-xs font-medium">{lookupResult.query}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground">缓存回答</div>
                    <div className="text-xs text-muted-foreground whitespace-pre-wrap">{lookupResult.response}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    {lookupResult.category && <Badge variant="default">{lookupResult.category}</Badge>}
                    {lookupResult.importance !== undefined && (
                      <Badge variant="bare" className={`${importanceColor(lookupResult.importance)}`}>
                        重要性: {importanceLabel(lookupResult.importance)}
                      </Badge>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Tab: Config */}
      {tab === 'config' && config && (
        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-4">
              <div className="text-xs font-medium flex items-center gap-2">
                <Settings className="w-3.5 h-3.5 text-primary" />
                缓存配置
              </div>
              {!editingConfig ? (
                <Button onClick={() => setEditingConfig(true)} variant="secondary" className="gap-1 px-2 py-1 text-[10px]">
                  <Edit3 className="w-3 h-3" />编辑
                </Button>
              ) : (
                <div className="flex gap-1">
                  <Button onClick={() => setEditingConfig(false)} variant="secondary" className="px-2 py-1 text-[10px]">取消</Button>
                  <Button onClick={saveConfig} variant="primary" className="py-1 text-[10px]">
                    <Save className="w-3 h-3" />保存
                  </Button>
                </div>
              )}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="text-[10px] text-muted-foreground">最大条目数</label>
                {editingConfig ? (
                  <input type="number" className="w-full mt-1 px-2 py-1.5 text-xs bg-muted border border-border rounded-md" value={configForm.max_entries} onChange={(e) => setConfigForm((p) => ({ ...p, max_entries: Number(e.target.value) }))} />
                ) : (
                  <div className="text-lg font-bold mt-1">{config.max_entries}</div>
                )}
              </div>
              <div>
                <label className="text-[10px] text-muted-foreground">相似度阈值</label>
                {editingConfig ? (
                  <div className="flex items-center gap-2 mt-1">
                    <input type="range" min={0} max={1} step={0.05} className="flex-1" value={configForm.similarity_threshold} onChange={(e) => setConfigForm((p) => ({ ...p, similarity_threshold: Number(e.target.value) }))} />
                    <span className="text-xs font-mono">{configForm.similarity_threshold.toFixed(2)}</span>
                  </div>
                ) : (
                  <div className="text-lg font-bold mt-1">{(config.similarity_threshold * 100).toFixed(0)}%</div>
                )}
              </div>
              <div>
                <label className="text-[10px] text-muted-foreground">默认TTL（秒）</label>
                {editingConfig ? (
                  <input type="number" className="w-full mt-1 px-2 py-1.5 text-xs bg-muted border border-border rounded-md" value={configForm.default_ttl} onChange={(e) => setConfigForm((p) => ({ ...p, default_ttl: e.target.value }))} placeholder="留空=永久" />
                ) : (
                  <div className="text-lg font-bold mt-1">{config.default_ttl_seconds ? `${config.default_ttl_seconds}s` : '永久'}</div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Create Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">添加缓存条目</h2>
              <Button onClick={() => setShowCreate(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">问题 *</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={newEntry.query}
                  onChange={(e) => setNewEntry((p) => ({ ...p, query: e.target.value }))}
                  placeholder="高频问题..."
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">回答 *</label>
                <textarea
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md resize-y"
                  rows={4}
                  value={newEntry.response}
                  onChange={(e) => setNewEntry((p) => ({ ...p, response: e.target.value }))}
                  placeholder="标准回答..."
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">分类</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={newEntry.category} onChange={(e) => setNewEntry((p) => ({ ...p, category: e.target.value }))} placeholder="分类" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">标签（逗号分隔）</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={newEntry.tags} onChange={(e) => setNewEntry((p) => ({ ...p, tags: e.target.value }))} placeholder="tag1, tag2" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">重要性 ({newEntry.importance})</label>
                  <input type="range" min={0} max={1} step={0.1} className="w-full mt-1" value={newEntry.importance} onChange={(e) => setNewEntry((p) => ({ ...p, importance: Number(e.target.value) }))} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">TTL（秒，留空=永久）</label>
                  <input type="number" className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={newEntry.ttl_seconds} onChange={(e) => setNewEntry((p) => ({ ...p, ttl_seconds: e.target.value }))} />
                </div>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreate(false)} variant="secondary">取消</Button>
                <Button onClick={handleCreate}
                  disabled={!newEntry.query.trim() || !newEntry.response.trim() || creating} variant="primary">
                  {creating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                  添加
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
