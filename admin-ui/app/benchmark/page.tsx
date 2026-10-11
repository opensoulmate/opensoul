'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Gauge, Play, Trash2, RefreshCw, X, Loader2, AlertCircle,
  BarChart3, Zap, Clock, Target, TrendingUp
} from 'lucide-react';
import { Button } from '@/components/ui/button';

interface BenchmarkTarget {
  organ: string;
  label: string;
  endpoint: string;
}

interface BenchmarkResult {
  organ: string;
  iterations: number;
  concurrency: number;
  avg_ms: number;
  min_ms: number;
  max_ms: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  success_rate: number;
  total_time_ms: number;
  timestamp: number;
}

interface BenchmarkRun {
  run_id: string;
  organs: string[];
  iterations: number;
  concurrency: number;
  started_at: number;
  finished_at: number | null;
  status: string;
  results: BenchmarkResult[];
}

export default function BenchmarkPage() {
  const [targets, setTargets] = useState<BenchmarkTarget[]>([]);
  const [latest, setLatest] = useState<Record<string, BenchmarkResult>>({});
  const [runs, setRuns] = useState<BenchmarkRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [running, setRunning] = useState(false);
  const [selectedOrgans, setSelectedOrgans] = useState<Set<string>>(new Set());
  const [iterations, setIterations] = useState(20);
  const [concurrency, setConcurrency] = useState(5);
  const [lastResult, setLastResult] = useState<BenchmarkRun | null>(null);
  const [tab, setTab] = useState<'latest' | 'history' | 'run'>('latest');

  const fetchTargets = useCallback(async () => {
    try {
      const data = await apiFetch('/api/benchmark/targets');
      setTargets(Array.isArray(data.targets) ? data.targets : []);
    } catch { /* silent */ }
  }, []);

  const fetchLatest = useCallback(async () => {
    try {
      const data = await apiFetch('/api/benchmark/latest');
      setLatest(data.latest || {});
    } catch { /* silent */ }
  }, []);

  const fetchRuns = useCallback(async () => {
    try {
      const data = await apiFetch('/api/benchmark/history/runs?limit=30');
      setRuns(Array.isArray(data.runs) ? data.runs : []);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchTargets(), fetchLatest(), fetchRuns()]).finally(() => setLoading(false));
  }, [fetchTargets, fetchLatest, fetchRuns]);

  const handleRun = async () => {
    if (selectedOrgans.size === 0 && targets.length === 0) return;
    try {
      setRunning(true);
      setError('');
      const body: any = { iterations, concurrency };
      if (selectedOrgans.size > 0) body.organs = Array.from(selectedOrgans);
      const data = await apiFetch('/api/benchmark/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setLastResult(data);
      setTab('run');
      fetchLatest();
      fetchRuns();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  const handleQuick = async (organ: string) => {
    try {
      setRunning(true);
      setError('');
      const data = await apiFetch(`/api/benchmark/quick/${organ}?iterations=10`, { method: 'POST' });
      setLastResult(data);
      setTab('run');
      fetchLatest();
      fetchRuns();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  const handleClearHistory = async () => {
    if (!confirm('确定要清除所有基准测试历史？')) return;
    try {
      await apiFetch('/api/benchmark/history', { method: 'DELETE' });
      setLatest({});
      setRuns([]);
      setLastResult(null);
    } catch (e: any) { setError(e.message); }
  };

  const toggleOrgan = (organ: string) => {
    setSelectedOrgans((prev) => {
      const next = new Set(prev);
      if (next.has(organ)) next.delete(organ); else next.add(organ);
      return next;
    });
  };

  const formatMs = (ms: number) => ms < 1 ? `${(ms * 1000).toFixed(0)}µs` : ms < 1000 ? `${ms.toFixed(1)}ms` : `${(ms / 1000).toFixed(2)}s`;
  const formatPct = (v: number) => `${(v * 100).toFixed(1)}%`;
  const formatTs = (ts: number) => { try { return new Date(ts * 1000).toLocaleString('zh-CN'); } catch { return '-'; } };

  const latestEntries = Object.entries(latest).sort((a, b) => (a[1].avg_ms || 0) - (b[1].avg_ms || 0));
  const bestAvg = latestEntries.length > 0 ? Math.min(...latestEntries.map(([, r]) => r.avg_ms)) : 1;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-500">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button onClick={() => setError('')} variant="icon-xs" className="hover:bg-red-500/20"><X className="w-3.5 h-3.5" /></Button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{targets.length}</div>
          <div className="text-xs text-muted-foreground">可测试目标</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{latestEntries.length}</div>
          <div className="text-xs text-muted-foreground">已测试</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-500">{runs.length}</div>
          <div className="text-xs text-muted-foreground">历史运行</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-orange-500">{latestEntries.length > 0 ? formatMs(bestAvg) : '-'}</div>
          <div className="text-xs text-muted-foreground">最佳平均延迟</div>
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex items-center gap-1 border-b border-border">
        {([['latest', '最新结果'], ['history', '历史记录'], ['run', '运行测试']] as const).map(([key, label]) => (
          <Button variant="bare" key={key} onClick={() => setTab(key as any)} className={`px-4 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${tab === key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
            {label}
          </Button>
        ))}
        <div className="flex-1" />
        <Button onClick={handleClearHistory} variant="chip-sm" className="flex items-center gap-1 text-red-500 hover:bg-red-500/10 mr-1">
          <Trash2 className="w-3 h-3" />清除历史
        </Button>
      </div>

      {/* Run Tab */}
      {tab === 'run' && (
        <div className="space-y-4">
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-sm font-medium mb-3">选择测试目标</h3>
            <div className="flex flex-wrap gap-2 mb-4">
              <Button onClick={() => setSelectedOrgans(new Set(targets.map((t) => t.organ)))} variant="secondary" className="px-2 py-1">全选</Button>
              <Button onClick={() => setSelectedOrgans(new Set())} variant="secondary" className="px-2 py-1">清空</Button>
              {targets.map((t) => (
                <Button variant="bare" key={t.organ} onClick={() => toggleOrgan(t.organ)} className={`text-xs lg:text-sm px-2.5 py-1 rounded-lg border transition-colors ${selectedOrgans.has(t.organ) ? 'bg-primary/10 border-primary/30 text-primary' : 'bg-muted border-border text-muted-foreground hover:text-foreground'}`}>
                  {t.label || t.organ}
                </Button>
              ))}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">迭代次数</label>
                <input type="number" min={1} max={200} className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={iterations} onChange={(e) => setIterations(Math.min(200, Math.max(1, +e.target.value || 20)))} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">并发数</label>
                <input type="number" min={1} max={50} className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={concurrency} onChange={(e) => setConcurrency(Math.min(50, Math.max(1, +e.target.value || 5)))} />
              </div>
            </div>
            <Button onClick={handleRun} disabled={running} variant="primary" className="gap-1.5 text-sm">
              {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              {running ? '运行中...' : '开始基准测试'}
            </Button>
          </div>

          {/* Quick Run */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-sm font-medium mb-3">快速测试（单个目标，10次迭代）</h3>
            <div className="flex flex-wrap gap-2">
              {targets.map((t) => (
                <Button key={t.organ} onClick={() => handleQuick(t.organ)} disabled={running} variant="secondary" className="gap-1">
                  <Zap className="w-3 h-3" />{t.label || t.organ}
                </Button>
              ))}
            </div>
          </div>

          {/* Last Result */}
          {lastResult && lastResult.results && lastResult.results.length > 0 && (
            <div className="rounded-lg border border-border overflow-hidden">
              <div className="px-4 py-2 text-xs font-medium text-muted-foreground bg-muted/30 border-b border-border">
                运行结果 — {lastResult.organs?.join(', ')} · {lastResult.iterations}次 · {lastResult.concurrency}并发
              </div>
              <div className="grid grid-cols-[1fr_80px_80px_80px_80px_80px_80px] gap-2 px-4 py-2 text-[10px] font-medium text-muted-foreground bg-muted/20 border-b border-border">
                <span>目标</span><span>平均</span><span>P50</span><span>P95</span><span>P99</span><span>最小</span><span>成功率</span>
              </div>
              {lastResult.results.map((r) => (
                <div key={r.organ} className="grid grid-cols-[1fr_80px_80px_80px_80px_80px_80px] gap-2 items-center px-4 py-2 border-b border-border last:border-b-0 text-xs">
                  <span className="font-medium">{r.organ}</span>
                  <span className={r.avg_ms < 100 ? 'text-green-500' : r.avg_ms < 500 ? 'text-orange-500' : 'text-red-500'}>{formatMs(r.avg_ms)}</span>
                  <span className="text-muted-foreground">{formatMs(r.p50_ms)}</span>
                  <span className="text-muted-foreground">{formatMs(r.p95_ms)}</span>
                  <span className="text-muted-foreground">{formatMs(r.p99_ms)}</span>
                  <span className="text-muted-foreground">{formatMs(r.min_ms)}</span>
                  <span className={r.success_rate >= 1 ? 'text-green-500' : 'text-orange-500'}>{formatPct(r.success_rate)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Latest Tab */}
      {tab === 'latest' && (
        <div>
          {latestEntries.length === 0 ? (
            <div className="text-center py-12 text-sm text-muted-foreground">
              <Gauge className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p>暂无基准测试结果</p>
              <p className="text-xs mt-1">切换到「运行测试」开始第一次测试</p>
            </div>
          ) : (
            <div className="space-y-2">
              {latestEntries.map(([organ, r]) => (
                <div key={organ} className="rounded-lg border border-border bg-card p-3">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Target className="w-3.5 h-3.5 text-primary" />
                      <span className="text-sm font-medium">{organ}</span>
                    </div>
                    <span className="text-[10px] text-muted-foreground">{formatTs(r.timestamp)}</span>
                  </div>
                  <div className="flex items-end gap-1 mb-2">
                    <span className="text-2xl font-bold">{formatMs(r.avg_ms)}</span>
                    <span className="text-xs text-muted-foreground mb-0.5">平均延迟</span>
                  </div>
                  {/* Simple bar visualization */}
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div className="h-full bg-primary/60 rounded-full" style={{ width: `${Math.min(100, (bestAvg / r.avg_ms) * 100)}%` }} />
                  </div>
                  <div className="grid grid-cols-5 gap-2 mt-2 text-[10px] text-muted-foreground">
                    <span>P50: {formatMs(r.p50_ms)}</span>
                    <span>P95: {formatMs(r.p95_ms)}</span>
                    <span>P99: {formatMs(r.p99_ms)}</span>
                    <span>最小: {formatMs(r.min_ms)}</span>
                    <span>成功率: {formatPct(r.success_rate)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* History Tab */}
      {tab === 'history' && (
        <div>
          {runs.length === 0 ? (
            <div className="text-center py-12 text-sm text-muted-foreground">
              <Clock className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p>暂无历史记录</p>
            </div>
          ) : (
            <div className="rounded-lg border border-border overflow-hidden">
              <div className="grid grid-cols-[1fr_80px_80px_100px_80px] gap-2 px-4 py-2 text-xs font-medium text-muted-foreground bg-muted/30 border-b border-border">
                <span>目标</span><span>迭代</span><span>并发</span><span>时间</span><span>状态</span>
              </div>
              {runs.map((run) => (
                <div key={run.run_id} className="grid grid-cols-[1fr_80px_80px_100px_80px] gap-2 items-center px-4 py-2.5 border-b border-border last:border-b-0 hover:bg-muted/20 text-xs">
                  <span className="truncate">{run.organs?.join(', ') || '全部'}</span>
                  <span>{run.iterations}</span>
                  <span>{run.concurrency}</span>
                  <span className="text-muted-foreground">{formatTs(run.started_at)}</span>
                  <span className={run.status === 'completed' ? 'text-green-500' : run.status === 'running' ? 'text-blue-500' : 'text-muted-foreground'}>{run.status}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
