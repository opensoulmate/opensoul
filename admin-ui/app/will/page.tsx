'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, Play, Pause, Trash2, Plus, Edit3,
  Workflow, ListChecks, Clock, CheckCircle, XCircle, AlertTriangle,
  Loader2, ChevronDown, ChevronRight, Zap, ArrowRight, Settings,
  GitBranch, Timer, Eye, Activity,
} from 'lucide-react';

// ── Types ───────────────────────────────────────────────────

interface WorkflowNode {
  id: string;
  node_type: string;
  label: string;
  config: Record<string, any>;
  position: { x: number; y: number };
}

interface WorkflowEdge {
  id: string;
  source_node_id: string;
  target_node_id: string;
  condition: string | null;
  label: string;
}

interface WorkflowItem {
  id: string;
  name: string;
  description: string;
  status: string;
  trigger: string;
  trigger_config: Record<string, any>;
  variables: Record<string, any>;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  created_at: string;
  updated_at: string;
}

interface Execution {
  id: string;
  workflow_id: string;
  workflow_name?: string;
  status: string;
  variables: Record<string, any>;
  started_at: string;
  completed_at?: string;
  error?: string;
  steps?: any[];
}

interface Stats {
  total_workflows?: number;
  active_workflows?: number;
  total_executions?: number;
  success_rate?: number;
  [key: string]: any;
}

// ── Helpers ─────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30',
  paused: 'bg-amber-500/10 text-amber-500 border-amber-500/30',
  draft: 'bg-muted text-muted-foreground border-border',
  running: 'bg-blue-500/10 text-blue-500 border-blue-500/30',
  completed: 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30',
  failed: 'bg-red-500/10 text-red-500 border-red-500/30',
  cancelled: 'bg-muted text-muted-foreground border-border',
  pending: 'bg-amber-500/10 text-amber-500 border-amber-500/30',
};

const TRIGGER_LABELS: Record<string, string> = {
  manual: '手动触发',
  schedule: '定时触发',
  event: '事件触发',
  webhook: 'Webhook',
};

const NODE_TYPE_ICONS: Record<string, typeof Zap> = {
  start: Play,
  end: CheckCircle,
  action: Zap,
  condition: GitBranch,
};

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  } catch { return ts; }
}

function formatDuration(start: string, end?: string) {
  if (!start) return '-';
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const diff = e - s;
  if (diff < 1000) return `${diff}ms`;
  if (diff < 60000) return `${(diff / 1000).toFixed(1)}s`;
  return `${Math.floor(diff / 60000)}m ${Math.floor((diff % 60000) / 1000)}s`;
}

// ── Workflow Card ───────────────────────────────────────────

function WorkflowCard({ wf, onExecute, onDelete, expanded, onToggle }: {
  wf: WorkflowItem;
  onExecute: () => void;
  onDelete: () => void;
  expanded: boolean;
  onToggle: () => void;
}) {
  const statusClass = STATUS_COLORS[wf.status] || STATUS_COLORS.draft;
  const nodeTypes = wf.nodes.reduce((acc, n) => {
    acc[n.node_type] = (acc[n.node_type] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  return (
    <div className="border border-border rounded-lg bg-card">
      <button onClick={onToggle} className="flex items-center gap-3 w-full p-3 text-left hover:bg-muted/30 transition-colors">
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
          wf.status === 'active' ? 'bg-green-500/10' : wf.status === 'paused' ? 'bg-amber-500/10' : 'bg-muted'
        }`}>
          <Workflow className={`w-5 h-5 ${
            wf.status === 'active' ? 'text-green-500' : wf.status === 'paused' ? 'text-amber-500' : 'text-muted-foreground'
          }`} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium truncate">{wf.name}</span>
            <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium border ${statusClass}`}>{wf.status}</span>
            <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground">
              {TRIGGER_LABELS[wf.trigger] || wf.trigger}
            </span>
          </div>
          {wf.description && <p className="text-[10px] text-muted-foreground truncate mt-0.5">{wf.description}</p>}
          <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground">
            <span>{wf.nodes.length} 节点</span>
            <span>{wf.edges.length} 连接</span>
            {Object.entries(nodeTypes).map(([type, count]) => (
              <span key={type}>{type}×{count}</span>
            ))}
          </div>
        </div>
        <div className="text-right shrink-0 mr-2">
          <div className="text-[10px] text-muted-foreground">{formatTime(wf.updated_at)}</div>
        </div>
        {expanded ? <ChevronDown className="w-3.5 h-3.5 shrink-0" /> : <ChevronRight className="w-3.5 h-3.5 shrink-0" />}
      </button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-border pt-2 space-y-3">
          {/* Node list */}
          <div>
            <div className="text-xs font-medium mb-1.5">节点 ({wf.nodes.length})</div>
            <div className="flex flex-wrap gap-2">
              {wf.nodes.map((node) => {
                const Icon = NODE_TYPE_ICONS[node.node_type] || Zap;
                return (
                  <div key={node.id} className="flex items-center gap-1.5 px-2 py-1 rounded bg-muted text-xs">
                    <Icon className="w-3 h-3 text-muted-foreground" />
                    <span>{node.label || node.node_type}</span>
                    <span className="text-[9px] text-muted-foreground font-mono">({node.id.slice(0, 6)})</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Edges */}
          {wf.edges.length > 0 && (
            <div>
              <div className="text-xs font-medium mb-1.5">连接 ({wf.edges.length})</div>
              <div className="space-y-1">
                {wf.edges.map((edge) => {
                  const source = wf.nodes.find(n => n.id === edge.source_node_id);
                  const target = wf.nodes.find(n => n.id === edge.target_node_id);
                  return (
                    <div key={edge.id} className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                      <span className="font-mono">{source?.label || edge.source_node_id.slice(0, 6)}</span>
                      <ArrowRight className="w-3 h-3" />
                      <span className="font-mono">{target?.label || edge.target_node_id.slice(0, 6)}</span>
                      {edge.condition && (
                        <span className="px-1 py-0.5 rounded bg-amber-500/10 text-amber-500 text-[9px]">
                          if: {edge.condition.slice(0, 40)}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Variables */}
          {Object.keys(wf.variables).length > 0 && (
            <div>
              <div className="text-xs font-medium mb-1.5">变量</div>
              <div className="flex flex-wrap gap-1">
                {Object.entries(wf.variables).map(([k, v]) => (
                  <span key={k} className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-primary/10 text-primary font-mono">
                    {k}={JSON.stringify(v).slice(0, 20)}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-2 pt-2 border-t border-border">
            {wf.status === 'active' && (
              <button onClick={onExecute} className="flex items-center gap-1 px-2 py-1 text-[10px] bg-emerald-500/10 text-emerald-500 rounded-lg hover:bg-emerald-500/20">
                <Play className="w-3 h-3" />执行
              </button>
            )}
            <button onClick={onDelete} className="flex items-center gap-1 px-2 py-1 text-[10px] bg-red-500/10 text-red-500 rounded-lg hover:bg-red-500/20">
              <Trash2 className="w-3 h-3" />删除
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Execution Card ──────────────────────────────────────────

function ExecutionCard({ exec }: { exec: Execution }) {
  const statusClass = STATUS_COLORS[exec.status] || STATUS_COLORS.pending;
  const statusIcon = exec.status === 'completed' ? CheckCircle :
    exec.status === 'failed' ? XCircle :
    exec.status === 'running' ? Loader2 :
    Clock;

  const Icon = statusIcon;

  return (
    <div className="flex items-center gap-3 py-2 px-3 rounded-lg border border-border bg-card hover:bg-muted/30 transition-colors">
      <Icon className={`w-4 h-4 shrink-0 ${
        exec.status === 'completed' ? 'text-green-500' :
        exec.status === 'failed' ? 'text-red-500' :
        exec.status === 'running' ? 'text-blue-500 animate-spin' :
        'text-muted-foreground'
      }`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium truncate">{exec.workflow_name || exec.workflow_id.slice(0, 12)}</span>
          <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium border ${statusClass}`}>{exec.status}</span>
        </div>
        {exec.error && <p className="text-[10px] text-red-500 truncate mt-0.5">{exec.error}</p>}
      </div>
      <div className="text-right shrink-0">
        <div className="text-[10px] text-muted-foreground">{formatTime(exec.started_at)}</div>
        <div className="text-[10px] text-muted-foreground">{formatDuration(exec.started_at, exec.completed_at)}</div>
      </div>
    </div>
  );
}

// ── Create Workflow Dialog ──────────────────────────────────

function CreateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [trigger, setTrigger] = useState('manual');
  const [saving, setSaving] = useState(false);

  const handleCreate = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await apiFetch('/api/will/workflows', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description, trigger }),
      });
      onCreated();
    } catch (e: any) {
      alert(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h2 className="text-sm font-medium">创建工作流</h2>
          <button onClick={onClose} className="p-1 hover:bg-muted rounded-md"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-4 space-y-3">
          <div>
            <label className="text-xs text-muted-foreground">名称</label>
            <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={name} onChange={e => setName(e.target.value)} placeholder="工作流名称" />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">描述</label>
            <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={description} onChange={e => setDescription(e.target.value)} placeholder="可选描述" />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">触发方式</label>
            <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={trigger} onChange={e => setTrigger(e.target.value)}>
              <option value="manual">手动触发</option>
              <option value="schedule">定时触发</option>
              <option value="event">事件触发</option>
              <option value="webhook">Webhook</option>
            </select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button onClick={onClose} className="px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted">取消</button>
            <button onClick={handleCreate} disabled={!name.trim() || saving} className="flex items-center gap-2 px-2 lg:px-2 lg:px-4 py-2 text-xs lg:text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 font-medium transition-colors">
              {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
              创建
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function WillPage() {
  const [workflows, setWorkflows] = useState<WorkflowItem[]>([]);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [actionTypes, setActionTypes] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [tab, setTab] = useState<'workflows' | 'executions' | 'actions'>('workflows');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [wfRes, execRes, statsRes, actionsRes] = await Promise.allSettled([
        apiFetch('/api/will/workflows'),
        apiFetch('/api/will/executions'),
        apiFetch('/api/will/stats'),
        apiFetch('/api/will/action-types'),
      ]);
      if (wfRes.status === 'fulfilled') setWorkflows(Array.isArray(wfRes.value) ? wfRes.value : wfRes.value.workflows || []);
      if (execRes.status === 'fulfilled') setExecutions(Array.isArray(execRes.value) ? execRes.value : execRes.value.executions || []);
      if (statsRes.status === 'fulfilled') setStats(statsRes.value);
      if (actionsRes.status === 'fulfilled') setActionTypes(actionsRes.value.action_types || []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleExecute = async (id: string) => {
    try {
      await apiFetch(`/api/will/workflows/${id}/execute`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('确定删除此工作流？')) return;
    try {
      await apiFetch(`/api/will/workflows/${id}`, { method: 'DELETE' });
      fetchData();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const filtered = workflows.filter(wf => {
    if (!search) return true;
    const q = search.toLowerCase();
    return wf.name.toLowerCase().includes(q) || wf.description.toLowerCase().includes(q);
  });

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.total_workflows ?? workflows.length}</div>
          <div className="text-xs text-muted-foreground">工作流总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-500">{stats?.active_workflows ?? workflows.filter(w => w.status === 'active').length}</div>
          <div className="text-xs text-muted-foreground">活跃中</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{stats?.total_executions ?? executions.length}</div>
          <div className="text-xs text-muted-foreground">执行次数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.success_rate != null ? `${(stats.success_rate * 100).toFixed(0)}%` : '-'}</div>
          <div className="text-xs text-muted-foreground">成功率</div>
        </div>
      </div>

      {/* Tabs + Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1 bg-muted rounded-lg p-0.5">
          {[
            { id: 'workflows' as const, label: '工作流', icon: Workflow },
            { id: 'executions' as const, label: '执行记录', icon: ListChecks },
            { id: 'actions' as const, label: '动作类型', icon: Zap },
          ].map(t => {
            const Icon = t.icon;
            return (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm rounded-lg transition-colors ${
                  tab === t.id ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
                }`}>
                <Icon className="w-3.5 h-3.5" />{t.label}
              </button>
            );
          })}
        </div>

        {tab === 'workflows' && (
          <>
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <input className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                placeholder="搜索工作流..." value={search} onChange={e => setSearch(e.target.value)} />
            </div>
            <button onClick={() => setShowCreate(true)} className="flex items-center gap-1.5 px-2 lg:px-2 lg:px-4 py-2 text-xs lg:text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 font-medium transition-colors">
              <Plus className="w-3.5 h-3.5" />创建工作流
            </button>
          </>
        )}
        <button onClick={fetchData} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500" />
          <span className="text-xs text-red-500">{error}</span>
          <button onClick={() => setError('')} className="ml-auto"><X className="w-3 h-3" /></button>
        </div>
      )}

      {/* Workflows tab */}
      {tab === 'workflows' && (
        <div className="space-y-2">
          {filtered.map(wf => (
            <WorkflowCard
              key={wf.id}
              wf={wf}
              expanded={expandedId === wf.id}
              onToggle={() => setExpandedId(expandedId === wf.id ? null : wf.id)}
              onExecute={() => handleExecute(wf.id)}
              onDelete={() => handleDelete(wf.id)}
            />
          ))}
          {filtered.length === 0 && (
            <div className="text-center py-12 text-muted-foreground">
              <Workflow className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-xs">{search ? '无匹配工作流' : '暂无工作流'}</p>
            </div>
          )}
        </div>
      )}

      {/* Executions tab */}
      {tab === 'executions' && (
        <div className="space-y-1">
          {executions.length > 0 ? executions.map(exec => (
            <ExecutionCard key={exec.id} exec={exec} />
          )) : (
            <div className="text-center py-12 text-muted-foreground">
              <ListChecks className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-xs">暂无执行记录</p>
            </div>
          )}
        </div>
      )}

      {/* Action types tab */}
      {tab === 'actions' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {actionTypes.map((action) => (
            <div key={action.type} className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-center gap-2 mb-2">
                <Zap className="w-4 h-4 text-primary" />
                <span className="text-sm font-medium">{action.label}</span>
                <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-muted text-muted-foreground font-mono">{action.type}</span>
              </div>
              <p className="text-xs text-muted-foreground mb-2">{action.description}</p>
              {action.config_schema && (
                <div className="space-y-1">
                  <div className="text-[10px] font-medium text-muted-foreground">配置参数:</div>
                  {Object.entries(action.config_schema).map(([key, schema]: [string, any]) => (
                    <div key={key} className="flex items-center gap-2 text-[10px]">
                      <span className="font-mono text-primary">{key}</span>
                      {schema.required && <span className="text-red-500">*</span>}
                      <span className="text-muted-foreground">{schema.type}</span>
                      {schema.default !== undefined && <span className="text-muted-foreground">默认: {JSON.stringify(schema.default)}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
          {actionTypes.length === 0 && (
            <div className="text-center py-12 text-muted-foreground col-span-2">
              <Zap className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-xs">暂无动作类型</p>
            </div>
          )}
        </div>
      )}

      {/* Create dialog */}
      {showCreate && <CreateDialog onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); fetchData(); }} />}
    </div>
  );
}
