'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Heart, RefreshCw, X, Play, AlertCircle, CheckCircle,
  XCircle, Clock, Loader2, Activity, ShieldCheck, Zap, History,
  ChevronDown, ChevronRight, Stethoscope
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface DiagnosisResult {
  organ: string;
  healthy: boolean;
  severity: string;
  symptoms: string[];
  root_cause: string;
  recommended_action: string;
  action_taken: string;
  action_success: boolean;
  response_time_ms: number;
  timestamp: number;
}

interface HealerStats {
  total_diagnoses: number;
  total_heals: number;
  success_rate: number;
  avg_heal_time_ms: number;
}

interface CycleResult {
  cycle_complete: boolean;
  elapsed_seconds: number;
  total_organs: number;
  healthy: number;
  unhealthy: number;
  healed: number;
  organs: DiagnosisResult[];
}

const SEVERITY_CONFIG: Record<string, { color: string; bg: string; label: string }> = {
  healthy: { color: 'text-green-600', bg: 'bg-green-500/10', label: '健康' },
  recovered: { color: 'text-blue-600', bg: 'bg-blue-500/10', label: '已恢复' },
  degraded: { color: 'text-orange-600', bg: 'bg-orange-500/10', label: '降级' },
  critical: { color: 'text-red-600', bg: 'bg-red-500/10', label: '严重' },
  unknown: { color: 'text-gray-600', bg: 'bg-gray-500/10', label: '未知' },
};

export default function HealerPage() {
  const [stats, setStats] = useState<HealerStats | null>(null);
  const [history, setHistory] = useState<DiagnosisResult[]>([]);
  const [lastCycle, setLastCycle] = useState<CycleResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionLoading, setActionLoading] = useState('');
  const [expandedOrgan, setExpandedOrgan] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [statsData, histData] = await Promise.all([
        apiFetch('/api/healer/stats'),
        apiFetch('/api/healer/history?limit=50'),
      ]);
      setStats(statsData);
      setHistory(Array.isArray(histData.history) ? histData.history : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleDiagnoseAll = async () => {
    try {
      setActionLoading('diagnose');
      const data = await apiFetch('/api/healer/diagnose-all', { method: 'POST' });
      setLastCycle({
        cycle_complete: true,
        elapsed_seconds: 0,
        total_organs: data.total,
        healthy: data.healthy,
        unhealthy: data.unhealthy,
        healed: 0,
        organs: Array.isArray(data.organs) ? data.organs : [],
      });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setActionLoading('');
    }
  };

  const handleHealAll = async () => {
    try {
      setActionLoading('heal');
      const data = await apiFetch('/api/healer/heal-all', { method: 'POST' });
      setLastCycle({
        cycle_complete: true,
        elapsed_seconds: 0,
        total_organs: data.total,
        healthy: data.healthy,
        unhealthy: data.failures,
        healed: data.healed,
        organs: Array.isArray(data.organs) ? data.organs : [],
      });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setActionLoading('');
    }
  };

  const handleFullCycle = async () => {
    try {
      setActionLoading('cycle');
      const data = await apiFetch('/api/healer/cycle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_heal: true, notify: true, audit: true }),
      });
      setLastCycle(data);
      fetchData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setActionLoading('');
    }
  };

  const handleDiagnoseOne = async (organ: string) => {
    try {
      setActionLoading(`diagnose-${organ}`);
      const data = await apiFetch(`/api/healer/diagnose/${organ}`, { method: 'POST' });
      // Update in lastCycle
      if (lastCycle) {
        setLastCycle({
          ...lastCycle,
          organs: lastCycle.organs.map((o) => o.organ === organ ? data : o),
        });
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setActionLoading('');
    }
  };

  const handleHealOne = async (organ: string) => {
    try {
      setActionLoading(`heal-${organ}`);
      const data = await apiFetch(`/api/healer/heal/${organ}`, { method: 'POST' });
      if (lastCycle) {
        setLastCycle({
          ...lastCycle,
          organs: lastCycle.organs.map((o) => o.organ === organ ? data : o),
        });
      }
      fetchData();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setActionLoading('');
    }
  };

  const formatTime = (ts: number) => {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Stethoscope className="w-4 h-4 text-primary" />
            <span className="text-xs text-muted-foreground">诊断次数</span>
          </div>
          <div className="text-2xl font-bold mt-1">{stats?.total_diagnoses || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Heart className="w-4 h-4 text-red-500" />
            <span className="text-xs text-muted-foreground">修复次数</span>
          </div>
          <div className="text-2xl font-bold mt-1">{stats?.total_heals || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-green-500" />
            <span className="text-xs text-muted-foreground">成功率</span>
          </div>
          <div className="text-2xl font-bold mt-1">{stats?.success_rate ? `${(stats.success_rate * 100).toFixed(0)}%` : '-'}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-blue-500" />
            <span className="text-xs text-muted-foreground">平均耗时</span>
          </div>
          <div className="text-2xl font-bold mt-1">{stats?.avg_heal_time_ms ? `${stats.avg_heal_time_ms.toFixed(0)}ms` : '-'}</div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          onClick={handleDiagnoseAll}
          disabled={!!actionLoading}
          variant="chip-lg" className="flex items-center gap-1.5 bg-blue-500/10 text-blue-600 border border-blue-500/20 hover:bg-blue-500/20 disabled:opacity-50"
        >
          {actionLoading === 'diagnose' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Stethoscope className="w-3.5 h-3.5" />}
          全量诊断
        </Button>
        <Button
          onClick={handleHealAll}
          disabled={!!actionLoading}
          variant="chip-lg" className="flex items-center gap-1.5 bg-emerald-500/10 text-emerald-500 border border-green-500/20 hover:bg-emerald-500/20 disabled:opacity-50"
        >
          {actionLoading === 'heal' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Heart className="w-3.5 h-3.5" />}
          诊断+修复
        </Button>
        <Button
          onClick={handleFullCycle}
          disabled={!!actionLoading}
          variant="chip-lg" className="flex items-center gap-1.5 bg-primary/10 text-primary border border-primary/20 hover:bg-primary/20 disabled:opacity-50"
        >
          {actionLoading === 'cycle' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
          完整周期 (诊断→修复→通知→审计)
        </Button>
        <div className="flex-1" />
        <Button variant="bare"
          onClick={() => setShowHistory(!showHistory)}
          className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm rounded-lg border transition-colors ${showHistory ? 'bg-primary/10 text-primary border-primary/20' : 'bg-muted border-border text-muted-foreground hover:bg-muted'}`}
        >
          <History className="w-3.5 h-3.5" />历史记录
        </Button>
        <Button onClick={fetchData} variant="secondary" className="py-2">
          <RefreshCw className="w-3.5 h-3.5" />刷新
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

      {/* Cycle Results */}
      {lastCycle && (
        <div className="rounded-lg border border-border bg-card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-medium">诊断结果</h3>
            <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
              <span>总计: {lastCycle.total_organs}</span>
              <span className="text-green-600">健康: {lastCycle.healthy}</span>
              <span className="text-red-600">异常: {lastCycle.unhealthy}</span>
              {lastCycle.healed > 0 && <span className="text-blue-600">已修复: {lastCycle.healed}</span>}
              {lastCycle.elapsed_seconds > 0 && <span>耗时: {lastCycle.elapsed_seconds}s</span>}
            </div>
          </div>

          {/* Progress bar */}
          <div className="w-full bg-muted rounded-full h-2 flex overflow-hidden">
            <div className="bg-green-500 h-2 transition-all" style={{ width: `${(lastCycle.healthy / lastCycle.total_organs) * 100}%` }} />
            {lastCycle.healed > 0 && (
              <div className="bg-blue-500 h-2 transition-all" style={{ width: `${(lastCycle.healed / lastCycle.total_organs) * 100}%` }} />
            )}
            <div className="bg-red-500 h-2 transition-all" style={{ width: `${((lastCycle.unhealthy - lastCycle.healed) / lastCycle.total_organs) * 100}%` }} />
          </div>

          {/* Organ results */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
            {lastCycle.organs.map((organ) => {
              const sevConfig = SEVERITY_CONFIG[organ.severity] || SEVERITY_CONFIG.unknown;
              const isExpanded = expandedOrgan === organ.organ;
              return (
                <div
                  key={organ.organ}
                  className={`rounded-md border p-2 cursor-pointer transition-colors ${organ.healthy ? 'border-border hover:bg-muted/30' : 'border-red-500/20 bg-red-500/5 hover:bg-red-500/10'}`}
                  onClick={() => setExpandedOrgan(isExpanded ? null : organ.organ)}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 min-w-0">
                      {organ.healthy ? (
                        <CheckCircle className="w-3.5 h-3.5 text-green-500 shrink-0" />
                      ) : (
                        <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />
                      )}
                      <span className="text-xs font-medium truncate">{organ.organ}</span>
                      <Badge variant="bare" className={`${sevConfig.bg} ${sevConfig.color}`}>{sevConfig.label}</Badge>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <span className="text-[10px] text-muted-foreground font-mono">{organ.response_time_ms}ms</span>
                      {!organ.healthy && (
                        <Button
                          onClick={(e) => { e.stopPropagation(); handleHealOne(organ.organ); }}
                          disabled={!!actionLoading}
                          variant="icon-sm" className="hover:bg-emerald-500/10"
                          title="修复"
                        >
                          {actionLoading === `heal-${organ.organ}` ? (
                            <Loader2 className="w-3 h-3 animate-spin text-green-500" />
                          ) : (
                            <Heart className="w-3 h-3 text-green-500" />
                          )}
                        </Button>
                      )}
                      <Button
                        onClick={(e) => { e.stopPropagation(); handleDiagnoseOne(organ.organ); }}
                        disabled={!!actionLoading}
                        variant="icon-sm" className="hover:bg-blue-500/10"
                        title="重新诊断"
                      >
                        {actionLoading === `diagnose-${organ.organ}` ? (
                          <Loader2 className="w-3 h-3 animate-spin text-blue-500" />
                        ) : (
                          <RefreshCw className="w-3 h-3 text-blue-500" />
                        )}
                      </Button>
                      {isExpanded ? <ChevronDown className="w-3 h-3 text-muted-foreground" /> : <ChevronRight className="w-3 h-3 text-muted-foreground" />}
                    </div>
                  </div>

                  {isExpanded && (
                    <div className="mt-2 pt-2 border-t border-border space-y-1">
                      {organ.symptoms?.length > 0 && (
                        <div>
                          <span className="text-[10px] font-medium text-muted-foreground">症状</span>
                          <ul className="text-[10px] text-muted-foreground ml-2">
                            {organ.symptoms.map((s, i) => <li key={i}>• {s}</li>)}
                          </ul>
                        </div>
                      )}
                      {organ.root_cause && (
                        <div>
                          <span className="text-[10px] font-medium text-muted-foreground">根因</span>
                          <div className="text-[10px] text-red-600">{organ.root_cause}</div>
                        </div>
                      )}
                      {organ.recommended_action && organ.recommended_action !== 'none' && (
                        <div>
                          <span className="text-[10px] font-medium text-muted-foreground">建议操作</span>
                          <div className="text-[10px] text-orange-600">{organ.recommended_action}</div>
                        </div>
                      )}
                      {organ.action_taken && organ.action_taken !== 'none' && (
                        <div>
                          <span className="text-[10px] font-medium text-muted-foreground">已执行</span>
                          <div className={`text-[10px] ${organ.action_success ? 'text-green-600' : 'text-red-600'}`}>
                            {organ.action_taken} → {organ.action_success ? '成功' : '失败'}
                          </div>
                        </div>
                      )}
                      {organ.timestamp > 0 && (
                        <div className="text-[10px] text-muted-foreground">{formatTime(organ.timestamp)}</div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* History */}
      {showHistory && (
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="text-xs font-medium mb-3">诊断/修复历史 ({history.length})</h3>
          {history.length === 0 ? (
            <div className="text-center py-8 text-xs text-muted-foreground">暂无历史记录</div>
          ) : (
            <div className="max-h-[400px] overflow-auto space-y-1">
              {history.map((item, idx) => {
                const sevConfig = SEVERITY_CONFIG[item.severity] || SEVERITY_CONFIG.unknown;
                return (
                  <div key={idx} className={`flex items-center gap-3 px-3 py-2 rounded-md text-xs ${item.healthy ? 'bg-muted/30' : 'bg-red-500/5'}`}>
                    {item.healthy ? (
                      <CheckCircle className="w-3.5 h-3.5 text-green-500 shrink-0" />
                    ) : (
                      <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />
                    )}
                    <span className="font-medium w-20 shrink-0">{item.organ}</span>
                    <Badge variant="bare" className={`${sevConfig.bg} ${sevConfig.color}`}>{sevConfig.label}</Badge>
                    <span className="text-muted-foreground truncate flex-1">{item.root_cause || item.symptoms?.join(', ') || '-'}</span>
                    {item.action_taken && item.action_taken !== 'none' && (
                      <span className={`text-[10px] ${item.action_success ? 'text-green-600' : 'text-red-600'}`}>{item.action_taken}</span>
                    )}
                    <span className="text-[10px] text-muted-foreground shrink-0">{formatTime(item.timestamp)}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="text-[10px] text-muted-foreground text-center">
        OpenHealer 自愈系统 · API: /api/healer · {history.length} 条历史记录
      </div>
    </div>
  );
}
