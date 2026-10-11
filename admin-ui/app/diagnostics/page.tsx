'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Activity, RefreshCw, X, CheckCircle, XCircle, AlertCircle,
  Clock, Server, Cpu, HardDrive, MemoryStick, Loader2, Settings,
  ChevronDown, ChevronRight, Zap, Layers, Wifi
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface OrganCheck {
  key: string;
  label: string;
  category: string;
  status: string;
  status_code: number;
  response_time_ms: number;
  error?: string;
  detail?: any;
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
  organs: OrganCheck[];
}

interface ConfigChange {
  key: string;
  default: any;
  current: any;
}

const CATEGORY_LABELS: Record<string, string> = {
  core: '核心',
  platform: '平台',
  advanced: '高级',
  system: '系统',
};

const CATEGORY_COLORS: Record<string, string> = {
  core: 'bg-red-500/10 text-red-600',
  platform: 'bg-blue-500/10 text-blue-600',
  advanced: 'bg-purple-500/10 text-purple-600',
  system: 'bg-emerald-500/10 text-emerald-500',
};

export default function DiagnosticsPage() {
  const [result, setResult] = useState<CheckAllResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [expandedOrgan, setExpandedOrgan] = useState<string | null>(null);
  const [organDetail, setOrganDetail] = useState<Record<string, any>>({});
  const [configChanges, setConfigChanges] = useState<ConfigChange[]>([]);
  const [showConfig, setShowConfig] = useState(false);
  const [configLoading, setConfigLoading] = useState(false);

  const runCheckAll = useCallback(async () => {
    try {
      setScanning(true);
      setError('');
      const data = await apiFetch('/api/diagnostics/check-all');
      setResult(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setScanning(false); setLoading(false);
    }
  }, []);

  useEffect(() => { runCheckAll(); }, [runCheckAll]);

  const fetchOrganDetail = async (key: string) => {
    if (organDetail[key]) { setExpandedOrgan(expandedOrgan === key ? null : key); return; }
    try {
      setExpandedOrgan(key);
      const data = await apiFetch(`/api/diagnostics/organs/${key}`);
      setOrganDetail(prev => ({ ...prev, [key]: data }));
    } catch (e: any) { setError(e.message); }
  };

  const fetchConfigDiff = async () => {
    try {
      setConfigLoading(true);
      const data = await apiFetch('/api/diagnostics/config-diff');
      setConfigChanges(Array.isArray(data.changes) ? data.changes : []);
      setShowConfig(true);
    } catch (e: any) { setError(e.message); }
    finally { setConfigLoading(false); }
  };

  const formatUptime = (seconds?: number) => {
    if (!seconds) return '-';
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}天${h}时${m}分`;
    if (h > 0) return `${h}时${m}分`;
    return `${m}分`;
  };

  const filteredOrgans = (result?.organs || []).filter((o) => {
    if (categoryFilter !== 'all' && o.category !== categoryFilter) return false;
    if (statusFilter === 'ok' && o.status !== 'ok') return false;
    if (statusFilter === 'error' && o.status === 'ok') return false;
    if (search) {
      const q = search.toLowerCase();
      return o.key.toLowerCase().includes(q) || o.label.toLowerCase().includes(q);
    }
    return true;
  });

  const categories = [...new Set((result?.organs || []).map(o => o.category))];

  if (loading) return <div className="text-sm text-muted-foreground p-4">正在执行系统诊断...</div>;

  const summary = result?.summary;
  const sys = result?.system;

  return (
    <div className="space-y-4">
      {/* Summary Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className={`text-2xl font-bold ${summary?.overall === 'ok' ? 'text-emerald-600' : 'text-amber-500'}`}>
            {summary?.overall === 'ok' ? '健康' : '降级'}
          </div>
          <div className="text-xs text-muted-foreground">系统状态</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{summary?.total ?? 0}</div>
          <div className="text-xs text-muted-foreground">器官总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-emerald-600">{summary?.healthy ?? 0}</div>
          <div className="text-xs text-muted-foreground">正常</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-red-500">{summary?.unhealthy ?? 0}</div>
          <div className="text-xs text-muted-foreground">异常</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{summary?.avg_response_ms ?? 0}<span className="text-xs ml-0.5">ms</span></div>
          <div className="text-xs text-muted-foreground">平均响应</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-amber-500">{summary?.max_response_ms ?? 0}<span className="text-xs ml-0.5">ms</span></div>
          <div className="text-xs text-muted-foreground">最大响应</div>
        </div>
      </div>

      {/* System Info */}
      {sys && (
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="text-xs font-semibold mb-3 flex items-center gap-2"><Server className="w-3.5 h-3.5" />系统信息</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div><span className="text-muted-foreground">主机名:</span> {sys.hostname}</div>
            <div><span className="text-muted-foreground">系统:</span> {sys.os}</div>
            <div><span className="text-muted-foreground">架构:</span> {sys.arch}</div>
            <div><span className="text-muted-foreground">Python:</span> {sys.python}</div>
            <div className="flex items-center gap-1"><Cpu className="w-3 h-3 text-muted-foreground" /> CPU {sys.cpu_count}核{sys.cpu_percent !== undefined ? ` (${sys.cpu_percent}%)` : ''}</div>
            {sys.memory_total_gb !== undefined && (
              <div className="flex items-center gap-1"><MemoryStick className="w-3 h-3 text-muted-foreground" /> 内存 {sys.memory_used_gb}/{sys.memory_total_gb}GB ({sys.memory_percent}%)</div>
            )}
            {sys.disk_total_gb !== undefined && (
              <div className="flex items-center gap-1"><HardDrive className="w-3 h-3 text-muted-foreground" /> 磁盘 {sys.disk_used_gb}/{sys.disk_total_gb}GB ({sys.disk_percent}%)</div>
            )}
            {sys.uptime_seconds !== undefined && <div><span className="text-muted-foreground">运行时间:</span> {formatUptime(sys.uptime_seconds)}</div>}
            {sys.load_avg_1m !== undefined && <div><span className="text-muted-foreground">负载:</span> {sys.load_avg_1m.toFixed(2)} / {sys.load_avg_5m?.toFixed(2)} / {sys.load_avg_15m?.toFixed(2)}</div>}
          </div>
          {/* Resource bars */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">
            {sys.cpu_percent !== undefined && (
              <div>
                <div className="flex justify-between text-[10px] text-muted-foreground mb-1"><span>CPU</span><span>{sys.cpu_percent}%</span></div>
                <div className="w-full bg-muted rounded-full h-1.5"><div className={`h-1.5 rounded-full transition-all ${sys.cpu_percent > 80 ? 'bg-red-500' : sys.cpu_percent > 50 ? 'bg-orange-500' : 'bg-green-500'}`} style={{ width: `${sys.cpu_percent}%` }} /></div>
              </div>
            )}
            {sys.memory_percent !== undefined && (
              <div>
                <div className="flex justify-between text-[10px] text-muted-foreground mb-1"><span>内存</span><span>{sys.memory_percent}%</span></div>
                <div className="w-full bg-muted rounded-full h-1.5"><div className={`h-1.5 rounded-full transition-all ${sys.memory_percent > 80 ? 'bg-red-500' : sys.memory_percent > 50 ? 'bg-orange-500' : 'bg-green-500'}`} style={{ width: `${sys.memory_percent}%` }} /></div>
              </div>
            )}
            {sys.disk_percent !== undefined && (
              <div>
                <div className="flex justify-between text-[10px] text-muted-foreground mb-1"><span>磁盘</span><span>{sys.disk_percent}%</span></div>
                <div className="w-full bg-muted rounded-full h-1.5"><div className={`h-1.5 rounded-full transition-all ${sys.disk_percent > 80 ? 'bg-red-500' : sys.disk_percent > 50 ? 'bg-orange-500' : 'bg-green-500'}`} style={{ width: `${sys.disk_percent}%` }} /></div>
              </div>
            )}
          </div>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 text-red-500 text-xs">
          <AlertCircle className="w-3.5 h-3.5" /><span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')} className="p-0.5"><X className="w-3.5 h-3.5" /></Button>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索器官名称..." value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <select className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
          <option value="all">全部分类</option>
          {categories.map(c => <option key={c} value={c}>{CATEGORY_LABELS[c] || c}</option>)}
        </select>
        <select className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="all">全部状态</option>
          <option value="ok">正常</option>
          <option value="error">异常</option>
        </select>
        <Button onClick={runCheckAll} disabled={scanning} variant="secondary">
          {scanning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          {scanning ? '扫描中...' : '重新扫描'}
        </Button>
        <Button onClick={fetchConfigDiff} disabled={configLoading} variant="secondary">
          {configLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Settings className="w-3.5 h-3.5" />}
          配置差异
        </Button>
      </div>

      {/* Organ Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
        {filteredOrgans.map((organ) => {
          const isExpanded = expandedOrgan === organ.key;
          const detail = organDetail[organ.key];
          const isOk = organ.status === 'ok';
          const speedColor = organ.response_time_ms < 100 ? 'text-green-600' : organ.response_time_ms < 500 ? 'text-orange-500' : 'text-red-500';
          return (
            <div key={organ.key} className={`rounded-lg border bg-card transition-colors ${isOk ? 'border-border' : 'border-red-500/30'}`}>
              <div className="p-3 cursor-pointer hover:bg-muted/30" onClick={() => fetchOrganDetail(organ.key)}>
                <div className="flex items-center gap-2">
                  <span className="text-sm">{organ.label.split(' ')[0]}</span>
                  <span className="text-xs font-medium flex-1">{organ.label.split(' ').slice(1).join(' ')}</span>
                  <Badge variant="bare" className={`${CATEGORY_COLORS[organ.category] || 'bg-muted text-muted-foreground'}`}>
                    {CATEGORY_LABELS[organ.category] || organ.category}
                  </Badge>
                  {isOk ? (
                    <CheckCircle className="w-3.5 h-3.5 text-emerald-600" />
                  ) : (
                    <XCircle className="w-3.5 h-3.5 text-red-500" />
                  )}
                  <span className={`text-[10px] font-mono ${speedColor}`}>{organ.response_time_ms}ms</span>
                  {isExpanded ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" /> : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />}
                </div>
                {organ.error && <p className="text-[10px] text-red-500 mt-1 truncate">{organ.error}</p>}
              </div>
              {isExpanded && detail && (
                <div className="border-t border-border p-3">
                  <div className="text-xs space-y-1">
                    <div><span className="text-muted-foreground">状态码:</span> {detail.status_code}</div>
                    <div><span className="text-muted-foreground">端点:</span> <span className="font-mono">{detail.endpoint}</span></div>
                    {detail.detail && (
                      <div>
                        <span className="text-muted-foreground">响应详情:</span>
                        <pre className="mt-1 bg-muted rounded p-2 text-[10px] overflow-x-auto max-h-32 font-mono">
                          {JSON.stringify(detail.detail, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {filteredOrgans.length === 0 && (
        <div className="text-center py-12 text-muted-foreground text-sm">
          <Activity className="w-8 h-8 mx-auto mb-2 opacity-30" />
          <p>无匹配的器官</p>
        </div>
      )}

      {/* Config Diff Dialog */}
      {showConfig && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowConfig(false)}>
          <div className="bg-card rounded-xl border border-border shadow-xl w-full max-w-2xl mx-4 max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border shrink-0">
              <h2 className="text-sm font-semibold">配置差异 ({configChanges.length} 项自定义)</h2>
              <Button onClick={() => setShowConfig(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {configChanges.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground text-sm">
                  <CheckCircle className="w-8 h-8 mx-auto mb-2 text-emerald-600 opacity-50" />
                  <p>使用默认配置，无自定义修改</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {configChanges.map((c, i) => (
                    <div key={i} className="rounded-lg border border-border bg-muted/30 p-3">
                      <div className="text-xs font-medium font-mono mb-1">{c.key}</div>
                      <div className="grid grid-cols-2 gap-2 text-[10px]">
                        <div>
                          <span className="text-muted-foreground">默认值:</span>
                          <pre className="mt-0.5 bg-muted rounded p-1.5 overflow-x-auto">{JSON.stringify(c.default, null, 2)}</pre>
                        </div>
                        <div>
                          <span className="text-muted-foreground">当前值:</span>
                          <pre className="mt-0.5 bg-green-500/10 rounded p-1.5 overflow-x-auto">{JSON.stringify(c.current, null, 2)}</pre>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="flex items-center justify-end p-4 border-t border-border shrink-0">
              <Button onClick={() => setShowConfig(false)} variant="primary">关闭</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
