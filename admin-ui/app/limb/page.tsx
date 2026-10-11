'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Plus, Trash2, Play, Pause, RefreshCw, X, CheckCircle, XCircle,
  Clock, Loader2, AlertCircle, Eye, Settings, Monitor, Keyboard, Mouse,
  Camera, FileText, History, LayoutTemplate, Zap, ChevronDown, ChevronRight,
  Square, Copy
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface RPATask {
  task_id: string;
  name: string;
  status: string;
  priority: number;
  tags: string[];
  actions: any[];
  variables: Record<string, any>;
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  results?: any[];
  error?: string;
}

interface RPATemplate {
  template_id: string;
  name: string;
  description: string;
  category: string;
  actions: any[];
  variables: any[];
  tags: string[];
  is_builtin?: boolean;
}

interface HistoryRecord {
  task_id: string;
  name: string;
  status: string;
  started_at: string;
  finished_at?: string;
  duration_ms?: number;
}

interface WindowInfo {
  id: string;
  title: string;
  app?: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
}

const STATUS_MAP: Record<string, { label: string; color: string; icon: typeof CheckCircle }> = {
  queued: { label: '排队中', color: 'text-gray-500 bg-gray-500/10', icon: Clock },
  running: { label: '运行中', color: 'text-blue-500 bg-blue-500/10', icon: Loader2 },
  completed: { label: '已完成', color: 'text-green-600 bg-green-500/10', icon: CheckCircle },
  failed: { label: '失败', color: 'text-red-500 bg-red-500/10', icon: XCircle },
  cancelled: { label: '已取消', color: 'text-orange-500 bg-orange-500/10', icon: Pause },
};

export default function LimbPage() {
  const [tab, setTab] = useState<'tasks' | 'templates' | 'history' | 'tools'>('tasks');
  const [tasks, setTasks] = useState<RPATask[]>([]);
  const [templates, setTemplates] = useState<RPATemplate[]>([]);
  const [history, setHistory] = useState<HistoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [showCreateTask, setShowCreateTask] = useState(false);
  const [showCreateTemplate, setShowCreateTemplate] = useState(false);
  const [selectedTask, setSelectedTask] = useState<RPATask | null>(null);
  const [executing, setExecuting] = useState<string | null>(null);

  // RPA Tools state
  const [windows, setWindows] = useState<WindowInfo[]>([]);
  const [screenshotResult, setScreenshotResult] = useState<string | null>(null);
  const [ocrResult, setOcrResult] = useState<string | null>(null);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [typeText, setTypeText] = useState('');
  const [clickX, setClickX] = useState('500');
  const [clickY, setClickY] = useState('300');

  // Task form
  const [taskForm, setTaskForm] = useState({
    name: '', actions: '[\n  {"type": "wait", "seconds": 1}\n]', priority: '5', tags: '', variables: '{}',
  });

  // Template form
  const [tmplForm, setTmplForm] = useState({
    name: '', description: '', category: 'custom',
    actions: '[]', variables: '[]', tags: '',
  });

  const fetchTasks = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (statusFilter !== 'all') params.set('status', statusFilter);
      const qs = params.toString() ? `?${params}` : '';
      const data = await apiFetch(`/api/limb/tasks${qs}`);
      setTasks(Array.isArray(data.tasks) ? data.tasks : []);
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  }, [statusFilter]);

  const fetchTemplates = useCallback(async () => {
    try {
      const data = await apiFetch('/api/limb/templates');
      setTemplates(Array.isArray(data.templates) ? data.templates : []);
    } catch (e: any) { setError(e.message); }
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const data = await apiFetch('/api/limb/history?limit=100');
      setHistory(Array.isArray(data.history) ? data.history : []);
    } catch (e: any) { setError(e.message); }
  }, []);

  const fetchWindows = async () => {
    try {
      const data = await apiFetch('/api/limb/rpa/windows');
      setWindows(Array.isArray(data.windows) ? data.windows : []);
    } catch (e: any) { setError(e.message); }
  };

  useEffect(() => { fetchTasks(); }, [fetchTasks]);

  const handleCreateTask = async () => {
    try {
      let actions, variables;
      try { actions = JSON.parse(taskForm.actions); } catch { setError('Actions JSON格式错误'); return; }
      try { variables = JSON.parse(taskForm.variables); } catch { setError('Variables JSON格式错误'); return; }
      await apiFetch('/api/limb/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: taskForm.name, actions, priority: parseInt(taskForm.priority) || 5,
          tags: taskForm.tags ? taskForm.tags.split(',').map(t => t.trim()) : [],
          variables,
        }),
      });
      setShowCreateTask(false);
      setTaskForm({ name: '', actions: '[\n  {"type": "wait", "seconds": 1}\n]', priority: '5', tags: '', variables: '{}' });
      await fetchTasks();
    } catch (e: any) { setError(e.message); }
  };

  const handleExecuteTask = async (taskId: string) => {
    try {
      setExecuting(taskId);
      await apiFetch(`/api/limb/tasks/${taskId}/execute`, { method: 'POST' });
      await fetchTasks();
    } catch (e: any) { setError(e.message); }
    finally { setExecuting(null); }
  };

  const handleCancelTask = async (taskId: string) => {
    try {
      await apiFetch(`/api/limb/tasks/${taskId}/cancel`, { method: 'POST' });
      await fetchTasks();
    } catch (e: any) { setError(e.message); }
  };

  const handleDeleteTask = async (taskId: string, name: string) => {
    if (!confirm(`确定删除任务「${name}」？`)) return;
    try {
      await apiFetch(`/api/limb/tasks/${taskId}`, { method: 'DELETE' });
      await fetchTasks();
    } catch (e: any) { setError(e.message); }
  };

  const handleCreateTemplate = async () => {
    try {
      let actions, variables;
      try { actions = JSON.parse(tmplForm.actions); } catch { setError('Actions JSON格式错误'); return; }
      try { variables = JSON.parse(tmplForm.variables); } catch { setError('Variables JSON格式错误'); return; }
      await apiFetch('/api/limb/templates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: tmplForm.name, description: tmplForm.description, category: tmplForm.category,
          actions, variables,
          tags: tmplForm.tags ? tmplForm.tags.split(',').map(t => t.trim()) : [],
        }),
      });
      setShowCreateTemplate(false);
      setTmplForm({ name: '', description: '', category: 'custom', actions: '[]', variables: '[]', tags: '' });
      await fetchTemplates();
    } catch (e: any) { setError(e.message); }
  };

  const handleInstantiateTemplate = async (templateId: string) => {
    try {
      const data = await apiFetch(`/api/limb/templates/${templateId}/instantiate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ variables: {} }),
      });
      await fetchTasks();
      setTab('tasks');
    } catch (e: any) { setError(e.message); }
  };

  const handleDeleteTemplate = async (templateId: string, name: string) => {
    if (!confirm(`确定删除模板「${name}」？`)) return;
    try {
      await apiFetch(`/api/limb/templates/${templateId}`, { method: 'DELETE' });
      await fetchTemplates();
    } catch (e: any) { setError(e.message); }
  };

  const handleScreenshot = async () => {
    try {
      const data = await apiFetch('/api/limb/rpa/screenshot', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      if (data.image) setScreenshotResult(`data:image/png;base64,${data.image}`);
    } catch (e: any) { setError(e.message); }
  };

  const handleOCR = async () => {
    try {
      setOcrLoading(true);
      const data = await apiFetch('/api/limb/rpa/ocr', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      setOcrResult(data.text || JSON.stringify(data, null, 2));
    } catch (e: any) { setError(e.message); }
    finally { setOcrLoading(false); }
  };

  const handleClick = async () => {
    try {
      await apiFetch('/api/limb/rpa/click', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ x: parseInt(clickX), y: parseInt(clickY) }),
      });
    } catch (e: any) { setError(e.message); }
  };

  const handleType = async () => {
    if (!typeText) return;
    try {
      await apiFetch('/api/limb/rpa/type', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: typeText }),
      });
    } catch (e: any) { setError(e.message); }
  };

  const filteredTasks = tasks.filter((t) => {
    if (search) {
      const q = search.toLowerCase();
      return t.name.toLowerCase().includes(q) || t.task_id.toLowerCase().includes(q);
    }
    return true;
  });

  if (loading && tab === 'tasks') return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{tasks.length}</div>
          <div className="text-xs text-muted-foreground">任务总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{tasks.filter(t => t.status === 'running').length}</div>
          <div className="text-xs text-muted-foreground">运行中</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{tasks.filter(t => t.status === 'completed').length}</div>
          <div className="text-xs text-muted-foreground">已完成</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{templates.length}</div>
          <div className="text-xs text-muted-foreground">模板数</div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border">
        {([['tasks', '任务管理'], ['templates', '模板库'], ['history', '执行历史'], ['tools', 'RPA工具']] as const).map(([t, label]) => (
          <Button variant="bare" key={t} onClick={() => {
            setTab(t as any);
            if (t === 'templates') fetchTemplates();
            if (t === 'history') fetchHistory();
            if (t === 'tools') fetchWindows();
          }}
            className={`px-4 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${
              tab === t ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}>{label}</Button>
        ))}
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 text-red-500 text-xs">
          <AlertCircle className="w-3.5 h-3.5" /><span className="flex-1">{error}</span>
          <Button variant="bare" onClick={() => setError('')} className="p-0.5"><X className="w-3.5 h-3.5" /></Button>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索..." value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        {tab === 'tasks' && (
          <>
            <select className="px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
              value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="all">全部状态</option>
              <option value="queued">排队中</option>
              <option value="running">运行中</option>
              <option value="completed">已完成</option>
              <option value="failed">失败</option>
            </select>
            <Button onClick={fetchTasks} variant="secondary">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </Button>
            <Button onClick={() => setShowCreateTask(true)} variant="primary" className="gap-1.5">
              <Plus className="w-3.5 h-3.5" />新建任务
            </Button>
          </>
        )}
        {tab === 'templates' && (
          <Button onClick={() => setShowCreateTemplate(true)} variant="primary" className="gap-1.5">
            <Plus className="w-3.5 h-3.5" />新建模板
          </Button>
        )}
      </div>

      {/* Tasks Tab */}
      {tab === 'tasks' && (
        <div className="space-y-2">
          {filteredTasks.map((task) => {
            const st = STATUS_MAP[task.status] || STATUS_MAP.queued;
            const StIcon = st.icon;
            return (
              <div key={task.task_id} className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer"
                onClick={() => setSelectedTask(task)}>
                <div className="flex items-center gap-2">
                  <Badge variant="bare" className={`flex gap-0.5 ${st.color}`}>
                    <StIcon className={`w-2.5 h-2.5 ${task.status === 'running' ? 'animate-spin' : ''}`} />{st.label}
                  </Badge>
                  <span className="text-sm font-medium">{task.name}</span>
                  <Badge variant="muted">优先级 {task.priority}</Badge>
                  {task.tags?.map(tag => <Badge variant="info" key={tag}>{tag}</Badge>)}
                  <span className="text-[10px] text-muted-foreground ml-auto">{task.actions?.length ?? 0} 步</span>
                  {task.created_at && <span className="text-[10px] text-muted-foreground">{new Date(task.created_at).toLocaleString('zh-CN')}</span>}
                </div>
                <div className="flex items-center gap-1.5 mt-3 pt-3 border-t border-border" onClick={(e) => e.stopPropagation()}>
                  {(task.status === 'queued') && (
                    <Button onClick={() => handleExecuteTask(task.task_id)} disabled={executing === task.task_id}
                      variant="chip-xs" className="flex items-center gap-1 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 disabled:opacity-50">
                      {executing === task.task_id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}执行
                    </Button>
                  )}
                  {task.status === 'running' && (
                    <Button onClick={() => handleCancelTask(task.task_id)}
                      variant="chip-xs" className="flex items-center gap-1 bg-amber-500/10 text-amber-500 hover:bg-amber-500/20">
                      <Square className="w-3 h-3" />取消
                    </Button>
                  )}
                  <Button onClick={() => handleDeleteTask(task.task_id, task.name)}
                    variant="chip-xs" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20">
                    <Trash2 className="w-3 h-3" />删除
                  </Button>
                </div>
              </div>
            );
          })}
          {filteredTasks.length === 0 && (
            <div className="text-center py-12 text-muted-foreground text-sm">
              <Zap className="w-8 h-8 mx-auto mb-2 opacity-30" /><p>暂无RPA任务</p>
            </div>
          )}
        </div>
      )}

      {/* Templates Tab */}
      {tab === 'templates' && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {templates.filter((t) => !search || t.name.toLowerCase().includes(search.toLowerCase())).map((tmpl) => (
            <div key={tmpl.template_id} className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-start gap-2">
                <LayoutTemplate className="w-4 h-4 text-primary mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-medium truncate">{tmpl.name}</h3>
                    {tmpl.is_builtin && <Badge variant="accent">内置</Badge>}
                  </div>
                  {tmpl.description && <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{tmpl.description}</p>}
                  <div className="flex items-center gap-2 mt-2 text-[10px] text-muted-foreground">
                    <span className="px-1.5 py-0.5 rounded bg-muted">{tmpl.category}</span>
                    <span>{tmpl.actions?.length ?? 0} 步</span>
                    {tmpl.tags?.map(tag => <span key={tag} className="px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-500">{tag}</span>)}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-1.5 mt-3 pt-3 border-t border-border">
                <Button onClick={() => handleInstantiateTemplate(tmpl.template_id)}
                  variant="chip-xs" className="flex items-center gap-1 bg-primary/10 text-primary hover:bg-primary/20">
                  <Copy className="w-3 h-3" />实例化
                </Button>
                {!tmpl.is_builtin && (
                  <Button onClick={() => handleDeleteTemplate(tmpl.template_id, tmpl.name)}
                    variant="chip-xs" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20">
                    <Trash2 className="w-3 h-3" />删除
                  </Button>
                )}
              </div>
            </div>
          ))}
          {templates.length === 0 && (
            <div className="col-span-full text-center py-12 text-muted-foreground text-sm">
              <LayoutTemplate className="w-8 h-8 mx-auto mb-2 opacity-30" /><p>暂无模板</p>
            </div>
          )}
        </div>
      )}

      {/* History Tab */}
      {tab === 'history' && (
        <div className="space-y-1">
          {history.filter((h) => !search || h.name.toLowerCase().includes(search.toLowerCase())).map((h, i) => {
            const st = STATUS_MAP[h.status] || STATUS_MAP.queued;
            return (
              <div key={`${h.task_id}-${i}`} className="rounded-lg border border-border bg-card p-3 flex items-center gap-3">
                <Badge variant="bare" className={`${st.color}`}>{st.label}</Badge>
                <span className="text-xs font-medium flex-1">{h.name}</span>
                {h.duration_ms !== undefined && <span className="text-[10px] text-muted-foreground">{h.duration_ms}ms</span>}
                <span className="text-[10px] text-muted-foreground">{new Date(h.started_at).toLocaleString('zh-CN')}</span>
              </div>
            );
          })}
          {history.length === 0 && (
            <div className="text-center py-12 text-muted-foreground text-sm">
              <History className="w-8 h-8 mx-auto mb-2 opacity-30" /><p>暂无执行记录</p>
            </div>
          )}
        </div>
      )}

      {/* RPA Tools Tab */}
      {tab === 'tools' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Screenshot */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-semibold mb-3 flex items-center gap-2"><Camera className="w-3.5 h-3.5" />截图</h3>
            <Button onClick={handleScreenshot} variant="primary" className="gap-1.5 mb-3">
              <Camera className="w-3.5 h-3.5" />截取屏幕
            </Button>
            {screenshotResult && (
              <img src={screenshotResult} alt="Screenshot" className="w-full rounded border border-border" />
            )}
          </div>

          {/* OCR */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-semibold mb-3 flex items-center gap-2"><FileText className="w-3.5 h-3.5" />OCR文字识别</h3>
            <Button onClick={handleOCR} disabled={ocrLoading} variant="primary" className="gap-1.5 mb-3">
              {ocrLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileText className="w-3.5 h-3.5" />}识别屏幕文字
            </Button>
            {ocrResult && (
              <pre className="text-xs bg-muted rounded p-3 max-h-48 overflow-auto whitespace-pre-wrap">{ocrResult}</pre>
            )}
          </div>

          {/* Click */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-semibold mb-3 flex items-center gap-2"><Mouse className="w-3.5 h-3.5" />鼠标点击</h3>
            <div className="flex items-center gap-2 mb-3">
              <input className="w-20 px-2 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                placeholder="X" value={clickX} onChange={(e) => setClickX(e.target.value)} />
              <input className="w-20 px-2 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                placeholder="Y" value={clickY} onChange={(e) => setClickY(e.target.value)} />
              <Button onClick={handleClick} variant="primary">
                <Mouse className="w-3 h-3" />点击
              </Button>
            </div>
          </div>

          {/* Type */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-xs font-semibold mb-3 flex items-center gap-2"><Keyboard className="w-3.5 h-3.5" />键盘输入</h3>
            <div className="flex items-center gap-2 mb-3">
              <input className="flex-1 px-2 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                placeholder="输入文本..." value={typeText} onChange={(e) => setTypeText(e.target.value)} />
              <Button onClick={handleType} variant="primary">
                <Keyboard className="w-3 h-3" />输入
              </Button>
            </div>
          </div>

          {/* Windows */}
          <div className="col-span-full rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-semibold flex items-center gap-2"><Monitor className="w-3.5 h-3.5" />窗口列表</h3>
              <Button onClick={fetchWindows} variant="secondary" className="gap-1 px-2 py-1 text-[10px]">
                <RefreshCw className="w-3 h-3" />刷新
              </Button>
            </div>
            <div className="space-y-1">
              {windows.map((w) => (
                <div key={w.id} className="flex items-center gap-3 p-2 rounded bg-muted/30 text-xs">
                  <Monitor className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                  <span className="font-medium truncate flex-1">{w.title}</span>
                  {w.app && <span className="text-[10px] text-muted-foreground">{w.app}</span>}
                  {w.width && w.height && <span className="text-[10px] text-muted-foreground">{w.width}×{w.height}</span>}
                </div>
              ))}
              {windows.length === 0 && <p className="text-xs text-muted-foreground text-center py-4">暂无窗口</p>}
            </div>
          </div>
        </div>
      )}

      {/* Create Task Dialog */}
      {showCreateTask && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowCreateTask(false)}>
          <div className="bg-card rounded-xl border border-border shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">新建RPA任务</h2>
              <Button onClick={() => setShowCreateTask(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">任务名称 *</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  value={taskForm.name} onChange={(e) => setTaskForm(f => ({ ...f, name: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">操作步骤 (JSON数组)</label>
                <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 font-mono resize-none"
                  rows={6} value={taskForm.actions} onChange={(e) => setTaskForm(f => ({ ...f, actions: e.target.value }))} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">优先级</label>
                  <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                    type="number" value={taskForm.priority} onChange={(e) => setTaskForm(f => ({ ...f, priority: e.target.value }))} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">标签 (逗号分隔)</label>
                  <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                    value={taskForm.tags} onChange={(e) => setTaskForm(f => ({ ...f, tags: e.target.value }))} />
                </div>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 p-4 border-t border-border">
              <Button onClick={() => setShowCreateTask(false)} variant="secondary">取消</Button>
              <Button onClick={handleCreateTask} disabled={!taskForm.name} variant="primary">创建</Button>
            </div>
          </div>
        </div>
      )}

      {/* Create Template Dialog */}
      {showCreateTemplate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowCreateTemplate(false)}>
          <div className="bg-card rounded-xl border border-border shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">新建模板</h2>
              <Button onClick={() => setShowCreateTemplate(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">模板名称 *</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  value={tmplForm.name} onChange={(e) => setTmplForm(f => ({ ...f, name: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">描述</label>
                <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none resize-none"
                  rows={2} value={tmplForm.description} onChange={(e) => setTmplForm(f => ({ ...f, description: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">分类</label>
                <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                  value={tmplForm.category} onChange={(e) => setTmplForm(f => ({ ...f, category: e.target.value }))} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">操作步骤 (JSON数组)</label>
                <textarea className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none font-mono resize-none"
                  rows={4} value={tmplForm.actions} onChange={(e) => setTmplForm(f => ({ ...f, actions: e.target.value }))} />
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 p-4 border-t border-border">
              <Button onClick={() => setShowCreateTemplate(false)} variant="secondary">取消</Button>
              <Button onClick={handleCreateTemplate} disabled={!tmplForm.name} variant="primary">创建</Button>
            </div>
          </div>
        </div>
      )}

      {/* Task Detail Dialog */}
      {selectedTask && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setSelectedTask(null)}>
          <div className="bg-card rounded-xl border border-border shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-semibold">任务详情</h2>
              <Button onClick={() => setSelectedTask(null)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <div><span className="text-muted-foreground">ID:</span> <span className="font-mono">{selectedTask.task_id}</span></div>
                <div><span className="text-muted-foreground">名称:</span> {selectedTask.name}</div>
                <div><span className="text-muted-foreground">状态:</span> {STATUS_MAP[selectedTask.status]?.label || selectedTask.status}</div>
                <div><span className="text-muted-foreground">优先级:</span> {selectedTask.priority}</div>
              </div>
              <div>
                <span className="text-muted-foreground">操作步骤:</span>
                <pre className="mt-1 bg-muted rounded p-2 text-[10px] overflow-x-auto font-mono">{JSON.stringify(selectedTask.actions, null, 2)}</pre>
              </div>
              {selectedTask.error && <div className="text-red-500"><span className="text-muted-foreground">错误:</span> {selectedTask.error}</div>}
              {selectedTask.results && selectedTask.results.length > 0 && (
                <div>
                  <span className="text-muted-foreground">执行结果:</span>
                  <pre className="mt-1 bg-muted rounded p-2 text-[10px] overflow-x-auto font-mono">{JSON.stringify(selectedTask.results, null, 2)}</pre>
                </div>
              )}
            </div>
            <div className="flex items-center justify-end p-4 border-t border-border">
              <Button onClick={() => setSelectedTask(null)} variant="primary">关闭</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
