'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Brain, RefreshCw, X, AlertCircle, Loader2, TrendingUp, TrendingDown,
  Lightbulb, AlertTriangle, Info, Activity, Zap, BarChart3, ShieldCheck
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface SystemSummary {
  health_score: number;
  status: string;
  components_tracked: number;
  total_insights: number;
  uptime_seconds: number;
  recommendations: number;
}

interface Insight {
  id: string;
  component: string;
  insight_type: string;
  severity: string;
  title: string;
  description: string;
  recommendation: string;
  timestamp: number;
}

interface ComponentDetail {
  name: string;
  health: string;
  response_time_ms: number;
  request_count: number;
  error_count: number;
  last_seen: number;
  metrics: Record<string, any>;
}

interface Recommendation {
  component: string;
  type: string;
  priority: string;
  title: string;
  description: string;
  action: string;
}

const SEVERITY_CONFIG: Record<string, { icon: any; color: string; bg: string }> = {
  critical: { icon: AlertCircle, color: 'text-red-600', bg: 'bg-red-600/10' },
  high: { icon: AlertTriangle, color: 'text-red-500', bg: 'bg-red-500/10' },
  medium: { icon: AlertTriangle, color: 'text-orange-500', bg: 'bg-orange-500/10' },
  low: { icon: Info, color: 'text-blue-500', bg: 'bg-blue-500/10' },
};

const TYPE_LABELS: Record<string, string> = {
  anomaly: '异常',
  optimization: '优化',
  trend: '趋势',
  warning: '警告',
  info: '信息',
};

export default function IntelligencePage() {
  const [summary, setSummary] = useState<SystemSummary | null>(null);
  const [insights, setInsights] = useState<Insight[]>([]);
  const [components, setComponents] = useState<ComponentDetail[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [collecting, setCollecting] = useState(false);
  const [tab, setTab] = useState<'overview' | 'insights' | 'components' | 'recommendations'>('overview');
  const [severityFilter, setSeverityFilter] = useState('');

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [s, i, c, r] = await Promise.all([
        apiFetch('/api/intelligence/summary').catch(() => null),
        apiFetch('/api/intelligence/insights?limit=100').catch(() => ({ insights: [] })),
        apiFetch('/api/intelligence/components').catch(() => ({ components: [] })),
        apiFetch('/api/intelligence/recommendations').catch(() => ({ recommendations: [] })),
      ]);
      if (s) setSummary(s);
      setInsights(Array.isArray(i.insights) ? i.insights : []);
      setComponents(Array.isArray(c.components) ? c.components : []);
      setRecommendations(Array.isArray(r.recommendations) ? r.recommendations : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleCollect = async () => {
    try {
      setCollecting(true);
      await apiFetch('/api/intelligence/collect', { method: 'POST' });
      await fetchAll();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCollecting(false);
    }
  };

  const formatUptime = (s: number) => {
    if (s < 60) return `${s}秒`;
    if (s < 3600) return `${Math.floor(s / 60)}分钟`;
    if (s < 86400) return `${Math.floor(s / 3600)}小时`;
    return `${Math.floor(s / 86400)}天`;
  };

  const filteredInsights = severityFilter
    ? insights.filter((i) => i.severity === severityFilter)
    : insights;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-500">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')} className="p-0.5 hover:bg-red-500/20 rounded-md"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-green-500" />
            <div className="text-2xl font-bold">{summary?.health_score ?? '-'}</div>
          </div>
          <div className="text-xs text-muted-foreground">健康分数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-blue-500" />
            <div className="text-2xl font-bold">{summary?.components_tracked ?? components.length}</div>
          </div>
          <div className="text-xs text-muted-foreground">组件追踪</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Lightbulb className="w-4 h-4 text-orange-500" />
            <div className="text-2xl font-bold">{summary?.total_insights ?? insights.length}</div>
          </div>
          <div className="text-xs text-muted-foreground">洞察数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-purple-500" />
            <div className="text-2xl font-bold">{recommendations.length}</div>
          </div>
          <div className="text-xs text-muted-foreground">优化建议</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1 border-b border-border">
          {([['overview', '总览'], ['insights', '洞察'], ['components', '组件'], ['recommendations', '建议']] as const).map(([key, label]) => (
            <button key={key} onClick={() => setTab(key as any)} className={`px-3 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${tab === key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
              {label}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <Button onClick={fetchAll} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={handleCollect} disabled={collecting} variant="primary" className="gap-1.5">
          {collecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <BarChart3 className="w-3.5 h-3.5" />}
          {collecting ? '采集中...' : '采集指标'}
        </Button>
      </div>

      {/* Overview Tab */}
      {tab === 'overview' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Recent Insights */}
          <div className="rounded-lg border border-border bg-card">
            <div className="px-4 py-2.5 border-b border-border text-xs font-medium">最近洞察</div>
            <div className="max-h-64 overflow-y-auto">
              {insights.slice(0, 8).map((insight) => {
                const sc = SEVERITY_CONFIG[insight.severity] || SEVERITY_CONFIG.low;
                const SevIcon = sc.icon;
                return (
                  <div key={insight.id} className="flex items-start gap-2 px-4 py-2 border-b border-border last:border-b-0">
                    <SevIcon className={`w-3.5 h-3.5 mt-0.5 ${sc.color} shrink-0`} />
                    <div className="min-w-0">
                      <div className="text-xs font-medium truncate">{insight.title}</div>
                      <div className="text-[10px] text-muted-foreground">{insight.component} · {TYPE_LABELS[insight.insight_type] || insight.insight_type}</div>
                    </div>
                  </div>
                );
              })}
              {insights.length === 0 && <div className="px-4 py-6 text-xs text-muted-foreground text-center">暂无洞察</div>}
            </div>
          </div>

          {/* Component Health Overview */}
          <div className="rounded-lg border border-border bg-card">
            <div className="px-4 py-2.5 border-b border-border text-xs font-medium">组件健康</div>
            <div className="max-h-64 overflow-y-auto">
              {components.sort((a, b) => (a.health === 'ok' ? 0 : 1) - (b.health === 'ok' ? 0 : 1)).slice(0, 12).map((c) => (
                <div key={c.name} className="flex items-center gap-2 px-4 py-2 border-b border-border last:border-b-0">
                  <div className={`w-2 h-2 rounded-full ${c.health === 'ok' ? 'bg-green-500' : 'bg-red-500'}`} />
                  <span className="text-xs font-medium flex-1">{c.name}</span>
                  <span className="text-[10px] text-muted-foreground">{c.response_time_ms?.toFixed(0) ?? '-'}ms</span>
                  <span className={`text-[10px] ${c.health === 'ok' ? 'text-green-500' : 'text-red-500'}`}>{c.health}</span>
                </div>
              ))}
              {components.length === 0 && <div className="px-4 py-6 text-xs text-muted-foreground text-center">暂无组件数据，请先「采集指标」</div>}
            </div>
          </div>
        </div>
      )}

      {/* Insights Tab */}
      {tab === 'insights' && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <select className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none" value={severityFilter} onChange={(e) => setSeverityFilter(e.target.value)}>
              <option value="">全部级别</option>
              <option value="critical">严重</option>
              <option value="high">高</option>
              <option value="medium">中</option>
              <option value="low">低</option>
            </select>
            <span className="text-xs text-muted-foreground">{filteredInsights.length} 条洞察</span>
          </div>
          {filteredInsights.length === 0 ? (
            <div className="text-center py-12 text-sm text-muted-foreground">
              <Brain className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p>暂无洞察数据</p>
              <p className="text-xs mt-1">点击「采集指标」收集系统数据后生成洞察</p>
            </div>
          ) : (
            filteredInsights.map((insight) => {
              const sc = SEVERITY_CONFIG[insight.severity] || SEVERITY_CONFIG.low;
              const SevIcon = sc.icon;
              return (
                <div key={insight.id} className={`rounded-lg border border-border bg-card p-4 ${sc.bg}`}>
                  <div className="flex items-start gap-3">
                    <SevIcon className={`w-4 h-4 mt-0.5 ${sc.color} shrink-0`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm font-medium">{insight.title}</span>
                        <Badge variant="muted">{TYPE_LABELS[insight.insight_type] || insight.insight_type}</Badge>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${sc.bg} ${sc.color}`}>{insight.severity}</span>
                      </div>
                      <p className="text-xs text-muted-foreground mb-2">{insight.description}</p>
                      {insight.recommendation && (
                        <div className="flex items-start gap-1.5 text-xs">
                          <Lightbulb className="w-3 h-3 text-yellow-500 mt-0.5 shrink-0" />
                          <span>{insight.recommendation}</span>
                        </div>
                      )}
                      <div className="text-[10px] text-muted-foreground mt-2">{insight.component}</div>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}

      {/* Components Tab */}
      {tab === 'components' && (
        <div>
          {components.length === 0 ? (
            <div className="text-center py-12 text-sm text-muted-foreground">
              <Activity className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p>暂无组件数据</p>
              <p className="text-xs mt-1">点击「采集指标」收集组件健康状态</p>
            </div>
          ) : (
            <div className="rounded-lg border border-border overflow-hidden">
              <div className="grid grid-cols-[1fr_80px_80px_80px_80px] gap-2 px-4 py-2 text-xs font-medium text-muted-foreground bg-muted/30 border-b border-border">
                <span>组件</span><span>健康</span><span>响应时间</span><span>请求数</span><span>错误数</span>
              </div>
              {components.sort((a, b) => a.name.localeCompare(b.name)).map((c) => (
                <div key={c.name} className="grid grid-cols-[1fr_80px_80px_80px_80px] gap-2 items-center px-4 py-2.5 border-b border-border last:border-b-0 hover:bg-muted/20 text-xs">
                  <span className="font-medium">{c.name}</span>
                  <span className={c.health === 'ok' ? 'text-green-500' : 'text-red-500'}>{c.health}</span>
                  <span className="text-muted-foreground">{c.response_time_ms?.toFixed(1) ?? '-'}ms</span>
                  <span className="text-muted-foreground">{c.request_count ?? 0}</span>
                  <span className={c.error_count > 0 ? 'text-red-500' : 'text-muted-foreground'}>{c.error_count ?? 0}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Recommendations Tab */}
      {tab === 'recommendations' && (
        <div>
          {recommendations.length === 0 ? (
            <div className="text-center py-12 text-sm text-muted-foreground">
              <Lightbulb className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p>暂无优化建议</p>
              <p className="text-xs mt-1">系统运行良好，采集更多数据后可生成建议</p>
            </div>
          ) : (
            <div className="space-y-3">
              {recommendations.map((rec, i) => (
                <div key={i} className="rounded-lg border border-border bg-card p-4">
                  <div className="flex items-start gap-3">
                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${rec.priority === 'high' ? 'bg-red-500/10' : rec.priority === 'medium' ? 'bg-orange-500/10' : 'bg-blue-500/10'}`}>
                      <Lightbulb className={`w-4 h-4 ${rec.priority === 'high' ? 'text-red-500' : rec.priority === 'medium' ? 'text-orange-500' : 'text-blue-500'}`} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm font-medium">{rec.title}</span>
                        <Badge variant="muted">{rec.component}</Badge>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${rec.priority === 'high' ? 'bg-red-500/10 text-red-500' : rec.priority === 'medium' ? 'bg-amber-500/10 text-amber-500' : 'bg-blue-500/10 text-blue-500'}`}>{rec.priority}</span>
                      </div>
                      <p className="text-xs text-muted-foreground mb-2">{rec.description}</p>
                      {rec.action && (
                        <div className="flex items-center gap-1.5 text-xs text-primary">
                          <Zap className="w-3 h-3" />{rec.action}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
