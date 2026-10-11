'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  RefreshCw, X, Activity, Cpu, MemoryStick, HardDrive, Wifi,
  CheckCircle, XCircle, AlertTriangle, Clock, Zap, TrendingUp,
  TrendingDown, Minus, Loader2, BarChart3, Heart, Shield, Bell,
  Eye, EyeOff, ChevronDown, ChevronRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

// ── Types ───────────────────────────────────────────────────

interface VitalStats {
  status: string;
  component: string;
  system: {
    cpu_percent: number;
    memory_percent: number;
    memory_used_mb: number;
    memory_total_mb: number;
    disk_percent: number;
    disk_used_gb: number;
    disk_total_gb: number;
  };
  app: {
    request_qps: number;
    latency_p99_ms: number;
    error_rate: number;
    total_requests: number;
    total_errors: number;
  };
  health: {
    status: string;
    component_count: number;
    healthy: number;
  };
  alerts: {
    total: number;
    active: number;
  };
}

interface HealthComponent {
  name: string;
  status: string;
  latency_ms: number;
  message: string;
}

interface HealthReport {
  status: string;
  components: HealthComponent[];
  ts: string;
}

interface Alert {
  rule: string;
  severity: string;
  message: string;
  value: number;
  threshold: number;
  resolved: boolean;
  ts: string;
}

interface HistoryEntry {
  ts: string;
  cpu: number;
  mem: number;
  mem_mb: number;
  disk: number;
  qps: number;
  p99: number;
  err_rate: number;
  requests: number;
  errors: number;
  knowledge: number;
}

interface HistorySummary {
  cpu: { min: number; max: number; avg: number };
  memory: { min: number; max: number; avg: number };
  qps: { min: number; max: number; avg: number };
  p99: { min: number; max: number; avg: number };
  error_rate: { min: number; max: number; avg: number };
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch { return ts; }
}

function formatBytes(mb: number) {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${Math.round(mb)} MB`;
}

function formatUptime(seconds?: number) {
  if (!seconds) return '-';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}天${h}小时`;
  if (h > 0) return `${h}小时${m}分钟`;
  return `${m}分钟`;
}

function percentColor(pct: number) {
  if (pct > 80) return 'text-red-500';
  if (pct > 60) return 'text-orange-500';
  return 'text-green-500';
}

function percentBarColor(pct: number) {
  if (pct > 80) return 'bg-red-500';
  if (pct > 60) return 'bg-orange-500';
  return 'bg-green-500';
}

function severityColor(s: string) {
  if (s === 'critical' || s === 'error') return 'bg-red-500/10 text-red-500 border-red-500/30';
  if (s === 'warning' || s === 'warn') return 'bg-amber-500/10 text-amber-500 border-amber-500/30';
  return 'bg-blue-500/10 text-blue-500 border-blue-500/30';
}

// ── Mini Sparkline (SVG) ────────────────────────────────────

function Sparkline({ data, color = 'blue', height = 40, width = 200 }: {
  data: number[];
  color?: string;
  height?: number;
  width?: number;
}) {
  if (data.length < 2) return <div className="text-[10px] text-muted-foreground">数据不足</div>;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = width / (data.length - 1);

  const colorMap: Record<string, string> = {
    blue: '#3b82f6',
    green: '#22c55e',
    red: '#ef4444',
    amber: '#f59e0b',
    purple: '#a855f7',
  };
  const stroke = colorMap[color] || color;

  const points = data.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  }).join(' ');

  return (
    <svg width={width} height={height} className="overflow-visible">
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Last point dot */}
      {data.length > 0 && (() => {
        const lastX = (data.length - 1) * step;
        const lastY = height - ((data[data.length - 1] - min) / range) * (height - 4) - 2;
        return <circle cx={lastX} cy={lastY} r="2.5" fill={stroke} />;
      })()}
    </svg>
  );
}

// ── Resource Gauge ──────────────────────────────────────────

function ResourceGauge({ label, icon: Icon, percent, detail, color }: {
  label: string;
  icon: typeof Cpu;
  percent: number;
  detail: string;
  color: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
        <Icon className="w-3.5 h-3.5" />
        <span>{label}</span>
      </div>
      <div className="flex items-end justify-between mb-1">
        <span className={`text-xl font-bold ${percentColor(percent)}`}>{percent.toFixed(1)}%</span>
        <span className="text-[10px] text-muted-foreground">{detail}</span>
      </div>
      <div className="w-full bg-muted rounded-full h-2">
        <div
          className={`h-2 rounded-full transition-all ${percentBarColor(percent)}`}
          style={{ width: `${Math.min(100, percent)}%` }}
        />
      </div>
    </div>
  );
}

// ── Metric Card with Sparkline ──────────────────────────────

function MetricCard({ label, value, unit, trend, sparkData, sparkColor }: {
  label: string;
  value: string | number;
  unit?: string;
  trend?: 'up' | 'down' | 'flat';
  sparkData?: number[];
  sparkColor?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-muted-foreground">{label}</span>
        {trend && (
          trend === 'up' ? <TrendingUp className="w-3 h-3 text-red-400" /> :
          trend === 'down' ? <TrendingDown className="w-3 h-3 text-green-400" /> :
          <Minus className="w-3 h-3 text-muted-foreground" />
        )}
      </div>
      <div className="flex items-end gap-1">
        <span className="text-lg font-bold">{value}</span>
        {unit && <span className="text-[10px] text-muted-foreground mb-0.5">{unit}</span>}
      </div>
      {sparkData && sparkData.length > 0 && (
        <div className="mt-1.5">
          <Sparkline data={sparkData} color={sparkColor || 'blue'} height={28} width={140} />
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function VitalPage() {
  const [stats, setStats] = useState<VitalStats | null>(null);
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [summary, setSummary] = useState<HistorySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [historyMinutes, setHistoryMinutes] = useState(60);
  const [showResolvedAlerts, setShowResolvedAlerts] = useState(false);
  const [expandedAlert, setExpandedAlert] = useState<number | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError('');
    const results = await Promise.allSettled([
      apiFetch('/api/vital/stats'),
      apiFetch('/api/vital/health'),
      apiFetch('/api/vital/alerts'),
      apiFetch(`/api/vital/history?minutes=${historyMinutes}`),
      apiFetch('/api/vital/history/summary'),
    ]);

    const [sRes, hRes, aRes, histRes, sumRes] = results;
    if (sRes.status === 'fulfilled') setStats(sRes.value);
    if (hRes.status === 'fulfilled') setHealth(hRes.value);
    if (aRes.status === 'fulfilled') setAlerts(aRes.value.alerts || []);
    if (histRes.status === 'fulfilled') setHistory(histRes.value.data || []);
    if (sumRes.status === 'fulfilled') setSummary(sumRes.value);

    if (results.every(r => r.status === 'rejected')) {
      setError('无法连接到 OpenVital 服务');
    }
    setLastRefresh(new Date());
    setLoading(false);
  }, [historyMinutes]);

  useEffect(() => { fetchData(); }, [fetchData]);

  useEffect(() => {
    if (!autoRefresh) return;
    const iv = setInterval(fetchData, 10000);
    return () => clearInterval(iv);
  }, [autoRefresh, fetchData]);

  // Extract sparkline data from history
  const cpuHistory = history.map(h => h.cpu);
  const memHistory = history.map(h => h.mem);
  const qpsHistory = history.map(h => h.qps);
  const p99History = history.map(h => h.p99);
  const errRateHistory = history.map(h => h.err_rate);

  // Filter alerts
  const filteredAlerts = alerts.filter(a => showResolvedAlerts || !a.resolved);
  const activeAlerts = alerts.filter(a => !a.resolved);

  // Trend detection
  const getTrend = (data: number[]): 'up' | 'down' | 'flat' => {
    if (data.length < 5) return 'flat';
    const recent = data.slice(-5);
    const older = data.slice(-10, -5);
    if (older.length === 0) return 'flat';
    const recentAvg = recent.reduce((a, b) => a + b, 0) / recent.length;
    const olderAvg = older.reduce((a, b) => a + b, 0) / older.length;
    const diff = recentAvg - olderAvg;
    if (Math.abs(diff) < 0.5) return 'flat';
    return diff > 0 ? 'up' : 'down';
  };

  if (loading && !stats) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Header with auto-refresh ── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Heart className="w-5 h-5 text-red-500" />
          <h2 className="text-sm font-medium">生命体征</h2>
          <Badge variant="muted">OpenVital</Badge>
          {stats && (
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
              stats.health.status === 'ok' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-orange-500/10 text-orange-600'
            }`}>
              {stats.health.status === 'ok' ? '健康' : '降级'}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs cursor-pointer">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} className="rounded" />
            自动刷新(10s)
          </label>
          <select
            className="px-2 py-1 text-xs bg-muted border border-border rounded-md"
            value={historyMinutes}
            onChange={(e) => setHistoryMinutes(Number(e.target.value))}
          >
            <option value={15}>15分钟</option>
            <option value={30}>30分钟</option>
            <option value={60}>1小时</option>
            <option value={180}>3小时</option>
            <option value={360}>6小时</option>
            <option value={720}>12小时</option>
          </select>
          <Button onClick={fetchData} disabled={loading} variant="secondary">
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}刷新
          </Button>
        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* ── Resource Gauges ── */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <ResourceGauge
            label="CPU"
            icon={Cpu}
            percent={stats.system.cpu_percent}
            detail={`${history.length > 0 ? history[history.length - 1]?.cpu?.toFixed(1) : stats.system.cpu_percent.toFixed(1)}%`}
            color="blue"
          />
          <ResourceGauge
            label="内存"
            icon={MemoryStick}
            percent={stats.system.memory_percent}
            detail={`${formatBytes(stats.system.memory_used_mb)} / ${formatBytes(stats.system.memory_total_mb)}`}
            color="purple"
          />
          <ResourceGauge
            label="磁盘"
            icon={HardDrive}
            percent={stats.system.disk_percent}
            detail={`${stats.system.disk_used_gb?.toFixed(1)} / ${stats.system.disk_total_gb?.toFixed(1)} GB`}
            color="amber"
          />
        </div>
      )}

      {/* ── App Metrics with Sparklines ── */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <MetricCard
            label="请求 QPS"
            value={stats.app.request_qps?.toFixed(1) || '0'}
            unit="req/s"
            trend={getTrend(qpsHistory)}
            sparkData={qpsHistory}
            sparkColor="blue"
          />
          <MetricCard
            label="P99 延迟"
            value={stats.app.latency_p99_ms?.toFixed(0) || '0'}
            unit="ms"
            trend={getTrend(p99History)}
            sparkData={p99History}
            sparkColor="amber"
          />
          <MetricCard
            label="错误率"
            value={`${((stats.app.error_rate || 0) * 100).toFixed(2)}`}
            unit="%"
            trend={getTrend(errRateHistory)}
            sparkData={errRateHistory.map(v => v * 100)}
            sparkColor="red"
          />
          <MetricCard
            label="总请求数"
            value={stats.app.total_requests || 0}
          />
          <MetricCard
            label="总错误数"
            value={stats.app.total_errors || 0}
          />
        </div>
      )}

      {/* ── Health Components ── */}
      {health && (
        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center gap-2 p-3 border-b border-border">
            <Shield className="w-4 h-4 text-primary" />
            <span className="text-sm font-medium">组件健康</span>
            <span className="text-[10px] text-muted-foreground ml-auto">
              {health.components.filter(c => c.status === 'ok').length}/{health.components.length} 健康
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2 p-3">
            {health.components.map((comp) => (
              <div
                key={comp.name}
                className={`flex items-center gap-3 p-2.5 rounded-lg border ${
                  comp.status === 'ok' ? 'border-border bg-muted/30' : 'border-red-500/20 bg-red-500/5'
                }`}
              >
                {comp.status === 'ok' ? (
                  <CheckCircle className="w-4 h-4 text-green-500 shrink-0" />
                ) : (
                  <XCircle className="w-4 h-4 text-red-500 shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-medium truncate">{comp.name}</div>
                  {comp.message && (
                    <div className="text-[10px] text-muted-foreground truncate">{comp.message}</div>
                  )}
                </div>
                <span className="text-[10px] font-mono text-muted-foreground shrink-0">
                  {comp.latency_ms?.toFixed(0) || 0}ms
                </span>
              </div>
            ))}
            {health.components.length === 0 && (
              <div className="col-span-full text-xs text-muted-foreground text-center py-4">暂无组件数据</div>
            )}
          </div>
        </div>
      )}

      {/* ── Alerts ── */}
      <div className="rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 p-3 border-b border-border">
          <Bell className="w-4 h-4 text-amber-500" />
          <span className="text-sm font-medium">告警</span>
          {activeAlerts.length > 0 && (
            <Badge variant="destructive">{activeAlerts.length} 活跃</Badge>
          )}
          <label className="flex items-center gap-1 text-[10px] text-muted-foreground ml-auto cursor-pointer">
            <input type="checkbox" checked={showResolvedAlerts} onChange={(e) => setShowResolvedAlerts(e.target.checked)} className="rounded" />
            显示已解决
          </label>
        </div>
        <div className="p-3 space-y-1.5 max-h-[300px] overflow-auto">
          {filteredAlerts.length === 0 ? (
            <div className="text-xs text-muted-foreground text-center py-4">
              {alerts.length === 0 ? '暂无告警' : '所有告警已解决'}
            </div>
          ) : (
            filteredAlerts.map((alert, i) => (
              <div
                key={i}
                className={`rounded-lg border p-2.5 cursor-pointer hover:bg-muted/30 transition-colors ${
                  alert.resolved ? 'border-border opacity-60' : 'border-amber-500/20 bg-amber-500/5'
                }`}
                onClick={() => setExpandedAlert(expandedAlert === i ? null : i)}
              >
                <div className="flex items-center gap-2">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium border ${severityColor(alert.severity)}`}>
                    {alert.severity}
                  </span>
                  <span className="text-xs font-medium">{alert.rule}</span>
                  {alert.resolved && (
                    <Badge variant="success">已解决</Badge>
                  )}
                  <span className="text-[10px] text-muted-foreground ml-auto">{formatTime(alert.ts)}</span>
                  {expandedAlert === i ? <ChevronDown className="w-3 h-3 text-muted-foreground" /> : <ChevronRight className="w-3 h-3 text-muted-foreground" />}
                </div>
                {expandedAlert === i && (
                  <div className="mt-2 pt-2 border-t border-border text-xs text-muted-foreground space-y-1">
                    <p>{alert.message}</p>
                    <div className="flex items-center gap-4">
                      <span>当前值: <span className="font-mono">{alert.value?.toFixed(2)}</span></span>
                      <span>阈值: <span className="font-mono">{alert.threshold?.toFixed(2)}</span></span>
                    </div>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      {/* ── Metrics History Summary ── */}
      {summary && (
        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center gap-2 p-3 border-b border-border">
            <BarChart3 className="w-4 h-4 text-primary" />
            <span className="text-sm font-medium">指标统计 (最近 {historyMinutes} 分钟)</span>
          </div>
          <div className="p-3">
            <div className="grid grid-cols-5 gap-3 text-center">
              {[
                { key: 'cpu', label: 'CPU', unit: '%' },
                { key: 'memory', label: '内存', unit: '%' },
                { key: 'qps', label: 'QPS', unit: '' },
                { key: 'p99', label: 'P99延迟', unit: 'ms' },
                { key: 'error_rate', label: '错误率', unit: '%' },
              ].map(({ key, label, unit }) => {
                const s = (summary as any)[key];
                if (!s) return null;
                return (
                  <div key={key} className="space-y-1">
                    <div className="text-[10px] text-muted-foreground font-medium">{label}</div>
                    <div className="text-[10px] space-y-0.5">
                      <div className="flex items-center justify-between px-1">
                        <span className="text-muted-foreground">最小</span>
                        <span className="font-mono">{s.min?.toFixed(2)}{unit}</span>
                      </div>
                      <div className="flex items-center justify-between px-1">
                        <span className="text-muted-foreground">平均</span>
                        <span className="font-mono font-medium">{s.avg?.toFixed(2)}{unit}</span>
                      </div>
                      <div className="flex items-center justify-between px-1">
                        <span className="text-muted-foreground">最大</span>
                        <span className="font-mono">{s.max?.toFixed(2)}{unit}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* ── History Timeline ── */}
      {history.length > 0 && (
        <div className="rounded-lg border border-border bg-card">
          <div className="flex items-center gap-2 p-3 border-b border-border">
            <Clock className="w-4 h-4 text-primary" />
            <span className="text-sm font-medium">时序数据</span>
            <span className="text-[10px] text-muted-foreground ml-auto">{history.length} 个采样点</span>
          </div>
          {/* CPU + Memory sparklines */}
          <div className="grid grid-cols-2 gap-3 p-3">
            <div>
              <div className="text-[10px] text-muted-foreground mb-1">CPU 使用率趋势</div>
              <Sparkline data={cpuHistory} color="blue" height={50} width={300} />
            </div>
            <div>
              <div className="text-[10px] text-muted-foreground mb-1">内存使用率趋势</div>
              <Sparkline data={memHistory} color="purple" height={50} width={300} />
            </div>
            <div>
              <div className="text-[10px] text-muted-foreground mb-1">QPS 趋势</div>
              <Sparkline data={qpsHistory} color="green" height={50} width={300} />
            </div>
            <div>
              <div className="text-[10px] text-muted-foreground mb-1">P99 延迟趋势</div>
              <Sparkline data={p99History} color="amber" height={50} width={300} />
            </div>
          </div>
          {/* Recent data table */}
          <div className="px-3 pb-3 max-h-[200px] overflow-auto">
            <table className="w-full text-[10px]">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="text-left py-1 px-1 text-muted-foreground">时间</th>
                  <th className="text-right py-1 px-1 text-muted-foreground">CPU%</th>
                  <th className="text-right py-1 px-1 text-muted-foreground">内存%</th>
                  <th className="text-right py-1 px-1 text-muted-foreground">磁盘%</th>
                  <th className="text-right py-1 px-1 text-muted-foreground">QPS</th>
                  <th className="text-right py-1 px-1 text-muted-foreground">P99ms</th>
                  <th className="text-right py-1 px-1 text-muted-foreground">错误率</th>
                </tr>
              </thead>
              <tbody>
                {history.slice(-20).reverse().map((h, i) => (
                  <tr key={i} className="border-b border-border/50 hover:bg-muted/30">
                    <td className="py-0.5 px-1 font-mono">{formatTime(h.ts)}</td>
                    <td className={`text-right py-0.5 px-1 font-mono ${percentColor(h.cpu)}`}>{h.cpu?.toFixed(1)}</td>
                    <td className={`text-right py-0.5 px-1 font-mono ${percentColor(h.mem)}`}>{h.mem?.toFixed(1)}</td>
                    <td className={`text-right py-0.5 px-1 font-mono ${percentColor(h.disk)}`}>{h.disk?.toFixed(1)}</td>
                    <td className="text-right py-0.5 px-1 font-mono">{h.qps?.toFixed(1)}</td>
                    <td className="text-right py-0.5 px-1 font-mono">{h.p99?.toFixed(0)}</td>
                    <td className="text-right py-0.5 px-1 font-mono">{(h.err_rate * 100)?.toFixed(2)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Footer ── */}
      <div className="flex items-center justify-between text-[10px] text-muted-foreground">
        <span>OpenVital · 生命体征监控 · 系统健康 / 指标 / 告警 / 时序</span>
        {lastRefresh && <span>上次刷新: {lastRefresh.toLocaleTimeString('zh-CN')}</span>}
      </div>
    </div>
  );
}
