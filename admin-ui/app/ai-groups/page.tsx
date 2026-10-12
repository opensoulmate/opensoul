'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, Plus, Trash2, RefreshCw, X, Users, Bot, ChevronRight,
  MessageSquare, CheckCircle, Clock, AlertCircle, Play, Eye,
  UserPlus, Settings, ListChecks, ArrowLeft, Send, Star,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge, tone } from '@/components/ui/badge';

// ── Types ─────────────────────────────────────────────────────

interface GroupAgent {
  id: string;
  group_id: string;
  agent_id: string;
  name: string;
  role: string;
  model: string;
  status: string;
  temperature?: number;
}

interface GroupTask {
  id: string;
  group_id: string;
  parent_task_id: string | null;
  goal: string;
  constraints: string | string[];
  completion_criteria: string | string[];
  status: string;
  assigned_agent_id: string | null;
  result: string;
  quality_score: number;
  iteration: number;
  max_iterations: number;
  created_at: string;
  updated_at: string;
  subtasks?: GroupTask[];
}

interface AIGroup {
  id: string;
  name: string;
  description: string;
  status: string;
  created_at: string;
  agents: GroupAgent[];
  tasks?: GroupTask[];
  task_count?: number;
}

interface DiscussionMessage {
  id: string;
  agent_id: string;
  agent_name: string;
  intent: string;
  content: string;
  round_num: number;
  created_at: string;
}

const ROLES = [
  { value: 'advisor', label: '顾问', color: 'bg-blue-500/10 text-blue-600' },
  { value: 'executor', label: '执行者', color: 'bg-emerald-500/10 text-emerald-500' },
  { value: 'verifier', label: '验证者', color: 'bg-orange-500/10 text-orange-600' },
  { value: 'human', label: '人工', color: 'bg-purple-500/10 text-purple-600' },
];

const TASK_STATUSES: Record<string, { label: string; color: string; icon: typeof Clock }> = {
  planning: { label: '规划中', color: tone('info'), icon: Clock },
  pending: { label: '待处理', color: tone('muted'), icon: Clock },
  discussing: { label: '讨论中', color: tone('warning'), icon: MessageSquare },
  assigned: { label: '已分配', color: tone('accent'), icon: CheckCircle },
  executing: { label: '执行中', color: tone('primary'), icon: Play },
  reviewing: { label: '评审中', color: tone('warning'), icon: Eye },
  awaiting_confirm: { label: '待确认', color: tone('warning'), icon: AlertCircle },
  scored: { label: '已评分', color: tone('info'), icon: Star },
  completed: { label: '已完成', color: tone('success'), icon: CheckCircle },
  failed: { label: '失败', color: tone('destructive'), icon: AlertCircle },
};

function getRoleLabel(role: string) {
  return ROLES.find((r) => r.value === role)?.label || role;
}
function getRoleColor(role: string) {
  return ROLES.find((r) => r.value === role)?.color || 'bg-muted text-muted-foreground';
}
function getStatusInfo(status: string) {
  return TASK_STATUSES[status] || { label: status, color: tone('muted'), icon: Clock };
}

// ── Main Page ─────────────────────────────────────────────────

export default function AIGroupsPage() {
  const [groups, setGroups] = useState<AIGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [selectedGroup, setSelectedGroup] = useState<AIGroup | null>(null);
  const [detailTab, setDetailTab] = useState<'agents' | 'tasks'>('agents');
  const [selectedTask, setSelectedTask] = useState<GroupTask | null>(null);
  const [discussionMessages, setDiscussionMessages] = useState<DiscussionMessage[]>([]);
  const [showDiscussion, setShowDiscussion] = useState(false);
  const [confirmNote, setConfirmNote] = useState('');

  // Create form state
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newAgents, setNewAgents] = useState<{ agent_id: string; name: string; role: string; model: string }[]>([
    { agent_id: 'advisor-1', name: '顾问', role: 'advisor', model: '' },
    { agent_id: 'executor-1', name: '执行者', role: 'executor', model: '' },
    { agent_id: 'verifier-1', name: '验证者', role: 'verifier', model: '' },
  ]);

  // Add agent form
  const [showAddAgent, setShowAddAgent] = useState(false);
  const [addAgentForm, setAddAgentForm] = useState({ agent_id: '', name: '', role: 'executor', model: '' });

  // Submit task form
  const [showSubmitTask, setShowSubmitTask] = useState(false);
  const [taskForm, setTaskForm] = useState({ goal: '', constraints: '', criteria: '' });

  const fetchGroups = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/ai-groups');
      setGroups(Array.isArray(data) ? data : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchGroups(); }, [fetchGroups]);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      await apiFetch('/api/ai-groups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName, description: newDesc, agents: newAgents }),
      });
      setShowCreate(false);
      setNewName('');
      setNewDesc('');
      await fetchGroups();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleDelete = async (groupId: string) => {
    if (!confirm('确定删除该AI群组？所有Agent、任务和讨论记录将被清除。')) return;
    try {
      await apiFetch(`/api/ai-groups/${groupId}`, { method: 'DELETE' });
      setSelectedGroup(null);
      await fetchGroups();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const fetchGroupDetail = async (groupId: string) => {
    try {
      const data = await apiFetch(`/api/ai-groups/${groupId}`);
      setSelectedGroup(data);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleAddAgent = async () => {
    if (!selectedGroup || !addAgentForm.agent_id.trim()) return;
    try {
      await apiFetch(`/api/ai-groups/${selectedGroup.id}/agents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(addAgentForm),
      });
      setShowAddAgent(false);
      setAddAgentForm({ agent_id: '', name: '', role: 'executor', model: '' });
      await fetchGroupDetail(selectedGroup.id);
      await fetchGroups();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleRemoveAgent = async (groupId: string, agentId: string) => {
    if (!confirm('确定移除该Agent？')) return;
    try {
      await apiFetch(`/api/ai-groups/${groupId}/agents/${agentId}`, { method: 'DELETE' });
      await fetchGroupDetail(groupId);
      await fetchGroups();
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleSubmitTask = async () => {
    if (!selectedGroup || !taskForm.goal.trim()) return;
    try {
      const constraints = taskForm.constraints ? taskForm.constraints.split('\n').filter(Boolean) : [];
      const criteria = taskForm.criteria ? taskForm.criteria.split('\n').filter(Boolean) : [];
      await apiFetch(`/api/ai-groups/${selectedGroup.id}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal: taskForm.goal, constraints, completion_criteria: criteria }),
      });
      setShowSubmitTask(false);
      setTaskForm({ goal: '', constraints: '', criteria: '' });
      await fetchGroupDetail(selectedGroup.id);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleExecuteTask = async (taskId: string, agentId: string, goal: string) => {
    if (!selectedGroup) return;
    try {
      await apiFetch(`/api/ai-groups/${selectedGroup.id}/tasks/${taskId}/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id: agentId, goal }),
      });
      await fetchGroupDetail(selectedGroup.id);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleVerifyTask = async (taskId: string, passed: boolean) => {
    if (!selectedGroup) return;
    try {
      await apiFetch(`/api/ai-groups/${selectedGroup.id}/tasks/${taskId}/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passed, reason: passed ? '' : '需要改进' }),
      });
      await fetchGroupDetail(selectedGroup.id);
    } catch (e: any) {
      setError(e.message);
    }
  };

  // F4b-㉓ 看板确认按钮：awaiting_confirm 任务的人工裁决口（确认放行/拒绝），
  // 对齐 POST /api/ai-groups/{gid}/tasks/{tid}/confirm（approved+note）。
  const handleConfirmTask = async (taskId: string, approved: boolean) => {
    if (!selectedGroup) return;
    try {
      await apiFetch(`/api/ai-groups/${selectedGroup.id}/tasks/${taskId}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved, note: confirmNote }),
      });
      setConfirmNote('');
      setSelectedTask(null);
      await fetchGroupDetail(selectedGroup.id);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const fetchDiscussion = async (taskId: string) => {
    if (!selectedGroup) return;
    try {
      const data = await apiFetch(`/api/ai-groups/${selectedGroup.id}/discuss/${taskId}/messages`);
      setDiscussionMessages(Array.isArray(data) ? data : []);
      setShowDiscussion(true);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const filtered = groups.filter((g) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return g.name.toLowerCase().includes(q) || g.description.toLowerCase().includes(q);
  });

  const totalAgents = groups.reduce((s, g) => s + (g.agents?.length || 0), 0);
  const totalTasks = groups.reduce((s, g) => s + (g.task_count || 0), 0);

  // ── Task Detail View ──
  if (selectedTask && selectedGroup) {
    const sts = getStatusInfo(selectedTask.status);
    const StsIcon = sts.icon;
    return (
      <div className="space-y-4">
        <Button variant="bare" onClick={() => setSelectedTask(null)} className="flex items-center gap-1 text-xs lg:text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="w-3.5 h-3.5" />返回群组详情
        </Button>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-start justify-between">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-medium">{selectedTask.goal}</h2>
                <Badge variant="bare" className={`flex gap-1 ${sts.color}`}>
                  <StsIcon className="w-3 h-3" />{sts.label}
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground mt-1">
                任务ID: {selectedTask.id} · 迭代: {selectedTask.iteration}/{selectedTask.max_iterations} · 质量分: {selectedTask.quality_score || '-'}
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              {selectedTask.status === 'pending' && selectedTask.assigned_agent_id && (
                <Button onClick={() => handleExecuteTask(selectedTask.id, selectedTask.assigned_agent_id!, selectedTask.goal)} variant="primary">
                  <Play className="w-3.5 h-3.5" />执行
                </Button>
              )}
              {(selectedTask.status === 'reviewing' || selectedTask.status === 'scored') && (
                <>
                  <Button onClick={() => { handleVerifyTask(selectedTask.id, true); setSelectedTask(null); }}
                    variant="chip-sm" className="flex items-center gap-1 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20">
                    <CheckCircle className="w-3.5 h-3.5" />通过
                  </Button>
                  <Button onClick={() => { handleVerifyTask(selectedTask.id, false); setSelectedTask(null); }}
                    variant="chip-sm" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20">
                    <X className="w-3.5 h-3.5" />驳回
                  </Button>
                </>
              )}
              {selectedTask.status === 'awaiting_confirm' && (
                <>
                  <Button onClick={() => handleConfirmTask(selectedTask.id, true)}
                    variant="chip-sm" className="flex items-center gap-1 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20">
                    <CheckCircle className="w-3.5 h-3.5" />确认放行
                  </Button>
                  <Button onClick={() => handleConfirmTask(selectedTask.id, false)}
                    variant="chip-sm" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20">
                    <X className="w-3.5 h-3.5" />拒绝
                  </Button>
                </>
              )}
              <Button onClick={() => fetchDiscussion(selectedTask.id)} variant="secondary" className="gap-1">
                <MessageSquare className="w-3.5 h-3.5" />讨论
              </Button>
            </div>
          </div>
          {selectedTask.result && (
            <div className="mt-3">
              <div className="text-xs text-muted-foreground mb-1">{selectedTask.status === 'awaiting_confirm' ? '确认提示' : '执行结果'}</div>
              <pre className="text-xs bg-muted p-3 rounded-md overflow-x-auto max-h-64 whitespace-pre-wrap">{selectedTask.result}</pre>
            </div>
          )}
          {selectedTask.status === 'awaiting_confirm' && (
            <div className="mt-3">
              <label className="text-xs text-muted-foreground mb-1 block">裁决说明（可选，拒绝时作为原因）</label>
              <input className="w-full px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                value={confirmNote} onChange={(e) => setConfirmNote(e.target.value)} placeholder="人工裁决备注..." />
            </div>
          )}
          {selectedTask.assigned_agent_id && (
            <div className="mt-3 text-xs text-muted-foreground">
              执行者: <span className="text-foreground font-mono">{selectedTask.assigned_agent_id}</span>
            </div>
          )}
        </div>

        {/* Subtasks */}
        {selectedTask.subtasks && selectedTask.subtasks.length > 0 && (
          <div className="rounded-lg border border-border bg-card">
            <div className="px-4 py-3 border-b border-border text-sm font-medium">子任务 ({selectedTask.subtasks.length})</div>
            <div className="divide-y divide-border">
              {selectedTask.subtasks.map((st) => {
                const sInfo = getStatusInfo(st.status);
                const SIcon = sInfo.icon;
                return (
                  <div key={st.id} className="px-4 py-3 flex items-center justify-between hover:bg-muted/30">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm truncate">{st.goal}</div>
                      <div className="text-[10px] text-muted-foreground mt-0.5">
                        {st.assigned_agent_id && <span className="font-mono mr-2">{st.assigned_agent_id}</span>}
                        分数: {st.quality_score || '-'}
                      </div>
                    </div>
                    <Badge variant="bare" className={`flex gap-1 ${sInfo.color}`}>
                      <SIcon className="w-3 h-3" />{sInfo.label}
                    </Badge>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Discussion Dialog */}
        {showDiscussion && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowDiscussion(false)}>
            <div className="bg-card rounded-lg border border-border w-full max-w-2xl mx-4 max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between p-4 border-b border-border shrink-0">
                <h3 className="text-sm font-medium">讨论记录</h3>
                <Button onClick={() => setShowDiscussion(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
              </div>
              <div className="flex-1 overflow-auto p-4 space-y-3">
                {discussionMessages.length === 0 ? (
                  <div className="text-center text-sm text-muted-foreground py-8">暂无讨论记录</div>
                ) : discussionMessages.map((msg) => (
                  <div key={msg.id} className={`flex gap-3 ${msg.agent_id === 'system' ? 'justify-center' : ''}`}>
                    <div className={`rounded-lg p-3 max-w-[80%] ${msg.agent_id === 'system' ? 'bg-muted/50 text-xs text-muted-foreground' : 'bg-muted'}`}>
                      {msg.agent_id !== 'system' && (
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-xs font-medium">{msg.agent_name || msg.agent_id}</span>
                          <Badge variant="muted" className="bg-background text-inherit">{msg.intent}</Badge>
                          <span className="text-[10px] text-muted-foreground">R{msg.round_num}</span>
                        </div>
                      )}
                      <div className="text-xs whitespace-pre-wrap">{msg.content}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ── Group Detail View ──
  if (selectedGroup) {
    return (
      <div className="space-y-4">
        <Button variant="bare" onClick={() => setSelectedGroup(null)} className="flex items-center gap-1 text-xs lg:text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="w-3.5 h-3.5" />返回列表
        </Button>

        <div className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-lg font-medium">{selectedGroup.name}</h2>
              {selectedGroup.description && <p className="text-sm text-muted-foreground mt-1">{selectedGroup.description}</p>}
              <div className="text-xs text-muted-foreground mt-2">
                ID: {selectedGroup.id} · 创建: {new Date(selectedGroup.created_at).toLocaleString('zh-CN')}
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <Button onClick={() => { setTaskForm({ goal: '', constraints: '', criteria: '' }); setShowSubmitTask(true); }} variant="primary">
                <Send className="w-3.5 h-3.5" />提交任务
              </Button>
              <Button onClick={() => handleDelete(selectedGroup.id)}
                variant="chip-sm" className="flex items-center gap-1 bg-red-500/10 text-red-500 hover:bg-red-500/20">
                <Trash2 className="w-3.5 h-3.5" />删除
              </Button>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 border-b border-border">
          <Button variant="bare" onClick={() => setDetailTab('agents')}
            className={`px-4 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${detailTab === 'agents' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
            <Users className="w-3.5 h-3.5 inline mr-1" />Agent ({selectedGroup.agents?.length || 0})
          </Button>
          <Button variant="bare" onClick={() => setDetailTab('tasks')}
            className={`px-4 py-2 text-xs lg:text-sm font-medium border-b-2 transition-colors ${detailTab === 'tasks' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}>
            <ListChecks className="w-3.5 h-3.5 inline mr-1" />任务 ({selectedGroup.tasks?.length || 0})
          </Button>
        </div>

        {/* Agents Tab */}
        {detailTab === 'agents' && (
          <div className="space-y-2">
            <div className="flex justify-end">
              <Button onClick={() => { setAddAgentForm({ agent_id: '', name: '', role: 'executor', model: '' }); setShowAddAgent(true); }} variant="secondary" className="gap-1">
                <UserPlus className="w-3.5 h-3.5" />添加Agent
              </Button>
            </div>
            <div className="rounded-lg border border-border bg-card divide-y divide-border">
              {(selectedGroup.agents || []).length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-muted-foreground">暂无Agent</div>
              ) : (selectedGroup.agents || []).map((agent) => (
                <div key={agent.id} className="px-4 py-3 flex items-center justify-between hover:bg-muted/30">
                  <div className="flex items-center gap-3">
                    <Bot className="w-5 h-5 text-muted-foreground" />
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{agent.name}</span>
                        <Badge variant="bare" className={`${getRoleColor(agent.role)}`}>
                          {getRoleLabel(agent.role)}
                        </Badge>
                        <Badge variant="bare" className={`${agent.status === 'online' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                          {agent.status}
                        </Badge>
                      </div>
                      <div className="text-[10px] text-muted-foreground mt-0.5">
                        ID: <span className="font-mono">{agent.agent_id}</span>
                        {agent.model && <span> · 模型: {agent.model}</span>}
                        {agent.temperature != null && <span> · 温度: {agent.temperature}</span>}
                      </div>
                    </div>
                  </div>
                  <Button onClick={() => handleRemoveAgent(selectedGroup.id, agent.agent_id)}
                    variant="icon-md" className="hover:bg-red-500/10 text-muted-foreground hover:text-red-500">
                    <Trash2 className="w-3.5 h-3.5" />
                  </Button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tasks Tab */}
        {detailTab === 'tasks' && (
          <div className="rounded-lg border border-border bg-card divide-y divide-border">
            {(selectedGroup.tasks || []).length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-muted-foreground">暂无任务</div>
            ) : (selectedGroup.tasks || []).map((task) => {
              const tInfo = getStatusInfo(task.status);
              const TIcon = tInfo.icon;
              return (
                <div key={task.id}
                  className="px-4 py-3 flex items-center justify-between hover:bg-muted/30 cursor-pointer"
                  onClick={() => { setConfirmNote(''); setSelectedTask(task); }}>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm truncate">{task.goal}</div>
                    <div className="flex items-center gap-2 mt-1 text-[10px] text-muted-foreground">
                      <span>ID: {task.id}</span>
                      {task.assigned_agent_id && <span>· 执行者: <span className="font-mono">{task.assigned_agent_id}</span></span>}
                      {task.quality_score > 0 && <span>· 分数: {task.quality_score}</span>}
                      {task.subtasks && task.subtasks.length > 0 && <span>· 子任务: {task.subtasks.length}</span>}
                      <span>· {new Date(task.created_at).toLocaleString('zh-CN')}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-3">
                    <Badge variant="bare" className={`flex gap-1 ${tInfo.color}`}>
                      <TIcon className="w-3 h-3" />{tInfo.label}
                    </Badge>
                    <ChevronRight className="w-4 h-4 text-muted-foreground" />
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Add Agent Dialog */}
        {showAddAgent && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowAddAgent(false)}>
            <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between p-4 border-b border-border">
                <h3 className="text-sm font-medium">添加Agent</h3>
                <Button onClick={() => setShowAddAgent(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
              </div>
              <div className="p-4 space-y-3">
                <div>
                  <label className="text-xs text-muted-foreground">Agent ID *</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                    value={addAgentForm.agent_id} onChange={(e) => setAddAgentForm((f) => ({ ...f, agent_id: e.target.value }))} placeholder="executor-2" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">名称</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                    value={addAgentForm.name} onChange={(e) => setAddAgentForm((f) => ({ ...f, name: e.target.value }))} placeholder="执行者2号" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">角色</label>
                  <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none"
                    value={addAgentForm.role} onChange={(e) => setAddAgentForm((f) => ({ ...f, role: e.target.value }))}>
                    {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">模型 (可选)</label>
                  <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                    value={addAgentForm.model} onChange={(e) => setAddAgentForm((f) => ({ ...f, model: e.target.value }))} placeholder="claude-sonnet-4" />
                </div>
              </div>
              <div className="flex justify-end gap-2 p-4 border-t border-border">
                <Button onClick={() => setShowAddAgent(false)} variant="secondary">取消</Button>
                <Button onClick={handleAddAgent} variant="primary">添加</Button>
              </div>
            </div>
          </div>
        )}

        {/* Submit Task Dialog */}
        {showSubmitTask && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowSubmitTask(false)}>
            <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between p-4 border-b border-border">
                <h3 className="text-sm font-medium">提交任务</h3>
                <Button onClick={() => setShowSubmitTask(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
              </div>
              <div className="p-4 space-y-3">
                <div>
                  <label className="text-xs text-muted-foreground">目标 *</label>
                  <textarea rows={3} className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-none"
                    value={taskForm.goal} onChange={(e) => setTaskForm((f) => ({ ...f, goal: e.target.value }))} placeholder="描述任务目标..." />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">约束条件 (每行一条)</label>
                  <textarea rows={2} className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-none"
                    value={taskForm.constraints} onChange={(e) => setTaskForm((f) => ({ ...f, constraints: e.target.value }))} placeholder="使用中文回答&#10;不超过500字" />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">完成标准 (每行一条)</label>
                  <textarea rows={2} className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-none"
                    value={taskForm.criteria} onChange={(e) => setTaskForm((f) => ({ ...f, criteria: e.target.value }))} placeholder="包含代码示例&#10;通过测试验证" />
                </div>
              </div>
              <div className="flex justify-end gap-2 p-4 border-t border-border">
                <Button onClick={() => setShowSubmitTask(false)} variant="secondary">取消</Button>
                <Button onClick={handleSubmitTask} variant="primary">提交</Button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ── Group List View ──
  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {error && (
        <div className="flex items-center gap-2 px-3 py-2 text-xs bg-red-500/10 text-red-500 rounded-md">
          <AlertCircle className="w-3.5 h-3.5" />
          {error}
          <Button variant="bare" onClick={() => setError('')} className="ml-auto"><X className="w-3.5 h-3.5" /></Button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{groups.length}</div>
          <div className="text-xs text-muted-foreground">AI群组</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-blue-600">{totalAgents}</div>
          <div className="text-xs text-muted-foreground">总Agent数</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-emerald-600">{totalTasks}</div>
          <div className="text-xs text-muted-foreground">总任务数</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索群组名称..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button onClick={fetchGroups} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
        <Button onClick={() => { setNewName(''); setNewDesc(''); setShowCreate(true); }} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />新建群组
        </Button>
      </div>

      {/* Group Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {filtered.map((group) => (
          <div key={group.id}
            className="rounded-lg border border-border bg-card p-4 hover:bg-muted/30 transition-colors cursor-pointer"
            onClick={() => fetchGroupDetail(group.id)}>
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-2">
                <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
                  <Users className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <h3 className="text-sm font-medium">{group.name}</h3>
                  <Badge variant="bare" className={`${group.status === 'active' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'}`}>
                    {group.status}
                  </Badge>
                </div>
              </div>
              <ChevronRight className="w-4 h-4 text-muted-foreground" />
            </div>
            {group.description && (
              <p className="text-xs text-muted-foreground mt-2 line-clamp-2">{group.description}</p>
            )}
            <div className="flex items-center gap-3 mt-3 pt-3 border-t border-border text-[10px] text-muted-foreground">
              <span className="flex items-center gap-1"><Bot className="w-3 h-3" />{group.agents?.length || 0} Agent</span>
              <span className="flex items-center gap-1"><ListChecks className="w-3 h-3" />{group.task_count || 0} 任务</span>
              <span>{new Date(group.created_at).toLocaleDateString('zh-CN')}</span>
            </div>
            {/* Agent avatars */}
            {group.agents && group.agents.length > 0 && (
              <div className="flex items-center gap-1 mt-2">
                {group.agents.slice(0, 5).map((a) => (
                  <Badge variant="bare" key={a.id} className={`${getRoleColor(a.role)}`}>
                    {getRoleLabel(a.role)}
                  </Badge>
                ))}
                {group.agents.length > 5 && <span className="text-[10px] text-muted-foreground">+{group.agents.length - 5}</span>}
              </div>
            )}
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Users className="w-10 h-10 mb-2 opacity-30" />
          <p className="text-xs">{search ? '没有匹配的群组' : '暂无AI群组，点击"新建群组"创建'}</p>
        </div>
      )}

      {/* Create Group Dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreate(false)}>
          <div className="bg-card rounded-lg border border-border w-full max-w-lg mx-4 max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-border shrink-0">
              <h3 className="text-sm font-medium">新建AI群组</h3>
              <Button onClick={() => setShowCreate(false)} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
            </div>
            <div className="flex-1 overflow-auto p-4 space-y-3">
              <div>
                <label className="text-xs text-muted-foreground">群组名称 *</label>
                <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
                  value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="研发协作组" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">描述</label>
                <textarea rows={2} className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50 resize-none"
                  value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder="群组用途描述..." />
              </div>
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="text-xs text-muted-foreground">初始Agent</label>
                  <Button variant="bare" onClick={() => setNewAgents((a) => [...a, { agent_id: '', name: '', role: 'executor', model: '' }])}
                    className="text-[10px] text-primary hover:underline">+ 添加</Button>
                </div>
                <div className="space-y-2">
                  {newAgents.map((agent, idx) => (
                    <div key={idx} className="flex items-center gap-2 p-2 rounded bg-muted/50">
                      <input className="flex-1 px-2 py-1 text-xs bg-background border border-border rounded focus:outline-none focus:ring-1 focus:ring-primary/50"
                        placeholder="agent_id" value={agent.agent_id}
                        onChange={(e) => setNewAgents((a) => a.map((x, i) => i === idx ? { ...x, agent_id: e.target.value } : x))} />
                      <input className="w-20 px-2 py-1 text-xs bg-background border border-border rounded focus:outline-none focus:ring-1 focus:ring-primary/50"
                        placeholder="名称" value={agent.name}
                        onChange={(e) => setNewAgents((a) => a.map((x, i) => i === idx ? { ...x, name: e.target.value } : x))} />
                      <select className="w-20 px-1 py-1 text-xs bg-background border border-border rounded focus:outline-none"
                        value={agent.role}
                        onChange={(e) => setNewAgents((a) => a.map((x, i) => i === idx ? { ...x, role: e.target.value } : x))}>
                        {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                      </select>
                      <Button onClick={() => setNewAgents((a) => a.filter((_, i) => i !== idx))}
                        variant="icon-sm" className="hover:bg-red-500/10 text-muted-foreground hover:text-red-500">
                        <X className="w-3 h-3" />
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2 p-4 border-t border-border shrink-0">
              <Button onClick={() => setShowCreate(false)} variant="secondary">取消</Button>
              <Button onClick={handleCreate} disabled={!newName.trim()} variant="primary">创建</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
