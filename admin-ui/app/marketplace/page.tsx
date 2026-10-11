'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Store, Trash2, RefreshCw, X, Plus, Edit2, Save,
  ExternalLink, CheckCircle, XCircle, Filter, Download, Package,
  Zap, ShoppingBag, Globe, ToggleLeft, ToggleRight, AlertCircle
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface Source {
  id: string;
  name: string;
  type: string;
  url: string;
  description: string;
  enabled: boolean;
  builtin: boolean;
  auto_sync?: boolean;
  auto_update?: boolean;
  sync_interval?: number;
  last_sync: string | null;
  skill_count?: number;
  agent_count?: number;
}

interface Stats {
  skill_sources: number;
  agent_sources: number;
  total_skills: number;
  total_agents: number;
}

type TabType = 'skills' | 'agents';

const SOURCE_TYPES = [
  { value: 'hermes', label: 'Hermes' },
  { value: 'openclaw', label: 'OpenClaw' },
  { value: 'github', label: 'GitHub' },
  { value: 'tencent', label: '腾讯' },
  { value: 'aliyun', label: '阿里云' },
  { value: 'custom', label: '自定义' },
];

const TYPE_COLORS: Record<string, string> = {
  hermes: 'bg-violet-500/10 text-violet-500',
  openclaw: 'bg-blue-500/10 text-blue-500',
  github: 'bg-gray-500/10 text-gray-400',
  tencent: 'bg-cyan-500/10 text-cyan-500',
  aliyun: 'bg-amber-500/10 text-amber-500',
  builtin: 'bg-emerald-500/10 text-emerald-500',
  custom: 'bg-yellow-500/10 text-yellow-500',
};

export default function MarketplacePage() {
  const [tab, setTab] = useState<TabType>('skills');
  const [sources, setSources] = useState<Source[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [editSource, setEditSource] = useState<Source | null>(null);
  const [syncing, setSyncing] = useState<Set<string>>(new Set());

  // Form state
  const [formName, setFormName] = useState('');
  const [formType, setFormType] = useState('custom');
  const [formUrl, setFormUrl] = useState('');
  const [formDesc, setFormDesc] = useState('');
  const [formEnabled, setFormEnabled] = useState(true);
  const [formAutoSync, setFormAutoSync] = useState(true);
  const [formSyncInterval, setFormSyncInterval] = useState(3600);

  const fetchSources = useCallback(async () => {
    try {
      setLoading(true);
      const endpoint = tab === 'skills' ? '/api/marketplace/skills/sources' : '/api/marketplace/agents/sources';
      const data = await apiFetch(endpoint);
      setSources(Array.isArray(data.sources) ? data.sources : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [tab]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/marketplace/stats');
      setStats(data);
    } catch {
      // silent
    }
  }, []);

  useEffect(() => { fetchSources(); fetchStats(); }, [fetchSources, fetchStats]);

  const resetForm = () => {
    setFormName('');
    setFormType('custom');
    setFormUrl('');
    setFormDesc('');
    setFormEnabled(true);
    setFormAutoSync(true);
    setFormSyncInterval(3600);
    setEditSource(null);
    setShowCreate(false);
  };

  const openEdit = (src: Source) => {
    setEditSource(src);
    setFormName(src.name);
    setFormType(src.type);
    setFormUrl(src.url);
    setFormDesc(src.description);
    setFormEnabled(src.enabled);
    setFormAutoSync(src.auto_sync ?? src.auto_update ?? true);
    setFormSyncInterval(src.sync_interval ?? 3600);
    setShowCreate(true);
  };

  const handleSave = async () => {
    try {
      const endpoint = tab === 'skills' ? '/api/marketplace/skills/sources' : '/api/marketplace/agents/sources';
      if (editSource) {
        await apiFetch(`${endpoint}/${editSource.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: formName,
            url: formUrl,
            description: formDesc,
            enabled: formEnabled,
            auto_sync: formAutoSync,
            sync_interval: formSyncInterval,
          }),
        });
      } else {
        await apiFetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: formName,
            type: formType,
            url: formUrl,
            description: formDesc,
            enabled: formEnabled,
            auto_sync: formAutoSync,
            sync_interval: formSyncInterval,
          }),
        });
      }
      resetForm();
      fetchSources();
      fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDelete = async (src: Source) => {
    if (src.builtin) { setError('内置数据源不能删除'); return; }
    if (!confirm(`确定要删除数据源 "${src.name}"？`)) return;
    try {
      const endpoint = tab === 'skills' ? '/api/marketplace/skills/sources' : '/api/marketplace/agents/sources';
      await apiFetch(`${endpoint}/${src.id}`, { method: 'DELETE' });
      fetchSources();
      fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleToggle = async (src: Source) => {
    try {
      const endpoint = tab === 'skills' ? '/api/marketplace/skills/sources' : '/api/marketplace/agents/sources';
      await apiFetch(`${endpoint}/${src.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !src.enabled }),
      });
      fetchSources();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleSync = async (src: Source) => {
    try {
      setSyncing((prev) => new Set(prev).add(src.id));
      await apiFetch(`/api/marketplace/skills/sources/${src.id}/sync`, { method: 'POST' });
      fetchSources();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSyncing((prev) => {
        const next = new Set(prev);
        next.delete(src.id);
        return next;
      });
    }
  };

  const filtered = sources.filter((s) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q) || s.type.toLowerCase().includes(q);
  });

  const formatTime = (ts: string | null) => {
    if (!ts) return '从未';
    try {
      return new Date(ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch { return ts; }
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.skill_sources ?? '-'}</div>
          <div className="text-xs text-muted-foreground">技能数据源</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{stats?.agent_sources ?? '-'}</div>
          <div className="text-xs text-muted-foreground">Agent数据源</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-500">{stats?.total_skills ?? '-'}</div>
          <div className="text-xs text-muted-foreground">可用技能</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-purple-500">{stats?.total_agents ?? '-'}</div>
          <div className="text-xs text-muted-foreground">可用Agent</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 p-0.5 bg-muted rounded-lg w-fit">
        <Button variant="bare"
          onClick={() => setTab('skills')}
          className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm rounded-lg transition-colors ${tab === 'skills' ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:text-foreground'}`}
        >
          <Zap className="w-3.5 h-3.5" />技能数据源
        </Button>
        <Button variant="bare"
          onClick={() => setTab('agents')}
          className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm rounded-lg transition-colors ${tab === 'agents' ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:text-foreground'}`}
        >
          <Package className="w-3.5 h-3.5" />Agent数据源
        </Button>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索数据源..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button onClick={() => { fetchSources(); fetchStats(); }} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => { resetForm(); setShowCreate(true); }} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />添加数据源
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

      {/* Source List */}
      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <div className="grid grid-cols-[1fr_80px_100px_80px_80px_100px] gap-2 px-4 py-2 text-[10px] font-medium text-muted-foreground uppercase tracking-wider bg-muted/30 border-b border-border">
          <span>数据源</span>
          <span>类型</span>
          <span>上次同步</span>
          <span>数量</span>
          <span>状态</span>
          <span className="text-right">操作</span>
        </div>
        {filtered.map((src) => (
          <div
            key={src.id}
            className="grid grid-cols-[1fr_80px_100px_80px_80px_100px] gap-2 px-4 py-3 items-center border-b border-border last:border-0 hover:bg-muted/20 transition-colors"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium truncate">{src.name}</span>
                {src.builtin && (
                  <Badge variant="default">内置</Badge>
                )}
              </div>
              <p className="text-[10px] text-muted-foreground truncate mt-0.5">{src.description}</p>
              {src.url && (
                <a
                  href={src.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-0.5 text-[10px] text-primary/70 hover:text-primary mt-0.5 truncate max-w-full"
                >
                  <Globe className="w-2.5 h-2.5 shrink-0" />
                  <span className="truncate">{src.url.replace(/^https?:\/\//, '').slice(0, 40)}</span>
                </a>
              )}
            </div>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium w-fit ${TYPE_COLORS[src.type] || TYPE_COLORS.custom}`}>
              {src.type}
            </span>
            <span className="text-[10px] text-muted-foreground">{formatTime(src.last_sync)}</span>
            <span className="text-xs font-medium">{src.skill_count ?? src.agent_count ?? 0}</span>
            <div>
              <Button variant="bare"
                onClick={() => handleToggle(src)}
                className="flex items-center gap-1"
                title={src.enabled ? '点击禁用' : '点击启用'}
              >
                {src.enabled ? (
                  <ToggleRight className="w-5 h-5 text-green-500" />
                ) : (
                  <ToggleLeft className="w-5 h-5 text-muted-foreground" />
                )}
              </Button>
            </div>
            <div className="flex items-center justify-end gap-1">
              {tab === 'skills' && (
                <Button
                  onClick={() => handleSync(src)}
                  disabled={syncing.has(src.id)}
                  variant="icon-md" className="hover:bg-muted disabled:opacity-50"
                  title="同步"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${syncing.has(src.id) ? 'animate-spin' : ''}`} />
                </Button>
              )}
              <Button onClick={() => openEdit(src)} variant="icon-md" className="hover:bg-muted" title="编辑">
                <Edit2 className="w-3.5 h-3.5" />
              </Button>
              {!src.builtin && (
                <Button onClick={() => handleDelete(src)} variant="icon-md" className="hover:bg-red-500/10" title="删除">
                  <Trash2 className="w-3.5 h-3.5 text-red-500" />
                </Button>
              )}
            </div>
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <Store className="w-10 h-10 mb-2 opacity-30" />
            <p className="text-xs">{search ? '没有匹配的数据源' : '暂无数据源'}</p>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="text-[10px] text-muted-foreground text-center">
        共 {sources.length} 个数据源 | {sources.filter((s) => s.enabled).length} 个已启用
      </div>

      {/* Create/Edit Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => resetForm()}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-base font-medium">{editSource ? '编辑数据源' : '添加数据源'}</h2>
              <Button onClick={() => resetForm()} variant="icon-sm" className="hover:bg-muted">
                <X className="w-4 h-4" />
              </Button>
            </div>
            <div className="p-4 space-y-4">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">名称 *</label>
                <input
                  className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="数据源名称"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                />
              </div>
              {!editSource && (
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">类型 *</label>
                  <select
                    className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                    value={formType}
                    onChange={(e) => setFormType(e.target.value)}
                  >
                    {SOURCE_TYPES.map((t) => (
                      <option key={t.value} value={t.value}>{t.label}</option>
                    ))}
                  </select>
                </div>
              )}
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">URL</label>
                <input
                  className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="https://..."
                  value={formUrl}
                  onChange={(e) => setFormUrl(e.target.value)}
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <input
                  className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="数据源描述"
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <label className="flex items-center gap-2 text-xs cursor-pointer">
                  <input type="checkbox" checked={formEnabled} onChange={(e) => setFormEnabled(e.target.checked)} className="rounded" />
                  启用
                </label>
                <label className="flex items-center gap-2 text-xs cursor-pointer">
                  <input type="checkbox" checked={formAutoSync} onChange={(e) => setFormAutoSync(e.target.checked)} className="rounded" />
                  自动同步
                </label>
              </div>
              {formAutoSync && (
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">同步间隔（秒）</label>
                  <input
                    type="number"
                    className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                    value={formSyncInterval}
                    onChange={(e) => setFormSyncInterval(Number(e.target.value))}
                    min={60}
                  />
                </div>
              )}
              <div className="flex items-center gap-2 pt-2">
                <Button onClick={handleSave}
                  disabled={!formName.trim()} variant="primary" className="gap-1.5 px-4 py-1.5">
                  <Save className="w-3.5 h-3.5" />{editSource ? '保存' : '创建'}
                </Button>
                <Button onClick={() => resetForm()} variant="secondary" className="px-4">
                  取消
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
