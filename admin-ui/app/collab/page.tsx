'use client';

import { useEffect, useState, useCallback } from 'react';
import { apiFetch } from '@/lib/api';
import {
  Search, RefreshCw, X, Bot, Users, MessageSquare, Send,
  CheckCircle, XCircle, AlertTriangle, Loader2, Plus, Zap,
  ArrowRightLeft, Shield, Clock, Wifi, WifiOff, Activity,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

// ── Types ───────────────────────────────────────────────────

interface AgentStatus {
  agent_id: string;
  name: string;
  model: string;
  role: string;
  capabilities: string[];
  status: string;
  last_seen: string;
}

// ── Helpers ─────────────────────────────────────────────────

function formatTime(ts: string) {
  if (!ts) return '-';
  try {
    return new Date(ts).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    });
  } catch { return ts; }
}

function formatRelative(ts: string) {
  if (!ts) return '';
  try {
    const diff = Date.now() - new Date(ts).getTime();
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    return `${Math.floor(diff / 86400000)}天前`;
  } catch { return ''; }
}

// ── Agent Card ──────────────────────────────────────────────

function AgentCard({ agent }: { agent: AgentStatus }) {
  const isOnline = agent.status === 'online';

  return (
    <div className={`flex items-center gap-3 p-4 rounded-lg border bg-card transition-colors ${
      isOnline ? 'border-emerald-500/20 hover:bg-muted/30' : 'border-border opacity-70 hover:bg-muted/30'
    }`}>
      <div className="relative">
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
          isOnline ? 'bg-emerald-500/10' : 'bg-muted'
        }`}>
          <Bot className={`w-5 h-5 ${isOnline ? 'text-emerald-500' : 'text-muted-foreground'}`} />
        </div>
        <div className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-card ${
          isOnline ? 'bg-emerald-500' : 'bg-muted-foreground/30'
        }`} />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{agent.name}</span>
          <Badge variant="bare" className={`${
 isOnline ? 'bg-emerald-500/10 text-emerald-500' : 'bg-muted text-muted-foreground'
 }`}>
            {isOnline ? '在线' : agent.status}
          </Badge>
        </div>
        {agent.role && <p className="text-[10px] text-muted-foreground truncate">{agent.role}</p>}
        <div className="flex items-center gap-2 mt-1">
          {agent.model && (
            <Badge variant="default" className="font-mono">{agent.model}</Badge>
          )}
          {agent.capabilities.map(cap => (
            <Badge variant="muted" key={cap}>{cap}</Badge>
          ))}
        </div>
      </div>

      <div className="text-right shrink-0">
        <div className="text-[10px] text-muted-foreground flex items-center gap-1 justify-end">
          <Clock className="w-2.5 h-2.5" />{formatRelative(agent.last_seen)}
        </div>
        <div className="text-[10px] text-muted-foreground font-mono">{agent.agent_id}</div>
      </div>
    </div>
  );
}

// ── Register Dialog ─────────────────────────────────────────

function RegisterDialog({ onClose, onRegistered }: { onClose: () => void; onRegistered: () => void }) {
  const [form, setForm] = useState({
    agent_id: '',
    name: '',
    model: '',
    role: '',
    capabilities: '',
    endpoint: '',
  });
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ token: string } | null>(null);

  const handleRegister = async () => {
    if (!form.agent_id.trim() || !form.name.trim()) return;
    setSaving(true);
    try {
      const data = await apiFetch('/api/collab/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_id: form.agent_id,
          name: form.name,
          model: form.model,
          role: form.role,
          capabilities: form.capabilities.split(',').map(s => s.trim()).filter(Boolean),
          endpoint: form.endpoint,
        }),
      });
      setResult(data);
      onRegistered();
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
          <h2 className="text-sm font-medium">注册Agent</h2>
          <Button onClick={onClose} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
        </div>
        <div className="p-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted-foreground">Agent ID *</label>
              <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono" value={form.agent_id} onChange={e => setForm(f => ({ ...f, agent_id: e.target.value }))} placeholder="unique-id" />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">名称 *</label>
              <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="Agent名称" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted-foreground">模型</label>
              <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={form.model} onChange={e => setForm(f => ({ ...f, model: e.target.value }))} placeholder="gpt-4o / local" />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">角色</label>
              <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value }))} placeholder="coder / reviewer" />
            </div>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">能力 (逗号分隔)</label>
            <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={form.capabilities} onChange={e => setForm(f => ({ ...f, capabilities: e.target.value }))} placeholder="code, review, translate" />
          </div>
          <div>
            <label className="text-xs text-muted-foreground">WebSocket端点</label>
            <input className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono" value={form.endpoint} onChange={e => setForm(f => ({ ...f, endpoint: e.target.value }))} placeholder="ws://host:port/ws/agent/id" />
          </div>

          {result && (
            <div className="p-2 rounded bg-green-500/5 border border-green-500/20 text-xs">
              <div className="text-green-500 font-medium">注册成功！</div>
              <div className="text-[10px] text-muted-foreground mt-1">Token: <span className="font-mono text-primary">{result.token}</span></div>
              <div className="text-[10px] text-muted-foreground">请保存此Token，后续通信需要使用。</div>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button onClick={onClose} variant="secondary">关闭</Button>
            <Button onClick={handleRegister} disabled={!form.agent_id.trim() || !form.name.trim() || saving} variant="primary">
              {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}注册
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Message Dialog ──────────────────────────────────────────

function MessageDialog({ agents, onClose }: { agents: AgentStatus[]; onClose: () => void }) {
  const [fromAgent, setFromAgent] = useState('');
  const [toAgent, setToAgent] = useState('');
  const [msgType, setMsgType] = useState('task');
  const [payload, setPayload] = useState('{}');
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<string>('');

  const handleSend = async () => {
    if (!fromAgent || !toAgent) return;
    setSending(true);
    try {
      const data = await apiFetch('/api/collab/message', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          from_agent: fromAgent,
          to_agent: toAgent,
          type: msgType,
          payload: JSON.parse(payload),
        }),
      });
      setResult(`消息已发送 (ID: ${data.message_id})`);
    } catch (e: any) {
      setResult(`错误: ${e.message}`);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="bg-card rounded-lg border border-border w-full max-w-md mx-4" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h2 className="text-sm font-medium">发送消息</h2>
          <Button onClick={onClose} variant="icon-sm" className="hover:bg-muted"><X className="w-4 h-4" /></Button>
        </div>
        <div className="p-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted-foreground">发送方</label>
              <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={fromAgent} onChange={e => setFromAgent(e.target.value)}>
                <option value="">选择Agent</option>
                {agents.filter(a => a.status === 'online').map(a => <option key={a.agent_id} value={a.agent_id}>{a.name}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-muted-foreground">接收方</label>
              <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={toAgent} onChange={e => setToAgent(e.target.value)}>
                <option value="">选择Agent</option>
                {agents.filter(a => a.status === 'online').map(a => <option key={a.agent_id} value={a.agent_id}>{a.name}</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">消息类型</label>
            <select className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md" value={msgType} onChange={e => setMsgType(e.target.value)}>
              <option value="task">任务</option>
              <option value="result">结果</option>
              <option value="query">查询</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">Payload (JSON)</label>
            <textarea className="w-full mt-1 px-3 py-1.5 text-xs bg-muted border border-border rounded-md font-mono h-20 resize-none" value={payload} onChange={e => setPayload(e.target.value)} />
          </div>
          {result && <div className="text-xs p-2 rounded bg-muted">{result}</div>}
          <div className="flex justify-end gap-2 pt-2">
            <Button onClick={onClose} variant="secondary">关闭</Button>
            <Button onClick={handleSend} disabled={!fromAgent || !toAgent || sending} variant="primary">
              {sending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}发送
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────

export default function CollabPage() {
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [showRegister, setShowRegister] = useState(false);
  const [showMessage, setShowMessage] = useState(false);

  const fetchAgents = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/collab/status');
      setAgents(Array.isArray(data) ? data : []);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAgents(); }, [fetchAgents]);

  const filtered = agents.filter(a => {
    if (!search) return true;
    const q = search.toLowerCase();
    return a.name.toLowerCase().includes(q) || a.agent_id.toLowerCase().includes(q) ||
      a.role.toLowerCase().includes(q) || a.capabilities.some(c => c.toLowerCase().includes(q));
  });

  const onlineCount = agents.filter(a => a.status === 'online').length;

  if (loading) return <div className="text-sm text-muted-foreground p-4">加载中...</div>;

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold">{agents.length}</div>
          <div className="text-xs text-muted-foreground">注册Agent</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-green-500">{onlineCount}</div>
          <div className="text-xs text-muted-foreground">在线</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-muted-foreground">{agents.length - onlineCount}</div>
          <div className="text-xs text-muted-foreground">离线</div>
        </div>
        <div className="rounded-lg border border-border bg-card p-3">
          <div className="text-2xl font-bold text-primary">
            {new Set(agents.flatMap(a => a.capabilities)).size}
          </div>
          <div className="text-xs text-muted-foreground">能力类型</div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input className="w-full pl-8 pr-3 py-1.5 text-xs bg-muted border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary/50"
            placeholder="搜索Agent名称、ID、能力..." value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <Button onClick={() => setShowRegister(true)} variant="primary" className="gap-1.5">
          <Plus className="w-3.5 h-3.5" />注册Agent
        </Button>
        <Button onClick={() => setShowMessage(true)} disabled={onlineCount < 2} variant="secondary" className="disabled:opacity-30">
          <Send className="w-3.5 h-3.5" />发送消息
        </Button>
        <Button onClick={fetchAgents} variant="secondary">
          <RefreshCw className="w-3.5 h-3.5" />刷新
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-3 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500" />
          <span className="text-xs text-red-500">{error}</span>
          <Button variant="bare" onClick={() => setError('')} className="ml-auto"><X className="w-3 h-3" /></Button>
        </div>
      )}

      {/* Agent list */}
      <div className="space-y-2">
        {filtered.map(agent => <AgentCard key={agent.agent_id} agent={agent} />)}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-12 text-muted-foreground">
          <Users className="w-10 h-10 mx-auto mb-2 opacity-30" />
          <p className="text-xs">{search ? '无匹配Agent' : '暂无注册Agent'}</p>
          <p className="text-[10px] mt-1">注册Agent后可进行协作通信</p>
        </div>
      )}

      {/* Info */}
      <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 p-3 flex items-center gap-2">
        <Shield className="w-4 h-4 text-blue-500 shrink-0" />
        <div>
          <span className="text-xs font-medium text-blue-500">Agent协作协议</span>
          <p className="text-[10px] text-muted-foreground mt-0.5">
            支持Agent注册、消息传递（task/result/query）、上下文交接（handoff）、WebSocket实时通信。
          </p>
        </div>
      </div>

      {/* Dialogs */}
      {showRegister && <RegisterDialog onClose={() => setShowRegister(false)} onRegistered={() => { setShowRegister(false); fetchAgents(); }} />}
      {showMessage && <MessageDialog agents={agents} onClose={() => setShowMessage(false)} />}
    </div>
  );
}
