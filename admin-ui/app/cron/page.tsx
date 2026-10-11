'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Clock, Plus, Trash2, Play, Pause, RefreshCw, X,
  History, Loader2, AlertCircle, CheckCircle, Timer
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

interface CronJob {
  id: string;
  name: string;
  status: string;
  schedule: string;
  next_run: string;
  last_run: string;
  deliver: string;
  prompt: string;
}

interface RunRecord {
  raw: string;
  status: string;
}

export default function CronPage() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');

  // Create dialog
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({
    schedule: '', prompt: '', name: '', deliver: '', skill: '', workdir: '', model: '', provider: '',
  });
  const [creating, setCreating] = useState(false);

  // History dialog
  const [showHistory, setShowHistory] = useState(false);
  const [historyJob, setHistoryJob] = useState<CronJob | null>(null);
  const [historyRuns, setHistoryRuns] = useState<RunRecord[]>([]);
  const [historyRaw, setHistoryRaw] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);

  // Detail dialog
  const [selectedJob, setSelectedJob] = useState<CronJob | null>(null);
  const [showDetail, setShowDetail] = useState(false);

  const fetchJobs = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/cron/list');
      setJobs(Array.isArray(data.jobs) ? data.jobs : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchJobs(); }, [fetchJobs]);

  const handleCreate = async () => {
    if (!createForm.schedule.trim()) { setError('调度表达式必填'); return; }
    try {
      setCreating(true);
      const body: any = { schedule: createForm.schedule };
      if (createForm.prompt) body.prompt = createForm.prompt;
      if (createForm.name) body.name = createForm.name;
      if (createForm.deliver) body.deliver = createForm.deliver;
      if (createForm.skill) body.skill = createForm.skill.split(',').map((s) => s.trim()).filter(Boolean);
      if (createForm.workdir) body.workdir = createForm.workdir;
      if (createForm.model) body.model = createForm.model;
      if (createForm.provider) body.provider = createForm.provider;
      await apiFetch('/api/cron/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setShowCreate(false);
      setCreateForm({ schedule: '', prompt: '', name: '', deliver: '', skill: '', workdir: '', model: '', provider: '' });
      fetchJobs();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  const handlePause = async (jobId: string) => {
    try {
      await apiFetch(`/api/cron/${jobId}/pause`, { method: 'POST' });
      fetchJobs();
    } catch (e: any) { setError(e.message); }
  };

  const handleResume = async (jobId: string) => {
    try {
      await apiFetch(`/api/cron/${jobId}/resume`, { method: 'POST' });
      fetchJobs();
    } catch (e: any) { setError(e.message); }
  };

  const handleRun = async (jobId: string) => {
    try {
      await apiFetch(`/api/cron/${jobId}/run`, { method: 'POST' });
    } catch (e: any) { setError(e.message); }
  };

  const handleDelete = async (jobId: string) => {
    if (!confirm('确定要删除该定时任务？')) return;
    try {
      await apiFetch(`/api/cron/${jobId}`, { method: 'DELETE' });
      fetchJobs();
    } catch (e: any) { setError(e.message); }
  };

  const handleShowHistory = async (job: CronJob) => {
    setHistoryJob(job);
    setShowHistory(true);
    setHistoryLoading(true);
    try {
      const data = await apiFetch(`/api/cron/${job.id}/history`);
      setHistoryRuns(Array.isArray(data.runs) ? data.runs : []);
      setHistoryRaw(data.raw || '');
    } catch (e: any) {
      setHistoryRuns([]);
      setHistoryRaw('');
    } finally {
      setHistoryLoading(false);
    }
  };

  const filtered = jobs.filter((j) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (j.name || '').toLowerCase().includes(q) || (j.id || '').toLowerCase().includes(q) || (j.prompt || '').toLowerCase().includes(q);
  });

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{jobs.length}</div>
          <div className="text-xs text-muted-foreground">总任务数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-emerald-600">{jobs.filter((j) => j.status === 'active').length}</div>
          <div className="text-xs text-muted-foreground">运行中</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-muted-foreground">{jobs.filter((j) => j.status === 'paused').length}</div>
          <div className="text-xs text-muted-foreground">已暂停</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索任务名称、ID、Prompt..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button onClick={() => fetchJobs()} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => setShowCreate(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />创建任务
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

      {/* Jobs list */}
      <div className="space-y-2">
        {filtered.map((job) => (
          <div key={job.id} className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors">
            <div className="flex items-start justify-between">
              <div className="min-w-0 flex-1 cursor-pointer" onClick={() => { setSelectedJob(job); setShowDetail(true); }}>
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-medium truncate">{job.name || job.id}</h3>
                  <Badge variant="bare" className={`${job.status === 'active' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                    {job.status === 'active' ? '运行中' : '已暂停'}
                  </Badge>
                </div>
                <div className="flex items-center gap-3 mt-1 text-[10px] text-muted-foreground">
                  <span className="font-mono">ID: {job.id}</span>
                  <span className="font-mono">{job.schedule}</span>
                </div>
                {job.prompt && <p className="text-xs text-muted-foreground mt-1 line-clamp-1">{job.prompt}</p>}
                <div className="flex items-center gap-4 mt-1 text-[10px] text-muted-foreground">
                  {job.next_run && <span>下次: {job.next_run}</span>}
                  {job.last_run && <span>上次: {job.last_run}</span>}
                  {job.deliver && <span>投递: {job.deliver}</span>}
                </div>
              </div>
              <div className="flex items-center gap-1 shrink-0 ml-2" onClick={(e) => e.stopPropagation()}>
                <Button onClick={() => handleShowHistory(job)} variant="icon-md" className="hover:bg-muted" title="历史">
                  <History className="w-3.5 h-3.5 text-muted-foreground" />
                </Button>
                <Button onClick={() => handleRun(job.id)} variant="icon-md" className="hover:bg-blue-500/10" title="立即执行">
                  <Play className="w-3.5 h-3.5 text-blue-500" />
                </Button>
                {job.status === 'active' ? (
                  <Button onClick={() => handlePause(job.id)} variant="icon-md" className="hover:bg-amber-500/10" title="暂停">
                    <Pause className="w-3.5 h-3.5 text-amber-500" />
                  </Button>
                ) : (
                  <Button onClick={() => handleResume(job.id)} variant="icon-md" className="hover:bg-emerald-500/10" title="恢复">
                    <Play className="w-3.5 h-3.5 text-emerald-500" />
                  </Button>
                )}
                <Button onClick={() => handleDelete(job.id)} variant="icon-md" className="hover:bg-red-500/10" title="删除">
                  <Trash2 className="w-3.5 h-3.5 text-red-500" />
                </Button>
              </div>
            </div>
          </div>
        ))}
        {filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <Timer className="w-10 h-10 mb-2 opacity-30" />
            <p className="text-xs">{search ? '没有匹配的任务' : '暂无定时任务'}</p>
          </div>
        )}
      </div>

      {/* Create Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">创建定时任务</h2>
              <Button onClick={() => setShowCreate(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">调度表达式 *</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono"
                  value={createForm.schedule}
                  onChange={(e) => setCreateForm((f) => ({ ...f, schedule: e.target.value }))}
                  placeholder="every 30m / daily at 9:00 / cron expr"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">任务名称</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={createForm.name}
                  onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="my-task"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Prompt</label>
                <textarea
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md h-20 resize-none"
                  value={createForm.prompt}
                  onChange={(e) => setCreateForm((f) => ({ ...f, prompt: e.target.value }))}
                  placeholder="执行指令..."
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">投递目标</label>
                  <input
                    className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={createForm.deliver}
                    onChange={(e) => setCreateForm((f) => ({ ...f, deliver: e.target.value }))}
                    placeholder="terminal / wechat"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">工作目录</label>
                  <input
                    className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={createForm.workdir}
                    onChange={(e) => setCreateForm((f) => ({ ...f, workdir: e.target.value }))}
                    placeholder="/path/to/workdir"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground">模型</label>
                  <input
                    className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={createForm.model}
                    onChange={(e) => setCreateForm((f) => ({ ...f, model: e.target.value }))}
                    placeholder="可选"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Provider</label>
                  <input
                    className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                    value={createForm.provider}
                    onChange={(e) => setCreateForm((f) => ({ ...f, provider: e.target.value }))}
                    placeholder="可选"
                  />
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">技能（逗号分隔）</label>
                <input
                  className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md"
                  value={createForm.skill}
                  onChange={(e) => setCreateForm((f) => ({ ...f, skill: e.target.value }))}
                  placeholder="skill1, skill2"
                />
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreate(false)} variant="secondary">取消</Button>
                <Button onClick={handleCreate}
                  disabled={!createForm.schedule || creating} variant="primary">
                  {creating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                  {creating ? '创建中...' : '创建'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Detail Dialog */}
      {showDetail && selectedJob && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDetail(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-sm font-medium">{selectedJob.name || selectedJob.id}</h2>
              <Button onClick={() => setShowDetail(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="p-4 space-y-3 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <div><span className="text-muted-foreground">ID:</span> <span className="font-mono">{selectedJob.id}</span></div>
                <div><span className="text-muted-foreground">状态:</span> <span className={selectedJob.status === 'active' ? 'text-emerald-600' : 'text-muted-foreground'}>{selectedJob.status === 'active' ? '运行中' : '已暂停'}</span></div>
                <div><span className="text-muted-foreground">调度:</span> <span className="font-mono">{selectedJob.schedule}</span></div>
                <div><span className="text-muted-foreground">投递:</span> {selectedJob.deliver || '-'}</div>
                <div><span className="text-muted-foreground">下次执行:</span> {selectedJob.next_run || '-'}</div>
                <div><span className="text-muted-foreground">上次执行:</span> {selectedJob.last_run || '-'}</div>
              </div>
              {selectedJob.prompt && (
                <div>
                  <div className="text-muted-foreground">Prompt</div>
                  <pre className="mt-1 p-2 bg-muted rounded text-[11px] whitespace-pre-wrap">{selectedJob.prompt}</pre>
                </div>
              )}
              <div className="flex items-center gap-2 pt-2 border-t border-border">
                <Button onClick={() => handleRun(selectedJob.id)} variant="chip-sm" className="flex items-center gap-1 bg-blue-500/10 text-blue-500 hover:bg-blue-500/20">
                  <Play className="w-3 h-3" />立即执行
                </Button>
                {selectedJob.status === 'active' ? (
                  <Button onClick={() => { handlePause(selectedJob.id); setShowDetail(false); }} variant="chip-sm" className="flex items-center gap-1 bg-amber-500/10 text-amber-500 hover:bg-amber-500/20">
                    <Pause className="w-3 h-3" />暂停
                  </Button>
                ) : (
                  <Button onClick={() => { handleResume(selectedJob.id); setShowDetail(false); }} variant="chip-sm" className="flex items-center gap-1 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20">
                    <Play className="w-3 h-3" />恢复
                  </Button>
                )}
                <Button onClick={() => { handleDelete(selectedJob.id); setShowDetail(false); }} variant="chip-sm" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20 ml-auto">
                  <Trash2 className="w-3 h-3" />删除
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* History Dialog */}
      {showHistory && historyJob && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowHistory(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border shrink-0">
              <h2 className="text-sm font-medium">执行历史 — {historyJob.name || historyJob.id}</h2>
              <Button onClick={() => setShowHistory(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="flex-1 overflow-auto p-4 min-h-0">
              {historyLoading ? (
                <div className="text-xs text-muted-foreground text-center py-8">加载中...</div>
              ) : historyRuns.length > 0 ? (
                <div className="space-y-2">
                  {historyRuns.map((run, i) => (
                    <div key={i} className="p-2 rounded border border-border bg-muted/30">
                      <div className="flex items-center gap-2">
                        <Badge variant="bare" className={`${run.status === 'success' ? 'bg-emerald-500/10 text-emerald-500' : run.status === 'failed' ? 'bg-red-500/10 text-red-500' : 'bg-muted text-muted-foreground'}`}>
                          {run.status}
                        </Badge>
                      </div>
                      <pre className="text-[10px] mt-1 whitespace-pre-wrap text-muted-foreground">{run.raw}</pre>
                    </div>
                  ))}
                </div>
              ) : historyRaw ? (
                <pre className="text-[10px] whitespace-pre-wrap text-muted-foreground">{historyRaw}</pre>
              ) : (
                <div className="text-xs text-muted-foreground text-center py-8">暂无执行记录</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
