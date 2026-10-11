'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Workflow, Plus, Trash2, Play, Pause, RefreshCw, X,
  Loader2, AlertCircle, CheckCircle, Clock, RotateCcw, Settings, PauseCircle
} from 'lucide-react';
import { Badge, TONES } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface WorkflowTask {
  id: string;
  name: string;
  description: string;
  task_type: string;
  config: Record<string, any>;
  schedule: string | null;
  status: string;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string;
}

interface WorkflowStats {
  status: string;
  component: string;
  total_tasks: number;
  active_tasks: number;
  by_type: Record<string, number>;
}

const STATUS_CONFIG: Record<string, { icon: any; color: string; bg: string; label: string }> = {
  idle: { icon: Clock, color: TONES.muted.text, bg: TONES.muted.bg, label: '空闲' },
  running: { icon: Loader2, color: TONES.info.text, bg: TONES.info.bg, label: '运行中' },
  active: { icon: CheckCircle, color: TONES.success.text, bg: TONES.success.bg, label: '活跃' },
  paused: { icon: PauseCircle, color: TONES.warning.text, bg: TONES.warning.bg, label: '已暂停' },
  error: { icon: AlertCircle, color: TONES.destructive.text, bg: TONES.destructive.bg, label: '错误' },
  completed: { icon: CheckCircle, color: TONES.success.text, bg: TONES.success.bg, label: '已完成' },
};

const TASK_TYPES = [
  { value: 'manual', label: '手动' },
  { value: 'scheduled', label: '定时' },
  { value: 'triggered', label: '触发式' },
  { value: 'pipeline', label: '流水线' },
];

function formatDate(s: string | null): string {
  if (!s) return '-';
  try { return new Date(s).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); } catch { return s; }
}

export default function WorkflowPage() {
  const [tasks, setTasks] = useState<WorkflowTask[]>([]);
  const [stats, setStats] = useState<WorkflowStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  // Create dialog
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ name: '', description: '', task_type: 'manual', schedule: '', config: '{}' });
  const [creating, setCreating] = useState(false);

  // Detail dialog
  const [selectedTask, setSelectedTask] = useState<WorkflowTask | null>(null);
  const [showDetail, setShowDetail] = useState(false);

  // Edit dialog
  const [showEdit, setShowEdit] = useState(false);
  const [editForm, setEditForm] = useState({ name: '', description: '', task_type: 'manual', schedule: '', config: '{}' });
  const [editing, setEditing] = useState(false);

  const fetchTasks = useCallback(async () => {
    try {
      setLoading(true);
      const params = statusFilter ? `?status=${statusFilter}` : '';
      const data = await apiFetch(`/api/workflow/tasks${params}`);
      setTasks(Array.isArray(data) ? data : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/workflow/stats');
      setStats(data);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchTasks(); fetchStats(); }, [fetchTasks, fetchStats]);

  const handleCreate = async () => {
    if (!createForm.name.trim()) return;
    try {
      setCreating(true);
      let config = {};
      try { config = JSON.parse(createForm.config); } catch { /* empty */ }
      await apiFetch('/api/workflow/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: createForm.name.trim(),
          description: createForm.description.trim(),
          task_type: createForm.task_type,
          schedule: createForm.schedule.trim() || null,
          config,
        }),
      });
      setShowCreate(false);
      setCreateForm({ name: '', description: '', task_type: 'manual', schedule: '', config: '{}' });
      fetchTasks();
      fetchStats();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handleRun = async (taskId: string) => {
    try {
      await apiFetch(`/api/workflow/tasks/${taskId}/run`, { method: 'POST' });
      fetchTasks();
      fetchStats();
    } catch (e: any) { setError(e.message); }
  };

  const handlePause = async (taskId: string) => {
    try {
      await apiFetch(`/api/workflow/tasks/${taskId}/pause`, { method: 'POST' });
      fetchTasks();
    } catch (e: any) { setError(e.message); }
  };

  const handleResume = async (taskId: string) => {
    try {
      await apiFetch(`/api/workflow/tasks/${taskId}/resume`, { method: 'POST' });
      fetchTasks();
    } catch (e: any) { setError(e.message); }
  };

  const handleDelete = async (task: WorkflowTask) => {
    if (!confirm(`确定要删除任务「${task.name}」？`)) return;
    try {
      await apiFetch(`/api/workflow/tasks/${task.id}`, { method: 'DELETE' });
      setTasks((prev) => prev.filter((t) => t.id !== task.id));
      fetchStats();
    } catch (e: any) { setError(e.message); }
  };

  const openEdit = (task: WorkflowTask) => {
    setEditForm({
      name: task.name,
      description: task.description,
      task_type: task.task_type,
      schedule: task.schedule || '',
      config: JSON.stringify(task.config, null, 2),
    });
    setShowEdit(true);
  };

  const filtered = tasks.filter((t) => {
    if (search) {
      const q = search.toLowerCase();
      return t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q) || t.task_type.toLowerCase().includes(q);
    }
    return true;
  });

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Error Banner */}
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
          <div className="text-2xl font-bold">{stats?.total_tasks ?? tasks.length}</div>
          <div className="text-xs text-muted-foreground">总任务数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{stats?.active_tasks ?? tasks.filter((t) => t.status === 'running' || t.status === 'active').length}</div>
          <div className="text-xs text-muted-foreground">活跃任务</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-amber-500">{tasks.filter((t) => t.status === 'paused').length}</div>
          <div className="text-xs text-muted-foreground">已暂停</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-gray-500">{tasks.filter((t) => t.status === 'idle').length}</div>
          <div className="text-xs text-muted-foreground">空闲</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索任务名称、描述..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">全部状态</option>
          {Object.entries(STATUS_CONFIG).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>
        <Button onClick={() => { fetchTasks(); fetchStats(); }} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowCreate(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />新建任务
        </Button>
      </div>

      {/* Task List */}
      {filtered.length === 0 ? (
        <div className="text-center py-12 text-sm text-muted-foreground">
          <Workflow className="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p>暂无工作流任务</p>
          <p className="text-xs mt-1">点击「新建任务」创建你的第一个工作流</p>
        </div>
      ) : (
        <div className="rounded-lg border border-border overflow-hidden">
          <div className="grid grid-cols-[1fr_100px_100px_120px_120px_100px] gap-2 px-4 py-2 text-xs font-medium text-muted-foreground bg-muted/30 border-b border-border">
            <span>任务名称</span>
            <span>类型</span>
            <span>状态</span>
            <span>上次运行</span>
            <span>创建时间</span>
            <span className="text-right">操作</span>
          </div>
          {filtered.map((task) => {
            const sc = STATUS_CONFIG[task.status] || STATUS_CONFIG.idle;
            const StatusIcon = sc.icon;
            return (
              <div
                key={task.id}
                className="grid grid-cols-[1fr_100px_100px_120px_120px_100px] gap-2 items-center px-4 py-2.5 border-b border-border last:border-b-0 hover:bg-muted/20 cursor-pointer transition-colors"
                onClick={() => { setSelectedTask(task); setShowDetail(true); }}
              >
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">{task.name}</div>
                  {task.description && <div className="text-[10px] text-muted-foreground truncate">{task.description}</div>}
                </div>
                <Badge variant="muted" className="w-fit">
                  {TASK_TYPES.find((t) => t.value === task.task_type)?.label || task.task_type}
                </Badge>
                <span className={`flex items-center gap-1 text-xs ${sc.color}`}>
                  <StatusIcon className={`w-3 h-3 ${task.status === 'running' ? 'animate-spin' : ''}`} />
                  {sc.label}
                </span>
                <span className="text-xs text-muted-foreground">{formatDate(task.last_run_at)}</span>
                <span className="text-xs text-muted-foreground">{formatDate(task.created_at)}</span>
                <div className="flex items-center gap-1 justify-end" onClick={(e) => e.stopPropagation()}>
                  {(task.status === 'idle' || task.status === 'active') && (
                    <Button onClick={() => handleRun(task.id)} variant="icon-sm" className="hover:bg-emerald-500/10 text-emerald-500" title="运行">
                      <Play className="w-3.5 h-3.5" />
                    </Button>
                  )}
                  {(task.status === 'running' || task.status === 'idle') && (
                    <Button onClick={() => handlePause(task.id)} variant="icon-sm" className="hover:bg-amber-500/10 text-amber-500" title="暂停">
                      <Pause className="w-3.5 h-3.5" />
                    </Button>
                  )}
                  {task.status === 'paused' && (
                    <Button onClick={() => handleResume(task.id)} variant="icon-sm" className="hover:bg-blue-500/10 text-blue-500" title="恢复">
                      <RotateCcw className="w-3.5 h-3.5" />
                    </Button>
                  )}
                  <Button onClick={() => handleDelete(task)} variant="icon-sm" className="hover:bg-red-500/10 text-red-500" title="删除">
                    <Trash2 className="w-3.5 h-3.5" />
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Create Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="bg-card border border-border rounded-xl shadow-xl w-full max-w-lg mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">新建工作流任务</h2>
              <Button onClick={() => setShowCreate(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">任务名称 *</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={createForm.name} onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))} placeholder="输入任务名称" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <textarea className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-none" rows={2} value={createForm.description} onChange={(e) => setCreateForm((f) => ({ ...f, description: e.target.value }))} placeholder="任务描述（可选）" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">任务类型</label>
                  <select className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none" value={createForm.task_type} onChange={(e) => setCreateForm((f) => ({ ...f, task_type: e.target.value }))}>
                    {TASK_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">调度表达式</label>
                  <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={createForm.schedule} onChange={(e) => setCreateForm((f) => ({ ...f, schedule: e.target.value }))} placeholder="如 0 */6 * * *" />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">配置 (JSON)</label>
                <textarea className="w-full px-3 py-1.5 text-xs font-mono bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-none" rows={3} value={createForm.config} onChange={(e) => setCreateForm((f) => ({ ...f, config: e.target.value }))} />
              </div>
            </div>
            <div className="flex justify-end gap-2 p-4 border-t border-border">
              <Button onClick={() => setShowCreate(false)} variant="secondary" className="px-4">取消</Button>
              <Button onClick={handleCreate} disabled={creating || !createForm.name.trim()} variant="primary" className="gap-1.5 px-4 py-1.5">
                {creating && <Loader2 className="w-3 h-3 animate-spin" />}创建
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Detail Dialog */}
      {showDetail && selectedTask && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDetail(false)}>
          <div className="bg-card border border-border rounded-xl shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">任务详情</h2>
              <Button onClick={() => setShowDetail(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div><span className="text-xs text-muted-foreground">名称</span><div className="text-sm font-medium">{selectedTask.name}</div></div>
                <div><span className="text-xs text-muted-foreground">类型</span><div className="text-sm">{TASK_TYPES.find((t) => t.value === selectedTask.task_type)?.label || selectedTask.task_type}</div></div>
                <div>
                  <span className="text-xs text-muted-foreground">状态</span>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    {(() => { const sc = STATUS_CONFIG[selectedTask.status] || STATUS_CONFIG.idle; const I = sc.icon; return <><I className={`w-3.5 h-3.5 ${sc.color}`} /><span className={`text-sm ${sc.color}`}>{sc.label}</span></>; })()}
                  </div>
                </div>
                <div><span className="text-xs text-muted-foreground">调度</span><div className="text-sm font-mono">{selectedTask.schedule || '无'}</div></div>
                <div><span className="text-xs text-muted-foreground">上次运行</span><div className="text-sm">{formatDate(selectedTask.last_run_at)}</div></div>
                <div><span className="text-xs text-muted-foreground">下次运行</span><div className="text-sm">{formatDate(selectedTask.next_run_at)}</div></div>
                <div><span className="text-xs text-muted-foreground">创建时间</span><div className="text-sm">{formatDate(selectedTask.created_at)}</div></div>
                <div><span className="text-xs text-muted-foreground">ID</span><div className="text-xs font-mono text-muted-foreground truncate">{selectedTask.id}</div></div>
              </div>
              {selectedTask.description && (
                <div>
                  <span className="text-xs text-muted-foreground">描述</span>
                  <div className="text-sm mt-1 p-2 bg-muted rounded-md">{selectedTask.description}</div>
                </div>
              )}
              {Object.keys(selectedTask.config).length > 0 && (
                <div>
                  <span className="text-xs text-muted-foreground">配置</span>
                  <pre className="text-xs font-mono mt-1 p-2 bg-muted rounded-md overflow-x-auto">{JSON.stringify(selectedTask.config, null, 2)}</pre>
                </div>
              )}
            </div>
            <div className="flex justify-between p-4 border-t border-border">
              <div className="flex gap-2">
                <Button onClick={() => { setShowDetail(false); openEdit(selectedTask); }} variant="secondary">
                  <Settings className="w-3 h-3" />编辑
                </Button>
              </div>
              <div className="flex gap-2">
                {(selectedTask.status === 'idle' || selectedTask.status === 'active') && (
                  <Button onClick={() => { handleRun(selectedTask.id); setShowDetail(false); }} variant="chip-sm" className="flex items-center gap-1.5 bg-emerald-500 text-white hover:bg-emerald-600">
                    <Play className="w-3 h-3" />运行
                  </Button>
                )}
                {(selectedTask.status === 'running' || selectedTask.status === 'idle') && (
                  <Button onClick={() => { handlePause(selectedTask.id); setShowDetail(false); }} variant="chip-sm" className="flex items-center gap-1.5 bg-amber-500 text-white hover:bg-amber-600">
                    <Pause className="w-3 h-3" />暂停
                  </Button>
                )}
                {selectedTask.status === 'paused' && (
                  <Button onClick={() => { handleResume(selectedTask.id); setShowDetail(false); }} variant="chip-sm" className="flex items-center gap-1.5 bg-blue-500 text-white hover:bg-blue-600">
                    <RotateCcw className="w-3 h-3" />恢复
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Edit Dialog */}
      {showEdit && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowEdit(false)}>
          <div className="bg-card border border-border rounded-xl shadow-xl w-full max-w-lg mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">编辑任务</h2>
              <Button onClick={() => setShowEdit(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">任务名称</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={editForm.name} onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <textarea className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-none" rows={2} value={editForm.description} onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">任务类型</label>
                  <select className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none" value={editForm.task_type} onChange={(e) => setEditForm((f) => ({ ...f, task_type: e.target.value }))}>
                    {TASK_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">调度表达式</label>
                  <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={editForm.schedule} onChange={(e) => setEditForm((f) => ({ ...f, schedule: e.target.value }))} />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">配置 (JSON)</label>
                <textarea className="w-full px-3 py-1.5 text-xs font-mono bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-none" rows={3} value={editForm.config} onChange={(e) => setEditForm((f) => ({ ...f, config: e.target.value }))} />
              </div>
            </div>
            <div className="flex justify-end gap-2 p-4 border-t border-border">
              <Button onClick={() => setShowEdit(false)} variant="secondary" className="px-4">取消</Button>
              <Button onClick={() => { setShowEdit(false); /* TODO: PUT endpoint not available yet */ }} disabled={editing} variant="primary" className="gap-1.5 px-4 py-1.5">
                {editing && <Loader2 className="w-3 h-3 animate-spin" />}保存
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
