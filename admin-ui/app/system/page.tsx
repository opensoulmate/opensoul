'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Settings, Database, HardDrive, Cpu, RefreshCw, ToggleLeft, ToggleRight,
  Server, Clock, Shield, X, Save, Activity, CheckCircle, XCircle,
  AlertTriangle, Loader2, Play, Zap, ChevronDown, ChevronRight,
  BarChart3, Terminal, Globe, Info
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

// ── Types ───────────────────────────────────────────────────

interface SystemInfo {
  status: string;
  version: string;
  database: string;
  uptime: string;
}

interface Organ {
  key: string;
  enabled: boolean;
  config: Record<string, any>;
}

interface OrganHealth {
  key: string;
  endpoint: string;
  status: 'ok' | 'error' | 'timeout' | 'unknown';
  response_ms?: number;
  data?: any;
}

interface QuickOverview {
  version: string;
  uptime: string;
  organs_total: number;
  organs_healthy: number;
  knowledge_entries: number;
  active_agents: number;
  recent_sessions: number;
  memory_mb: number;
  cpu_percent: number;
  disk_usage: Record<string, any>;
}

interface BootstrapStatus {
  initialized: boolean;
  steps: Array<{ name: string; status: string; message?: string }>;
}

type Tab = 'overview' | 'organs' | 'config' | 'bootstrap';

// ── Helpers ─────────────────────────────────────────────────

function formatUptime(seconds: number) {
  if (!seconds || seconds < 0) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}天 ${h}小时`;
  if (h > 0) return `${h}小时 ${m}分钟`;
  return `${m}分钟`;
}

function StatusBadge({ status }: { status: string }) {
  const cfg: Record<string, { icon: typeof CheckCircle; color: string; bg: string; label: string }> = {
    ok: { icon: CheckCircle, color: 'text-green-500', bg: 'bg-green-500/10', label: '正常' },
    healthy: { icon: CheckCircle, color: 'text-green-500', bg: 'bg-green-500/10', label: '正常' },
    error: { icon: XCircle, color: 'text-red-500', bg: 'bg-red-500/10', label: '异常' },
    timeout: { icon: AlertTriangle, color: 'text-amber-500', bg: 'bg-amber-500/10', label: '超时' },
    degraded: { icon: AlertTriangle, color: 'text-amber-500', bg: 'bg-amber-500/10', label: '降级' },
    uninitialized: { icon: AlertTriangle, color: 'text-gray-500', bg: 'bg-gray-500/10', label: '未初始化' },
  };
  const c = cfg[status] || { icon: Info, color: 'text-muted-foreground', bg: 'bg-muted', label: status || '未知' };
  const Icon = c.icon;
  return (
    <Badge className={`gap-1 ${c.color} ${c.bg}`}>
      <Icon className="w-3 h-3" />{c.label}
    </Badge>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function SystemPage() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [organs, setOrgans] = useState<Organ[]>([]);
  const [organHealth, setOrganHealth] = useState<OrganHealth[]>([]);
  const [quick, setQuick] = useState<QuickOverview | null>(null);
  const [bootstrap, setBootstrap] = useState<BootstrapStatus | null>(null);
  const [config, setConfig] = useState<Record<string, any>>({});
  const [configText, setConfigText] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<Tab>('overview');
  const [saving, setSaving] = useState(false);
  const [healthLoading, setHealthLoading] = useState(false);
  const [expandedOrgan, setExpandedOrgan] = useState<string | null>(null);
  const [bootstrapping, setBootstrapping] = useState(false);

  const fetchOverview = useCallback(async () => {
    try {
      const [health, overview, quickData] = await Promise.allSettled([
        apiFetch('/api/health'),
        apiFetch('/api/system/overview'),
        apiFetch('/api/system/quick'),
      ]);
      const h = health.status === 'fulfilled' ? health.value : null;
      const o = overview.status === 'fulfilled' ? overview.value : null;
      const q = quickData.status === 'fulfilled' ? quickData.value : null;
      setInfo({
        status: (h as any)?.status || 'ok',
        version: (h as any)?.version || (o as any)?.version || '—',
        database: (o as any)?.database || 'SQLite',
        uptime: (o as any)?.uptime || '—',
      });
      if (q) setQuick(q as unknown as QuickOverview);
    } catch { /* ignore */ }
  }, []);

  const fetchOrgans = useCallback(async () => {
    try {
      const data = await apiFetch('/api/config/organs');
      setOrgans(data.organs || []);
    } catch { /* ignore */ }
  }, []);

  const fetchConfig = useCallback(async () => {
    try {
      const data = await apiFetch('/api/config');
      setConfig(data);
      setConfigText(JSON.stringify(data, null, 2));
    } catch { /* ignore */ }
  }, []);

  const fetchBootstrap = useCallback(async () => {
    try {
      const data = await apiFetch('/api/system/bootstrap/status');
      setBootstrap(data);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      await Promise.allSettled([fetchOverview(), fetchOrgans(), fetchConfig(), fetchBootstrap()]);
      setLoading(false);
    };
    load();
  }, [fetchOverview, fetchOrgans, fetchConfig, fetchBootstrap]);

  const toggleOrgan = async (key: string, current: boolean) => {
    try {
      await apiFetch(`/api/config/organs/${key}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !current }),
      });
      fetchOrgans();
    } catch (e: any) { setError(e.message); }
  };

  const saveConfig = async () => {
    setSaving(true);
    try {
      const parsed = JSON.parse(configText);
      await apiFetch('/api/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(parsed),
      });
      setConfig(parsed);
    } catch (e: any) {
      setError(e.message.includes('JSON') ? 'JSON 格式错误' : e.message);
    } finally { setSaving(false); }
  };

  const checkOrganHealth = async () => {
    setHealthLoading(true);
    try {
      const data = await apiFetch('/api/diagnostics/check-all');
      setOrganHealth(data.results || []);
    } catch (e: any) {
      setError(e.message);
    } finally { setHealthLoading(false); }
  };

  const runBootstrap = async () => {
    if (!confirm('确定要执行系统初始化？这可能需要几分钟。')) return;
    setBootstrapping(true);
    try {
      await apiFetch('/api/system/bootstrap/run', { method: 'POST' });
      await fetchBootstrap();
    } catch (e: any) {
      setError(e.message);
    } finally { setBootstrapping(false); }
  };

  const configDiff = async () => {
    try {
      const data = await apiFetch('/api/diagnostics/config-diff');
      alert(JSON.stringify(data, null, 2));
    } catch (e: any) { setError(e.message); }
  };

  if (loading) return <div className="flex items-center justify-center py-20"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /><span className="ml-2 text-sm text-muted-foreground">加载系统信息...</span></div>;

  const enabledCount = organs.filter(o => o.enabled).length;
  const healthyOrganCount = organHealth.filter(o => o.status === 'ok').length;
  const totalOrganCount = organHealth.length;

  return (
    <div className="space-y-4">
      {/* ── System Status Banner ── */}
      <div className={`rounded-xl border p-4 ${info?.status === 'ok' ? 'border-emerald-500/30 bg-green-500/5' : 'border-red-500/30 bg-red-500/5'}`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className={`w-3 h-3 rounded-full ${info?.status === 'ok' ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`} />
            <div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">OpenSoul 系统</span>
                <StatusBadge status={info?.status || 'unknown'} />
              </div>
              <div className="flex items-center gap-4 mt-1 text-[10px] text-muted-foreground">
                <span>版本 {info?.version}</span>
                <span>数据库 {info?.database}</span>
                <span className="flex items-center gap-1"><Clock className="w-2.5 h-2.5" />运行 {info?.uptime}</span>
              </div>
            </div>
          </div>
          <Button onClick={() => { fetchOverview(); fetchOrgans(); fetchBootstrap(); }} variant="secondary">
            <RefreshCw className="w-3.5 h-3.5" />刷新
          </Button>
        </div>
      </div>

      {/* ── Quick Stats ── */}
      {quick && (
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
          {[
            { label: '器官', value: `${quick.organs_healthy}/${quick.organs_total}`, icon: <Cpu className="w-3.5 h-3.5 text-blue-500" /> },
            { label: '知识条目', value: quick.knowledge_entries, icon: <Database className="w-3.5 h-3.5 text-purple-500" /> },
            { label: '活跃Agent', value: quick.active_agents, icon: <Activity className="w-3.5 h-3.5 text-green-500" /> },
            { label: '近期会话', value: quick.recent_sessions, icon: <Globe className="w-3.5 h-3.5 text-amber-500" /> },
            { label: 'CPU', value: `${(quick.cpu_percent || 0).toFixed(1)}%`, icon: <Cpu className="w-3.5 h-3.5 text-cyan-500" /> },
            { label: '内存', value: `${quick.memory_mb || 0} MB`, icon: <HardDrive className="w-3.5 h-3.5 text-pink-500" /> },
            { label: '已启用', value: `${enabledCount}/${organs.length}`, icon: <ToggleRight className="w-3.5 h-3.5 text-emerald-500" /> },
            { label: '健康检查', value: totalOrganCount > 0 ? `${healthyOrganCount}/${totalOrganCount}` : '—', icon: <Shield className="w-3.5 h-3.5 text-red-400" /> },
          ].map((s, i) => (
            <div key={i} className="rounded-lg border border-border bg-card p-2.5">
              <div className="flex items-center gap-1.5 mb-1">{s.icon}<span className="text-[10px] text-muted-foreground">{s.label}</span></div>
              <div className="text-lg font-bold">{s.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* ── Error ── */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* ── Tab Bar ── */}
      <div className="flex items-center gap-1 border-b border-border pb-0">
        {([
          { key: 'overview' as Tab, label: '总览', icon: <BarChart3 className="w-3.5 h-3.5" /> },
          { key: 'organs' as Tab, label: '器官管理', icon: <Cpu className="w-3.5 h-3.5" /> },
          { key: 'bootstrap' as Tab, label: '初始化', icon: <Play className="w-3.5 h-3.5" /> },
          { key: 'config' as Tab, label: '配置', icon: <Settings className="w-3.5 h-3.5" /> },
        ]).map(t => (
          <Button
            key={t.key}
            onClick={() => setTab(t.key)}
            variant="tabline" className={`font-medium  ${
              tab === t.key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {t.icon}{t.label}
          </Button>
        ))}
      </div>

      {/* ── Overview Tab ── */}
      {tab === 'overview' && (
        <div className="space-y-4">
          {/* Organ Health Grid */}
          <div className="rounded-xl border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Cpu className="w-4 h-4 text-blue-500" />
                <span className="text-sm font-medium">器官健康状态</span>
                {totalOrganCount > 0 && <span className="text-[10px] text-muted-foreground">({healthyOrganCount}/{totalOrganCount} 正常)</span>}
              </div>
              <Button onClick={checkOrganHealth} disabled={healthLoading} variant="secondary" className="px-2.5 py-1">
                {healthLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}
                {healthLoading ? '检测中...' : '健康检查'}
              </Button>
            </div>
            {totalOrganCount === 0 ? (
              <div className="text-center py-8 text-muted-foreground text-xs">
                点击"健康检查"按钮检测所有器官状态
              </div>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
                {organHealth.map(o => (
                  <div key={o.key} className={`rounded-lg border p-2 transition-colors cursor-pointer ${
                    o.status === 'ok' ? 'border-emerald-500/30 bg-green-500/5' :
                    o.status === 'error' ? 'border-red-500/30 bg-red-500/5' :
                    o.status === 'timeout' ? 'border-amber-500/30 bg-amber-500/5' :
                    'border-border bg-card'
                  }`} onClick={() => setExpandedOrgan(expandedOrgan === o.key ? null : o.key)}>
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-mono truncate">{o.key}</span>
                      {o.status === 'ok' ? <CheckCircle className="w-3 h-3 text-green-500 shrink-0" /> :
                       o.status === 'error' ? <XCircle className="w-3 h-3 text-red-500 shrink-0" /> :
                       <AlertTriangle className="w-3 h-3 text-amber-500 shrink-0" />}
                    </div>
                    {o.response_ms != null && (
                      <div className="text-[9px] text-muted-foreground mt-0.5">{o.response_ms}ms</div>
                    )}
                    {expandedOrgan === o.key && o.data && (
                      <pre className="text-[9px] text-muted-foreground mt-1 bg-muted rounded p-1 max-h-[60px] overflow-auto">
                        {JSON.stringify(o.data, null, 1).slice(0, 200)}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Quick Overview Details */}
          {quick && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="rounded-xl border border-border bg-card p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Activity className="w-4 h-4 text-green-500" />
                  <span className="text-sm font-medium">运行时信息</span>
                </div>
                <div className="space-y-2 text-xs">
                  {[
                    { label: '版本', value: quick.version },
                    { label: '运行时间', value: formatUptime(parseInt(quick.uptime) || 0) },
                    { label: 'CPU 使用率', value: `${(quick.cpu_percent || 0).toFixed(1)}%` },
                    { label: '内存使用', value: `${quick.memory_mb || 0} MB` },
                  ].map((item, i) => (
                    <div key={i} className="flex items-center justify-between py-1 border-b border-border/50 last:border-0">
                      <span className="text-muted-foreground">{item.label}</span>
                      <span className="font-mono">{item.value}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="rounded-xl border border-border bg-card p-4">
                <div className="flex items-center gap-2 mb-3">
                  <Database className="w-4 h-4 text-purple-500" />
                  <span className="text-sm font-medium">数据统计</span>
                </div>
                <div className="space-y-2 text-xs">
                  {[
                    { label: '知识条目', value: quick.knowledge_entries },
                    { label: '活跃 Agent', value: quick.active_agents },
                    { label: '近期会话', value: quick.recent_sessions },
                    { label: '已启用器官', value: `${enabledCount} / ${organs.length}` },
                  ].map((item, i) => (
                    <div key={i} className="flex items-center justify-between py-1 border-b border-border/50 last:border-0">
                      <span className="text-muted-foreground">{item.label}</span>
                      <span className="font-mono">{item.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Organs Tab ── */}
      {tab === 'organs' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">共 {organs.length} 个器官，{enabledCount} 个已启用</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {organs.map((organ) => (
              <div
                key={organ.key}
                className={`rounded-lg border bg-card p-4 transition-colors ${
                  organ.enabled ? 'border-emerald-500/30' : 'border-border opacity-60'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full ${organ.enabled ? 'bg-green-500' : 'bg-gray-400'}`} />
                    <span className="text-sm font-medium font-mono">{organ.key}</span>
                  </div>
                  <Button variant="bare"
                    onClick={() => toggleOrgan(organ.key, organ.enabled)}
                    className="p-0.5"
                    title={organ.enabled ? '点击禁用' : '点击启用'}
                  >
                    {organ.enabled ? (
                      <ToggleRight className="w-7 h-7 text-green-500" />
                    ) : (
                      <ToggleLeft className="w-7 h-7 text-muted-foreground" />
                    )}
                  </Button>
                </div>
                {Object.keys(organ.config).length > 0 && (
                  <div className="mt-2">
                    <Button variant="bare"
                      onClick={() => setExpandedOrgan(expandedOrgan === organ.key ? null : organ.key)}
                      className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
                    >
                      {expandedOrgan === organ.key ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                      配置 ({Object.keys(organ.config).length} 项)
                    </Button>
                    {expandedOrgan === organ.key && (
                      <pre className="mt-1 text-[10px] text-muted-foreground font-mono bg-muted rounded p-2 max-h-[120px] overflow-auto">
                        {JSON.stringify(organ.config, null, 2)}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            ))}
            {organs.length === 0 && (
              <div className="col-span-full flex flex-col items-center justify-center py-12 text-muted-foreground">
                <Cpu className="w-10 h-10 mb-2 opacity-30" />
                <p className="text-xs">暂无器官配置</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Bootstrap Tab ── */}
      {tab === 'bootstrap' && (
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Play className="w-4 h-4 text-blue-500" />
                <span className="text-sm font-medium">系统初始化</span>
                {bootstrap && <StatusBadge status={bootstrap.initialized ? 'ok' : 'uninitialized'} />}
              </div>
              <Button onClick={runBootstrap}
                disabled={bootstrapping} variant="primary" className="gap-1.5">
                {bootstrapping ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
                {bootstrapping ? '执行中...' : '执行初始化'}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground mb-3">
              初始化将创建必要的数据库表、默认配置和基础数据。幂等操作，可重复执行。
            </p>
            {bootstrap?.steps && bootstrap.steps.length > 0 && (
              <div className="space-y-1.5">
                {bootstrap.steps.map((step, i) => (
                  <div key={i} className="flex items-center gap-3 p-2 rounded-lg bg-muted/30">
                    {step.status === 'ok' ? <CheckCircle className="w-4 h-4 text-green-500 shrink-0" /> :
                     step.status === 'error' ? <XCircle className="w-4 h-4 text-red-500 shrink-0" /> :
                     <Clock className="w-4 h-4 text-muted-foreground shrink-0" />}
                    <div className="flex-1 min-w-0">
                      <span className="text-xs font-medium">{step.name}</span>
                      {step.message && <p className="text-[10px] text-muted-foreground truncate">{step.message}</p>}
                    </div>
                    <StatusBadge status={step.status} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Config Tab ── */}
      {tab === 'config' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">直接编辑系统配置 JSON（谨慎操作）</p>
            <div className="flex items-center gap-2">
              <Button onClick={configDiff} variant="secondary" className="gap-1">
                <Terminal className="w-3 h-3" />配置差异
              </Button>
              <Button onClick={saveConfig}
                disabled={saving} variant="primary" className="gap-1.5">
                <Save className="w-3.5 h-3.5" />{saving ? '保存中...' : '保存配置'}
              </Button>
            </div>
          </div>
          <textarea
            className="w-full h-[500px] p-4 text-xs font-mono bg-muted border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-primary/50 resize-y"
            value={configText}
            onChange={(e) => setConfigText(e.target.value)}
            spellCheck={false}
          />
        </div>
      )}

      {/* ── Footer ── */}
      <div className="text-[10px] text-muted-foreground text-center">
        OpenSystem · 系统管理 · 器官配置 / 健康检查 / 初始化 / 运行时配置
      </div>
    </div>
  );
}
