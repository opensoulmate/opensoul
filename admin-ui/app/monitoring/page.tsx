'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Activity, RefreshCw, CheckCircle, XCircle, Clock, Server,
  Cpu, HardDrive, MemoryStick, Wifi, Loader2, ChevronDown,
  ChevronRight, Settings, BarChart3, AlertTriangle, Zap,
  TrendingUp, Eye, X, Layers, Globe, Database,
} from 'lucide-react';

// ── Types ───────────────────────────────────────────────────

interface OrganStatus {
  key: string;
  label: string;
  category: string;
  status: string;
  status_code: number;
  response_time_ms: number;
  error?: string;
}

interface OrganDetail {
  organ: string;
  label: string;
  category: string;
  status: string;
  status_code: number;
  response_time_ms: number;
  detail?: Record<string, any>;
  error?: string;
}

interface SystemInfo {
  hostname: string;
  os: string;
  arch: string;
  python: string;
  cpu_count: number;
  cpu_percent?: number;
  memory_total_gb?: number;
  memory_used_gb?: number;
  memory_percent?: number;
  disk_total_gb?: number;
  disk_used_gb?: number;
  disk_percent?: number;
  uptime_seconds?: number;
  load_avg_1m?: number;
  load_avg_5m?: number;
  load_avg_15m?: number;
}

interface CheckAllResult {
  summary: {
    total: number;
    healthy: number;
    unhealthy: number;
    avg_response_ms: number;
    max_response_ms: number;
    overall: string;
  };
  system: SystemInfo;
  organs: OrganStatus[];
}

interface ConfigDiff {
  has_customization: boolean;
  changes: { key: string; default: any; current: any }[];
  config_path: string;
}

interface DiagnosticsStats {
  status: string;
  component: string;
  total_organs: number;
  categories: string[];
}

// ── Helpers ─────────────────────────────────────────────────

function formatUptime(seconds?: number) {
  if (!seconds) return '-';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}天${hours}小时`;
  if (hours > 0) return `${hours}小时${mins}分钟`;
  return `${mins}分钟`;
}

function formatBytes(bytes?: number) {
  if (!bytes) return '-';
  if (bytes > 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (bytes > 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function StatusBadge({ status }: { status: string }) {
  if (status === 'ok') {
    return <span className="flex items-center gap-1 text-[10px] text-green-600 bg-green-500/10 px-1.5 py-0.5 rounded"><CheckCircle className="w-3 h-3" />健康</span>;
  }
  return <span className="flex items-center gap-1 text-[10px] text-red-500 bg-red-500/10 px-1.5 py-0.5 rounded"><XCircle className="w-3 h-3" />异常</span>;
}

// ── Component ───────────────────────────────────────────────

export default function MonitoringPage() {
  const [data, setData] = useState<CheckAllResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [refreshInterval, setRefreshInterval] = useState(15);

  // Organ detail
  const [selectedOrgan, setSelectedOrgan] = useState<OrganDetail | null>(null);
  const [organLoading, setOrganLoading] = useState(false);

  // Config diff
  const [configDiff, setConfigDiff] = useState<ConfigDiff | null>(null);
  const [showConfigDiff, setShowConfigDiff] = useState(false);
  const [configLoading, setConfigLoading] = useState(false);

  // Stats
  const [stats, setStats] = useState<DiagnosticsStats | null>(null);

  // Prometheus metrics (parsed)
  const [metricsText, setMetricsText] = useState('');
  const [showMetrics, setShowMetrics] = useState(false);

  // History for sparkline
  const [history, setHistory] = useState<{ time: Date; healthy: number; total: number }[]>([]);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const result = await apiFetch('/api/diagnostics/check-all');
      setData(result);
      setLastRefresh(new Date());
      setHistory(prev => {
        const next = [...prev, {
          time: new Date(),
          healthy: result.summary.healthy,
          total: result.summary.total,
        }];
        return next.slice(-20);
      });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const result = await apiFetch('/api/diagnostics/stats');
      setStats(result);
    } catch {}
  }, []);

  useEffect(() => { fetchData(); fetchStats(); }, [fetchData, fetchStats]);

  useEffect(() => {
    if (!autoRefresh) return;
    const iv = setInterval(fetchData, refreshInterval * 1000);
    return () => clearInterval(iv);
  }, [autoRefresh, refreshInterval, fetchData]);

  const fetchOrganDetail = async (key: string) => {
    setOrganLoading(true);
    try {
      const result = await apiFetch(`/api/diagnostics/organs/${key}`);
      setSelectedOrgan(result);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setOrganLoading(false);
    }
  };

  const fetchConfigDiff = async () => {
    setConfigLoading(true);
    try {
      const result = await apiFetch('/api/diagnostics/config-diff');
      setConfigDiff(result);
      setShowConfigDiff(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setConfigLoading(false);
    }
  };

  const fetchMetrics = async () => {
    try {
      const res = await fetch('/api/metrics');
      const text = await res.text();
      setMetricsText(text);
      setShowMetrics(true);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const organs = data?.organs || [];
  const categories = Array.from(new Set(organs.map((o) => o.category))).sort();

  const filtered = organs.filter((o) => {
    if (categoryFilter !== 'all' && o.category !== categoryFilter) return false;
    if (statusFilter === 'ok' && o.status !== 'ok') return false;
    if (statusFilter === 'error' && o.status !== 'error') return false;
    return true;
  });

  // Group organs by category
  const grouped = filtered.reduce((acc, organ) => {
    (acc[organ.category] = acc[organ.category] || []).push(organ);
    return acc;
  }, {} as Record<string, OrganStatus[]>);

  // Category labels
  const categoryLabels: Record<string, string> = {
    core: '核心组件',
    platform: '平台组件',
    advanced: '高级组件',
    system: '系统组件',
  };

  return (
    <div className="space-y-4">
      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-primary" />
            <span className="text-xs text-muted-foreground">总体状态</span>
          </div>
          <div className={`text-lg font-bold mt-1 ${data?.summary?.overall === 'ok' ? 'text-green-600' : 'text-orange-500'}`}>
            {data?.summary?.overall === 'ok' ? '正常' : '降级'}
          </div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-green-500" />
            <span className="text-xs text-muted-foreground">健康组件</span>
          </div>
          <div className="text-lg font-bold mt-1">{data?.summary?.healthy || 0}/{data?.summary?.total || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-blue-500" />
            <span className="text-xs text-muted-foreground">平均响应</span>
          </div>
          <div className="text-lg font-bold mt-1">{data?.summary?.avg_response_ms || 0}ms</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-orange-500" />
            <span className="text-xs text-muted-foreground">最慢响应</span>
          </div>
          <div className="text-lg font-bold mt-1">{data?.summary?.max_response_ms || 0}ms</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-purple-500" />
            <span className="text-xs text-muted-foreground">系统运行</span>
          </div>
          <div className="text-lg font-bold mt-1">{formatUptime(data?.system?.uptime_seconds)}</div>
        </div>
      </div>

      {/* System resources */}
      {data?.system && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {data.system.cpu_percent !== undefined && (
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Cpu className="w-3.5 h-3.5" />CPU ({data.system.cpu_count}核)
              </div>
              <div className="mt-2">
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="font-medium">{data.system.cpu_percent}%</span>
                </div>
                <div className="w-full bg-muted rounded-full h-1.5">
                  <div
                    className={`h-1.5 rounded-full transition-all ${data.system.cpu_percent > 80 ? 'bg-red-500' : data.system.cpu_percent > 60 ? 'bg-orange-500' : 'bg-green-500'}`}
                    style={{ width: `${data.system.cpu_percent}%` }}
                  />
                </div>
              </div>
            </div>
          )}
          {data.system.memory_percent !== undefined && (
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <MemoryStick className="w-3.5 h-3.5" />内存
              </div>
              <div className="mt-2">
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="font-medium">{data.system.memory_percent}%</span>
                  <span className="text-muted-foreground">{data.system.memory_used_gb}/{data.system.memory_total_gb}GB</span>
                </div>
                <div className="w-full bg-muted rounded-full h-1.5">
                  <div
                    className={`h-1.5 rounded-full transition-all ${data.system.memory_percent > 80 ? 'bg-red-500' : data.system.memory_percent > 60 ? 'bg-orange-500' : 'bg-green-500'}`}
                    style={{ width: `${data.system.memory_percent}%` }}
                  />
                </div>
              </div>
            </div>
          )}
          {data.system.disk_percent !== undefined && (
            <div className="rounded-lg border border-border bg-card p-3">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <HardDrive className="w-3.5 h-3.5" />磁盘
              </div>
              <div className="mt-2">
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="font-medium">{data.system.disk_percent}%</span>
                  <span className="text-muted-foreground">{data.system.disk_used_gb}/{data.system.disk_total_gb}GB</span>
                </div>
                <div className="w-full bg-muted rounded-full h-1.5">
                  <div
                    className={`h-1.5 rounded-full transition-all ${data.system.disk_percent > 80 ? 'bg-red-500' : data.system.disk_percent > 60 ? 'bg-orange-500' : 'bg-green-500'}`}
                    style={{ width: `${data.system.disk_percent}%` }}
                  />
                </div>
              </div>
            </div>
          )}
          <div className="rounded-lg border border-border bg-card p-3">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Globe className="w-3.5 h-3.5" />系统信息
            </div>
            <div className="mt-1 text-[10px] space-y-0.5">
              <div>{data.system.hostname}</div>
              <div>{data.system.os}</div>
              <div>Python {data.system.python}</div>
            </div>
          </div>
        </div>
      )}

      {/* Health sparkline */}
      {history.length > 1 && (
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp className="w-3.5 h-3.5 text-muted-foreground" />
            <span className="text-xs text-muted-foreground">健康趋势（最近{history.length}次检查）</span>
          </div>
          <div className="flex items-end gap-1 h-8">
            {history.map((h, i) => {
              const pct = h.total > 0 ? (h.healthy / h.total) * 100 : 0;
              return (
                <div
                  key={i}
                  className={`flex-1 rounded-t transition-all ${pct === 100 ? 'bg-green-500' : pct > 80 ? 'bg-orange-500' : 'bg-red-500'}`}
                  style={{ height: `${Math.max(pct, 5)}%` }}
                  title={`${h.healthy}/${h.total} 健康`}
                />
              );
            })}
          </div>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
        >
          <option value="all">全部分类</option>
          {categories.map((c) => <option key={c} value={c}>{categoryLabels[c] || c}</option>)}
        </select>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="ok">健康</option>
          <option value="error">异常</option>
        </select>
        <div className="flex items-center gap-1.5">
          <label className="flex items-center gap-1 text-xs cursor-pointer">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} className="rounded" />
            自动刷新
          </label>
          {autoRefresh && (
            <select
              className="px-2 py-1 text-[10px] bg-muted border border-border rounded"
              value={refreshInterval}
              onChange={(e) => setRefreshInterval(Number(e.target.value))}
            >
              <option value={5}>5秒</option>
              <option value={15}>15秒</option>
              <option value={30}>30秒</option>
              <option value={60}>60秒</option>
            </select>
          )}
        </div>
        <button onClick={fetchData} disabled={loading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted disabled:opacity-50 transition-colors">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}刷新
        </button>
        <button onClick={fetchConfigDiff} disabled={configLoading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted disabled:opacity-50 transition-colors">
          {configLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Settings className="w-3.5 h-3.5" />}配置差异
        </button>
        <button onClick={fetchMetrics} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">
          <BarChart3 className="w-3.5 h-3.5" />Prometheus
        </button>
        {stats && (
          <span className="text-[10px] text-muted-foreground ml-auto">
            {stats.total_organs} 组件 · {stats.categories.length} 分类
          </span>
        )}
        {lastRefresh && <span className="text-[10px] text-muted-foreground">上次刷新: {lastRefresh.toLocaleTimeString('zh-CN')}</span>}
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')}><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* Organ grid grouped by category */}
      {Object.entries(grouped).map(([category, categoryOrgans]) => (
        <div key={category} className="space-y-2">
          <div className="flex items-center gap-2">
            <Layers className="w-3.5 h-3.5 text-muted-foreground" />
            <h3 className="text-xs font-medium text-muted-foreground">{categoryLabels[category] || category}</h3>
            <span className="text-[10px] text-muted-foreground">
              ({categoryOrgans.filter(o => o.status === 'ok').length}/{categoryOrgans.length})
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
            {categoryOrgans.map((organ) => (
              <button
                key={organ.key}
                onClick={() => fetchOrganDetail(organ.key)}
                className={`text-left rounded-lg border p-3 transition-colors hover:bg-muted/30 ${organ.status === 'ok' ? 'border-border bg-card' : 'border-red-500/20 bg-red-500/5'}`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 min-w-0">
                    {organ.status === 'ok' ? (
                      <CheckCircle className="w-4 h-4 text-green-500 shrink-0" />
                    ) : (
                      <XCircle className="w-4 h-4 text-red-500 shrink-0" />
                    )}
                    <div className="min-w-0">
                      <h4 className="text-xs font-medium truncate">{organ.label}</h4>
                      <span className="text-[10px] text-muted-foreground">{organ.key}</span>
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-xs font-mono">{organ.response_time_ms}ms</div>
                    {organ.status_code > 0 && <div className="text-[10px] text-muted-foreground">HTTP {organ.status_code}</div>}
                  </div>
                </div>
                {organ.error && <p className="text-[10px] text-red-500 mt-1 truncate">{organ.error}</p>}
              </button>
            ))}
          </div>
        </div>
      ))}

      {filtered.length === 0 && (
        <div className="text-center py-8 text-xs text-muted-foreground">没有匹配的组件</div>
      )}

      {/* Organ Detail Dialog */}
      {selectedOrgan && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setSelectedOrgan(null)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                <StatusBadge status={selectedOrgan.status} />
                <h2 className="text-sm font-medium">{selectedOrgan.label}</h2>
              </div>
              <button onClick={() => setSelectedOrgan(null)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 overflow-y-auto space-y-3 text-xs">
              <div className="grid grid-cols-2 gap-2">
                <div className="p-2 rounded bg-muted/50">
                  <div className="text-muted-foreground">组件标识</div>
                  <div className="font-mono mt-0.5">{selectedOrgan.organ}</div>
                </div>
                <div className="p-2 rounded bg-muted/50">
                  <div className="text-muted-foreground">分类</div>
                  <div className="mt-0.5">{categoryLabels[selectedOrgan.category] || selectedOrgan.category}</div>
                </div>
                <div className="p-2 rounded bg-muted/50">
                  <div className="text-muted-foreground">HTTP状态码</div>
                  <div className="font-mono mt-0.5">{selectedOrgan.status_code || '-'}</div>
                </div>
                <div className="p-2 rounded bg-muted/50">
                  <div className="text-muted-foreground">响应时间</div>
                  <div className="font-mono mt-0.5">{selectedOrgan.response_time_ms}ms</div>
                </div>
              </div>
              {selectedOrgan.error && (
                <div className="p-2 rounded bg-red-500/5 border border-red-500/20">
                  <div className="text-red-500 font-medium mb-0.5">错误信息</div>
                  <div className="text-red-500 break-all">{selectedOrgan.error}</div>
                </div>
              )}
              {selectedOrgan.detail && (
                <div>
                  <div className="text-muted-foreground mb-1">详细响应</div>
                  <pre className="p-2 rounded bg-muted/50 text-[10px] overflow-x-auto max-h-60 overflow-y-auto">
                    {JSON.stringify(selectedOrgan.detail, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Config Diff Dialog */}
      {showConfigDiff && configDiff && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowConfigDiff(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-2xl mx-4 max-h-[80vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                <Settings className="w-4 h-4 text-primary" />
                <h2 className="text-sm font-medium">配置差异</h2>
                <span className="text-[10px] text-muted-foreground">{configDiff.changes.length} 项自定义</span>
              </div>
              <button onClick={() => setShowConfigDiff(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 overflow-y-auto">
              {configDiff.changes.length === 0 ? (
                <div className="text-center py-8 text-xs text-muted-foreground">使用默认配置，无自定义项</div>
              ) : (
                <div className="space-y-2">
                  <div className="text-[10px] text-muted-foreground mb-2">配置文件: {configDiff.config_path}</div>
                  {configDiff.changes.map((c, i) => (
                    <div key={i} className="rounded border border-border p-2">
                      <div className="font-mono text-xs font-medium mb-1">{c.key}</div>
                      <div className="grid grid-cols-2 gap-2 text-[10px]">
                        <div>
                          <span className="text-muted-foreground">默认: </span>
                          <span className="font-mono">{JSON.stringify(c.default)}</span>
                        </div>
                        <div>
                          <span className="text-primary">当前: </span>
                          <span className="font-mono text-primary">{JSON.stringify(c.current)}</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Prometheus Metrics Dialog */}
      {showMetrics && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowMetrics(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-3xl mx-4 max-h-[80vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div className="flex items-center gap-2">
                <BarChart3 className="w-4 h-4 text-primary" />
                <h2 className="text-sm font-medium">Prometheus Metrics</h2>
                <span className="text-[10px] text-muted-foreground font-mono">/api/metrics</span>
              </div>
              <button onClick={() => setShowMetrics(false)} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4 overflow-y-auto">
              <pre className="text-[10px] font-mono whitespace-pre-wrap text-muted-foreground leading-relaxed">
                {metricsText}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
