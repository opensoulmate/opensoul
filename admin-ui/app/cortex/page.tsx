'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  RefreshCw, X, Brain, Cpu, Zap, Activity, Send, Loader2,
  AlertCircle, CheckCircle, Target, Users, MessageSquare,
  BarChart3, Clock, ChevronDown, ChevronRight, Lightbulb,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { CreativeAlternatives } from '@/components/creative-alternatives';
import type { CreativeAlternativesPayload } from '@/components/creative-alternatives';

// ── Types ───────────────────────────────────────────────────

interface CortexStats {
  status: string;
  component: string;
  modules: Record<string, string>;
  usage: {
    plan_calls: number;
    agent_calls: number;
    think_calls: number;
    total_calls: number;
    errors: number;
    last_activity: number | null;
  };
}

interface PlanResult {
  goal: string;
  tasks: {
    index: number;
    description: string;
    dependencies: number[];
    priority: number;
  }[];
  alternatives?: CreativeAlternativesPayload;
}

interface AgentResult {
  topic: string;
  research?: string;
  analysis?: string;
  output?: string;
  [key: string]: any;
}

interface ThinkResult {
  reasoning?: string;
  answer?: string;
  confidence?: number;
  [key: string]: any;
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: number | null) {
  if (!ts) return '-';
  try {
    return new Date(ts * 1000).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  } catch { return '-'; }
}

// ── Main Page ───────────────────────────────────────────────

export default function CortexPage() {
  const [stats, setStats] = useState<CortexStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Plan
  const [planGoal, setPlanGoal] = useState('');
  const [planResult, setPlanResult] = useState<PlanResult | null>(null);
  const [planning, setPlanning] = useState(false);

  // Creativity（G1-③b-2 备选方案 on-demand 生成面）
  const [altProblem, setAltProblem] = useState('');
  const [altConstraints, setAltConstraints] = useState('');
  const [altResult, setAltResult] = useState<CreativeAlternativesPayload>();
  const [altGenerating, setAltGenerating] = useState(false);

  // Agent
  const [agentTopic, setAgentTopic] = useState('');
  const [agentResult, setAgentResult] = useState<AgentResult | null>(null);
  const [agentRunning, setAgentRunning] = useState(false);

  // Think
  const [thinkQuestion, setThinkQuestion] = useState('');
  const [thinkContext, setThinkContext] = useState('');
  const [thinkResult, setThinkResult] = useState<ThinkResult | null>(null);
  const [thinking, setThinking] = useState(false);

  const [activeTab, setActiveTab] = useState<'plan' | 'agent' | 'think' | 'creativity'>('plan');

  const fetchStats = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/cortex/stats');
      setStats(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchStats(); }, [fetchStats]);

  const handlePlan = async () => {
    if (!planGoal.trim()) return;
    try {
      setPlanning(true);
      setPlanResult(null);
      setError('');
      const data = await apiFetch('/api/cortex/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal: planGoal }),
      });
      setPlanResult(data);
      fetchStats();
    } catch (e: any) { setError(e.message); }
    finally { setPlanning(false); }
  };

  // G1-③b-2 备选方案 on-demand 生成/重生成：每次真调用创造力引擎（无缓存）。
  // 载荷走后端 alternatives_payload 归一（payload 加性键），前端零哨兵分类。
  const handleGenerateAlternatives = async () => {
    if (!altProblem.trim()) return;
    try {
      setAltGenerating(true);
      setAltResult(undefined);
      setError('');
      const params = new URLSearchParams({ problem: altProblem.trim(), count: '3' });
      if (altConstraints.trim()) params.set('constraints', altConstraints.trim());
      const data = await apiFetch(`/api/brain/creativity?${params.toString()}`, { method: 'POST' });
      setAltResult(data.payload);
    } catch (e: any) { setError(e.message); }
    finally { setAltGenerating(false); }
  };

  const handleAgent = async () => {
    if (!agentTopic.trim()) return;
    try {
      setAgentRunning(true);
      setAgentResult(null);
      setError('');
      const data = await apiFetch('/api/cortex/agent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic: agentTopic }),
      });
      setAgentResult(data);
      fetchStats();
    } catch (e: any) { setError(e.message); }
    finally { setAgentRunning(false); }
  };

  const handleThink = async () => {
    if (!thinkQuestion.trim()) return;
    try {
      setThinking(true);
      setThinkResult(null);
      setError('');
      const data = await apiFetch('/api/cortex/think', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: thinkQuestion, context: thinkContext }),
      });
      setThinkResult(data);
      fetchStats();
    } catch (e: any) { setError(e.message); }
    finally { setThinking(false); }
  };

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* ── Stats ── */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <BarChart3 className="w-3.5 h-3.5 text-primary" />
            <span className="text-xs text-muted-foreground">总调用</span>
          </div>
          <div className="text-2xl font-bold">{stats?.usage?.total_calls || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Target className="w-3.5 h-3.5 text-blue-500" />
            <span className="text-xs text-muted-foreground">任务规划</span>
          </div>
          <div className="text-2xl font-bold text-blue-500">{stats?.usage?.plan_calls || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Users className="w-3.5 h-3.5 text-violet-500" />
            <span className="text-xs text-muted-foreground">多Agent</span>
          </div>
          <div className="text-2xl font-bold text-violet-500">{stats?.usage?.agent_calls || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <Brain className="w-3.5 h-3.5 text-amber-500" />
            <span className="text-xs text-muted-foreground">推理</span>
          </div>
          <div className="text-2xl font-bold text-amber-500">{stats?.usage?.think_calls || 0}</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-1">
            <AlertCircle className="w-3.5 h-3.5 text-red-500" />
            <span className="text-xs text-muted-foreground">错误</span>
          </div>
          <div className="text-2xl font-bold text-red-500">{stats?.usage?.errors || 0}</div>
        </div>
      </div>

      {/* ── Module Status ── */}
      {stats?.modules && (
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="flex items-center gap-2 mb-2">
            <Cpu className="w-4 h-4 text-primary" />
            <span className="text-sm font-medium">模块状态</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(stats.modules).map(([name, status]) => (
              <span key={name} className={`flex items-center gap-1.5 text-[10px] px-2 py-1 rounded ${
                status === 'available' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-red-500/10 text-red-500'
              }`}>
                {status === 'available' ? <CheckCircle className="w-2.5 h-2.5" /> : <AlertCircle className="w-2.5 h-2.5" />}
                {name}
              </span>
            ))}
          </div>
          {stats.usage?.last_activity && (
            <div className="text-[10px] text-muted-foreground mt-2">
              最后活动: {formatTime(stats.usage.last_activity)}
            </div>
          )}
        </div>
      )}

      {/* ── Error ── */}
      {error && (
        <div className="flex items-center gap-2 p-2 text-xs text-red-500 bg-red-500/5 rounded-md border border-red-500/20">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          <span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')}><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* ── Cortex Tools ── */}
      <div className="rounded-lg border border-border bg-card">
        <div className="flex items-center border-b border-border">
          {[
            { key: 'plan' as const, label: '任务规划', icon: Target, color: 'text-blue-500' },
            { key: 'agent' as const, label: '多Agent', icon: Users, color: 'text-purple-500' },
            { key: 'think' as const, label: '链式推理', icon: Brain, color: 'text-amber-500' },
            { key: 'creativity' as const, label: '备选方案', icon: Lightbulb, color: 'text-violet-500' },
          ].map(tab => (
            <Button variant="bare"
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium transition-colors border-b-2 ${
                activeTab === tab.key
                  ? `border-primary ${tab.color}`
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <tab.icon className="w-3.5 h-3.5" />
              {tab.label}
            </Button>
          ))}
        </div>

        <div className="p-4">
          {/* ── Plan Tab ── */}
          {activeTab === 'plan' && (
            <div className="space-y-3">
              <div className="flex gap-2">
                <input
                  className="flex-1 px-3 py-2 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="输入目标，AI将分解为子任务... (如: 部署一个知识管理系统)"
                  value={planGoal}
                  onChange={(e) => setPlanGoal(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handlePlan()}
                />
                <Button onClick={handlePlan}
                  disabled={!planGoal.trim() || planning} variant="primary" className="gap-1.5">
                  {planning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Target className="w-3.5 h-3.5" />}
                  {planning ? '规划中...' : '分解'}
                </Button>
              </div>
              {planResult && (
                <div className="space-y-2">
                  <div className="text-xs font-medium text-primary">目标: {planResult.goal}</div>
                  <div className="space-y-1.5">
                    {planResult.tasks.map((task) => (
                      <div key={task.index} className="flex items-start gap-3 p-2.5 rounded-lg border border-border bg-muted/30">
                        <div className="w-6 h-6 rounded-full bg-blue-500/10 text-blue-500 flex items-center justify-center text-[10px] font-bold shrink-0">
                          {task.index + 1}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="text-xs">{task.description}</p>
                          <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground">
                            <span>优先级: {task.priority}</span>
                            {task.dependencies.length > 0 && (
                              <span>依赖: {task.dependencies.map(d => `#${d + 1}`).join(', ')}</span>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                  <CreativeAlternatives payload={planResult.alternatives} />
                </div>
              )}
            </div>
          )}

          {/* ── Agent Tab ── */}
          {activeTab === 'agent' && (
            <div className="space-y-3">
              <div className="text-[10px] text-muted-foreground mb-2">
                多Agent流水线: 研究员 → 分析师 → 撰写者，协作完成复杂主题
              </div>
              <div className="flex gap-2">
                <input
                  className="flex-1 px-3 py-2 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="输入研究主题... (如: 最新LLM推理优化技术)"
                  value={agentTopic}
                  onChange={(e) => setAgentTopic(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleAgent()}
                />
                <Button
                  onClick={handleAgent}
                  disabled={!agentTopic.trim() || agentRunning}
                  variant="chip-lg" className="flex items-center gap-1.5 bg-violet-500/10 text-violet-600 border border-violet-500/20 hover:bg-violet-500/20 disabled:opacity-50"
                >
                  {agentRunning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Users className="w-3.5 h-3.5" />}
                  {agentRunning ? '运行中...' : '启动'}
                </Button>
              </div>
              {agentResult && (
                <div className="space-y-2">
                  <div className="text-xs font-medium text-violet-500">主题: {agentResult.topic}</div>
                  {Object.entries(agentResult).filter(([k]) => k !== 'topic').map(([key, value]) => (
                    <div key={key} className="rounded-lg border border-border bg-muted/30 p-3">
                      <div className="text-[10px] font-medium text-muted-foreground mb-1 uppercase">{key}</div>
                      <p className="text-xs whitespace-pre-wrap">{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ── Think Tab ── */}
          {activeTab === 'think' && (
            <div className="space-y-3">
              <div className="text-[10px] text-muted-foreground mb-2">
                链式推理: 自我反思式推理，逐步分析问题并给出答案
              </div>
              <input
                className="w-full px-3 py-2 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                placeholder="输入问题..."
                value={thinkQuestion}
                onChange={(e) => setThinkQuestion(e.target.value)}
              />
              <textarea
                className="w-full px-3 py-2 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 h-16 resize-none"
                placeholder="上下文 (可选)..."
                value={thinkContext}
                onChange={(e) => setThinkContext(e.target.value)}
              />
              <Button
                onClick={handleThink}
                disabled={!thinkQuestion.trim() || thinking}
                variant="chip-lg" className="flex items-center gap-1.5 bg-amber-500/10 text-amber-600 border border-amber-500/20 hover:bg-amber-500/20 disabled:opacity-50"
              >
                {thinking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Brain className="w-3.5 h-3.5" />}
                {thinking ? '推理中...' : '开始推理'}
              </Button>
              {thinkResult && (
                <div className="space-y-2">
                  {thinkResult.reasoning && (
                    <div className="rounded-lg border border-border bg-muted/30 p-3">
                      <div className="text-[10px] font-medium text-muted-foreground mb-2">推理步骤</div>
                      <p className="text-xs text-muted-foreground whitespace-pre-wrap">{thinkResult.reasoning}</p>
                    </div>
                  )}
                  {thinkResult.answer && (
                    <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
                      <div className="text-[10px] font-medium text-amber-600 mb-1">答案</div>
                      <p className="text-xs">{thinkResult.answer}</p>
                    </div>
                  )}
                  {thinkResult.confidence !== undefined && (
                    <div className="text-[10px] text-muted-foreground">
                      置信度: {(thinkResult.confidence * 100).toFixed(0)}%
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* ── Creativity Tab（G1-③b-2 备选方案 on-demand 生成/重生成） ── */}
          {activeTab === 'creativity' && (
            <div className="space-y-3">
              <div className="text-[10px] text-muted-foreground mb-2">
                创造力引擎：针对问题按需生成多条备选方案（每次都是真调用，可反复重新生成）
              </div>
              <textarea
                className="w-full px-3 py-2 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 h-16 resize-none"
                placeholder="描述要解决的问题... (如: 设计一个高可用的文件同步方案)"
                value={altProblem}
                onChange={(e) => setAltProblem(e.target.value)}
              />
              <div className="flex gap-2">
                <input
                  className="flex-1 px-3 py-2 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  placeholder="约束条件，逗号分隔（可选）... (如: 预算有限, 必须离线可用)"
                  value={altConstraints}
                  onChange={(e) => setAltConstraints(e.target.value)}
                />
                <Button onClick={handleGenerateAlternatives}
                  disabled={!altProblem.trim() || altGenerating} variant="primary" className="gap-1.5">
                  {altGenerating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Lightbulb className="w-3.5 h-3.5" />}
                  {altGenerating ? '生成中...' : altResult ? '重新生成' : '生成'}
                </Button>
              </div>
              <CreativeAlternatives payload={altResult} />
            </div>
          )}
        </div>
      </div>

      {/* ── Footer ── */}
      <div className="text-[10px] text-muted-foreground text-center">
        OpenCortex · 大脑皮层 · 任务规划 / 多Agent协作 / 链式推理 / 备选方案
      </div>
    </div>
  );
}
