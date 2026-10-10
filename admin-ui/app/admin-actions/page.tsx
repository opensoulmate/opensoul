'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  RefreshCw, Trash2, Download, Activity, Shield, Play, Loader2,
  AlertCircle, CheckCircle, XCircle, X, Clock, Database, HardDrive,
  Server, Wrench, FileText, BarChart3, Zap, Settings,
} from 'lucide-react';

// ── Types ───────────────────────────────────────────────────

interface ActionResult {
  action: string;
  status?: string;
  results?: Record<string, { status: string; code?: number; error?: string; data?: any }>;
  cleared?: number;
  cleaned?: number;
  total?: number;
  data?: any;
}

interface OverviewData {
  timestamp: number;
  health: {
    status?: string;
    healthy?: number;
    total?: number;
    organs?: Record<string, { status: string; label: string }>;
  };
  stats: Record<string, any>;
}

interface ReportData {
  generated_at: number;
  platform: string;
  version: string;
  health: any;
  components: any;
  recent_events: any;
  stats: Record<string, any>;
  summary: {
    health_status: string;
    healthy_organs: number;
    total_organs: number;
    health_percentage: number;
    total_components: number;
  };
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: number) {
  if (!ts) return '-';
  try {
    return new Date(ts * 1000).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch { return String(ts); }
}

function ActionButton({ icon: Icon, label, sublabel, onClick, loading, variant = 'default' }: {
  icon: any;
  label: string;
  sublabel: string;
  onClick: () => void;
  loading: boolean;
  variant?: 'default' | 'danger' | 'warning';
}) {
  const colors = {
    default: 'bg-primary/10 text-primary hover:bg-primary/20 border-primary/20',
    danger: 'bg-red-500/10 text-red-500 hover:bg-red-500/20 border-red-500/20',
    warning: 'bg-amber-500/10 text-amber-500 hover:bg-amber-500/20 border-amber-500/20',
  };
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className={`flex flex-col items-center gap-2 p-4 rounded-lg border transition-colors disabled:opacity-50 ${colors[variant]}`}
    >
      {loading ? <Loader2 className="w-6 h-6 animate-spin" /> : <Icon className="w-6 h-6" />}
      <span className="text-xs font-medium">{label}</span>
      <span className="text-[10px] opacity-70">{sublabel}</span>
    </button>
  );
}

function ResultPanel({ result, onClose }: { result: ActionResult; onClose: () => void }) {
  const successCount = result.results
    ? Object.values(result.results).filter((r) => r.status === 'ok').length
    : 0;
  const failCount = result.results
    ? Object.values(result.results).filter((r) => r.status === 'error').length
    : 0;

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div className="flex items-center justify-between p-3 border-b border-border">
        <div className="flex items-center gap-2">
          {failCount > 0 ? (
            <AlertCircle className="w-4 h-4 text-amber-500" />
          ) : (
            <CheckCircle className="w-4 h-4 text-green-500" />
          )}
          <span className="text-sm font-medium">{result.action}</span>
          {result.cleared !== undefined && (
            <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-emerald-500/10 text-emerald-500">清除 {result.cleared}/{result.total}</span>
          )}
          {result.cleaned !== undefined && (
            <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-emerald-500/10 text-emerald-500">清理 {result.cleaned}/{result.total}</span>
          )}
        </div>
        <button onClick={onClose} className="p-1 rounded-md hover:bg-muted"><X className="w-4 h-4" /></button>
      </div>
      {result.results && (
        <div className="p-3 space-y-1">
          {Object.entries(result.results).map(([name, info]) => (
            <div key={name} className="flex items-center gap-2 text-xs">
              {info.status === 'ok' ? (
                <CheckCircle className="w-3 h-3 text-green-500 shrink-0" />
              ) : (
                <XCircle className="w-3 h-3 text-red-500 shrink-0" />
              )}
              <span className="font-medium">{name}</span>
              {info.error && <span className="text-red-400 truncate">{info.error}</span>}
            </div>
          ))}
        </div>
      )}
      {result.data && !result.results && (
        <div className="p-3 text-xs text-muted-foreground">
          <pre className="whitespace-pre-wrap break-all">{JSON.stringify(result.data, null, 2).slice(0, 1000)}</pre>
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function AdminActionsPage() {
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [report, setReport] = useState<ReportData | null>(null);
  const [results, setResults] = useState<ActionResult[]>([]);
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'actions' | 'overview' | 'report'>('actions');

  const runAction = useCallback(async (action: string, endpoint: string, method = 'POST', body?: any) => {
    try {
      setLoading((p) => ({ ...p, [action]: true }));
      setError('');
      const opts: RequestInit = { method };
      if (body) opts.body = JSON.stringify(body);
      const data = await apiFetch(`/api/admin/${endpoint}`, opts);
      setResults((prev) => [data as ActionResult, ...prev].slice(0, 20));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading((p) => ({ ...p, [action]: false }));
    }
  }, []);

  const fetchOverview = useCallback(async () => {
    try {
      setLoading((p) => ({ ...p, overview: true }));
      const data = await apiFetch('/api/admin/overview');
      setOverview(data as OverviewData);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading((p) => ({ ...p, overview: false }));
    }
  }, []);

  const fetchReport = useCallback(async () => {
    try {
      setLoading((p) => ({ ...p, report: true }));
      const data = await apiFetch('/api/admin/report');
      setReport(data as ReportData);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading((p) => ({ ...p, report: false }));
    }
  }, []);

  const exportConfig = useCallback(async () => {
    try {
      setLoading((p) => ({ ...p, export: true }));
      const data = await apiFetch('/api/admin/export/config');
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `opensoul-config-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading((p) => ({ ...p, export: false }));
    }
  }, []);

  const healthyOrgans = overview?.health?.organs
    ? Object.values(overview.health.organs).filter((o) => o.status === 'ok').length
    : 0;
  const totalOrgans = overview?.health?.organs ? Object.keys(overview.health.organs).length : 0;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Server className="w-3.5 h-3.5" />系统状态</div>
          <div className="text-2xl font-bold">{overview?.health?.status === 'ok' ? '正常' : overview?.health?.status || '-'}</div>
          <div className="text-[10px] text-muted-foreground">{healthyOrgans}/{totalOrgans} 器官</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Activity className="w-3.5 h-3.5" />操作记录</div>
          <div className="text-2xl font-bold">{results.length}</div>
          <div className="text-[10px] text-muted-foreground">本次会话</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Database className="w-3.5 h-3.5" />组件</div>
          <div className="text-2xl font-bold">{Object.keys(overview?.stats || {}).length}</div>
          <div className="text-[10px] text-muted-foreground">已注册</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1"><Clock className="w-3.5 h-3.5" />更新时间</div>
          <div className="text-xs font-medium">{overview ? formatTime(overview.timestamp) : '-'}</div>
          <button onClick={fetchOverview} className="text-[10px] text-primary mt-1 hover:underline">刷新</button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')}><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        {([['actions', '运维操作', Wrench], ['overview', '系统总览', BarChart3], ['report', '系统报告', FileText]] as const).map(([key, label, Icon]) => (
          <button
            key={key}
            onClick={() => {
              setTab(key);
              if (key === 'overview' && !overview) fetchOverview();
              if (key === 'report' && !report) fetchReport();
            }}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm border-b-2 transition-colors ${tab === key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
          >
            <Icon className="w-3.5 h-3.5" />{label}
          </button>
        ))}
      </div>

      {/* Tab: Actions */}
      {tab === 'actions' && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <ActionButton
              icon={Trash2}
              label="清除缓存"
              sublabel="清除所有组件缓存"
              onClick={() => runAction('clear_caches', 'caches/clear')}
              loading={!!loading['clear_caches']}
              variant="danger"
            />
            <ActionButton
              icon={HardDrive}
              label="清理过期"
              sublabel="清理过期数据"
              onClick={() => runAction('cleanup', 'cleanup')}
              loading={!!loading['cleanup']}
              variant="warning"
            />
            <ActionButton
              icon={Database}
              label="立即备份"
              sublabel="备份系统数据"
              onClick={() => runAction('backup', 'backup')}
              loading={!!loading['backup']}
            />
            <ActionButton
              icon={Download}
              label="导出配置"
              sublabel="下载JSON配置"
              onClick={exportConfig}
              loading={!!loading['export']}
            />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <ActionButton
              icon={BarChart3}
              label="系统总览"
              sublabel="加载总览数据"
              onClick={fetchOverview}
              loading={!!loading['overview']}
            />
            <ActionButton
              icon={FileText}
              label="系统报告"
              sublabel="生成完整报告"
              onClick={fetchReport}
              loading={!!loading['report']}
            />
            <ActionButton
              icon={Shield}
              label="健康检查"
              sublabel="全面健康诊断"
              onClick={() => runAction('health_check', 'overview', 'GET')}
              loading={!!loading['health_check']}
            />
            <ActionButton
              icon={Zap}
              label="连接测试"
              sublabel="测试Provider连通"
              onClick={() => runAction('test_connection', 'gland/test', 'POST')}
              loading={!!loading['test_connection']}
            />
          </div>

          {/* Results */}
          {results.length > 0 && (
            <div className="space-y-2">
              <div className="text-xs font-medium text-muted-foreground">操作结果</div>
              {results.map((r, i) => (
                <ResultPanel key={i} result={r} onClose={() => setResults((prev) => prev.filter((_, j) => j !== i))} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tab: Overview */}
      {tab === 'overview' && (
        <div className="space-y-3">
          {loading['overview'] ? (
            <div className="text-xs text-muted-foreground p-4 flex items-center gap-2"><Loader2 className="w-3 h-3 animate-spin" />加载中...</div>
          ) : overview ? (
            <>
              {/* Organ statuses */}
              {overview.health?.organs && (
                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium mb-3">器官状态</div>
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                    {Object.entries(overview.health.organs).map(([key, organ]) => (
                      <div key={key} className="flex items-center gap-2 p-2 rounded bg-muted/30">
                        {organ.status === 'ok' ? (
                          <CheckCircle className="w-3.5 h-3.5 text-green-500 shrink-0" />
                        ) : (
                          <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />
                        )}
                        <span className="text-xs truncate">{organ.label || key}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Component stats */}
              {overview.stats && Object.keys(overview.stats).length > 0 && (
                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium mb-3">组件统计</div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    {Object.entries(overview.stats).map(([name, stat]) => (
                      <div key={name} className="flex items-center gap-3 p-2 rounded bg-muted/30">
                        <Database className="w-3.5 h-3.5 text-primary shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="text-xs font-medium">{name}</div>
                          <div className="text-[10px] text-muted-foreground truncate">
                            {typeof stat === 'object' ? JSON.stringify(stat).slice(0, 80) : String(stat)}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="text-center py-12 text-muted-foreground">
              <BarChart3 className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-xs">点击"系统总览"加载数据</p>
            </div>
          )}
        </div>
      )}

      {/* Tab: Report */}
      {tab === 'report' && (
        <div className="space-y-3">
          {loading['report'] ? (
            <div className="text-xs text-muted-foreground p-4 flex items-center gap-2"><Loader2 className="w-3 h-3 animate-spin" />生成报告中...</div>
          ) : report ? (
            <>
              {/* Summary */}
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="text-xs font-medium mb-3">报告摘要</div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <div>
                    <div className="text-[10px] text-muted-foreground">健康状态</div>
                    <div className="text-sm font-bold">{report.summary.health_status}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground">健康率</div>
                    <div className="text-sm font-bold">{report.summary.health_percentage}%</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground">健康器官</div>
                    <div className="text-sm font-bold">{report.summary.healthy_organs}/{report.summary.total_organs}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground">总组件</div>
                    <div className="text-sm font-bold">{report.summary.total_components}</div>
                  </div>
                </div>
              </div>

              {/* Stats */}
              {report.stats && Object.keys(report.stats).length > 0 && (
                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium mb-3">详细统计</div>
                  <div className="space-y-2">
                    {Object.entries(report.stats).map(([name, stat]) => (
                      <div key={name} className="p-2 rounded bg-muted/30">
                        <div className="text-xs font-medium mb-1">{name}</div>
                        <pre className="text-[10px] text-muted-foreground whitespace-pre-wrap break-all">{JSON.stringify(stat, null, 2).slice(0, 300)}</pre>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Recent events */}
              {report.recent_events && (
                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="text-xs font-medium mb-3">近期事件</div>
                  <pre className="text-[10px] text-muted-foreground whitespace-pre-wrap break-all max-h-60 overflow-auto">
                    {JSON.stringify(report.recent_events, null, 2).slice(0, 2000)}
                  </pre>
                </div>
              )}
            </>
          ) : (
            <div className="text-center py-12 text-muted-foreground">
              <FileText className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-xs">点击"系统报告"生成</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
