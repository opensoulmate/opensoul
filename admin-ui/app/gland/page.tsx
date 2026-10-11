'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, Plus, Trash2, Server, Key, Cpu, Activity,
  Zap, CheckCircle, AlertCircle, Loader2, DollarSign, Gauge, Wifi,
  WifiOff, Settings, ArrowRight, Shield, BarChart3, Clock,
} from 'lucide-react';
import { Badge, TONES } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface Provider {
  name: string;
  base_url: string;
  models: Record<string, string>;
  enabled: boolean;
  priority: number;
  consecutive_failures: number;
}

interface KeySlot {
  provider: string;
  masked_key: string;
  active: boolean;
}

interface UsageSummary {
  total_input_tokens: number;
  total_output_tokens: number;
  total_requests: number;
  budget_limit: number;
  budget_used_pct: number;
  by_provider: Record<string, { input: number; output: number; requests: number }>;
}

interface UsageRecord {
  timestamp: string;
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  task: string;
}

interface HealthData {
  status: string;
  component: string;
  providers: { total: number; enabled: number; unhealthy: number };
  keys: { total: number };
  token_meter: UsageSummary;
}

// ── Helpers ─────────────────────────────────────────────────

function formatNumber(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return String(n);
}

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  } catch { return ts; }
}

function StatusDot({ enabled }: { enabled: boolean }) {
  return <span className={`inline-block w-2 h-2 rounded-full ${enabled ? TONES.success.fill : TONES.destructive.fill}`} />;
}

// ── Main Page ───────────────────────────────────────────────

export default function GlandPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [health, setHealth] = useState<HealthData | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [keys, setKeys] = useState<KeySlot[]>([]);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [recentUsage, setRecentUsage] = useState<UsageRecord[]>([]);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ provider: string; ok: boolean; msg: string } | null>(null);

  // Add provider dialog
  const [showAddProvider, setShowAddProvider] = useState(false);
  const [newProvider, setNewProvider] = useState({ name: '', base_url: '', models: '', api_key: '', priority: 0 });
  const [addingProvider, setAddingProvider] = useState(false);

  // Add key dialog
  const [showAddKey, setShowAddKey] = useState(false);
  const [newKey, setNewKey] = useState({ provider: '', api_key: '' });
  const [addingKey, setAddingKey] = useState(false);

  // Budget dialog
  const [showBudget, setShowBudget] = useState(false);
  const [budgetLimit, setBudgetLimit] = useState('');
  const [settingBudget, setSettingBudget] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    const results = await Promise.allSettled([
      apiFetch('/api/gland/health'),
      apiFetch('/api/gland/providers'),
      apiFetch('/api/gland/keys'),
      apiFetch('/api/gland/usage'),
      apiFetch('/api/gland/usage/recent?limit=30'),
    ]);
    const [h, p, k, u, r] = results;
    if (h.status === 'fulfilled') setHealth(h.value);
    if (p.status === 'fulfilled') setProviders(p.value.providers || []);
    if (k.status === 'fulfilled') {
      const keyData = k.value;
      setKeys(Array.isArray(keyData) ? keyData : keyData.keys || keyData.slots || []);
    }
    if (u.status === 'fulfilled') setUsage(u.value);
    if (r.status === 'fulfilled') setRecentUsage(r.value.records || []);

    // Check for errors
    const errors = results.filter(r => r.status === 'rejected') as PromiseRejectedResult[];
    if (errors.length === results.length) {
      setError(errors[0]?.reason?.message || '所有请求失败');
    }
    setLoading(false);
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleTestProvider = async (providerName?: string) => {
    try {
      setTesting(providerName || 'auto');
      setTestResult(null);
      const data = await apiFetch('/api/gland/test', {
        method: 'POST',
        body: providerName ? JSON.stringify({ provider: providerName }) : undefined,
      });
      setTestResult({ provider: providerName || 'auto', ok: true, msg: data.message || '连接成功' });
    } catch (e: any) {
      setTestResult({ provider: providerName || 'auto', ok: false, msg: e.message });
    } finally {
      setTesting(null);
    }
  };

  const handleAddProvider = async () => {
    if (!newProvider.name || !newProvider.base_url) return;
    try {
      setAddingProvider(true);
      const models: Record<string, string> = {};
      if (newProvider.models) {
        newProvider.models.split(',').forEach(m => {
          const [k, v] = m.split(':').map(s => s.trim());
          if (k && v) models[k] = v;
        });
      }
      await apiFetch('/api/gland/providers', {
        method: 'POST',
        body: JSON.stringify({
          name: newProvider.name,
          base_url: newProvider.base_url,
          models: Object.keys(models).length > 0 ? models : undefined,
          api_key: newProvider.api_key || undefined,
          priority: newProvider.priority,
        }),
      });
      setShowAddProvider(false);
      setNewProvider({ name: '', base_url: '', models: '', api_key: '', priority: 0 });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAddingProvider(false);
    }
  };

  const handleRemoveProvider = async (name: string) => {
    if (!confirm(`确定要移除 Provider「${name}」？`)) return;
    try {
      await apiFetch(`/api/gland/providers/${encodeURIComponent(name)}`, { method: 'DELETE' });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleAddKey = async () => {
    if (!newKey.provider || !newKey.api_key) return;
    try {
      setAddingKey(true);
      await apiFetch('/api/gland/keys', {
        method: 'POST',
        body: JSON.stringify(newKey),
      });
      setShowAddKey(false);
      setNewKey({ provider: '', api_key: '' });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAddingKey(false);
    }
  };

  const handleSetBudget = async () => {
    try {
      setSettingBudget(true);
      await apiFetch('/api/gland/budget', {
        method: 'POST',
        body: JSON.stringify({ limit: parseInt(budgetLimit) || 0 }),
      });
      setShowBudget(false);
      fetchData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSettingBudget(false);
    }
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Health Stats ── */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <StatusDot enabled={health?.status === 'ok'} />
            <span className="text-xs text-muted-foreground">状态</span>
          </div>
          <div className="text-lg font-bold">{health?.status === 'ok' ? '正常' : '异常'}</div>
          <div className="text-[10px] text-muted-foreground">OpenGland</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{providers.length}</div>
          <div className="text-xs text-muted-foreground">Provider总数</div>
          <div className="text-[10px] text-green-500">{providers.filter(p => p.enabled).length} 可用</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{keys.length}</div>
          <div className="text-xs text-muted-foreground">API密钥</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{formatNumber(usage?.total_requests || 0)}</div>
          <div className="text-xs text-muted-foreground">总请求数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{formatNumber((usage?.total_input_tokens || 0) + (usage?.total_output_tokens || 0))}</div>
          <div className="text-xs text-muted-foreground">总Token数</div>
          <div className="text-[10px] text-muted-foreground">
            入: {formatNumber(usage?.total_input_tokens || 0)} / 出: {formatNumber(usage?.total_output_tokens || 0)}
          </div>
        </div>
      </div>

      {/* ── Budget Bar ── */}
      {usage?.budget_limit ? (
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <DollarSign className="w-4 h-4 text-amber-500" />
              <span className="text-sm font-medium">Token预算</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {formatNumber((usage?.total_input_tokens || 0) + (usage?.total_output_tokens || 0))} / {formatNumber(usage.budget_limit)}
              </span>
              <Button onClick={() => { setBudgetLimit(String(usage.budget_limit)); setShowBudget(true); }} variant="secondary" className="text-[10px] px-2 py-0.5">修改</Button>
            </div>
          </div>
          <div className="w-full h-3 bg-muted rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${
                (usage.budget_used_pct || 0) > 90 ? 'bg-red-500' :
                (usage.budget_used_pct || 0) > 70 ? 'bg-amber-500' : 'bg-green-500'
              }`}
              style={{ width: `${Math.min(100, usage.budget_used_pct || 0)}%` }}
            />
          </div>
          <div className="text-right text-[10px] text-muted-foreground mt-1">
            {(usage.budget_used_pct || 0).toFixed(1)}% 已使用
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-border bg-card p-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <DollarSign className="w-4 h-4 text-muted-foreground" />
            <span className="text-xs text-muted-foreground">Token预算: 无限制</span>
          </div>
          <Button onClick={() => { setBudgetLimit(''); setShowBudget(true); }} variant="secondary" className="py-1">设置预算</Button>
        </div>
      )}

      {/* ── Toolbar ── */}
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => handleTestProvider()}
          disabled={testing !== null} variant="primary" className="gap-1.5">
          {testing === 'auto' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
          测试连接
        </Button>
        <Button onClick={() => fetchData()} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowAddProvider(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />添加Provider
        </Button>
        <Button onClick={() => setShowAddKey(true)} variant="secondary">
          <Key className="w-3.5 h-3.5" />添加密钥
        </Button>
      </div>

      {/* ── Test Result ── */}
      {testResult && (
        <div className={`flex items-center gap-2 p-3 rounded-lg border ${
          testResult.ok ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-red-500/30 bg-red-500/5'
        }`}>
          {testResult.ok ? <CheckCircle className="w-4 h-4 text-emerald-500" /> : <AlertCircle className="w-4 h-4 text-red-500" />}
          <span className={`text-xs ${testResult.ok ? 'text-emerald-500' : 'text-red-500'}`}>
            {testResult.provider}: {testResult.msg}
          </span>
          <Button variant="bare" onClick={() => setTestResult(null)} className="ml-auto"><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* ── Providers List ── */}
      <div className="rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 p-4 border-b border-border">
          <Server className="w-4 h-4" />
          <span className="text-sm font-medium">Provider列表</span>
          <span className="text-[10px] text-muted-foreground ml-auto">按优先级排序，数字越小优先级越高</span>
        </div>
        <div className="divide-y divide-border">
          {providers.length === 0 ? (
            <div className="p-8 text-center text-muted-foreground">
              <Server className="w-8 h-8 mx-auto mb-2 opacity-30" />
              <p className="text-xs">暂无Provider配置</p>
            </div>
          ) : (
            providers
              .sort((a, b) => a.priority - b.priority)
              .map((p) => (
                <div key={p.name} className="flex items-center gap-3 p-3 hover:bg-muted/30 transition-colors">
                  <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                    p.enabled ? 'bg-emerald-500/10' : 'bg-red-500/10'
                  }`}>
                    {p.enabled ? <Wifi className="w-5 h-5 text-emerald-500" /> : <WifiOff className="w-5 h-5 text-red-500" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{p.name}</span>
                      <Badge variant="bare" className={`${
 p.enabled ? 'bg-emerald-500/10 text-emerald-500' : 'bg-red-500/10 text-red-500'
 }`}>
                        {p.enabled ? '可用' : '不可用'}
                      </Badge>
                      <Badge variant="muted">
                        优先级: {p.priority}
                      </Badge>
                      {p.consecutive_failures > 0 && (
                        <Badge variant="destructive">
                          {p.consecutive_failures} 次失败
                        </Badge>
                      )}
                    </div>
                    <div className="text-[10px] text-muted-foreground mt-0.5 font-mono truncate">
                      {p.base_url}
                    </div>
                    {Object.keys(p.models || {}).length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {Object.entries(p.models).map(([task, model]) => (
                          <Badge key={task} variant="default">
                            {task}: {model}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <Button onClick={() => handleTestProvider(p.name)}
                      disabled={testing !== null}
                      
                      title="测试连接" variant="secondary" className="text-[10px] px-2 py-1">
                      {testing === p.name ? <Loader2 className="w-3 h-3 animate-spin" /> : <Wifi className="w-3 h-3" />}
                    </Button>
                    <Button
                      onClick={() => handleRemoveProvider(p.name)}
                      variant="chip-xs" className="bg-red-500/10 text-red-500 hover:bg-red-500/20"
                      title="移除"
                    >
                      <Trash2 className="w-3 h-3" />
                    </Button>
                  </div>
                </div>
              ))
          )}
        </div>
      </div>

      {/* ── Keys & Usage side by side ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Keys */}
        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center gap-2 p-4 border-b border-border">
            <Key className="w-4 h-4" />
            <span className="text-sm font-medium">API密钥</span>
            <span className="text-[10px] text-muted-foreground ml-auto">密钥已脱敏显示</span>
          </div>
          <div className="divide-y divide-border max-h-[300px] overflow-auto">
            {keys.length === 0 ? (
              <div className="p-6 text-center text-muted-foreground">
                <Key className="w-6 h-6 mx-auto mb-2 opacity-30" />
                <p className="text-xs">暂无API密钥</p>
              </div>
            ) : (
              keys.map((k, i) => (
                <div key={i} className="flex items-center gap-3 p-3 hover:bg-muted/30">
                  <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center">
                    <Key className="w-4 h-4 text-amber-500" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{k.provider || 'unknown'}</span>
                      {k.active !== undefined && (
                        <Badge variant="bare" className={`${
 k.active ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'
 }`}>
                          {k.active ? '活跃' : '闲置'}
                        </Badge>
                      )}
                    </div>
                    <span className="text-[10px] font-mono text-muted-foreground">{k.masked_key || '****'}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Usage by Provider */}
        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center gap-2 p-4 border-b border-border">
            <BarChart3 className="w-4 h-4" />
            <span className="text-sm font-medium">各Provider用量</span>
          </div>
          <div className="divide-y divide-border max-h-[300px] overflow-auto">
            {Object.keys(usage?.by_provider || {}).length === 0 ? (
              <div className="p-6 text-center text-muted-foreground">
                <BarChart3 className="w-6 h-6 mx-auto mb-2 opacity-30" />
                <p className="text-xs">暂无用量数据</p>
              </div>
            ) : (
              Object.entries(usage?.by_provider || {}).map(([name, data]) => {
                const total = (data.input || 0) + (data.output || 0);
                const maxTotal = Math.max(...Object.values(usage?.by_provider || {}).map(d => (d.input || 0) + (d.output || 0)), 1);
                return (
                  <div key={name} className="p-3 hover:bg-muted/30">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium">{name}</span>
                      <span className="text-[10px] text-muted-foreground">{formatNumber(data.requests || 0)} 请求</span>
                    </div>
                    <div className="w-full h-2 bg-muted rounded-full overflow-hidden mb-1">
                      <div className="h-full bg-primary rounded-full" style={{ width: `${(total / maxTotal) * 100}%` }} />
                    </div>
                    <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                      <span>入: {formatNumber(data.input || 0)}</span>
                      <span>出: {formatNumber(data.output || 0)}</span>
                      <span>合计: {formatNumber(total)}</span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* ── Recent Usage Records ── */}
      {recentUsage.length > 0 && (
        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center gap-2 p-4 border-b border-border">
            <Clock className="w-4 h-4" />
            <span className="text-sm font-medium">最近调用记录</span>
            <span className="text-[10px] text-muted-foreground ml-auto">最近 {recentUsage.length} 条</span>
          </div>
          <div className="divide-y divide-border max-h-[400px] overflow-auto">
            {recentUsage.map((r, i) => (
              <div key={i} className="flex items-center gap-3 px-3 py-2 hover:bg-muted/30 text-xs">
                <span className="text-muted-foreground shrink-0 w-24">{formatTime(r.timestamp)}</span>
                <span className="px-1.5 py-0.5 rounded bg-primary/10 text-primary shrink-0">{r.provider}</span>
                <span className="font-mono text-muted-foreground truncate flex-1">{r.model}</span>
                <span className="text-muted-foreground shrink-0">{r.task}</span>
                <span className="text-muted-foreground shrink-0 text-[10px]">
                  {formatNumber(r.input_tokens)}→{formatNumber(r.output_tokens)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Error ── */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
          <span className="text-xs text-red-500 flex-1">{error}</span>
          <Button onClick={() => setError('')} variant="icon-sm" className="hover:bg-muted"><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* ── Add Provider Dialog ── */}
      {showAddProvider && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowAddProvider(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">添加Provider</h2>
              <Button onClick={() => setShowAddProvider(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">名称 *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={newProvider.name} onChange={e => setNewProvider(p => ({ ...p, name: e.target.value }))}
                  placeholder="openai, ollama, custom..." />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Base URL *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  value={newProvider.base_url} onChange={e => setNewProvider(p => ({ ...p, base_url: e.target.value }))}
                  placeholder="https://api.openai.com/v1" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">模型映射（task:model,逗号分隔）</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={newProvider.models} onChange={e => setNewProvider(p => ({ ...p, models: e.target.value }))}
                  placeholder="chat:gpt-4o, embedding:text-embedding-3-small" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">API密钥（可选）</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  type="password" value={newProvider.api_key} onChange={e => setNewProvider(p => ({ ...p, api_key: e.target.value }))}
                  placeholder="sk-..." />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">优先级（数字越小越优先）</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  type="number" value={newProvider.priority} onChange={e => setNewProvider(p => ({ ...p, priority: parseInt(e.target.value) || 0 }))} />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowAddProvider(false)} variant="secondary">取消</Button>
                <Button onClick={handleAddProvider} disabled={!newProvider.name || !newProvider.base_url || addingProvider} variant="primary">
                  {addingProvider ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                  添加
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Add Key Dialog ── */}
      {showAddKey && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowAddKey(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-sm mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">添加API密钥</h2>
              <Button onClick={() => setShowAddKey(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">Provider名称</label>
                <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={newKey.provider} onChange={e => setNewKey(k => ({ ...k, provider: e.target.value }))}>
                  <option value="">选择Provider</option>
                  {providers.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">API密钥</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  type="password" value={newKey.api_key} onChange={e => setNewKey(k => ({ ...k, api_key: e.target.value }))}
                  placeholder="sk-..." />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowAddKey(false)} variant="secondary">取消</Button>
                <Button onClick={handleAddKey} disabled={!newKey.provider || !newKey.api_key || addingKey} variant="primary">
                  {addingKey ? <Loader2 className="w-3 h-3 animate-spin" /> : <Key className="w-3 h-3" />}
                  添加
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── Budget Dialog ── */}
      {showBudget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowBudget(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-sm mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">设置Token预算</h2>
              <Button onClick={() => setShowBudget(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">预算上限（Token数，0 = 无限制）</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  type="number" value={budgetLimit} onChange={e => setBudgetLimit(e.target.value)}
                  placeholder="0" />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowBudget(false)} variant="secondary">取消</Button>
                <Button onClick={handleSetBudget} disabled={settingBudget} variant="primary">
                  {settingBudget ? <Loader2 className="w-3 h-3 animate-spin" /> : <DollarSign className="w-3 h-3" />}
                  保存
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
