'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Brain, Layers, Activity, Cpu, Workflow, Zap, RefreshCw, Search,
  ChevronRight, Shield, Target, Network, ArrowRight, CheckCircle, XCircle
} from 'lucide-react';

// ── Types ─────────────────────────────────────────────────────

interface LayerStatus {
  status: string;
  tasks_analyzed?: number;
  usage_percent?: number;
  compression_count?: number;
  tool_calls?: number;
  guardrail_blocks?: number;
  iterations?: number;
  avg_quality?: number;
  groups?: number;
  agents?: number;
  tasks?: number;
}

interface EngineStatus {
  layers: Record<string, LayerStatus>;
  total_tasks: number;
  success_rate: number;
  avg_response_time: string;
}

interface TaskCard {
  task_id: string;
  message: string;
  complexity: string;
  activate: string[];
  reason: string;
  estimated_time: string;
  suggested_agents: { role: string; model: string; reason: string }[];
}

interface ContextState {
  total_tokens: number;
  used_tokens: number;
  usage_percent: number;
  layers: Record<string, { tokens: number; percent: number }>;
  compression_needed: boolean;
}

interface ToolRoute {
  tool: string;
  mode: string;
  permissions: string;
  guardrails: string[];
}

interface GraphStatus {
  active_groups: number;
  total_agents: number;
  running_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  roles: Record<string, { count: number; busy: number }>;
}

// ── Layer definitions ─────────────────────────────────────────

const LAYERS = [
  { key: 'prompt', label: 'Prompt层', desc: '任务分析与复杂度判断', color: 'text-blue-400', bg: 'bg-blue-400/10', border: 'border-blue-400/20', icon: <Target className="w-5 h-5" /> },
  { key: 'context', label: 'Context层', desc: '上下文管理与压缩', color: 'text-green-400', bg: 'bg-green-400/10', border: 'border-green-400/20', icon: <Layers className="w-5 h-5" /> },
  { key: 'harness', label: 'Harness层', desc: '工具编排与安全护栏', color: 'text-yellow-400', bg: 'bg-yellow-400/10', border: 'border-yellow-400/20', icon: <Shield className="w-5 h-5" /> },
  { key: 'loop', label: 'Loop层', desc: '迭代优化与自我反思', color: 'text-purple-400', bg: 'bg-purple-400/10', border: 'border-purple-400/20', icon: <RefreshCw className="w-5 h-5" /> },
  { key: 'graph', label: 'Graph层', desc: 'AI群引擎与任务分解', color: 'text-red-400', bg: 'bg-red-400/10', border: 'border-red-400/20', icon: <Network className="w-5 h-5" /> },
];

const COMPLEXITY_COLORS: Record<string, string> = {
  simple: 'text-green-400 bg-green-400/10 border-green-400/20',
  medium: 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20',
  complex: 'text-orange-400 bg-orange-400/10 border-orange-400/20',
  ultra: 'text-red-400 bg-red-400/10 border-red-400/20',
};

// ── Component ─────────────────────────────────────────────────

export default function AIEnginePage() {
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [context, setContext] = useState<ContextState | null>(null);
  const [toolRoutes, setToolRoutes] = useState<ToolRoute[]>([]);
  const [graphStatus, setGraphStatus] = useState<GraphStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Task analyzer
  const [analyzeInput, setAnalyzeInput] = useState('');
  const [analyzeResult, setAnalyzeResult] = useState<TaskCard | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  // Task decomposer
  const [decomposeInput, setDecomposeInput] = useState('');
  const [decomposeResult, setDecomposeResult] = useState<{ goal: string; subtasks: { goal: string; role: string; order: number }[]; estimated_time: string } | null>(null);
  const [decomposing, setDecomposing] = useState(false);

  const [activeTab, setActiveTab] = useState<'overview' | 'analyze' | 'context' | 'harness' | 'graph'>('overview');

  // ── Fetch ──────────────────────────────────────────────────

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [s, c, h, g] = await Promise.all([
        apiFetch('/api/ai-engine/status').catch(() => null),
        apiFetch('/api/ai-engine/context').catch(() => null),
        apiFetch('/api/ai-engine/harness/routes').catch(() => []),
        apiFetch('/api/ai-engine/graph/status').catch(() => null),
      ]);
      if (s) setStatus(s as EngineStatus);
      if (c) setContext(c as ContextState);
      setToolRoutes(Array.isArray(h) ? h as ToolRoute[] : []);
      if (g) setGraphStatus(g as GraphStatus);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // ── Actions ────────────────────────────────────────────────

  const handleAnalyze = async () => {
    if (!analyzeInput.trim()) return;
    setAnalyzing(true);
    try {
      const data = await apiFetch('/api/ai-engine/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: analyzeInput }),
      });
      setAnalyzeResult(data as TaskCard);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAnalyzing(false);
    }
  };

  const handleDecompose = async () => {
    if (!decomposeInput.trim()) return;
    setDecomposing(true);
    try {
      const data = await apiFetch(`/api/ai-engine/graph/decompose?goal=${encodeURIComponent(decomposeInput)}`, {
        method: 'POST',
      });
      setDecomposeResult(data as typeof decomposeResult);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDecomposing(false);
    }
  };

  // ── Render ─────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          <span className="flex-1">{error}</span>
          <button onClick={() => setError('')}><XCircle className="w-4 h-4" /></button>
        </div>
      )}

      {/* Header Stats */}
      {status && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[
            { label: '总任务', value: status.total_tasks, icon: <Target className="w-4 h-4" />, color: 'text-blue-400' },
            { label: '成功率', value: `${((status.success_rate || 0) * 100).toFixed(0)}%`, icon: <CheckCircle className="w-4 h-4" />, color: 'text-green-400' },
            { label: '平均响应', value: status.avg_response_time, icon: <Zap className="w-4 h-4" />, color: 'text-yellow-400' },
            { label: '活跃层', value: Object.values(status.layers || {}).filter(l => l.status === 'active').length + '/5', icon: <Layers className="w-4 h-4" />, color: 'text-purple-400' },
          ].map((c, i) => (
            <div key={i} className="rounded-xl border border-border bg-card p-4">
              <div className="flex items-center gap-2 text-muted-foreground text-xs mb-1">
                <span className={c.color}>{c.icon}</span>{c.label}
              </div>
              <div className={`text-2xl font-bold ${c.color}`}>{c.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* 5-Layer Pipeline Visualization */}
      <div className="rounded-xl border border-border bg-card p-5">
        <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
          <Brain className="w-4 h-4 text-primary" /> AI Engineering 5层引擎
        </h3>
        <div className="flex items-center gap-2 overflow-x-auto pb-2">
          {LAYERS.map((layer, i) => {
            const ls = status?.layers?.[layer.key];
            const active = ls?.status === 'active';
            return (
              <div key={layer.key} className="flex items-center gap-2 shrink-0">
                <div className={`flex flex-col items-center gap-1.5 p-3 rounded-xl border min-w-[140px] ${active ? `${layer.bg} ${layer.border}` : 'bg-muted/30 border-border opacity-50'}`}>
                  <div className={`${active ? layer.color : 'text-muted-foreground'}`}>{layer.icon}</div>
                  <div className="text-xs font-medium">{layer.label}</div>
                  <div className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${active ? 'bg-green-400/20 text-green-400' : 'bg-muted text-muted-foreground'}`}>
                    {active ? '活跃' : '未激活'}
                  </div>
                </div>
                {i < LAYERS.length - 1 && (
                  <ArrowRight className="w-4 h-4 text-muted-foreground shrink-0" />
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex items-center gap-1 border-b border-border pb-0 overflow-x-auto">
        {[
          { key: 'overview' as const, label: '概览', icon: <Activity className="w-4 h-4" /> },
          { key: 'analyze' as const, label: '任务分析', icon: <Search className="w-4 h-4" /> },
          { key: 'context' as const, label: '上下文', icon: <Layers className="w-4 h-4" /> },
          { key: 'harness' as const, label: '工具路由', icon: <Shield className="w-4 h-4" /> },
          { key: 'graph' as const, label: 'AI群', icon: <Network className="w-4 h-4" /> },
        ].map(t => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px whitespace-nowrap ${
              activeTab === t.key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {t.icon}{t.label}
          </button>
        ))}
      </div>

      {/* ── Overview Tab ──────────────────────────────────── */}
      {activeTab === 'overview' && status && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {LAYERS.map(layer => {
            const ls = status.layers?.[layer.key];
            if (!ls) return null;
            return (
              <div key={layer.key} className={`rounded-xl border ${layer.border} ${layer.bg} p-4 space-y-2`}>
                <div className="flex items-center gap-2">
                  <span className={layer.color}>{layer.icon}</span>
                  <span className="text-sm font-medium">{layer.label}</span>
                </div>
                <div className="text-xs text-muted-foreground space-y-1">
                  {ls.tasks_analyzed !== undefined && <div>分析任务: <span className="text-foreground font-medium">{ls.tasks_analyzed}</span></div>}
                  {ls.usage_percent !== undefined && <div>上下文使用: <span className="text-foreground font-medium">{ls.usage_percent}%</span></div>}
                  {ls.compression_count !== undefined && <div>压缩次数: <span className="text-foreground font-medium">{ls.compression_count}</span></div>}
                  {ls.tool_calls !== undefined && <div>工具调用: <span className="text-foreground font-medium">{ls.tool_calls}</span></div>}
                  {ls.guardrail_blocks !== undefined && <div>护栏拦截: <span className="text-foreground font-medium">{ls.guardrail_blocks}</span></div>}
                  {ls.iterations !== undefined && <div>迭代次数: <span className="text-foreground font-medium">{ls.iterations}</span></div>}
                  {ls.avg_quality !== undefined && <div>平均质量: <span className="text-foreground font-medium">{ls.avg_quality}</span></div>}
                  {ls.groups !== undefined && <div>群组: <span className="text-foreground font-medium">{ls.groups}</span></div>}
                  {ls.agents !== undefined && <div>Agent: <span className="text-foreground font-medium">{ls.agents}</span></div>}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Analyze Tab ───────────────────────────────────── */}
      {activeTab === 'analyze' && (
        <div className="space-y-4">
          <div className="flex gap-2">
            <input
              value={analyzeInput}
              onChange={e => setAnalyzeInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleAnalyze()}
              placeholder="输入任务描述，如：帮我写一个系统架构方案"
              className="flex-1 px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary"
            />
            <button onClick={handleAnalyze} disabled={analyzing} className="px-2 lg:px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 font-medium transition-colors">
              {analyzing ? '分析中...' : '分析'}
            </button>
          </div>

          {analyzeResult && (
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
              <div className="flex items-center gap-3">
                <span className="text-sm text-muted-foreground">任务:</span>
                <span className="text-sm font-medium">{analyzeResult.message}</span>
              </div>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <div className="rounded-lg bg-muted/50 p-3">
                  <div className="text-xs text-muted-foreground mb-1">复杂度</div>
                  <span className={`inline-block px-2 py-0.5 rounded-full text-xs border ${COMPLEXITY_COLORS[analyzeResult.complexity] || ''}`}>
                    {analyzeResult.complexity}
                  </span>
                </div>
                <div className="rounded-lg bg-muted/50 p-3">
                  <div className="text-xs text-muted-foreground mb-1">预计时间</div>
                  <div className="text-sm font-medium">{analyzeResult.estimated_time}</div>
                </div>
                <div className="rounded-lg bg-muted/50 p-3 col-span-2">
                  <div className="text-xs text-muted-foreground mb-1">原因</div>
                  <div className="text-sm">{analyzeResult.reason}</div>
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-2">激活层</div>
                <div className="flex flex-wrap gap-2">
                  {LAYERS.map(layer => {
                    const active = analyzeResult.activate.includes(layer.key);
                    return (
                      <div key={layer.key} className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs border ${active ? `${layer.bg} ${layer.border} ${layer.color}` : 'bg-muted/30 border-border text-muted-foreground opacity-40'}`}>
                        {layer.icon}
                        {layer.label}
                        {active && <CheckCircle className="w-3 h-3" />}
                      </div>
                    );
                  })}
                </div>
              </div>
              {analyzeResult.suggested_agents?.length > 0 && (
                <div>
                  <div className="text-xs text-muted-foreground mb-2">建议Agent</div>
                  <div className="flex flex-wrap gap-2">
                    {analyzeResult.suggested_agents.map((a, i) => (
                      <div key={i} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-muted/50 border border-border text-xs">
                        <span className="font-medium">{a.role}</span>
                        <span className="text-muted-foreground">{a.model}</span>
                        <span className="text-muted-foreground">— {a.reason}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Context Tab ───────────────────────────────────── */}
      {activeTab === 'context' && context && (
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-card p-5">
            <h3 className="text-sm font-semibold mb-4">上下文窗口使用</h3>
            <div className="flex items-center gap-3 mb-3">
              <div className="flex-1 h-4 rounded-full bg-muted overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${context.usage_percent > 80 ? 'bg-red-400' : context.usage_percent > 60 ? 'bg-yellow-400' : 'bg-green-400'}`}
                  style={{ width: `${Math.min(context.usage_percent, 100)}%` }}
                />
              </div>
              <span className="text-sm font-medium">{context.usage_percent}%</span>
            </div>
            <div className="text-xs text-muted-foreground">
              {context.used_tokens.toLocaleString()} / {context.total_tokens.toLocaleString()} tokens
            </div>
            {context.compression_needed && (
              <div className="mt-2 text-xs text-yellow-400 flex items-center gap-1">
                <Zap className="w-3 h-3" /> 建议压缩上下文
              </div>
            )}
          </div>

          <div className="rounded-xl border border-border bg-card p-5">
            <h3 className="text-sm font-semibold mb-3">上下文分层</h3>
            <div className="space-y-2">
              {Object.entries(context.layers || {}).map(([name, info]) => (
                <div key={name} className="flex items-center gap-3">
                  <span className="text-xs text-muted-foreground w-20 shrink-0">{name}</span>
                  <div className="flex-1 h-3 rounded-full bg-muted overflow-hidden">
                    <div className="h-full rounded-full bg-primary/60" style={{ width: `${Math.min(info.percent, 100)}%` }} />
                  </div>
                  <span className="text-xs w-24 text-right">{info.tokens.toLocaleString()} tokens</span>
                  <span className="text-xs text-muted-foreground w-12 text-right">{info.percent}%</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── Harness Tab ───────────────────────────────────── */}
      {activeTab === 'harness' && (
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-card p-5">
            <h3 className="text-sm font-semibold mb-3 flex items-center gap-2">
              <Shield className="w-4 h-4 text-yellow-400" /> 工具路由矩阵
            </h3>
            <div className="space-y-2">
              {toolRoutes.map((r, i) => (
                <div key={i} className="flex items-center gap-3 p-3 rounded-lg bg-muted/50 border border-border">
                  <code className="text-xs font-mono bg-muted px-2 py-1 rounded">{r.tool}</code>
                  <span className="text-xs px-2 py-0.5 rounded bg-primary/10 text-primary">{r.mode}</span>
                  <span className={`text-xs px-2 py-0.5 rounded ${r.permissions === 'readonly' ? 'bg-green-400/10 text-green-400' : 'bg-yellow-400/10 text-yellow-400'}`}>
                    {r.permissions === 'readonly' ? '只读' : '读写'}
                  </span>
                  <div className="flex-1" />
                  <div className="flex flex-wrap gap-1">
                    {r.guardrails.map((g, j) => (
                      <span key={j} className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground">{g}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── Graph Tab ─────────────────────────────────────── */}
      {activeTab === 'graph' && (
        <div className="space-y-4">
          {graphStatus && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              {[
                { label: '活跃群组', value: graphStatus.active_groups, color: 'text-blue-400' },
                { label: '总Agent', value: graphStatus.total_agents, color: 'text-green-400' },
                { label: '运行中任务', value: graphStatus.running_tasks, color: 'text-yellow-400' },
                { label: '已完成', value: graphStatus.completed_tasks, color: 'text-purple-400' },
              ].map((c, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-4">
                  <div className="text-xs text-muted-foreground mb-1">{c.label}</div>
                  <div className={`text-2xl font-bold ${c.color}`}>{c.value}</div>
                </div>
              ))}
            </div>
          )}

          {graphStatus?.roles && (
            <div className="rounded-xl border border-border bg-card p-5">
              <h3 className="text-sm font-semibold mb-3">Agent角色分布</h3>
              <div className="grid gap-2 sm:grid-cols-3">
                {Object.entries(graphStatus.roles).map(([role, info]) => (
                  <div key={role} className="flex items-center gap-3 p-3 rounded-lg bg-muted/50 border border-border">
                    <span className="text-sm font-medium capitalize">{role}</span>
                    <div className="flex-1" />
                    <span className="text-xs text-muted-foreground">总数 {info.count}</span>
                    <span className={`text-xs ${info.busy > 0 ? 'text-yellow-400' : 'text-green-400'}`}>
                      忙碌 {info.busy}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Decompose */}
          <div className="rounded-xl border border-border bg-card p-5 space-y-4">
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <Workflow className="w-4 h-4 text-red-400" /> 任务自动分解
            </h3>
            <div className="flex gap-2">
              <input
                value={decomposeInput}
                onChange={e => setDecomposeInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleDecompose()}
                placeholder="输入目标，如：编写系统架构方案"
                className="flex-1 px-3 py-2 text-sm rounded-lg bg-muted border border-border focus:outline-none focus:border-primary"
              />
              <button onClick={handleDecompose} disabled={decomposing} className="px-2 lg:px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 font-medium transition-colors">
                {decomposing ? '分解中...' : '分解'}
              </button>
            </div>

            {decomposeResult && (
              <div className="space-y-2">
                <div className="text-sm text-muted-foreground">
                  目标: <span className="text-foreground font-medium">{decomposeResult.goal}</span>
                  <span className="ml-3">预计: {decomposeResult.estimated_time}</span>
                </div>
                {decomposeResult.subtasks.map((st, i) => (
                  <div key={i} className="flex items-center gap-3 p-3 rounded-lg bg-muted/50 border border-border">
                    <span className="w-6 h-6 rounded-full bg-primary/10 text-primary text-xs flex items-center justify-center shrink-0">{st.order}</span>
                    <span className="text-sm flex-1">{st.goal}</span>
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      st.role === 'advisor' ? 'bg-blue-400/10 text-blue-400' :
                      st.role === 'executor' ? 'bg-green-400/10 text-green-400' :
                      'bg-purple-400/10 text-purple-400'
                    }`}>{st.role}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {loading && <div className="text-sm text-muted-foreground p-4 text-center">加载中...</div>}
    </div>
  );
}
