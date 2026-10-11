'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Activity, Server, BookOpen, Users, Cpu, HardDrive, MemoryStick,
  Zap, Puzzle, Clock, RefreshCw, ArrowRight, CheckCircle, XCircle,
  AlertTriangle, TrendingUp, Database, Brain, GitBranch, Lightbulb,
  MessageSquare, Shield, Timer, BarChart3, Layers, Gauge, Workflow,
} from 'lucide-react';
import { Badge, TONES, tone, type ToneName } from '@/components/ui/badge';
import { Button } from '../components/ui/button';

// ── Types ───────────────────────────────────────────────────────

interface OrganHealth {
  organs: Record<string, string>;
  healthy_count: number;
  error_count: number;
  total_count: number;
  status: string;
  latency: {
    avg_ms: number;
    slowest: Array<{ organ: string; ms: number }>;
  };
}

interface SystemMetrics {
  cpu_percent: number;
  memory: { total_mb: number; used_mb: number; percent: number };
  disk: { total_gb: number; used_gb: number; percent: number };
}

interface OverviewData {
  timestamp: number;
  elapsed_ms: number;
  version: string;
  system_status: string;
  organs: OrganHealth;
  metrics: SystemMetrics;
  knowledge: { total_entries: number };
  plugins: { total_plugins: number; active_plugins: number };
  gland: { total_tokens: number; call_count: number; by_model: Record<string, any> };
}

interface KnowledgeStats {
  total_entries: number;
  recent_24h: number;
  top_users: Array<{ user_id: string; count: number }>;
}

interface FeedbackStats {
  total: number;
  by_type: Record<string, number>;
  by_confidence: Record<string, number>;
}

// ── Helpers ─────────────────────────────────────────────────────

function formatNumber(n: number | undefined): string {
  if (n === undefined || n === null || n < 0) return '—';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toString();
}

function formatBytes(mb: number): string {
  if (mb >= 1024) return (mb / 1024).toFixed(1) + ' GB';
  return mb + ' MB';
}

function statusTone(status: string): ToneName {
  switch (status) {
    case 'ok': return 'success';
    case 'degraded': return 'warning';
    case 'critical': return 'destructive';
    case 'error': return 'destructive';
    default: return 'muted';
  }
}

function statusColor(status: string) {
  return TONES[statusTone(status)].text;
}

function statusBg(status: string) {
  return TONES[statusTone(status)].fill;
}

function statusLabel(status: string) {
  switch (status) {
    case 'ok': return '正常';
    case 'degraded': return '降级';
    case 'critical': return '严重';
    case 'error': return '异常';
    default: return '未知';
  }
}

function percentColor(pct: number) {
  if (pct >= 90) return 'text-red-400';
  if (pct >= 70) return 'text-amber-400';
  return 'text-emerald-400';
}

function percentBarColor(pct: number) {
  if (pct >= 90) return 'bg-red-500';
  if (pct >= 70) return 'bg-amber-500';
  return 'bg-emerald-500';
}

// ── Stat Card ───────────────────────────────────────────────────

function StatCard({
  icon: Icon, label, value, sub, color = 'text-foreground', onClick,
}: {
  icon: typeof Activity; label: string; value: string | number; sub?: string;
  color?: string; onClick?: () => void;
}) {
  return (
    <Button variant="bare"
      onClick={onClick}
      disabled={!onClick}
      className={`rounded-xl border border-border bg-card p-4 text-left transition-all ${onClick ? 'hover:border-primary/50 hover:shadow-md cursor-pointer' : ''}`}
    >
      <div className="flex items-center gap-2 text-muted-foreground text-xs mb-2">
        <Icon className="w-3.5 h-3.5" />
        <span>{label}</span>
      </div>
      <div className={`text-2xl font-bold ${color}`}>{value}</div>
      {sub && <div className="text-xs text-muted-foreground mt-1">{sub}</div>}
    </Button>
  );
}

// ── Progress Bar ────────────────────────────────────────────────

function ProgressBar({ value, max = 100, label, showPercent = true }: {
  value: number; max?: number; label?: string; showPercent?: boolean;
}) {
  const pct = Math.min(100, Math.round((value / max) * 100));
  return (
    <div className="space-y-1">
      {label && (
        <div className="flex justify-between text-xs">
          <span className="text-muted-foreground">{label}</span>
          {showPercent && <span className={percentColor(pct)}>{pct}%</span>}
        </div>
      )}
      <div className="h-2 rounded-full bg-muted/50 overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-700 ${percentBarColor(pct)}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ── WikiSkill Pipeline Visualization ────────────────────────────

function WikiSkillPipeline() {
  const stages = [
    { icon: MessageSquare, label: 'Raw层', sub: '执行轨迹', color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/30', desc: 'Session历史、Feedback提取' },
    { icon: Lightbulb, label: 'Wiki层', sub: '累积知识', color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/30', desc: '知识/模式/决策/反馈' },
    { icon: Zap, label: 'Skill层', sub: '技能进化', color: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', desc: '可逆技能更新' },
  ];

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-center gap-2 mb-4">
        <Brain className="w-4 h-4 text-primary" />
        <h3 className="text-sm font-medium">WikiSkill 三层架构</h3>
        <span className="text-xs text-muted-foreground ml-auto">自我进化知识大脑</span>
      </div>
      <div className="flex items-stretch gap-2">
        {stages.map((s, i) => (
          <div key={s.label} className="flex-1 flex items-center">
            <div className={`flex-1 rounded-lg border ${s.border} ${s.bg} p-3`}>
              <div className="flex items-center gap-2 mb-1.5">
                <s.icon className={`w-4 h-4 ${s.color}`} />
                <span className={`text-sm font-medium ${s.color}`}>{s.label}</span>
              </div>
              <div className="text-xs text-muted-foreground font-medium mb-1">{s.sub}</div>
              <div className="text-[11px] text-muted-foreground/70">{s.desc}</div>
            </div>
            {i < stages.length - 1 && (
              <ArrowRight className="w-5 h-5 text-muted-foreground/40 mx-1 shrink-0" />
            )}
          </div>
        ))}
      </div>
      <div className="flex items-center justify-center mt-3">
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground/60">
          <RefreshCw className="w-3 h-3" />
          <span>持续循环：提取 → 发现 → 更新 → 验证</span>
        </div>
      </div>
    </div>
  );
}

// ── Organ Health Grid ───────────────────────────────────────────

function OrganHealthGrid({ organs }: { organs: OrganHealth }) {
  const [expanded, setExpanded] = useState(false);
  const entries = Object.entries(organs.organs || {});
  const okEntries = entries.filter(([, s]) => s === 'ok');
  const errEntries = entries.filter(([, s]) => s !== 'ok');
  const sorted = [...errEntries, ...okEntries];
  const display = expanded ? sorted : sorted.slice(0, 24);

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Server className="w-4 h-4 text-primary" />
          <h3 className="text-sm font-medium">器官状态</h3>
          <Badge variant="bare" className={tone(statusTone(organs.status))}>
            {statusLabel(organs.status)}
          </Badge>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1"><CheckCircle className="w-3 h-3 text-emerald-400" />{organs.healthy_count}</span>
          {organs.error_count > 0 && <span className="flex items-center gap-1"><XCircle className="w-3 h-3 text-red-400" />{organs.error_count}</span>}
          <span>avg {organs.latency?.avg_ms?.toFixed(0)}ms</span>
        </div>
      </div>
      <div className="grid grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-1.5">
        {display.map(([name, status]) => (
          <div
            key={name}
            className={`flex items-center gap-1.5 px-2 py-1.5 rounded text-xs transition-colors ${
              status === 'ok' ? 'bg-muted/20 text-muted-foreground' : 'bg-red-500/10 text-red-400'
            }`}
          >
            <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusBg(status)}`} />
            <span className="truncate">{name}</span>
          </div>
        ))}
      </div>
      {sorted.length > 24 && (
        <Button variant="bare"
          onClick={() => setExpanded(!expanded)}
          className="mt-2 text-xs lg:text-sm text-primary hover:underline"
        >
          {expanded ? '收起' : `展开全部 ${sorted.length} 个`}
        </Button>
      )}
      {organs.latency?.slowest?.length > 0 && (
        <div className="mt-3 pt-3 border-t border-border/50">
          <div className="text-xs text-muted-foreground mb-1.5">响应最慢：</div>
          <div className="flex flex-wrap gap-2">
            {organs.latency.slowest.slice(0, 3).map((s) => (
              <Badge key={s.organ} variant="warning">
                {s.organ} {s.ms.toFixed(0)}ms
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── System Resources ────────────────────────────────────────────

function SystemResources({ metrics }: { metrics: SystemMetrics }) {
  if (!metrics || metrics.cpu_percent === undefined) return null;

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-center gap-2 mb-4">
        <Gauge className="w-4 h-4 text-primary" />
        <h3 className="text-sm font-medium">系统资源</h3>
      </div>
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <Cpu className="w-4 h-4 text-blue-400 shrink-0" />
          <div className="flex-1">
            <ProgressBar value={metrics.cpu_percent} label="CPU" />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <MemoryStick className="w-4 h-4 text-purple-400 shrink-0" />
          <div className="flex-1">
            <ProgressBar
              value={metrics.memory.used_mb}
              max={metrics.memory.total_mb}
              label={`内存 ${formatBytes(metrics.memory.used_mb)} / ${formatBytes(metrics.memory.total_mb)}`}
            />
          </div>
        </div>
        <div className="flex items-center gap-3">
          <HardDrive className="w-4 h-4 text-amber-400 shrink-0" />
          <div className="flex-1">
            <ProgressBar
              value={metrics.disk.used_gb}
              max={metrics.disk.total_gb}
              label={`磁盘 ${metrics.disk.used_gb}GB / ${metrics.disk.total_gb}GB`}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Model Gateway Stats ─────────────────────────────────────────

function GatewayStats({ gland }: { gland: { total_tokens: number; call_count: number; by_model: Record<string, any> } }) {
  const models = Object.entries(gland.by_model || {});
  const totalTokens = gland.total_tokens || 0;
  const callCount = gland.call_count || 0;

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-center gap-2 mb-4">
        <Cpu className="w-4 h-4 text-primary" />
        <h3 className="text-sm font-medium">模型网关</h3>
      </div>
      <div className="grid grid-cols-2 gap-3 mb-3">
        <div className="text-center p-2 rounded bg-muted/20">
          <div className="text-lg font-bold">{formatNumber(totalTokens)}</div>
          <div className="text-[11px] text-muted-foreground">总Token</div>
        </div>
        <div className="text-center p-2 rounded bg-muted/20">
          <div className="text-lg font-bold">{formatNumber(callCount)}</div>
          <div className="text-[11px] text-muted-foreground">调用次数</div>
        </div>
      </div>
      {models.length > 0 && (
        <div className="space-y-1.5">
          {models.slice(0, 5).map(([name, info]) => {
            const tokens = typeof info === 'object' ? info.tokens || 0 : 0;
            const calls = typeof info === 'object' ? info.calls || 0 : 0;
            return (
              <div key={name} className="flex items-center justify-between text-xs px-2 py-1 rounded bg-muted/10">
                <span className="text-muted-foreground truncate max-w-[120px]">{name}</span>
                <span className="text-foreground font-mono">{formatNumber(tokens)} tok / {calls}次</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Quick Links ─────────────────────────────────────────────────

function QuickLinks() {
  const links = [
    { label: '知识库', path: '/knowledge', icon: BookOpen, desc: '管理知识条目' },
    { label: '技能进化', path: '/skills', icon: Zap, desc: '技能版本与回滚' },
    { label: '执行轨迹', path: '/traces', icon: Activity, desc: '不可变执行记录' },
    { label: '模式发现', path: '/patterns', icon: Lightbulb, desc: '问题→方案模式' },
    { label: '进化流水线', path: '/evolution', icon: GitBranch, desc: '迭代验证循环' },
    { label: '知识大脑', path: '/brain', icon: Brain, desc: '知识图谱可视化' },
  ];

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-center gap-2 mb-4">
        <Workflow className="w-4 h-4 text-primary" />
        <h3 className="text-sm font-medium">快速导航</h3>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
        {links.map((l) => (
          <a
            key={l.path}
            href={l.path}
            className="flex items-center gap-2.5 p-2.5 rounded-lg bg-muted/20 hover:bg-muted/40 transition-colors group"
          >
            <l.icon className="w-4 h-4 text-muted-foreground group-hover:text-primary transition-colors" />
            <div>
              <div className="text-xs font-medium">{l.label}</div>
              <div className="text-[10px] text-muted-foreground">{l.desc}</div>
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}

// ── Main Dashboard ──────────────────────────────────────────────

export default function DashboardPage() {
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [kbStats, setKbStats] = useState<KnowledgeStats | null>(null);
  const [fbStats, setFbStats] = useState<FeedbackStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError('');

      // Fetch overview (includes organs, metrics, knowledge count, plugins, gateway)
      const ov = await apiFetch('/api/system/overview') as OverviewData;
      setOverview(ov);

      // Fetch detailed knowledge stats
      try {
        const ks = await apiFetch('/api/knowledge/stats') as KnowledgeStats;
        setKbStats(ks);
      } catch { /* optional */ }

      // Fetch feedback stats (needs user_id, try without)
      try {
        const fs = await apiFetch('/api/feedback/stats?user_id=00000000-0000-0000-0000-000000000001') as FeedbackStats;
        setFbStats(fs);
      } catch { /* optional */ }

      setLastRefresh(new Date());
    } catch (e: any) {
      setError(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Auto-refresh every 60s
  useEffect(() => {
    const timer = setInterval(fetchData, 60000);
    return () => clearInterval(timer);
  }, [fetchData]);

  if (loading && !overview) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="flex items-center gap-2 text-muted-foreground">
          <RefreshCw className="w-4 h-4 animate-spin" />
          <span className="text-sm">加载系统概览...</span>
        </div>
      </div>
    );
  }

  if (error && !overview) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center space-y-3">
          <AlertTriangle className="w-8 h-8 text-red-400 mx-auto" />
          <div className="text-sm text-red-400">{error}</div>
          <Button variant="bare" onClick={fetchData} className="text-xs lg:text-sm text-primary hover:underline">重试</Button>
        </div>
      </div>
    );
  }

  const organs = overview?.organs;
  const metrics = overview?.metrics;
  const knowledge = overview?.knowledge;
  const plugins = overview?.plugins;
  const gland = overview?.gland;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold">OpenSoul 系统总览</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            自我进化的知识大脑 · v{overview?.version || '—'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[11px] text-muted-foreground">
            <Clock className="w-3 h-3 inline mr-1" />
            {lastRefresh.toLocaleTimeString('zh-CN')}
          </span>
          <Button
            onClick={fetchData}
            disabled={loading}
            variant="chip-sm" className="flex items-center gap-1.5 bg-muted/50 hover:bg-muted transition-colors"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        </div>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatCard
          icon={Activity}
          label="系统状态"
          value={statusLabel(overview?.system_status || 'unknown')}
          sub={`v${overview?.version || '—'}`}
          color={statusColor(overview?.system_status || '')}
          onClick={() => window.location.href = '/monitoring'}
        />
        <StatCard
          icon={Server}
          label="器官健康"
          value={`${organs?.healthy_count ?? 0}/${organs?.total_count ?? 0}`}
          sub={`${organs?.error_count ?? 0} 异常`}
          color={organs?.error_count ? 'text-amber-400' : 'text-emerald-400'}
          onClick={() => window.location.href = '/monitoring'}
        />
        <StatCard
          icon={BookOpen}
          label="知识库"
          value={formatNumber(kbStats?.total_entries ?? knowledge?.total_entries)}
          sub={kbStats?.recent_24h ? `+${kbStats.recent_24h} 今日` : '条目'}
          onClick={() => window.location.href = '/knowledge'}
        />
        <StatCard
          icon={MessageSquare}
          label="反馈"
          value={formatNumber(fbStats?.total)}
          sub={fbStats?.by_type ? `${Object.keys(fbStats.by_type).length} 类型` : '条'}
          onClick={() => window.location.href = '/feedback'}
        />
        <StatCard
          icon={Puzzle}
          label="插件"
          value={plugins?.total_plugins ?? 0}
          sub={`${plugins?.active_plugins ?? 0} 活跃`}
          onClick={() => window.location.href = '/plugins'}
        />
        <StatCard
          icon={Zap}
          label="Token消耗"
          value={formatNumber(gland?.total_tokens)}
          sub={`${formatNumber(gland?.call_count)} 次调用`}
          onClick={() => window.location.href = '/gland'}
        />
      </div>

      {/* WikiSkill Pipeline */}
      <WikiSkillPipeline />

      {/* Two-column: Organ Health + System Resources */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {organs && <OrganHealthGrid organs={organs} />}
        {metrics && <SystemResources metrics={metrics} />}
      </div>

      {/* Two-column: Gateway Stats + Quick Links */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {gland && <GatewayStats gland={gland} />}
        <QuickLinks />
      </div>

      {/* Response time */}
      {overview?.elapsed_ms !== undefined && (
        <div className="text-center text-[11px] text-muted-foreground/50">
          数据聚合耗时 {overview.elapsed_ms}ms · 器官延迟 {organs?.latency?.avg_ms?.toFixed(0)}ms avg
        </div>
      )}
    </div>
  );
}
