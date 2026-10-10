'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Plus, Trash2, RefreshCw, X, Database, Clock, Archive,
  Brain, Tag, Settings, BarChart3, Play, Edit3, Save, Zap, AlertTriangle,
  Layers
} from 'lucide-react';

interface Memory {
  memory_id: string;
  session_id: string;
  content: string;
  importance: number;
  tags: string[];
  retention: number;
  access_count: number;
  archived: boolean;
  created_at: string;
  last_accessed_at: string;
}

interface Session {
  session_id: string;
  user_id: string;
  title: string;
  status: string;
  memory_count: number;
  created_at: string;
  last_active_at: string;
}

interface DecayConfig {
  strategy: string;
  half_life_hours: number;
  archive_threshold: number;
  forget_threshold: number;
}

type Tab = 'memories' | 'sessions' | 'decay';

export default function HippoPage() {
  const [tab, setTab] = useState<Tab>('memories');
  const [memories, setMemories] = useState<Memory[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [includeArchived, setIncludeArchived] = useState(false);
  const [decayConfig, setDecayConfig] = useState<DecayConfig | null>(null);
  const [stats, setStats] = useState<any>(null);

  // Create memory dialog
  const [showCreateMem, setShowCreateMem] = useState(false);
  const [memForm, setMemForm] = useState({ session_id: '', content: '', importance: '0.5', tags: '' });
  const [saving, setSaving] = useState(false);

  // Create session dialog
  const [showCreateSess, setShowCreateSess] = useState(false);
  const [sessForm, setSessForm] = useState({ user_id: '', title: '' });

  // Detail
  const [selectedMem, setSelectedMem] = useState<Memory | null>(null);
  const [showMemDetail, setShowMemDetail] = useState(false);

  // Simulate dialog
  const [showSimulate, setShowSimulate] = useState(false);
  const [simForm, setSimForm] = useState({ age_hours: '24', importance: '0.5', access_count: '0' });
  const [simResult, setSimResult] = useState<any>(null);

  const fetchMemories = useCallback(async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (search) params.set('tag', search);
      if (includeArchived) params.set('include_archived', 'true');
      params.set('limit', '100');
      const data = await apiFetch(`/api/hippo/memories?${params}`);
      setMemories(Array.isArray(data.memories) ? data.memories : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [search, includeArchived]);

  const fetchSessions = useCallback(async () => {
    try {
      const data = await apiFetch('/api/hippo/sessions?limit=100');
      setSessions(Array.isArray(data.sessions) ? data.sessions : []);
    } catch (e: any) {
      setError(e.message);
    }
  }, []);

  const fetchDecayConfig = useCallback(async () => {
    try {
      const data = await apiFetch('/api/hippo/decay/config');
      setDecayConfig(data);
    } catch { /* silent */ }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiFetch('/api/hippo/stats');
      setStats(data);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    fetchMemories(); fetchSessions(); fetchDecayConfig(); fetchStats();
  }, [fetchMemories, fetchSessions, fetchDecayConfig, fetchStats]);

  const handleCreateMemory = async () => {
    try {
      setSaving(true);
      await apiFetch('/api/hippo/memories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: memForm.session_id || 'default',
          content: memForm.content,
          importance: parseFloat(memForm.importance) || 0.5,
          tags: memForm.tags.split(',').map(t => t.trim()).filter(Boolean),
        }),
      });
      setShowCreateMem(false);
      setMemForm({ session_id: '', content: '', importance: '0.5', tags: '' });
      await fetchMemories();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteMemory = async (mid: string) => {
    if (!confirm('确定删除此记忆？')) return;
    try {
      await apiFetch(`/api/hippo/memories/${mid}`, { method: 'DELETE' });
      await fetchMemories();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleCreateSession = async () => {
    try {
      setSaving(true);
      await apiFetch('/api/hippo/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sessForm),
      });
      setShowCreateSess(false);
      setSessForm({ user_id: '', title: '' });
      await fetchSessions();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleArchiveSession = async (sid: string) => {
    try {
      await apiFetch(`/api/hippo/sessions/${sid}/archive`, { method: 'POST' });
      await fetchSessions();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDeleteSession = async (sid: string) => {
    if (!confirm('确定删除此会话及其所有记忆？')) return;
    try {
      await apiFetch(`/api/hippo/sessions/${sid}`, { method: 'DELETE' });
      await fetchSessions();
      await fetchMemories();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleRunDecay = async () => {
    try {
      const data = await apiFetch('/api/hippo/decay/run', { method: 'POST' });
      alert(`衰减完成: ${JSON.stringify(data)}`);
      await fetchMemories();
      await fetchStats();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleSimulate = async () => {
    try {
      const params = new URLSearchParams({
        age_hours: simForm.age_hours,
        importance: simForm.importance,
        access_count: simForm.access_count,
      });
      const data = await apiFetch(`/api/hippo/decay/simulate?${params}`);
      setSimResult(data);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleLifecycleCheck = async () => {
    try {
      const data = await apiFetch('/api/hippo/sessions/lifecycle-check', { method: 'POST' });
      alert(`生命周期检查: ${JSON.stringify(data)}`);
      await fetchSessions();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const filteredMemories = memories.filter(m => {
    if (!search) return true;
    const q = search.toLowerCase();
    return m.content.toLowerCase().includes(q) || m.tags.some(t => t.toLowerCase().includes(q)) ||
      m.session_id.toLowerCase().includes(q);
  });

  const retentionColor = (r: number) => {
    if (r >= 0.7) return 'text-green-600';
    if (r >= 0.4) return 'text-yellow-500';
    return 'text-red-500';
  };

  const tabs: { key: Tab; label: string; icon: any }[] = [
    { key: 'memories', label: '记忆', icon: Brain },
    { key: 'sessions', label: '会话', icon: Layers },
    { key: 'decay', label: '衰减', icon: Clock },
  ];

  return (
    <div className="space-y-4">
      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 text-red-500 text-xs">
          {error}
          <button onClick={() => setError('')} className="ml-auto"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{stats?.memory?.total ?? memories.length}</div>
          <div className="text-xs text-muted-foreground">记忆总数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-600">{stats?.memory?.active ?? memories.filter(m => !m.archived).length}</div>
          <div className="text-xs text-muted-foreground">活跃记忆</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-orange-500">{stats?.memory?.archived ?? memories.filter(m => m.archived).length}</div>
          <div className="text-xs text-muted-foreground">已归档</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-500">{stats?.sessions?.total ?? sessions.length}</div>
          <div className="text-xs text-muted-foreground">会话数</div>
        </div>
      </div>

      {/* Tab Bar */}
      <div className="flex items-center gap-1 p-1 bg-muted rounded-lg w-fit">
        {tabs.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs lg:text-sm rounded-lg transition-colors ${tab === t.key ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:text-foreground'}`}
          >
            <t.icon className="w-3.5 h-3.5" />{t.label}
          </button>
        ))}
      </div>

      {/* Memories Tab */}
      {tab === 'memories' && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <input className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" placeholder="搜索内容、标签、会话ID..." value={search} onChange={e => setSearch(e.target.value)} />
            </div>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
              <input type="checkbox" checked={includeArchived} onChange={e => setIncludeArchived(e.target.checked)} className="rounded" />
              包含已归档
            </label>
            <button onClick={fetchMemories} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </button>
            <button onClick={() => setShowCreateMem(true)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-primary/10 text-primary border border-border rounded-lg hover:bg-primary/20">
              <Plus className="w-3.5 h-3.5" />新建记忆
            </button>
          </div>

          <div className="rounded-lg border border-border overflow-hidden">
            <table className="w-full text-xs lg:text-sm min-w-[500px]">
              <thead>
                <tr className="border-b border-border bg-muted/30">
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium text-muted-foreground">内容</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium w-24 text-muted-foreground">会话</th>
                  <th className="text-center px-2 lg:px-4 py-2.5 font-medium w-16 text-muted-foreground">重要度</th>
                  <th className="text-center px-2 lg:px-4 py-2.5 font-medium w-16 text-muted-foreground">留存率</th>
                  <th className="text-center px-2 lg:px-4 py-2.5 font-medium w-16 text-muted-foreground">访问</th>
                  <th className="text-left px-2 lg:px-4 py-2.5 font-medium w-32 text-muted-foreground">标签</th>
                  <th className="text-center px-2 lg:px-4 py-2.5 font-medium w-20 text-muted-foreground">状态</th>
                  <th className="text-right px-2 lg:px-4 py-2.5 font-medium w-20 text-muted-foreground">操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredMemories.map(m => (
                  <tr key={m.memory_id} className="border-b border-border last:border-0 hover:bg-muted/30 cursor-pointer" onClick={() => { setSelectedMem(m); setShowMemDetail(true); }}>
                    <td className="px-2 lg:px-4 py-2.5 truncate max-w-[300px]">{m.content}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-xs font-mono text-muted-foreground truncate">{m.session_id}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-center">{m.importance.toFixed(2)}</td>
                    <td className={`px-2 lg:px-4 py-2.5 text-center font-medium ${retentionColor(m.retention)}`}>{(m.retention * 100).toFixed(0)}%</td>
                    <td className="px-2 lg:px-4 py-2.5 text-center text-muted-foreground">{m.access_count}</td>
                    <td className="px-2 lg:px-4 py-2.5">
                      <div className="flex flex-wrap gap-1">
                        {m.tags.slice(0, 3).map(t => <span key={t} className="text-[10px] px-1 py-0.5 rounded bg-muted">{t}</span>)}
                      </div>
                    </td>
                    <td className="px-2 lg:px-4 py-2.5 text-center">
                      {m.archived ? (
                        <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-amber-500/10 text-amber-500">归档</span>
                      ) : (
                        <span className="rounded-full px-2 py-0.5 text-[10px] font-medium bg-emerald-500/10 text-emerald-500">活跃</span>
                      )}
                    </td>
                    <td className="px-2 lg:px-4 py-2.5 text-right" onClick={e => e.stopPropagation()}>
                      <button onClick={() => handleDeleteMemory(m.memory_id)} className="p-1 text-muted-foreground hover:text-red-500 rounded-md">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
                {filteredMemories.length === 0 && (
                  <tr><td colSpan={8} className="px-2 lg:px-4 py-8 text-center text-muted-foreground">暂无记忆数据</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Sessions Tab */}
      {tab === 'sessions' && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={fetchSessions} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm border border-border rounded-lg hover:bg-muted transition-colors">
              <RefreshCw className="w-3.5 h-3.5" />刷新
            </button>
            <button onClick={() => setShowCreateSess(true)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-primary/10 text-primary border border-border rounded-lg hover:bg-primary/20">
              <Plus className="w-3.5 h-3.5" />新建会话
            </button>
            <button onClick={handleLifecycleCheck} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-amber-500/10 text-amber-500 border border-border rounded-lg hover:bg-amber-500/20">
              <Clock className="w-3.5 h-3.5" />生命周期检查
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {sessions.map(s => (
              <div key={s.session_id} className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="text-sm font-medium">{s.title || s.session_id}</h3>
                    <p className="text-xs text-muted-foreground font-mono mt-0.5">{s.session_id}</p>
                  </div>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${s.status === 'active' ? 'bg-emerald-500/10 text-emerald-500' : s.status === 'archived' ? 'bg-amber-500/10 text-amber-500' : 'bg-muted text-muted-foreground'}`}>
                    {s.status}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 mt-3 text-xs text-muted-foreground">
                  <div>记忆数: <span className="font-medium text-foreground">{s.memory_count}</span></div>
                  <div>用户: <span className="font-mono">{s.user_id || '—'}</span></div>
                  <div>创建: {s.created_at ? new Date(s.created_at).toLocaleDateString() : '—'}</div>
                  <div>最后活跃: {s.last_active_at ? new Date(s.last_active_at).toLocaleDateString() : '—'}</div>
                </div>
                <div className="flex items-center gap-1.5 mt-3 pt-3 border-t border-border">
                  {s.status === 'active' && (
                    <button onClick={() => handleArchiveSession(s.session_id)} className="text-[10px] px-2 py-1 bg-amber-500/10 text-amber-500 rounded-lg hover:bg-amber-500/20">
                      <Archive className="w-3 h-3 inline mr-0.5" />归档
                    </button>
                  )}
                  <button onClick={() => handleDeleteSession(s.session_id)} className="text-[10px] px-2 py-1 bg-red-500/10 text-red-500 rounded-lg hover:bg-red-500/20">
                    <Trash2 className="w-3 h-3 inline mr-0.5" />删除
                  </button>
                </div>
              </div>
            ))}
            {sessions.length === 0 && (
              <div className="col-span-full text-center text-sm text-muted-foreground py-8">暂无会话数据</div>
            )}
          </div>
        </>
      )}

      {/* Decay Tab */}
      {tab === 'decay' && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Config */}
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-center gap-2 mb-3">
                <Settings className="w-4 h-4 text-muted-foreground" />
                <h3 className="text-sm font-medium">衰减配置</h3>
              </div>
              {decayConfig && (
                <div className="space-y-2 text-xs">
                  <div className="flex justify-between"><span className="text-muted-foreground">策略:</span><span className="font-medium">{decayConfig.strategy}</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">半衰期:</span><span className="font-medium">{decayConfig.half_life_hours}h</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">归档阈值:</span><span className="font-medium">{decayConfig.archive_threshold}</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">遗忘阈值:</span><span className="font-medium">{decayConfig.forget_threshold}</span></div>
                </div>
              )}
              <div className="flex gap-2 mt-4">
                <button onClick={handleRunDecay} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-amber-500/10 text-amber-500 border border-border rounded-lg hover:bg-amber-500/20">
                  <Play className="w-3.5 h-3.5" />运行衰减
                </button>
                <button onClick={() => setShowSimulate(true)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-violet-500/10 text-violet-500 border border-border rounded-lg hover:bg-violet-500/20">
                  <BarChart3 className="w-3.5 h-3.5" />模拟衰减
                </button>
              </div>
            </div>

            {/* By Tag Stats */}
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-center gap-2 mb-3">
                <Tag className="w-4 h-4 text-muted-foreground" />
                <h3 className="text-sm font-medium">标签分布</h3>
              </div>
              {stats?.by_tag && Object.keys(stats.by_tag).length > 0 ? (
                <div className="space-y-1.5">
                  {Object.entries(stats.by_tag).sort((a, b) => (b[1] as number) - (a[1] as number)).slice(0, 10).map(([tag, cnt]) => (
                    <div key={tag} className="flex items-center gap-2 text-xs">
                      <span className="w-20 truncate text-muted-foreground">{tag}</span>
                      <div className="flex-1 bg-muted rounded-full h-1.5">
                        <div className="bg-primary h-1.5 rounded-full" style={{ width: `${Math.min(((cnt as number) / Math.max(...Object.values(stats.by_tag).map(Number))) * 100, 100)}%` }} />
                      </div>
                      <span className="w-6 text-right font-medium">{String(cnt)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-xs text-muted-foreground py-4 text-center">暂无标签数据</div>
              )}
            </div>
          </div>

          {/* Simulate Result */}
          {simResult && (
            <div className="rounded-lg border border-border bg-card p-4">
              <div className="flex items-center gap-2 mb-3">
                <BarChart3 className="w-4 h-4 text-purple-500" />
                <h3 className="text-sm font-medium">模拟结果</h3>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <div><span className="text-muted-foreground">留存率: </span><span className={`font-bold ${retentionColor(simResult.retention)}`}>{(simResult.retention * 100).toFixed(1)}%</span></div>
                <div><span className="text-muted-foreground">应归档: </span><span className="font-medium">{simResult.should_archive ? '是' : '否'}</span></div>
                <div><span className="text-muted-foreground">应遗忘: </span><span className="font-medium">{simResult.should_forget ? '是' : '否'}</span></div>
                <div><span className="text-muted-foreground">策略: </span><span className="font-medium">{simResult.strategy}</span></div>
              </div>
              {/* Visual retention bar */}
              <div className="mt-3">
                <div className="text-xs text-muted-foreground mb-1">记忆留存率可视化</div>
                <div className="w-full bg-muted rounded-full h-4 relative overflow-hidden">
                  <div
                    className={`h-4 rounded-full transition-all ${simResult.retention >= 0.7 ? 'bg-green-500' : simResult.retention >= 0.4 ? 'bg-yellow-500' : 'bg-red-500'}`}
                    style={{ width: `${simResult.retention * 100}%` }}
                  />
                  <span className="absolute inset-0 flex items-center justify-center text-[10px] font-medium">{(simResult.retention * 100).toFixed(1)}%</span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Memory Detail Dialog */}
      {showMemDetail && selectedMem && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowMemDetail(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-lg w-full mx-4 max-h-[80vh] overflow-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">记忆详情</h2>
              <button onClick={() => setShowMemDetail(false)}><X className="w-4 h-4 text-muted-foreground" /></button>
            </div>
            <div className="space-y-3 text-sm">
              <div><span className="text-muted-foreground">内容: </span>{selectedMem.content}</div>
              <div className="grid grid-cols-2 gap-2">
                <div><span className="text-muted-foreground">ID: </span><code className="text-xs bg-muted px-1.5 py-0.5 rounded">{selectedMem.memory_id}</code></div>
                <div><span className="text-muted-foreground">会话: </span><code className="text-xs bg-muted px-1.5 py-0.5 rounded">{selectedMem.session_id}</code></div>
                <div><span className="text-muted-foreground">重要度: </span>{selectedMem.importance.toFixed(2)}</div>
                <div><span className="text-muted-foreground">留存率: </span><span className={retentionColor(selectedMem.retention)}>{(selectedMem.retention * 100).toFixed(1)}%</span></div>
                <div><span className="text-muted-foreground">访问次数: </span>{selectedMem.access_count}</div>
                <div><span className="text-muted-foreground">状态: </span>{selectedMem.archived ? '已归档' : '活跃'}</div>
              </div>
              {selectedMem.tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {selectedMem.tags.map(t => <span key={t} className="text-xs px-2 py-0.5 rounded bg-primary/5 text-primary/70">{t}</span>)}
                </div>
              )}
              <div className="text-xs text-muted-foreground">
                <div>创建: {selectedMem.created_at}</div>
                <div>最后访问: {selectedMem.last_accessed_at}</div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Create Memory Dialog */}
      {showCreateMem && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowCreateMem(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-md w-full mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">新建记忆</h2>
              <button onClick={() => setShowCreateMem(false)}><X className="w-4 h-4 text-muted-foreground" /></button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">会话ID</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={memForm.session_id} onChange={e => setMemForm({ ...memForm, session_id: e.target.value })} placeholder="default" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">内容 *</label>
                <textarea className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 h-24 resize-none" value={memForm.content} onChange={e => setMemForm({ ...memForm, content: e.target.value })} placeholder="记忆内容" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">重要度 (0-1)</label>
                  <input type="number" step="0.1" min="0" max="1" className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={memForm.importance} onChange={e => setMemForm({ ...memForm, importance: e.target.value })} />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground mb-1 block">标签（逗号分隔）</label>
                  <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={memForm.tags} onChange={e => setMemForm({ ...memForm, tags: e.target.value })} placeholder="重要, 项目" />
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <button onClick={() => setShowCreateMem(false)} className="px-3 py-1.5 text-xs lg:text-sm bg-muted border border-border rounded-lg">取消</button>
              <button onClick={handleCreateMemory} disabled={saving || !memForm.content.trim()} className="flex items-center gap-1.5 px-2 lg:px-2 lg:px-4 py-2 text-xs lg:text-sm bg-primary text-primary-foreground rounded-lg disabled:opacity-50 font-medium transition-colors">
                {saving ? <Zap className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}保存
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create Session Dialog */}
      {showCreateSess && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowCreateSess(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-md w-full mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">新建会话</h2>
              <button onClick={() => setShowCreateSess(false)}><X className="w-4 h-4 text-muted-foreground" /></button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">用户ID</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={sessForm.user_id} onChange={e => setSessForm({ ...sessForm, user_id: e.target.value })} placeholder="可选" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">标题</label>
                <input className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50" value={sessForm.title} onChange={e => setSessForm({ ...sessForm, title: e.target.value })} placeholder="会话标题" />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <button onClick={() => setShowCreateSess(false)} className="px-3 py-1.5 text-xs lg:text-sm bg-muted border border-border rounded-lg">取消</button>
              <button onClick={handleCreateSession} disabled={saving} className="flex items-center gap-1.5 px-2 lg:px-2 lg:px-4 py-2 text-xs lg:text-sm bg-primary text-primary-foreground rounded-lg disabled:opacity-50 font-medium transition-colors">
                {saving ? <Zap className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}保存
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Simulate Dialog */}
      {showSimulate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowSimulate(false)}>
          <div className="bg-card rounded-lg border border-border p-6 max-w-md w-full mx-4" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-medium">模拟衰减</h2>
              <button onClick={() => setShowSimulate(false)}><X className="w-4 h-4 text-muted-foreground" /></button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">记忆年龄（小时）</label>
                <input type="number" className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={simForm.age_hours} onChange={e => setSimForm({ ...simForm, age_hours: e.target.value })} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">重要度</label>
                <input type="number" step="0.1" min="0" max="1" className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={simForm.importance} onChange={e => setSimForm({ ...simForm, importance: e.target.value })} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">访问次数</label>
                <input type="number" className="w-full px-3 py-1.5 text-sm bg-muted border border-border rounded-md" value={simForm.access_count} onChange={e => setSimForm({ ...simForm, access_count: e.target.value })} />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-6">
              <button onClick={() => setShowSimulate(false)} className="px-3 py-1.5 text-xs lg:text-sm bg-muted border border-border rounded-lg">取消</button>
              <button onClick={handleSimulate} className="flex items-center gap-1.5 px-3 py-1.5 text-xs lg:text-sm bg-violet-500/10 text-violet-500 border border-border rounded-lg hover:bg-violet-500/20">
                <BarChart3 className="w-3.5 h-3.5" />模拟
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
